from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend import shop_registry as registry

HOME_URL = "https://console.example.test/admin?workspace=one#overview"


def create_in_process(registry_path, folder, name, queue):
    try:
        value = registry.create_shop(Path(registry_path), [], {"name": name, "parent_folder": folder, "home_url": HOME_URL})
        queue.put({"ok": True, "record": value})
    except Exception as exc:
        queue.put({"ok": False, "error": str(exc)})


class ShopRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "shops.json"
        self.original = b'{"schema_version":1,"shops":[{"id":"shop01","store":"Existing"}]}'
        self.source.write_bytes(self.original)
        self.parent = self.root / "chosen"
        self.parent.mkdir()
        self.manifest = self.root / registry.MANIFEST_NAME

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, name="新增店铺", **overrides):
        return registry.create_shop(self.source, [], {
            "name": name, "parent_folder": str(self.parent), "home_url": HOME_URL, **overrides,
        })

    def config(self, record):
        return json.loads(Path(record["browser_config"]).read_text(encoding="utf-8"))

    def test_creates_two_isolated_profiles_and_recovers_records_without_changing_original(self):
        self.assertEqual(registry.load_custom_shops(self.source), [])
        first = self.create(login_username="test-user")
        second = self.create("另一店铺")
        a, b = self.config(first), self.config(second)
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(a["remote_debugging_port"], b["remote_debugging_port"])
        self.assertNotEqual(a["user_data_dir"], b["user_data_dir"])
        for config, record in ((a, first), (b, second)):
            self.assertTrue(Path(config["user_data_dir"]).is_dir())
            self.assertTrue(Path(config["download_dir"]).is_dir())
            self.assertEqual(Path(record["browser_config"]).parent.parent, self.parent)
            self.assertRegex(record["id"], r"^shop-[0-9a-f]{8}$")
            self.assertEqual(config["browser"], "Edge")
            self.assertEqual(config["profile_directory"], "Default")
            self.assertEqual(config["browser_sessions"], {"home": {"url": HOME_URL}})
            self.assertGreaterEqual(config["remote_debugging_port"], 9401)
        self.assertEqual(registry.load_custom_shops(self.source), [first, second])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertNotIn("password", self.manifest.read_text(encoding="utf-8"))

    def test_invalid_payload_and_paths_do_not_create_environment(self):
        invalid = [
            ({"name": ""}, "invalid_shop_name"),
            ({"name": "a" * 81}, "invalid_shop_name"),
            ({"name": "a\nb"}, "invalid_shop_name"),
            ({"login_username": "u" * 161}, "invalid_login_username"),
            ({"password": "never-persist"}, "invalid_shop"),
            ({"home_url": ""}, "invalid_home_url"),
            ({"parent_folder": "relative"}, "invalid_parent_folder"),
            ({"parent_folder": "C:relative"}, "invalid_parent_folder"),
            ({"parent_folder": r"\\server\share"}, "invalid_parent_folder"),
            ({"parent_folder": r"\\?\C:\data"}, "invalid_parent_folder"),
            ({"parent_folder": str(self.parent / "NUL")}, "invalid_parent_folder"),
            ({"parent_folder": str(self.parent / "trailing.")}, "invalid_parent_folder"),
            ({"parent_folder": str(self.parent / "missing")}, "invalid_parent_folder"),
            ({"parent_folder": str(self.source)}, "invalid_parent_folder"),
        ]
        for override, code in invalid:
            with self.subTest(override=override):
                with self.assertRaises(registry.ShopRegistryError) as error:
                    self.create(**override)
                self.assertEqual(error.exception.code, code)
        self.assertFalse(self.manifest.exists())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_names_are_unique_across_original_and_custom_environments(self):
        self.create("店铺 A")
        for name in ("店铺  A", "店铺 Ａ"):
            with self.assertRaises(registry.ShopRegistryError) as error:
                self.create(name)
            self.assertEqual(error.exception.code, "duplicate_shop_name")
        with self.assertRaises(registry.ShopRegistryError) as error:
            registry.create_shop(self.source, [{"id": "shop01", "name": "原店铺"}],
                                 {"name": "原店铺", "parent_folder": str(self.parent), "home_url": HOME_URL})
        self.assertEqual(error.exception.code, "duplicate_shop_name")

    def test_deleted_registration_releases_name_but_reserves_directory_and_port(self):
        original = self.create("填错的环境")
        config = self.config(original)
        sentinel = Path(config["user_data_dir"]) / "login-data"
        sentinel.write_bytes(b"keep login data")
        reservation = {"id": original["id"], "name": original["store"], "deleted": True,
                       "user_data_dir": config["user_data_dir"], "download_dir": config["download_dir"],
                       "debug_port": config["remote_debugging_port"], "config_path": original["browser_config"]}
        replacement = registry.create_shop(self.source, [reservation], {"name": original["store"],
                    "parent_folder": str(self.parent), "home_url": HOME_URL})
        self.assertNotEqual(replacement["id"], original["id"])
        self.assertNotEqual(self.config(replacement)["remote_debugging_port"], config["remote_debugging_port"])
        self.assertEqual(sentinel.read_bytes(), b"keep login data")
        self.assertEqual(len(registry.load_custom_shops(self.source, retired_ids={original["id"]})), 2)
        # Files retained by deletion may subsequently be removed by their owner.
        # Creation must still reserve the old resources without reading them.
        Path(original["browser_config"]).unlink()
        third = registry.create_shop(self.source, [reservation], {"name": "另一个新环境",
                    "parent_folder": str(self.parent), "home_url": HOME_URL})
        self.assertNotEqual(self.config(third)["remote_debugging_port"], config["remote_debugging_port"])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_protected_profile_and_download_subtrees_rejected_but_ancestor_parent_allowed(self):
        profile, downloads = self.parent / "old-profile", self.parent / "old-downloads"
        profile.mkdir(); downloads.mkdir()
        nested = profile / "nested"
        nested.mkdir()
        existing = [{"id": "shop01", "name": "旧店铺", "user_data_dir": str(profile),
                     "download_dir": str(downloads), "debug_port": 9401}]
        for parent in (profile, nested, downloads):
            with self.assertRaises(registry.ShopRegistryError) as error:
                registry.create_shop(self.source, existing, {"name": "新店铺", "parent_folder": str(parent), "home_url": HOME_URL})
            self.assertEqual(error.exception.code, "folder_conflict")
        record = registry.create_shop(self.source, existing, {"name": "新店铺", "parent_folder": str(self.parent), "home_url": HOME_URL})
        self.assertNotEqual(self.config(record)["remote_debugging_port"], 9401)
        self.assertTrue(profile.is_dir())
        self.assertTrue(downloads.is_dir())

    def test_homepage_is_required_and_invalid_addresses_leave_no_files(self):
        missing = {"name": "Missing homepage", "parent_folder": str(self.parent)}
        with self.assertRaises(registry.ShopRegistryError) as error:
            registry.create_shop(self.source, [], missing)
        self.assertEqual(error.exception.code, "invalid_home_url")
        for url in (None, "", "   ", "example.test", "//example.test", "http:///missing",
                    "javascript:alert(1)", "file:///C:/private", "ftp://example.test/", "https://user:secret@example.test/",
                    "https://@example.test/", "https://example.test:0/", "http://example.test:65536/",
                    "http://[::1", "https://example.test/a b", "https://example.test/\n", "https://example.test/\x85",
                    "https://example.test\\other", "https://example.test/" + "a" * 4096):
            with self.subTest(url=url), self.assertRaises(registry.ShopRegistryError) as error:
                self.create(home_url=url)
            self.assertEqual(error.exception.code, "invalid_home_url")
            self.assertFalse(self.manifest.exists())
            self.assertEqual(list(self.parent.iterdir()), [])

    def test_homepage_preserves_user_path_query_fragment_and_supports_local_urls(self):
        for index, url in enumerate(("http://127.0.0.1:18765/admin?shop=a&next=%2Fhome#workspace",
                                    "http://intranet/dashboard", "http://[::1]:18765/login?team=b#login",
                                    "https://console.example.test/path/%E4%B8%AD?q=a%20b#section")):
            with self.subTest(url=url):
                record = self.create(name=f"Custom {index}", home_url="  " + url + "  ")
                self.assertEqual(self.config(record)["browser_sessions"]["home"]["url"], url)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_live_listener_port_is_excluded(self):
        port, listener = registry._reserve_debug_port(set())
        try:
            listener.listen()
            with patch.object(registry, "FIRST_PORT", port):
                record = self.create()
            self.assertNotEqual(self.config(record)["remote_debugging_port"], port)
        finally:
            listener.close()

    def test_existing_directory_is_never_reused_or_overwritten(self):
        root = self.parent / "shop-aaaaaaaa"
        root.mkdir()
        marker = root / "user-data.txt"
        marker.write_text("keep", encoding="utf-8")
        with patch.object(registry.uuid, "uuid4", return_value=SimpleNamespace(hex="a" * 32)):
            with self.assertRaises(registry.ShopRegistryError) as error:
                self.create()
        self.assertEqual(error.exception.code, "folder_conflict")
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_config_publication_cannot_overwrite_an_unexpected_file(self):
        target = self.parent / "browser.json"
        target.write_text("existing user file", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            registry._atomic_write_json(target, {"schema_version": 1}, replace_existing=False)
        self.assertEqual(target.read_text(encoding="utf-8"), "existing user file")
        self.assertEqual(list(self.parent.iterdir()), [target])

    def test_busy_registry_lock_has_bounded_failure_and_preserves_original(self):
        lock = registry.FileMutex(self.root / registry.LOCK_NAME)
        lock.acquire()
        try:
            with patch.object(registry, "LOCK_WAIT_SECONDS", 0):
                with self.assertRaises(registry.ShopRegistryError) as error:
                    self.create()
            self.assertEqual(error.exception.code, "registry_busy")
            self.assertEqual(list(self.parent.iterdir()), [])
            self.assertEqual(self.source.read_bytes(), self.original)
        finally:
            lock.release()

    def test_manifest_validation_and_relative_config_resolution(self):
        for content in ("not json", "[]", '{"schema_version":2,"shops":[]}', '{"schema_version":1,"shops":[{}]}'):
            self.manifest.write_text(content, encoding="utf-8")
            with self.assertRaises(registry.ShopRegistryError) as error:
                registry.load_custom_shops(self.source)
            self.assertEqual(error.exception.code, "invalid_custom_registry")
        self.manifest.write_text(json.dumps({"schema_version": 1, "shops": [
            {"id": "custom-one", "store": "自定义", "browser_config": "relative/browser.json"}
        ]}), encoding="utf-8")
        record = registry.load_custom_shops(self.source)[0]
        self.assertEqual(Path(record["browser_config"]), self.root / "relative" / "browser.json")
        self.assertEqual(record["login_username"], "")

    def test_commit_failure_preserves_existing_manifest_and_user_files(self):
        first = self.create("已完成")
        document = json.loads(self.manifest.read_text(encoding="utf-8"))
        document["custom_metadata"] = {"preserve": True}
        self.manifest.write_text(json.dumps(document), encoding="utf-8")
        before = self.manifest.read_bytes()
        first_config = Path(first["browser_config"]).read_bytes()
        original_replace = registry.os.replace
        def fail_manifest(source, destination):
            if Path(destination) == self.manifest:
                # A file placed in the new profile must survive cleanup.
                new_roots = [p for p in self.parent.iterdir() if p.name != first["id"]]
                (new_roots[0] / "profile" / "keep.txt").write_text("user file", encoding="utf-8")
                raise PermissionError("simulated manifest failure")
            return original_replace(source, destination)
        with patch.object(registry.os, "replace", side_effect=fail_manifest):
            with self.assertRaises(registry.ShopRegistryError) as error:
                self.create("提交失败")
        self.assertEqual(error.exception.code, "registry_write_failed")
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(Path(first["browser_config"]).read_bytes(), first_config)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(registry.load_custom_shops(self.source), [first])
        survivors = list(self.parent.glob("*/profile/keep.txt"))
        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0].read_text(encoding="utf-8"), "user file")
        self.create("恢复后创建")
        self.assertTrue(json.loads(self.manifest.read_text(encoding="utf-8"))["custom_metadata"]["preserve"])

    def test_two_processes_append_without_lost_records_or_shared_ports(self):
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [context.Process(target=create_in_process, args=(str(self.source), str(self.parent), name, queue))
                     for name in ("并发甲", "并发乙")]
        for process in processes:
            process.start()
        results = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertTrue(all(item["ok"] for item in results), results)
        records = registry.load_custom_shops(self.source)
        self.assertEqual(len(records), 2)
        self.assertEqual(len({self.config(record)["remote_debugging_port"] for record in records}), 2)
        self.assertEqual(len({self.config(record)["user_data_dir"] for record in records}), 2)
        self.assertEqual(self.source.read_bytes(), self.original)
        queue.close()


if __name__ == "__main__":
    unittest.main()
