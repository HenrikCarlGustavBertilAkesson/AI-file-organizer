"""Review a frozen move manifest once; execute child moves with durable outcomes."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import re
import sqlite3

import database
from actions.executor import execute_action
from actions.validator import validate_action
from groups import context, encoded, pagination
from models import ProposedAction, ActionStatus
from scanner import scan_file
from workspace import load_scope, excluded, DEFAULT_EXCLUSIONS


def members(db, batch_id):
    # Exhaust each SQL page before yielding so job progress can use another writer.
    last_id = 0
    while True:
        rows = db.execute('''SELECT file_id,snapshot FROM organization_batch_members
            WHERE batch_id=? AND file_id>? ORDER BY file_id LIMIT 100''', (batch_id, last_id)).fetchall()
        if not rows:
            return
        for row in rows:
            yield row
        last_id = rows[-1]['file_id']


def digest(db, batch):
    value = hashlib.sha256(encoded({'id': batch['id'], 'context': batch['context'], 'operation': batch['operation']}).encode())
    for row in members(db, batch['id']):
        value.update(encoded([row['file_id'], row['snapshot']]).encode())
    return value.hexdigest()


def batch_record(db, root, batch_id):
    row = db.execute("""SELECT b.*,coalesce(r.status,'pending') AS review_status
        FROM organization_batches b LEFT JOIN group_reviews r ON r.batch_id=b.id
        WHERE b.id=? AND b.root=? AND b.operation='move'""", (batch_id, str(Path(root).resolve()))).fetchone()
    if not row:
        raise ValueError('Group proposal is not available in this workspace.')
    return dict(row)


def proposal_page(root, *, page=1, batch_id=None, member_page=1):
    """Bounded cards and member pages. This does not hash or mutate user files."""
    pagination(page, 10, 0)
    pagination(member_page, 20, 0)
    root = str(Path(root).resolve())
    with closing(database.get_connection()) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        if batch_id is not None:
            batch = batch_record(db, root, batch_id)
            total = db.execute('SELECT count(*) FROM organization_batch_members WHERE batch_id=?', (batch_id,)).fetchone()[0]
            meta = pagination(member_page, 20, total)
            rows = db.execute('''SELECT m.snapshot,coalesce(i.status,'pending') AS outcome,i.error
                FROM organization_batch_members m LEFT JOIN group_review_items i
                ON i.batch_id=m.batch_id AND i.file_id=m.file_id
                WHERE m.batch_id=? ORDER BY m.file_id LIMIT 20 OFFSET ?''', (batch_id, (meta['page']-1)*20))
            return {'batch_id': batch_id, 'token': digest(db, batch), 'status': batch['review_status'],
                    'members': [{**json.loads(r['snapshot']), 'outcome': r['outcome'], 'error': r['error']} for r in rows],
                    'pagination': meta}
        total = db.execute("SELECT count(*) FROM organization_batches WHERE root=? AND operation='move'", (root,)).fetchone()[0]
        meta = pagination(page, 10, total)
        ids = db.execute("""SELECT b.id FROM organization_batches b LEFT JOIN group_reviews r ON r.batch_id=b.id
            WHERE b.root=? AND b.operation='move'
            ORDER BY CASE WHEN r.status IS NULL THEN 0 ELSE 1 END,b.id DESC LIMIT 10 OFFSET ?""", (root, (meta['page']-1)*10)).fetchall()
        cards = []
        for row in ids:
            batch = batch_record(db, root, row['id'])
            count, size = db.execute("""SELECT count(*),coalesce(sum(json_extract(snapshot,'$.size')),0)
                FROM organization_batch_members WHERE batch_id=?""", (batch['id'],)).fetchone()
            first = db.execute('SELECT snapshot FROM organization_batch_members WHERE batch_id=? ORDER BY file_id LIMIT 1', (batch['id'],)).fetchone()
            sample = json.loads(first[0]) if first else {}
            outcomes = {r[0]: r[1] for r in db.execute('SELECT status,count(*) FROM group_review_items WHERE batch_id=? GROUP BY status', (batch['id'],))}
            message = db.execute('SELECT message FROM group_reviews WHERE batch_id=?', (batch['id'],)).fetchone()
            cards.append({'batch_id': batch['id'], 'token': digest(db, batch), 'status': batch['review_status'],
                          'category': sample.get('category', ''), 'destination': str(Path(sample['destination']).parent) if sample else '',
                          'file_count': count, 'total_bytes': size, 'outcomes': outcomes,
                          'message': message[0] if message else ''})
        pending = db.execute("""SELECT count(*) FROM organization_batches b LEFT JOIN group_reviews r ON r.batch_id=b.id
            WHERE b.root=? AND b.operation='move' AND r.status IS NULL""", (root,)).fetchone()[0]
        return {'group_actions': cards, 'group_action_pagination': meta, 'pending_group_count': pending}


def verify(root, batch, member):
    if batch['context'] != context(Path(root))[2]:
        raise ValueError('Scope or organization policy changed. Generate a new group proposal.')
    source = Path(member['path'])
    target = Path(member['destination'])
    scope = load_scope(root)
    for path in (source, target):
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            raise ValueError('File is outside the workspace.')
        if excluded(parts, scope.exclusions if scope else DEFAULT_EXCLUSIONS):
            raise ValueError('File is excluded.')
        current = Path(root)
        for part in parts:
            current /= part
            if current.is_symlink():
                raise ValueError('Symlinks cannot be moved in a group.')
    row = database.get_file_by_path(str(source))
    if not row or row['id'] != member['id'] or not row['is_present'] or (row['category'] or '').strip().casefold() != member['category']:
        raise ValueError('Indexed file or category changed. Generate a new proposal.')
    action = ProposedAction('move', str(source), str(target), 'Approved category group')
    valid, error = validate_action(action, str(root))
    if not valid:
        raise ValueError(error)
    if not re.fullmatch('[0-9a-fA-F]{64}', member.get('hash') or ''):
        raise ValueError('This draft has no verified file hash. Rescan and generate a new proposal.')
    fresh = scan_file(str(source))
    if (fresh.hash, fresh.size, fresh.modified) != (member['hash'], member['size'], member['modified']):
        raise ValueError('File changed since this proposal was created. Generate a new proposal.')
    return action


def set_status(batch_id, status, message):
    with closing(database.get_connection()) as db, db:
        db.execute('UPDATE group_reviews SET status=?,message=?,updated_at=CURRENT_TIMESTAMP WHERE batch_id=?',
                   (status, message, batch_id))



def retire_pending_children(batch_id, status, error=''):
    with closing(database.get_connection()) as db, db:
        db.row_factory = sqlite3.Row
        for row in members(db, batch_id):
            item = json.loads(row['snapshot'])
            db.execute("UPDATE actions SET status=?,error=? WHERE source=? AND destination=? AND status='pending'",
                       (status, error, item['path'], item['destination']))


def review_group(root, batch_id, token, decision, *, progress=None):
    """Approval binds to the persisted manifest, not a client list or live category."""
    if decision not in ('y', 'n'):
        raise ValueError('Choose approve or reject.')
    root = Path(root).resolve()
    with closing(database.get_connection()) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        batch = batch_record(db, root, batch_id)
        if batch['review_status'] != 'pending' or token != digest(db, batch):
            raise ValueError('This group was already reviewed or its selection changed. Refresh the proposals.')
        db.execute("INSERT INTO group_reviews(batch_id,status) VALUES (?,?)", (batch_id, 'running' if decision=='y' else 'rejected'))
    completed, failures = 0, 0
    try:
        with closing(database.get_connection()) as db:
            db.row_factory = sqlite3.Row
            count = db.execute('SELECT count(*) FROM organization_batch_members WHERE batch_id=?', (batch_id,)).fetchone()[0]
            if not count:
                raise ValueError('Group is empty.')
            if decision == 'n':
                retire_pending_children(batch_id, 'rejected')
                return {'message': f'Rejected group of {count} files.'}
            destinations = set()
            for row in members(db, batch_id):
                if progress:
                    progress(0, count, 'Checking every file before the group move…')
                item = json.loads(row['snapshot'])
                verify(root, batch, item)
                if item['destination'] in destinations:
                    raise ValueError('Multiple members have the same destination.')
                destinations.add(item['destination'])
            for row in members(db, batch_id):
                item = json.loads(row['snapshot'])
                if progress:
                    progress(completed, count, f"Moving approved group: {Path(item['path']).name}", failures=failures)
                try:
                    action = verify(root, batch, item)
                except (ValueError, OSError) as error:
                    with db:
                        db.execute('INSERT INTO group_review_items VALUES (?,?,?,?)', (batch_id, item['id'], 'skipped', str(error)))
                    failures += 1
                    continue
                with db:
                    db.execute("INSERT INTO group_review_items VALUES (?,?,'executing','')", (batch_id, item['id']))
                action.status = ActionStatus.APPROVED
                success = execute_action(action, str(root))
                if success:
                    completed += 1
                outcome = 'executed' if success else ('needs_review' if not Path(action.source).exists() else 'failed')
                # Persist outcome before updating the index: failures must not trigger replay.
                with db:
                    db.execute('UPDATE group_review_items SET status=?,error=? WHERE batch_id=? AND file_id=?',
                               (outcome, action.error, batch_id, item['id']))
                with db:
                    if success:
                        db.execute('UPDATE files SET path=?,filename=? WHERE id=? AND path=?',
                                   (action.destination, Path(action.destination).name, item['id'], action.source))
                        db.execute("UPDATE actions SET status='executed' WHERE source=? AND destination=? AND status='pending'",
                                   (action.source, action.destination))
                        db.execute("UPDATE actions SET status='failed',error='Superseded by approved group move' WHERE source=? AND status='pending'", (action.source,))
                    else:
                        failures += 1
            message = f'Group move: {completed} moved, {failures} skipped or failed, out of {count} files.'
            if progress:
                progress(completed+failures, count, message, failures=failures, force=True)
            if failures:
                retire_pending_children(batch_id, 'failed', 'Group move stopped. Review and propose remaining files again.')
            set_status(batch_id, 'executed' if completed == count else 'partial', message)
            return {'message': message, 'group_move': {'batch_id': batch_id, 'moved': completed, 'failed': failures, 'total': count}}
    except Exception as error:
        set_status(batch_id, 'needs_review', f'{completed} moved before stopping. {error} Rescan and propose remaining files again; this batch will not replay.')
        retire_pending_children(batch_id, 'failed', 'Group stopped; rescan and create a new proposal.')
        raise


def belongs_to_pending_group(action, root):
    with closing(database.get_connection()) as db:
        return db.execute("""SELECT 1 FROM organization_batch_members m
            JOIN organization_batches b ON b.id=m.batch_id
            LEFT JOIN group_reviews r ON r.batch_id=b.id
            WHERE b.root=? AND b.operation='move' AND (r.status IS NULL OR r.status='running')
            AND json_extract(m.snapshot,'$.path')=?
            AND json_extract(m.snapshot,'$.destination')=? LIMIT 1""",
            (str(Path(root).resolve()), action.source, action.destination)).fetchone() is not None
