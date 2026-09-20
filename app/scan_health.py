"""Persistent scan safety markers, separate from indexed content/classification."""
from contextlib import closing
from pathlib import Path
import sqlite3
import database


def load_issues():
    path = Path(database.DATABASE).resolve()
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='scan_issues'").fetchone():
            return []
        return [dict(row) for row in db.execute('SELECT * FROM scan_issues')]


def verification_error(path, issues=None):
    path = Path(path).absolute()
    for issue in load_issues() if issues is None else issues:
        if str(path) == issue['path'] or (issue['kind']=='directory' and path.is_relative_to(issue['path'])):
            return f"Scan verification required: {issue['message']}"
    return ''


def record_scan(root, files, stats, scope=None, *, finished=True):
    # Standalone scanner callers may have no database. The app migrates on startup.
    if not Path(database.DATABASE).exists():
        return
    with closing(database.get_connection()) as db, db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='scan_issues'").fetchone():
            return
        if finished:
            # Previously failed files are forced through fresh hashing before clearing.
            db.executemany('DELETE FROM scan_issues WHERE path=? AND kind=\'file\'', ((f.path,) for f in files))
            for path, kind in db.execute('SELECT path,kind FROM scan_issues').fetchall():
                covered = Path(path).is_relative_to(root) and (scope is None or scope.allows(path, directory=kind=='directory'))
                directory_failed = any(i.kind == 'directory' and
                                       (Path(i.path).is_relative_to(path) or Path(path).is_relative_to(i.path))
                                       for i in stats.issues)
                if covered and (stats.complete or (kind == 'directory' and not directory_failed)):
                    db.execute('DELETE FROM scan_issues WHERE path=?', (path,))
        for issue in stats.issues:
            db.execute('INSERT OR REPLACE INTO scan_issues VALUES (?,?,?)', (issue.path, issue.kind, issue.message))
            if issue.kind == 'directory':
                # Keep excluded descendants blocked if a future narrower scan succeeds.
                for (path,) in db.execute('SELECT path FROM files'):
                    if Path(path).is_relative_to(issue.path):
                        db.execute('INSERT OR REPLACE INTO scan_issues VALUES (?,\'file\',?)', (path, issue.message))
