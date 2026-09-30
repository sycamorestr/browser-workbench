from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend import browser_service as service_module
from backend.browser_service import BrowserService, BrowserServiceError, normalized_path
from backend.vendor.browser_controller import BrowserControllerError, ProfileLock
from backend.session_cookies import empty_status, METADATA_NAME


URLS = {
    "home": "https://myseller.taobao.com/",
    "invoice": "https://myseller.taobao.com/home.htm/merchant-invoice/",
    "orders": "https://myseller.taobao.com/home.htm/trade-platform/tp/sold",
    "goods": "https://fp.erp321.com/setting/goodsManage",
}
CUSTOM_HOME = "https://portal.example.test/workspace?tenant=demo#overview"


class BrowserServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "outputs").mkdir()
        for index, env_id in enumerate(("piaoju", "shop01", "shop02")):
            roles = ("goods",) if env_id == "piaoju" else ("invoice", "orders")
            (self.root / env_id).mkdir()
            config = {"schema_version": 1, "browser": "Edge", "user_data_dir": env_id,
                      "remote_debugging_port": 19000 + index,
                      "browser_sessions": {role: {"url": URLS[role]} for role in roles}}
            (self.root / f"{env_id}.json").write_text(json.dumps(config), encoding="utf-8")
        registry = {"schema_version": 1, "issuer": "示例有限公司", "jst_browser_config": "piaoju.json",
                    "output_root": "outputs", "shops": [
                        {"id": "shop01", "store": "示例店铺一", "browser_config": "shop01.json"},
                        {"id": "shop02", "store": "示例店铺二", "browser_config": "shop02.json"},
                    ]}
        self.registry = self.root / "shops.json"
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            self.service = BrowserService(self.registry)

    def tearDown(self):
        self.service.shutdown()
        self.temporary.cleanup()

    def process(self, env_id="shop01", **overrides):
        env = self.service.environments[env_id]
        return {"pid": 321, "root": normalized_path(env.root), "profile": "Default", "port": env.port,
                "name": "msedge.exe", "started_at": "2026-01-01T00:00:00Z", **overrides}

    def generic_environment(self, home_url=CUSTOM_HOME):
        previous = self.service.environments["shop01"]
        config = json.loads(previous.config_path.read_text(encoding="utf-8"))
        config["browser_sessions"] = {"home": {"url": home_url}}
        previous.config_path.write_text(json.dumps(config), encoding="utf-8")
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            env = self.service._load_environment("shop01", previous.name, "shop", str(previous.config_path))
        self.service.environments[env.id] = env
        return env

    @staticmethod
    def probe_controller(page):
        return Mock(start=AsyncMock(), connect=AsyncMock(), close=AsyncMock(),
                    probe_page=AsyncMock(return_value=(page, "probe-target")))

    @staticmethod
    def playwright(browser):
        runtime = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser)),
                                  stop=AsyncMock())
        factory = Mock(return_value=SimpleNamespace(start=AsyncMock(return_value=runtime)))
        return runtime, patch.dict(sys.modules, {"playwright.async_api": SimpleNamespace(async_playwright=factory)})

    def test_snapshot_uses_one_cached_inventory_and_probes_only_owned_processes(self):
        record = self.process()
        with patch.object(service_module, "enumerate_browser_processes", return_value=[record]) as inventory, \
                patch.object(service_module, "probe_cdp", return_value={"available": True, "tabs_count": 2,
                                  "tabs": [{"url": URLS["invoice"], "title": "业务页"}]}) as probe:
            first = self.service.snapshot()
            second = self.service.snapshot()
        inventory.assert_called_once()
        probe.assert_called_once_with(record["port"])
        self.assertEqual(first["summary"]["running"], 1)
        self.assertEqual(second["summary"]["connected"], 1)
        by_id = {row["id"]: row for row in first["environments"]}
        self.assertEqual(by_id["shop01"]["cdp"], "connected")
        self.assertEqual(by_id["piaoju"]["cdp"], "stopped")
        self.assertEqual(by_id["shop01"]["configured_urls"], {"home": URLS["home"]})
        self.assertTrue(by_id["shop01"]["cdp_endpoint"].endswith(str(record["port"])))
        self.assertEqual(by_id["shop01"]["tabs_count"], 2)
        self.assertTrue(all(row["kind"] == "browser" for row in first["environments"]))
        self.assertEqual(by_id["piaoju"]["name"], "票聚")

    def test_registry_without_legacy_site_starts_and_empty_install_can_create_environment(self):
        self.service.shutdown()
        raw = json.loads(self.registry.read_text(encoding="utf-8"))
        raw.pop("jst_browser_config")
        self.registry.write_text(json.dumps(raw), encoding="utf-8")
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            self.service = BrowserService(self.registry)
        self.assertEqual(set(self.service.environments), {"shop01", "shop02"})
        self.service.shutdown()
        raw["shops"] = []
        self.registry.write_text(json.dumps(raw), encoding="utf-8")
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            self.service = BrowserService(self.registry)
        self.assertEqual(self.service.environments, {})
        with patch.object(service_module, "enumerate_browser_processes", return_value=[]):
            self.assertEqual(self.service.snapshot()["summary"]["total"], 0)
        for action in (lambda: self.service.close_all_shops({"pause_maintenance": True}), self.service.run_maintenance):
            with self.assertRaises(BrowserServiceError) as caught:
                action()
            self.assertEqual(caught.exception.code, "no_environments")
        with patch.object(service_module, "PlaywrightBrowserController") as browser:
            created = self.service.create_shop({"name": "独立浏览器", "parent_folder": str(self.root),
                                                "login_username": "", "home_url": CUSTOM_HOME})
            browser.assert_not_called()
        self.assertEqual(created["kind"], "browser")
        self.assertIn(created["id"], self.service.environments)
        self.assertNotIn("piaoju", self.service.environments)

    def test_legacy_site_keeps_profile_port_roles_and_cookie_scope_after_public_unification(self):
        env = self.service.environments["piaoju"]
        self.assertEqual(env.kind, "shared")
        self.assertEqual(env.root, self.root / "piaoju")
        self.assertEqual(env.port, 19000)
        self.assertEqual(env.config["browser_sessions"], {"goods": {"url": URLS["goods"]}})
        browser = Mock()
        with patch.object(service_module, "persist_session_cookies", new_callable=AsyncMock,
                          return_value={**empty_status(), "status": "saved"}) as save:
            asyncio.run(self.service._save_session(env, browser))
        self.assertEqual(save.call_args.args[1], {"erp321.com"})
        self.assertEqual(save.call_args.args[2], {"fp.erp321.com"})

    def test_default_url_checks_use_probe_without_platform_scripts_for_all_sites(self):
        for env_id in ("shop01", "piaoju"):
            env = self.service.environments[env_id]
            page = Mock(url=env.home_url)
            with patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                              return_value={"status": "assumed", "message": "推定已登录"}) as probe, \
                    patch.object(self.service, "_platform_auth", new_callable=AsyncMock) as platform:
                auth = asyncio.run(self.service._check_auth(env, page))
            self.assertEqual(auth["status"], "assumed")
            probe.assert_awaited_once_with(page, env.home_url, service_module.login_settings.DEFAULTS)
            platform.assert_not_awaited()

    def test_generic_start_uses_configured_home_and_saves_only_its_site(self):
        env = self.generic_environment()
        page = Mock(url=CUSTOM_HOME)
        controller = self.probe_controller(page)
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller) as factory, \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                             return_value={"status": "assumed", "message": "推定已登录"}) as probe, \
                patch.object(service_module, "persist_session_cookies", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved"}) as save:
            value = asyncio.run(self.service._browser_action("start", env))
        self.assertEqual(factory.call_args.args[0]["browser_sessions"], {"home": {"url": CUSTOM_HOME}})
        self.assertTrue(factory.call_args.kwargs["login_probe_mode"])
        self.assertEqual(value["auth"]["status"], "assumed")
        self.assertEqual(value["cookie_sync"]["status"], "saved")
        self.assertEqual(save.call_args.args[1:3], (set(), {"portal.example.test"}))
        probe.assert_awaited_once_with(page, CUSTOM_HOME, service_module.login_settings.DEFAULTS)
        controller.close.assert_awaited_once()

    def test_auth_adapter_uses_configured_home_instead_of_legacy_environment_kind(self):
        self.assertEqual(self.generic_environment().auth_adapter, "generic")
        self.assertEqual(self.generic_environment("https://myseller.taobao.com.attacker.test/").auth_adapter, "generic")
        self.assertEqual(self.generic_environment(URLS["home"]).auth_adapter, "qianniu")
        env = self.generic_environment(URLS["goods"])
        self.assertEqual(env.auth_adapter, "jst")
        self.assertEqual(env.home_role, "home")
        self.assertEqual(env.kind, "shop")

    def test_generic_page_selection_does_not_reuse_another_login_tenant(self):
        env = self.generic_environment("https://portal.example.test/login?tenant=one")
        other = Mock(url="https://portal.example.test/login?tenant=two", is_closed=Mock(return_value=False))
        with self.assertRaises(BrowserServiceError) as caught:
            self.service._business_page(env, [other])
        self.assertEqual(caught.exception.code, "page_missing")

    def test_foreign_profile_port_conflict_is_not_contacted_or_claimed_running(self):
        record = self.process(root=normalized_path(self.root / "foreign"))
        with patch.object(service_module, "enumerate_browser_processes", return_value=[record]), \
                patch.object(service_module, "probe_cdp") as probe:
            snapshot = self.service.snapshot()
        row = next(row for row in snapshot["environments"] if row["id"] == "shop01")
        self.assertEqual(row["cdp"], "conflict")
        self.assertFalse(row["running"])
        probe.assert_not_called()

    def test_inventory_failure_is_visible(self):
        with patch.object(service_module, "enumerate_browser_processes",
                          side_effect=BrowserServiceError("进程查询失败", "process_inventory_failed")):
            snapshot = self.service.snapshot()
        self.assertEqual(snapshot["error"]["code"], "process_inventory_failed")
        self.assertTrue(all(row["cdp"] == "unavailable" for row in snapshot["environments"]))

    def test_process_restart_invalidates_cached_auth_without_login_queries(self):
        with patch.object(service_module, "enumerate_browser_processes", side_effect=[
            [self.process()], [self.process(started_at="2026-01-02T00:00:00Z")]]), \
                patch.object(service_module, "probe_cdp", return_value={"available": True, "tabs_count": 1}):
            self.service.snapshot()
            self.service._set_auth("shop01", "verified", "已核验", "示例店铺一")
            self.service._inventory_at = 0
            snapshot = self.service.snapshot()
        row = next(row for row in snapshot["environments"] if row["id"] == "shop01")
        self.assertEqual(row["auth"]["status"], "unchecked")
        self.assertIsNone(row["auth"]["checked_at"])

    def test_auth_cache_expires_after_fifteen_minutes_preserving_previous_check_time(self):
        self.service._set_auth("shop01", "verified", "已核验", "示例店铺一")
        previous = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        self.service._auth["shop01"]["checked_at"] = previous
        with patch.object(service_module, "enumerate_browser_processes", return_value=[]):
            snapshot = self.service.snapshot()
        row = next(row for row in snapshot["environments"] if row["id"] == "shop01")
        self.assertEqual(row["auth"]["status"], "unchecked")
        self.assertEqual(row["auth"]["checked_at"], previous)

    def test_external_profile_lock_probe_is_read_only(self):
        env = self.service.environments["shop01"]
        lock = ProfileLock(env.root, "Default")
        self.assertFalse(service_module.profile_busy(env.root))
        self.assertFalse(lock.path.exists())
        lock.acquire()
        try:
            self.assertTrue(service_module.profile_busy(env.root))
        finally:
            lock.release()
        before = lock.path.read_bytes()
        self.assertFalse(service_module.profile_busy(env.root))
        self.assertEqual(lock.path.read_bytes(), before)

    def test_submission_rejects_unknown_actions_ids_and_duplicates(self):
        for action, ids, code in (("delete", ["shop01"], "invalid_action"),
                                  ("close", ["foreign"], "invalid_environment"),
                                  ("focus", [], "invalid_environment"),
                                  ("start", ["shop01", "shop01"], "invalid_environment")):
            with self.assertRaises(BrowserServiceError) as error:
                self.service.submit(action, ids)
            self.assertEqual(error.exception.code, code)

    def test_queue_is_serial_and_busy_includes_waiting_work(self):
        entered = threading.Event()
        finish = threading.Event()
        calls = []
        def action(action, env):
            calls.append((action, env.id))
            if len(calls) == 1:
                entered.set()
                self.assertTrue(finish.wait(2))
            return {"message": "完成"}
        with patch.object(self.service, "_perform", side_effect=action), \
                patch.object(service_module, "enumerate_browser_processes", return_value=[]):
            first = self.service.submit("start", ["shop01"])
            self.assertTrue(entered.wait(1))
            self.service.submit("focus", ["shop02"])
            snapshot = self.service.snapshot()
            self.assertEqual(first["status"], "queued")
            self.assertEqual(snapshot["summary"]["busy"], 2)
            self.assertEqual(len(calls), 1)
            finish.set()
            self.service._queue.join()
        self.assertEqual(calls, [("start", "shop01"), ("focus", "shop02")])
        self.assertTrue(all(job["status"] == "complete" for job in self.service._jobs))

    def test_shutdown_drains_active_and_queued_actions_without_adding_browser_close(self):
        entered, release, drained = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def perform(action, env):
            calls.append((action, env.id))
            if len(calls) == 1:
                entered.set()
                release.wait()
            return {"message": "完成"}
        def drain():
            self.service.shutdown()
            drained.set()
        waiter = threading.Thread(target=drain, daemon=True)
        with patch.object(self.service, "_perform", side_effect=perform), \
                patch.object(service_module, "PlaywrightBrowserController") as controller:
            try:
                self.service.submit("focus", ["shop01"])
                self.assertTrue(entered.wait(1))
                self.service.submit("save-session", ["shop02", "piaoju"])
                self.service.shutdown(wait=False)
                self.service.shutdown(wait=False)
                waiter.start()
                self.assertFalse(drained.wait(0.1), "a repeated shutdown must still wait for busy work")
                self.assertTrue(self.service._worker.is_alive())
                release.set()
                self.assertTrue(drained.wait(2))
            finally:
                release.set()
                if waiter.ident is not None:
                    waiter.join(2)
            controller.assert_not_called()
        self.assertEqual(calls, [("focus", "shop01"), ("save-session", "shop02"), ("save-session", "piaoju")])
        self.assertFalse(self.service._worker.is_alive())
        self.assertEqual(self.service._queue.unfinished_tasks, 0)
        self.assertTrue(all(job["status"] == "complete" for job in self.service._jobs))

    def test_stopping_rejects_mutations_and_stops_automatic_work_without_changing_settings(self):
        settings = {"enabled": True, "interval_minutes": 120, "include_shared": True}
        self.service.update_maintenance(settings)
        persisted = self.service._maintenance_path.read_bytes()
        self.service.shutdown()
        mutations = [lambda: self.service.submit("start", ["shop01"]),
                     lambda: self.service.close_all_shops({"pause_maintenance": True}),
                     lambda: self.service.create_shop({}),
                     lambda: self.service.update_maintenance(settings),
                     lambda: self.service.update_session_autosave({"enabled": False, "interval_minutes": 5}),
                     self.service.run_maintenance]
        for mutate in mutations:
            with self.assertRaises(BrowserServiceError) as caught:
                mutate()
            self.assertEqual(caught.exception.code, "service_closed")
        self.service._maintenance["next_run_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.service._next_cookie_sync = {key: 0 for key in self.service.environments}
        with patch.object(self.service, "_perform") as action, \
                patch.object(self.service, "_browser_action", new_callable=AsyncMock) as checkpoint:
            self.service._maintenance_tick()
            self.service._idle_checkpoint()
            action.assert_not_called()
            checkpoint.assert_not_awaited()
        self.assertTrue(self.service.maintenance_snapshot()["enabled"])
        self.assertEqual(self.service._maintenance_path.read_bytes(), persisted)

    def test_shutdown_finishes_current_maintenance_shop_and_skips_rest_before_manual_queue(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        self.service.update_maintenance({"enabled": True, "interval_minutes": 120, "include_shared": True})
        def perform(action, env):
            calls.append((action, env.id))
            if action == "maintain-session":
                entered.set()
                release.wait()
            return {"auth": {"status": "verified"}, "message": "完成"}
        with patch.object(self.service, "_perform", side_effect=perform):
            try:
                self.service.run_maintenance()
                self.assertTrue(entered.wait(1))
                self.service.submit("focus", ["piaoju"])
                self.service.shutdown(wait=False)
            finally:
                release.set()
            self.service.shutdown()
        self.assertEqual(calls, [("maintain-session", "shop01"), ("focus", "piaoju")])
        maintenance = self.service.maintenance_snapshot()
        self.assertFalse(maintenance["running"])
        self.assertTrue(maintenance["enabled"])
        self.assertEqual(maintenance["last_status"], "paused")
        self.assertEqual([result["status"] for result in maintenance["last_results"]], ["complete", "skipped", "skipped"])
        self.assertEqual(self.service._queue.unfinished_tasks, 0)

    def test_shutdown_waits_for_shop_creation_that_already_started_writing(self):
        entered, release, drained = threading.Event(), threading.Event(), threading.Event()
        original = service_module.shop_registry.create_shop
        created = []
        def create(*args):
            entered.set()
            release.wait()
            return original(*args)
        def drain():
            self.service.shutdown()
            drained.set()
        creator = threading.Thread(target=lambda: created.append(self.service.create_shop(
            {"name": "停机前创建", "parent_folder": str(self.root), "login_username": "", "home_url": CUSTOM_HOME})), daemon=True)
        waiter = threading.Thread(target=drain, daemon=True)
        with patch.object(service_module.shop_registry, "create_shop", side_effect=create):
            try:
                creator.start()
                self.assertTrue(entered.wait(1))
                self.service.shutdown(wait=False)
                waiter.start()
                self.assertFalse(drained.wait(0.1))
            finally:
                release.set()
                creator.join(3)
                if waiter.ident is not None:
                    waiter.join(3)
        self.assertTrue(drained.is_set())
        self.assertEqual(len(created), 1)
        self.assertIn(created[0]["id"], self.service.environments)

    def test_start_reuses_controller_and_does_not_force_close_native_browser(self):
        env = self.service.environments["shop01"]
        page = Mock(url=env.home_url)
        controller = self.probe_controller(page)
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller), \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                             return_value={"status": "assumed", "message": "推定已登录"}), \
                patch.object(self.service, "_save_session", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved"}) as save:
            value = asyncio.run(self.service._browser_action("start", env))
        controller.start.assert_awaited_once_with(open_missing=False)
        controller.close.assert_awaited_once()
        controller.stop.assert_not_called()
        self.assertEqual(value["auth"]["status"], "assumed")
        save.assert_awaited_once()

    def test_final_login_redirect_sets_required_without_saving_and_keeps_probe_target(self):
        env = self.service.environments["piaoju"]
        page = Mock(url="https://identity.example.test/login")
        controller = self.probe_controller(page)
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller), \
                patch.object(self.service, "_require_owner", return_value=self.process("piaoju")), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                             return_value={"status": "required", "message": "需要登录"}), \
                patch.object(self.service, "_save_session", new_callable=AsyncMock) as save:
            value = asyncio.run(self.service._browser_action("start", env))
        self.assertEqual(value["auth"]["status"], "required")
        self.assertIn(env.id, self.service._maintenance_required)
        self.assertEqual(self.service._probe_targets[env.id]["url"], page.url)
        save.assert_not_awaited()
        controller.close.assert_awaited_once()

    def test_probe_connection_failure_marks_error_and_disconnects_without_stopping_browser(self):
        controller = Mock(start=AsyncMock(side_effect=BrowserControllerError("failed", "browser_launch_failed")), close=AsyncMock())
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller):
            with self.assertRaises(BrowserControllerError):
                asyncio.run(self.service._browser_action("start", self.service.environments["piaoju"]))
        self.assertEqual(self.service._auth["piaoju"]["status"], "error")
        controller.close.assert_awaited_once()
        controller.stop.assert_not_called()

    def test_check_login_does_not_start_a_stopped_browser(self):
        with patch.object(service_module, "enumerate_browser_processes", return_value=[]), \
                patch.object(service_module, "PlaywrightBrowserController") as create:
            asyncio.run(self.service._browser_action("check-login", self.service.environments["shop01"]))
        self.assertEqual(self.service._auth["shop01"]["status"], "unchecked")
        create.assert_not_called()

    def test_close_already_stopped_environment_is_idempotent_without_cdp(self):
        with patch.object(service_module, "enumerate_browser_processes", return_value=[]), \
                patch.object(service_module, "PlaywrightBrowserController") as create, \
                patch.object(self.service, "_save_session", new_callable=AsyncMock) as save:
            result = asyncio.run(self.service._browser_action("close", self.service.environments["shop01"]))
        self.assertIn("已关闭", result["message"])
        create.assert_not_called()
        save.assert_not_awaited()

    def test_close_all_validates_pause_option_without_enqueuing(self):
        for payload in ({}, {"pause_maintenance": 1}, {"pause_maintenance": "true"},
                        {"pause_maintenance": True, "ids": ["piaoju"]}, None):
            with self.assertRaises(BrowserServiceError) as error:
                self.service.close_all_shops(payload)
            self.assertEqual(error.exception.code, "invalid_close_all")
        self.assertEqual(self.service._jobs, [])

    def test_close_all_includes_all_environments_after_queued_start_including_legacy_shared(self):
        entered, finish = threading.Event(), threading.Event()
        calls = []
        def perform(action, env):
            calls.append((action, env.id))
            if action == "start":
                entered.set()
                self.assertTrue(finish.wait(2))
            return {"message": "完成"}
        with patch.object(self.service, "_perform", side_effect=perform):
            self.service.submit("start", ["shop01"])
            self.assertTrue(entered.wait(1))
            result = self.service.close_all_shops({"pause_maintenance": False})
            try:
                with self.assertRaises(BrowserServiceError) as error:
                    self.service.close_all_shops({"pause_maintenance": False})
                self.assertEqual(error.exception.code, "close_all_busy")
                close_job = next(job for job in self.service._jobs if job["id"] == result["id"])
                self.assertTrue(close_job["close_all"])
                self.assertEqual(close_job["ids"], ["shop01", "shop02", "piaoju"])
            finally:
                finish.set()
            self.service._queue.join()
        self.assertEqual(calls, [("start", "shop01"), ("close", "shop01"), ("close", "shop02"), ("close", "piaoju")])
        self.assertIsNone(self.service._close_all_job_id)

    def test_close_all_one_failure_does_not_block_other_shops_or_future_close_all(self):
        with patch.object(self.service, "_perform", side_effect=[
                BrowserServiceError("会话保存失败", "cookie_sync_failed"), {"message": "已关闭"}, {"message": "已关闭"}]) as perform:
            result = self.service.close_all_shops({"pause_maintenance": True})
            self.service._queue.join()
        self.assertEqual(perform.call_count, 3)
        job = next(job for job in self.service._jobs if job["id"] == result["id"])
        self.assertEqual(job["status"], "partial")
        self.assertEqual([row["status"] for row in job["results"]], ["failed", "complete", "complete"])
        self.assertIsNone(self.service._close_all_job_id)
        with patch.object(self.service, "_perform", return_value={"message": "已关闭"}):
            self.service.close_all_shops({"pause_maintenance": False})
            self.service._queue.join()

    def test_create_shop_registers_without_start_and_survives_service_restart(self):
        original = self.registry.read_bytes()
        parent = self.root / "custom"
        parent.mkdir()
        with patch.object(service_module, "PlaywrightBrowserController") as create:
            result = self.service.create_shop({"name": "自定义测试店", "parent_folder": str(parent),
                                               "login_username": "test-account", "home_url": CUSTOM_HOME})
        create.assert_not_called()
        self.assertEqual(result["kind"], "browser")
        self.assertEqual(self.registry.read_bytes(), original)
        env = self.service.environments[result["id"]]
        self.assertEqual(env.login_username, "test-account")
        self.assertEqual(env.config["browser_sessions"], {"home": {"url": CUSTOM_HOME}})
        self.assertEqual(result["home_url"], CUSTOM_HOME)
        self.assertEqual(self.service._auth[env.id]["status"], "unchecked")
        self.assertEqual(self.service._pending[env.id], 0)
        self.assertIn(env.id, self.service._next_cookie_sync)
        self.assertEqual(self.service._cookie_sync[env.id]["status"], "idle")
        with patch.object(service_module, "enumerate_browser_processes", return_value=[]):
            snapshot = self.service.snapshot()
        self.assertEqual(snapshot["creation_defaults"], {"parent_folder": str(self.registry.parent)})
        row = next(row for row in snapshot["environments"] if row["id"] == env.id)
        self.assertFalse(row["running"])
        self.assertEqual(row["login_username"], "test-account")
        self.assertEqual(row["home_url"], CUSTOM_HOME)
        self.assertEqual(row["configured_urls"]["home"], "https://portal.example.test/workspace")
        self.service.shutdown()
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            self.service = BrowserService(self.registry)
        self.assertIn(env.id, self.service.environments)
        self.assertEqual(self.service.environments[env.id].root, env.root)
        self.assertEqual(self.service.environments[env.id].login_username, "test-account")
        self.assertEqual(self.service.environments[env.id].home_url, CUSTOM_HOME)
        self.assertEqual(self.service.environments[env.id].auth_adapter, "generic")
        self.assertEqual(self.registry.read_bytes(), original)

    def test_create_shop_uses_immutable_environment_replacement_during_inventory(self):
        parent = self.root / "custom"
        parent.mkdir()
        previous_mapping = self.service.environments
        created = []
        def inventory():
            created.append(self.service.create_shop({"name": "并发创建测试店", "parent_folder": str(parent),
                                                     "login_username": "", "home_url": CUSTOM_HOME}))
            return []
        with patch.object(service_module, "enumerate_browser_processes", side_effect=inventory):
            snapshot = self.service.snapshot()
        self.assertIsNot(previous_mapping, self.service.environments)
        self.assertNotIn(created[0]["id"], previous_mapping)
        row = next(row for row in snapshot["environments"] if row["id"] == created[0]["id"])
        self.assertEqual(row["cdp"], "stopped")
        self.assertEqual(snapshot["summary"]["total"], 4)

    def test_create_shop_factory_errors_preserve_typed_code(self):
        error = service_module.shop_registry.ShopRegistryError("invalid_shop", "环境设置无效")
        with patch.object(service_module.shop_registry, "create_shop", side_effect=error):
            with self.assertRaises(BrowserServiceError) as caught:
                self.service.create_shop({})
        self.assertEqual(caught.exception.code, "invalid_shop")
        self.assertEqual(caught.exception.message, "环境设置无效")
        self.assertEqual(len(self.service.environments), 3)

    def test_login_page_never_executes_identity_requests(self):
        page = Mock(url="https://loginmyseller.taobao.com/", evaluate=AsyncMock())
        result = asyncio.run(self.service._platform_auth(self.service.environments["shop01"], page))
        self.assertEqual(result["status"], "required")
        page.evaluate.assert_not_awaited()

    def test_shop_display_name_does_not_have_to_match_registry(self):
        page = Mock(url=URLS["home"], evaluate=AsyncMock(return_value={"verified": True, "store": "其他测试店铺"}))
        result = asyncio.run(self.service._platform_auth(self.service.environments["shop01"], page))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["identity"], "其他测试店铺")

    def test_shop_login_does_not_require_display_name_but_still_requires_evidence(self):
        page = Mock(url=URLS["home"], evaluate=AsyncMock(return_value={"verified": True}))
        result = asyncio.run(self.service._platform_auth(self.service.environments["shop01"], page))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["identity"], "")
        page.evaluate.return_value = {"store": "示例店铺一"}
        result = asyncio.run(self.service._platform_auth(self.service.environments["shop01"], page))
        self.assertEqual(result["status"], "error")
        page.evaluate.side_effect = RuntimeError("network failed")
        result = asyncio.run(self.service._platform_auth(self.service.environments["shop01"], page))
        self.assertEqual(result["status"], "error")

    def test_jst_cached_dom_and_resource_ids_are_not_sufficient(self):
        frame = Mock(url=service_module.GOODS_FRAME_URL, evaluate=AsyncMock(return_value={"coid": "test", "uid": "test"}))
        page = Mock(url=URLS["goods"], frames=[frame], evaluate=AsyncMock(return_value="示例有限公司[测试员]"))
        result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
        self.assertEqual(result["status"], "error")
        frame.evaluate.return_value = {"coid": "test", "uid": "test", "verified": True}
        result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["identity"], "示例有限公司")

    def test_jst_waits_for_business_frame_then_queries_session_once(self):
        frame = Mock(url=service_module.GOODS_FRAME_URL,
                     evaluate=AsyncMock(return_value={"coid": "test", "uid": "test", "verified": True}))
        page = Mock(url=URLS["goods"], frames=[], evaluate=AsyncMock(return_value=""))
        async def frame_ready(_duration):
            page.frames = [frame]
        with patch.object(service_module.asyncio, "sleep", side_effect=frame_ready) as pause:
            result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
        self.assertEqual(result["status"], "verified")
        page.evaluate.assert_awaited_once()
        frame.evaluate.assert_awaited_once()
        pause.assert_awaited_once()

    def test_jst_dom_wait_detects_login_redirect_without_tenant_query(self):
        frame = Mock(url=service_module.GOODS_FRAME_URL, evaluate=AsyncMock())
        page = Mock(url=URLS["goods"], frames=[], evaluate=AsyncMock())
        async def redirect(_duration):
            page.url = "https://www.erp321.com/login.aspx"
        with patch.object(service_module.asyncio, "sleep", side_effect=redirect):
            result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
        self.assertEqual(result["status"], "required")
        frame.evaluate.assert_not_awaited()
        page.evaluate.assert_not_awaited()

    def test_jst_login_does_not_require_issuer_or_matching_company_label(self):
        frame = Mock(url=service_module.GOODS_FRAME_URL,
                     evaluate=AsyncMock(return_value={"verified": True, "coid": "test", "uid": "test"}))
        page = Mock(url=URLS["goods"], frames=[frame], evaluate=AsyncMock(return_value="另一家测试公司[用户]"))
        for issuer in ("示例有限公司", ""):
            self.service.issuer = issuer
            result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["identity"], "另一家测试公司")

    def test_jst_optional_label_failure_does_not_hide_valid_login(self):
        frame = Mock(url=service_module.GOODS_FRAME_URL, evaluate=AsyncMock(return_value={"verified": True}))
        page = Mock(url=URLS["goods"], frames=[frame], evaluate=AsyncMock(side_effect=RuntimeError("label absent")))
        result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["identity"], "")
        frame.evaluate.return_value = {"required": True}
        result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
        self.assertEqual(result["status"], "required")
        frame.evaluate.side_effect = RuntimeError("network failed")
        result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
        self.assertEqual(result["status"], "error")

    def test_jst_loading_timeout_is_actionable_without_claiming_login_success(self):
        page = Mock(url=URLS["goods"], frames=[], evaluate=AsyncMock(return_value=""))
        with patch.object(service_module, "JST_PAGE_READY_SECONDS", 0):
            result = asyncio.run(self.service._platform_auth(self.service.environments["piaoju"], page))
        self.assertEqual(result["status"], "error")
        self.assertIn("重新检查", result["message"])

    def test_close_rechecks_process_ownership_before_protocol_close(self):
        env = self.service.environments["shop01"]
        session = Mock(send=AsyncMock())
        browser = Mock(new_browser_cdp_session=AsyncMock(return_value=session), close=AsyncMock())
        _, playwright = self.playwright(browser)
        with playwright, patch.object(self.service, "_require_owner", side_effect=[
            self.process(), BrowserServiceError("process changed", "ownership_conflict")]):
            with self.assertRaises(BrowserServiceError) as error:
                asyncio.run(self.service._browser_action("close", env))
        self.assertEqual(error.exception.code, "ownership_conflict")
        session.send.assert_not_awaited()
        self.assertFalse(service_module.profile_busy(env.root))

    def test_close_uses_protocol_then_waits_for_exit_without_killing_process(self):
        events = []
        async def send(command):
            events.append(command)
        async def exited(fingerprint):
            events.append("process-exited")
        session = Mock(send=AsyncMock(side_effect=send))
        browser = Mock(new_browser_cdp_session=AsyncMock(return_value=session), close=AsyncMock())
        _, playwright = self.playwright(browser)
        with playwright, patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(self.service, "_wait_for_exit", side_effect=exited), \
                patch.object(self.service, "_save_session", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved"}) as save, \
                patch.object(service_module.subprocess, "run") as command:
            result = asyncio.run(self.service._browser_action("close", self.service.environments["shop01"]))
        self.assertEqual(events, ["Browser.close", "process-exited"])
        self.assertIn("已正常关闭", result["message"])
        command.assert_not_called()
        save.assert_awaited_once()

    def test_partial_cookie_save_keeps_browser_open_and_auth_intact(self):
        browser = Mock(new_browser_cdp_session=AsyncMock(), close=AsyncMock())
        _, playwright = self.playwright(browser)
        self.service._set_auth("shop01", "verified", "已核验", "示例店铺一")
        with playwright, patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(self.service, "_save_session", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "partial"}):
            with self.assertRaises(BrowserServiceError) as error:
                asyncio.run(self.service._browser_action("close", self.service.environments["shop01"]))
        self.assertEqual(error.exception.code, "cookie_sync_failed")
        browser.new_browser_cdp_session.assert_not_awaited()
        self.assertEqual(self.service._auth["shop01"]["status"], "verified")

    def test_metadata_write_failure_prevents_close(self):
        browser = Mock(new_browser_cdp_session=AsyncMock(), close=AsyncMock())
        _, playwright = self.playwright(browser)
        with playwright, patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "persist_session_cookies", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved", "persisted_count": 1}), \
                patch.object(service_module, "write_cookie_metadata", side_effect=PermissionError("secret-path")):
            with self.assertRaises(BrowserServiceError) as error:
                asyncio.run(self.service._browser_action("close", self.service.environments["shop01"]))
        self.assertEqual(error.exception.code, "cookie_sync_failed")
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(self.service._cookie_sync["shop01"]["status"], "error")
        browser.new_browser_cdp_session.assert_not_awaited()

    def test_check_login_only_saves_assumed_or_verified_success(self):
        env = self.service.environments["shop01"]
        page = Mock(url=env.home_url)
        controller = self.probe_controller(page)
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller), \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock) as probe, \
                patch.object(self.service, "_save_session", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved"}) as save:
            for status in ("assumed", "required", "error"):
                probe.return_value = {"status": status, "message": status}
                result = asyncio.run(self.service._browser_action("check-login", env))
                self.assertEqual(result["auth"]["status"], status)
                self.assertEqual(save.await_count, 1 if status == "assumed" else 0)
                save.reset_mock()
        self.assertEqual(controller.connect.await_count, 3)
        controller.start.assert_not_awaited()

    def test_platform_mode_runs_legacy_probe_only_after_successful_navigation(self):
        env = self.service.environments["shop01"]
        self.service.update_login_check(env.id, {"mode": "platform", "login_url": "", "wait_seconds": 1})
        page = Mock(url=env.home_url)
        with patch.object(service_module, "probe_login_url", new_callable=AsyncMock) as probe, \
                patch.object(self.service, "_platform_auth", new_callable=AsyncMock,
                             return_value={"status": "verified", "message": "已核验"}) as platform:
            for status in ("error", "required", "assumed"):
                probe.return_value = {"status": status, "message": status}
                auth = asyncio.run(self.service._check_auth(env, page))
                self.assertEqual(auth["status"], "verified" if status == "assumed" else status)
            platform.assert_awaited_once_with(env, page)

    def test_manual_save_does_not_need_business_pages_or_run_login_checks(self):
        browser = Mock(contexts=[], close=AsyncMock())
        _, playwright = self.playwright(browser)
        with playwright, patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "persist_session_cookies", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved", "persisted_count": 2}) as save, \
                patch.object(self.service, "_check_auth", new_callable=AsyncMock) as auth:
            result = asyncio.run(self.service._browser_action("save-session", self.service.environments["shop01"]))
        self.assertEqual(result["cookie_sync"]["persisted_count"], 2)
        self.assertTrue((self.service.environments["shop01"].root / METADATA_NAME).exists())
        self.assertEqual(save.call_args.args[1], {"taobao.com", "tmall.com"})
        auth.assert_not_awaited()
        browser.new_page.assert_not_called()

    def test_idle_checkpoint_skips_busy_profiles_and_prioritizes_explicit_queue(self):
        self.service._next_cookie_sync = {key: float("inf") for key in self.service.environments}
        self.service._next_cookie_sync["shop01"] = 0
        self.service._observations["shop01"] = {"cdp": "connected"}
        with patch.object(self.service, "_refresh"), \
                patch.object(service_module, "profile_busy", return_value=True), \
                patch.object(self.service, "_browser_action", new_callable=AsyncMock) as action:
            self.service._idle_checkpoint()
            action.assert_not_awaited()
        self.service._next_cookie_sync["shop01"] = 0
        with patch.object(self.service, "_refresh"), \
                patch.object(self.service._queue, "empty", return_value=False), \
                patch.object(self.service, "_browser_action", new_callable=AsyncMock) as action:
            self.service._idle_checkpoint()
            action.assert_not_awaited()

    def test_idle_checkpoint_uses_existing_session_without_checking_auth(self):
        self.service._next_cookie_sync = {key: float("inf") for key in self.service.environments}
        self.service._next_cookie_sync["shop01"] = 0
        self.service._observations["shop01"] = {"cdp": "connected"}
        with patch.object(self.service, "_refresh"), \
                patch.object(service_module, "profile_busy", return_value=False), \
                patch.object(self.service, "_browser_action", new_callable=AsyncMock) as action:
            self.service._idle_checkpoint()
            action.assert_awaited_once_with("save-session", self.service.environments["shop01"], automatic=True,
                                           checkpoint_revision=0)
            self.service._idle_checkpoint()
            action.assert_awaited_once()

    def test_autosave_settings_reschedule_disable_and_reload_without_browser_actions(self):
        with patch.object(self.service, "_refresh"), \
                patch.object(service_module, "profile_busy", return_value=False), \
                patch.object(self.service, "_browser_action", new_callable=AsyncMock) as action:
            self.assertEqual(self.service.snapshot()["session_autosave"], {"enabled": True, "interval_minutes": 5})
            with patch.object(service_module.time, "monotonic", return_value=100):
                self.service.update_session_autosave({"enabled": True, "interval_minutes": 10})
            self.assertEqual(set(self.service._next_cookie_sync.values()), {700})
            self.service.update_session_autosave({"enabled": False, "interval_minutes": 30})
            self.assertEqual(self.service._next_cookie_sync, {})
            self.service._idle_checkpoint()
            action.assert_not_awaited()
            self.assertEqual(self.service._jobs, [])
        self.service.shutdown()
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            self.service = BrowserService(self.registry)
        self.assertEqual(self.service._session_autosave, {"enabled": False, "interval_minutes": 30})
        self.assertEqual(self.service._next_cookie_sync, {})
        with patch.object(service_module.time, "monotonic", return_value=200):
            self.service.update_session_autosave({"enabled": True, "interval_minutes": 1})
        self.assertEqual(set(self.service._next_cookie_sync.values()), {260})

    def test_autosave_storage_failure_preserves_memory_deadlines_and_file(self):
        self.service.update_session_autosave({"enabled": True, "interval_minutes": 10})
        deadlines = dict(self.service._next_cookie_sync)
        content = self.service._session_autosave_path.read_bytes()
        revision = self.service._session_autosave_revision
        with patch.object(service_module.session_settings, "save", side_effect=PermissionError("private path")):
            with self.assertRaises(BrowserServiceError) as caught:
                self.service.update_session_autosave({"enabled": False, "interval_minutes": 30})
        self.assertEqual(caught.exception.code, "session_autosave_storage_failed")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.service._session_autosave, {"enabled": True, "interval_minutes": 10})
        self.assertEqual(self.service._next_cookie_sync, deadlines)
        self.assertEqual(self.service._session_autosave_revision, revision)
        self.assertEqual(self.service._session_autosave_path.read_bytes(), content)

    def test_settings_change_during_inventory_invalidates_selected_checkpoint(self):
        for enabled in (True, False):
            self.service.update_session_autosave({"enabled": True, "interval_minutes": 1})
            self.service._next_cookie_sync = {"shop01": 0}
            self.service._observations["shop01"] = {"cdp": "connected"}
            with patch.object(self.service, "_refresh", side_effect=lambda: self.service.update_session_autosave(
                    {"enabled": enabled, "interval_minutes": 60})), \
                    patch.object(service_module, "ProfileLock") as lock, \
                    patch.object(service_module, "profile_busy") as busy, \
                    patch.object(self.service, "_browser_action", new_callable=AsyncMock) as action:
                self.service._idle_checkpoint()
                lock.assert_not_called()
                busy.assert_not_called()
                action.assert_not_awaited()

    def test_interval_change_after_admission_does_not_start_stale_checkpoint(self):
        self.service._next_cookie_sync = {"shop01": 0}
        self.service._observations["shop01"] = {"cdp": "connected"}
        original = self.service._browser_action
        async def change_interval(*args, **kwargs):
            self.service.update_session_autosave({"enabled": True, "interval_minutes": 60})
            return await original(*args, **kwargs)
        with patch.object(self.service, "_refresh"), \
                patch.object(service_module, "profile_busy", return_value=False), \
                patch.object(service_module, "ProfileLock") as lock, \
                patch.object(self.service, "_browser_action", side_effect=change_interval):
            self.service._idle_checkpoint()
        lock.assert_not_called()

    def test_disable_waits_for_admitted_checkpoint_to_detach_without_cookie_write(self):
        self.service._next_cookie_sync = {"shop01": 0}
        self.service._observations["shop01"] = {"cdp": "connected"}
        connected, release, persisted, updated = (threading.Event() for _ in range(4))
        browser = Mock(close=AsyncMock())
        runtime, playwright = self.playwright(browser)
        async def connect(*_args, **_kwargs):
            connected.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return browser
        runtime.chromium.connect_over_cdp.side_effect = connect
        original_save = service_module.session_settings.save
        def save(path, settings):
            original_save(path, settings)
            persisted.set()
        def disable():
            self.service.update_session_autosave({"enabled": False, "interval_minutes": 5})
            updated.set()
        worker = threading.Thread(target=self.service._idle_checkpoint)
        writer = threading.Thread(target=disable)
        with playwright, patch.object(self.service, "_refresh"), \
                patch.object(service_module, "profile_busy", return_value=False), \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module.session_settings, "save", side_effect=save), \
                patch.object(service_module, "persist_session_cookies", new_callable=AsyncMock) as cookies:
            try:
                worker.start()
                self.assertTrue(connected.wait(2))
                writer.start()
                self.assertTrue(persisted.wait(2))
                with self.service._state_lock:
                    self.assertFalse(self.service._session_autosave["enabled"])
                self.assertFalse(updated.is_set())
            finally:
                release.set()
                worker.join(3)
                if writer.ident is not None:
                    writer.join(3)
            self.assertTrue(updated.is_set())
            self.assertFalse(worker.is_alive())
            self.assertFalse(writer.is_alive())
            cookies.assert_not_awaited()
            browser.close.assert_awaited_once()
            runtime.stop.assert_awaited_once()
        self.assertIsNone(self.service._checkpoint_env)
        self.assertEqual(self.service._next_cookie_sync, {})
        self.assertFalse(service_module.profile_busy(self.service.environments["shop01"].root))

    def test_disabled_autosave_keeps_manual_save_and_close_available(self):
        self.service.update_session_autosave({"enabled": False, "interval_minutes": 5})
        browser = Mock(contexts=[], close=AsyncMock(), new_browser_cdp_session=AsyncMock(
            return_value=Mock(send=AsyncMock())))
        _, playwright = self.playwright(browser)
        with playwright, patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(self.service, "_wait_for_exit", new_callable=AsyncMock), \
                patch.object(service_module, "persist_session_cookies", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved", "persisted_count": 2}) as save:
            for action in ("save-session", "close"):
                result = asyncio.run(self.service._browser_action(action, self.service.environments["shop01"]))
                self.assertEqual(result["cookie_sync"]["status"], "saved")
            self.assertEqual(save.await_count, 2)
            self.assertIsNone(save.call_args.kwargs["should_continue"])
        self.assertEqual(self.service._next_cookie_sync, {})

    def test_auto_cookie_writes_yield_after_setting_change_without_replacing_new_deadline(self):
        env = self.service.environments["shop01"]
        session = Mock(detach=AsyncMock())
        async def send(method, *_args):
            self.assertEqual(method, "Storage.getCookies")
            with patch.object(service_module.time, "monotonic", return_value=100):
                self.service.update_session_autosave({"enabled": True, "interval_minutes": 60})
            return {"cookies": [{"name": "example", "value": "test", "domain": ".taobao.com",
                                  "path": "/", "session": True}]}
        session.send = AsyncMock(side_effect=send)
        browser = Mock(new_browser_cdp_session=AsyncMock(return_value=session))
        value = asyncio.run(self.service._save_session(env, browser, automatic=True))
        session.send.assert_awaited_once_with("Storage.getCookies")
        self.assertEqual(value["persisted_count"], 0)
        self.assertEqual(self.service._next_cookie_sync[env.id], 3700)

    def test_closed_environment_is_skipped_without_profile_lock_or_browser_connection(self):
        self.service._next_cookie_sync = {"shop01": 0}
        self.service._observations["shop01"] = {"cdp": "stopped"}
        with patch.object(self.service, "_refresh"), \
                patch.object(service_module, "profile_busy", return_value=False), \
                patch.object(service_module, "ProfileLock") as lock, \
                patch.object(self.service, "_browser_action", new_callable=AsyncMock) as action:
            self.service._idle_checkpoint()
        lock.assert_not_called()
        action.assert_not_awaited()

    def test_idle_exception_does_not_stop_worker_or_block_explicit_actions(self):
        failed = threading.Event()
        finished = threading.Event()
        def fail_idle():
            failed.set()
            raise RuntimeError("secret-internal-details")
        def perform(_action, _env):
            finished.set()
            return {"message": "完成"}
        with patch.object(self.service, "_idle_checkpoint", side_effect=fail_idle), \
                patch.object(self.service, "_perform", side_effect=perform):
            self.assertTrue(failed.wait(2))
            self.service.submit("focus", ["shop01"])
            self.assertTrue(finished.wait(2))
            self.service._queue.join()
        self.assertTrue(self.service._worker.is_alive())
        self.assertEqual(self.service._jobs[-1]["status"], "complete")
        self.assertNotIn("secret", json.dumps(self.service._activity))

    def test_refresh_inventory_in_flight_does_not_erase_new_process_auth(self):
        previous = self.process()
        current = self.process(started_at="2026-09-26T00:00:00Z")
        def inventory():
            self.service._adopt_process("shop01", self.service._process_key(current))
            self.service._set_auth("shop01", "verified", "已核验", "示例店铺一")
            return [previous]
        with patch.object(service_module, "enumerate_browser_processes", side_effect=inventory), \
                patch.object(service_module, "probe_cdp", return_value={"available": True, "tabs_count": 1}):
            self.service._refresh(force=True)
        self.assertEqual(self.service._auth["shop01"]["status"], "verified")
        self.assertEqual(self.service._process_keys["shop01"], self.service._process_key(current))

    def test_wait_for_close_requires_actual_target_exit(self):
        record = self.process()
        with patch.object(service_module, "enumerate_browser_processes", side_effect=[[record], []]) as inventory, \
                patch.object(service_module.asyncio, "sleep", new_callable=AsyncMock) as pause:
            asyncio.run(self.service._wait_for_exit(self.service._process_key(record)))
        self.assertEqual(inventory.call_count, 2)
        pause.assert_awaited_once()

    def test_focus_missing_business_page_still_focuses_verified_native_window(self):
        browser = Mock(contexts=[SimpleNamespace(pages=[])], close=AsyncMock())
        _, playwright = self.playwright(browser)
        with playwright, patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "focus_window") as focus:
            asyncio.run(self.service._browser_action("focus", self.service.environments["shop01"]))
        focus.assert_called_once_with(321, "")
        browser.new_page.assert_not_called()

    def test_busy_external_profile_is_not_closed(self):
        env = self.service.environments["shop01"]
        lock = ProfileLock(env.root, "Default")
        lock.acquire()
        try:
            with patch.object(self.service, "_require_owner") as ownership:
                with self.assertRaises(BrowserControllerError) as error:
                    asyncio.run(self.service._browser_action("close", env))
            self.assertEqual(error.exception.code, "profile_locked")
            ownership.assert_not_called()
        finally:
            lock.release()

    def test_display_url_removes_credentials_and_query(self):
        self.assertEqual(service_module.display_url("https://user:password@example.test/login?token=secret#secret"),
                         "https://example.test/login")

    def test_cdp_probe_explicitly_disables_proxy(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = json.dumps([{"type": "page", "url": "https://example.test/?token=secret", "title": "Page"}]).encode()
        opener = Mock(open=Mock(return_value=response))
        with patch.object(service_module, "build_opener", return_value=opener) as build:
            result = service_module.probe_cdp(19000)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertEqual(result["tabs"][0]["url"], "https://example.test/")


if __name__ == "__main__":
    unittest.main()
