from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from models import ProposedAction
from web import dispatch, selected_root, STATIC


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.base / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def call(self, operation, **data):
        return dispatch(operation, {'root': str(self.root), **data})

    def test_scan_preview_apply_and_search(self):
        path = self.root / 'invoice.txt'
        path.write_text('hello')
        self.assertEqual(self.call('scan')['report']['new_paths'], [str(path)])
        self.assertEqual(database.get_all_files(), [])
        self.assertEqual(len(self.call('apply')['files']), 1)
        self.assertEqual(self.call('search', query='invoice')['results'][0]['path'], str(path))

    def test_approval_uses_executor_and_updates_index(self):
        source = self.root / 'old.txt'
        destination = self.root / 'new.txt'
        source.write_text('hello')
        self.call('apply')
        action = database.save_action(ProposedAction('move', str(source), str(destination), 'Organize'))
        self.assertTrue(self.call('state')['actions'][0]['valid'])
        self.call('review', id=action.id, decision='y')
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(), 'hello')
        self.assertIsNotNone(database.get_file_by_path(str(destination)))
        with self.assertRaises(ValueError):
            self.call('review', id=action.id, decision='y')

    def test_outside_proposals_cannot_be_approved(self):
        source = self.base / 'outside.txt'
        source.write_text('hello')
        action = database.save_action(ProposedAction('move', str(source), str(self.root / 'new.txt'), 'Outside'))
        self.assertEqual(self.call('state')['actions'], [])
        with self.assertRaises(ValueError):
            self.call('review', id=action.id, decision='y')
        self.assertTrue(source.exists())

    def test_rejection_does_not_move(self):
        source = self.root / 'old.txt'
        source.write_text('hello')
        action = database.save_action(ProposedAction('move', str(source), str(self.root / 'new.txt'), 'Organize'))
        self.call('review', id=action.id, decision='n')
        self.assertTrue(source.exists())
        self.assertEqual(self.call('state')['actions'], [])

    def test_invalid_inputs_and_assets(self):
        for value in ('', None, str(self.root / 'missing')):
            with self.assertRaises(ValueError):
                selected_root(value)
        with self.assertRaises(ValueError):
            self.call('unknown')
        for filename in ('index.html', 'app.js', 'style.css'):
            self.assertTrue((STATIC / filename).is_file())


if __name__ == '__main__':
    unittest.main()
