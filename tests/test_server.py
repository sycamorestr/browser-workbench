import json
import tempfile
import threading
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

    def close_all_shops(self, payload):
        self.closed_shop_groups.append(payload)
        return {"id": "close-all-job", "status": "queued"}


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
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
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
        settings = {"enabled": True, "interval_minutes": 120, "include_shared": True}
        status, body, _ = self.maintenance_post("/api/maintenance", settings)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"maintenance": settings})
        status, body, _ = self.maintenance_post("/api/maintenance/run", {})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)["job"]["id"], "maintenance-job")
        self.assertEqual(self.service.maintenance_updates, [settings])
        self.assertEqual(self.service.maintenance_runs, 1)
        self.assertEqual(self.service.actions, [])

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
        payload = {"name": "新店测试", "parent_folder": str(Path(self.tmp.name)), "login_username": ""}
        status, body, _ = self.maintenance_post("/api/shops", payload)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["environment"]["id"], "shop-example")
        self.assertEqual(self.service.created_shops, [payload])
        status, body, _ = self.maintenance_post("/api/shops/close-all", {"pause_maintenance": True})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)["job"]["id"], "close-all-job")
        self.assertEqual(self.service.closed_shop_groups, [{"pause_maintenance": True}])

    def test_folder_picker_uses_local_registry_default_and_can_cancel(self):
        with patch("backend.server.choose_folder", return_value={"path": None, "cancelled": True}) as picker:
            status, body, _ = self.maintenance_post("/api/folders/pick", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"path": None, "cancelled": True})
        picker.assert_called_once_with(str(self.service.registry_path.parent))

    def test_shop_routes_and_picker_require_token_origin_and_known_fields(self):
        routes = [("/api/shops", {"name": "测试", "parent_folder": str(Path(self.tmp.name)), "login_username": ""}),
                  ("/api/shops/close-all", {"pause_maintenance": True}), ("/api/folders/pick", {})]
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


if __name__ == "__main__":
    unittest.main()
