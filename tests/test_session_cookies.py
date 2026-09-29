from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from backend.session_cookies import (
    METADATA_NAME, RETENTION_DAYS, business_cookie_scope, empty_status, in_scope,
    persist_session_cookies, persistent_cookie_param, read_metadata, write_metadata,
)


def cookie(**overrides):
    return {"name": "session", "value": "secret-cookie-value", "domain": ".taobao.com", "path": "/",
            "secure": True, "httpOnly": True, "sameSite": "None", "priority": "High", "session": True,
            "sourceScheme": "Secure", "sourcePort": 443, **overrides}


class CookieParameterTests(unittest.TestCase):
    def test_domain_cookie_preserves_attributes_without_server_session_claims(self):
        value = persistent_cookie_param(cookie(), 12345)
        self.assertEqual(value, {"name": "session", "value": "secret-cookie-value", "domain": ".taobao.com",
                                 "path": "/", "secure": True, "httpOnly": True, "sameSite": "None",
                                 "priority": "High", "sourceScheme": "Secure", "sourcePort": 443,
                                 "expires": 12345})
        self.assertEqual(RETENTION_DAYS, 30)

    def test_host_only_and_host_prefix_never_gain_domain_scope(self):
        for overrides in ({"domain": "login.taobao.com"}, {"domain": ".taobao.com", "name": "__Host-session"}):
            value = persistent_cookie_param(cookie(**overrides), 12345)
            self.assertNotIn("domain", value)
            self.assertTrue(value["url"].startswith("https://"))
            self.assertNotIn("https://.", value["url"])

    def test_partition_key_and_false_attributes_are_preserved(self):
        partition = {"topLevelSite": "https://tmall.com", "hasCrossSiteAncestor": False}
        value = persistent_cookie_param(cookie(partitionKey=partition, sameParty=False, sourcePort=-1), 100)
        self.assertEqual(value["partitionKey"], partition)
        self.assertIsNot(value["partitionKey"], partition)
        self.assertIs(value["sameParty"], False)
        self.assertEqual(value["sourcePort"], -1)
        with self.assertRaises(ValueError):
            persistent_cookie_param(cookie(partitionKeyOpaque=True), 100)

    def test_business_family_boundaries_and_configured_exact_hosts(self):
        families, hosts = business_cookie_scope("shop", ["https://myseller.taobao.com/", "http://127.0.0.1:1234/business", "https://one.custom.test/"])
        for domain in (".taobao.com", "login.taobao.com", ".tmall.com", "127.0.0.1", "one.custom.test"):
            self.assertTrue(in_scope(domain, families, hosts), domain)
        for domain in ("faketaobao.com", "taobao.com.attacker.test", "alicdn.com", "two.custom.test", "erp321.com"):
            self.assertFalse(in_scope(domain, families, hosts), domain)
        families, hosts = business_cookie_scope("shared", ["https://fp.erp321.com/"])
        self.assertTrue(in_scope(".erp321.com", families, hosts))
        self.assertFalse(in_scope("fakeerp321.com", families, hosts))
        self.assertFalse(in_scope(".taobao.com", families, hosts))

    def test_generic_cookie_scope_comes_from_configured_sites_without_legacy_platform_families(self):
        for legacy_kind in ("shop", "shared", "browser"):
            families, hosts = business_cookie_scope(legacy_kind, ["https://portal.example.test/path?tenant=one", "http://127.0.0.1:8010/"])
            self.assertEqual(families, set())
            self.assertEqual(hosts, {"portal.example.test", "127.0.0.1"})
            for domain in ("portal.example.test", ".example.test", "127.0.0.1"):
                self.assertTrue(in_scope(domain, families, hosts), domain)
            for domain in ("other.example.test", "example.test", ".taobao.com", ".tmall.com", ".alibaba.com", ".erp321.com", ".test"):
                self.assertFalse(in_scope(domain, families, hosts), domain)

    def test_platform_lookalike_hosts_cannot_expand_cookie_family_scope(self):
        families, hosts = business_cookie_scope("shop", ["https://myseller.taobao.com.attacker.test/"])
        self.assertEqual(families, set())
        self.assertFalse(in_scope(".taobao.com", families, hosts))

    def test_metadata_allowlist_and_untrusted_content_sanitization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = {**empty_status(), "status": "saved", "last_saved_at": "2026-09-26T00:00:00+00:00",
                     "persisted_count": 1, "cookie": cookie(), "message": "secret-value"}
            write_metadata(root, value)
            raw = (root / METADATA_NAME).read_text(encoding="utf-8")
            self.assertNotIn("secret", raw)
            self.assertNotIn("cookie", raw)
            restored = read_metadata(root)
            self.assertEqual(restored["persisted_count"], 1)
            self.assertNotIn("secret", restored["message"])
            (root / METADATA_NAME).write_text(json.dumps({**value, "persisted_count": "secret"}), encoding="utf-8")
            self.assertEqual(read_metadata(root)["status"], "idle")


class CookiePersistenceTests(unittest.IsolatedAsyncioTestCase):
    def browser(self, cookies, failing_names=()):
        self.writes = []
        async def send(method, params=None):
            if method == "Storage.getCookies":
                return {"cookies": cookies}
            self.assertEqual(method, "Storage.setCookies")
            entry = params["cookies"][0]
            self.writes.append(entry)
            if entry["name"] in failing_names:
                raise RuntimeError("secret-cookie-value and secret-name must not escape")
            return {}
        self.session = Mock(send=AsyncMock(side_effect=send), detach=AsyncMock())
        return Mock(new_browser_cdp_session=AsyncMock(return_value=self.session))

    async def test_only_scoped_nonopaque_session_cookies_are_promoted(self):
        browser = self.browser([cookie(), cookie(session=False, expires=10), cookie(partitionKeyOpaque=True),
                                cookie(domain="other.test"), cookie(domain="127.0.0.1", secure=False)])
        result = await persist_session_cookies(browser, {"taobao.com"}, {"127.0.0.1"})
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["persisted_count"], 2)
        self.assertEqual(result["skipped_count"], 3)
        self.assertEqual(result["failed_count"], 0)
        self.assertIsNotNone(result["last_saved_at"])
        self.assertEqual(len(self.writes), 2)
        self.assertNotIn("secret", json.dumps(result))
        self.session.detach.assert_awaited_once()

    async def test_partial_failure_is_visible_and_does_not_abort_other_cookies(self):
        browser = self.browser([cookie(name="ok"), cookie(name="bad"), cookie(name="also-ok")], {"bad"})
        result = await persist_session_cookies(browser, {"taobao.com"}, set())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["persisted_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(len(self.writes), 3)
        self.assertNotIn("secret", json.dumps(result))

    async def test_cookie_read_errors_are_redacted(self):
        browser = self.browser([])
        self.session.send.side_effect = RuntimeError("secret-token")
        result = await persist_session_cookies(browser, {"taobao.com"}, set())
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["last_saved_at"])
        self.assertNotIn("secret", json.dumps(result))
        self.session.detach.assert_awaited_once()

    async def test_explicit_work_can_interrupt_background_checkpoint(self):
        browser = self.browser([cookie(), cookie(name="another")])
        result = await persist_session_cookies(browser, {"taobao.com"}, set(), should_continue=lambda: False)
        self.assertEqual(self.writes, [])
        self.assertNotEqual(result["status"], "saved")
        self.assertEqual(result["failed_count"], 2)

    async def test_empty_cookie_jar_is_successful_without_writes(self):
        browser = self.browser([])
        result = await persist_session_cookies(browser, {"taobao.com"}, set())
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["persisted_count"], 0)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
