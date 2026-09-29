"""Conservative, site-independent login checks based on a fresh navigation."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import unquote, urlsplit


NAVIGATION_TIMEOUT_MS = 15_000
DOM_CHECK_TIMEOUT_SECONDS = 5
DEFAULT_WAIT_SECONDS = 5
LOGIN_MARKERS = re.compile(r"(?:^|[./_!\-])(login|signin|sign-in|sign_in|logon|passport)(?:$|[./_!\-])", re.I)


def _generic_login_url(url: str) -> bool:
    """Inspect routes, excluding query strings and OAuth hash parameters."""
    parts = urlsplit(url)
    fragment_route = parts.fragment.split("?", 1)[0]
    if "=" in fragment_route and not fragment_route.startswith(("/", "!/")):
        fragment_route = ""
    routes = (parts.hostname or "", parts.path, fragment_route)
    login_host = re.search(r"(?:^|\.)(?:login|signin|sign-in|sign_in|logon|passport)[a-z0-9_-]*(?:\.|$)",
                           parts.hostname or "", re.I)
    return bool(login_host) or any(LOGIN_MARKERS.search(unquote(route)) for route in routes)


def _wait_seconds(settings: dict[str, Any]) -> float:
    try:
        seconds = float(settings.get("wait_seconds", DEFAULT_WAIT_SECONDS))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_WAIT_SECONDS
    # The service validates the setting too; keep this helper bounded on its own.
    return max(1.0, min(30.0, seconds))


def _error(message: str) -> dict[str, str]:
    return {"status": "error", "message": message}


async def probe_login_url(page: Any, home_url: str, settings: dict[str, Any]) -> dict[str, str]:
    """Visit home, wait for redirects, and return assumed/required/error.

    This leaves the page open and never brings it to the foreground. ``assumed``
    means that a usable page did not match a login URL, not verified identity.
    Timestamping and any platform-specific checks belong to the caller.
    """
    documents: list[dict[str, Any]] = []
    document_by_request: dict[int, dict[str, Any]] = {}
    failures: list[str] = []
    attached: list[tuple[str, Any]] = []

    def document_for(request: Any) -> dict[str, Any] | None:
        try:
            if not request.is_navigation_request() or request.frame != page.main_frame:
                return None
        except Exception:
            # Service workers and detached frames may not expose a frame.
            return None
        key = id(request)
        if key not in document_by_request:
            document = {"request": request, "response": None}
            document_by_request[key] = document
            documents.append(document)
        return document_by_request[key]

    def on_request(request: Any) -> None:
        document_for(request)

    def on_response(response: Any) -> None:
        try:
            document = document_for(response.request)
            if document is None:
                return
            document["response"] = response
            status = response.status
            if isinstance(status, int) and status >= 400:
                failures.append(f"页面加载异常（HTTP {status}），无法判断登录状态")
        except Exception:
            failures.append("无法读取主文档响应，无法判断登录状态")

    def on_request_failed(request: Any) -> None:
        if document_for(request) is not None:
            failures.append("主文档网络请求失败，无法判断登录状态")

    try:
        for name, callback in (("request", on_request), ("response", on_response),
                               ("requestfailed", on_request_failed)):
            page.on(name, callback)
            attached.append((name, callback))

        try:
            initial_response = await page.goto(
                home_url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS,
            )
            if initial_response is None and not documents:
                # A hash-only SPA navigation has no new document/HTTP result.
                # Reload that same controlled page to obtain fresh evidence.
                initial_response = await page.reload(wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        except Exception:
            return _error("访问主页超时或网络请求失败，无法判断登录状态")
        if initial_response is not None:
            on_response(initial_response)

        await asyncio.sleep(_wait_seconds(settings))
        if failures:
            return _error(failures[-1])

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            ready_state = await asyncio.wait_for(
                page.evaluate("() => document.readyState"), timeout=DOM_CHECK_TIMEOUT_SECONDS,
            )
            final_url = page.url
        except Exception:
            return _error("等待页面加载失败或超时，无法判断登录状态")

        # Recheck after the awaits: a delayed main-frame navigation can fail
        # while the previous document still appears ready.
        if failures:
            return _error(failures[-1])
        if not documents:
            return _error("主页未返回可验证的主文档响应，无法判断登录状态")
        final_response = documents[-1]["response"]
        if final_response is None:
            return _error("页面仍在跳转或加载，无法判断登录状态")
        status = final_response.status
        if not isinstance(status, int) or not 200 <= status < 400:
            return _error("主文档响应无效，无法判断登录状态")
        if ready_state not in {"interactive", "complete"}:
            return _error("页面尚未加载完成，无法判断登录状态")
        try:
            parts = urlsplit(final_url)
            valid_url = parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)
        except (TypeError, ValueError):
            valid_url = False
        if not valid_url:
            return _error("当前不是可用网页，无法判断登录状态")

        login_url = settings.get("login_url", "")
        required = login_url in final_url if isinstance(login_url, str) and login_url else _generic_login_url(final_url)
        if required:
            return {"status": "required", "message": "检测到登录页面，请在浏览器中完成登录"}
        return {"status": "assumed", "message": "页面未跳转至登录页，暂认为登录有效"}
    finally:
        for name, callback in attached:
            try:
                page.remove_listener(name, callback)
            except Exception:
                pass
