from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from backend import lifecycle


class LifecycleStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / lifecycle.FILE_NAME
        self.resource = {"id": "shop-one", "name": "示例环境", "user_data_dir": str(self.root / "profile"),
                         "download_dir": str(self.root / "downloads"), "config_path": str(self.root / "browser.json"), "debug_port": 9450}

    def tearDown(self):
        self.temporary.cleanup()

    def test_archive_restore_delete_only_change_registration(self):
        files = [Path(self.resource["config_path"]), self.root / "profile" / "login-data", self.root / "downloads" / "report"]
        for file in files:
            file.parent.mkdir(exist_ok=True)
            file.write_bytes(b"keep unchanged")
        self.assertEqual(lifecycle.load(self.path), {})
        archived = lifecycle.save_state(self.path, "shop-one", "archived", self.resource)
        self.assertEqual(archived["shop-one"]["state"], "archived")
        self.assertEqual(lifecycle.load(self.path), archived)
        self.assertEqual(lifecycle.save_state(self.path, "shop-one", "active", self.resource), {})
        deleted = lifecycle.save_state(self.path, "shop-one", "deleted", self.resource)
        self.assertEqual(deleted["shop-one"]["resource"], self.resource)
        self.assertTrue(all(file.read_bytes() == b"keep unchanged" for file in files))

    def test_updates_read_latest_document_and_keep_other_environment(self):
        lifecycle.save_state(self.path, "shop-one", "archived", self.resource)
        other = {**self.resource, "id": "shop-two", "name": "第二个", "debug_port": 9451}
        latest = lifecycle.save_state(self.path, "shop-two", "archived", other)
        self.assertEqual(set(latest), {"shop-one", "shop-two"})
        restored = lifecycle.save_state(self.path, "shop-one", "active", self.resource)
        self.assertEqual(set(restored), {"shop-two"})

    def test_failed_write_retains_previous_state_and_does_not_touch_profile(self):
        lifecycle.save_state(self.path, "shop-one", "archived", self.resource)
        before = self.path.read_bytes()
        with patch.object(lifecycle, "_atomic_write_json", side_effect=PermissionError), self.assertRaises(lifecycle.LifecycleError):
            lifecycle.save_state(self.path, "shop-one", "deleted", self.resource)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corruption_and_invalid_resource_cannot_silently_reset_records(self):
        for value in ("broken", json.dumps({"schema_version": 2, "environments": {}})):
            self.path.write_text(value, encoding="utf-8")
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.load(self.path)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.save_state(self.path, "../shop", "archived", self.resource)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.save_state(self.path, "shop-one", "archived", {**self.resource, "user_data_dir": "relative"})


if __name__ == "__main__":
    unittest.main()
