from contextlib import closing
from pathlib import Path
from threading import Event
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from jobs import JobManager, JobCancelled, get_job, recent_jobs
from scanner import scan_file
from web import dispatch


class JobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def manager(self, runner=dispatch):
        manager = JobManager(runner)
        self.addCleanup(manager.close)
        return manager

    def test_background_start_cancel_and_persistent_status(self):
        started, release = Event(), Event()
        self.addCleanup(release.set)
        def runner(operation, data, progress):
            progress(2, 10, 'Working', failures=1, force=True)
            started.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            progress(3, 10, 'Next file', force=True)
            return {'message': 'done'}
        manager = self.manager(runner)
        job = manager.submit('scan', {'root': str(self.root)})
        self.assertTrue(started.wait(5))
        saved = get_job(job['id'])
        self.assertEqual((saved['completed'], saved['total'], saved['failures']), (2, 10, 1))
        with self.assertRaises(ValueError):
            manager.submit('scan', {'root': str(self.root)})
        manager.cancel(job['id'])
        release.set()
        manager.close()
        self.assertEqual(get_job(job['id'])['status'], 'cancelled')
        self.assertEqual(recent_jobs(str(self.root))[0]['id'], job['id'])

    def test_cancelled_scan_apply_never_marks_missing(self):
        path = self.root / 'old.txt'
        path.write_text('text')
        database.save_file(scan_file(str(path)))
        path.unlink()
        before = database.get_all_files()
        def cancel(*args, **kwargs):
            raise JobCancelled('stop')
        with self.assertRaises(JobCancelled):
            dispatch('apply', {'root': str(self.root)}, progress=cancel)
        self.assertEqual(database.get_all_files(), before)

    def test_classification_resume_skips_completed_files(self):
        for name in ('first.txt', 'second.txt'):
            path = self.root / name
            path.write_text('text')
            file = scan_file(str(path))
            file.status = 'pending'
            database.save_file(file)
        processed = []
        def process(file):
            processed.append(file.path)
            file.status = 'classified'
            database.save_file(file)
            return file
        def cancel_after_first(completed=None, *args, **kwargs):
            if completed == 1:
                raise JobCancelled('stop')
        with patch('process_pending.process_file', side_effect=process):
            with self.assertRaises(JobCancelled):
                dispatch('classify', {'root': str(self.root)}, progress=cancel_after_first)
            manager = self.manager()
            job = manager.submit('classify', {'root': str(self.root)})
            manager.pool.shutdown(wait=True)
        self.assertEqual(get_job(job['id'])['status'], 'succeeded')
        self.assertEqual(len(processed), 2)
        self.assertEqual(len(set(processed)), 2)

    def test_restart_recovery_and_resume(self):
        with closing(database.get_connection()) as connection, connection:
            cursor = connection.execute("INSERT INTO jobs(operation,root,parameters,status) VALUES (?,?,?,'running')",
                ('scan', str(self.root), '{"root":' + __import__('json').dumps(str(self.root)) + '}'))
            job_id = cursor.lastrowid
        manager = self.manager()
        self.assertEqual(get_job(job_id)['status'], 'interrupted')
        resumed = manager.resume(job_id)
        manager.pool.shutdown(wait=True)
        saved = get_job(resumed['id'])
        self.assertEqual(saved['status'], 'succeeded')
        self.assertEqual(saved['parent_id'], job_id)
        self.assertEqual(saved['result']['report']['missing_paths'], [])

    def test_failures_are_recorded(self):
        def runner(*args, **kwargs):
            raise ValueError('scan failed')
        manager = self.manager(runner)
        job = manager.submit('scan', {'root': str(self.root)})
        manager.pool.shutdown(wait=True)
        saved = get_job(job['id'])
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['message'], 'scan failed')
        manager.cancel(job['id'])
        self.assertEqual(get_job(job['id'])['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
