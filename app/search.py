"""Read-only keyword search over the local file index."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

import database
from workspace import load_scope


@dataclass
class SearchResult:
    path: str
    snippet: str
    score: float


def search_files(directory: str, query: str, *, limit: int = 20) -> list[SearchResult]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")
    # Treat user input as words, not executable FTS query syntax. All words
    # must match, but they can occur in different indexed fields.
    terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
    if not terms:
        return []
    match = " AND ".join('"' + term + '"' for term in terms)
    uri = Path(database.DATABASE).resolve().as_uri() + "?mode=ro"
    scope = load_scope(root)
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        def inside_root(path):
            candidate = Path(path)
            return (candidate.is_relative_to(root)
                    and candidate.resolve().is_relative_to(root)
                    and (scope is None or scope.allows(candidate)))

        connection.create_function("inside_root", 1, inside_root)
        rows = connection.execute("""
            SELECT files.path,
                   snippet(files_fts, -1, '[', ']', ' … ', 24),
                   bm25(files_fts, 5.0, 1.0, 2.0, 2.0, 2.0) AS score
            FROM files_fts JOIN files ON files.id = files_fts.rowid
            WHERE files_fts MATCH ? AND files.is_present = 1
                  AND inside_root(files.path)
            ORDER BY score, files.path
            LIMIT ?
        """, (match, limit)).fetchall()
    return [SearchResult(*row) for row in rows]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Search indexed files by keyword (read-only).")
    parser.add_argument("directory")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        results = search_files(args.directory, args.query, limit=args.limit)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Search failed: {error}\nInitialize/upgrade the database before searching.\n")
    for result in results:
        print(f"{result.path}\n  {result.snippet}\n")
    print(f"{len(results)} result(s).")


if __name__ == "__main__":
    main()
