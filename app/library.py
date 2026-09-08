"""Bounded dashboard queries with SQL filtering, counts, and stable pagination."""
from contextlib import closing
from pathlib import Path
import math
import re
import sqlite3

import database
from workspace import load_scope


def page_number(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f'{name} must be a positive integer.')
    return value


def library_page(root, *, page=1, page_size=50, status='', category=None, query='', action_page=1,
                 subdirectory=None):
    page_number(page, 'Page')
    page_number(action_page, 'Proposal page')
    page_number(page_size, 'Page size')
    if page_size > 100:
        raise ValueError('Page size cannot exceed 100.')
    if status not in ('', 'pending', 'classified', 'unsupported', 'empty', 'failed', 'missing'):
        raise ValueError('Unknown status filter.')
    if category is not None and not isinstance(category, str):
        raise ValueError('Category must be text.')
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError('Search must be text of at most 1000 characters.')
    root = Path(root).expanduser().resolve()
    directory = Path(subdirectory).resolve() if subdirectory else root
    if not directory.is_relative_to(root):
        raise ValueError('Directory is outside the workspace.')
    scope = load_scope(root)
    def allowed(value):
        path = Path(value)
        return (path.is_relative_to(directory) and path.resolve().is_relative_to(root)
                and (scope is None or scope.allows(path)))
    uri = Path(database.DATABASE).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function('in_scope', 1, allowed)
        connection.execute('BEGIN')
        base = 'in_scope(files.path)'
        summary = dict(connection.execute(f'''SELECT
            count(CASE WHEN is_present=1 THEN 1 END) AS total,
            count(CASE WHEN is_present=1 AND status='pending' THEN 1 END) AS pending
            FROM files WHERE {base}''').fetchone())
        categories = [row[0] for row in connection.execute(
            f"SELECT DISTINCT coalesce(category,'') FROM files WHERE {base} ORDER BY 1")]
        conditions = [base, 'files.is_present = ?']
        parameters = [0 if status == 'missing' else 1]
        if status and status != 'missing':
            conditions.append('files.status = ?')
            parameters.append(status)
        if category is not None:
            if category:
                conditions.append('files.category = ?')
                parameters.append(category)
            else:
                conditions.append("(files.category IS NULL OR files.category = '')")
        source = 'files'
        snippet = "'' AS snippet"
        order = 'files.path'
        if query.strip():
            words = re.findall(r'[^\W_]+', query, flags=re.UNICODE)
            if words:
                source += ' JOIN files_fts ON files_fts.rowid = files.id'
                conditions.append('files_fts MATCH ?')
                parameters.append(' AND '.join('"' + word + '"' for word in words))
                snippet = "snippet(files_fts,-1,'[',']',' … ',24) AS snippet"
                order = 'bm25(files_fts,5.0,1.0,2.0,2.0,2.0), files.path'
            else:
                conditions.append('0')
        where = ' AND '.join(conditions)
        total = connection.execute(f'SELECT count(*) FROM {source} WHERE {where}', parameters).fetchone()[0]
        pages = max(1, math.ceil(total / page_size))
        page = min(page, pages)
        rows = connection.execute(f'''SELECT files.path, files.filename,
            files.category, substr(files.description,1,400) AS description,
            files.status, files.is_present, {snippet}
            FROM {source} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?''',
            (*parameters, page_size, (page-1)*page_size)).fetchall()
        action_where = "status='pending' AND in_scope(source) AND in_scope(destination)"
        action_count = connection.execute(f'SELECT count(*) FROM actions WHERE {action_where}').fetchone()[0]
        action_pages = max(1, math.ceil(action_count / 20))
        action_page = min(action_page, action_pages)
        actions = connection.execute(f'''SELECT id, action_type, source, destination, reason, status, error
            FROM actions WHERE {action_where} ORDER BY id LIMIT 20 OFFSET ?''',
            ((action_page-1)*20,)).fetchall()
    return {'files': [dict(row) for row in rows], 'summary': summary, 'categories': categories,
            'pagination': {'page': page, 'page_size': page_size, 'total': total, 'pages': pages},
            'actions': [dict(row) for row in actions],
            'action_pagination': {'page': action_page, 'pages': action_pages, 'total': action_count}}
