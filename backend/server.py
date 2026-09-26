"""Loopback-only HTTP API and static frontend, without a web framework."""
from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .folder_picker import choose_folder

APP_ID = "browser-workbench"
APP_VERSION = "0.2.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 16_384


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, service, static_root: Path):
        self.service = service
        self.static_root = static_root.resolve()
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: WorkbenchServer
    server_version = "BrowserWorkbench/0.2"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, fmt, *args):
        # No request bodies, URLs, account names or CDP endpoints in server logs.
        pass

    def _send(self, status, body: bytes, content_type="application/json; charset=utf-8"):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (ConnectionError, TimeoutError):
            # Refresh/cancel can disconnect during headers as well as body.
            # Do not attempt a second response on that already-closed socket.
            pass

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _error(self, status, code, message):
        self._json(status, {"error": {"code": code, "message": message}})

    def _valid_request(self, *, mutate=False):
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "").lower() not in allowed_hosts:
            self._error(403, "HOST_REJECTED", "请从本机面板地址访问。")
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in allowed_hosts}:
            self._error(403, "ORIGIN_REJECTED", "不允许其他网站控制本地浏览器。")
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._error(403, "ORIGIN_REJECTED", "不允许跨站访问。")
            return False
        if mutate and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf_token):
            self._error(403, "TOKEN_REJECTED", "面板连接已更新，请刷新后重试。")
            return False
        return True

    def do_GET(self):
        if not self._valid_request():
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/api/health":
            self._json(200, {"app": APP_ID, "version": APP_VERSION, "project_root": str(PROJECT_ROOT)})
            return
        if path == "/api/state":
            try:
                state = self.server.service.snapshot()
                self._json(200, {**state, "csrf_token": self.server.csrf_token})
            except Exception:
                self._error(503, "STATE_UNAVAILABLE", "暂时无法读取浏览器状态，请稍后刷新。")
            return
        if path.startswith("/api/"):
            self._error(404, "NOT_FOUND", "接口不存在。")
            return
        try:
            target = (self.server.static_root / (path.lstrip("/") or "index.html")).resolve()
            if not target.is_relative_to(self.server.static_root):
                self._error(403, "PATH_REJECTED", "路径不可访问。")
                return
            if not target.is_file():
                self._error(404, "NOT_FOUND", "页面资源不存在，请先构建前端。")
                return
            mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if target.suffix == ".js":
                mime = "text/javascript"
            self._send(200, target.read_bytes(), mime)
        except (OSError, ValueError):
            self._error(404, "NOT_FOUND", "页面资源不可访问。")

    def do_POST(self):
        if not self._valid_request(mutate=True):
            return
        path = urlsplit(self.path).path
        if path not in {"/api/actions", "/api/maintenance", "/api/maintenance/run",
                        "/api/shops", "/api/shops/close-all", "/api/folders/pick"}:
            self._error(404, "NOT_FOUND", "接口不存在。")
            return
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            self._error(415, "CONTENT_TYPE", "请求必须使用 JSON。")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MAX_BODY:
                self._error(413, "BODY_SIZE", "请求内容过大或为空。")
                return
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Invalid object")
            if path == "/api/folders/pick":
                if payload:
                    raise ValueError("Folder picker accepts no overrides")
                self._json(200, choose_folder(str(self.server.service.registry_path.parent)))
                return
            if path == "/api/shops":
                if set(payload) != {"name", "parent_folder", "login_username"}:
                    raise ValueError("Invalid shop fields")
                self._json(201, {"environment": self.server.service.create_shop(payload)})
                return
            if path == "/api/shops/close-all":
                if set(payload) != {"pause_maintenance"} or type(payload["pause_maintenance"]) is not bool:
                    raise ValueError("Invalid close-all settings")
                self._json(202, {"job": self.server.service.close_all_shops(payload)})
                return
            if path == "/api/maintenance":
                if set(payload) != {"enabled", "interval_minutes", "include_shared"}:
                    raise ValueError("Invalid maintenance settings")
                maintenance = self.server.service.update_maintenance(payload)
                self._json(200, {"maintenance": maintenance})
                return
            if path == "/api/maintenance/run":
                if payload:
                    raise ValueError("Run accepts no overrides")
                self._json(202, {"job": self.server.service.run_maintenance()})
                return
            if set(payload) - {"action", "environment_ids"}:
                raise ValueError("Invalid action fields")
            action = payload.get("action")
            ids = payload.get("environment_ids")
            if not isinstance(action, str) or not isinstance(ids, list) or not ids or len(ids) > 100 or not all(isinstance(i, str) for i in ids):
                raise ValueError("Invalid action")
            job = self.server.service.submit(action, ids)
            self._json(202, {"job": job})
        except (ValueError, UnicodeDecodeError):
            self._error(400, "INVALID_REQUEST", "操作参数无效。")
        except Exception as exc:
            code = getattr(exc, "code", "ACTION_FAILED")
            message = str(exc) if hasattr(exc, "code") else "操作提交失败，请刷新后重试。"
            status = 409 if "busy" in code.lower() or "conflict" in code.lower() else 503 if code.endswith("storage_failed") else 400
            self._error(status, code, message)


def main():
    from .browser_service import BrowserService

    parser = argparse.ArgumentParser(description="Local browser workbench")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--port", type=int, default=17860)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    service = BrowserService(args.registry)
    try:
        server = WorkbenchServer(("127.0.0.1", args.port), service, PROJECT_ROOT / "dist")
    except OSError:
        service.shutdown()
        sys.exit("Panel port already occupied. Reuse the existing panel or choose another port.")
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.shutdown()


if __name__ == "__main__":
    main()
