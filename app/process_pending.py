"""Explicitly process queued files; supported content may be sent to the AI API."""
from contextlib import closing
from dataclasses import dataclass, field, asdict
from pathlib import Path
import sqlite3

from database import create_database, get_all_files, get_connection
from scanner import scan_file
from workspace import load_scope
from ai.runtime import bounded_int, usage_scope, AILimitReached
from jobs import JobCancelled


@dataclass
class ProcessingSummary:
    classified: int = 0
    unsupported: int = 0
    empty: int = 0
    failed: int = 0
    skipped: int = 0
    remaining: int = 0
    usage: dict = field(default_factory=dict)


def process_file(file):
    # Help and empty queues don't need document libraries or an API client.
    from processor import process_file as process
    return process(file)


def process_pending(directory: str, *, retry_failed: bool = False, progress=None,
                    batch_size: int = 25) -> ProcessingSummary:
    bounded_int(batch_size, 100, 'Batch size')
    with usage_scope(batch_size * 3, progress=progress) as budget:
        summary = _process_pending(directory, retry_failed=retry_failed, progress=progress,
                                   batch_size=batch_size)
        summary.usage = asdict(budget.usage)
        return summary


def _process_pending(directory, *, retry_failed, progress, batch_size):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    statuses = {"pending", "failed"} if retry_failed else {"pending"}
    summary = ProcessingSummary()
    scope = load_scope(root)
    candidates = [row for row in get_all_files(columns=('path', 'status', 'is_present'))
                  if row['is_present'] and row['status'] in statuses
                  and Path(row['path']).is_relative_to(root)
                  and (scope is None or scope.allows(row['path']))]
    candidates.sort(key=lambda row: row['path'])
    summary.remaining = max(0, len(candidates) - batch_size)
    candidates = candidates[:batch_size]
    for index, row in enumerate(candidates):
        if progress:
            progress(index, len(candidates), f"Processing {row['path']}", summary.failed, force=True)
        path = Path(row["path"])
        if (not row["is_present"] or row["status"] not in statuses
                or not path.is_relative_to(root) or (scope and not scope.allows(path))):
            continue
        try:
            # Resolve again: an indexed path may now be a symlink outside root.
            if not path.resolve().is_relative_to(root) or not path.is_file():
                summary.skipped += 1
                continue
            file = scan_file(str(path))
            if not Path(file.path).is_relative_to(root):
                summary.skipped += 1
                continue
            result = process_file(file)
            status = result["status"] if isinstance(result, dict) else result.status
            if status not in {"classified", "unsupported", "empty", "failed"}:
                raise ValueError(f"Unexpected processing status: {status}")
            setattr(summary, status, getattr(summary, status) + 1)
        except (sqlite3.Error, AILimitReached, JobCancelled):
            # A broken index is a command-level failure, not a file failure.
            raise
        except Exception as error:
            with closing(get_connection()) as connection, connection:
                connection.execute(
                    "UPDATE files SET status = 'failed', error = ? WHERE path = ?",
                    (str(error), row["path"]),
                )
            summary.failed += 1
            print(f"Failed: {path}: {error}")
    if progress:
        progress(len(candidates), len(candidates), 'Processing complete', summary.failed, force=True)
    return summary


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Extract and classify present pending files. Supported file "
                    "contents may be sent to the AI API."
    )
    parser.add_argument("directory")
    parser.add_argument('--batch-size', type=int, default=25,
                        help='Maximum files to process in this run (1–100; default 25)')
    parser.add_argument("--retry-failed", action="store_true",
                        help="Also retry files with failed status")
    args = parser.parse_args()
    try:
        create_database()
        summary = process_pending(args.directory, retry_failed=args.retry_failed, batch_size=args.batch_size)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Processing stopped: {error}\n")
    print(f"\nClassified: {summary.classified}\nUnsupported: {summary.unsupported}"
          f"\nEmpty: {summary.empty}\nFailed: {summary.failed}\nSkipped: {summary.skipped}")
    print(f'Remaining outside this batch: {summary.remaining}\nAI usage: {summary.usage}')
    if summary.failed:
        parser.exit(1)


if __name__ == "__main__":
    main()
