import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import session_settings


class SessionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / session_settings.FILE_NAME

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_defaults_to_five_minutes_without_writing_a_file(self):
        self.assertEqual(session_settings.load(self.path), {"enabled": True, "interval_minutes": 5})
        self.assertFalse(self.path.exists())

    def test_save_and_reload_disabled_and_each_supported_interval(self):
        for interval in (1, 5, 10, 30, 60):
            settings = {"enabled": interval != 30, "interval_minutes": interval}
            session_settings.save(self.path, settings)
            self.assertEqual(session_settings.load(self.path), settings)
            self.assertEqual(json.loads(self.path.read_text()), {"schema_version": 1, **settings})

    def test_rejects_unknown_missing_and_coerced_settings_without_writing(self):
        for settings in ({}, [], {"enabled": True}, {"enabled": 1, "interval_minutes": 5},
                         {"enabled": False, "interval_minutes": True},
                         {"enabled": True, "interval_minutes": 5.0},
                         {"enabled": True, "interval_minutes": "5"},
                         {"enabled": True, "interval_minutes": 2},
                         {"enabled": True, "interval_minutes": 5, "path": "other"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                session_settings.save(self.path, settings)
        self.assertFalse(self.path.exists())

    def test_corrupt_existing_settings_fail_closed_with_actionable_message(self):
        for content in (b"{", b"\xff", b"[]", b'{"schema_version":true,"enabled":true,"interval_minutes":5}',
                        b'{"schema_version":1,"enabled":true,"interval_minutes":2}',
                        b'{"schema_version":1,"enabled":true,"interval_minutes":5,"extra":1}'):
            self.path.write_bytes(content)
            result = session_settings.load(self.path)
            self.assertFalse(result["enabled"])
            self.assertIn("请重新保存设置", result["message"])
            self.assertEqual(self.path.read_bytes(), content)

    def test_atomic_replace_failure_preserves_prior_settings_and_removes_temp(self):
        prior = {"enabled": False, "interval_minutes": 30}
        session_settings.save(self.path, prior)
        with patch.object(session_settings.os, "replace", side_effect=PermissionError("unwritable")):
            with self.assertRaises(OSError):
                session_settings.save(self.path, {"enabled": True, "interval_minutes": 1})
        self.assertEqual(session_settings.load(self.path), prior)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
