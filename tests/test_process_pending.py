from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import database
from process_pending import process_pending
from scanner import scan_file


class PendingProcessingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "files"
        self.root.mkdir()
        override = patch.object(database, "DATABASE", str(self.base / "index.db"))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def add(self, name, status="pending", present=True, outside=False):
        path = (self.base if outside else self.root) / name
        path.write_text("original")
        file = scan_file(str(path))
        file.status = status
        file.is_present = present
        database.save_file(file)
        return path

    def test_filters_and_rescans(self):
        pending = self.add("pending.txt")
        pending.write_text("changed contents")
        self.add("classified.txt", status="classified")
        self.add("failed.txt", status="failed")
        self.add("absent.txt", present=False)
        self.add("outside.txt", outside=True)
        self.add("deleted.txt").unlink()
        escaped = self.add("escaped.txt")
        escaped.unlink()
        escaped.symlink_to(self.base / "outside.txt")

        def process(file):
            self.assertEqual(file.path, str(pending))
            self.assertEqual(file.hash, scan_file(str(pending)).hash)
            file.status = "classified"
            database.save_file(file)
            return file

        with patch("process_pending.process_file", side_effect=process) as worker:
            summary = process_pending(str(self.root))
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(summary.classified, 1)
        self.assertEqual(summary.skipped, 2)
        self.assertEqual(database.get_file_by_path(str(self.root / "classified.txt"))["status"], "classified")

    def test_failure_does_not_stop_queue_and_can_be_retried(self):
        failed = self.add("first.txt")
        self.add("second.txt")

        def process(file):
            if file.path == str(failed):
                raise RuntimeError("classification unavailable")
            file.status = "empty"
            database.save_file(file)
            return file

        with patch("process_pending.process_file", side_effect=process):
            summary = process_pending(str(self.root))
        self.assertEqual((summary.failed, summary.empty), (1, 1))
        self.assertEqual(database.get_file_by_path(str(failed))["status"], "failed")
        with patch("process_pending.process_file") as worker:
            process_pending(str(self.root))
            worker.assert_not_called()

        def retry(file):
            file.status = "unsupported"
            database.save_file(file)
            return file

        with patch("process_pending.process_file", side_effect=retry):
            summary = process_pending(str(self.root), retry_failed=True)
        self.assertEqual(summary.unsupported, 1)

    def test_pipeline_reported_failure_is_counted(self):
        self.add("file.txt")
        with patch("process_pending.process_file", return_value={"status": "failed"}):
            self.assertEqual(process_pending(str(self.root)).failed, 1)


if __name__ == "__main__":
    unittest.main()
