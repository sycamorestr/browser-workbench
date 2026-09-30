"""Durable settings for idle session-cookie saves, separate from visits."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


FILE_NAME = ".browser-workbench-session-autosave.json"
INTERVALS = {1, 5, 10, 30, 60}
SETTING_KEYS = {"enabled", "interval_minutes"}
DEFAULTS = {"enabled": True, "interval_minutes": 5}


def validate_settings(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != SETTING_KEYS
            or type(value["enabled"]) is not bool
            or type(value["interval_minutes"]) is not int
            or value["interval_minutes"] not in INTERVALS):
        raise ValueError("invalid session autosave settings")
    return {key: value[key] for key in SETTING_KEYS}


def load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != SETTING_KEYS | {"schema_version"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1):
            raise ValueError("invalid session autosave file")
        return validate_settings({key: data[key] for key in SETTING_KEYS})
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError, TypeError, KeyError):
        return {**DEFAULTS, "enabled": False,
                "message": "自动保存设置无法读取，已停止自动保存；请重新保存设置"}


def save(path: Path, settings: dict[str, Any]) -> None:
    data = {"schema_version": 1, **validate_settings(settings)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".session-autosave-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
