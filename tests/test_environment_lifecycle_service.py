from datetime import datetime, timedelta, timezone
import json
import threading
import unittest
from unittest.mock import AsyncMock, patch

from backend import browser_service as service_module, lifecycle
from backend.browser_service import BrowserService, BrowserServiceError
from backend.vendor.browser_controller import ProfileLock
import test_browser_service as fixtures


class EnvironmentLifecycleServiceTests(unittest.TestCase):
    setUp = fixtures.BrowserServiceTests.setUp
    tearDown = fixtures.BrowserServiceTests.tearDown
    process = fixtures.BrowserServiceTests.process

    def change(self, env_id="shop01", operation="archive", confirm_name=None):
        with patch.object(service_module, "enumerate_browser_processes", return_value=[]):
            return self.service.change_environment_lifecycle(env_id, operation, confirm_name)

    def snapshot(self):
        with patch.object(service_module, "enumerate_browser_processes", return_value=[]):
            return self.service.snapshot()

    def restart(self):
        self.service.shutdown()
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            self.service = BrowserService(self.registry)

    def test_archive_and_restore_preserve_identity_configuration_and_login_rule_across_restart(self):
        env = self.service.environments["shop01"]
        before = {path: path.read_bytes() for path in (self.registry, env.config_path)}
        rule = {"mode": "url", "login_url": "https://identity.example.test/login", "wait_seconds": 2}
        self.service.update_login_check(env.id, rule)
        result = self.change()
        self.assertEqual(result, {"environment_id": env.id, "state": "archived"})
        self.assertEqual([row["id"] for row in self.snapshot()["environments"]], ["shop02", "piaoju"])
        row = self.snapshot()["archived_environments"][0]
        self.assertTrue(row["archived"])
        self.assertIsNotNone(datetime.fromisoformat(row["archived_at"]).tzinfo)
        self.assertEqual(row["login_check"], rule)
        self.restart()
        self.assertEqual(self.snapshot()["summary"]["total"], 2)
        self.assertEqual(self.change(operation="restore"), {"environment_id": env.id, "state": "active"})
        restored = self.service.environments[env.id]
        self.assertEqual((restored.root, restored.port, restored.config_path), (env.root, env.port, env.config_path))
        self.assertEqual(self.service.login_check_settings(env.id), rule)
        self.assertEqual(self.snapshot()["archived_environments"], [])
        self.assertTrue(all(path.read_bytes() == value for path, value in before.items()))

    def test_archive_uses_fresh_inventory_and_rejects_running_conflicting_or_unknown_process_state(self):
        for records, code in (([self.process()], "environment_running"),
                              ([self.process(root=service_module.normalized_path(self.root / "foreign"))], "environment_conflict")):
            with patch.object(service_module, "enumerate_browser_processes", return_value=records):
                with self.assertRaises(BrowserServiceError) as caught:
                    self.service.change_environment_lifecycle("shop01", "archive")
            self.assertEqual(caught.exception.code, code)
        with patch.object(service_module, "enumerate_browser_processes",
                          side_effect=BrowserServiceError("无法读取进程", "process_inventory_failed")):
            with self.assertRaises(BrowserServiceError) as caught:
                self.service.change_environment_lifecycle("shop01", "archive")
        self.assertEqual(caught.exception.code, "process_inventory_failed")
        self.assertEqual(self.service._lifecycle, {})

    def test_restore_allows_owned_running_browser_but_rejects_conflicts_and_delete_requires_stopped(self):
        self.change()
        env = self.service.environments["shop01"]
        with patch.object(service_module, "enumerate_browser_processes", return_value=[self.process()]):
            with self.assertRaises(BrowserServiceError) as caught:
                self.service.change_environment_lifecycle(env.id, "delete", env.name)
            self.assertEqual(caught.exception.code, "environment_running")
        with patch.object(service_module, "enumerate_browser_processes",
                          return_value=[self.process(root=service_module.normalized_path(self.root / "foreign"))]):
            with self.assertRaises(BrowserServiceError) as caught:
                self.service.change_environment_lifecycle(env.id, "restore")
            self.assertEqual(caught.exception.code, "environment_conflict")
        with patch.object(service_module, "enumerate_browser_processes", return_value=[self.process()]):
            self.assertEqual(self.service.change_environment_lifecycle(env.id, "restore")["state"], "active")

    def test_busy_checkpoint_maintenance_and_external_profile_lock_prevent_lifecycle_changes(self):
        for field, value in (("_checkpoint_env", "shop01"), ("_maintenance_job_id", "pending-round")):
            with patch.object(self.service, field, value):
                with self.assertRaises(BrowserServiceError) as caught:
                    self.change()
                self.assertEqual(caught.exception.code, "environment_busy")
        lock = ProfileLock(self.service.environments["shop01"].root, "Default")
        lock.acquire()
        try:
            with self.assertRaises(BrowserServiceError) as caught:
                self.change()
            self.assertEqual(caught.exception.code, "environment_busy")
        finally:
            lock.release()

    def test_queued_and_active_jobs_finish_before_archiving_is_allowed(self):
        entered, release = threading.Event(), threading.Event()
        def perform(_action, _env):
            entered.set()
            release.wait(3)
            return {"message": "完成"}
        with patch.object(self.service, "_perform", side_effect=perform):
            try:
                self.service.submit("focus", ["shop01"])
                self.assertTrue(entered.wait(1))
                self.service.submit("focus", ["shop02"])
                for env_id in ("shop01", "shop02"):
                    with self.assertRaises(BrowserServiceError) as caught:
                        self.change(env_id)
                    self.assertEqual(caught.exception.code, "environment_busy")
            finally:
                release.set()
            self.service._queue.join()
        self.assertEqual(self.change()["state"], "archived")

    def test_archived_action_allowlist_and_all_automatic_work_use_only_active_environments(self):
        self.change()
        for action in ("start", "check-login", "save-session", "focus"):
            with self.assertRaises(BrowserServiceError) as caught:
                self.service.submit(action, ["shop01"])
            self.assertEqual(caught.exception.code, "environment_archived")
        with self.assertRaises(BrowserServiceError) as caught:
            self.service.update_login_check("shop01", {"mode": "url", "login_url": "", "wait_seconds": 5})
        self.assertEqual(caught.exception.code, "environment_archived")
        with patch.object(self.service, "_perform", return_value={"message": "完成", "auth": {"status": "assumed"}}) as perform:
            for action in ("open-folder", "open-results", "close"):
                self.service.submit(action, ["shop01"])
            self.service._queue.join()
            self.assertEqual([call.args[0] for call in perform.call_args_list], ["open-folder", "open-results", "close"])
            perform.reset_mock()
            self.service.close_all_shops({"pause_maintenance": False})
            self.service._queue.join()
            self.assertEqual([call.args[1].id for call in perform.call_args_list], ["shop02", "piaoju"])
            perform.reset_mock()
            self.service.run_maintenance()
            self.service._queue.join()
            self.assertEqual([call.args[1].id for call in perform.call_args_list], ["shop02", "piaoju"])
        self.service._next_cookie_sync = {"shop01": 0}
        self.service._observations["shop01"] = {"cdp": "connected"}
        with patch.object(self.service, "_browser_action", new_callable=AsyncMock) as save:
            self.service._idle_checkpoint()
            save.assert_not_awaited()

    def test_all_archived_is_valid_empty_active_state_and_scheduler_does_not_start_work(self):
        for env_id in tuple(self.service.environments):
            self.change(env_id)
        self.assertEqual(self.snapshot()["environments"], [])
        self.assertEqual(len(self.snapshot()["archived_environments"]), 3)
        for action in (lambda: self.service.close_all_shops({"pause_maintenance": False}), self.service.run_maintenance):
            with self.assertRaises(BrowserServiceError) as caught:
                action()
            self.assertEqual(caught.exception.code, "no_environments")
        self.service._maintenance.update(enabled=True, next_run_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        self.service._maintenance_tick()
        self.assertEqual(self.service._jobs, [])

    def test_delete_requires_archive_and_exact_name_and_preserves_every_resource(self):
        env = self.service.environments["shop01"]
        profile_marker = env.root / "keep.txt"
        profile_marker.write_text("profile sentinel", encoding="utf-8")
        download = self.root / "downloads-marker.txt"
        download.write_text("download sentinel", encoding="utf-8")
        before = {path: path.read_bytes() for path in (self.registry, env.config_path, profile_marker, download)}
        with self.assertRaises(BrowserServiceError) as caught:
            self.change(operation="delete", confirm_name=env.name)
        self.assertEqual(caught.exception.code, "environment_state_conflict")
        self.change()
        with self.assertRaises(BrowserServiceError) as caught:
            self.change(operation="delete", confirm_name=env.name + " ")
        self.assertEqual(caught.exception.code, "confirmation_failed")
        self.assertEqual(self.change(operation="delete", confirm_name=env.name)["state"], "deleted")
        self.assertTrue(all(path.read_bytes() == value for path, value in before.items()))
        self.assertNotIn(env.id, self.service.environments)
        self.assertEqual(self.snapshot()["archived_environments"], [])
        resource = self.service._lifecycle[env.id]["resource"]
        self.assertEqual(resource["user_data_dir"], str(env.root))
        self.assertEqual(resource["debug_port"], env.port)
        self.restart()
        with self.assertRaises(BrowserServiceError):
            self.change(operation="restore")
        env.config_path.unlink()  # Only the temporary fixture: tombstones must survive stale source configs.
        self.restart()
        self.assertNotIn(env.id, self.service.environments)

    def test_save_failure_never_changes_memory_or_existing_archive_state(self):
        for operation in ("archive", "restore", "delete"):
            if operation == "restore":
                self.change()
            before = dict(self.service._lifecycle)
            with patch.object(lifecycle, "save_state", side_effect=lifecycle.LifecycleError("lifecycle_storage_failed", "无法保存")):
                with self.assertRaises(lifecycle.LifecycleError):
                    self.change(operation=operation, confirm_name=self.service.environments["shop01"].name)
            self.assertEqual(self.service._lifecycle, before)
            self.assertIn("shop01", self.service.environments)

    def test_deleted_custom_name_can_be_recreated_with_new_id_profile_and_reserved_port(self):
        payload = {"name": "可重建环境", "parent_folder": str(self.root), "login_username": "",
                   "home_url": fixtures.CUSTOM_HOME}
        first = self.service.create_shop(payload)
        old = self.service.environments[first["id"]]
        self.change(old.id)
        self.change(old.id, "delete", old.name)
        with patch.object(service_module.shop_registry, "create_shop", wraps=service_module.shop_registry.create_shop) as create:
            second = self.service.create_shop(payload)
        existing = create.call_args.args[1]
        reservation = next(item for item in existing if item["id"] == old.id)
        self.assertTrue(reservation["deleted"])
        self.assertNotEqual(second["id"], first["id"])
        self.assertNotEqual(second["user_data_dir"], first["user_data_dir"])
        self.assertNotEqual(second["debug_port"], first["debug_port"])
        self.assertTrue(old.root.exists())
        self.assertTrue(old.config_path.exists())
        self.restart()
        self.assertIn(second["id"], self.service.environments)
        self.assertNotIn(first["id"], self.service.environments)
        old.config_path.unlink()
        self.restart()
        self.assertIn(second["id"], self.service.environments)

    def test_closed_service_rejects_lifecycle_changes(self):
        self.service.shutdown()
        with self.assertRaises(BrowserServiceError) as caught:
            self.change()
        self.assertEqual(caught.exception.code, "service_closed")
