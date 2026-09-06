import shutil
from pathlib import Path

from models import ProposedAction, ActionStatus
from actions.validator import validate_action


def execute_action(
    action: ProposedAction,
    allowed_root: str,
) -> bool:

    if action.status != ActionStatus.APPROVED:
        action.error = "Action has not been approved."
        return False

    valid, error = validate_action(
        action,
        allowed_root,
    )

    if not valid:
        action.status = ActionStatus.FAILED
        action.error = error
        return False

    try:
        source = Path(action.source)
        destination = Path(action.destination)

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.move(
            str(source),
            str(destination),
        )

        action.status = ActionStatus.EXECUTED
        action.error = ""

        return True

    except Exception as error:
        action.status = ActionStatus.FAILED
        action.error = str(error)

        return False