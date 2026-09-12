from pathlib import Path

from models import ProposedAction
from workspace import load_scope
from organization_policy import validate_policy_action


def is_inside_directory(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_action(
    action: ProposedAction,
    allowed_root: str,
) -> tuple[bool, str]:

    if action.action_type != "move":
        return False, f"Unsupported action: {action.action_type}"

    root = Path(allowed_root)

    source = Path(action.source).resolve()
    destination = Path(action.destination).resolve()
    scope = load_scope(root)
    if scope and (not scope.allows(Path(action.source)) or not scope.allows(Path(action.destination))):
        return False, "Source or destination is outside the saved workspace scope."

    if not is_inside_directory(source, root):
        return False, "Source is outside the allowed directory."

    if not is_inside_directory(destination, root):
        return False, "Destination is outside the allowed directory."

    if not source.exists():
        return False, "Source file does not exist."

    if not source.is_file():
        return False, "Source path is not a file."

    if source == destination:
        return False, "Source and destination are the same."

    if destination.exists():
        return False, "Destination already exists."

    policy_error = validate_policy_action(action, root)
    if policy_error:
        return False, policy_error

    return True, ""
