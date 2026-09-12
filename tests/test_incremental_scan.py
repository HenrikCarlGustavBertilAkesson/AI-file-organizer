from contextlib import closing
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from scanner import scan_file, calculate_hash, ScanError
from reconciliation import reconcile_directory
from jobs import JobManager, get_job
from web import dispatch


class IncrementalScanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def indexed(self, name='file.txt', content='original'):
        path = self.root / name
        path.write_text(content)
        # Use a timestamp exactly representable by both SQLite REAL and the
        # filesystem, so restoring it tests unchanged metadata rather than rounding.
        os.utime(path, (1700000000, 1700000000))
        file = scan_file(str(path))
        file.status, file.category = 'classified', 'Work'
        database.save_file(file)
        return path

    def test_unchanged_quick_scan_does_not_open_or_hash_contents(self):
        self.indexed()
        before = database.get_all_files()
        with patch('scanner.calculate_hash', side_effect=AssertionError('Unnecessary hash')), \
             patch('pathlib.Path.open', side_effect=AssertionError('Unnecessary content read')):
            result = reconcile_directory(str(self.root))
        self.assertEqual((result.scan.hashed_files, result.scan.reused_hashes), (0, 1))
        self.assertEqual(result.scan.mode, 'quick')
        self.assertEqual(result.modified_paths, [])
        self.assertEqual(database.get_all_files(), before)

    def test_new_and_changed_files_are_hashed(self):
        path = self.indexed()
        self.indexed('unchanged.txt')
        (self.root / 'new.txt').write_text('new')
        path.write_text('changed size')
        with patch('scanner.calculate_hash', wraps=calculate_hash) as hashing:
            result = reconcile_directory(str(self.root))
        self.assertEqual(hashing.call_count, 2)
        self.assertEqual(result.scan.reused_hashes, 1)
        self.assertEqual(result.modified_paths, [str(path)])
        self.assertEqual(result.new_paths, [str(self.root / 'new.txt')])

    def test_same_size_edit_with_changed_timestamp_is_hashed(self):
        path = self.indexed(content='before')
        timestamp = path.stat().st_mtime_ns
        path.write_text('after!')
        os.utime(path, ns=(timestamp, timestamp + 1000000000))
        result = reconcile_directory(str(self.root))
        self.assertEqual(result.scan.hashed_files, 1)
        self.assertEqual(result.modified_paths, [str(path)])

    def test_full_verification_detects_edit_hidden_from_quick_mode(self):
        path = self.indexed(content='before')
        info = path.stat()
        before = database.get_file_by_path(str(path))
        path.write_text('after!')
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        quick = reconcile_directory(str(self.root), apply=True)
        self.assertEqual(quick.modified_paths, [])
        self.assertEqual(quick.scan.reused_hashes, 1)
        self.assertEqual(database.get_file_by_path(str(path)), before)
        full = reconcile_directory(str(self.root), full_verification=True, apply=True)
        self.assertEqual(full.scan.mode, 'full')
        self.assertEqual((full.scan.hashed_files, full.scan.reused_hashes), (1, 0))
        self.assertEqual(full.modified_paths, [str(path)])
        self.assertEqual(database.get_file_by_path(str(path))['status'], 'pending')

    def test_missing_or_invalid_stored_hash_is_never_reused(self):
        self.indexed()
        for stored_hash in (None, '', 'invalid'):
            with self.subTest(stored_hash=stored_hash):
                with closing(database.get_connection()) as connection, connection:
                    connection.execute('UPDATE files SET hash=?', (stored_hash,))
                result = reconcile_directory(str(self.root))
                self.assertEqual((result.scan.hashed_files, result.scan.reused_hashes), (1, 0))

    def test_timestamp_only_apply_refreshes_metadata_without_losing_analysis(self):
        path = self.indexed()
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
        result = reconcile_directory(str(self.root), apply=True)
        self.assertEqual(result.modified_paths, [])
        self.assertEqual(result.metadata_paths, [str(path)])
        self.assertEqual(result.scan.hashed_files, 1)
        row = database.get_file_by_path(str(path))
        self.assertEqual((row['status'], row['category']), ('classified', 'Work'))
        next_scan = reconcile_directory(str(self.root))
        self.assertEqual(next_scan.scan.reused_hashes, 1)
        self.assertEqual(next_scan.metadata_paths, [])

    def test_returning_file_is_rehashed_even_when_metadata_matches(self):
        self.indexed()
        with closing(database.get_connection()) as connection, connection:
            connection.execute('UPDATE files SET is_present=0')
        result = reconcile_directory(str(self.root), apply=True)
        self.assertEqual(result.scan.hashed_files, 1)
        self.assertEqual(result.scan.reused_hashes, 0)
        self.assertEqual(database.get_all_files()[0]['is_present'], 1)

    def test_rename_still_uses_hash_move_detection(self):
        old = self.indexed()
        new = self.root / 'renamed.txt'
        old.rename(new)
        result = reconcile_directory(str(self.root))
        self.assertEqual(result.scan.hashed_files, 1)
        self.assertEqual(result.probable_moves[0].old_path, str(old))
        self.assertEqual(result.probable_moves[0].new_path, str(new))

    def test_partial_quick_scan_cannot_commit_missing_file_updates(self):
        self.indexed('unchanged.txt')
        missing = self.indexed('missing.txt')
        missing.unlink()
        (self.root / 'unreadable.txt').write_text('new')
        before = database.get_all_files()
        with patch('scanner.calculate_hash', side_effect=PermissionError('denied')):
            with self.assertRaises(ScanError):
                reconcile_directory(str(self.root), apply=True)
        self.assertEqual(database.get_all_files(), before)

    def test_file_changing_while_hashed_aborts(self):
        path = self.indexed()
        before = database.get_all_files()
        def changed(file_path):
            result = calculate_hash(file_path)
            file_path.write_text('modified during hash')
            return result
        with patch('scanner.calculate_hash', side_effect=changed):
            with self.assertRaisesRegex(ScanError, 'changed while hashing'):
                reconcile_directory(str(self.root), full_verification=True, apply=True)
        self.assertEqual(database.get_all_files(), before)

    def test_background_job_preserves_full_verification_mode(self):
        self.indexed()
        manager = JobManager(dispatch)
        self.addCleanup(manager.close)
        job = manager.submit('scan', {'root': str(self.root), 'full_verification': True})
        manager.pool.shutdown(wait=True)
        saved = get_job(job['id'])
        self.assertEqual(saved['status'], 'succeeded')
        self.assertTrue(saved['parameters']['full_verification'])
        self.assertEqual(saved['result']['report']['scan']['mode'], 'full')
        self.assertEqual(saved['result']['report']['scan']['hashed_files'], 1)


if __name__ == '__main__':
    unittest.main()
