from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend.vendor.browser_controller import (
    BrowserControllerError,
    PageRegistration,
    PlaywrightBrowserController,
    _same_role_page,
    _role_has_login_detection,
    _url_is_login,
)


class FakePage:
    def __init__(self, url="about:blank", *, goto_redirect=None, load_redirect=None, load_error=False):
        self.url = url
        self.goto_redirect = goto_redirect
        self.load_redirect = load_redirect
        self.load_error = load_error
        self.closed = False
        self.visits = []

    def is_closed(self):
        return self.closed

    async def goto(self, url, **_kwargs):
        self.visits.append(url)
        self.url = self.goto_redirect or url

    async def wait_for_load_state(self, *_args, **_kwargs):
        if self.load_redirect:
            self.url = self.load_redirect
        if self.load_error:
            raise TimeoutError("Simulated navigation timeout")

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pages=(), *, next_page=None):
        self.pages = list(pages)
        self.next_page = next_page
        self.created = []

    async def new_page(self):
        page = self.next_page or FakePage()
        self.next_page = None
        self.pages.append(page)
        self.created.append(page)
        return page

    async def new_cdp_session(self, page):
        return Mock(send=AsyncMock(return_value={"targetInfo": {"targetId": str(id(page))}}), detach=AsyncMock())


def controller_for(url, pages=(), *, role="home", next_page=None):
    controller = PlaywrightBrowserController({
        "user_data_dir": "unused-unit-test-profile",
        "browser_sessions": {role: {"url": url}},
    }, timeout_ms=100)
    controller.context = FakeContext(pages, next_page=next_page)
    return controller


KNOWN_ROLES = (
    ("home", "https://myseller.taobao.com/", "https://loginmyseller.taobao.com/login.htm"),
    ("goods", "https://fp.erp321.com/setting/goodsManage", "https://jstlogin.erp321.com/login.aspx"),
)


class BrowserControllerHomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_mode_skips_known_login_gates_and_leaves_other_tabs_untouched(self):
        existing = FakePage("https://loginmyseller.taobao.com/login.htm?tenant=other")
        controller = controller_for("https://myseller.taobao.com/", [existing])
        controller.login_probe_mode = True
        with patch.object(controller, "register_roles", new_callable=AsyncMock) as register:
            await controller.start(open_missing=False)
            page, target_id = await controller.probe_page("home")
        register.assert_not_awaited()
        self.assertIsNot(page, existing)
        self.assertTrue(target_id)
        self.assertEqual(existing.visits, [])
        self.assertEqual(page.visits, [])

    async def test_probe_page_uses_only_exact_home_or_owned_unchanged_redirect(self):
        home = "https://portal.example.test/app?tenant=A#/home"
        unrelated = FakePage("https://portal.example.test/app?tenant=B#/home")
        redirect = FakePage("https://sso.other.test/login?tenant=A")
        controller = controller_for(home, [unrelated, redirect])
        controller.login_probe_mode = True
        page, target_id = await controller.probe_page("home", previous_target_id=str(id(redirect)), previous_url=redirect.url)
        self.assertIs(page, redirect)
        self.assertEqual(target_id, str(id(redirect)))
        self.assertEqual(controller.context.created, [])
        last_url = redirect.url
        redirect.url = "https://sso.other.test/login?tenant=B"
        fresh, _ = await controller.probe_page("home", previous_target_id=target_id, previous_url=last_url)
        self.assertIsNot(fresh, redirect)
        self.assertIsNot(fresh, unrelated)
        self.assertEqual(redirect.visits, [])
        self.assertEqual(unrelated.visits, [])

    async def test_probe_exact_matching_also_protects_known_site_tenant_parameters(self):
        home = "https://myseller.taobao.com/?tenant=A"
        unrelated = FakePage("https://myseller.taobao.com/?tenant=B")
        own = FakePage(home)
        controller = controller_for(home, [unrelated, own])
        controller.login_probe_mode = True
        page, _ = await controller.probe_page("home")
        self.assertIs(page, own)
        self.assertEqual(controller.context.created, [])

    async def test_probe_reuses_only_its_own_launch_blank(self):
        for launched in (False, True):
            blank = FakePage()
            controller = controller_for("https://example.test/", [blank])
            controller.login_probe_mode = True
            controller._browser_owned = launched
            page, _ = await controller.probe_page("home")
            self.assertEqual(page is blank, launched)

    def test_specialized_login_detection_matches_only_supported_homepages(self):
        self.assertTrue(_role_has_login_detection("https://fp.erp321.com/setting/goodsManage/"))
        self.assertFalse(_role_has_login_detection("https://fp.erp321.com/setting/goodsManage/custom/login"))
        self.assertFalse(_role_has_login_detection("https://fp.erp321.com/other/login"))

    async def test_generic_login_home_opens_and_reuses_without_claiming_authentication(self):
        for url in ("http://localhost:8080/login?workspace=A#entry", "https://portal.example.test/login"):
            with self.subTest(url=url):
                self.assertTrue(_url_is_login(url))
                controller = controller_for(url)
                opened = await controller.register_roles(open_missing=True)
                page = controller.page("home")
                self.assertEqual(opened["roles"]["home"]["url"], url)
                self.assertEqual(opened["authentication"], "not_checked")
                await controller.register_roles(open_missing=True)
                await controller._wait_for_startup_roles()
                self.assertEqual(controller.context.created, [page])
                self.assertEqual(page.visits, [url])
                auth = await controller.check_login("home")
                self.assertFalse(auth["is_logged_in"])
                self.assertEqual(auth["code"], "context_missing")
                self.assertNotIn("login_url", auth["signals"])

    async def test_existing_generic_login_home_is_reused_at_startup(self):
        for url in ("https://portal.example.test/account/login", "https://fp.erp321.com/custom/login"):
            with self.subTest(url=url):
                page = FakePage(url)
                controller = controller_for(url, [page])
                result = await controller._wait_for_startup_roles()
                self.assertIs(controller.page("home"), page)
                self.assertFalse(result["roles"]["home"]["created"])
                self.assertEqual(controller.context.created, [])
                self.assertEqual(page.visits, [])

    async def test_query_hash_and_path_variants_remain_untouched_when_opening_home(self):
        target = "https://portal.example.test/workspace?shop=A#overview"
        unrelated_urls = (
            "https://portal.example.test/workspace?shop=B#overview",
            "https://portal.example.test/workspace?shop=A#settings",
            "https://portal.example.test/workspace/child?shop=A#overview",
            "https://portal.example.test/workspace/?shop=A#overview",
            "http://portal.example.test/workspace?shop=A#overview",
        )
        for existing_url in unrelated_urls:
            with self.subTest(existing_url=existing_url):
                page = FakePage(existing_url)
                controller = controller_for(target, [page])
                with self.assertRaises(BrowserControllerError) as missing:
                    await controller.register_roles(open_missing=False)
                self.assertEqual(missing.exception.code, "page_missing")
                await controller.register_roles(open_missing=True)
                self.assertIsNot(controller.page("home"), page)
                self.assertEqual(page.url, existing_url)
                self.assertEqual(page.visits, [])
                self.assertFalse(page.closed)
                await controller.register_roles(open_missing=True)
                self.assertEqual(len(controller.context.created), 1)

    async def test_registered_generic_home_drift_does_not_create_or_overwrite_tabs(self):
        target = "https://portal.example.test/?shop=A#overview"
        page = FakePage(target)
        controller = controller_for(target, [page])
        await controller.register_roles()
        page.url = "https://portal.example.test/?shop=B#overview"
        with self.assertRaises(BrowserControllerError) as missing:
            await controller.register_roles(open_missing=True)
        self.assertEqual(missing.exception.code, "page_missing")
        self.assertEqual(controller.context.created, [])
        self.assertEqual(page.visits, [])
        self.assertFalse(page.closed)

    async def test_browser_normalized_generic_home_is_reused_without_rewriting_configuration(self):
        cases = (
            ("HTTP://EXAMPLE.COM:80", "http://example.com/"),
            ("https://EXAMPLE.COM:443/workspace?shop=A#overview", "https://example.com/workspace?shop=A#overview"),
            ("https://portal.example.test/中文?词=值#首页", "https://portal.example.test/%E4%B8%AD%E6%96%87?%E8%AF%8D=%E5%80%BC#%E9%A6%96%E9%A1%B5"),
            ("https://例子.测试/主页", "https://xn--fsqu00a.xn--0zwm56d/%E4%B8%BB%E9%A1%B5"),
        )
        for target, browser_url in cases:
            with self.subTest(target=target):
                page = FakePage(browser_url)
                controller = controller_for(target, [page])
                await controller.register_roles(open_missing=True)
                await controller._wait_for_startup_roles()
                self.assertIs(controller.page("home"), page)
                self.assertEqual(controller.context.created, [])
                self.assertEqual(page.visits, [])
                self.assertEqual(controller.specs["home"].url, target)

    def test_browser_normalization_still_distinguishes_origin_tenant_and_hash(self):
        target = "https://EXAMPLE.COM:443/中文?shop=A#首页"
        other_urls = (
            "http://example.com/%E4%B8%AD%E6%96%87?shop=A#%E9%A6%96%E9%A1%B5",
            "https://other.example.com/%E4%B8%AD%E6%96%87?shop=A#%E9%A6%96%E9%A1%B5",
            "https://example.com:444/%E4%B8%AD%E6%96%87?shop=A#%E9%A6%96%E9%A1%B5",
            "https://example.com/%E4%B8%AD%E6%96%87?shop=B#%E9%A6%96%E9%A1%B5",
            "https://example.com/%E4%B8%AD%E6%96%87?shop=A#settings",
            "https://example.com/%E4%B8%AD%E6%96%87?shop=A&tab=1#%E9%A6%96%E9%A1%B5",
        )
        for other_url in other_urls:
            with self.subTest(url=other_url):
                self.assertFalse(_same_role_page(other_url, target))

    async def test_existing_known_login_redirects_still_require_manual_login(self):
        for role, target, redirected in KNOWN_ROLES:
            with self.subTest(role=role):
                page = FakePage(redirected)
                controller = controller_for(target, [page], role=role)
                with self.assertRaises(BrowserControllerError) as required:
                    await controller.register_roles(open_missing=True)
                self.assertEqual(required.exception.code, "login_required")
                self.assertEqual(controller.context.created, [])
                controller.registrations[role] = PageRegistration(role, page)
                auth = await controller.check_login(role)
                self.assertEqual(auth["code"], "login_required")
                self.assertEqual(auth["signals"], ["login_url"])

    async def test_new_known_login_redirects_are_left_open_for_manual_login(self):
        for role, target, redirected in KNOWN_ROLES:
            with self.subTest(role=role):
                page = FakePage(goto_redirect=redirected)
                controller = controller_for(target, role=role, next_page=page)
                with self.assertRaises(BrowserControllerError) as required:
                    await controller.register_roles(open_missing=True)
                self.assertEqual(required.exception.code, "login_required")
                self.assertFalse(page.closed)
                self.assertEqual(page.visits, [target])

    async def test_known_startup_redirects_keep_login_gate_even_if_load_times_out(self):
        for role, target, redirected in KNOWN_ROLES:
            for load_error in (False, True):
                with self.subTest(role=role, load_error=load_error):
                    page = FakePage(target, load_redirect=redirected, load_error=load_error)
                    controller = controller_for(target, [page], role=role)
                    with self.assertRaises(BrowserControllerError) as required:
                        await controller._wait_for_startup_roles()
                    self.assertEqual(required.exception.code, "login_required")
                    self.assertFalse(page.closed)

    def test_legacy_role_matching_and_generic_root_equivalence_are_preserved(self):
        home = "https://myseller.taobao.com/"
        self.assertTrue(_same_role_page(home + "home.htm", home))
        self.assertTrue(_same_role_page(home + "home.htm/QnworkbenchHome?from=desktop", home))
        self.assertFalse(_same_role_page(home + "home.htm/merchant-invoice/", home))
        goods = "https://fp.erp321.com/setting/goodsManage"
        self.assertTrue(_same_role_page(goods + "/detail?goods=1#tab", goods))
        self.assertTrue(_same_role_page("https://portal.example.test/", "https://portal.example.test"))
        self.assertFalse(_same_role_page("https://portal.example.test/?shop=B", "https://portal.example.test/?shop=A"))


if __name__ == "__main__":
    unittest.main()
