from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend.login_probe import probe_login_url


HOME = "https://portal.example.test/workspace"


class PageDouble:
    def __init__(self, final_url=HOME, status=200):
        self.url = "about:blank"
        self.final_url = final_url
        self.status = status
        self.main_frame = object()
        self.listeners = {}
        self.on = Mock(side_effect=lambda event, callback: self.listeners.setdefault(event, []).append(callback))
        self.remove_listener = Mock(side_effect=lambda event, callback: self.listeners[event].remove(callback))
        self.goto = AsyncMock(side_effect=self._goto)
        self.reload = AsyncMock(return_value=None)
        self.wait_for_load_state = AsyncMock()
        self.evaluate = AsyncMock(return_value="complete")
        self.bring_to_front = AsyncMock()
        self.close = AsyncMock()

    def emit(self, event, item):
        for listener in tuple(self.listeners.get(event, [])):
            listener(item)

    def request(self, url, *, subresource=False, child_frame=False, failure=None):
        request = SimpleNamespace(
            url=url, frame=object() if child_frame else self.main_frame,
            is_navigation_request=Mock(return_value=not subresource), failure=failure,
        )
        self.emit("request", request)
        return request

    def navigate(self, url, status=200, *, child_frame=False):
        request = self.request(url, child_frame=child_frame)
        response = SimpleNamespace(request=request, status=status, url=url)
        self.emit("response", response)
        if not child_frame:
            self.url = url
        return response

    async def _goto(self, url, **kwargs):
        return self.navigate(self.final_url, self.status)


class LoginProbeTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, page, settings=None, during_wait=None):
        with patch("backend.login_probe.asyncio.sleep", new_callable=AsyncMock, side_effect=during_wait) as sleep:
            result = await probe_login_url(page, HOME, settings or {})
        self.assertTrue(all(not callbacks for callbacks in page.listeners.values()))
        self.assertEqual(page.remove_listener.call_count, 3)
        page.bring_to_front.assert_not_called()
        page.close.assert_not_called()
        return result, sleep

    async def test_visits_home_with_bounded_navigation_and_waits_default(self):
        page = PageDouble()
        result, sleep = await self.check(page)
        self.assertEqual(result["status"], "assumed")
        page.goto.assert_awaited_once_with(HOME, wait_until="domcontentloaded", timeout=15000)
        sleep.assert_awaited_once_with(5)
        page.wait_for_load_state.assert_awaited_once_with("domcontentloaded", timeout=15000)
        page.evaluate.assert_awaited_once_with("() => document.readyState")
        self.assertNotIn("checked_at", result)

    async def test_custom_login_url_is_literal_substring_and_uses_configured_wait(self):
        page = PageDouble("https://identity.other.test/Account/Enter?next=workspace")
        result, sleep = await self.check(page, {"login_url": "/Account/Enter", "wait_seconds": 7})
        self.assertEqual(result["status"], "required")
        sleep.assert_awaited_once_with(7)

    async def test_custom_login_url_does_not_apply_generic_fallback(self):
        page = PageDouble("https://login.other.test/Account/Enter")
        result, _ = await self.check(page, {"login_url": "/account/enter"})
        self.assertEqual(result["status"], "assumed")

    async def test_custom_literal_may_match_query_when_user_explicitly_configures_it(self):
        page = PageDouble(HOME + "?view=account-gate")
        result, _ = await self.check(page, {"login_url": "view=account-gate"})
        self.assertEqual(result["status"], "required")

    async def test_generic_markers_match_host_path_and_hash_routes(self):
        for url in (
            "https://login.other.test/", "https://other.test/SignIn",
            "https://other.test/sign-in", "https://other.test/logon",
            "https://other.test/sign_in", "https://passport.other.test/",
            "https://loginmyseller.example.test/", "https://passport-sso.other.test/",
            "https://other.test/#/login",
            "https://other.test/#!/signin?next=workspace", "https://other.test/#login",
        ):
            with self.subTest(url=url):
                result, _ = await self.check(PageDouble(url))
                self.assertEqual(result["status"], "required")

    async def test_query_and_hash_query_redirect_parameters_are_not_login_routes(self):
        for url in (
            HOME + "?redirect=https%3A%2F%2Flogin.other.test%2Flogin",
            HOME + "?next=/signin&flow=oauth",
            HOME + "#/overview?redirect=%2Flogin",
            HOME + "#access_token=contains-login&scope=oauth",
            "https://other.test/oauth/callback?code=example",
            "https://other.test/authorize/callback",
            "https://other.test/catalogin",
        ):
            with self.subTest(url=url):
                result, _ = await self.check(PageDouble(url))
                self.assertEqual(result["status"], "assumed")

    async def test_sso_intermediate_page_returning_home_is_assumed(self):
        page = PageDouble("https://sso.other.test/oauth/authorize")

        async def redirect(_seconds):
            page.navigate(HOME)

        result, _ = await self.check(page, during_wait=redirect)
        self.assertEqual(result["status"], "assumed")

    async def test_delayed_cross_domain_login_is_required(self):
        page = PageDouble()

        async def redirect(_seconds):
            page.navigate("https://login.other.test/signin")

        result, _ = await self.check(page, during_wait=redirect)
        self.assertEqual(result["status"], "required")

    async def test_redirect_chain_can_complete_before_goto_returns(self):
        page = PageDouble()

        async def goto(*args, **kwargs):
            page.navigate("https://sso.other.test/authorize", 302)
            return page.navigate(HOME)

        page.goto.side_effect = goto
        result, _ = await self.check(page)
        self.assertEqual(result["status"], "assumed")

    async def test_http_failure_has_priority_over_login_url(self):
        for status in (400, 401, 403, 404, 500, 503):
            with self.subTest(status=status):
                result, _ = await self.check(PageDouble("https://other.test/login", status))
                self.assertEqual(result["status"], "error")
                self.assertIn(f"HTTP {status}", result["message"])

    async def test_goto_timeout_and_network_failure_are_errors_and_clean_up(self):
        for error in (TimeoutError("Timeout 15000ms exceeded"), RuntimeError("net::ERR_NAME_NOT_RESOLVED")):
            with self.subTest(error=error):
                page = PageDouble()
                page.url = "https://other.test/login"
                page.goto.side_effect = error
                result, sleep = await self.check(page)
                self.assertEqual(result["status"], "error")
                sleep.assert_not_called()

    async def test_delayed_main_document_http_failure_is_error(self):
        page = PageDouble()

        async def redirect(_seconds):
            page.navigate("https://other.test/login", 502)

        result, _ = await self.check(page, during_wait=redirect)
        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 502", result["message"])

    async def test_delayed_main_document_network_failure_is_error(self):
        page = PageDouble()

        async def redirect(_seconds):
            request = page.request("https://other.test/login", failure="net::ERR_CONNECTION_REFUSED")
            page.emit("requestfailed", request)

        result, _ = await self.check(page, during_wait=redirect)
        self.assertEqual(result["status"], "error")
        self.assertIn("网络请求失败", result["message"])

    async def test_subresource_and_child_frame_failures_do_not_pollute_result(self):
        page = PageDouble()

        async def resources(_seconds):
            request = page.request("https://other.test/login.js", subresource=True, failure="failed")
            page.emit("response", SimpleNamespace(request=request, status=500))
            page.emit("requestfailed", request)
            child = page.navigate("https://child.test/login", 503, child_frame=True)
            child.request.failure = "net::ERR_FAILED"
            page.emit("requestfailed", child.request)

        result, _ = await self.check(page, during_wait=resources)
        self.assertEqual(result["status"], "assumed")

    async def test_pending_navigation_is_not_assumed_from_previous_ready_document(self):
        page = PageDouble()

        async def pending(_seconds):
            page.request("https://other.test/login")

        result, _ = await self.check(page, during_wait=pending)
        self.assertEqual(result["status"], "error")

    async def test_failure_during_final_load_wait_is_not_assumed(self):
        page = PageDouble()

        async def wait(*args, **kwargs):
            request = page.request("https://other.test/", failure="net::ERR_FAILED")
            page.emit("requestfailed", request)

        page.wait_for_load_state.side_effect = wait
        result, _ = await self.check(page)
        self.assertEqual(result["status"], "error")

    async def test_final_load_or_dom_check_failure_is_error(self):
        for method in ("wait_for_load_state", "evaluate"):
            with self.subTest(method=method):
                page = PageDouble()
                getattr(page, method).side_effect = TimeoutError("timed out")
                result, _ = await self.check(page)
                self.assertEqual(result["status"], "error")

    async def test_blank_and_browser_error_urls_are_not_assumed(self):
        for url in ("about:blank", "chrome-error://chromewebdata/", "edge-error://edgewebdata/", "", "data:text/html,login"):
            with self.subTest(url=url):
                result, _ = await self.check(PageDouble(url))
                self.assertEqual(result["status"], "error")

    async def test_none_goto_without_new_document_evidence_is_error(self):
        page = PageDouble()
        page.goto.side_effect = None
        page.goto.return_value = None
        page.url = HOME + "#overview"
        result, _ = await self.check(page)
        self.assertEqual(result["status"], "error")

    async def test_none_goto_with_later_successful_document_has_evidence(self):
        page = PageDouble()
        page.goto.side_effect = None
        page.goto.return_value = None

        async def redirect(_seconds):
            page.navigate(HOME)

        result, _ = await self.check(page, during_wait=redirect)
        self.assertEqual(result["status"], "assumed")

    async def test_hash_only_navigation_reloads_for_fresh_document_evidence(self):
        page = PageDouble()
        page.goto.side_effect = None
        page.goto.return_value = None
        page.url = HOME + "#/home"
        page.reload.side_effect = lambda **kwargs: page.navigate(HOME + "#/login")
        result, _ = await self.check(page)
        page.reload.assert_awaited_once_with(wait_until="domcontentloaded", timeout=15000)
        self.assertEqual(result["status"], "required")

    async def test_navigation_exception_does_not_expose_sensitive_url_query(self):
        page = PageDouble()
        page.goto.side_effect = TimeoutError("https://other.test/?token=secret-value")
        result, _ = await self.check(page)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret-value", result["message"])
        self.assertNotIn("token", result["message"])

    async def test_document_loading_is_not_assumed(self):
        page = PageDouble()
        page.evaluate.return_value = "loading"
        result, _ = await self.check(page)
        self.assertEqual(result["status"], "error")

    async def test_cancellation_cleans_up_listeners(self):
        page = PageDouble()
        page.goto.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await probe_login_url(page, HOME, {})
        self.assertTrue(all(not callbacks for callbacks in page.listeners.values()))
        self.assertEqual(page.remove_listener.call_count, 3)


if __name__ == "__main__":
    unittest.main()
