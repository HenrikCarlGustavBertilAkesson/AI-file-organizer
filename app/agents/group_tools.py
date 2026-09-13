"""Bounded category tools. Only draft manifests and move proposals are created."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import json

import database
from actions.validator import validate_action
from groups import group_page, freeze_selection
from models import ProposedAction


@dataclass
class GroupMoveProposal:
    batch_id: int
    group_id: int
    version: int
    category: str
    actions: list


def pending(path):
    with closing(database.get_connection()) as db:
        return db.execute("SELECT 1 FROM actions WHERE source=? AND status IN ('pending','approved') LIMIT 1",
                          (path,)).fetchone() is not None


def group_tool(name, arguments, root, candidates, seen_groups, *, max_files,
               remaining_proposals, proposed_sources, proposed_destinations, progress=None):
    if name == 'list_category_groups':
        result = group_page(root, page=arguments['page'], page_size=20)
        for group in result['groups']:
            seen_groups.setdefault(group['id'], {})
        return result
    gid = arguments['group_id']
    if type(gid) is not int or gid not in seen_groups:
        raise ValueError('Browse category groups before selecting a group.')
    if name == 'list_group_members':
        result = group_page(root, group_id=gid, page=arguments['page'], page_size=20,
                            organization_candidates=True)
        group = result['group']
        ledger = seen_groups[gid]
        if ledger.get('version') != group['version']:
            ledger.clear()
            ledger.update(version=group['version'], members={})
        rows = []
        for member in result['members']:
            path = member['path']
            if path not in candidates and len(candidates) >= max_files:
                continue
            if path in proposed_sources:
                continue
            payload = {key: member[key] for key in ('id', 'path', 'filename', 'category',
                        'confidence', 'destination', 'state')}
            payload['membership_needs_review'] = member['confidence'] is None or member['confidence'] < 0.7
            # Do not discover files whose data cannot fit in the actual tool response.
            if len(json.dumps(rows + [payload])) > 9000:
                break
            rows.append(payload)
            candidates.add(path)
            ledger['members'][member['id']] = member
        return {'group': group, 'members': rows, 'pagination': result['pagination'],
                'truncated': len(rows) < len(result['members']),
                'remaining_candidate_capacity': max_files-len(candidates)}
    if name != 'propose_group_move':
        raise ValueError('Unknown group tool.')
    ledger = seen_groups[gid]
    version = arguments['version']
    ids = arguments['file_ids']
    if type(version) is not int or version != ledger.get('version'):
        raise ValueError('Read current group members before proposing a move.')
    if (not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids)
            or len(ids) != len(set(ids)) or len(ids) > remaining_proposals):
        raise ValueError('Select distinct discovered file IDs within the remaining proposal allowance.')
    current = group_page(root, group_id=gid, page_size=1)['group']
    if current['version'] != version:
        raise ValueError('Group changed. Read its members again.')
    if str(Path(arguments['destination']).resolve()) != current['destination']:
        raise ValueError('Use the exact saved category folder; policy changes require user review.')
    actions, destinations = [], set(proposed_destinations)
    for fid in ids:
        if progress:
            progress(message='Validating category-group proposal…')
        member = ledger.get('members', {}).get(fid)
        if not member or member['path'] not in candidates:
            raise ValueError('Read each selected member before proposing its move.')
        if member['path'] in proposed_sources or pending(member['path']):
            raise ValueError('This file already has a pending or current-run proposal.')
        if member['destination'] in destinations:
            raise ValueError('Selected filenames collide in the shared folder. Review them separately.')
        destinations.add(member['destination'])
        reason = f"Group {gid}: collect files classified as {current['category']} in the saved category folder."
        if member['confidence'] is None or member['confidence'] < 0.7:
            reason += ' Uncertain category membership: review before approving.'
        action = ProposedAction('move', member['path'], member['destination'], reason)
        valid, error = validate_action(action, str(Path(root).resolve()))
        if not valid:
            raise ValueError(error)
        with closing(database.get_connection()) as db:
            collision = db.execute("SELECT 1 FROM actions WHERE destination=? AND status IN ('pending','approved') LIMIT 1",
                                   (action.destination,)).fetchone()
        if collision:
            raise ValueError('Another pending proposal uses this destination.')
        actions.append(action)
    # freeze_selection rechecks membership, scope and policy revisions before persisting.
    bid = freeze_selection(root, gid, version, file_ids=ids)
    return GroupMoveProposal(bid, gid, version, current['category'], actions)
