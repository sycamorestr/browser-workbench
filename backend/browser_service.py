"""Local browser environment management, independent of invoice execution.

The registry is read-only. Browser actions are explicit queued requests;
snapshot polling never logs in, opens tabs, or runs business collection.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import queue
import re
import shlex
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid

from .vendor.browser_controller import (
    BrowserControllerError,
    PlaywrightBrowserController,
    ProfileLock,
    _configured_debug_port,
    _resolve_browser_executable,
    _same_role_page,
    _same_site_family,
    _url_is_login,
    load_browser_config,
)
from .session_cookies import (
    business_cookie_scope,
    persist_session_cookies,
    read_metadata as read_cookie_metadata,
    write_metadata as write_cookie_metadata,
)
from . import maintenance as maintenance_state
from . import login_settings
from . import session_settings
from . import lifecycle
from .login_probe import probe_login_url
from . import shop_registry


VENDOR = Path(__file__).resolve().parent / "vendor"
GOODS_FRAME_URL = "https://src.erp321.com/erp-web-group/erp-scm-invoice-goods/index"
QIANNIU_HOME_URL = "https://myseller.taobao.com/"
ACTIONS = {"start", "focus", "check-login", "save-session", "close", "open-folder", "open-results"}
JST_PAGE_READY_SECONDS = 5


class BrowserServiceError(RuntimeError):
    def __init__(self, message: str, code: str = "browser_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_path(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve())).rstrip("\\/")


def resolve_path(base: Path, path: str) -> Path:
    value = Path(path).expanduser()
    return (value if value.is_absolute() else base / value).resolve()


def windows_arguments(command: str) -> list[str]:
    """Parse quoted Chromium flags without evaluating shell text."""
    if os.name != "nt":
        return shlex.split(command)
    import ctypes
    from ctypes import wintypes
    count = ctypes.c_int()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    values = shell32.CommandLineToArgvW(command, ctypes.byref(count))
    if not values:
        return []
    try:
        return [values[i] for i in range(count.value)]
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        kernel32.LocalFree(ctypes.cast(values, wintypes.HLOCAL))


def flag(arguments: list[str], name: str, default: str | None = None) -> str | None:
    for i, argument in enumerate(arguments):
        if argument.startswith(name + "="):
            return argument[len(name) + 1:]
        if argument == name and i + 1 < len(arguments):
            return arguments[i + 1]
    return default


def enumerate_browser_processes() -> list[dict[str, Any]]:
    """One Windows inventory call for every configured browser environment."""
    if os.name != "nt":
        raise BrowserServiceError("浏览器工作台目前需要 Windows", "platform_unsupported")
    script = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
        "Select-Object ProcessId,Name,ExecutablePath,CommandLine,"
        "@{Name='StartedAt';Expression={$_.CreationDate.ToUniversalTime().ToString('o')}} | "
        "ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
            check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        values = json.loads(result.stdout or "[]")
    except Exception as exc:
        raise BrowserServiceError("无法读取浏览器进程，请稍后刷新", "process_inventory_failed") from exc
    if isinstance(values, dict):
        values = [values]
    records = []
    for value in values or []:
        args = windows_arguments(str(value.get("CommandLine") or ""))
        if any(arg == "--type" or arg.startswith("--type=") for arg in args):
            continue
        root = flag(args, "--user-data-dir")
        if not root:
            continue
        raw_port = flag(args, "--remote-debugging-port")
        records.append({
            "pid": int(value["ProcessId"]), "name": str(value.get("Name") or "").lower(),
            "executable": str(value.get("ExecutablePath") or ""),
            "root": normalized_path(root), "profile": flag(args, "--profile-directory", "Default"),
            "port": int(raw_port) if raw_port and raw_port.isdigit() else None,
            "started_at": str(value.get("StartedAt") or ""),
        })
    return records


def profile_busy(root: Path) -> bool:
    """Probe the existing OS lock without creating, rewriting or deleting it."""
    path = root / ".qianniu-browser.lock"
    try:
        stream = path.open("rb", buffering=0)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return False
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return True
        return True
    finally:
        stream.close()


def display_url(url: str) -> str:
    """Keep page location useful without exposing login query credentials."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https", "about", "edge", "chrome"}:
            return parsed.scheme + ":" if parsed.scheme else ""
        hostname = parsed.hostname or ""
        host = hostname + (f":{parsed.port}" if parsed.port else "")
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return ""


def probe_cdp(port: int) -> dict[str, Any]:
    try:
        request = Request(f"http://127.0.0.1:{port}/json/list", method="GET")
        with build_opener(ProxyHandler({})).open(request, timeout=0.45) as response:
            tabs = json.loads(response.read().decode("utf-8"))
        if not isinstance(tabs, list):
            return {"available": False, "tabs_count": 0, "tabs": []}
        pages = [{"url": display_url(str(tab.get("url") or "")), "title": str(tab.get("title") or "")[:160]}
                 for tab in tabs if tab.get("type") == "page"]
        return {"available": True, "tabs_count": len(pages), "tabs": pages}
    except Exception:
        return {"available": False, "tabs_count": 0, "tabs": []}


def focus_window(pid: int, title: str = "") -> None:
    if os.name != "nt":
        raise BrowserServiceError("前台窗口操作目前需要 Windows", "platform_unsupported")
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    windows = []

    @callback_type
    def visit(hwnd, _):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == pid and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buffer, len(buffer))
            windows.append((hwnd, buffer.value))
        return True

    user32.EnumWindows(visit, 0)
    if not windows:
        raise BrowserServiceError("浏览器已连接，但未找到可见窗口", "window_missing")
    hwnd = next((handle for handle, text in windows if title and title in text), windows[0][0])
    foreground = user32.GetForegroundWindow()
    current_thread = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(target_thread and target_thread != current_thread and
                    user32.AttachThreadInput(current_thread, target_thread, True))
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if not user32.SetForegroundWindow(hwnd):
            raise BrowserServiceError("系统未允许切到前台，请点击任务栏中的该浏览器", "focus_denied")
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, target_thread, False)


@dataclass(frozen=True)
class Environment:
    id: str
    name: str
    kind: str
    config_path: Path
    config: dict[str, Any]
    executable_path: str
    login_username: str = ""

    @property
    def root(self) -> Path:
        return Path(self.config["user_data_dir"])

    @property
    def port(self) -> int:
        return _configured_debug_port(self.config)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def home_role(self) -> str:
        return "home" if "home" in self.config["browser_sessions"] else "goods"

    @property
    def home_url(self) -> str:
        value = self.config["browser_sessions"][self.home_role]
        return value["url"] if isinstance(value, dict) else value

    @property
    def auth_adapter(self) -> str:
        target = urlsplit(self.home_url)
        if target.hostname == "myseller.taobao.com":
            return "qianniu"
        if target.hostname == "fp.erp321.com" and target.path.rstrip("/") == "/setting/goodsManage":
            return "jst"
        return "generic"


