import asyncio
import json
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend import browser_service as service_module, login_settings
from backend.browser_service import BrowserService, BrowserServiceError
from backend.session_cookies import empty_status
from backend.vendor.browser_controller import ProfileLock
import test_browser_service as fixtures


class LoginCheckServiceTests(unittest.TestCase):
    setUp = fixtures.BrowserServiceTests.setUp
    tearDown = fixtures.BrowserServiceTests.tearDown
    process = fixtures.BrowserServiceTests.process
    generic_environment = fixtures.BrowserServiceTests.generic_environment
    probe_controller = staticmethod(fixtures.BrowserServiceTests.probe_controller)

    def settings(self, **changes):
        return {**login_settings.DEFAULTS, **changes}

    def test_snapshot_defaults_all_sites_to_url_and_separates_assumed_from_verified(self):
        with patch.object(service_module, "enumerate_browser_processes", return_value=[]):
            snapshot = self.service.snapshot()
        self.assertTrue(all(env["login_check"] == login_settings.DEFAULTS for env in snapshot["environments"]))
        by_id = {env["id"]: env for env in snapshot["environments"]}
        self.assertEqual(by_id["shop01"]["login_check_platform"], "qianniu")
        self.assertEqual(by_id["piaoju"]["login_check_platform"], "jst")
        self.service._set_auth("shop01", "assumed", "推定")
        self.service._set_auth("piaoju", "verified", "核验")
        with patch.object(self.service, "_refresh"):
            snapshot = self.service.snapshot()
        self.assertEqual(snapshot["summary"]["assumed"], 1)
        self.assertEqual(snapshot["summary"]["verified"], 1)

    def test_rule_is_persisted_resets_auth_and_block_and_survives_restart(self):
        env = self.service.environments["shop01"]
        original = env.config_path.read_bytes()
        self.service._set_auth(env.id, "required", "旧判断")
        settings = self.settings(login_url="https://accounts.example.test/signin", wait_seconds=3)
        result = self.service.update_login_check(env.id, settings)
        self.assertEqual(result["login_check"], settings)
        self.assertEqual(result["auth"]["status"], "unchecked")
        self.assertIsNone(result["auth"]["checked_at"])
        self.assertNotIn(env.id, self.service._maintenance_required)
        self.assertEqual(env.config_path.read_bytes(), original)
        self.service.shutdown()
        with patch.object(service_module, "_resolve_browser_executable", return_value=self.root / "msedge.exe"):
            self.service = BrowserService(self.registry)
        self.assertEqual(self.service.login_check_settings(env.id), settings)
        self.assertNotIn(env.id, self.service._maintenance_required)

    def test_rule_write_failure_preserves_previous_auth_and_block(self):
        self.service._set_auth("shop01", "required", "原判断")
        with patch.object(login_settings, "save", side_effect=login_settings.LoginSettingsError("login_settings_storage_failed", "无法保存")):
            with self.assertRaises(login_settings.LoginSettingsError):
                self.service.update_login_check("shop01", self.settings(wait_seconds=8))
        self.assertEqual(self.service.login_check_settings("shop01"), login_settings.DEFAULTS)
        self.assertEqual(self.service._auth["shop01"]["status"], "required")
        self.assertIn("shop01", self.service._maintenance_required)

    def test_rule_updates_reject_unknown_closed_unsupported_and_busy_environments(self):
        with self.assertRaises(BrowserServiceError) as unknown:
            self.service.update_login_check("unknown", self.settings())
        self.assertEqual(unknown.exception.code, "invalid_environment")
        env = self.generic_environment()
        with self.assertRaises(BrowserServiceError) as unsupported:
            self.service.update_login_check(env.id, self.settings(mode="platform"))
        self.assertEqual(unsupported.exception.code, "unsupported_login_platform")
        for field, value in (("_maintenance_job_id", "round"), ("_checkpoint_env", env.id)):
            with patch.object(self.service, field, value):
                with self.assertRaises(BrowserServiceError) as busy:
                    self.service.update_login_check(env.id, self.settings())
                self.assertEqual(busy.exception.code, "environment_busy")
        lock = ProfileLock(env.root, "Default")
        lock.acquire()
        try:
            with self.assertRaises(BrowserServiceError) as busy:
                self.service.update_login_check(env.id, self.settings())
            self.assertEqual(busy.exception.code, "environment_busy")
        finally:
            lock.release()
        self.service.shutdown()
        with self.assertRaises(BrowserServiceError) as closed:
            self.service.update_login_check(env.id, self.settings())
        self.assertEqual(closed.exception.code, "service_closed")

    def test_queued_and_active_actions_prevent_rule_changes_until_they_finish(self):
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
                    with self.assertRaises(BrowserServiceError) as busy:
                        self.service.update_login_check(env_id, self.settings())
                    self.assertEqual(busy.exception.code, "environment_busy")
            finally:
                release.set()
            self.service._queue.join()
        self.service.update_login_check("shop01", self.settings(wait_seconds=2))

    def test_url_error_is_failed_job_and_does_not_preserve_old_assumed_state_or_save(self):
        env = self.service.environments["shop01"]
        controller = self.probe_controller(Mock(url=env.home_url))
        self.service._set_auth(env.id, "assumed", "上次成功")
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller), \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                             return_value={"status": "error", "message": "HTTP 503"}), \
                patch.object(self.service, "_save_session", new_callable=AsyncMock) as save:
            queued = self.service.submit("check-login", [env.id])
            self.service._queue.join()
        job = next(job for job in self.service._jobs if job["id"] == queued["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["results"][0]["code"], "login_check_failed")
        self.assertEqual(self.service._auth[env.id]["status"], "error")
        save.assert_not_awaited()

    def test_success_clears_manual_block_but_cookie_failure_is_still_failed(self):
        env = self.service.environments["shop01"]
        controller = self.probe_controller(Mock(url=env.home_url))
        self.service._set_auth(env.id, "required", "旧登录页")
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller), \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock,
                             return_value={"status": "assumed", "message": "推定"}), \
                patch.object(self.service, "_save_session", new_callable=AsyncMock,
                             return_value={**empty_status(), "status": "saved"}) as save:
            asyncio.run(self.service._browser_action("check-login", env))
            self.assertNotIn(env.id, self.service._maintenance_required)
            save.return_value = {**empty_status(), "status": "partial"}
            with self.assertRaises(BrowserServiceError) as error:
                asyncio.run(self.service._browser_action("check-login", env))
        self.assertEqual(error.exception.code, "cookie_sync_failed")
        self.assertEqual(self.service._auth[env.id]["status"], "assumed")

    def test_cancellation_clears_previous_assumed_result_and_disconnects(self):
        env = self.service.environments["shop01"]
        controller = self.probe_controller(Mock(url=env.home_url))
        self.service._set_auth(env.id, "assumed", "上次成功")
        with patch.object(service_module, "PlaywrightBrowserController", return_value=controller), \
                patch.object(self.service, "_require_owner", return_value=self.process()), \
                patch.object(service_module, "probe_login_url", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(self.service._browser_action("start", env))
        self.assertEqual(self.service._auth[env.id]["status"], "error")
        controller.close.assert_awaited_once()
