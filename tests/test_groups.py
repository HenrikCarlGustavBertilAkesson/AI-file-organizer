from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from groups import group_page, freeze_selection, batch_page
from models import File
from organization_policy import save_policy
from workspace import save_scope, DEFAULT_EXCLUSIONS


class GroupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'workspace'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()
        save_policy(self.root, [{'category': 'Work', 'folder': 'Work'}], ['Protected'])

    def add(self, name, category='Work', **kwargs):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('unchanged file')
        database.save_file(File(str(path), path.name, '.txt', 10, 1,
                               category=category, status=kwargs.pop('status', 'classified'), **kwargs))
        return database.get_file_by_path(str(path))['id']

    def test_groups_stable_across_batches_and_members_paginated(self):
        for i in range(125):
            self.add(f'{i}.txt', ' WORK ' if i % 2 else 'Work')
        first = group_page(self.root)['groups'][0]
        self.assertEqual((first['file_count'], first['total_bytes'], first['needs_move']), (125, 1250, 125))
        pages = [group_page(self.root, group_id=first['id'], page=p) for p in (1, 2, 3)]
        self.assertEqual([len(p['members']) for p in pages], [50, 50, 25])
        self.assertEqual(len({m['id'] for p in pages for m in p['members']}), 125)
        self.assertEqual(group_page(self.root)['groups'][0]['version'], first['version'])
        self.add('later.txt')
        latest = group_page(self.root)['groups'][0]
        self.assertEqual(latest['id'], first['id'])
        self.assertGreater(latest['version'], first['version'])
        self.assertEqual(latest['file_count'], 126)
        self.assertNotIn('content', pages[0]['members'][0])

    def test_states_scope_and_classification_changes(self):
        self.add('Work/already.txt')
        self.add('Protected/private.txt')
        self.add('Project/code.txt')
        (self.root / 'Project/package.json').write_text('{}')
        self.add('loose.txt')
        self.add('unknown.txt', 'New category')
        self.add('pending.txt', status='pending')
        self.add('absent.txt', is_present=False)
        self.add('node_modules/ignored.txt')
        groups = {g['category']: g for g in group_page(self.root)['groups']}
        self.assertEqual(groups['work']['file_count'], 4)
        self.assertEqual(groups['work']['organized'], 1)
        self.assertEqual(groups['work']['blocked'], 2)
        self.assertEqual(groups['new category']['unmapped'], 1)
        gid = groups['work']['id']
        save_scope(self.root, ['Work'], False, DEFAULT_EXCLUSIONS)
        remaining = group_page(self.root)['groups']
        self.assertEqual(sum(g['file_count'] for g in remaining), 1)
        self.assertEqual(next(g for g in remaining if g['id']==gid)['organized'], 1)

    def test_frozen_drafts_do_not_follow_group_or_policy_changes(self):
        fid = self.add('first.txt')
        group = group_page(self.root)['groups'][0]
        bid = freeze_selection(self.root, group['id'], group['version'])
        original = batch_page(self.root, bid)
        self.assertEqual(original['batch']['status'], 'draft')
        self.add('later.txt')
        with self.assertRaisesRegex(ValueError, 'changed'):
            freeze_selection(self.root, group['id'], group['version'])
        save_policy(self.root, [{'category': 'Work', 'folder': 'Documents'}], [])
        group_page(self.root)
        self.assertEqual(batch_page(self.root, bid), original)
        self.assertEqual(original['members'][0]['id'], fid)
        self.assertTrue(original['members'][0]['destination'].endswith('/Work/first.txt'))
        self.assertEqual((self.root / 'first.txt').read_text(), 'unchanged file')
        with closing(database.get_connection()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM actions').fetchone()[0], 0)

    def test_explicit_selection_deduplicates_and_rejects_invalid_members(self):
        fid = self.add('first.txt')
        protected = self.add('Protected/private.txt')
        group = group_page(self.root)['groups'][0]
        bid = freeze_selection(self.root, group['id'], group['version'], operation='trash', file_ids=[fid, fid])
        self.assertEqual(batch_page(self.root, bid)['pagination']['total'], 1)
        for ids in ([protected], [999999], [], [True]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                freeze_selection(self.root, group['id'], group['version'], file_ids=ids)
        with self.assertRaises(ValueError):
            batch_page(self.root.parent, bid)
        with self.assertRaises(ValueError):
            group_page(self.root, page_size=101)

    def test_reclassification_missing_files_and_policy_invalidate_versions(self):
        fid = self.add('first.txt')
        group = group_page(self.root)['groups'][0]
        with closing(database.get_connection()) as db, db:
            db.execute("UPDATE files SET category='Travel' WHERE id=?", (fid,))
        updated = {g['category']: g for g in group_page(self.root)['groups']}
        self.assertEqual(updated['work']['id'], group['id'])
        self.assertEqual(updated['work']['file_count'], 0)
        self.assertEqual(updated['travel']['file_count'], 1)
        with closing(database.get_connection()) as db, db:
            db.execute('UPDATE files SET is_present=0 WHERE id=?', (fid,))
        self.assertEqual(sum(g['file_count'] for g in group_page(self.root)['groups']), 0)


if __name__ == '__main__':
    unittest.main()
