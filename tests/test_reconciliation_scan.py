from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from reconciliation import reconcile_directory
from scanner import ScanError, scan_directory
import database


class ScanCompletenessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        override = patch.object(database, 'DATABASE', str(Path(temp.name) / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def test_recursive_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "nested").mkdir()
            (root / "nested" / "file.txt").write_text("hello")
            files = scan_directory(directory)
            self.assertEqual([file.path for file in files],
                             [str(root / "nested" / "file.txt")])
            self.assertEqual(len(files[0].hash), 64)

    def test_file_errors_are_reported_without_false_missing_files(self):
        for error in (PermissionError('access denied'), ValueError('File does not exist')):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / 'file.txt'
                path.write_text('hello')
                with patch('scanner.scan_file', side_effect=error), \
                     patch('reconciliation.get_all_files', return_value=[{'path': str(path), 'is_present': 1}]):
                    result = reconcile_directory(directory)
                self.assertFalse(result.scan.complete)
                self.assertEqual(result.scan.issues[0].path, str(path))
                self.assertEqual(result.missing_paths, [])

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
