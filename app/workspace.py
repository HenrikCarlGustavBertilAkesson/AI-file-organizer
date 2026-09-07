"""Metadata-only folder inventory and persisted operation boundaries."""
from dataclasses import dataclass, asdict
from pathlib import Path
from contextlib import closing
import json
import os
import sqlite3
import stat

DEFAULT_EXCLUSIONS = ['.git', '.venv', 'venv', 'node_modules', '__pycache__']
BUNDLES = ('.app', '.bundle', '.framework')
PROJECT_MARKERS = {'.git', 'pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod', '.xcodeproj'}


def excluded(parts, exclusions):
    return any(part in exclusions or part.lower().endswith(BUNDLES) for part in parts)


@dataclass
class WorkspaceScope:
    root: str
    folders: list[str]
    loose_files: bool
    exclusions: list[str]

    def allows(self, value, *, directory=False):
        root = Path(self.root)
        path = Path(value)
        try:
            relative = path.relative_to(root)
            resolved = path.resolve().relative_to(root)
        except (ValueError, OSError, RuntimeError):
            return False
        for parts in (relative.parts, resolved.parts):
            if excluded(parts, self.exclusions):
                return False
            if not parts:
                if directory:
                    continue
                return False
            if len(parts) == 1 and not directory:
                if not (self.loose_files or parts[0] in self.folders):
                    return False
            elif parts[0] not in self.folders:
                return False
        # Never follow symlinks in a workspace, even when the target is inside.
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
        return True


def load_scope(root):
    import database
    path = Path(database.DATABASE).resolve()
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='workspaces'").fetchone():
            return None
        row = connection.execute('SELECT configuration FROM workspaces WHERE root = ?',
                                 (str(Path(root).expanduser().resolve()),)).fetchone()
    return WorkspaceScope(**json.loads(row[0])) if row else None


def save_scope(root, folders, loose_files, exclusions):
    import database
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing folder.')
    if not isinstance(folders, list) or not isinstance(exclusions, list) or not isinstance(loose_files, bool):
        raise ValueError('Invalid workspace selection.')
    for name in folders + exclusions:
        if not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\\' in name:
            raise ValueError('Use folder names without path separators for selections and exclusions.')
    for name in folders:
        path = root / name
        if not path.is_dir() or path.is_symlink() or excluded((name,), exclusions):
            raise ValueError(f'Cannot include excluded or unavailable folder: {name}')
    scope = WorkspaceScope(str(root), sorted(set(folders)), loose_files, sorted(set(exclusions)))
    with closing(database.get_connection()) as connection, connection:
        connection.execute('INSERT INTO workspaces(root, configuration) VALUES (?, ?) '
                           'ON CONFLICT(root) DO UPDATE SET configuration=excluded.configuration',
                           (str(root), json.dumps(asdict(scope))))
    return scope


def inventory(directory, exclusions=None):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing folder.')
    exclusions = DEFAULT_EXCLUSIONS if exclusions is None else exclusions
    groups = {'.': {'name': '.', 'files': 0, 'bytes': 0, 'project': False}}
    errors, skipped = [], []

    def visit(path, group=None):
        try:
            with os.scandir(path) as iterator:
                entries = list(iterator)
        except OSError as error:
            errors.append({'path': str(path), 'error': str(error)})
            return
        project = any(entry.name in PROJECT_MARKERS or entry.name.endswith('.xcodeproj') for entry in entries)
        if project:
            groups[group or '.']['project'] = True
        for entry in entries:
            child = Path(entry.path)
            try:
                if entry.is_symlink():
                    skipped.append({'path': str(child), 'reason': 'Symbolic link'})
                    continue
                if excluded((entry.name,), exclusions):
                    skipped.append({'path': str(child), 'reason': 'Excluded name or application bundle'})
                    continue
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    key = group or entry.name
                    if key not in groups:
                        groups[key] = {'name': key, 'files': 0, 'bytes': 0, 'project': project}
                    visit(child, key)
                elif stat.S_ISREG(info.st_mode):
                    bucket = groups[group or '.']
                    bucket['files'] += 1
                    bucket['bytes'] += info.st_size
            except OSError as error:
                errors.append({'path': str(child), 'error': str(error)})
    visit(root)
    saved = load_scope(root)
    return {'root': str(root), 'groups': list(groups.values()), 'errors': errors,
            'skipped': skipped, 'exclusions': exclusions, 'complete': not errors,
            'scope': asdict(saved) if saved else None}
