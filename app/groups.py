"""Persisted category review and frozen drafts; never mutates user files.

Snapshots use indexed metadata, not fresh verification. Drafts are deliberately
not approvals and cannot be submitted to the existing action executor.
"""
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
import argparse
import json
import math
import sqlite3

import database
from library import page_number
from organization_policy import load_policy, protected_reason
from workspace import load_scope, WorkspaceScope, DEFAULT_EXCLUSIONS


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def context(root):
    policy = load_policy(root)
    scope = load_scope(root)
    return policy, scope, encoded({'policy': asdict(policy) if policy else None,
                                  'scope': asdict(scope) if scope else None})


def refresh_groups(root):
    """Reconcile persisted membership from indexed classifications in one transaction.

    Streams metadata rows; extracted contents are never loaded or hashed.
    Empty groups retain their IDs so later classification batches reuse them.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing workspace.')
    with closing(database.get_connection()) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        policy, scope, config = context(root)
        # Legacy workspaces still exclude bundles, dependencies, and symlinks.
        if scope is None:
            folders = {p.name for p in root.iterdir() if p.is_dir()}
            if policy:
                folders.update(Path(folder).parts[0] for folder in policy.destinations.values()
                               if Path(folder).parts)
            scope = WorkspaceScope(str(root), sorted(folders), True, DEFAULT_EXCLUSIONS)
        db.execute('CREATE TEMP TABLE current_members (group_id, file_id, snapshot, state, size)')
        db.execute('CREATE INDEX current_members_group ON current_members(group_id,file_id)')
        groups = {row['category']: dict(row) for row in db.execute(
            'SELECT * FROM organization_groups WHERE root=?', (str(root),))}
        changed = set()
        for row in db.execute("""SELECT id,path,filename,size,modified,hash,category,confidence
                                 FROM files WHERE is_present=1 AND status='classified' ORDER BY id"""):
            category = (row['category'] or '').strip().casefold()
            if not category or not scope.allows(row['path']):
                continue
            folder = policy.destinations.get(category) if policy else None
            destination = str(root / folder) if folder is not None else None
            if category not in groups:
                gid = db.execute('''INSERT INTO organization_groups(root,category,destination,context)
                    VALUES (?,?,?,?)''', (str(root), category, destination, config)).lastrowid
                groups[category] = {'id': gid, 'destination': destination, 'context': config}
            group = groups[category]
            reason = protected_reason(row['path'], root, policy.protected_folders if policy else [])
            target = str(Path(destination) / Path(row['path']).name) if destination else None
            if not reason and target:
                if not scope.allows(target):
                    reason = 'Destination is outside the saved scope.'
                else:
                    reason = protected_reason(target, root, policy.protected_folders if policy else [])
            state = ('blocked' if reason else 'unmapped' if target is None else
                     'organized' if Path(row['path']).resolve() == Path(target).resolve() else 'needs_move')
            member = dict(row)
            member.update(category=category, destination=target, state=state, blocked_reason=reason,
                          verification='indexed_metadata_only')
            db.execute('INSERT INTO current_members VALUES (?,?,?,?,?)',
                       (group['id'], row['id'], encoded(member), state, max(0, row['size'] or 0)))
            if group['destination'] != destination or group['context'] != config:
                changed.add(group['id'])
        for category, group in groups.items():
            gid = group['id']
            # Compare membership and snapshots in SQL without materializing the group.
            different = db.execute('''SELECT 1 FROM (
                SELECT file_id,snapshot FROM current_members WHERE group_id=?
                EXCEPT SELECT file_id,snapshot FROM organization_group_members WHERE group_id=?
                ) LIMIT 1''', (gid, gid)).fetchone() or db.execute('''SELECT 1 FROM (
                SELECT file_id,snapshot FROM organization_group_members WHERE group_id=?
                EXCEPT SELECT file_id,snapshot FROM current_members WHERE group_id=?
                ) LIMIT 1''', (gid, gid)).fetchone()
            folder = policy.destinations.get(category) if policy else None
            destination = str(root / folder) if folder is not None else None
            if different or gid in changed or group['context'] != config:
                db.execute('''UPDATE organization_groups SET version=version+1,
                    destination=?,context=? WHERE id=?''', (destination, config, gid))
            db.execute('DELETE FROM organization_group_members WHERE group_id=?', (gid,))
            db.execute('INSERT INTO organization_group_members SELECT * FROM current_members WHERE group_id=?', (gid,))
    return str(root)


def pagination(page, page_size, total):
    page_number(page, 'Page')
    page_number(page_size, 'Page size')
    if page_size > 100:
        raise ValueError('Page size cannot exceed 100.')
    pages = max(1, math.ceil(total / page_size))
    return {'page': min(page, pages), 'page_size': page_size, 'total': total, 'pages': pages}


def group_page(root, *, group_id=None, page=1, page_size=50):
    pagination(page, page_size, 0)
    root = refresh_groups(root)
    with closing(database.get_connection()) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        if group_id is None:
            total = db.execute('SELECT count(*) FROM organization_groups WHERE root=?', (root,)).fetchone()[0]
            meta = pagination(page, page_size, total)
            rows = db.execute('''SELECT g.id,g.category,g.destination,g.version,
                count(m.file_id) AS file_count,coalesce(sum(m.size),0) AS total_bytes,
                count(CASE WHEN m.state='needs_move' THEN 1 END) AS needs_move,
                count(CASE WHEN m.state='organized' THEN 1 END) AS organized,
                count(CASE WHEN m.state='blocked' THEN 1 END) AS blocked,
                count(CASE WHEN m.state='unmapped' THEN 1 END) AS unmapped
                FROM organization_groups g LEFT JOIN organization_group_members m ON m.group_id=g.id
                WHERE g.root=? GROUP BY g.id ORDER BY g.category LIMIT ? OFFSET ?''',
                (root, page_size, (meta['page']-1)*page_size))
            return {'groups': [dict(r) for r in rows], 'pagination': meta}
        group = db.execute('SELECT id,category,destination,version FROM organization_groups WHERE id=? AND root=?',
                           (group_id, root)).fetchone()
        if group is None:
            raise ValueError('Unknown workspace group.')
        total = db.execute('SELECT count(*) FROM organization_group_members WHERE group_id=?', (group_id,)).fetchone()[0]
        meta = pagination(page, page_size, total)
        members = db.execute('''SELECT snapshot FROM organization_group_members WHERE group_id=?
            ORDER BY file_id LIMIT ? OFFSET ?''', (group_id, page_size, (meta['page']-1)*page_size))
        return {'group': dict(group), 'members': [json.loads(r[0]) for r in members], 'pagination': meta}


def freeze_selection(root, group_id, version, *, operation='move', file_ids=None):
    """Freeze all current group members or an explicit subset as an unapproved draft.

    Returns only a batch ID. Later group refreshes never modify batch members.
    An explicit group version prevents selecting members added since review.
    """
    if operation not in ('move', 'trash'):
        raise ValueError('Unknown operation.')
    if file_ids is not None and (not isinstance(file_ids, list) or not file_ids or
            any(type(i) is not int or i < 1 for i in file_ids)):
        raise ValueError('Select positive integer file IDs or use the whole group.')
    root = refresh_groups(root)
    with closing(database.get_connection()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        group = db.execute('SELECT version,context FROM organization_groups WHERE root=? AND id=?',
                           (root, group_id)).fetchone()
        if not group or group[0] != version or group[1] != context(Path(root))[2]:
            raise ValueError('Group changed. Review it again before selecting.')
        db.execute('CREATE TEMP TABLE selected_ids (id INTEGER PRIMARY KEY)')
        if file_ids is None:
            db.execute('INSERT INTO selected_ids SELECT file_id FROM organization_group_members WHERE group_id=?', (group_id,))
        else:
            db.executemany('INSERT OR IGNORE INTO selected_ids VALUES (?)', ((i,) for i in file_ids))
        invalid = db.execute('''SELECT 1 FROM selected_ids s LEFT JOIN organization_group_members m
            ON m.file_id=s.id AND m.group_id=? WHERE m.file_id IS NULL OR m.state='blocked'
            OR (?='move' AND m.state!='needs_move') LIMIT 1''', (group_id, operation)).fetchone()
        if invalid or not db.execute('SELECT 1 FROM selected_ids LIMIT 1').fetchone():
            raise ValueError('Selection contains unavailable, blocked, or ineligible members, or is empty.')
        bid = db.execute('''INSERT INTO organization_batches(root,operation,status,context)
            VALUES (?,?,'draft',?)''', (root, operation, group[1])).lastrowid
        db.execute('''INSERT INTO organization_batch_members
            SELECT ?,m.file_id,m.group_id,?,m.snapshot FROM organization_group_members m
            JOIN selected_ids s ON s.id=m.file_id WHERE m.group_id=?''', (bid, version, group_id))
        return bid


def batch_page(root, batch_id, *, page=1, page_size=50):
    pagination(page, page_size, 0)
    with closing(database.get_connection()) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        batch = db.execute('SELECT * FROM organization_batches WHERE id=? AND root=?',
                           (batch_id, str(Path(root).expanduser().resolve()))).fetchone()
        if not batch:
            raise ValueError('Unknown workspace batch.')
        total = db.execute('SELECT count(*) FROM organization_batch_members WHERE batch_id=?', (batch_id,)).fetchone()[0]
        meta = pagination(page, page_size, total)
        rows = db.execute('''SELECT snapshot FROM organization_batch_members WHERE batch_id=?
            ORDER BY file_id LIMIT ? OFFSET ?''', (batch_id, page_size, (meta['page']-1)*page_size))
        return {'batch': dict(batch), 'members': [json.loads(r[0]) for r in rows], 'pagination': meta}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--group', type=int)
    parser.add_argument('--page', type=int, default=1)
    parser.add_argument('--page-size', type=int, default=50)
    args = parser.parse_args()
    database.create_database()
    print(json.dumps(group_page(args.root, group_id=args.group, page=args.page,
                                page_size=args.page_size), indent=2))
