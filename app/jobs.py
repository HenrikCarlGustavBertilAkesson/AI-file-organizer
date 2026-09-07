"""One background operation at a time, with persistent status and cooperative cancellation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event, Lock
import json
import time

import database

OPERATIONS = {'inventory', 'scan', 'apply', 'classify', 'organize'}
ACTIVE = {'queued', 'running', 'cancelling'}


class JobCancelled(Exception):
    pass


def update(job_id, **values):
    with closing(database.get_connection()) as connection, connection:
        connection.execute('UPDATE jobs SET ' + ', '.join(f'{key} = ?' for key in values)
                           + ', updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                           (*values.values(), job_id))


def get_job(job_id):
    with closing(database.get_connection()) as connection:
        connection.row_factory = database.sqlite3.Row
        row = connection.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
    if not row:
        raise ValueError('Job not found.')
    job = dict(row)
    job['parameters'] = json.loads(job['parameters'])
    job['result'] = json.loads(job['result']) if job['result'] else None
    return job


def recent_jobs(root):
    with closing(database.get_connection()) as connection:
        ids = connection.execute('SELECT id FROM jobs WHERE root = ? ORDER BY id DESC LIMIT 20',
                                 (root,)).fetchall()
    return [get_job(row[0]) for row in ids]


class Progress:
    def __init__(self, job_id, cancelled):
        self.job_id = job_id
        self.cancelled = cancelled
        self.last_write = 0

    def __call__(self, completed=None, total=None, message='', failures=0, force=False):
        if self.cancelled.is_set():
            raise JobCancelled('Stopped at a safe boundary. Completed file work is saved.')
        now = time.monotonic()
        if force or now - self.last_write >= 0.2:
            values = {'message': message, 'failures': failures}
            if completed is not None:
                values.update(completed=completed, total=total)
            update(self.job_id, **values)
            self.last_write = now


class JobManager:
    def __init__(self, runner):
        self.runner = runner
        self.lock = Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='organizer')
        self.event = None
        self.active_id = None
        with closing(database.get_connection()) as connection, connection:
            connection.execute("UPDATE jobs SET status='interrupted', "
                               "message='Server stopped. Resume to continue remaining work.' "
                               "WHERE status IN ('queued','running','cancelling')")

    def busy(self):
        return self.active_id is not None

    def submit(self, operation, parameters, parent_id=None):
        if operation not in OPERATIONS:
            raise ValueError('This operation cannot run as a job.')
        with self.lock:
            if self.busy():
                raise ValueError('Another job is active. Wait or cancel it before starting another.')
            with closing(database.get_connection()) as connection, connection:
                cursor = connection.execute(
                    "INSERT INTO jobs(operation,root,parameters,status,parent_id) VALUES (?,?,?,'queued',?)",
                    (operation, parameters['root'], json.dumps(parameters), parent_id))
                job_id = cursor.lastrowid
            self.active_id = job_id
            self.event = Event()
            self.pool.submit(self._run, job_id, operation, parameters, self.event)
            return get_job(job_id)

    def _run(self, job_id, operation, parameters, event):
        try:
            update(job_id, status='running')
            progress = Progress(job_id, event)
            progress(message='Starting…', force=True)
            result = self.runner(operation, parameters, progress=progress)
            # Large file/action tables are refreshed separately by the UI.
            result = {key: value for key, value in result.items() if key not in ('files', 'actions')}
            update(job_id, status='succeeded', result=json.dumps(result),
                   message=result.get('message') or 'Completed.')
        except JobCancelled as error:
            update(job_id, status='cancelled', message=str(error))
        except Exception as error:
            update(job_id, status='failed', message=str(error),
                   failures=get_job(job_id)['failures'] + 1)
        finally:
            with self.lock:
                self.active_id = None
                self.event = None

    def cancel(self, job_id):
        with self.lock:
            if job_id == self.active_id and get_job(job_id)['status'] in ACTIVE:
                self.event.set()
                update(job_id, status='cancelling', message='Stopping after the current file or AI request…')
        return get_job(job_id)

    def resume(self, job_id):
        job = get_job(job_id)
        if job['status'] not in ('cancelled', 'interrupted', 'failed'):
            raise ValueError('Only stopped or failed jobs can be resumed.')
        return self.submit(job['operation'], job['parameters'], parent_id=job_id)

    def close(self):
        with self.lock:
            if self.event:
                self.event.set()
        self.pool.shutdown(wait=True)
