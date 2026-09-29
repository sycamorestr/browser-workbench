import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import login_settings


class LoginSettingsTests(unittest.TestCase):
    def test_short_url_is_saved_verbatim_without_query_or_path_expansion(self):
        settings = {"mode": "url", "login_url": "  https://登录.example.test/passport?service=a#signin  ", "wait_seconds": 5}
        result = login_settings.validate_settings(settings)
        self.assertEqual(result["login_url"], settings["login_url"].strip())
        self.assertEqual(login_settings.validate_settings(login_settings.DEFAULTS), login_settings.DEFAULTS)

    def test_rejects_invalid_urls_and_unbounded_waits(self):
        for url in ("login", "/login", "javascript:alert(1)", "https://user:secret@example.test/login",
                    "https://example.test:0/login", "https://example.test/log in", "\n", "https://example.test/\x00login"):
            with self.subTest(url=url), self.assertRaises(login_settings.LoginSettingsError):
                login_settings.validate_settings({**login_settings.DEFAULTS, "login_url": url})
        for extra in ({"mode": "automatic"}, {"mode": []}, {"wait_seconds": True}, {"wait_seconds": 0},
                      {"wait_seconds": 31}, {"wait_seconds": 2.5}, {"command": "anything"}):
            with self.subTest(extra=extra), self.assertRaises(login_settings.LoginSettingsError):
                login_settings.validate_settings({**login_settings.DEFAULTS, **extra})

    def test_round_trip_keeps_environment_settings_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / login_settings.FILE_NAME
            self.assertEqual(login_settings.load(path), {})
            values = {"shop01": {**login_settings.DEFAULTS, "login_url": "https://example.test/login"},
                      "shop02": {**login_settings.DEFAULTS, "wait_seconds": 8}}
            login_settings.save(path, values)
            self.assertEqual(login_settings.load(path), values)

    def test_failed_replace_retains_previous_rules_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / login_settings.FILE_NAME
            original = {"shop01": dict(login_settings.DEFAULTS)}
            login_settings.save(path, original)
            before = path.read_bytes()
            with patch.object(login_settings.os, "replace", side_effect=PermissionError), self.assertRaises(login_settings.LoginSettingsError):
                login_settings.save(path, {"shop01": {**login_settings.DEFAULTS, "wait_seconds": 8}})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(folder).glob("*.tmp")), [])

    def test_corrupt_file_cannot_silently_reset_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / login_settings.FILE_NAME
            for raw in ("{broken", json.dumps({"schema_version": 2, "environments": {}}),
                        json.dumps({"schema_version": 1, "environments": {"bad/id": login_settings.DEFAULTS}})):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(login_settings.LoginSettingsError):
                    login_settings.load(path)


if __name__ == "__main__":
    unittest.main()
