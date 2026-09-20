from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from groups import freeze_selection, group_page
from group_review import proposal_page, review_group
from jobs import JobCancelled, JobManager
from models import ProposedAction
from organization_policy import save_policy
from scanner import scan_file
from web import dispatch


class GroupReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()
        save_policy(self.root, [{'category': 'Work', 'folder': 'Sorted'}], [])

    def add(self, name):
        path = self.root/name
        path.write_text('file contents '+name)
        file = scan_file(str(path))
        file.category, file.status = 'Work', 'classified'
        database.save_file(file)
        return database.get_file_by_path(str(path))['id']

    def draft(self, count=3):
        for i in range(count):
            self.add(f'{i}.txt')
        group = group_page(self.root)['groups'][0]
        bid = freeze_selection(self.root, group['id'], group['version'])
        for member in group_page(self.root, group_id=group['id'])['members']:
            database.save_action(ProposedAction('move', member['path'], member['destination'], 'Group move'))
        card = proposal_page(self.root)['group_actions'][0]
        return bid, card['token']

    def state(self):
        return dispatch('state', {'root': str(self.root)})

    def test_one_approval_moves_all_exact_members_and_hides_individual_cards(self):
        bid, token = self.draft()
        state = self.state()
        self.assertEqual(state['actions'], [])
        self.assertEqual(state['action_pagination']['total'], 0)
        self.assertEqual(state['group_actions'][0]['file_count'], 3)
        self.add('later.txt')
        result = review_group(self.root, bid, token, 'y')
        self.assertEqual(result['group_move']['moved'], 3)
        self.assertTrue((self.root/'later.txt').exists())
        for i in range(3):
            target = self.root/'Sorted'/f'{i}.txt'
            self.assertTrue(target.exists())
            self.assertIsNotNone(database.get_file_by_path(str(target)))
        self.assertEqual(database.get_pending_actions(), [])
        with self.assertRaises(ValueError):
            review_group(self.root, bid, token, 'y')
        self.assertEqual(proposal_page(self.root, batch_id=bid)['status'], 'executed')

    def test_reject_and_wrong_token_never_move(self):
        bid, token = self.draft()
        with self.assertRaises(ValueError):
            review_group(self.root, bid, 'wrong', 'y')
        with self.assertRaises(ValueError):
            review_group(self.root.parent, bid, token, 'y')
        review_group(self.root, bid, token, 'n')
        self.assertEqual(len(list(self.root.glob('*.txt'))), 3)
        self.assertEqual(database.get_pending_actions(), [])
        self.assertEqual(self.state()['pending_group_count'], 0)

    def test_single_endpoint_cannot_approve_hidden_group_child(self):
        self.draft()
        action = database.get_pending_actions()[0]
        with self.assertRaisesRegex(ValueError, 'group'):
            dispatch('review', {'root': str(self.root), 'id': action.id, 'decision': 'y'})
        self.assertTrue(Path(action.source).exists())

    def test_preflight_checks_all_files_before_any_move(self):
        bid, token = self.draft()
        (self.root/'2.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            review_group(self.root, bid, token, 'y')
        self.assertTrue((self.root/'0.txt').exists())
        self.assertFalse((self.root/'Sorted').exists())
        self.assertEqual(proposal_page(self.root, batch_id=bid)['status'], 'needs_review')

    def test_policy_and_manifest_changes_invalidate_confirmation(self):
        bid, token = self.draft()
        save_policy(self.root, [{'category': 'Work', 'folder': 'Other'}], [])
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            review_group(self.root, bid, token, 'y')
        self.assertFalse((self.root/'Other').exists())

    def test_cancel_after_first_move_records_partial_outcome_without_replay(self):
        bid, token = self.draft()
        def progress(completed, total, message, **kwargs):
            if completed == 1:
                raise JobCancelled('cancel')
        with self.assertRaises(JobCancelled):
            review_group(self.root, bid, token, 'y', progress=progress)
        result = proposal_page(self.root, batch_id=bid)
        self.assertEqual([m['outcome'] for m in result['members']], ['executed', 'pending', 'pending'])
        with self.assertRaises(ValueError):
            review_group(self.root, bid, token, 'y')
        self.assertTrue((self.root/'Sorted/0.txt').exists())
        self.assertTrue((self.root/'1.txt').exists())

    def test_member_pagination_and_job_integration(self):
        bid, token = self.draft(23)
        page1 = proposal_page(self.root, batch_id=bid)
        page2 = proposal_page(self.root, batch_id=bid, member_page=2)
        self.assertEqual([len(page1['members']), len(page2['members'])], [20, 3])
        manager = JobManager(dispatch)
        self.addCleanup(manager.close)
        job = manager.submit('review-group', {'root': str(self.root), 'batch_id': bid, 'token': token, 'decision': 'y'})
        manager.pool.shutdown(wait=True)
        from jobs import get_job
        self.assertEqual(get_job(job['id'])['status'], 'succeeded')
        self.assertEqual(get_job(job['id'])['result']['group_move']['moved'], 23)
        with self.assertRaisesRegex(ValueError, 'replayed'):
            manager.resume(job['id'])

    def test_database_failure_after_move_keeps_receipt_and_does_not_replay(self):
        bid, token = self.draft()
        with closing(database.get_connection()) as db, db:
            db.execute("""CREATE TRIGGER fail_index BEFORE UPDATE OF path ON files
                BEGIN SELECT RAISE(ABORT,'index failure'); END""")
        with self.assertRaisesRegex(database.sqlite3.IntegrityError, 'index failure'):
            review_group(self.root, bid, token, 'y')
        self.assertTrue((self.root/'Sorted/0.txt').exists())
        self.assertEqual(proposal_page(self.root, batch_id=bid)['members'][0]['outcome'], 'executed')
        card = proposal_page(self.root)['group_actions'][0]
        self.assertIn('1 moved', card['message'])
        self.assertEqual(card['status'], 'needs_review')
        with self.assertRaises(ValueError):
            review_group(self.root, bid, token, 'y')

    def test_changed_manifest_and_destination_collision_do_not_move(self):
        bid, token = self.draft()
        with closing(database.get_connection()) as db, db:
            db.execute("UPDATE organization_batch_members SET snapshot=json_set(snapshot,'$.size',99) WHERE batch_id=?", (bid,))
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            review_group(self.root, bid, token, 'y')
        self.assertFalse((self.root/'Sorted').exists())

    def test_restart_marks_uncertain_batch_without_replaying(self):
        bid, token = self.draft()
        with closing(database.get_connection()) as db, db:
            db.execute("INSERT INTO group_reviews(batch_id,status) VALUES (?,'running')", (bid,))
        manager = JobManager(dispatch)
        self.addCleanup(manager.close)
        self.assertEqual(proposal_page(self.root, batch_id=bid)['status'], 'needs_review')
        self.assertFalse((self.root/'Sorted').exists())


if __name__ == '__main__':
    unittest.main()
