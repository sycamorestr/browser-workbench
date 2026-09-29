"""Archive and remove registrations while preserving every browser data file."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any

from .shop_registry import _atomic_write_json
from .vendor.browser_lock import FileMutex, FileMutexBusy


FILE_NAME = ".browser-workbench-lifecycle.json"
LOCK_NAME = ".browser-workbench-lifecycle.lock"
LOCK_WAIT_SECONDS = 5.0
_ID = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_RESOURCE_FIELDS = {"id", "name", "user_data_dir", "download_dir", "debug_port", "config_path"}


class LifecycleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _resource(value: Any, env_id: str) -> dict[str, Any]:
    if (not isinstance(value, dict) or not _RESOURCE_FIELDS.issubset(value)
            or set(value) - (_RESOURCE_FIELDS | {"login_username", "home_url"})
            or value["id"] != env_id or not isinstance(value["name"], str) or not value["name"].strip()
            or type(value["debug_port"]) is not int or not 1 <= value["debug_port"] <= 65535):
        raise ValueError("invalid environment resource reservation")
    for field in ("user_data_dir", "download_dir", "config_path"):
        if not isinstance(value[field], str) or not Path(value[field]).is_absolute():
            raise ValueError("environment resource path must be absolute")
    if any(not isinstance(value[key], str) for key in ("login_username", "home_url") if key in value):
        raise ValueError("invalid environment metadata")
    return deepcopy(value)


def _records(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError("invalid lifecycle records")
    result = {}
    for env_id, item in value.items():
        if (not isinstance(env_id, str) or not _ID.fullmatch(env_id) or not isinstance(item, dict)
                or set(item) != {"state", "changed_at", "resource"}
                or item["state"] not in ("archived", "deleted") or not isinstance(item["changed_at"], str)
                or datetime.fromisoformat(item["changed_at"]).tzinfo is None):
            raise ValueError("invalid lifecycle record")
        result[env_id] = {"state": item["state"], "changed_at": item["changed_at"],
                          "resource": _resource(item["resource"], env_id)}
    return result


def load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        if (not isinstance(document, dict) or set(document) != {"schema_version", "environments"}
                or document["schema_version"] != 1):
            raise ValueError("invalid lifecycle document")
        return _records(document["environments"])
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        raise LifecycleError("lifecycle_storage_failed", "环境归档记录无法读取，请检查本地归档登记文件") from exc


def save_state(path: Path, env_id: str, state: str, resource: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(env_id, str) or not _ID.fullmatch(env_id) or state not in ("active", "archived", "deleted"):
        raise LifecycleError("invalid_lifecycle", "环境归档操作无效")
    try:
        reserved = _resource(resource, env_id)
    except (ValueError, TypeError) as exc:
        raise LifecycleError("invalid_lifecycle", "环境数据记录不完整，无法归档或删除登记") from exc
    mutex = FileMutex(path.parent / LOCK_NAME)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            mutex.acquire()
            break
        except FileMutexBusy as exc:
            if time.monotonic() >= deadline:
                raise LifecycleError("lifecycle_busy", "其他操作正在更新环境归档，请稍后重试") from exc
            time.sleep(0.05)
        except OSError as exc:
            raise LifecycleError("lifecycle_storage_failed", "环境归档登记无法锁定，请检查文件夹权限") from exc
    try:
        records = load(path)
        if state == "active":
            records.pop(env_id, None)
        else:
            records[env_id] = {"state": state, "changed_at": datetime.now(timezone.utc).isoformat(), "resource": reserved}
        _atomic_write_json(path, {"schema_version": 1, "environments": records})
        return records
    except OSError as exc:
        raise LifecycleError("lifecycle_storage_failed", "环境归档登记保存失败，请检查文件夹权限后重试") from exc
    finally:
        mutex.release()
