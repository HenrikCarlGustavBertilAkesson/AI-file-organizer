from database import (
    get_pending_actions,
    update_action,
    update_file_path,
    fail_other_pending_moves
)
from models import ActionStatus, ProposedAction

from actions.validator import validate_action
from actions.executor import execute_action
from group_review import belongs_to_pending_group, proposal_page, review_group


def _process_single_action(action: ProposedAction, allowed_root: str):
    answer = input("\nApprove? [y]es / [n]o / [s]kip: ").lower()
    review_action(action, allowed_root, answer)


def review_action(action: ProposedAction, allowed_root: str, answer: str):
    """Shared CLI/browser decision path; execution still revalidates approval."""
    if belongs_to_pending_group(action, allowed_root):
        raise ValueError('This move belongs to a group. Review the group proposal instead.')
    if action.status != ActionStatus.PENDING:
        raise ValueError("This proposal has already been reviewed.")

    if answer == "y":
        action.status = ActionStatus.APPROVED
        update_action(action)

        success = execute_action(action, allowed_root)

        if success:
            print("Executed.")

            index_updated = update_file_path(
                old_path=action.source,
                new_path=action.destination,
            )

            if not index_updated:
                print(
                    "Warning: file was moved, but no matching "
                    "file was found in the index."
                )

            fail_other_pending_moves(
                source=action.source,
                executed_action_id=action.id,
            )
        else:
            print(f"Failed: {action.error}")

        update_action(action)

    elif answer == "n":
        action.status = ActionStatus.REJECTED
        update_action(action)
        print("Rejected.")

    else:
        print("Left pending.")


def review_pending_actions(allowed_root: str):
    # Freeze the list of pending group IDs before decisions reorder the cards.
    pending = []
    page = 1
    while True:
        result = proposal_page(allowed_root, page=page)
        pending.extend(g for g in result['group_actions'] if g['status']=='pending')
        if page >= result['group_action_pagination']['pages']:
            break
        page += 1
    for group in pending:
        print(f"\nGroup #{group['batch_id']}: {group['file_count']} files → {group['destination']}")
        page = 1
        while True:
            result = proposal_page(allowed_root, batch_id=group['batch_id'], member_page=page)
            for member in result['members']:
                print(f"  {member['path']} → {member['destination']}")
            if page >= result['pagination']['pages']:
                break
            page += 1
        answer = input('Approve this entire group? [y]es / [n]o / [s]kip: ').lower()
        if answer in ('y', 'n'):
            try:
                print(review_group(allowed_root, group['batch_id'], group['token'], answer)['message'])
            except (ValueError, OSError) as error:
                print(f'Group stopped: {error}')
    actions = [a for a in get_pending_actions() if not belongs_to_pending_group(a, allowed_root)]

    if not actions:
        print("\nNo pending actions.")
        return

    print(f"\n--- PENDING ACTIONS ({len(actions)}) ---\n")

    for action in actions:
        print(f"Action #{action.id}")
        print(f"Type: {action.action_type}")
        print(f"From: {action.source}")
        print(f"To:   {action.destination}")
        print(f"Why:  {action.reason}")

        valid, error = validate_action(action, allowed_root)

        if not valid:
            print(f"INVALID: {error}")
            action.status = ActionStatus.FAILED
            action.error = error
            update_action(action)
            print()
            continue

        _process_single_action(action, allowed_root)
        print()
