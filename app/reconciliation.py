from pathlib import Path
from collections import defaultdict
from contextlib import closing
import sqlite3

from database import create_database, get_all_files, get_connection, save_file
from models import DetectedMove, ReconciliationResult
from scanner import scan_directory
from workspace import load_scope


def reconcile_directory(allowed_root: str, *, apply: bool = False) -> ReconciliationResult:
    root = Path(allowed_root).expanduser().resolve()
    scope = load_scope(root)
    scanned_files = scan_directory(str(root), scope=scope) if scope else scan_directory(str(root))
    if not apply:
        return compare_files(root, scanned_files, get_all_files(), scope=scope)

    # Read and repair the index under one write transaction. Scanning must
    # finish successfully before we acquire the lock or change any records.
    with closing(get_connection()) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            indexed_files = get_all_files(connection)
            result = compare_files(root, scanned_files, indexed_files, scope=scope)
            scanned_by_path = {file.path: file for file in scanned_files}
            indexed_by_path = {row["path"]: row for row in indexed_files}
            for move in result.probable_moves:
                file = scanned_by_path[move.new_path]
                # A destination occupied by a historical row is a conflict;
                # the UNIQUE constraint aborts and rolls back the whole repair.
                connection.execute(
                    """UPDATE files SET path = ?, filename = ?, extension = ?,
                       size = ?, modified = ?, hash = ?, is_present = 1
                       WHERE path = ?""",
                    (file.path, file.filename, file.extension, file.size,
                     file.modified, file.hash, move.old_path),
                )
            for path in result.missing_paths:
                connection.execute(
                    "UPDATE files SET is_present = 0 WHERE path = ?", (path,)
                )
            for path in result.new_paths:
                file = scanned_by_path[path]
                existing = indexed_by_path.get(path)
                if existing and file.hash and existing["hash"] == file.hash:
                    # A returning file with identical contents retains its analysis.
                    connection.execute(
                        """UPDATE files SET is_present = 1, filename = ?,
                           extension = ?, size = ?, modified = ? WHERE path = ?""",
                        (file.filename, file.extension, file.size, file.modified, path),
                    )
                else:
                    file.status = "pending"
                    save_file(file, connection)
            for path in result.modified_paths:
                # Fresh scanner records contain metadata only. Upserting clears
                # analysis of the old contents while preserving the row's ID.
                file = scanned_by_path[path]
                file.status = "pending"
                save_file(file, connection)
    return result


def compare_files(root, scanned_files, indexed_files, *, scope=None) -> ReconciliationResult:

    scanned_by_path = {
        file.path: file
        for file in scanned_files
        if Path(file.path).is_relative_to(root)
        and (scope is None or scope.allows(file.path))
    }

    indexed_by_path = {
        row["path"]: row
        for row in indexed_files
        if Path(row["path"]).is_relative_to(root)
        and row["is_present"]
        and (scope is None or scope.allows(row["path"]))
    }

    new_paths = scanned_by_path.keys() - indexed_by_path.keys()
    missing_paths = indexed_by_path.keys() - scanned_by_path.keys()
    # A missing legacy hash cannot establish unchanged contents, so queue
    # those records for processing too.
    modified_paths = [
        path for path in scanned_by_path.keys() & indexed_by_path.keys()
        if scanned_by_path[path].hash != indexed_by_path[path].get("hash")
    ]

    new_by_hash = defaultdict(list)
    missing_by_hash = defaultdict(list)
    for path in new_paths:
        file_hash = scanned_by_path[path].hash
        if file_hash:
            new_by_hash[file_hash].append(path)
    for path in missing_paths:
        file_hash = indexed_by_path[path].get("hash")
        if file_hash:
            missing_by_hash[file_hash].append(path)

    probable_moves = []
    for file_hash in new_by_hash.keys() & missing_by_hash.keys():
        old_candidates = missing_by_hash[file_hash]
        new_candidates = new_by_hash[file_hash]
        # Identical content suggests a move only if the pairing is unique.
        if len(old_candidates) == 1 and len(new_candidates) == 1:
            old_path = old_candidates[0]
            new_path = new_candidates[0]
            probable_moves.append(DetectedMove(old_path, new_path))
            missing_paths.remove(old_path)
            new_paths.remove(new_path)

    return ReconciliationResult(
        new_paths=sorted(new_paths),
        missing_paths=sorted(missing_paths),
        probable_moves=sorted(probable_moves, key=lambda move: move.old_path),
        modified_paths=sorted(modified_paths),
    )

def print_reconciliation(result: ReconciliationResult) -> None:
    print("\n--- FILESYSTEM RECONCILIATION ---")

    print(f"\nNew files: {len(result.new_paths)}")
    for path in result.new_paths:
        print(f"  + {path}")

    print(f"\nMissing files: {len(result.missing_paths)}")
    for path in result.missing_paths:
        print(f"  - {path}")

    print(f"\nProbable moves: {len(result.probable_moves)}")
    for move in result.probable_moves:
        print(f"  {move.old_path} -> {move.new_path}")

    print(f"\nModified files: {len(result.modified_paths)}")
    for path in result.modified_paths:
        print(f"  * {path}")

    if not any((result.new_paths, result.missing_paths,
                result.probable_moves, result.modified_paths)):
        print("\nNo changes detected.")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Report filesystem changes; optionally repair the SQLite index."
    )
    parser.add_argument("directory")
    parser.add_argument("--apply", action="store_true",
                        help="Apply repairs to SQLite without changing files or calling AI")
    args = parser.parse_args()

    try:
        if args.apply:
            create_database()
        result = reconcile_directory(args.directory, apply=args.apply)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Reconciliation failed: {error}\nNo index repairs committed.\n")
    print_reconciliation(result)
    if args.apply:
        print("\nIndex repairs committed. No files were moved or deleted.")
