from database import (
    get_pending_actions,
    update_action,
    update_file_path,
    fail_other_pending_moves
)
from models import ActionStatus, ProposedAction

from actions.validator import validate_action
from actions.executor import execute_action


def _process_single_action(action: ProposedAction, allowed_root: str):
    answer = input("\nApprove? [y]es / [n]o / [s]kip: ").lower()
    review_action(action, allowed_root, answer)


def review_action(action: ProposedAction, allowed_root: str, answer: str):
    """Shared CLI/browser decision path; execution still revalidates approval."""
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
    actions = get_pending_actions()

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
