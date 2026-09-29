import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch

from backend.server import Handler, WorkbenchServer
from backend.browser_service import BrowserServiceError


class FakeService:
    def __init__(self):
        self.actions = []
        self.maintenance_updates = []
        self.maintenance_runs = 0
        self.created_shops = []
        self.closed_shop_groups = []
        self.shutdown_calls = []
        self.drain_started = threading.Event()
        self.allow_shutdown = threading.Event()

    def snapshot(self):
        return {"environments": [], "jobs": [], "activity": []}

    def submit(self, action, ids):
        self.actions.append((action, ids))
        return {"id": "test-job", "status": "queued"}

    def update_maintenance(self, settings):
        self.maintenance_updates.append(settings)
        return settings

    def run_maintenance(self):
        self.maintenance_runs += 1
        return {"id": "maintenance-job", "status": "queued"}

    def create_shop(self, payload):
        self.created_shops.append(payload)
        return {"id": "shop-example", "name": payload["name"]}

    def update_login_check(self, env_id, settings):
        self.login_check_update = (env_id, settings)
        return {"login_check": settings, "auth": {"status": "unchecked"}}

    def change_environment_lifecycle(self, env_id, operation, confirm_name=None):
        self.lifecycle_change = (env_id, operation, confirm_name)
        return {"environment_id": env_id, "state": {"archive": "archived", "restore": "active", "delete": "deleted"}[operation]}

    def close_all_shops(self, payload):
        self.closed_shop_groups.append(payload)
        return {"id": "close-all-job", "status": "queued"}

    def shutdown(self, *, wait=True):
        self.shutdown_calls.append(wait)
        if wait:
            self.drain_started.set()
            self.allow_shutdown.wait()


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.static = Path(self.tmp.name) / "dist"
        self.static.mkdir()
        (self.static / "index.html").write_text("<h1>Workbench</h1>", encoding="utf-8")
        (Path(self.tmp.name) / "private.json").write_text("private", encoding="utf-8")
        self.service = FakeService()
        self.service.registry_path = Path(self.tmp.name) / "shops.json"
        self.server = WorkbenchServer(("127.0.0.1", 0), self.service, self.static)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.service.allow_shutdown.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        if self.server._shutdown_thread is not None:
            self.server._shutdown_thread.join(3)
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        client = HTTPConnection("127.0.0.1", self.port, timeout=3)
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        client.close()
        return result

    def post(self, **extra):
        headers = {"Content-Type": "application/json", "X-CSRF-Token": self.server.csrf_token}
        headers.update(extra)
        return self.request("POST", "/api/actions", json.dumps({"action": "focus", "environment_ids": ["shop01"]}), headers)

    def test_state_provides_session_token_and_no_cors(self):
        status, body, headers = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["csrf_token"], self.server.csrf_token)
        self.assertEqual(json.loads(body)["service_status"], "running")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_cancelled_client_during_headers_or_body_needs_no_second_response(self):
        for stage in ("headers", "body"):
            with self.subTest(stage=stage):
                handler = Mock(command="GET")
                target = handler.end_headers if stage == "headers" else handler.wfile.write
                target.side_effect = ConnectionAbortedError("client cancelled")
                Handler._send(handler, 200, b"response")
                handler.send_response.assert_called_once_with(200)

    def test_valid_mutation_is_queued(self):
        self.assertEqual(self.post(Origin=f"http://127.0.0.1:{self.port}")[0], 202)
        self.assertEqual(self.service.actions, [("focus", ["shop01"])])

    def test_cross_origin_and_rebinding_rejected(self):
        self.assertEqual(self.post(Origin="https://example.com")[0], 403)
        self.assertEqual(self.post(Host=f"example.com:{self.port}")[0], 403)
        self.assertEqual(self.post(**{"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.service.actions, [])

    def test_token_required(self):
        self.assertEqual(self.post(**{"X-CSRF-Token": ""})[0], 403)
        self.assertEqual(self.service.actions, [])

    def test_path_traversal_rejected(self):
        for path in ["/%2e%2e/private.json", "/..%5cprivate.json", "/D:%5cprivate.json"]:
            status, body, _ = self.request("GET", path)
            self.assertIn(status, [403, 404])
            self.assertNotEqual(body, b"private")

    def test_invalid_payload_does_not_reach_service(self):
        for payload in [{"action": "start", "environment_ids": "shop01"}, {"action": "start", "environment_ids": []}, {"action": "start", "environment_ids": [1]}, {"command": "powershell"}]:
            result = self.request("POST", "/api/actions", json.dumps(payload), {"Content-Type": "application/json", "X-CSRF-Token": self.server.csrf_token})
            self.assertEqual(result[0], 400)
        self.assertEqual(self.service.actions, [])

    def maintenance_post(self, path, payload, **extra_headers):
        headers = {"Content-Type": "application/json", "X-CSRF-Token": self.server.csrf_token}
        headers.update(extra_headers)
        return self.request("POST", path, json.dumps(payload), headers)

    def test_maintenance_settings_and_run_use_separate_routes(self):
        settings = {"enabled": True, "interval_minutes": 120}
        status, body, _ = self.maintenance_post("/api/maintenance", settings)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"maintenance": settings})
        status, body, _ = self.maintenance_post("/api/maintenance/run", {})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)["job"]["id"], "maintenance-job")
        self.assertEqual(self.service.maintenance_updates, [settings])
        self.assertEqual(self.service.maintenance_runs, 1)
        self.assertEqual(self.service.actions, [])

    def test_login_check_settings_require_token_origin_and_valid_fields(self):
        path = "/api/environments/login-check"
        settings = {"environment_id": "shop01", "mode": "url", "login_url": "https://example.test/login", "wait_seconds": 5}
        for headers in ({"Origin": "https://example.com"}, {"X-CSRF-Token": ""}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.maintenance_post(path, settings, **headers)[0], 403)
        for patch_value in ({"wait_seconds": 0}, {"wait_seconds": True}, {"mode": "unknown"},
                            {"login_url": "javascript:alert(1)"}, {"environment_id": []}, {"command": "run"}):
            self.assertEqual(self.maintenance_post(path, {**settings, **patch_value})[0], 400)
        self.assertFalse(hasattr(self.service, "login_check_update"))
        status, body, _ = self.maintenance_post(path, settings)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["environment_id"], "shop01")
        self.assertEqual(result["login_check"]["login_url"], settings["login_url"])
        self.assertEqual(result["auth"]["status"], "unchecked")
        self.assertEqual(self.service.actions, [])

    def test_login_check_storage_or_busy_failure_is_reported(self):
        payload = {"environment_id": "shop01", "mode": "url", "login_url": "", "wait_seconds": 5}
        for code, expected in (("environment_busy", 409), ("login_settings_storage_failed", 503)):
            with patch.object(self.service, "update_login_check", side_effect=BrowserServiceError("无法保存", code)):
                status, body, _ = self.maintenance_post("/api/environments/login-check", payload)
            self.assertEqual(status, expected)
            self.assertEqual(json.loads(body)["error"]["code"], code)

    def test_environment_lifecycle_routes_are_authenticated_and_validate_confirmation_shape(self):
        for operation in ("archive", "restore", "delete"):
            path = f"/api/environments/{operation}"
            payload = {"environment_id": "shop-example"}
            if operation == "delete":
                payload["confirm_name"] = "示例环境"
            for extra in ({"Origin": "https://example.com"}, {"X-CSRF-Token": ""}, {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(self.maintenance_post(path, payload, **extra)[0], 403)
            for bad in ({}, {**payload, "environment_id": []}, {**payload, "environment_id": ""},
                        {**payload, "delete_files": True}, {**payload, "path": "D:/"}):
                self.assertEqual(self.maintenance_post(path, bad)[0], 400)
            if operation == "delete":
                for name in (None, "", True):
                    self.assertEqual(self.maintenance_post(path, {**payload, "confirm_name": name})[0], 400)
            status, body, _ = self.maintenance_post(path, payload)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["state"], {"archive": "archived", "restore": "active", "delete": "deleted"}[operation])
            self.assertEqual(self.service.lifecycle_change, ("shop-example", operation, payload.get("confirm_name")))

    def test_lifecycle_write_failure_or_busy_reports_failure_without_success_response(self):
        for code, expected in (("environment_busy", 409), ("lifecycle_storage_failed", 503), ("confirmation_mismatch", 400)):
            with patch.object(self.service, "change_environment_lifecycle", side_effect=BrowserServiceError("未更改", code)):
                status, body, _ = self.maintenance_post("/api/environments/delete", {"environment_id": "shop01", "confirm_name": "示例"})
            self.assertEqual(status, expected)
            self.assertEqual(json.loads(body)["error"]["code"], code)

    def test_maintenance_cannot_bypass_origin_token_or_fixed_payload(self):
        settings = {"enabled": True, "interval_minutes": 120, "include_shared": True}
        for path, payload in (("/api/maintenance", settings), ("/api/maintenance/run", {})):
            self.assertEqual(self.maintenance_post(path, payload, Origin="https://example.com")[0], 403)
            self.assertEqual(self.maintenance_post(path, payload, **{"X-CSRF-Token": ""})[0], 403)
            self.assertEqual(self.maintenance_post(path, {**payload, "url": "https://example.com"})[0], 400)
            self.assertEqual(self.maintenance_post(path, [payload])[0], 400)
        self.assertEqual(self.service.maintenance_updates, [])
        self.assertEqual(self.service.maintenance_runs, 0)

    def test_maintenance_busy_and_storage_failure_have_actionable_codes(self):
        with patch.object(self.service, "run_maintenance", side_effect=BrowserServiceError("正在维护", "maintenance_busy")):
            status, body, _ = self.maintenance_post("/api/maintenance/run", {})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "maintenance_busy")
        with patch.object(self.service, "update_maintenance", side_effect=BrowserServiceError("设置无法保存", "maintenance_storage_failed")):
            status, body, _ = self.maintenance_post("/api/maintenance", {"enabled": True, "interval_minutes": 120, "include_shared": True})
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "maintenance_storage_failed")

    def test_create_shop_and_close_all_have_fixed_routes(self):
        payload = {"name": "新店测试", "parent_folder": str(Path(self.tmp.name)), "login_username": "", "home_url": "https://example.test/admin?team=a#home"}
        status, body, _ = self.maintenance_post("/api/shops", payload)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["environment"]["id"], "shop-example")
        self.assertEqual(self.service.created_shops, [payload])
        status, body, _ = self.maintenance_post("/api/shops/close-all", {"pause_maintenance": True})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)["job"]["id"], "close-all-job")
        self.assertEqual(self.service.closed_shop_groups, [{"pause_maintenance": True}])

    def test_environment_routes_and_legacy_aliases_target_the_same_operations(self):
        payload = {"name": "浏览器环境", "parent_folder": str(Path(self.tmp.name)), "login_username": "", "home_url": "http://intranet.local/dashboard"}
        for path in ("/api/environments", "/api/shops"):
            self.assertEqual(self.maintenance_post(path, payload)[0], 201)
        for path in ("/api/environments/close-all", "/api/shops/close-all"):
            self.assertEqual(self.maintenance_post(path, {"pause_maintenance": True})[0], 202)
        self.assertEqual(self.service.created_shops, [payload, payload])
        self.assertEqual(self.service.closed_shop_groups, [{"pause_maintenance": True}] * 2)

    def test_maintenance_accepts_new_settings_and_ignores_legacy_boolean_choice(self):
        settings = {"enabled": True, "interval_minutes": 120}
        for payload in (settings, {**settings, "include_shared": True}, {**settings, "include_shared": False}):
            status, body, _ = self.maintenance_post("/api/maintenance", payload)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["maintenance"], settings)
        self.assertEqual(self.service.maintenance_updates, [settings] * 3)
        self.assertEqual(self.maintenance_post("/api/maintenance", {**settings, "include_shared": 0})[0], 400)

    def test_creation_requires_valid_explicit_homepage_before_calling_service(self):
        payload = {"name": "Browser", "parent_folder": str(Path(self.tmp.name)), "login_username": ""}
        for path in ("/api/environments", "/api/shops"):
            for extra in ({}, {"home_url": ""}, {"home_url": "javascript:alert(1)"},
                          {"home_url": "https://user:secret@example.test/"}, {"home_url": "http://example.test:0/"}):
                with self.subTest(path=path, extra=extra):
                    status, body, _ = self.maintenance_post(path, {**payload, **extra})
                    self.assertEqual(status, 400)
                    self.assertEqual(json.loads(body)["error"]["code"], "invalid_home_url")
        self.assertEqual(self.service.created_shops, [])
        address = "http://127.0.0.1:18765/admin?shop=one#dashboard"
        self.assertEqual(self.maintenance_post("/api/environments", {**payload, "home_url": "  " + address + "  "})[0], 201)
        self.assertEqual(self.service.created_shops[0]["home_url"], address)

    def test_folder_picker_uses_local_registry_default_and_can_cancel(self):
        with patch("backend.server.choose_folder", return_value={"path": None, "cancelled": True}) as picker:
            status, body, _ = self.maintenance_post("/api/folders/pick", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"path": None, "cancelled": True})
        picker.assert_called_once_with(str(self.service.registry_path.parent))

    def test_shop_routes_and_picker_require_token_origin_and_known_fields(self):
        routes = [(path, {"name": "测试", "parent_folder": str(Path(self.tmp.name)), "login_username": "", "home_url": "https://example.test/"})
                  for path in ("/api/environments", "/api/shops")]
        routes += [(path, {"pause_maintenance": True}) for path in ("/api/environments/close-all", "/api/shops/close-all")]
        routes.append(("/api/folders/pick", {}))
        with patch("backend.server.choose_folder") as picker:
            for path, payload in routes:
                self.assertEqual(self.maintenance_post(path, payload, Origin="https://example.com")[0], 403)
                self.assertEqual(self.maintenance_post(path, payload, **{"X-CSRF-Token": ""})[0], 403)
                self.assertEqual(self.maintenance_post(path, {**payload, "command": "anything"})[0], 400)
            self.assertEqual(self.maintenance_post("/api/shops/close-all", {"pause_maintenance": "false"})[0], 400)
            picker.assert_not_called()
        self.assertEqual(self.service.created_shops, [])
        self.assertEqual(self.service.closed_shop_groups, [])

    def test_close_all_busy_does_not_return_a_second_job(self):
        with patch.object(self.service, "close_all_shops", side_effect=BrowserServiceError("全部关闭已在队列中", "close_all_busy")):
            status, body, _ = self.maintenance_post("/api/shops/close-all", {"pause_maintenance": True})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "close_all_busy")

    def test_shutdown_requires_local_origin_host_token_and_empty_json_object(self):
        for headers in ({"Origin": "https://example.com"}, {"Host": f"evil.test:{self.port}"},
                        {"Sec-Fetch-Site": "cross-site"}, {"X-CSRF-Token": ""}):
            self.assertEqual(self.maintenance_post("/api/shutdown", {}, **headers)[0], 403)
        for payload in ({"close_browsers": True}, [], None, "shutdown"):
            self.assertEqual(self.maintenance_post("/api/shutdown", payload)[0], 400)
        self.assertEqual(self.request("POST", "/api/shutdown", "{}",
                         {"X-CSRF-Token": self.server.csrf_token, "Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/shutdown", "",
                         {"X-CSRF-Token": self.server.csrf_token, "Content-Type": "application/json"})[0], 413)
        self.assertEqual(self.service.shutdown_calls, [])
        self.assertEqual(self.server.service_status, "running")

    def test_shutdown_responds_before_drain_and_is_idempotent_while_stopping(self):
        status, body, _ = self.maintenance_post("/api/shutdown", {})
        self.assertEqual((status, json.loads(body)), (202, {"status": "stopping"}))
        self.assertTrue(self.service.drain_started.wait(1))
        self.assertTrue(self.server._shutdown_thread.is_alive())
        for path in ("/api/health", "/api/state"):
            status, body, _ = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["service_status"], "stopping")
        self.assertEqual(self.maintenance_post("/api/shutdown", {})[0], 202)
        self.assertEqual(self.service.shutdown_calls, [False, True])
        with patch("backend.server.choose_folder") as picker:
            for path in ("/api/actions", "/api/environments", "/api/environments/close-all", "/api/environments/login-check", "/api/shops", "/api/shops/close-all",
                         "/api/maintenance", "/api/maintenance/run", "/api/folders/pick",
                         "/api/environments/archive", "/api/environments/restore", "/api/environments/delete"):
                status, body, _ = self.maintenance_post(path, {})
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(body)["error"]["code"], "service_stopping")
            picker.assert_not_called()
        self.assertEqual(self.service.actions, [])
        self.assertEqual(self.service.closed_shop_groups, [])
        self.assertEqual(self.service.created_shops, [])
        self.assertEqual(self.service.maintenance_updates, [])
        self.assertEqual(self.service.maintenance_runs, 0)

    def test_shutdown_finishes_serve_loop_and_closes_the_actual_listener(self):
        self.assertEqual(self.maintenance_post("/api/shutdown", {})[0], 202)
        self.assertTrue(self.service.drain_started.wait(1))
        self.service.allow_shutdown.set()
        self.server._shutdown_thread.join(3)
        self.assertFalse(self.server._shutdown_thread.is_alive())
        self.thread.join(1)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.server.socket.fileno(), -1)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=0.3)


