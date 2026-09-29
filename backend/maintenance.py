"""Small, durable schedule for local browser session maintenance."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any


FILE_NAME = ".browser-workbench-maintenance.json"
INTERVALS = {30, 60, 120, 240, 720, 1440}
SETTING_KEYS = {"enabled", "interval_minutes"}
STATUSES = {"idle", "running", "complete", "partial", "failed", "paused"}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def later(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def valid_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.isoformat()


def validate_settings(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) not in (SETTING_KEYS, SETTING_KEYS | {"include_shared"})
            or type(value["enabled"]) is not bool
            or ("include_shared" in value and type(value["include_shared"]) is not bool)
            or type(value["interval_minutes"]) is not int or value["interval_minutes"] not in INTERVALS):
        raise ValueError("invalid maintenance settings")
    # Older clients/settings may still send this flag. Maintenance now always
    # covers every registered environment, so do not expose or persist it.
    return {key: value[key] for key in SETTING_KEYS}


def initial_state() -> dict[str, Any]:
    return {"enabled": False, "interval_minutes": 120,
            "next_run_at": None, "running": False, "last_run_at": None, "last_finished_at": None,
            "last_status": "idle", "last_results": [], "message": "定时维护尚未开启"}


def load(path: Path, names: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
    state = initial_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("invalid maintenance state")
        settings = {key: data[key] for key in SETTING_KEYS}
        if "include_shared" in data:
            settings["include_shared"] = data["include_shared"]
        state.update(validate_settings(settings))
        for key in ("next_run_at", "last_run_at", "last_finished_at"):
            state[key] = valid_timestamp(data.get(key))
        status = data.get("last_status")
        if status not in STATUSES:
            raise ValueError("invalid maintenance status")
        state["last_status"] = status
        results = data.get("last_results", [])
        if not isinstance(results, list):
            raise ValueError("invalid maintenance results")
        for result in results[:len(names)]:
            if (not isinstance(result, dict) or result.get("id") not in names
                    or result.get("status") not in {"complete", "skipped", "failed"}):
                continue
            cleaned = {"id": result["id"], "name": names[result["id"]], "status": result["status"],
                       "message": str(result.get("message") or "")[:300]}
            if isinstance(result.get("code"), str):
                cleaned["code"] = result["code"][:80]
            state["last_results"].append(cleaned)
        blocked = data.get("login_required_ids", [])
        if not isinstance(blocked, list):
            raise ValueError("invalid login markers")
        required = {value for value in blocked if isinstance(value, str) and value in names}
        state["message"] = "已恢复维护设置及上次结果" if state["enabled"] else "定时维护已暂停，可手动维护一次"
        if data.get("running") or status == "running":
            state["last_status"] = "partial"
            state["message"] = "上次维护因服务停止而中断；后续按当前计划执行"
        if state["enabled"] and state["next_run_at"] is None:
            state["next_run_at"] = later(state["interval_minutes"])
        if not state["enabled"]:
            state["next_run_at"] = None
        return state, required
    except FileNotFoundError:
        return state, set()
    except (OSError, ValueError, TypeError, KeyError):
        state = initial_state()
        state["message"] = "维护记录无法读取，定时维护已停止；请重新保存设置"
        return state, set()


def save(path: Path, state: dict[str, Any], required: set[str]) -> None:
    data = {"schema_version": 1, **deepcopy(state), "login_required_ids": sorted(required)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".maintenance-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