class BrowserService:
    def __init__(self, registry_path: Path) -> None:
        self.registry_path = Path(registry_path).expanduser().resolve()
        self.issuer = ""
        self.output_root = self.registry_path.parent / "outputs"
        self._lifecycle_path = self.registry_path.parent / lifecycle.FILE_NAME
        self._lifecycle = lifecycle.load(self._lifecycle_path)
        self.environments = self._read_registry()
        self._state_lock = threading.RLock()
        self._creation_lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._session_settings_lock = threading.Lock()
        self._autosave_action_lock = threading.Lock()
        self._session_autosave_path = self.registry_path.parent / session_settings.FILE_NAME
        self._session_autosave = session_settings.load(self._session_autosave_path)
        self._session_autosave_revision = 0
        self._inventory: list[dict[str, Any]] = []
        self._inventory_at = 0.0
        self._inventory_error: str | None = None
        self._observations: dict[str, dict[str, Any]] = {}
        self._process_keys: dict[str, tuple[Any, ...] | None] = {}
        self._auth = {key: self._unchecked() for key in self.environments}
        self._login_settings_path = self.registry_path.parent / login_settings.FILE_NAME
        self._login_checks = login_settings.load(self._login_settings_path)
        self._probe_targets: dict[str, dict[str, Any]] = {}
        self._checkpoint_env: str | None = None
        self._cookie_sync = {key: read_cookie_metadata(env.root) for key, env in self.environments.items()}
        self._next_cookie_sync: dict[str, float] = {}
        for key in self.environments:
            self._schedule_cookie_sync(key)
        self._idle_retry_after = 0.0
        self._jobs: list[dict[str, Any]] = []
        self._activity: list[dict[str, Any]] = []
        self._pending: dict[str, int] = {}
        self._close_all_job_id: str | None = None
        self._maintenance_path = self.registry_path.parent / maintenance_state.FILE_NAME
        self._maintenance, self._maintenance_required = maintenance_state.load(
            self._maintenance_path, {key: env.name for key, env in self.environments.items()},
        )
        self._maintenance_job_id: str | None = None
        self._maintenance_active_env: str | None = None
        self._maintenance_retry_after = 0.0
        self._queue: queue.Queue[Any] = queue.Queue()
        self._closed = False
        self._worker = threading.Thread(target=self._work, name="browser-actions", daemon=True)
        self._worker.start()

    def _read_registry(self) -> dict[str, Environment]:
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                raise ValueError("registry")
            self.issuer = str(raw.get("issuer") or "").strip()
            self.output_root = resolve_path(self.registry_path.parent, raw.get("output_root") or "outputs")
            shops = raw.get("shops", [])
            if not isinstance(shops, list):
                raise ValueError("shops")
            retired_ids = {env_id for env_id, record in self._lifecycle.items() if record["state"] == "deleted"}
            descriptions = [(str(shop["id"]), str(shop["store"]), "shop", shop["browser_config"],
                                 str(shop.get("login_username") or ""))
                                for shop in [*shops, *shop_registry.load_custom_shops(self.registry_path, retired_ids=retired_ids)]]
            # Legacy registry fields remain readable without making this
            # particular site a required environment or a public category.
            if raw.get("jst_browser_config"):
                descriptions.append(("piaoju", "票聚", "shared", raw["jst_browser_config"], ""))
            result = {}
            roots: set[str] = set()
            ports: set[int] = set()
            for env_id, name, kind, path, username in descriptions:
                if self._lifecycle.get(env_id, {}).get("state") == "deleted":
                    # A tombstone retains resource reservations, but deleting
                    # an old config later must not stop unrelated environments.
                    continue
                if env_id in result:
                    raise ValueError("duplicate environment")
                env = self._load_environment(env_id, name, kind, path, username)
                root = normalized_path(env.root)
                port = env.port
                if root in roots or port in ports:
                    raise ValueError("isolation")
                roots.add(root)
                ports.add(port)
                result[env_id] = env
            return result
        except shop_registry.ShopRegistryError as exc:
            raise BrowserServiceError(exc.message, exc.code) from exc
        except (OSError, ValueError, KeyError, TypeError, BrowserControllerError) as exc:
            raise BrowserServiceError("环境清单或浏览器配置无效，请检查配置文件", "configuration") from exc

    def _load_environment(self, env_id: str, name: str, kind: str, path: str,
                          username: str = "") -> Environment:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", env_id) or not name.strip():
            raise ValueError("environment")
        config_path = resolve_path(self.registry_path.parent, path)
        config, _ = load_browser_config(config_path)
        sessions = config["browser_sessions"]
        if "home" in sessions:
            # Manage the explicit homepage without overwriting its URL or
            # opening unrelated legacy invoice/order roles from this panel.
            config["browser_sessions"] = {"home": sessions["home"]}
        elif kind == "shared" and set(sessions) == {"goods"}:
            pass  # Keep the legacy goods page and login adapter compatible.
        elif (set(sessions).issubset({"invoice", "orders"}) and sessions and
              all(urlsplit(value["url"] if isinstance(value, dict) else value).hostname == "myseller.taobao.com"
                  for value in sessions.values())):
            # Existing invoice-tool profiles had no home role. Preserve this
            # compatibility only for their known seller-site configuration.
            config["browser_sessions"] = {"home": {"url": QIANNIU_HOME_URL}}
        else:
            raise ValueError("missing homepage")
        try:
            executable = str(_resolve_browser_executable(config))
        except BrowserControllerError:
            executable = str(config.get("executable_path") or config.get("browser_executable") or "")
        return Environment(env_id, name.strip(), kind, config_path, config, executable, username)

    def _is_active(self, env_id: str) -> bool:
        return env_id in self.environments and env_id not in self._lifecycle

    def _active_ids(self) -> list[str]:
        return [env_id for env_id in self.environments if self._is_active(env_id)]

    @staticmethod
    def _environment_resource(env: Environment) -> dict[str, Any]:
        return {"id": env.id, "name": env.name, "user_data_dir": str(env.root),
                "download_dir": str(env.config["download_dir"]), "debug_port": env.port,
                "config_path": str(env.config_path), "login_username": env.login_username,
                "home_url": env.home_url}

    def _environment_has_work(self, env_id: str) -> bool:
        if self._pending.get(env_id) or self._checkpoint_env == env_id:
            return True
        if self._maintenance_job_id:
            job = next((item for item in self._jobs if item["id"] == self._maintenance_job_id), None)
            return job is None or env_id in job["ids"]
        return False

    def change_environment_lifecycle(self, env_id: str, operation: str,
                                     confirm_name: str | None = None) -> dict[str, str]:
        if operation not in {"archive", "restore", "delete"}:
            raise BrowserServiceError("不支持的环境管理操作", "invalid_lifecycle_operation")
        # Share creation's lock order so the resource list cannot change while
        # a new environment is allocating its name, profile or debug port.
        with self._creation_lock:
            with self._state_lock:
                if self._closed:
                    raise BrowserServiceError("工作台正在关闭", "service_closed")
                if not isinstance(env_id, str) or env_id not in self.environments:
                    raise BrowserServiceError("请选择仍登记在工作台中的浏览器环境", "invalid_environment")
                env = self.environments[env_id]
                archived = not self._is_active(env_id)
                if operation == "archive" and archived:
                    raise BrowserServiceError("此环境已经归档", "environment_state_conflict")
                if operation in {"restore", "delete"} and not archived:
                    raise BrowserServiceError("请先归档此环境，再执行恢复或删除登记", "environment_state_conflict")
                if operation == "delete" and confirm_name != env.name:
                    raise BrowserServiceError("确认名称与环境名称不一致，未删除登记", "confirmation_failed")
                if self._environment_has_work(env_id) or profile_busy(env.root):
                    raise BrowserServiceError("此环境仍有任务等待或执行，请完成后再管理登记", "environment_busy")
                # Do not trust the four-second UI inventory cache for changes.
                # Holding state admission prevents local work from racing this
                # check; an external profile lock is checked a second time.
                inventory = enumerate_browser_processes()
                record, conflict = self._owned_process(env, inventory)
                if conflict:
                    raise BrowserServiceError("此环境的浏览器目录或调试端口存在运行冲突，请先处理", "environment_conflict")
                if record is not None and operation != "restore":
                    raise BrowserServiceError("请先关闭此环境的浏览器，再管理登记", "environment_running")
                if profile_busy(env.root):
                    raise BrowserServiceError("此环境正被其他任务使用，请完成后再管理登记", "environment_busy")
                target = {"archive": "archived", "restore": "active", "delete": "deleted"}[operation]
                updated = lifecycle.save_state(self._lifecycle_path, env_id, target, self._environment_resource(env))
                self._lifecycle = updated
                self._auth[env_id] = self._unchecked("环境已恢复，请重新检查登录" if target == "active" else "环境已归档，自动任务已排除")
                self._probe_targets.pop(env_id, None)
                self._schedule_cookie_sync(env_id)
                if target == "deleted":
                    self.environments = {key: value for key, value in self.environments.items() if key != env_id}
                    self._next_cookie_sync.pop(env_id, None)
                    self._observations.pop(env_id, None)
                    self._process_keys.pop(env_id, None)
                self._inventory_at = 0
                return {"environment_id": env_id, "state": target}

    def create_shop(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Filesystem work is serialized separately, without keeping HTTP
        # snapshots or already queued browser actions under the state lock.
        with self._creation_lock:
            with self._state_lock:
                if self._closed:
                    raise BrowserServiceError("工作台正在关闭", "service_closed")
                existing = [self._environment_resource(env) for env in self.environments.values()]
                existing.extend({**record["resource"], "deleted": True}
                                for record in self._lifecycle.values() if record["state"] == "deleted")
            try:
                record = shop_registry.create_shop(self.registry_path, existing, payload)
                env = self._load_environment(record["id"], record["store"], "shop", record["browser_config"],
                                             record.get("login_username") or "")
            except shop_registry.ShopRegistryError as exc:
                raise BrowserServiceError(exc.message, exc.code) from exc
            except (OSError, ValueError, KeyError, TypeError, BrowserControllerError) as exc:
                raise BrowserServiceError("环境记录已保存，但未能载入，请检查配置后重启工作台", "shop_configuration") from exc
            with self._state_lock:
                if (env.id in self.environments or any(
                        normalized_path(current.root) == normalized_path(env.root) or current.port == env.port
                        for current in self.environments.values())):
                    raise BrowserServiceError("新环境与已有环境冲突，未注册到当前服务", "shop_conflict")
                self._auth[env.id] = self._unchecked()
                self._cookie_sync[env.id] = read_cookie_metadata(env.root)
                self._observations[env.id] = {"running": False, "cdp": "stopped", "tabs_count": 0, "tabs": []}
                self._process_keys[env.id] = None
                self._pending[env.id] = 0
                # Readers holding the previous mapping can finish safely.
                self.environments = {**self.environments, env.id: env}
                self._schedule_cookie_sync(env.id)
                self._inventory_at = 0
            return {"id": env.id, "name": env.name, "kind": "browser", "login_username": env.login_username,
                    "home_url": env.home_url,
                    "login_check": self.login_check_settings(env.id),
                    "login_check_platform": None if env.auth_adapter == "generic" else env.auth_adapter,
                    "user_data_dir": str(env.root), "download_dir": env.config["download_dir"],
                    "debug_port": env.port, "config_path": str(env.config_path)}

    @staticmethod
    def _unchecked(message: str = "尚未检查当前登录状态") -> dict[str, Any]:
        return {"status": "unchecked", "checked_at": None, "message": message, "identity": ""}

    def login_check_settings(self, env_id: str) -> dict[str, Any]:
        with self._state_lock:
            return deepcopy(self._login_checks.get(env_id, login_settings.DEFAULTS))

    def update_login_check(self, env_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        settings = login_settings.validate_settings(payload)
        with self._state_lock:
            if self._closed:
                raise BrowserServiceError("工作台正在关闭", "service_closed")
            if not isinstance(env_id, str) or env_id not in self.environments:
                raise BrowserServiceError("请选择已登记的浏览器环境", "invalid_environment")
            env = self.environments[env_id]
            if not self._is_active(env_id):
                raise BrowserServiceError("环境已归档，请恢复后再修改登录检查", "environment_archived")
            if (self._pending.get(env_id) or self._maintenance_job_id or self._checkpoint_env == env_id
                    or profile_busy(env.root)):
                raise BrowserServiceError("环境仍有任务等待或执行，请完成后再修改登录检查", "environment_busy")
            if settings["mode"] == "platform" and env.auth_adapter == "generic":
                raise BrowserServiceError("此主页尚无专用平台检查，请使用通用 URL 检查", "unsupported_login_platform")
            candidate = {**self._login_checks, env_id: settings}
            # Publication is atomic. A write failure leaves the previous rule,
            # visible auth result and maintenance block intact.
            login_settings.save(self._login_settings_path, candidate)
            self._login_checks = candidate
            self._auth[env_id] = self._unchecked("登录检查设置已更新，请重新检查")
            self._maintenance_required.discard(env_id)
            self._persist_maintenance()
            return {"login_check": deepcopy(settings), "auth": deepcopy(self._auth[env_id])}

    @staticmethod
    def _process_key(record: dict[str, Any] | None) -> tuple[Any, ...] | None:
        if record is None:
            return None
        return tuple(record.get(key) for key in ("pid", "started_at", "root", "profile", "port"))

    @staticmethod
    def _owned_process(env: Environment, inventory: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
        root = normalized_path(env.root)
        same_root = [item for item in inventory if item["root"] == root]
        if not same_root:
            return None, any(item.get("port") == env.port for item in inventory)
        expected_name = "msedge.exe" if str(env.config.get("browser", "Edge")).lower() == "edge" else "chrome.exe"
        exact = [item for item in same_root if item.get("profile") == env.config["profile_directory"]
                 and item.get("port") == env.port and item.get("name") == expected_name]
        if len(exact) != 1 or len(same_root) != 1:
            return None, True
        return exact[0], False

    def _refresh(self, *, force: bool = False) -> None:
        with self._poll_lock:
            if not force and time.monotonic() - self._inventory_at < 4:
                return
            try:
                with self._state_lock:
                    known_process_keys = dict(self._process_keys)
                    environment_snapshot = self.environments
                inventory = enumerate_browser_processes()
            except BrowserServiceError as exc:
                self._inventory_error = str(exc)
                self._inventory_at = time.monotonic()
                return
            observations = {}
            owned = {}
            for env_id, env in environment_snapshot.items():
                record, conflict = self._owned_process(env, inventory)
                owned[env_id] = record
                observations[env_id] = {"running": any(item["root"] == normalized_path(env.root) for item in inventory),
                                        "cdp": "conflict" if conflict else "stopped",
                                        "tabs_count": 0, "tabs": []}
            live = [(env_id, environment_snapshot[env_id].port) for env_id, record in owned.items() if record]
            if live:
                with ThreadPoolExecutor(max_workers=min(10, len(live))) as executor:
                    probes = list(executor.map(lambda pair: probe_cdp(pair[1]), live))
                for (env_id, _), probe in zip(live, probes):
                    observations[env_id].update(cdp="connected" if probe["available"] else "unavailable",
                                                tabs_count=probe["tabs_count"], tabs=probe.get("tabs", []))
            with self._state_lock:
                for env_id, record in owned.items():
                    process_key = self._process_key(record)
                    if self._process_keys.get(env_id) != known_process_keys.get(env_id):
                        # An explicit action adopted a newly started process
                        # while this inventory was in flight. Do not erase its
                        # fresh identity check using the earlier inventory.
                        continue
                    if self._process_keys.get(env_id) != process_key:
                        self._auth[env_id] = self._unchecked("浏览器进程已变化，请重新检查登录")
                    self._process_keys[env_id] = process_key
                self._observations = {**self._observations, **observations}
                self._inventory = inventory
                self._inventory_error = None
                self._inventory_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        self._refresh()
        with self._state_lock:
            environments = []
            archived_environments = []
            for env_id, env in self.environments.items():
                auth = self._auth[env_id]
                if auth.get("checked_at") and auth["status"] != "unchecked":
                    try:
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(auth["checked_at"])).total_seconds()
                        if age > 900:
                            auth = {**auth, "status": "unchecked", "message": "上次登录检查已超过 15 分钟，请重新检查"}
                            self._auth[env_id] = auth
                    except ValueError:
                        self._auth[env_id] = self._unchecked()
                observed = self._observations.get(env_id, {"running": False, "cdp": "unavailable", "tabs_count": 0, "tabs": []})
                if self._inventory_error:
                    observed = {**observed, "cdp": "unavailable"}
                archived = not self._is_active(env_id)
                row = {
                    "id": env_id, "name": env.name, "kind": "browser", **observed,
                    "archived": archived,
                    "login_username": env.login_username,
                    "home_url": env.home_url,
                    "login_check": self.login_check_settings(env_id),
                    "login_check_platform": None if env.auth_adapter == "generic" else env.auth_adapter,
                    "auth": deepcopy(self._auth[env_id]),
                    "cookie_sync": deepcopy(self._cookie_sync[env_id]),
                    "busy": bool(self._pending.get(env_id) or profile_busy(env.root)),
                    "user_data_dir": str(env.root), "profile_directory": env.config["profile_directory"],
                    "config_path": str(env.config_path), "download_dir": env.config["download_dir"],
                    "executable_path": env.executable_path, "debug_port": env.port, "cdp_endpoint": env.endpoint,
                    "configured_urls": {role: display_url(value["url"] if isinstance(value, dict) else value)
                                        for role, value in env.config["browser_sessions"].items()},
                }
                if archived:
                    row["archived_at"] = self._lifecycle[env_id]["changed_at"]
                    archived_environments.append(row)
                else:
                    environments.append(row)
            summary = {"total": len(environments), "running": sum(env["running"] for env in environments),
                       "connected": sum(env["cdp"] == "connected" for env in environments),
                       "verified": sum(env["auth"]["status"] == "verified" for env in environments),
                       "assumed": sum(env["auth"]["status"] == "assumed" for env in environments),
                       "attention": sum(env["auth"]["status"] in {"required", "error"}
                                        or env["cdp"] in {"unavailable", "conflict"} for env in environments),
                       "busy": sum(env["busy"] for env in environments)}
            return {"environments": environments, "archived_environments": archived_environments,
                    "registry_path": str(self.registry_path),
                    "issuer": self.issuer, "updated_at": now(), "activity": deepcopy(self._activity[-50:]),
                    "jobs": deepcopy(self._jobs[-20:]), "summary": summary,
                    "maintenance": deepcopy(self._maintenance),
                    "session_autosave": deepcopy(self._session_autosave),
                    "creation_defaults": {"parent_folder": str(self.registry_path.parent)},
                    "error": {"code": "process_inventory_failed", "message": self._inventory_error}
                    if self._inventory_error else None}

    def submit(self, action: str, ids: list[str]) -> dict[str, str]:
        if action not in ACTIONS:
            raise BrowserServiceError("不支持的浏览器操作", "invalid_action")
        if (not isinstance(ids, list) or not ids or any(not isinstance(item, str) for item in ids)
                or len(set(ids)) != len(ids)):
            raise BrowserServiceError("请选择清单内的浏览器环境，且不要重复选择", "invalid_environment")
        with self._state_lock:
            if self._closed:
                raise BrowserServiceError("工作台正在关闭", "service_closed")
            if set(ids) - self.environments.keys():
                raise BrowserServiceError("请选择仍登记在工作台中的浏览器环境", "invalid_environment")
            if action not in {"open-folder", "open-results", "close"} and any(not self._is_active(env_id) for env_id in ids):
                raise BrowserServiceError("归档环境仅可查看目录或关闭浏览器，请恢复后再操作", "environment_archived")
            job = {"id": uuid.uuid4().hex, "action": action, "ids": list(ids), "status": "queued",
                   "created_at": now(), "finished_at": None, "results": []}
            self._jobs.append(job)
            for env_id in ids:
                self._pending[env_id] = self._pending.get(env_id, 0) + 1
            self._queue.put(job)
            return {"id": job["id"], "status": "queued"}

    def close_all_shops(self, payload: dict[str, Any]) -> dict[str, str]:
        if (not isinstance(payload, dict) or set(payload) != {"pause_maintenance"}
                or type(payload["pause_maintenance"]) is not bool):
            raise BrowserServiceError("关闭设置无效，请明确是否暂停定时维护", "invalid_close_all")
        with self._state_lock:
            if self._closed:
                raise BrowserServiceError("工作台正在关闭", "service_closed")
            if self._close_all_job_id:
                raise BrowserServiceError("已有关闭全部环境的任务正在等待或执行", "close_all_busy")
            ids = self._active_ids()
            if not ids:
                raise BrowserServiceError("尚未登记浏览器环境，请先新建环境", "no_environments")
            if payload["pause_maintenance"]:
                self.update_maintenance({"enabled": False,
                                         "interval_minutes": self._maintenance["interval_minutes"]})
            if self._maintenance_job_id:
                current = next((job for job in self._jobs if job["id"] == self._maintenance_job_id), None)
                if current is not None:
                    # Closing all environments also stops a manual round
                    # before it can reopen an already closed browser.
                    current["stop_requested"] = True
            result = self.submit("close", ids)
            self._jobs[-1]["close_all"] = True
            self._close_all_job_id = result["id"]
            return result

    def _schedule_cookie_sync(self, env_id: str) -> None:
        """Caller holds state lock (or is initializing the service)."""
        if self._session_autosave["enabled"] and self._is_active(env_id):
            self._next_cookie_sync[env_id] = time.monotonic() + self._session_autosave["interval_minutes"] * 60
        else:
            self._next_cookie_sync.pop(env_id, None)

    def _automatic_save_allowed(self, env_id: str, revision: int | None = None) -> bool:
        with self._state_lock:
            return (not self._closed and self._session_autosave["enabled"] and self._queue.empty()
                    and self._is_active(env_id)
                    and (revision is None or revision == self._session_autosave_revision))

    def update_session_autosave(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = session_settings.validate_settings(payload)
        except ValueError as exc:
            raise BrowserServiceError("自动保存设置无效，请完整填写开关和间隔", "invalid_session_autosave") from exc
        # Serialize settings updates without blocking the worker's state access
        # while an already admitted checkpoint releases its profile lock.
        with self._session_settings_lock:
            with self._state_lock:
                if self._closed:
                    raise BrowserServiceError("工作台正在关闭", "service_closed")
                try:
                    session_settings.save(self._session_autosave_path, settings)
                except OSError as exc:
                    raise BrowserServiceError("自动保存设置保存失败，请检查配置目录", "session_autosave_storage_failed") from exc
                self._session_autosave = settings
                self._session_autosave_revision += 1
                self._next_cookie_sync.clear()
                for env_id in self.environments:
                    self._schedule_cookie_sync(env_id)
                self._idle_retry_after = 0.0
            if not settings["enabled"]:
                # Return only after a checkpoint admitted under the old setting
                # has yielded and detached. No new automatic work can start.
                with self._autosave_action_lock:
                    pass
            return deepcopy(settings)

    def maintenance_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return deepcopy(self._maintenance)

    def _persist_maintenance(self) -> None:
        """Caller holds state lock; background I/O failure stays visible."""
        try:
            maintenance_state.save(self._maintenance_path, self._maintenance, self._maintenance_required)
        except OSError:
            self._maintenance["message"] = "维护记录保存失败，请检查配置目录；当前服务内的状态仍保留"

    def update_maintenance(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = maintenance_state.validate_settings(payload)
        except ValueError as exc:
            raise BrowserServiceError("维护设置无效，请完整填写开关和间隔", "invalid_maintenance") from exc
        with self._state_lock:
            if self._closed:
                raise BrowserServiceError("工作台正在关闭", "service_closed")
            candidate = {**deepcopy(self._maintenance), **settings}
            if not settings["enabled"]:
                candidate["next_run_at"] = None
                candidate["message"] = "定时维护已暂停，可手动维护一次"
                if candidate["running"]:
                    candidate["message"] = "定时维护已暂停，当前环境完成后不再继续定时轮次"
                else:
                    candidate["last_status"] = "paused"
            elif (not self._maintenance["enabled"]
                  or settings["interval_minutes"] != self._maintenance["interval_minutes"]
                  or not candidate["next_run_at"]):
                candidate["next_run_at"] = maintenance_state.later(settings["interval_minutes"])
                candidate["message"] = "定时维护已开启，将按设置间隔访问后台"
            try:
                maintenance_state.save(self._maintenance_path, candidate, self._maintenance_required)
            except OSError as exc:
                raise BrowserServiceError("维护设置保存失败，请检查配置目录", "maintenance_storage_failed") from exc
            self._maintenance = candidate
            if not settings["enabled"] and self._maintenance_job_id:
                job = next((job for job in self._jobs if job["id"] == self._maintenance_job_id), None)
                if job is not None and job.get("trigger") == "scheduled":
                    job["stop_requested"] = True
            return deepcopy(self._maintenance)

    def run_maintenance(self) -> dict[str, str]:
        return self._start_maintenance(scheduled=False)

    def _start_maintenance(self, *, scheduled: bool) -> dict[str, str]:
        with self._state_lock:
            if self._closed:
                raise BrowserServiceError("工作台正在关闭", "service_closed")
            if self._maintenance_job_id:
                raise BrowserServiceError("已有一轮会话维护正在等待或执行", "maintenance_busy")
            if self._close_all_job_id:
                raise BrowserServiceError("正在关闭全部环境，请完成后再运行维护", "maintenance_busy")
            ids = self._active_ids()
            if not ids:
                raise BrowserServiceError("尚未登记浏览器环境，请先新建环境", "no_environments")
            job = {"id": uuid.uuid4().hex, "action": "maintain-session", "ids": ids, "status": "queued",
                   "created_at": now(), "finished_at": None, "results": [],
                   "trigger": "scheduled" if scheduled else "manual", "stop_requested": False}
            candidate = {**self._maintenance, "running": True, "last_status": "running",
                         "last_run_at": now(), "last_finished_at": None, "last_results": [],
                         "message": "会话维护已排队，将依次处理各环境"}
            if candidate["enabled"]:
                candidate["next_run_at"] = maintenance_state.later(candidate["interval_minutes"])
            try:
                maintenance_state.save(self._maintenance_path, candidate, self._maintenance_required)
            except OSError as exc:
                raise BrowserServiceError("无法保存维护轮次，未开始执行", "maintenance_storage_failed") from exc
            self._maintenance = candidate
            self._maintenance_job_id = job["id"]
            self._jobs.append(job)
            self._queue.put(job)
            return {"id": job["id"], "status": "queued"}

    def _maintenance_tick(self) -> None:
        # Clock checks run in the server worker, independent of HTTP polling.
        with self._state_lock:
            if (self._closed or not self._active_ids() or self._maintenance_job_id or self._close_all_job_id or not self._maintenance["enabled"]
                    or not self._queue.empty() or time.monotonic() < self._maintenance_retry_after):
                return
            due = self._maintenance["next_run_at"]
            if not due or datetime.fromisoformat(due) > datetime.now(timezone.utc):
                return
            try:
                self._start_maintenance(scheduled=True)
            except BrowserServiceError:
                self._maintenance_retry_after = time.monotonic() + 60
                self._maintenance["message"] = "本轮定时维护未能开始，将稍后重试"

    def _clear_maintenance_login_block(self, env_id: str) -> None:
        with self._state_lock:
            if env_id in self._maintenance_required:
                self._maintenance_required.remove(env_id)
                self._persist_maintenance()

    def _maintenance_step(self, job: dict[str, Any]) -> None:
        """Run one environment, then yield to already queued user actions."""
        with self._state_lock:
            if self._maintenance_job_id != job["id"]:
                return
            remaining = job["ids"][len(job["results"]):]
            paused = self._closed or job["stop_requested"] or (job["trigger"] == "scheduled" and
                                                               not self._maintenance["enabled"])
            current_ids = remaining if paused else remaining[:1]
            job["status"] = "running"
            job.setdefault("started_at", now())
        for env_id in current_ids:
            env = self.environments[env_id]
            with self._state_lock:
                # Shutdown can arrive between choosing the next environment
                # and admitting it here. Only an already admitted one runs.
                paused = paused or self._closed or job["stop_requested"]
                self._pending[env_id] = self._pending.get(env_id, 0) + 1
                self._maintenance_active_env = env_id
            try:
                if paused:
                    result = {"status": "skipped", "code": "maintenance_paused", "message": "本轮维护已停止，本环境未执行"}
                elif job["trigger"] == "scheduled" and env_id in self._maintenance_required:
                    result = {"status": "skipped", "code": "login_required", "message": "等待人工登录；请登录后手动检查，再恢复定时维护"}
                elif profile_busy(env.root):
                    result = {"status": "skipped", "code": "profile_locked", "message": "此浏览器正被其他任务使用，本轮跳过"}
                else:
                    value = self._perform("maintain-session", env)
                    auth = value.get("auth", {})
                    if auth.get("status") == "required":
                        with self._state_lock:
                            self._maintenance_required.add(env_id)
                        result = {"status": "skipped", "code": "login_required", "message": "需要人工登录，后续定时轮次将跳过此环境"}
                    elif auth.get("status") not in {"verified", "assumed"}:
                        result = {"status": "failed", "code": "login_check_failed", "message": value.get("message", "登录检查未完成")}
                    else:
                        result = {"status": "complete", "message": value.get("message", "已访问后台并保存会话")}
                        self._clear_maintenance_login_block(env_id)
            except Exception as exc:
                error = self._friendly_error(exc)
                result = {"status": "skipped" if error.code in {"profile_locked", "login_required"} else "failed",
                          "code": error.code, "message": str(error)}
                if error.code == "login_required":
                    with self._state_lock:
                        self._maintenance_required.add(env_id)
            result = {"id": env_id, "name": env.name, **result}
            with self._state_lock:
                self._pending[env_id] -= 1
                self._maintenance_active_env = None
                job["results"].append(result)
                self._maintenance["last_results"] = deepcopy(job["results"])
                self._maintenance["message"] = f"已处理 {len(job['results'])}/{len(job['ids'])} 个环境"
                self._activity.append({"id": uuid.uuid4().hex, "at": now(), "environment_id": env_id,
                                       "name": env.name, "action": "maintain-session",
                                       "level": "error" if result["status"] == "failed" else "info",
                                       "message": result["message"]})
                self._activity = self._activity[-50:]
                self._persist_maintenance()
        with self._state_lock:
            self._inventory_at = 0
            if len(job["results"]) < len(job["ids"]):
                if self._closed or job["stop_requested"] or (job["trigger"] == "scheduled" and
                                                            not self._maintenance["enabled"]):
                    # Finalize skips before a shutdown sentinel can stop the
                    # worker; a paused round must not remain marked running.
                    self._maintenance_step(job)
                    return
                # Do not drain the entire round in one call: manual work
                # queued during this environment gets the next slot.
                self._queue.put(job)
                return
            statuses = [result["status"] for result in job["results"]]
            outcome = "complete" if all(value == "complete" for value in statuses) else (
                "failed" if all(value == "failed" for value in statuses) else "partial")
            job["status"] = outcome
            job["finished_at"] = now()
            final_status = "paused" if any(result.get("code") == "maintenance_paused" for result in job["results"]) else outcome
            self._maintenance.update(running=False, last_status=final_status, last_finished_at=job["finished_at"],
                                     message="本轮维护已暂停" if final_status == "paused" else "本轮会话维护已结束，请查看各环境结果")
            self._maintenance["next_run_at"] = maintenance_state.later(self._maintenance["interval_minutes"]) if self._maintenance["enabled"] else None
            self._maintenance_job_id = None
            self._jobs = [item for item in self._jobs if item["status"] in {"queued", "running"}] + [
                item for item in self._jobs if item["status"] not in {"queued", "running"}][-20:]
            self._persist_maintenance()

    def _abort_maintenance(self, job: dict[str, Any]) -> None:
        """Contain scheduler faults while preserving unrelated queued jobs."""
        with self._state_lock:
            active = self._maintenance_active_env
            if active is not None:
                self._pending[active] -= 1
                self._maintenance_active_env = None
            completed = {result["id"] for result in job["results"]}
            for env_id in job["ids"]:
                if env_id not in completed:
                    job["results"].append({"id": env_id, "name": self.environments[env_id].name,
                                           "status": "failed" if env_id == active else "skipped",
                                           "code": "maintenance_aborted", "message": "本轮维护因内部异常停止，可稍后手动重试"})
            job.update(status="failed", finished_at=now())
            self._maintenance.update(running=False, last_status="failed", last_finished_at=job["finished_at"],
                                     last_results=deepcopy(job["results"]), message="本轮维护异常，手动操作仍可使用")
            self._maintenance["next_run_at"] = maintenance_state.later(self._maintenance["interval_minutes"]) if self._maintenance["enabled"] else None
            self._maintenance_job_id = None
            self._persist_maintenance()

    def _work(self) -> None:
        while True:
            try:
                self._maintenance_tick()
            except Exception:
                # A clock/persistence fault must never stop manual actions.
                with self._state_lock:
                    self._maintenance_retry_after = time.monotonic() + 60
                    self._maintenance["message"] = "定时维护检查异常，将稍后重试；手动操作仍可使用"
            try:
                job = self._queue.get(timeout=1)
            except queue.Empty:
                if time.monotonic() >= self._idle_retry_after:
                    try:
                        self._idle_checkpoint()
                    except Exception:
                        # A failed background probe must not stop the sole
                        # worker or starve subsequent explicit actions.
                        with self._state_lock:
                            self._idle_retry_after = time.monotonic() + self._session_autosave["interval_minutes"] * 60
                            self._activity.append({"id": uuid.uuid4().hex, "at": now(), "environment_id": "",
                                                   "name": "工作台", "action": "save-session", "level": "error",
                                                   "message": "自动保存检查暂未完成，稍后重试；手动操作仍可使用"})
                            self._activity = self._activity[-50:]
                continue
            if job is None:
                self._queue.task_done()
                return
            if job["action"] == "maintain-session":
                try:
                    self._maintenance_step(job)
                except Exception:
                    self._abort_maintenance(job)
                finally:
                    self._queue.task_done()
                continue
            with self._state_lock:
                job["status"] = "running"
                job["started_at"] = now()
            for env_id in job["ids"]:
                env = self.environments[env_id]
                try:
                    value = self._perform(job["action"], env)
                    result = {"id": env_id, "status": "complete", **value}
                except Exception as exc:
                    error = self._friendly_error(exc)
                    result = {"id": env_id, "status": "failed", "code": error.code, "message": str(error)}
                with self._state_lock:
                    self._pending[env_id] -= 1
                    job["results"].append(result)
                    self._activity.append({"id": uuid.uuid4().hex, "at": now(), "environment_id": env_id,
                                           "name": env.name, "action": job["action"],
                                           "level": "error" if result["status"] == "failed" else "info",
                                           "message": result.get("message", "操作完成")})
                    self._activity = self._activity[-50:]
            with self._state_lock:
                failed = sum(item["status"] == "failed" for item in job["results"])
                job["status"] = "failed" if failed == len(job["ids"]) else "partial" if failed else "complete"
                job["finished_at"] = now()
                if self._close_all_job_id == job["id"]:
                    self._close_all_job_id = None
                self._jobs = [item for item in self._jobs if item["status"] in {"queued", "running"}] + [
                    item for item in self._jobs if item["status"] not in {"queued", "running"}][-20:]
                self._inventory_at = 0
            self._queue.task_done()

    def _idle_checkpoint(self) -> None:
        """One lock-aware checkpoint only when explicit actions are absent."""
        current = time.monotonic()
        with self._state_lock:
            if self._closed or not self._session_autosave["enabled"] or not self._queue.empty():
                return
            revision = self._session_autosave_revision
            due = sorted((due_at, key) for key, due_at in self._next_cookie_sync.items()
                         if due_at <= current and self._is_active(key))
        if not due:
            return
        self._refresh()
        _, env_id = due[0]
        # The settings endpoint can close admission immediately, then wait for
        # this short checkpoint to release its profile lock before responding.
        with self._autosave_action_lock:
            with self._state_lock:
                if not self._automatic_save_allowed(env_id, revision):
                    return
                self._schedule_cookie_sync(env_id)
                env = self.environments[env_id]
            if profile_busy(env.root):
                return
            with self._state_lock:
                observed = self._observations.get(env_id, {})
                ready = (not self._inventory_error and observed.get("cdp") == "connected"
                         and not self._pending.get(env_id))
                if not ready or not self._automatic_save_allowed(env_id, revision):
                    return
                self._checkpoint_env = env_id
            try:
                asyncio.run(self._browser_action("save-session", env, automatic=True,
                                                 checkpoint_revision=revision))
            except Exception as exc:
                if (getattr(exc, "code", "") == "profile_locked"
                        or not self._automatic_save_allowed(env_id, revision)):
                    return
                with self._state_lock:
                    self._activity.append({"id": uuid.uuid4().hex, "at": now(), "environment_id": env_id,
                                           "name": env.name, "action": "save-session", "level": "error",
                                           "message": "会话自动保存未完成，可在环境详情中重试保存"})
                    self._activity = self._activity[-50:]
            finally:
                with self._state_lock:
                    self._checkpoint_env = None

    @staticmethod
    def _friendly_error(exc: Exception) -> BrowserServiceError:
        if isinstance(exc, BrowserServiceError):
            return exc
        code = getattr(exc, "code", "browser_error")
        messages = {
            "profile_locked": "此浏览器正在执行其他任务，请完成后再操作",
            "login_required": "需要在浏览器中完成人工登录",
            "browser_disconnected": "浏览器未运行或连接尚未就绪",
            "page_missing": "未找到业务页面，请先打开该浏览器环境",
            "context_changed": "浏览器归属与配置不一致，已停止操作",
            "browser_identity_unverified": "无法确认浏览器归属，已停止操作",
            "dependency_missing": "缺少 Playwright 运行依赖",
            "configuration": "浏览器配置无效，请检查配置文件",
            "cookie_sync_failed": "会话未完整保存，浏览器保持运行，请重试保存",
        }
        return BrowserServiceError(messages.get(code, "浏览器操作失败，请稍后重试或检查该环境"), code)

    def _perform(self, action: str, env: Environment) -> dict[str, Any]:
        if action in {"open-folder", "open-results"}:
            target = env.root if action == "open-folder" else self.output_root
            if not target.is_dir():
                raise BrowserServiceError("目录尚不存在", "folder_missing")
            if os.name != "nt":
                raise BrowserServiceError("文件夹操作目前需要 Windows", "platform_unsupported")
            os.startfile(str(target))
            return {"message": "已打开浏览器数据目录" if action == "open-folder" else "已打开结果目录"}
        if action == "maintain-session":
            async def bounded_maintenance() -> dict[str, Any]:
                try:
                    budget = 90 + self.login_check_settings(env.id)["wait_seconds"]
                    return await asyncio.wait_for(self._maintain_browser(env), timeout=budget)
                except asyncio.TimeoutError as exc:
                    self._set_auth(env.id, "error", "本环境维护超时，未能判断登录状态")
                    raise BrowserServiceError("本环境维护超时，保留浏览器并继续其他环境", "maintenance_timeout") from exc
            return asyncio.run(bounded_maintenance())
        return asyncio.run(self._browser_action(action, env))

    async def _maintain_browser(self, env: Environment) -> dict[str, Any]:
        return await self._run_login_check(env, allow_launch=True)

    async def _run_login_check(self, env: Environment, *, allow_launch: bool) -> dict[str, Any]:
        """One navigation/check/save path for opening, checking and maintenance."""
        self._set_auth(env.id, "unchecked", "正在访问主页并检查登录")
        if not allow_launch:
            try:
                self._require_owner(env)
            except BrowserServiceError as exc:
                if exc.code == "browser_stopped":
                    auth = self._set_auth(env.id, "unchecked", "浏览器未运行，未检查登录")
                    return {"message": auth["message"], "auth": auth}
                self._set_auth(env.id, "error", self._friendly_error(exc).message)
                raise
        controller = PlaywrightBrowserController(env.config, timeout_ms=15000, login_probe_mode=True)
        try:
            if allow_launch:
                await controller.start(open_missing=False)
            else:
                await controller.connect(open_missing=False)
            fingerprint = self._process_key(self._require_owner(env))
            self._adopt_process(env.id, fingerprint)
            previous = self._probe_targets.get(env.id, {})
            if previous.get("fingerprint") != fingerprint:
                previous = {}
            page, target_id = await controller.probe_page(
                env.home_role, previous_target_id=previous.get("target_id"), previous_url=previous.get("url"),
            )
            try:
                auth = await self._check_auth(env, page)
            finally:
                self._probe_targets[env.id] = {"fingerprint": fingerprint, "target_id": target_id, "url": str(page.url)}
            self._require_owner(env, expected=fingerprint)
            result = {"message": auth["message"], "auth": auth}
            if auth["status"] == "error":
                result.update(status="failed", code="login_check_failed")
            if auth["status"] in {"verified", "assumed"}:
                saved = await self._save_session(env, controller.browser)
                self._require_saved(saved)
                self._clear_maintenance_login_block(env.id)
                result.update(message=auth["message"] + "；会话已保存", cookie_sync=saved)
            return result
        except asyncio.CancelledError:
            self._set_auth(env.id, "error", "本次检查被取消或超时，未能判断登录状态")
            raise
        except Exception as exc:
            if getattr(exc, "code", "") != "cookie_sync_failed":
                self._set_auth(env.id, "error", self._friendly_error(exc).message)
            raise
        finally:
            await controller.close()

    def _require_owner(self, env: Environment, *, expected: tuple[Any, ...] | None = None) -> dict[str, Any]:
        inventory = enumerate_browser_processes()
        record, conflict = self._owned_process(env, inventory)
        if conflict:
            raise BrowserServiceError("浏览器进程或端口属于其他环境，已停止操作", "ownership_conflict")
        if record is None:
            raise BrowserServiceError("浏览器尚未运行，请先启动", "browser_stopped")
        if not record.get("started_at"):
            raise BrowserServiceError("无法核对浏览器进程身份，已停止操作", "ownership_unverified")
        if expected is not None and self._process_key(record) != expected:
            raise BrowserServiceError("浏览器进程已变化，请重新操作", "ownership_conflict")
        return record

    def _set_auth(self, env_id: str, status: str, message: str, identity: str = "") -> dict[str, Any]:
        value = {"status": status, "checked_at": now(), "message": message, "identity": identity}
        with self._state_lock:
            self._auth[env_id] = value
            if status == "required" and env_id not in self._maintenance_required:
                self._maintenance_required.add(env_id)
                self._persist_maintenance()
        return value

    def _adopt_process(self, env_id: str, fingerprint: tuple[Any, ...] | None) -> None:
        with self._state_lock:
            if self._process_keys.get(env_id) != fingerprint:
                self._auth[env_id] = self._unchecked("浏览器进程已变化，正在重新检查登录")
            self._process_keys[env_id] = fingerprint

    async def _save_session(self, env: Environment, browser: Any, *, automatic: bool = False,
                            checkpoint_revision: int | None = None) -> dict[str, Any]:
        with self._state_lock:
            revision = self._session_autosave_revision if checkpoint_revision is None else checkpoint_revision
            if automatic and not self._automatic_save_allowed(env.id, revision):
                return deepcopy(self._cookie_sync[env.id])
        urls = [value["url"] if isinstance(value, dict) else value for value in env.config["browser_sessions"].values()]
        families, hosts = business_cookie_scope(env.kind, urls)
        result = await persist_session_cookies(
            browser, families, hosts,
            should_continue=(lambda: self._automatic_save_allowed(env.id, revision)) if automatic else None,
        )
        with self._state_lock:
            if not result["last_saved_at"]:
                result["last_saved_at"] = self._cookie_sync[env.id]["last_saved_at"]
        try:
            write_cookie_metadata(env.root, result)
        except OSError:
            result["status"] = "error"
            result["message"] = "无法写入会话保存记录，浏览器保持运行，请检查数据目录后重试"
        with self._state_lock:
            self._cookie_sync[env.id] = result
            # An update made during the CDP request already reset its deadline.
            if revision == self._session_autosave_revision:
                self._schedule_cookie_sync(env.id)
        return deepcopy(result)

    @staticmethod
    def _require_saved(result: dict[str, Any]) -> None:
        if result["status"] != "saved":
            raise BrowserServiceError(result["message"], "cookie_sync_failed")

    async def _browser_action(self, action: str, env: Environment, *, automatic: bool = False,
                              checkpoint_revision: int | None = None) -> dict[str, Any]:
        with self._state_lock:
            revision = self._session_autosave_revision if checkpoint_revision is None else checkpoint_revision
            if automatic and not self._automatic_save_allowed(env.id, revision):
                return {"message": "自动保存已停止或让位于排队操作"}
        if action in {"start", "check-login"}:
            return await self._run_login_check(env, allow_launch=action == "start")

        lock = ProfileLock(env.root, env.config["profile_directory"])
        lock.acquire()
        try:
            if automatic and not self._automatic_save_allowed(env.id, revision):
                return {"message": "自动保存已停止或让位于排队操作"}
            try:
                record = self._require_owner(env)
            except BrowserServiceError as exc:
                if action == "check-login" and exc.code == "browser_stopped":
                    self._set_auth(env.id, "unchecked", "浏览器未运行，未检查登录")
                    return {"message": "浏览器未运行，未检查登录"}
                if action == "close" and exc.code == "browser_stopped":
                    self._set_auth(env.id, "unchecked", "浏览器已关闭")
                    return {"message": "此浏览器环境已关闭，无需重复关闭"}
                raise
            fingerprint = self._process_key(record)
            if action == "focus":
                return await self._focus_browser(env, record, fingerprint)
            if automatic and not self._automatic_save_allowed(env.id, revision):
                return {"message": "自动保存已停止或让位于排队操作"}
            from playwright.async_api import async_playwright
            runtime = await asyncio.wait_for(async_playwright().start(), timeout=8) if automatic else await async_playwright().start()
            browser = None
            try:
                if automatic and not self._automatic_save_allowed(env.id, revision):
                    return {"message": "自动保存已停止或让位于排队操作"}
                browser = await runtime.chromium.connect_over_cdp(env.endpoint, timeout=8000)
                if automatic and not self._automatic_save_allowed(env.id, revision):
                    return {"message": "自动保存已停止或让位于排队操作"}
                if action in {"close", "save-session"}:
                    self._require_owner(env, expected=fingerprint)
                    if automatic and not self._automatic_save_allowed(env.id, revision):
                        return {"message": "自动保存已停止或让位于排队操作"}
                    saved = await self._save_session(env, browser, automatic=automatic,
                                                     checkpoint_revision=revision)
                    self._require_saved(saved)
                    if action == "save-session":
                        return {"message": saved["message"], "cookie_sync": saved}
                    self._require_owner(env, expected=fingerprint)
                    session = await browser.new_browser_cdp_session()
                    try:
                        await asyncio.wait_for(session.send("Browser.close"), timeout=5)
                    except Exception:
                        # Browser.close may disconnect before returning its
                        # response. Process exit, not send success, decides.
                        pass
                    await self._wait_for_exit(fingerprint)
                    self._set_auth(env.id, "unchecked", "浏览器已关闭，登录状态将在下次启动后检查")
                    return {"message": "会话已保存，此浏览器环境已正常关闭", "cookie_sync": saved}
                raise BrowserServiceError("不支持的浏览器操作", "invalid_action")
            finally:
                await self._detach(runtime, browser)
        finally:
            lock.release()

    @staticmethod
    async def _detach(runtime: Any, browser: Any) -> None:
        try:
            if browser is not None:
                await asyncio.wait_for(browser.close(), timeout=5)
        except Exception:
            pass
        finally:
            try:
                await asyncio.wait_for(runtime.stop(), timeout=5)
            except Exception:
                pass

    async def _wait_for_exit(self, fingerprint: tuple[Any, ...] | None) -> None:
        deadline = time.monotonic() + 10
        while True:
            inventory = await asyncio.to_thread(enumerate_browser_processes)
            if not any(self._process_key(record) == fingerprint for record in inventory):
                return
            if time.monotonic() >= deadline:
                raise BrowserServiceError("浏览器尚未退出，可能有确认对话框；未执行强制关闭", "close_pending")
            await asyncio.sleep(0.3)

    async def _focus_browser(self, env: Environment, record: dict[str, Any],
                             fingerprint: tuple[Any, ...] | None) -> dict[str, Any]:
        title = ""
        runtime = browser = None
        try:
            from playwright.async_api import async_playwright
            runtime = await async_playwright().start()
            browser = await runtime.chromium.connect_over_cdp(env.endpoint, timeout=5000)
            if browser.contexts:
                page = self._business_page(env, browser.contexts[0].pages)
                await page.bring_to_front()
                title = await page.title()
        except Exception:
            # Focusing a verified native window remains possible when its
            # business page or CDP connection is temporarily unavailable.
            pass
        finally:
            if runtime is not None:
                await self._detach(runtime, browser)
        self._require_owner(env, expected=fingerprint)
        focus_window(record["pid"], title)
        return {"message": "已切换到该浏览器窗口"}

    @staticmethod
    def _business_page(env: Environment, pages: list[Any]) -> Any:
        roles = env.config["browser_sessions"]
        preferred = env.home_role
        value = roles[preferred]
        expected_url = value["url"] if isinstance(value, dict) else value
        open_pages = [page for page in pages if not page.is_closed()]
        page = next((page for page in open_pages if _same_role_page(page.url, expected_url)), None)
        if page is None and env.auth_adapter != "generic":
            page = next((page for page in open_pages if _url_is_login(page.url)
                         and _same_site_family(page.url, expected_url)), None)
        if page is None:
            urls = [value["url"] if isinstance(value, dict) else value for value in roles.values()]
            page = next((page for page in open_pages if any(_same_role_page(page.url, url) for url in urls)), None)
        if page is None:
            raise BrowserServiceError("业务页尚未打开，请先启动此环境以恢复固定页面", "page_missing")
        return page

    async def _check_auth(self, env: Environment, page: Any) -> dict[str, Any]:
        settings = self.login_check_settings(env.id)
        value = await probe_login_url(page, env.home_url, settings)
        if settings["mode"] == "url" or value["status"] != "assumed":
            return self._set_auth(env.id, value["status"], value["message"])
        if env.auth_adapter == "generic":
            return self._set_auth(env.id, "error", "此主页没有专用平台检查，请改用通用 URL 检查")
        return await self._platform_auth(env, page)

    async def _platform_auth(self, env: Environment, page: Any) -> dict[str, Any]:
        if env.auth_adapter == "generic":
            return self._set_auth(env.id, "unchecked", "此网站暂不支持自动判断登录状态，请在浏览器中自行确认")
        if _url_is_login(page.url):
            return self._set_auth(env.id, "required", "需要在浏览器中完成人工登录")
        try:
            if env.auth_adapter == "qianniu":
                value = await asyncio.wait_for(page.evaluate((VENDOR / "check_qianniu.js").read_text(encoding="utf-8")), timeout=15)
                actual = str(value.get("store") or "").strip()
                if value.get("required"):
                    return self._set_auth(env.id, "required", "需要在浏览器中完成登录或验证")
                if value.get("verified") is not True:
                    return self._set_auth(env.id, "error", "未取得千牛当前会话的登录证据")
                return self._set_auth(env.id, "verified", "已确认千牛登录成功", actual)
            deadline = time.monotonic() + JST_PAGE_READY_SECONDS
            while True:
                if _url_is_login(page.url):
                    return self._set_auth(env.id, "required", "票聚登录失效，请人工登录")
                frame = next((frame for frame in page.frames if GOODS_FRAME_URL in str(frame.url)), None)
                if frame is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._set_auth(env.id, "error", "票聚页面尚未加载完成，请稍后重新检查登录")
                # Wait for the business frame only. The session check API
                # below is executed once after the page is actually ready.
                await asyncio.sleep(min(0.25, remaining))
            tenant = await asyncio.wait_for(frame.evaluate((VENDOR / "check_jst.js").read_text(encoding="utf-8")), timeout=8)
            if tenant.get("required"):
                return self._set_auth(env.id, "required", "票聚登录失效，请人工登录")
            if tenant.get("verified") is not True:
                return self._set_auth(env.id, "error", "未取得票聚当前会话的登录证据")
            actual = ""
            try:
                label = await asyncio.wait_for(page.evaluate("""() => {
                  const names = [...document.querySelectorAll('.companyInfoBox__name')]
                    .map(e => (e.textContent || '').trim()).filter(Boolean);
                  return names.length === 1 ? names[0] : '';
                }"""), timeout=1)
                actual = str(label or "").rsplit("[", 1)[0].strip()
            except Exception:
                pass  # Optional display text is never authentication evidence.
            if _url_is_login(page.url):
                return self._set_auth(env.id, "required", "票聚登录失效，请人工登录")
            return self._set_auth(env.id, "verified", "已确认票聚登录成功且接口可用", actual)
        except Exception:
            return self._set_auth(env.id, "error", "登录检查未完成，请确认业务页面已加载后重试")

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop admitting work and drain accepted actions without closing browsers."""
        with self._state_lock:
            if not self._closed:
                self._closed = True
                self._queue.put(None)
        if wait:
            if threading.current_thread() is self._worker:
                raise RuntimeError("The browser worker cannot wait for itself")
            self._worker.join()
            # A shop creation may already be writing its files outside the
            # state lock. Let that accepted operation finish before exit.
            with self._creation_lock:
                pass
