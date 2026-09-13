from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from groups import group_page, batch_page
from models import File, ProposedAction
from organization_policy import save_policy


class GroupAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()
        save_policy(self.root, [{'category': 'Work', 'folder': 'Sorted'}], ['Protected'])
        fake_tools = SimpleNamespace(read_file=Mock(), classify_path=Mock(),
                                     propose_move=lambda **kwargs: ProposedAction('move', **kwargs))
        spec = importlib.util.spec_from_file_location('group_agent_test',
            Path(__file__).resolve().parents[1] / 'app/agents/organizer.py')
        self.agent = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=lambda **kwargs: None),
                                     'tools.file_tools': fake_tools}):
            spec.loader.exec_module(self.agent)
        self.candidates, self.seen = set(), {}

    def add(self, name, confidence=0.9, category='Work', status='classified'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('content must not move')
        database.save_file(File(str(path), path.name, '.txt', 21, 0,
                               confidence=confidence, category=category, status=status))
        return database.get_file_by_path(str(path))['id']

    def call(self, name, args, **kwargs):
        return self.agent.call_tool(name, args, str(self.root), candidates=self.candidates,
                                    seen_groups=self.seen, **kwargs)

    def browse(self, **kwargs):
        group = self.call('list_category_groups', {'page': 1})['groups'][0]
        return self.call('list_group_members', {'group_id': group['id'], 'page': 1}, **kwargs)

    def propose(self, result, ids=None, **kwargs):
        return self.call('propose_group_move', {'group_id': result['group']['id'],
                         'version': result['group']['version'],
                         'destination': result['group']['destination'],
                         'file_ids': ids if ids is not None else [m['id'] for m in result['members']]}, **kwargs)

    def test_bounded_discovery_and_review_flags(self):
        for i in range(24):
            self.add(f'{i}.txt', confidence=0.2 if i == 0 else 0.9)
        result = self.browse(max_files=3)
        self.assertEqual(len(result['members']), 3)
        self.assertEqual(result['pagination']['total'], 24)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['members'][0]['membership_needs_review'])
        self.assertNotIn('content', result['members'][0])
        proposed = self.propose(result, remaining_proposals=3)
        self.assertEqual(len(proposed.actions), 3)
        self.assertIn('Uncertain', proposed.actions[0].reason)
        draft = batch_page(self.root, proposed.batch_id)
        self.assertEqual(draft['pagination']['total'], 3)
        self.assertEqual(draft['batch']['status'], 'draft')
        self.assertFalse((self.root / 'Sorted').exists())

    def test_filtering_before_pagination_and_repeated_runs(self):
        for i in range(22):
            self.add(f'Sorted/{i}.txt')
        self.add('Protected/secret.txt')
        self.add('pending.txt', status='pending')
        self.add('one.txt')
        first = self.browse()
        self.assertEqual(len(first['members']), 1)
        proposal = self.propose(first)
        for action in proposal.actions:
            database.save_action(action)
        self.add('two.txt')
        self.candidates, self.seen = set(), {}
        second = self.browse()
        self.assertEqual(second['group']['id'], first['group']['id'])
        self.assertEqual([m['filename'] for m in second['members']], ['two.txt'])
        self.assertEqual(second['group']['destination'], first['group']['destination'])

    def test_stale_unseen_over_budget_and_collision_proposals_do_not_persist(self):
        first = self.add('one.txt')
        self.add('two.txt')
        result = self.browse()
        for ids, limit in (([999], 10), ([first, first], 10), ([first], 0), ([True], 10)):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.propose(result, ids, remaining_proposals=limit)
        save_policy(self.root, [{'category': 'Work', 'folder': 'Other'}], [])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.propose(result)
        with closing(database.get_connection()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM organization_batches').fetchone()[0], 0)
        self.add('A/same.txt')
        self.add('B/same.txt')
        result = self.browse()
        ids = [m['id'] for m in result['members'] if m['filename']=='same.txt']
        with self.assertRaisesRegex(ValueError, 'collide'):
            self.propose(result, ids)

    def test_unmapped_protected_and_outside_groups_cannot_be_proposed(self):
        self.add('Protected/secret.txt')
        self.assertEqual(self.browse()['members'], [])
        with self.assertRaises(ValueError):
            self.call('list_group_members', {'group_id': 999, 'page': 1})
        self.add('unknown.txt', category='Unknown')
        summaries = self.call('list_category_groups', {'page': 1})['groups']
        unknown = next(g for g in summaries if g['category']=='unknown')
        self.assertIsNone(unknown['destination'])
        result = self.call('list_group_members', {'group_id': unknown['id'], 'page': 1})
        self.assertEqual(result['members'], [])
        with self.assertRaises(ValueError):
            self.call('propose_trash', {'path': str(self.root/'unknown.txt')})

    def test_classification_refreshes_membership_and_cancellation_creates_no_draft(self):
        fid = self.add('new.txt', status='pending')
        self.assertEqual(self.call('list_category_groups', {'page': 1})['groups'], [])
        self.call('get_indexed_files', {'page': 1})
        def classify(path):
            with closing(database.get_connection()) as db, db:
                db.execute("UPDATE files SET status='classified',category='Work',confidence=0.9 WHERE id=?", (fid,))
            return {'category': 'Work'}
        with patch.object(self.agent, 'classify_path', side_effect=classify):
            self.call('classify_path', {'path': str(self.root / 'new.txt')})
        result = self.browse()
        self.assertEqual(result['members'][0]['id'], fid)
        from jobs import JobCancelled
        with self.assertRaises(JobCancelled):
            self.propose(result, progress=Mock(side_effect=JobCancelled('cancelled')))
        with closing(database.get_connection()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM organization_batches').fetchone()[0], 0)

    def test_changed_scope_existing_target_and_reserved_destination_rejected(self):
        self.add('one.txt')
        result = self.browse()
        target = result['members'][0]['destination']
        with self.assertRaisesRegex(ValueError, 'collide'):
            self.propose(result, proposed_destinations={target})
        Path(target).parent.mkdir()
        Path(target).write_text('existing')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.propose(result)
        from workspace import save_scope, DEFAULT_EXCLUSIONS
        save_scope(self.root, ['Sorted'], False, DEFAULT_EXCLUSIONS)
        with self.assertRaises(ValueError):
            self.propose(result)
        self.assertEqual(Path(target).read_text(), 'existing')

    def test_agent_loop_persists_group_and_stops_at_file_proposal_limit(self):
        for i in range(3):
            self.add(f'{i}.txt')
        group = group_page(self.root)['groups'][0]
        members = group_page(self.root, group_id=group['id'])['members']
        class Call:
            type = 'function_call'
            def __init__(self, name, arguments):
                self.name, self.arguments, self.call_id = name, json.dumps(arguments), name
            def model_dump(self):
                return vars(self) | {'type': self.type}
        calls = [Call('list_category_groups', {'page': 1}),
                 Call('list_group_members', {'group_id': group['id'], 'page': 1}),
                 Call('propose_group_move', {'group_id': group['id'], 'version': group['version'],
                      'destination': group['destination'], 'file_ids': [m['id'] for m in members[:2]]})]
        response = SimpleNamespace(output=calls, output_text='', usage=SimpleNamespace(input_tokens=10, output_tokens=5))
        create = Mock(return_value=response)
        self.agent.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        result = self.agent.run_agent('Group work documents', str(self.root), max_proposals=2,
                                      proposal_callback=database.save_action)
        self.assertEqual(len(result.proposed_actions), 2)
        self.assertEqual(result.group_proposals[0]['file_count'], 2)
        self.assertEqual(len(database.get_pending_actions()), 2)
        self.assertEqual(create.call_count, 1)
        self.assertIn('Proposal limit', result.message)
        self.assertTrue(all((self.root/f'{i}.txt').exists() for i in range(3)))
        tool_names = {t['name'] for t in create.call_args.kwargs['tools']}
        self.assertFalse(any('delete' in n or 'trash' in n for n in tool_names))
        self.assertIn('Do not recommend deletion', self.agent.SYSTEM_PROMPT)


if __name__ == '__main__':
    unittest.main()
