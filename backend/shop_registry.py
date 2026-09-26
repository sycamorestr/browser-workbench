"""Create isolated browser environments without editing the source registry."""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import re
import socket
import time
import unicodedata
import uuid
from typing import Any

from .vendor.browser_lock import FileMutex, FileMutexBusy


MANIFEST_NAME = ".browser-workbench-shops.json"
LOCK_NAME = ".browser-workbench-shops.lock"
HOME_URL = "https://myseller.taobao.com/"
FIRST_PORT = 9401
LOCK_WAIT_SECONDS = 5.0
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$", re.I)


class ShopRegistryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _name_key(value: str) -> str:
    return unicodedata.normalize("NFKC", " ".join(value.split())).casefold()


def _path_key(value: Path) -> str:
    return os.path.normcase(str(value.resolve())).rstrip("\\/")


def _overlap(left: Path, right: Path) -> bool:
    a, b = _path_key(left), _path_key(right)
    try:
        return os.path.commonpath([a, b]) in {a, b}
    except ValueError:
        return False


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def _manifest_path(registry_path: Path) -> Path:
    return Path(registry_path).resolve().parent / MANIFEST_NAME


def _read_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "shops": []}
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ShopRegistryError("invalid_custom_registry", "自定义店铺登记文件无法读取，请检查本地文件") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("shops"), list):
        raise ShopRegistryError("invalid_custom_registry", "自定义店铺登记文件格式不正确")
    return document


def _records(document: dict[str, Any], base: Path) -> list[dict[str, Any]]:
    result = []
    ids, names, configs = set(), set(), set()
    for item in document["shops"]:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "store", "browser_config")):
            raise ShopRegistryError("invalid_custom_registry", "自定义店铺记录缺少有效的名称、标识或配置路径")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item["id"]) or len(item["store"].strip()) > 80:
            raise ShopRegistryError("invalid_custom_registry", "自定义店铺名称或标识格式不正确")
        username = item.get("login_username", "")
        if not isinstance(username, str) or len(username) > 160:
            raise ShopRegistryError("invalid_custom_registry", "自定义店铺登录用户名格式不正确")
        config = _resolve_path(item["browser_config"], base)
        keys = (item["id"].casefold(), _name_key(item["store"]), _path_key(config))
        if keys[0] in ids or keys[1] in names or keys[2] in configs:
            raise ShopRegistryError("invalid_custom_registry", "自定义店铺登记文件存在重复记录")
        ids.add(keys[0]); names.add(keys[1]); configs.add(keys[2])
        result.append({"id": item["id"], "store": item["store"], "login_username": username,
                       "browser_config": str(config)})
    return result


def load_custom_shops(registry_path: Path) -> list[dict[str, Any]]:
    """Read the sidecar manifest; relative config paths use registry.parent."""
    path = _manifest_path(registry_path)
    return _records(_read_document(path), path.parent)


def _validate_payload(payload: dict[str, Any]) -> tuple[str, str, Path]:
    if not isinstance(payload, dict) or set(payload) - {"name", "parent_folder", "login_username"}:
        raise ShopRegistryError("invalid_shop", "新建店铺只接受名称、父文件夹和登录用户名")
    name = payload.get("name")
    username = payload.get("login_username", "")
    folder = payload.get("parent_folder")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or any(ord(c) < 32 for c in name):
        raise ShopRegistryError("invalid_shop_name", "店铺名称不能为空，且不能超过 80 个字符或包含控制字符")
    if not isinstance(username, str) or len(username.strip()) > 160 or any(ord(c) < 32 for c in username):
        raise ShopRegistryError("invalid_login_username", "登录用户名不能超过 160 个字符或包含控制字符")
    if not isinstance(folder, str) or not folder or any(ord(c) < 32 for c in folder):
        raise ShopRegistryError("invalid_parent_folder", "请选择本机已有的绝对路径文件夹")
    windows = PureWindowsPath(folder)
    if folder.startswith(("\\\\", "//")) or windows.drive.startswith("\\"):
        raise ShopRegistryError("invalid_parent_folder", "请选择本机磁盘文件夹，不支持网络共享或设备路径")
    if os.name == "nt" or windows.drive:
        if not windows.is_absolute() or not re.fullmatch(r"[A-Za-z]:", windows.drive):
            raise ShopRegistryError("invalid_parent_folder", "文件夹必须是本机磁盘的绝对路径")
        parts = windows.parts[1:]
    else:
        if not Path(folder).is_absolute():
            raise ShopRegistryError("invalid_parent_folder", "文件夹必须是绝对路径")
        parts = Path(folder).parts[1:]
    if any(part in {".", ".."} or part.endswith((" ", ".")) or _RESERVED.fullmatch(part)
           or re.search(r'[<>:"|?*]', part) for part in parts):
        raise ShopRegistryError("invalid_parent_folder", "文件夹路径包含保留名称或无效字符")
    try:
        parent = Path(folder).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ShopRegistryError("invalid_parent_folder", "所选文件夹不存在或不可访问") from exc
    if not parent.is_dir():
        raise ShopRegistryError("invalid_parent_folder", "请选择已有文件夹，不能选择文件")
    # resolve() follows junctions and symlinks before any overlap checks.
    if os.name == "nt" and str(parent).startswith("\\\\"):
        raise ShopRegistryError("invalid_parent_folder", "所选文件夹不能指向网络共享")
    if os.name == "nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(parent.anchor)) == 4:
            raise ShopRegistryError("invalid_parent_folder", "请选择本机磁盘，不支持映射的网络驱动器")
    return name.strip(), username.strip(), parent


