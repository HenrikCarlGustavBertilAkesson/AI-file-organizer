from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from models import DetectedMove
from reconciliation import reconcile_directory, print_reconciliation
from scanner import scan_file


class MoveDetectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def index_file(self, name, content="same contents"):
        path = self.root / name
        path.write_text(content)
        return {"path": str(path), "hash": scan_file(str(path)).hash,
                "is_present": 1}

    def reconcile(self, rows):
        with patch("reconciliation.get_all_files", return_value=rows):
            return reconcile_directory(str(self.root))

    def test_rename_and_move_are_reported_without_mutation(self):
        for destination in ("renamed.txt", "nested/moved.txt"):
            with self.subTest(destination=destination):
                row = self.index_file("original.txt")
                target = self.root / destination
                target.parent.mkdir(exist_ok=True)
                Path(row["path"]).rename(target)
                result = self.reconcile([row])
                self.assertEqual(result.probable_moves,
                                 [DetectedMove(row["path"], str(target))])
                self.assertEqual(result.new_paths, [])
                self.assertEqual(result.missing_paths, [])
                self.assertEqual(target.read_text(), "same contents")
                self.assertFalse(Path(row["path"]).exists())
                output = StringIO()
                with redirect_stdout(output):
                    print_reconciliation(result)
                self.assertIn("Probable moves: 1", output.getvalue())
                self.assertNotIn("No path changes", output.getvalue())
                target.unlink()

    def test_ambiguous_hashes_remain_new_and_missing(self):
        for old_count, new_count in ((1, 2), (2, 1), (2, 2)):
            with self.subTest(old_count=old_count, new_count=new_count):
                rows = [self.index_file(f"old{i}.txt") for i in range(old_count)]
                for row in rows:
                    Path(row["path"]).unlink()
                targets = [self.root / f"new{i}.txt" for i in range(new_count)]
                for target in targets:
                    target.write_text("same contents")
                result = self.reconcile(rows)
                self.assertEqual(result.probable_moves, [])
                self.assertEqual(len(result.new_paths), new_count)
                self.assertEqual(len(result.missing_paths), old_count)
                for target in targets:
                    target.unlink()

    def test_empty_null_and_different_hashes_do_not_match(self):
        row = self.index_file("old.txt")
        Path(row["path"]).rename(self.root / "new.txt")
        for file_hash in ("", None, "different"):
            with self.subTest(file_hash=file_hash):
                result = self.reconcile([{**row, "hash": file_hash}])
                self.assertEqual(result.probable_moves, [])
                self.assertEqual(len(result.new_paths), 1)
                self.assertEqual(len(result.missing_paths), 1)

    def test_unchanged_and_modified_paths_are_not_move_candidates(self):
        row = self.index_file("file.txt")
        for content in ("same contents", "changed"):
            Path(row["path"]).write_text(content)
            result = self.reconcile([row])
            self.assertEqual(result.new_paths, [])
            self.assertEqual(result.missing_paths, [])
            self.assertEqual(result.probable_moves, [])

    def test_outside_root_and_inactive_rows_are_not_candidates(self):
        row = self.index_file("old.txt")
        Path(row["path"]).rename(self.root / "new.txt")
        result = self.reconcile([
            {**row, "path": str(self.root.parent / "outside.txt")},
            {**row, "is_present": 0},
        ])
        self.assertEqual(result.probable_moves, [])
        self.assertEqual(result.missing_paths, [])
        self.assertEqual(result.new_paths, [str(self.root / "new.txt")])


if __name__ == "__main__":
    unittest.main()
