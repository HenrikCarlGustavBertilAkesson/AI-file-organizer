from pathlib import Path
import importlib.util
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from workspace import inventory, save_scope, load_scope, DEFAULT_EXCLUSIONS
from scanner import scan_file, scan_directory, ScanError
from reconciliation import reconcile_directory
from process_pending import process_pending
from search import search_files
from actions.validator import validate_action
from models import ProposedAction
from web import dispatch


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'Desktop'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def file(self, relative, contents='hello'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def scope(self, folders=None, loose=True):
        return save_scope(self.root, folders or [], loose, DEFAULT_EXCLUSIONS)

    def test_inventory_uses_metadata_and_excludes_generated_content(self):
        self.file('loose.txt', 'abc')
        self.file('Docs/report.txt', 'abcd')
        self.file('Projects/app/package.json', '{}')
        self.file('Projects/app/.git/config')
        self.file('Projects/app/node_modules/lib.js')
        self.file('Tool.app/Contents/data')
        self.file('.venv/lib/data')
        (self.root / 'alias').symlink_to(self.root / 'Docs', target_is_directory=True)
        with patch('pathlib.Path.open', side_effect=AssertionError('Contents must not be read')), \
             patch('scanner.calculate_hash', side_effect=AssertionError('Must not hash')):
            result = inventory(self.root)
        groups = {group['name']: group for group in result['groups']}
        self.assertEqual(groups['.']['files'], 1)
        self.assertEqual(groups['.']['bytes'], 3)
        self.assertEqual(groups['Docs']['bytes'], 4)
        self.assertTrue(groups['Projects']['project'])
        self.assertEqual(groups['Projects']['files'], 1)
        self.assertEqual(len(result['skipped']), 5)
        self.assertTrue(result['complete'])

    def test_inventory_permission_failure_is_visible(self):
        self.file('Private/file.txt')
        import os
        real_scandir = os.scandir
        def scandir(path):
            if Path(path) == self.root / 'Private':
                raise PermissionError('Access denied')
            return real_scandir(path)
        with patch('workspace.os.scandir', side_effect=scandir):
            result = inventory(self.root)
        self.assertFalse(result['complete'])
        self.assertEqual(result['errors'][0]['path'], str(self.root / 'Private'))

    def test_scope_persists_and_rejects_escape(self):
        self.file('Docs/file.txt')
        scope = self.scope(['Docs'], loose=False)
        self.assertEqual(load_scope(self.root), scope)
        self.assertTrue(scope.allows(self.root / 'Docs/new/future.txt'))
        for path in (self.root / 'loose.txt', self.root / 'Other/file.txt',
                     self.root / 'Docs/node_modules/file', self.root / '../outside.txt'):
            self.assertFalse(scope.allows(path))
        with self.assertRaises(ValueError):
            self.scope(['../outside'])
        with self.assertRaises(ValueError):
            save_scope(self.root, [], True, ['../escape'])

    def test_excluded_records_are_not_marked_missing_or_processed(self):
        included = self.file('Docs/report.txt', 'invoice')
        excluded = self.file('Projects/code.txt', 'invoice')
        for path in (included, excluded):
            file = scan_file(str(path))
            file.content = 'invoice'
            file.status = 'pending'
            database.save_file(file)
        before = database.get_file_by_path(str(excluded))
        self.scope(['Docs'], loose=False)
        excluded.unlink()
        result = reconcile_directory(str(self.root), apply=True)
        self.assertEqual(result.missing_paths, [])
        self.assertEqual(database.get_file_by_path(str(excluded)), before)
        self.assertEqual([r.path for r in search_files(str(self.root), 'invoice')], [str(included)])
        def process(file):
            self.assertEqual(file.path, str(included))
            file.status = 'classified'
            return file
        with patch('process_pending.process_file', side_effect=process) as worker:
            process_pending(str(self.root))
        self.assertEqual(worker.call_count, 1)
        included.unlink()
        self.assertEqual(reconcile_directory(str(self.root)).missing_paths, [str(included)])

    def test_scanner_never_hashes_excluded_files(self):
        allowed = self.file('Docs/file.txt')
        self.file('Docs/node_modules/data')
        self.file('Projects/private.txt')
        scope = self.scope(['Docs'], loose=False)
        from scanner import calculate_hash
        with patch('scanner.calculate_hash', wraps=calculate_hash) as hashing:
            result = scan_directory(str(self.root), scope=scope)
        self.assertEqual([file.path for file in result], [str(allowed)])
        self.assertEqual(hashing.call_count, 1)

    def test_excluded_unreadable_directory_does_not_abort_scan(self):
        self.file('Docs/file.txt')
        self.file('Private/file.txt')
        scope = self.scope(['Docs'], loose=False)
        import os
        real_scandir = os.scandir
        def scandir(path):
            if Path(path) == self.root / 'Private':
                raise PermissionError('Access denied')
            return real_scandir(path)
        with patch('scanner.os.scandir', side_effect=scandir):
            self.assertEqual(len(scan_directory(str(self.root), scope=scope)), 1)
            self.scope(['Docs', 'Private'], loose=False)
            with self.assertRaises(ScanError):
                reconcile_directory(str(self.root), apply=True)

    def test_validator_and_dashboard_honor_scope(self):
        source = self.file('Docs/file.txt')
        self.file('Projects/file.txt')
        dispatch('save-scope', {'root': str(self.root), 'folders': ['Docs'],
                               'loose_files': False, 'exclusions': DEFAULT_EXCLUSIONS})
        action = ProposedAction('move', str(source), str(self.root / 'Projects/new.txt'), 'Move')
        self.assertFalse(validate_action(action, str(self.root))[0])
        dispatch('apply', {'root': str(self.root)})
        state = dispatch('state', {'root': str(self.root)})
        self.assertEqual([row['path'] for row in state['files']], [str(source)])
        preview = dispatch('inventory', {'root': str(self.root)})
        self.assertEqual(preview['inventory']['scope']['folders'], ['Docs'])

    def test_empty_scope_does_not_mark_anything_missing(self):
        path = self.file('file.txt')
        database.save_file(scan_file(str(path)))
        self.scope([], loose=False)
        path.unlink()
        self.assertEqual(reconcile_directory(str(self.root), apply=True).missing_paths, [])
        self.assertEqual(database.get_file_by_path(str(path))['is_present'], 1)

    def test_agent_rejects_excluded_reads_before_tool_execution(self):
        self.file('Docs/file.txt')
        excluded = self.file('Projects/private.txt')
        self.scope(['Docs'], loose=False)
        fake_tools = SimpleNamespace(**{name: Mock() for name in (
            'list_files', 'read_file', 'get_indexed_files', 'classify_path', 'propose_move')})
        spec = importlib.util.spec_from_file_location('scoped_agent_test',
            Path(__file__).resolve().parents[1] / 'app/agents/organizer.py')
        agent = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=lambda: None),
                                     'tools.file_tools': fake_tools}):
            spec.loader.exec_module(agent)
        for name in ('read_file', 'classify_path'):
            with self.assertRaisesRegex(ValueError, 'scope'):
                agent.call_tool(name, {'path': str(excluded)}, str(self.root))
        fake_tools.read_file.assert_not_called()
        fake_tools.classify_path.assert_not_called()


if __name__ == '__main__':
    unittest.main()
