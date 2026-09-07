from pathlib import Path
import sqlite3
import os
from contextlib import redirect_stdout
from io import StringIO
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import database
from reconciliation import reconcile_directory, print_reconciliation
from scanner import ScanError, scan_file


class IndexRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.root = base / "files"
        self.root.mkdir()
        override = patch.object(database, "DATABASE", str(base / "index.db"))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def indexed(self, name, content):
        path = self.root / name
        path.write_text(content)
        file = scan_file(str(path))
        file.category = "Work"
        file.status = "classified"
        database.save_file(file)
        return path

    def test_repairs_and_second_run_are_idempotent(self):
        old = self.indexed("old.txt", "moved")
        original = database.get_file_by_path(str(old))
        target = self.root / "moved.txt"
        old.rename(target)
        missing = self.indexed("missing.txt", "deleted")
        missing.unlink()
        new = self.root / "new.txt"
        new.write_text("new")
        before = database.get_all_files()
        reconcile_directory(str(self.root))
        self.assertEqual(database.get_all_files(), before)
        reconcile_directory(str(self.root), apply=True)
        moved = database.get_file_by_path(str(target))
        self.assertEqual(moved["id"], original["id"])
        self.assertEqual(moved["category"], "Work")
        self.assertEqual(moved["status"], "classified")
        self.assertEqual(database.get_file_by_path(str(missing))["is_present"], 0)
        self.assertEqual(database.get_file_by_path(str(new))["status"], "pending")
        after = database.get_all_files()
        result = reconcile_directory(str(self.root), apply=True)
        self.assertEqual(database.get_all_files(), after)
        self.assertFalse(result.new_paths or result.missing_paths or result.probable_moves)

    def test_returning_files_preserve_only_valid_classification(self):
        for name, returned_content in (("same.txt", "original"),
                                       ("changed.txt", "replacement")):
            path = self.indexed(name, "original")
            path.unlink()
            reconcile_directory(str(self.root), apply=True)
            path.write_text(returned_content)
            reconcile_directory(str(self.root), apply=True)
            row = database.get_file_by_path(str(path))
            self.assertEqual(row["is_present"], 1)
            self.assertEqual(row["status"],
                             "classified" if returned_content == "original" else "pending")

    def test_scan_failure_leaves_database_unchanged(self):
        self.indexed("file.txt", "text")
        before = database.get_all_files()
        with patch("reconciliation.scan_directory", side_effect=ScanError("denied")):
            with self.assertRaises(ScanError):
                reconcile_directory(str(self.root), apply=True)
        self.assertEqual(database.get_all_files(), before)

    def test_write_failure_rolls_back_earlier_updates(self):
        missing = self.indexed("missing.txt", "text")
        missing.unlink()
        (self.root / "new.txt").write_text("new")
        before = database.get_all_files()
        with patch("reconciliation.save_file", side_effect=sqlite3.IntegrityError("failure")):
            with self.assertRaises(sqlite3.IntegrityError):
                reconcile_directory(str(self.root), apply=True)
        self.assertEqual(database.get_all_files(), before)

    def test_save_file_updates_presence(self):
        path = self.indexed("file.txt", "text")
        file = scan_file(str(path))
        file.is_present = False
        database.save_file(file)
        file.is_present = True
        database.save_file(file)
        self.assertEqual(database.get_file_by_path(str(path))["is_present"], 1)

    def test_modified_contents_clear_analysis_and_preserve_identity(self):
        path = self.indexed("file.txt", "original")
        file = scan_file(str(path))
        file.content = "extracted text"
        file.category = "Work"
        file.subcategory = "Contract"
        file.description = "Old description"
        file.confidence = 0.9
        file.error = "old error"
        file.status = "classified"
        database.save_file(file)
        before = database.get_file_by_path(str(path))
        path.write_text("replacement contents")
        # Hash comparison must detect changes even with the original mtime.
        os.utime(path, (file.modified, file.modified))
        report = reconcile_directory(str(self.root))
        self.assertEqual(report.modified_paths, [str(path)])
        self.assertEqual(database.get_file_by_path(str(path)), before)
        output = StringIO()
        with redirect_stdout(output):
            print_reconciliation(report)
        self.assertIn("Modified files: 1", output.getvalue())
        self.assertNotIn("No changes detected", output.getvalue())
        reconcile_directory(str(self.root), apply=True)
        after = database.get_file_by_path(str(path))
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["hash"], scan_file(str(path)).hash)
        self.assertEqual(after["size"], path.stat().st_size)
        self.assertEqual(after["status"], "pending")
        for key in ("content", "category", "subcategory", "description", "error"):
            self.assertEqual(after[key], "")
        self.assertEqual(after["confidence"], 0.0)
        self.assertEqual(reconcile_directory(str(self.root), apply=True).modified_paths, [])
        self.assertEqual(database.get_file_by_path(str(path)), after)

    def test_timestamp_only_change_preserves_classification(self):
        path = self.indexed("file.txt", "original")
        before = database.get_file_by_path(str(path))
        os.utime(path, (before["modified"] + 60, before["modified"] + 60))
        result = reconcile_directory(str(self.root), apply=True)
        self.assertEqual(result.modified_paths, [])
        self.assertEqual(database.get_file_by_path(str(path)), before)

    def test_missing_legacy_hash_requires_reprocessing(self):
        path = self.indexed("file.txt", "original")
        file = scan_file(str(path))
        file.hash = ""
        database.save_file(file)
        result = reconcile_directory(str(self.root), apply=True)
        self.assertEqual(result.modified_paths, [str(path)])
        self.assertEqual(database.get_file_by_path(str(path))["status"], "pending")


if __name__ == "__main__":
    unittest.main()