def _custom_resources(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for record in records:
        config_path = Path(record["browser_config"])
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(config, dict) or config.get("schema_version") != 1:
                raise ValueError("invalid schema")
            port = config["remote_debugging_port"]
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError("invalid port")
            data = config["user_data_dir"]
            download = config["download_dir"]
            if not isinstance(data, str) or not data or not isinstance(download, str) or not download:
                raise ValueError("invalid paths")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ShopRegistryError("invalid_custom_registry", "已登记的自定义浏览器配置缺失或无效，请先修复该记录") from exc
        result.append({"id": record["id"], "name": record["store"], "debug_port": port,
                       "user_data_dir": str(_resolve_path(data, config_path.parent)),
                       "download_dir": str(_resolve_path(download, config_path.parent)),
                       "config_path": str(config_path)})
    return result


def _reserve_debug_port(excluded: set[int]) -> tuple[int, socket.socket]:
    for port in range(FIRST_PORT, 65536):
        if port in excluded:
            continue
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            reservation.bind(("127.0.0.1", port))
            return port, reservation
        except OSError:
            reservation.close()
    raise ShopRegistryError("no_debug_port", "没有可用的本机浏览器调试端口")


def _atomic_write_json(path: Path, value: Any, *, replace_existing: bool = True) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if replace_existing:
            os.replace(temp, path)
        elif os.name == "nt":
            # Windows rename fails when the destination exists. The profile
            # config must never overwrite even an unexpected new file.
            os.rename(temp, path)
        else:
            # POSIX rename replaces existing paths; hard-link publication is
            # the atomic no-overwrite equivalent within the same directory.
            os.link(temp, path)
    finally:
        # This temporary path was generated exclusively for this write.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def create_shop(registry_path: Path, existing: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """Create one fresh profile and append it under a cross-process lock."""
    name, username, parent = _validate_payload(payload)
    manifest = _manifest_path(registry_path)
    if not manifest.parent.is_dir():
        raise ShopRegistryError("invalid_custom_registry", "原店铺登记文件所在目录不存在")
    mutex = FileMutex(manifest.parent / LOCK_NAME)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            mutex.acquire()
            break
        except FileMutexBusy as exc:
            if time.monotonic() >= deadline:
                raise ShopRegistryError("registry_busy", "其他进程正在创建店铺环境，请稍后重试") from exc
            time.sleep(0.05)
        except OSError as exc:
            raise ShopRegistryError("registry_write_failed", "无法锁定自定义店铺登记文件") from exc
    created_dirs: list[Path] = []
    reservation = None
    committed = False
    try:
        document = _read_document(manifest)
        records = _records(document, manifest.parent)
        resources = list(existing) + _custom_resources(records)
        if any(_name_key(str(item.get("name", item.get("store", "")))) == _name_key(name) for item in resources):
            raise ShopRegistryError("duplicate_shop_name", "该店铺名称已存在，请使用不同名称")
        ids = {str(item.get("id", "")).casefold() for item in resources}
        protected = [Path(str(item[key])).resolve() for item in resources for key in ("user_data_dir", "download_dir") if item.get(key)]
        existing_configs = {_path_key(Path(str(item["config_path"]))) for item in resources if item.get("config_path")}
        for _ in range(20):
            env_id = "shop-" + uuid.uuid4().hex[:8]
            root = parent / env_id
            if env_id.casefold() in ids or _path_key(root / "browser.json") in existing_configs:
                continue
            if any(_overlap(root, item) for item in protected):
                raise ShopRegistryError("folder_conflict", "新环境不能放在已有浏览器数据或下载目录中，也不能与它们嵌套")
            try:
                root.mkdir()
                created_dirs.append(root)
                break
            except FileExistsError:
                continue
        else:
            raise ShopRegistryError("folder_conflict", "无法分配独立的新环境目录，请重试")
        profile, downloads = root / "profile", root / "downloads"
        for directory in (profile, downloads):
            directory.mkdir()
            created_dirs.append(directory)
        excluded = set()
        for item in resources:
            try:
                excluded.add(int(item.get("debug_port")))
            except (TypeError, ValueError):
                pass
        port, reservation = _reserve_debug_port(excluded)
        config_path = root / "browser.json"
        config = {"schema_version": 1, "browser": "Edge", "user_data_dir": str(profile),
                  "profile_directory": "Default", "remote_debugging_port": port,
                  "download_dir": str(downloads), "browser_sessions": {"home": {"url": HOME_URL}}}
        _atomic_write_json(config_path, config, replace_existing=False)
        record = {"id": env_id, "store": name, "login_username": username,
                  "browser_config": str(config_path)}
        document["shops"].append(record)
        _atomic_write_json(manifest, document)
        committed = True
        return dict(record)
    except ShopRegistryError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ShopRegistryError("registry_write_failed", "新环境创建失败；已登记的店铺保持不变") from exc
    finally:
        if reservation is not None:
            reservation.close()
        # Never recursively delete. A successfully written config or any
        # unexpected user file keeps its new root intact after a failed commit.
        if not committed:
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        mutex.release()
