from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from reconciliation import reconcile_directory
from scanner import ScanError, scan_directory


class ScanCompletenessTests(unittest.TestCase):
    def test_recursive_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "nested").mkdir()
            (root / "nested" / "file.txt").write_text("hello")
            files = scan_directory(directory)
            self.assertEqual([file.path for file in files],
                             [str(root / "nested" / "file.txt")])
            self.assertEqual(len(files[0].hash), 64)

    def test_file_errors_abort_reconciliation_before_database_read(self):
        for error in (PermissionError("access denied"),
                      ValueError("File does not exist")):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "file.txt").write_text("hello")
                with patch("scanner.scan_file", side_effect=error), \
                     patch("reconciliation.get_all_files") as read_index:
                    with self.assertRaisesRegex(ScanError, "file.txt"):
                        reconcile_directory(directory)
                    read_index.assert_not_called()

    def test_directory_error_aborts_scan(self):
        def failed_walk(root, onerror, followlinks):
            onerror(PermissionError(13, "access denied", str(root / "private")))
            return iter(())

        with tempfile.TemporaryDirectory() as directory:
            with patch("scanner.os.walk", side_effect=failed_walk):
                with self.assertRaisesRegex(ScanError, "private"):
                    scan_directory(directory)

    def test_complete_scan_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory).resolve() / "gone.txt")
            with patch("reconciliation.get_all_files", return_value=[
                {"path": missing, "is_present": 1}
            ]):
                result = reconcile_directory(directory)
            self.assertEqual(result.missing_paths, [missing])


if __name__ == "__main__":
    unittest.main()
