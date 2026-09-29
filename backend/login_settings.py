"""User-owned login checks kept apart from imported browser configurations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .shop_registry import ShopRegistryError, validate_home_url


FILE_NAME = ".browser-workbench-login-checks.json"
DEFAULTS = {"mode": "url", "login_url": "", "wait_seconds": 5}


class LoginSettingsError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_settings(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != set(DEFAULTS)
            or not isinstance(value["mode"], str) or value["mode"] not in {"url", "platform"}
            or type(value["wait_seconds"]) is not int or not 1 <= value["wait_seconds"] <= 30
            or not isinstance(value["login_url"], str)):
        raise LoginSettingsError("invalid_login_check", "登录检查方式无效，等待时间须为 1 至 30 秒的整数")
    raw_url = value["login_url"]
    # An empty value deliberately selects the generic built-in URL markers.
    if raw_url.strip() or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in raw_url):
        try:
            login_url = validate_home_url(raw_url)
        except ShopRegistryError as exc:
            raise LoginSettingsError("invalid_login_url", "请填写有效的 HTTP 或 HTTPS 登录页短地址，不包含账号密码、空白或控制字符；也可留空使用通用规则") from exc
    else:
        login_url = ""
    return {"mode": value["mode"], "login_url": login_url, "wait_seconds": value["wait_seconds"]}


def _validated_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError("invalid login checks document")
    result = {}
    for env_id, settings in value.items():
        if not isinstance(env_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", env_id):
            raise ValueError("invalid environment identifier")
        result[env_id] = validate_settings(settings)
    return result


def load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or value.get("schema_version") != 1 or set(value) != {"schema_version", "environments"}:
            raise ValueError("invalid login checks schema")
        return _validated_map(value["environments"])
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, LoginSettingsError) as exc:
        raise LoginSettingsError("login_settings_storage_failed", "登录检查设置无法读取，请检查独立的登录检查设置文件") from exc


def save(path: Path, settings_map: dict[str, dict[str, Any]]) -> None:
    data = {"schema_version": 1, "environments": _validated_map(settings_map)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".login-check-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise LoginSettingsError("login_settings_storage_failed", "登录检查设置无法保存，请检查文件夹权限后重试") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
