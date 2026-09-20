import hashlib
import os
import stat
import re
import errno
import time
from pathlib import Path
from models import File, ScanStats, ScanIssue


class ScanError(OSError):
    """A file could not be verified, or a scan could not start."""


class FileChangedError(ScanError):
    def __init__(self, path, fields):
        self.changed_fields = sorted(set(fields))
        super().__init__(f"File changed while hashing: {path}. Changed fields: {', '.join(self.changed_fields)}.")


STAT_FIELDS = ('st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_ino', 'st_dev')
FIELD_NAMES = ('size', 'modification time', 'metadata-change time', 'file identity', 'device')


def differences(before, after):
    return [name for field, name in zip(STAT_FIELDS, FIELD_NAMES)
            if getattr(before, field) != getattr(after, field)]


def calculate_hash(path_or_handle) -> str:
    """Accept an existing handle so verification and hashing use the same file."""
    if not hasattr(path_or_handle, 'read'):
        with Path(path_or_handle).open('rb') as handle:
            return calculate_hash(handle)
    hasher = hashlib.sha256()
    while chunk := path_or_handle.read(8192):
        hasher.update(chunk)
    return hasher.hexdigest()


def _scan_once(path):
    file_path = Path(path).expanduser().absolute()
    before_path = file_path.stat()
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f'Not a regular file: {file_path}')
    with file_path.open('rb') as handle:
        before = os.fstat(handle.fileno())
        fields = differences(before_path, before)
        if fields:
            raise FileChangedError(file_path, fields)
        file_hash = calculate_hash(handle)
        after = os.fstat(handle.fileno())
        try:
            after_path = file_path.stat()
        except FileNotFoundError:
            raise FileChangedError(file_path, ['path disappeared'])
        fields = differences(before, after) + differences(after, after_path)
        if fields:
            raise FileChangedError(file_path, fields)
    return File(str(file_path.resolve()), file_path.name, file_path.suffix.lower(),
                before.st_size, before.st_mtime, hash=file_hash)


def scan_file(path: str, *, progress=None, stats=None) -> File:
    """Retry instability/transient I/O up to three times; never accept a mixed hash."""
    for attempt in range(1, 4):
        if progress:
            progress(message=f'Verifying {path} (attempt {attempt}/3)')
        try:
            return _scan_once(path)
        except (OSError, ValueError) as error:
            error.attempts = attempt
            transient = isinstance(error, FileChangedError) or getattr(error, 'errno', None) in (
                errno.EAGAIN, errno.EBUSY, errno.ESTALE)
            if not transient or attempt == 3:
                raise
            if stats is not None:
                stats.retries += 1
            try:
                if progress:
                    progress(message=f'Retrying unstable file: {path}')
                time.sleep(0.1 * attempt)
            except Exception:
                if stats is not None:
                    stats.complete = False
                    stats.issues.append(ScanIssue(str(Path(path).absolute()), 'file', str(error),
                                                 attempt, getattr(error, 'changed_fields', [])))
                raise


def scan_directory(directory: str, *, scope=None, progress=None, indexed_files=None,
                   full_verification=False, stats=None, unverified_paths=()):
    root = Path(directory).expanduser().resolve()

    if not root.exists():
        raise ValueError(f"Directory does not exist: {directory}")

    if not root.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    files = []
    cached = indexed_files or {}
    report_issues = stats is not None
    stats = stats if stats is not None else ScanStats()
    stats.mode = 'full' if full_verification else 'quick'
    stats.hashed_files = stats.reused_hashes = stats.retries = 0
    stats.issues = []
    stats.complete = True

    def issue(path, kind, error):
        stats.complete = False
        stats.issues.append(ScanIssue(str(path), kind, str(error),
                                     getattr(error, 'attempts', 1), getattr(error, 'changed_fields', [])))

    def traversal_error(error: OSError) -> None:
        issue(Path(error.filename or root).absolute(), 'directory', error)

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
                    blocked = any(str(resolved) == item['path'] or
                                  (item['kind'] == 'directory' and resolved.is_relative_to(item['path']))
                                  for item in unverified_paths)
                    reusable = (not blocked and not full_verification and previous.get('is_present')
                                and isinstance(stored_hash, str)
                                and re.fullmatch(r'[0-9a-fA-F]{64}', stored_hash)
                                and previous.get('size') == info.st_size
                                and previous.get('modified') == info.st_mtime)
                    if reusable:
                        files.append(File(str(resolved), resolved.name, resolved.suffix.lower(),
                                          info.st_size, info.st_mtime, hash=stored_hash))
                        stats.reused_hashes += 1
                    else:
                        files.append(scan_file(str(path), progress=progress, stats=stats))
                        stats.hashed_files += 1
            except (OSError, ValueError) as error:
                issue(path.absolute(), 'file', error)

    if progress:
        progress(len(files), len(files),
                 f'{stats.mode.title()} scan complete: {stats.hashed_files} hashed, '
                 f'{stats.reused_hashes} hashes reused; {len(stats.issues)} paths could not be verified',
                 failures=len(stats.issues), force=True)
    if not report_issues and stats.issues:
        raise ScanError(f'Incomplete scan: {stats.issues[0].path}: {stats.issues[0].message}')
    return files
