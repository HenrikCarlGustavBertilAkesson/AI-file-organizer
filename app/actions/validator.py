from pathlib import Path

from models import ProposedAction


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

    return True, ""