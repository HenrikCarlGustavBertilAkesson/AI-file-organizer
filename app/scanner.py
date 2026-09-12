import hashlib
import os
import stat
import re
from pathlib import Path
from models import File, ScanStats


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
    file_hash = calculate_hash(file_path)
    after = file_path.stat()
    if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_dev) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_dev):
        raise ScanError(f'File changed while hashing: {file_path}. Retry the scan.')

    return File(
        path=str(file_path),
        filename=file_path.name,
        extension=file_path.suffix.lower(),
        size=stat.st_size,
        modified=stat.st_mtime,
        hash=file_hash,
    )

def scan_directory(directory: str, *, scope=None, progress=None, indexed_files=None,
                   full_verification=False, stats=None):
    root = Path(directory).expanduser().resolve()

    if not root.exists():
        raise ValueError(f"Directory does not exist: {directory}")

    if not root.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    files = []
    cached = indexed_files or {}
    stats = stats if stats is not None else ScanStats()
    stats.mode = 'full' if full_verification else 'quick'
    stats.hashed_files = stats.reused_hashes = 0

    def traversal_error(error: OSError) -> None:
        raise ScanError(
            f"Could not scan directory {error.filename}: {error}"
        ) from error

    # Unlike rglob, walk exposes directory traversal failures via onerror.
    for directory_path, directories, filenames in os.walk(
        root, onerror=traversal_error, followlinks=False
    ):
        if progress:
            progress(len(files), message=f'Scanning {directory_path}')
        if scope:
            directories[:] = [name for name in directories
                              if scope.allows(Path(directory_path) / name, directory=True)]
        for filename in filenames:
            path = Path(directory_path) / filename
            if scope and not scope.allows(path):
                continue
            if progress:
                progress(len(files), message=f'Checking {path}')
            try:
                # stat raises on inaccessible/disappearing files instead of
                # treating them as absent. Ignore non-regular filesystem entries.
                resolved = path.resolve()
                info = resolved.stat()
                if stat.S_ISREG(info.st_mode):
                    previous = cached.get(str(resolved), {})
                    stored_hash = previous.get('hash')
                    reusable = (not full_verification and previous.get('is_present')
                                and isinstance(stored_hash, str)
                                and re.fullmatch(r'[0-9a-fA-F]{64}', stored_hash)
                                and previous.get('size') == info.st_size
                                and previous.get('modified') == info.st_mtime)
                    if reusable:
                        files.append(File(str(resolved), resolved.name, resolved.suffix.lower(),
                                          info.st_size, info.st_mtime, hash=stored_hash))
                        stats.reused_hashes += 1
                    else:
                        files.append(scan_file(str(path)))
                        stats.hashed_files += 1
            except (OSError, ValueError) as error:
                raise ScanError(f"Could not scan file {path}: {error}") from error

    if progress:
        progress(len(files), len(files),
                 f'{stats.mode.title()} scan complete: {stats.hashed_files} hashed, '
                 f'{stats.reused_hashes} hashes reused', force=True)
    return files
