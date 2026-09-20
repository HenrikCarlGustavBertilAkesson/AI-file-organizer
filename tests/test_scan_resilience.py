from contextlib import closing
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from scanner import scan_file, calculate_hash, FileChangedError
from reconciliation import reconcile_directory
from scan_health import load_issues, verification_error
from organization_policy import save_policy
from groups import group_page
from library import library_page
from actions.validator import validate_action
from models import ProposedAction, ScanStats
from jobs import JobCancelled, JobManager, get_job
from web import dispatch


class ResilientScanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()
        save_policy(self.root, [{'category': 'Work', 'folder': 'Sorted'}], [])

    def indexed(self, name='receipt.pdf'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'%PDF test bytes: hashing does not parse formats')
        file = scan_file(str(path))
        file.category, file.status = 'Work', 'classified'
        database.save_file(file)
        return path

    def test_one_metadata_change_retries_and_succeeds_on_same_handle(self):
        path = self.indexed()
        calls = []
        def hashing(handle):
            self.assertTrue(hasattr(handle, 'fileno'))
            value = calculate_hash(handle)
            calls.append(handle.fileno())
            if len(calls) == 1:
                # Changes ctime without changing contents or modification time.
                os.chmod(path, 0o400)
            return value
        stats = ScanStats()
        with patch('scanner.calculate_hash', side_effect=hashing), patch('scanner.time.sleep'):
            file = scan_file(str(path), stats=stats)
        self.assertEqual(stats.retries, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(file.hash, database.get_file_by_path(str(path))['hash'])

    def test_path_replacement_is_detected_even_with_identical_content(self):
        path = self.indexed()
        def hashing(handle):
            value = calculate_hash(handle)
            replacement = self.root / 'replacement'
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
            return value
        with patch('scanner.calculate_hash', side_effect=hashing), patch('scanner.time.sleep'):
            with self.assertRaises(FileChangedError) as error:
                scan_file(str(path))
        self.assertIn('file identity', error.exception.changed_fields)
        self.assertEqual(error.exception.attempts, 3)

    def test_partial_apply_preserves_failed_record_and_applies_good_updates(self):
        bad = self.indexed('bad.pdf')
        good = self.indexed('good.txt')
        original = database.get_file_by_path(str(bad))
        good.write_text('new contents')
        real = scan_file
        def scan(path, **kwargs):
            if path == str(bad):
                raise PermissionError('denied')
            return real(path, **kwargs)
        with patch('scanner.scan_file', side_effect=scan):
            result = reconcile_directory(self.root, apply=True, full_verification=True)
        self.assertFalse(result.scan.complete)
        self.assertEqual(result.modified_paths, [str(good)])
        self.assertEqual(database.get_file_by_path(str(bad)), original)
        self.assertEqual(database.get_file_by_path(str(good))['status'], 'pending')
        action = ProposedAction('move', str(bad), str(self.root/'Sorted'/bad.name), 'Move')
        self.assertIn('verification required', validate_action(action, str(self.root))[1])
        self.assertEqual(group_page(self.root)['groups'][0]['blocked'], 1)
        self.assertNotIn(str(bad), [r['path'] for r in library_page(self.root, organization_candidates=True)['files']])
        with patch('scanner.calculate_hash', wraps=calculate_hash) as hashing:
            recovered = reconcile_directory(self.root)
        self.assertTrue(recovered.scan.complete)
        self.assertEqual(hashing.call_count, 1)  # bad's unchanged metadata cannot reuse its hash
        self.assertEqual(load_issues(), [])
        self.assertTrue(validate_action(action, str(self.root))[0])

    def test_unreadable_directory_preserves_descendants_and_defers_moves(self):
        bad = self.indexed('Private/old.pdf')
        moved = self.indexed('old.txt')
        moved.rename(self.root/'renamed.txt')
        original = database.get_file_by_path(str(bad))
        actual_walk = os.walk
        def walk(root, onerror, followlinks):
            def error(error):
                onerror(error)
            for directory, dirs, files in actual_walk(root, onerror=error, followlinks=followlinks):
                if Path(directory) == self.root:
                    dirs.remove('Private')
                    onerror(PermissionError(13, 'denied', str(self.root/'Private')))
                yield directory, dirs, files
        with patch('scanner.os.walk', side_effect=walk):
            result = reconcile_directory(self.root, apply=True)
        self.assertEqual(result.missing_paths, [])
        self.assertEqual(result.probable_moves, [])
        self.assertEqual(database.get_file_by_path(str(bad)), original)
        self.assertTrue(verification_error(bad))
        self.assertTrue(reconcile_directory(self.root).scan.complete)
        self.assertFalse(verification_error(bad))

    def test_cancel_during_retry_stops_without_index_repairs(self):
        path = self.indexed()
        before = database.get_all_files()
        def changed(handle):
            result = calculate_hash(handle)
            path.write_text(path.read_text()+'x')
            return result
        def progress(*args, **kwargs):
            if kwargs.get('message', '').startswith('Retrying'):
                raise JobCancelled('stop')
        with patch('scanner.calculate_hash', side_effect=changed), patch('scanner.time.sleep'):
            with self.assertRaises(JobCancelled):
                reconcile_directory(self.root, apply=True, full_verification=True, progress=progress)
        self.assertEqual(database.get_all_files(), before)
        self.assertTrue(verification_error(path))

    def test_failed_scan_job_finishes_with_visible_issues(self):
        self.indexed()
        manager = JobManager(dispatch)
        self.addCleanup(manager.close)
        with patch('scanner.scan_file', side_effect=PermissionError('denied')):
            job = manager.submit('scan', {'root': str(self.root), 'full_verification': True})
            manager.pool.shutdown(wait=True)
        saved = get_job(job['id'])
        self.assertEqual(saved['status'], 'succeeded')
        self.assertFalse(saved['result']['report']['scan']['complete'])
        self.assertEqual(saved['failures'], 1)
        self.assertIn('unverifiable', saved['message'])


if __name__ == '__main__':
    unittest.main()
