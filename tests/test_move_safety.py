from contextlib import redirect_stdout, closing
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import database
from actions.executor import execute_action
from actions.validator import validate_action
from models import ActionStatus, ProposedAction
from review import review_pending_actions
from scanner import scan_file


class MoveSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "allowed"
        self.root.mkdir()
        self.source = self.root / "source.txt"
        self.source.write_text("precious contents")
        self.destination = self.root / "nested" / "destination.txt"
        override = patch.object(database, "DATABASE", str(self.base / "index.db"))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def action(self, **overrides):
        values = dict(action_type="move", source=str(self.source),
                      destination=str(self.destination), reason="Organize")
        values.update(overrides)
        return ProposedAction(**values)

    def test_validator_accepts_move_inside_root(self):
        self.assertEqual(validate_action(self.action(), str(self.root)), (True, ""))

    def test_validator_rejects_unsafe_paths_and_types(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside")
        cases = [
            {"action_type": "delete"},
            {"source": str(outside)},
            {"destination": str(outside)},
            {"destination": str(self.root / ".." / "escape.txt")},
            {"destination": str(self.base / "allowed-other" / "escape.txt")},
            {"source": str(self.root / "missing.txt")},
            {"source": str(self.root)},
            {"destination": str(self.source)},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                valid, error = validate_action(self.action(**overrides), str(self.root))
                self.assertFalse(valid)
                self.assertTrue(error)
        self.assertEqual(self.source.read_text(), "precious contents")

    def test_symlink_escape_is_rejected_for_source_and_destination(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside")
        link = self.root / "link.txt"
        link.symlink_to(outside)
        directory_link = self.root / "external"
        directory_link.symlink_to(self.base, target_is_directory=True)
        for action in (self.action(source=str(link)),
                       self.action(destination=str(directory_link / "new.txt"))):
            self.assertFalse(validate_action(action, str(self.root))[0])

    def test_unapproved_actions_never_move_files(self):
        for status in ("pending", "rejected", "failed", "executed"):
            with self.subTest(status=status):
                action = self.action(status=status)
                self.assertFalse(execute_action(action, str(self.root)))
                self.assertEqual(action.status, status)
                self.assertTrue(action.error)
                self.assertTrue(self.source.exists())
                self.assertFalse(self.destination.exists())

    def test_approved_move_creates_directories(self):
        action = self.action(status=ActionStatus.APPROVED)
        self.assertTrue(execute_action(action, str(self.root)))
        self.assertEqual(action.status, ActionStatus.EXECUTED)
        self.assertEqual(action.error, "")
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_text(), "precious contents")

    def test_executor_revalidates_destination_collision(self):
        action = self.action(status=ActionStatus.APPROVED)
        self.assertTrue(validate_action(action, str(self.root))[0])
        self.destination.parent.mkdir()
        self.destination.write_text("do not overwrite")
        self.assertFalse(execute_action(action, str(self.root)))
        self.assertEqual(action.status, ActionStatus.FAILED)
        self.assertIn("already exists", action.error)
        self.assertEqual(self.destination.read_text(), "do not overwrite")
        self.assertTrue(self.source.exists())

    def test_executor_revalidates_disappeared_source(self):
        action = self.action(status=ActionStatus.APPROVED)
        self.assertTrue(validate_action(action, str(self.root))[0])
        self.source.unlink()
        self.assertFalse(execute_action(action, str(self.root)))
        self.assertEqual(action.status, ActionStatus.FAILED)
        self.assertFalse(self.destination.exists())

    def index_and_queue(self):
        file = scan_file(str(self.source))
        file.content = "extracted contents"
        file.category = "Work"
        file.status = "classified"
        database.save_file(file)
        return database.save_action(self.action())

    def stored_action(self, action):
        with closing(database.get_connection()) as connection:
            return connection.execute(
                "SELECT status, error FROM actions WHERE id = ?", (action.id,)
            ).fetchone()

    def review(self, answer):
        with patch("builtins.input", return_value=answer), redirect_stdout(StringIO()):
            review_pending_actions(str(self.root))

    def test_review_persists_success_and_preserves_classification(self):
        action = self.index_and_queue()
        before = database.get_file_by_path(str(self.source))
        self.review("y")
        after = database.get_file_by_path(str(self.destination))
        for field in ("id", "content", "category", "status", "hash"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after["filename"], self.destination.name)
        self.assertEqual(after["is_present"], 1)
        self.assertIsNone(database.get_file_by_path(str(self.source)))
        self.assertEqual(self.stored_action(action), ("executed", ""))

    def test_execution_error_is_persisted_without_index_move(self):
        action = self.index_and_queue()
        before = database.get_all_files()
        with patch("actions.executor.shutil.move", side_effect=PermissionError("denied")):
            self.review("y")
        self.assertEqual(self.stored_action(action), ("failed", "denied"))
        self.assertEqual(database.get_all_files(), before)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_reject_and_skip_do_not_change_files_or_index(self):
        action = self.index_and_queue()
        before = database.get_all_files()
        self.review("s")
        self.assertEqual(self.stored_action(action)[0], "pending")
        self.review("n")
        self.assertEqual(self.stored_action(action)[0], "rejected")
        self.assertEqual(database.get_all_files(), before)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_collision_during_approval_is_persisted_as_failed(self):
        action = self.index_and_queue()

        def approve(_):
            self.destination.parent.mkdir()
            self.destination.write_text("new arrival")
            return "y"

        with patch("builtins.input", side_effect=approve), redirect_stdout(StringIO()):
            review_pending_actions(str(self.root))
        self.assertEqual(self.stored_action(action)[0], "failed")
        self.assertTrue(self.source.exists())
        self.assertEqual(self.destination.read_text(), "new arrival")
        self.assertIsNotNone(database.get_file_by_path(str(self.source)))


if __name__ == "__main__":
    unittest.main()
