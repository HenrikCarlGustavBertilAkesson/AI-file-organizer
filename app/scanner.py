import hashlib
from pathlib import Path
from models import File


def calculate_hash(path: Path) -> str:
    hasher = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(8192):
            hasher.update(chunk)

    return hasher.hexdigest()

def scan_file(path: str) -> File:
    file_path = Path(path).expanduser().resolve()

    if not file_path.exists():
        raise ValueError(f"File does not exist: {path}")

    if not file_path.is_file():
        raise ValueError(f"Not a file: {path}")

    stat = file_path.stat()

    return File(
        path=str(file_path),
        filename=file_path.name,
        extension=file_path.suffix.lower(),
        size=stat.st_size,
        modified=stat.st_mtime,
        hash=calculate_hash(file_path),
    )

def scan_directory(directory: str):
    root = Path(directory).expanduser().resolve()

    if not root.exists():
        raise ValueError(f"Directory does not exist: {directory}")

    if not root.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    files = []

    for path in root.rglob("*"):
        if path.is_file():
            try:
                files.append(scan_file(str(path)))
            except OSError as error:
                print(f"Could not scan {path}: {error}")

    return files