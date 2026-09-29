from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend import browser_service as service_module, maintenance
from backend.browser_service import BrowserService, BrowserServiceError
from backend.vendor.browser_controller import BrowserControllerError, PlaywrightBrowserController, ProfileLock, _same_role_page
from backend.session_cookies import empty_status
import test_browser_service as fixtures


SUCCESS = {"auth": {"status": "verified"}, "message": "已访问后台并保存会话"}


class MaintenanceTests(unittest.TestCase):
    setUp = fixtures.BrowserServiceTests.setUp
    tearDown = fixtures.BrowserServiceTests.tearDown
    process = fixtures.BrowserServiceTests.process
    generic_environment = fixtures.BrowserServiceTests.generic_environment
    probe_controller = staticmethod(fixtures.BrowserServiceTests.probe_controller)

    def settings(self, *, enabled=False, include_shared=None, interval=120):
        payload = {"enabled": enabled, "interval_minutes": interval}
        if include_shared is not None:
            payload["include_shared"] = include_shared
        return self.service.update_maintenance(payload)

    def due(self):
        self.service._maintenance["next_run_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.service._maintenance_tick()

    def test_defaults_and_strict_complete_settings_persist_separately(self):
        before = self.registry.read_bytes()
        value = self.service.maintenance_snapshot()
        self.assertEqual((value["enabled"], value["interval_minutes"]), (False, 120))
        self.assertNotIn("include_shared", value)
        self.assertIsNone(value["next_run_at"])
        for payload in ({}, {"enabled": True, "interval_minutes": 15, "include_shared": True},
                        {"enabled": 1, "interval_minutes": 120, "include_shared": True},
                        {"enabled": True, "interval_minutes": True, "include_shared": True},
                        {"enabled": True, "interval_minutes": 120, "include_shared": "true"},
                        {"enabled": True, "interval_minutes": 120, "include_shared": True, "extra": 1}):
            with self.assertRaises(BrowserServiceError) as error:
                self.service.update_maintenance(payload)
            self.assertEqual(error.exception.code, "invalid_maintenance")
        value = self.settings(enabled=True, include_shared=True, interval=30)
        self.assertIsNotNone(value["next_run_at"])
        self.assertEqual(self.registry.read_bytes(), before)
        stored, required = maintenance.load(self.root / maintenance.FILE_NAME,
                                           {key: env.name for key, env in self.service.environments.items()})
        self.assertTrue(stored["enabled"])
        self.assertEqual(stored["interval_minutes"], 30)
        self.assertEqual(required, set())

    def test_setting_write_failure_does_not_enable_schedule(self):
        with patch.object(maintenance, "save", side_effect=PermissionError("private-detail")):
            with self.assertRaises(BrowserServiceError) as error:
                self.settings(enabled=True)
        self.assertEqual(error.exception.code, "maintenance_storage_failed")
        self.assertNotIn("private-detail", str(error.exception))
        self.assertFalse(self.service.maintenance_snapshot()["enabled"])

    def test_legacy_exclusion_setting_migrates_without_losing_schedule_or_results(self):
        names = {key: env.name for key, env in self.service.environments.items()}
        previous = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        legacy = {"schema_version": 1, **maintenance.initial_state(), "enabled": True,
                  "interval_minutes": 60, "include_shared": False, "next_run_at": future,
                  "last_status": "complete", "last_run_at": previous, "last_finished_at": previous,
                  "last_results": [{"id": "shop01", "name": "旧名称", "status": "complete", "message": "已完成"}],
                  "login_required_ids": ["shop02"]}
        self.service.shutdown()
        self.service._maintenance_path.write_text(json.dumps(legacy), encoding="utf-8")
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            self.service = BrowserService(self.registry)
        state = self.service.maintenance_snapshot()
        self.assertTrue(state["enabled"])
        self.assertEqual(state["interval_minutes"], 60)
        self.assertEqual(state["next_run_at"], future)
        self.assertEqual(state["last_run_at"], previous)
        self.assertEqual(state["last_results"][0]["message"], "已完成")
        self.assertEqual(self.service._maintenance_required, {"shop02"})
        self.assertNotIn("include_shared", state)
        with patch.object(self.service, "_perform", return_value=SUCCESS) as perform:
            self.service.run_maintenance()
            self.service._queue.join()
        self.assertEqual([call.args[1].id for call in perform.call_args_list], ["shop01", "shop02", "piaoju"])
        persisted = json.loads(self.service._maintenance_path.read_text(encoding="utf-8"))
        self.assertNotIn("include_shared", persisted)
        restored, _ = maintenance.load(self.service._maintenance_path, names)
        self.assertTrue(restored["enabled"])
        self.assertEqual(restored["interval_minutes"], 60)

    def test_legacy_api_false_flag_still_maintains_every_registered_environment(self):
        state = self.settings(include_shared=False)
        self.assertNotIn("include_shared", state)
        with patch.object(self.service, "_perform", return_value=SUCCESS) as perform:
            self.service.run_maintenance()
            self.service._queue.join()
        self.assertEqual([call.args[1].id for call in perform.call_args_list], ["shop01", "shop02", "piaoju"])

    def test_manual_round_runs_while_disabled_and_cannot_overlap(self):
        self.settings()
        entered, release = threading.Event(), threading.Event()
        def perform(_action, _env):
            entered.set()
            self.assertTrue(release.wait(2))
            return SUCCESS
        with patch.object(self.service, "_perform", side_effect=perform):
            job = self.service.run_maintenance()
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(BrowserServiceError) as error:
                    self.service.run_maintenance()
                self.assertEqual(error.exception.code, "maintenance_busy")
            finally:
                release.set()
            self.service._queue.join()
        self.assertEqual(job["status"], "queued")
        value = self.service.maintenance_snapshot()
        self.assertFalse(value["running"])
        self.assertEqual(value["last_status"], "complete")
        self.assertEqual([row["id"] for row in value["last_results"]], ["shop01", "shop02", "piaoju"])
        self.assertIsNone(value["next_run_at"])

    def test_missed_schedule_runs_once_without_snapshot_polling(self):
        self.settings(enabled=True)
        with patch.object(self.service, "_perform", return_value=SUCCESS) as perform:
            self.due()
            self.service._queue.join()
            self.service._maintenance_tick()
        self.assertEqual(perform.call_count, 3)
        self.assertEqual(len(self.service._jobs), 1)
        self.assertGreater(datetime.fromisoformat(self.service.maintenance_snapshot()["next_run_at"]), datetime.now(timezone.utc))

    def test_server_worker_wakes_due_schedule_without_frontend(self):
        self.settings(enabled=True)
        finished = threading.Event()
        def perform(_action, _env):
            finished.set()
            return SUCCESS
        with patch.object(self.service, "_perform", side_effect=perform):
            self.service._maintenance["next_run_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            self.assertTrue(finished.wait(2), "server worker must start a due round without snapshot()")
            self.service._queue.join()

    def test_explicit_actions_take_priority_between_environments(self):
        self.settings()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def perform(action, env):
            calls.append((action, env.id))
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(2))
            return SUCCESS
        with patch.object(self.service, "_perform", side_effect=perform):
            self.service.run_maintenance()
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.service._pending.get("shop01", 0), 1)
            self.assertEqual(self.service._pending.get("shop02", 0), 0,
                             "unvisited shops must remain available for manual actions")
            self.service.submit("focus", ["shop02"])
            self.assertEqual(self.service._pending.get("shop02", 0), 1)
            release.set()
            self.service._queue.join()
        self.assertEqual(calls, [("maintain-session", "shop01"), ("focus", "shop02"),
                                 ("maintain-session", "shop02"), ("maintain-session", "piaoju")])
        self.assertTrue(all(value == 0 for value in self.service._pending.values()))

    def test_internal_maintenance_fault_does_not_stop_explicit_actions(self):
        self.settings()
        finished = threading.Event()
        def perform(_action, _env):
            finished.set()
            return SUCCESS
        with patch.object(self.service, "_maintenance_step", side_effect=RuntimeError("secret")), \
                patch.object(self.service, "_perform", side_effect=perform):
            self.service.run_maintenance()
            self.service.submit("focus", ["shop02"])
            self.assertTrue(finished.wait(2))
            self.service._queue.join()
        self.assertTrue(self.service._worker.is_alive())
        self.assertFalse(self.service.maintenance_snapshot()["running"])
        self.assertEqual(self.service.maintenance_snapshot()["last_status"], "failed")
        self.assertEqual(self.service._pending.get("shop02", 0), 0)
        self.assertNotIn("secret", json.dumps(self.service.maintenance_snapshot()))

    def test_clock_fault_does_not_stop_explicit_actions(self):
        finished = threading.Event()
        def perform(_action, _env):
            finished.set()
            return SUCCESS
        with patch.object(self.service, "_maintenance_tick", side_effect=RuntimeError("secret")), \
                patch.object(self.service, "_perform", side_effect=perform):
            self.service.submit("focus", ["shop02"])
            self.assertTrue(finished.wait(2))
            self.service._queue.join()
        self.assertTrue(self.service._worker.is_alive())

    def test_pause_finishes_current_environment_and_skips_rest(self):
        self.settings(enabled=True, include_shared=True)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def perform(_action, env):
            calls.append(env.id)
            entered.set()
            self.assertTrue(release.wait(2))
            return SUCCESS
        with patch.object(self.service, "_perform", side_effect=perform):
            self.due()
            self.assertTrue(entered.wait(1))
            self.settings(enabled=False, include_shared=True)
            release.set()
            self.service._queue.join()
        self.assertEqual(calls, ["shop01"])
        state = self.service.maintenance_snapshot()
        self.assertEqual(state["last_status"], "paused")
        self.assertEqual([row["status"] for row in state["last_results"]], ["complete", "skipped", "skipped"])
        self.assertIsNone(state["next_run_at"])
        self.assertTrue(all(value == 0 for value in self.service._pending.values()))

    def test_close_all_stops_current_manual_round_without_reopening_closed_shops(self):
        self.settings(enabled=True, include_shared=True)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def perform(action, env):
            calls.append((action, env.id))
            if action == "maintain-session":
                entered.set()
                self.assertTrue(release.wait(2))
            return SUCCESS
        with patch.object(self.service, "_perform", side_effect=perform):
            self.service.run_maintenance()
            self.assertTrue(entered.wait(1))
            result = self.service.close_all_shops({"pause_maintenance": False})
            with self.assertRaises(BrowserServiceError) as error:
                self.service.run_maintenance()
            self.assertEqual(error.exception.code, "maintenance_busy")
            release.set()
            self.service._queue.join()
        self.assertEqual(calls, [("maintain-session", "shop01"), ("close", "shop01"),
                                 ("close", "shop02"), ("close", "piaoju")])
        self.assertTrue(self.service.maintenance_snapshot()["enabled"])
        self.assertEqual(self.service.maintenance_snapshot()["last_status"], "paused")
        self.assertIsNotNone(self.service.maintenance_snapshot()["next_run_at"])
        job = next(job for job in self.service._jobs if job["id"] == result["id"])
        self.assertEqual(job["status"], "complete")

    def test_close_all_pause_choice_is_persisted_before_close_actions(self):
        self.settings(enabled=True)
        def perform(_action, _env):
            self.assertFalse(self.service.maintenance_snapshot()["enabled"])
            stored, _ = maintenance.load(self.root / maintenance.FILE_NAME,
                                         {key: env.name for key, env in self.service.environments.items()})
            self.assertFalse(stored["enabled"])
            return SUCCESS
        with patch.object(self.service, "_perform", side_effect=perform):
            self.service.close_all_shops({"pause_maintenance": True})
            self.service._queue.join()
        self.assertIsNone(self.service.maintenance_snapshot()["next_run_at"])

    def test_close_all_pause_write_failure_does_not_enqueue_close(self):
        self.settings(enabled=True)
        with patch.object(maintenance, "save", side_effect=PermissionError("denied")):
            with self.assertRaises(BrowserServiceError) as error:
                self.service.close_all_shops({"pause_maintenance": True})
        self.assertEqual(error.exception.code, "maintenance_storage_failed")
        self.assertIsNone(self.service._close_all_job_id)
        self.assertEqual(self.service._jobs, [])
        self.assertTrue(self.service.maintenance_snapshot()["enabled"])

    def test_ordinary_pause_does_not_cancel_manual_round(self):
        self.settings(enabled=True)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def perform(_action, env):
            calls.append(env.id)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(2))
            return SUCCESS
        with patch.object(self.service, "_perform", side_effect=perform):
            self.service.run_maintenance()
            self.assertTrue(entered.wait(1))
            self.settings(enabled=False)
            release.set()
            self.service._queue.join()
        self.assertEqual(calls, ["shop01", "shop02", "piaoju"])
        self.assertEqual(self.service.maintenance_snapshot()["last_status"], "complete")

    def test_external_profile_lock_skips_only_that_environment(self):
        self.settings()
        env = self.service.environments["shop01"]
        lock = ProfileLock(env.root, "Default")
        lock.acquire()
        try:
            with patch.object(self.service, "_perform", return_value=SUCCESS) as perform:
                self.service.run_maintenance()
                self.service._queue.join()
            self.assertEqual([call.args[1].id for call in perform.call_args_list], ["shop02", "piaoju"])
            state = self.service.maintenance_snapshot()
            self.assertEqual(state["last_results"][0]["code"], "profile_locked")
        finally:
            lock.release()

    def test_single_environment_failure_does_not_block_other_environments(self):
        self.settings()
        with patch.object(self.service, "_perform", side_effect=[RuntimeError("secret"), SUCCESS, SUCCESS]):
            self.service.run_maintenance()
            self.service._queue.join()
        state = self.service.maintenance_snapshot()
        self.assertEqual(state["last_status"], "partial")
        self.assertEqual([row["status"] for row in state["last_results"]], ["failed", "complete", "complete"])
        self.assertNotIn("secret", json.dumps(state))

    def test_login_required_marker_survives_restart_and_manual_recheck_can_clear_it(self):
        self.settings(enabled=True)
        with patch.object(self.service, "_perform", side_effect=[{"auth": {"status": "required"}}, SUCCESS, SUCCESS]):
            self.due()
            self.service._queue.join()
        self.assertIn("shop01", self.service._maintenance_required)
        names = {key: env.name for key, env in self.service.environments.items()}
        state, blocked = maintenance.load(self.root / maintenance.FILE_NAME, names)
        self.assertIn("shop01", blocked)
        self.assertEqual(state["last_results"][0]["code"], "login_required")
        with patch.object(self.service, "_perform", return_value=SUCCESS) as perform:
            self.due()
            self.service._queue.join()
            self.assertEqual([call.args[1].id for call in perform.call_args_list], ["shop02", "piaoju"])
            perform.reset_mock()
            self.service.run_maintenance()
            self.service._queue.join()
            self.assertEqual([call.args[1].id for call in perform.call_args_list], ["shop01", "shop02", "piaoju"])
        self.assertNotIn("shop01", self.service._maintenance_required)

    def test_interrupted_round_is_not_resumed_as_overlapping_running_state(self):
        state = maintenance.initial_state()
        state.update(enabled=True, running=True, last_status="running", next_run_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
        maintenance.save(self.root / maintenance.FILE_NAME, state, {"shop01"})
        restored, blocked = maintenance.load(self.root / maintenance.FILE_NAME,
                                             {key: env.name for key, env in self.service.environments.items()})
        self.assertFalse(restored["running"])
        self.assertEqual(restored["last_status"], "partial")
        self.assertIn("shop01", blocked)
        self.assertLess(datetime.fromisoformat(restored["next_run_at"]), datetime.now(timezone.utc))

    def test_home_config_does_not_mutate_invoice_skill_urls(self):
        env = self.service.environments["shop01"]
        self.assertEqual(env.config["browser_sessions"], {"home": {"url": service_module.QIANNIU_HOME_URL}})
        original = json.loads(env.config_path.read_text(encoding="utf-8"))
        self.assertEqual(set(original["browser_sessions"]), {"invoice", "orders"})
        self.assertEqual(original["browser_sessions"]["invoice"]["url"], fixtures.URLS["invoice"])

    def test_url_maintenance_visits_home_saves_its_cookies_and_completes_assumed(self):
        env = self.generic_environment()
        page = Mock(url=fixtures.CUSTOM_HOME)
        controller = self.probe_controller(page)
        self.service._maintenance_required.add(env.id)
        def perform(_action, current):
            return asyncio.run(self.service._maintain_browser(current)) if current.id == env.id else SUCCESS
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller), \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                             return_value={"status": "assumed", "message": "推定已登录"}) as probe, \
                patch.object(service_module, "persist_session_cookies", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved"}) as save, \
                patch.object(self.service, "_perform", side_effect=perform):
            self.service.run_maintenance()
            self.service._queue.join()
        probe.assert_awaited_once_with(page, fixtures.CUSTOM_HOME, service_module.login_settings.DEFAULTS)
        self.assertEqual(save.call_args.args[1:3], (set(), {"portal.example.test"}))
        self.assertEqual(self.service._auth[env.id]["status"], "assumed")
        self.assertNotIn(env.id, self.service._maintenance_required)
        state = self.service.maintenance_snapshot()
        self.assertEqual(state["last_status"], "complete")
        self.assertEqual(state["last_results"][0]["status"], "complete")

    def test_home_matching_never_reuses_invoice_or_order_pages(self):
        expected = service_module.QIANNIU_HOME_URL
        for url in (expected, expected + "home.htm", expected + "home.htm/QnworkbenchHome/"):
            self.assertTrue(_same_role_page(url, expected))
        for url in (fixtures.URLS["invoice"], fixtures.URLS["orders"], expected + "home.htm/QnworkbenchHome/other"):
            self.assertFalse(_same_role_page(url, expected))

    def test_missing_home_is_created_once_without_changing_skill_tabs(self):
        env = self.service.environments["shop01"]
        controller = PlaywrightBrowserController(env.config)
        invoice = Mock(url=fixtures.URLS["invoice"], is_closed=Mock(return_value=False), goto=AsyncMock(), close=AsyncMock())
        orders = Mock(url=fixtures.URLS["orders"], is_closed=Mock(return_value=False), goto=AsyncMock(), close=AsyncMock())
        home = Mock(url=service_module.QIANNIU_HOME_URL, is_closed=Mock(return_value=False), goto=AsyncMock())
        controller.context = SimpleNamespace(pages=[invoice, orders], new_page=AsyncMock(return_value=home))
        async def run():
            await controller.register_roles(open_missing=True)
            await controller.register_roles(open_missing=True)
        asyncio.run(run())
        controller.context.new_page.assert_awaited_once()
        home.goto.assert_awaited_once()
        invoice.goto.assert_not_awaited()
        orders.goto.assert_not_awaited()
        invoice.close.assert_not_awaited()
        orders.close.assert_not_awaited()

    def test_maintenance_uses_the_shared_probe_without_focus_or_platform_gate(self):
        env = self.service.environments["shop01"]
        page = Mock(url=env.home_url, bring_to_front=AsyncMock())
        controller = self.probe_controller(page)
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller) as factory, \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                             return_value={"status": "assumed", "message": "推定已登录"}) as probe, \
                patch.object(self.service, "_save_session", new_callable=AsyncMock, return_value={**empty_status(), "status": "saved"}):
            result = asyncio.run(self.service._maintain_browser(env))
        self.assertEqual(result["auth"]["status"], "assumed")
        self.assertTrue(factory.call_args.kwargs["login_probe_mode"])
        controller.start.assert_awaited_once_with(open_missing=False)
        probe.assert_awaited_once_with(page, env.home_url, service_module.login_settings.DEFAULTS)
        page.bring_to_front.assert_not_awaited()
        controller.stop.assert_not_called()
        controller.close.assert_awaited_once()

    def test_maintenance_final_login_redirect_is_required_without_saving_or_retry(self):
        env = self.service.environments["shop01"]
        page = Mock(url="https://login.example.test/enter")
        controller = self.probe_controller(page)
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller), \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                             return_value={"status": "required", "message": "需要登录"}) as probe, \
                patch.object(self.service, "_save_session", new_callable=AsyncMock) as save:
            value = asyncio.run(self.service._maintain_browser(env))
        self.assertEqual(value["auth"]["status"], "required")
        self.assertIn("shop01", self.service._maintenance_required)
        probe.assert_awaited_once()
        save.assert_not_awaited()



if __name__ == "__main__":
    unittest.main()