class ProcessShutdownTests(unittest.TestCase):
    def test_main_process_exits_cleanly_after_shutdown_on_an_ephemeral_port(self):
        # Exercise the real main()/HTTP lifecycle in a separate interpreter.
        # The fake service and port zero cannot contact production browsers
        # or claim the user's live workbench port.
        script = """
import json
import sys
from pathlib import Path
from backend import server, browser_service
ready = Path(sys.argv[1])
class FakeService:
    def __init__(self, registry): pass
    def shutdown(self, *, wait=True): pass
    def snapshot(self): return {}
class EphemeralServer(server.WorkbenchServer):
    def __init__(self, address, service, static_root):
        super().__init__(("127.0.0.1", 0), service, static_root)
        pending = ready.with_suffix(".tmp")
        pending.write_text(json.dumps({"port": self.server_port, "token": self.csrf_token}))
        pending.replace(ready)
browser_service.BrowserService = FakeService
server.WorkbenchServer = EphemeralServer
sys.argv = ["shutdown-test", "--registry", "unused-test-registry.json"]
server.main()
"""
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / "ready.json"
            process = subprocess.Popen([sys.executable, "-c", script, str(ready)],
                                       cwd=Path(__file__).resolve().parents[1],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), "isolated workbench failed to start")
                connection = json.loads(ready.read_text())
                client = HTTPConnection("127.0.0.1", connection["port"], timeout=3)
                try:
                    client.request("POST", "/api/shutdown", "{}", headers={
                        "Content-Type": "application/json", "X-CSRF-Token": connection["token"],
                    })
                    response = client.getresponse()
                    self.assertEqual(response.status, 202)
                    self.assertEqual(json.loads(response.read()), {"status": "stopping"})
                finally:
                    client.close()
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
                self.assertEqual(stderr, b"")
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", connection["port"]), timeout=0.3)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
