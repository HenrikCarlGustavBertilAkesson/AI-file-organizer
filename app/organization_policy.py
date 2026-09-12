"""Reviewed category destinations and deterministic protection of project structures."""
from contextlib import closing
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import os
import sqlite3

from workspace import load_scope, PROJECT_MARKERS, excluded, DEFAULT_EXCLUSIONS


@dataclass
class OrganizationPolicy:
    root: str
    destinations: dict[str, str]
    protected_folders: list[str]
    version: int = 1


def load_policy(root):
    import database
    path = Path(database.DATABASE).resolve()
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='organization_policies'").fetchone():
            return None
        row = connection.execute('SELECT configuration,version FROM organization_policies WHERE root=?',
                                 (str(Path(root).expanduser().resolve()),)).fetchone()
    if not row:
        return None
    config = json.loads(row[0])
    return OrganizationPolicy(**config, version=row[1])


def relative_folder(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError('Enter a relative destination/protected folder path (up to 500 characters).')
    value = value.strip()
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or '\\' in value or '\x00' in value or value.startswith('~'):
        raise ValueError('Folder paths must stay relative to the workspace, without .. or ~.')
    return str(path)


def protected_reason(path, root, protected_folders=()):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.resolve().is_relative_to(root):
        return 'Path is outside the workspace.'
    for folder in protected_folders:
        if path.resolve().is_relative_to(root / folder):
            return f'Protected folder: {folder}'
    # Examine only ancestors, not the whole workspace. New project markers are
    # detected again immediately before a move, even after proposal creation.
    directory = path if path.is_dir() else path.parent
    while directory.is_relative_to(root):
        if directory.exists():
            try:
                with os.scandir(directory) as entries:
                    if any(entry.name in PROJECT_MARKERS or entry.name.endswith('.xcodeproj') for entry in entries):
                        return f'Protected project structure: {directory}'
            except OSError as error:
                return f'Cannot verify project protection: {error}'
        if directory == root:
            break
        directory = directory.parent
    return ''


def save_policy(root, rules, protected_folders):
    import database
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Choose an existing workspace.')
    if not isinstance(rules, list) or not 1 <= len(rules) <= 50:
        raise ValueError('Add between 1 and 50 category rules.')
    if not isinstance(protected_folders, list) or len(protected_folders) > 100:
        raise ValueError('Use at most 100 protected folder paths.')
    protected = sorted(set(relative_folder(value) for value in protected_folders))
    for folder in protected:
        if not (root / folder).resolve().is_relative_to(root):
            raise ValueError('Protected folder resolves outside the workspace.')
    scope = load_scope(root)
    destinations = {}
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError('Each rule needs a category and folder.')
        category = rule.get('category')
        if not isinstance(category, str) or not category.strip() or len(category) > 100:
            raise ValueError('Each category must contain 1–100 characters.')
        category = category.strip().casefold()
        if category in destinations:
            raise ValueError('Each category can have only one destination.')
        folder = relative_folder(rule.get('folder'))
        target = root / folder
        if not target.resolve().is_relative_to(root):
            raise ValueError('Destination resolves outside the workspace.')
        current = root
        for part in Path(folder).parts:
            current /= part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise ValueError('Destinations cannot contain symlinks or existing files.')
        if excluded(Path(folder).parts, scope.exclusions if scope else DEFAULT_EXCLUSIONS):
            raise ValueError('Destination is an excluded folder or bundle.')
        if scope and not scope.allows(target / '__destination_check__'):
            raise ValueError(f'{folder} is outside the saved scope. Choose a destination under a selected folder.')
        reason = protected_reason(target, root, protected)
        if reason:
            raise ValueError(reason)
        destinations[category] = folder
    config = {'root': str(root), 'destinations': destinations, 'protected_folders': protected}
    with closing(database.get_connection()) as connection, connection:
        connection.execute('''INSERT INTO organization_policies(root,configuration,version) VALUES (?,?,1)
            ON CONFLICT(root) DO UPDATE SET configuration=excluded.configuration,
            version=organization_policies.version+1, updated_at=CURRENT_TIMESTAMP''',
            (str(root), json.dumps(config)))
    return load_policy(root)


def validate_policy_action(action, root):
    import database
    policy = load_policy(root)
    protected = policy.protected_folders if policy else []
    for path in (action.source, action.destination):
        reason = protected_reason(path, root, protected)
        if reason:
            return reason
    if policy is None:
        return ''  # Legacy manual proposals retain the existing approval workflow.
    row = database.get_file_by_path(str(Path(action.source).resolve()))
    if not row or not row['is_present'] or row['status'] != 'classified':
        return 'Classify this file before proposing a policy-based move.'
    folder = policy.destinations.get((row['category'] or '').strip().casefold())
    if folder is None:
        return 'No saved destination for this category. Add a rule or leave the file in place.'
    expected = (Path(root) / folder / Path(action.source).name).resolve()
    if Path(action.destination).resolve() != expected:
        return f'The saved policy requires destination: {expected}'
    return ''


def policy_view(root):
    root = Path(root).expanduser().resolve()
    policy = load_policy(root)
    if policy:
        return asdict(policy)
    scope = load_scope(root)
    # Draft only: names reuse an existing matching selected folder where possible.
    categories = ['Finance', 'Work', 'Personal', 'Legal', 'Education', 'Travel', 'Programming', 'Taxes', 'Receipts', 'Other']
    destinations = {}
    base = scope.folders[0] if scope and scope.folders else ''
    for category in categories:
        existing = next((name for name in (scope.folders if scope else [])
                         if name.casefold() == category.casefold()), None)
        destinations[category.casefold()] = existing or str(Path(base) / category)
    return {'root': str(root), 'destinations': destinations, 'protected_folders': [], 'version': 0}
