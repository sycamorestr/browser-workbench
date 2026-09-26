import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from backend import folder_picker


@unittest.skipUnless(folder_picker.os.name == "nt", "Windows folder dialog")
class FolderPickerTests(unittest.TestCase):
    def test_path_is_passed_as_data_and_selection_is_returned(self):
        initial = 'D:\\folder with spaces\\$(not-a-command)'
        response = Mock(stdout=json.dumps({"path": initial, "cancelled": False}))
        with patch.object(folder_picker.subprocess, "run", return_value=response) as run:
            self.assertEqual(folder_picker.choose_folder(initial), {"path": initial, "cancelled": False})
        command = run.call_args.args[0]
        self.assertNotIn(initial, " ".join(command))
        self.assertEqual(run.call_args.kwargs["env"]["BROWSER_WORKBENCH_PICKER_START"], initial)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_cancel_does_not_return_stale_path(self):
        with patch.object(folder_picker.subprocess, "run", return_value=Mock(stdout='{"cancelled":true,"path":"old"}')):
            self.assertEqual(folder_picker.choose_folder("D:\\"), {"path": None, "cancelled": True})

    def test_timeout_releases_picker_for_next_request(self):
        with patch.object(folder_picker.subprocess, "run", side_effect=subprocess.TimeoutExpired("picker", 170)):
            with self.assertRaises(folder_picker.FolderPickerError) as result:
                folder_picker.choose_folder("D:\\")
        self.assertEqual(result.exception.code, "folder_picker_timeout")
        self.assertTrue(folder_picker._picker_lock.acquire(blocking=False))
        folder_picker._picker_lock.release()

    def test_second_picker_is_rejected_without_opening_another_dialog(self):
        folder_picker._picker_lock.acquire()
        try:
            with patch.object(folder_picker.subprocess, "run") as run:
                with self.assertRaises(folder_picker.FolderPickerError) as result:
                    folder_picker.choose_folder("D:\\")
            self.assertEqual(result.exception.code, "folder_picker_busy")
            run.assert_not_called()
        finally:
            folder_picker._picker_lock.release()

    def test_invalid_response_has_no_raw_output_in_error(self):
        with patch.object(folder_picker.subprocess, "run", return_value=Mock(stdout="private-process-error")):
            with self.assertRaises(folder_picker.FolderPickerError) as result:
                folder_picker.choose_folder("D:\\")
        self.assertNotIn("private-process-error", str(result.exception))


if __name__ == "__main__":
    unittest.main()
