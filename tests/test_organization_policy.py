from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from organization_policy import save_policy, load_policy, policy_view
from workspace import save_scope, DEFAULT_EXCLUSIONS
from models import ProposedAction
from scanner import scan_file
from actions.validator import validate_action
from actions.executor import execute_action
from web import dispatch
from library import library_page


class OrganizationPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def file(self, name, category='Work', status='classified'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('document')
        file = scan_file(str(path))
        file.category, file.status = category, status
        database.save_file(file)
        return path

    def save(self, folder='Sorted/Work', protected=None):
        return save_policy(self.root, [{'category': 'Work', 'folder': folder}], protected or [])

    def action(self, source, destination):
        return ProposedAction('move', str(source), str(self.root / destination), 'Organize', status='approved')

    def test_reviewed_policy_persists_and_increments_version(self):
        draft = policy_view(self.root)
        self.assertEqual(draft['version'], 0)
        self.assertIsNone(load_policy(self.root))
        self.save()
        first = load_policy(self.root)
        self.assertEqual(first.destinations, {'work': 'Sorted/Work'})
        self.assertEqual(first.version, 1)
        second = self.save('Existing/Contracts')
        self.assertEqual(second.version, 2)
        self.assertEqual(policy_view(self.root)['destinations'], second.destinations)
        self.assertFalse((self.root / 'Existing').exists())

    def test_exact_category_destination_and_filename_are_required(self):
        source = self.file('contract.txt')
        self.save()
        valid = self.action(source, 'Sorted/Work/contract.txt')
        self.assertTrue(validate_action(valid, str(self.root))[0])
        for destination in ('Other/contract.txt', 'Sorted/Work/renamed.txt',
                            'Sorted/Work/Subfolder/contract.txt'):
            with self.subTest(destination=destination):
                self.assertFalse(validate_action(self.action(source, destination), str(self.root))[0])
        self.assertTrue(execute_action(valid, str(self.root)))
        self.assertTrue((self.root / 'Sorted/Work/contract.txt').exists())

    def test_unknown_and_unclassified_files_stay_in_place(self):
        self.save()
        for name, category, status in (('unknown.txt','Other','classified'),
                                        ('pending.txt','Work','pending')):
            source = self.file(name, category, status)
            action = self.action(source, 'Sorted/Work/' + name)
            self.assertFalse(execute_action(action, str(self.root)))
            self.assertTrue(source.exists())

    def test_custom_protected_source_and_destination(self):
        source = self.file('Keep/contract.txt')
        self.save(protected=['Keep'])
        self.assertFalse(validate_action(self.action(source, 'Sorted/Work/contract.txt'), str(self.root))[0])
        with self.assertRaisesRegex(ValueError, 'Protected'):
            self.save('Keep', ['Keep'])

    def test_new_project_marker_is_rechecked_at_execution(self):
        source = self.file('Project/contract.txt')
        self.save()
        action = self.action(source, 'Sorted/Work/contract.txt')
        self.assertTrue(validate_action(action, str(self.root))[0])
        (self.root / 'Project/package.json').write_text('{}')
        self.assertFalse(execute_action(action, str(self.root)))
        self.assertIn('project', action.error)
        self.assertTrue(source.exists())

    def test_destination_project_is_protected(self):
        source = self.file('contract.txt')
        self.save('Sorted')
        (self.root / 'Sorted').mkdir()
        (self.root / 'Sorted/pyproject.toml').write_text('')
        action = self.action(source, 'Sorted/contract.txt')
        self.assertFalse(execute_action(action, str(self.root)))
        self.assertTrue(source.exists())

    def test_changed_policy_invalidates_old_proposals(self):
        source = self.file('contract.txt')
        self.save()
        action = self.action(source, 'Sorted/Work/contract.txt')
        self.save('Contracts')
        self.assertFalse(execute_action(action, str(self.root)))
        self.assertIn('Contracts', action.error)
        self.assertTrue(source.exists())

    def test_path_validation_and_scope_are_not_bypassed(self):
        (self.root / 'Docs').mkdir()
        save_scope(self.root, ['Docs'], True, DEFAULT_EXCLUSIONS)
        for folder in ('../escape', '/tmp/outside', 'Unselected/Work', 'Docs/node_modules', 'Docs/Tool.app'):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                self.save(folder)
        target = self.root / 'Docs/link'
        target.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.save('Docs/link/Work')
        self.save('Docs/Contracts')
        self.assertEqual(load_policy(self.root).destinations['work'], 'Docs/Contracts')

    def test_duplicate_categories_and_empty_rules_rejected(self):
        for rules in ([], [{'category':'Work','folder':'A'},{'category':' work ','folder':'B'}]):
            with self.assertRaises(ValueError):
                save_policy(self.root, rules, [])

    def test_dashboard_saves_and_returns_reviewed_policy(self):
        result = dispatch('save-policy', {'root': str(self.root),
            'rules': [{'category':'Work','folder':'Contracts'}], 'protected_folders': []})
        self.assertEqual(result['policy']['version'], 1)
        self.assertEqual(dispatch('state', {'root': str(self.root)})['policy'], result['policy'])

    def test_agent_reuses_policy_destinations_across_batches(self):
        first = self.file('first.txt')
        second = self.file('second.txt')
        self.save('Contracts')
        tools = SimpleNamespace(read_file=lambda **kw: {}, classify_path=lambda **kw: {},
                                propose_move=lambda **kw: ProposedAction('move', **kw))
        spec = importlib.util.spec_from_file_location('policy_agent_test',
            Path(__file__).resolve().parents[1] / 'app/agents/organizer.py')
        agent = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=lambda **kw: None),
                                     'tools.file_tools': tools}):
            spec.loader.exec_module(agent)
        for source in (first, second):
            candidates = set()
            listing = agent.call_tool('get_indexed_files', {'page':1}, str(self.root), candidates=candidates)
            row = next(row for row in listing['files'] if row['path'] == str(source))
            expected = str(self.root / 'Contracts' / source.name)
            self.assertEqual(row['policy_destination'], expected)
            proposed = agent.call_tool('propose_move', {'source': str(source), 'destination': expected,
                                      'reason': 'Follow reviewed rule'}, str(self.root), candidates=candidates)
            self.assertEqual(proposed.destination, expected)
        self.assertEqual(load_policy(self.root).version, 1)

    def test_batches_skip_organized_protected_and_pending_proposals_before_paging(self):
        self.save('Contracts', ['Keep'])
        self.file('Contracts/already.txt')
        self.file('Keep/protected.txt')
        pending = self.file('pending-proposal.txt')
        self.file('unmapped.txt', category='Other')
        next_file = self.file('zz-next.txt')
        database.save_action(ProposedAction('move', str(pending),
            str(self.root / 'Contracts' / pending.name), 'Already queued'))
        result = library_page(self.root, organization_candidates=True, page_size=1)
        self.assertEqual(result['pagination']['total'], 1)
        self.assertEqual(result['files'][0]['path'], str(next_file))


if __name__ == '__main__':
    unittest.main()
