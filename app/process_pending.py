"""Explicitly process queued files; supported content may be sent to the AI API."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from database import create_database, get_all_files, get_connection
from scanner import scan_file


@dataclass
class ProcessingSummary:
    classified: int = 0
    unsupported: int = 0
    empty: int = 0
    failed: int = 0
    skipped: int = 0


def process_file(file):
    # Help and empty queues don't need document libraries or an API client.
    from processor import process_file as process
    return process(file)


def process_pending(directory: str, *, retry_failed: bool = False) -> ProcessingSummary:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    statuses = {"pending", "failed"} if retry_failed else {"pending"}
    summary = ProcessingSummary()
    for row in get_all_files():
        path = Path(row["path"])
        if (not row["is_present"] or row["status"] not in statuses
                or not path.is_relative_to(root)):
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
        except sqlite3.Error:
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
    return summary


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Extract and classify present pending files. Supported file "
                    "contents may be sent to the AI API."
    )
    parser.add_argument("directory")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Also retry files with failed status")
    args = parser.parse_args()
    try:
        create_database()
        summary = process_pending(args.directory, retry_failed=args.retry_failed)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Processing stopped: {error}\n")
    print(f"\nClassified: {summary.classified}\nUnsupported: {summary.unsupported}"
          f"\nEmpty: {summary.empty}\nFailed: {summary.failed}\nSkipped: {summary.skipped}")
    if summary.failed:
        parser.exit(1)


if __name__ == "__main__":
    main()
