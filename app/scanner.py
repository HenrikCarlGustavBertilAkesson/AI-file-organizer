import hashlib
from pathlib import Path
from models import File


def calculate_hash(path: Path) -> str:
    hasher = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(8192):
            hasher.update(chunk)

    return hasher.hexdigest()


def scan_directory(directory: str):
    root = Path(directory)

    if not root.exists():
        raise ValueError(f"Directory does not exist: {directory}")

    if not root.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    files = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        try:
            stat = path.stat()

            files.append(
                File(
                    path=str(path.resolve()),
                    filename=path.name,
                    extension=path.suffix.lower(),
                    size=stat.st_size,
                    modified=stat.st_mtime,
                    hash=calculate_hash(path),
                )
            )

        except OSError as error:
            print(f"Could not scan {path}: {error}")

    return files