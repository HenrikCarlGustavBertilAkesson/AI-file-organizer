import hashlib
import os
import stat
from pathlib import Path
from models import File


class ScanError(OSError):
    """A scan could not finish; its results must not be used."""


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

def scan_directory(directory: str, *, scope=None):
    root = Path(directory).expanduser().resolve()

    if not root.exists():
        raise ValueError(f"Directory does not exist: {directory}")

    if not root.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    files = []

    def traversal_error(error: OSError) -> None:
        raise ScanError(
            f"Could not scan directory {error.filename}: {error}"
        ) from error

    # Unlike rglob, walk exposes directory traversal failures via onerror.
    for directory_path, directories, filenames in os.walk(
        root, onerror=traversal_error, followlinks=False
    ):
        if scope:
            directories[:] = [name for name in directories
                              if scope.allows(Path(directory_path) / name, directory=True)]
        for filename in filenames:
            path = Path(directory_path) / filename
            if scope and not scope.allows(path):
                continue
            try:
                # stat raises on inaccessible/disappearing files instead of
                # treating them as absent. Ignore non-regular filesystem entries.
                if stat.S_ISREG(path.stat().st_mode):
                    files.append(scan_file(str(path)))
            except (OSError, ValueError) as error:
                raise ScanError(f"Could not scan file {path}: {error}") from error

    return files
