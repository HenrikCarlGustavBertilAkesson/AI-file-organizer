from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from library import library_page
from models import File, ProposedAction
from web import snapshot
from workspace import save_scope, DEFAULT_EXCLUSIONS


class LibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()
        database.save_files([File(path=str(self.root / f'{index:03}.txt'),
            filename=f'{index:03}.txt', extension='.txt', size=1, modified=0,
            content='contract ' + 'private extracted contents ' * 100,
            status='classified' if index % 2 else 'pending',
            category='Work' if index % 2 else 'Finance', description='D' * 1000)
            for index in range(125)])

    def test_pages_are_bounded_stable_and_have_full_totals(self):
        results = [library_page(self.root, page=page) for page in (1, 2, 3)]
        self.assertEqual([len(result['files']) for result in results], [50, 50, 25])
        paths = [row['path'] for result in results for row in result['files']]
        self.assertEqual(len(set(paths)), 125)
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(results[1]['pagination']['total'], 125)
        self.assertEqual(results[1]['summary'], {'total': 125, 'pending': 63})
        for result in results:
            for row in result['files']:
                self.assertNotIn('content', row)
                self.assertNotIn('hash', row)
                self.assertLessEqual(len(row['description']), 400)
        self.assertEqual(library_page(self.root, page=999)['pagination']['page'], 3)

    def test_filters_apply_before_pagination(self):
        result = library_page(self.root, status='classified', category='Work', page_size=25, page=2)
        self.assertEqual(result['pagination']['total'], 62)
        self.assertEqual(len(result['files']), 25)
        self.assertTrue(all(row['status']=='classified' and row['category']=='Work' for row in result['files']))
        self.assertEqual(library_page(self.root, status='pending', category='Work')['pagination']['total'], 0)

    def test_ranked_search_is_paginated_and_filtered(self):
        first = library_page(self.root, query='contract', status='classified', page_size=25)
        second = library_page(self.root, query='contract', status='classified', page_size=25, page=2)
        self.assertEqual(first['pagination']['total'], 62)
        self.assertFalse(set(row['path'] for row in first['files']) & set(row['path'] for row in second['files']))
        self.assertTrue(all('[contract]' in row['snippet'] for row in first['files']))
        self.assertEqual(library_page(self.root, query='!!!')['files'], [])
        self.assertEqual(library_page(self.root, category="' OR 1=1 --")['files'], [])

    def test_missing_scope_and_outside_root_filters(self):
        with closing(database.get_connection()) as connection, connection:
            connection.execute('UPDATE files SET is_present=0 WHERE filename=?', ('000.txt',))
        missing = library_page(self.root, status='missing')
        self.assertEqual(missing['pagination']['total'], 1)
        self.assertEqual(missing['summary']['total'], 124)
        database.save_file(File(str(self.root.parent / 'outside.txt'), 'outside.txt', '.txt', 0, 0))
        self.assertEqual(library_page(self.root)['pagination']['total'], 124)
        save_scope(self.root, [], False, DEFAULT_EXCLUSIONS)
        self.assertEqual(library_page(self.root)['pagination']['total'], 0)
        self.assertEqual(library_page(self.root, status='missing')['files'], [])

    def test_actions_are_bounded_and_counted(self):
        for index in range(45):
            database.save_action(ProposedAction('move', str(self.root / f'{index:03}.txt'),
                                               str(self.root / f'new{index}.txt'), 'Organize'))
        first = snapshot(self.root)
        second = snapshot(self.root, {'action_page': 2})
        self.assertEqual(len(first['actions']), 20)
        self.assertEqual(first['action_pagination']['total'], 45)
        self.assertFalse(set(row['id'] for row in first['actions']) & set(row['id'] for row in second['actions']))

    def test_snapshot_does_not_load_full_file_or_action_tables(self):
        with patch('database.get_all_files', side_effect=AssertionError('Unbounded query')), \
             patch('database.get_pending_actions', side_effect=AssertionError('Unbounded proposals')):
            result = snapshot(self.root)
        self.assertEqual(len(result['files']), 50)

    def test_invalid_pagination_and_empty_database(self):
        for options in ({'page': 0}, {'page': -1}, {'page': True}, {'page_size': 101},
                        {'page_size': '50'}, {'status': 'invented'}, {'action_page': 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                library_page(self.root, **options)
        with closing(database.get_connection()) as connection, connection:
            connection.execute('DELETE FROM files')
        result = library_page(self.root)
        self.assertEqual(result['files'], [])
        self.assertEqual(result['pagination'], {'page': 1, 'page_size': 50, 'total': 0, 'pages': 1})

    def test_metadata_projection_and_unknown_column_rejection(self):
        rows = database.get_all_files(columns=('path', 'hash', 'is_present'))
        self.assertEqual(set(rows[0]), {'path', 'hash', 'is_present'})
        with self.assertRaises(ValueError):
            database.get_all_files(columns=('path; DROP TABLE files',))


if __name__ == '__main__':
    unittest.main()
