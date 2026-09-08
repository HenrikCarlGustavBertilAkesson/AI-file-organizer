"""Ordered SQLite upgrades. Append migrations; never edit released versions."""
import sqlite3


def _initial_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            extension TEXT,

            size INTEGER,
            modified REAL,
            hash TEXT,

            content TEXT,

            category TEXT,
            subcategory TEXT,
            description TEXT,
            confidence REAL,

            status TEXT,
            error TEXT,

            is_present INTEGER NOT NULL DEFAULT 1
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            action_type TEXT NOT NULL,
            source TEXT NOT NULL,
            destination TEXT NOT NULL,
            reason TEXT,

            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _legacy_file_columns(connection: sqlite3.Connection) -> None:
    # Unversioned installations may predate these file-index fields.
    columns = {row[1] for row in connection.execute("PRAGMA table_info(files)")}
    additions = {
        "hash": "TEXT",
        "status": "TEXT",
        "error": "TEXT",
        "is_present": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")


def _search_index(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE VIRTUAL TABLE files_fts USING fts5(
        filename, content, category, subcategory, description,
        content='files', content_rowid='id'
    )""")
    connection.execute("""CREATE TRIGGER files_search_insert AFTER INSERT ON files BEGIN
        INSERT INTO files_fts(rowid, filename, content, category, subcategory, description)
        VALUES (new.id, new.filename, new.content, new.category, new.subcategory, new.description);
    END""")
    connection.execute("""CREATE TRIGGER files_search_delete AFTER DELETE ON files BEGIN
        INSERT INTO files_fts(files_fts, rowid, filename, content, category, subcategory, description)
        VALUES ('delete', old.id, old.filename, old.content, old.category, old.subcategory, old.description);
    END""")
    connection.execute("""CREATE TRIGGER files_search_update AFTER UPDATE ON files BEGIN
        INSERT INTO files_fts(files_fts, rowid, filename, content, category, subcategory, description)
        VALUES ('delete', old.id, old.filename, old.content, old.category, old.subcategory, old.description);
        INSERT INTO files_fts(rowid, filename, content, category, subcategory, description)
        VALUES (new.id, new.filename, new.content, new.category, new.subcategory, new.description);
    END""")
    connection.execute("INSERT INTO files_fts(files_fts) VALUES ('rebuild')")


def _workspace_scopes(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE workspaces (
        root TEXT PRIMARY KEY, configuration TEXT NOT NULL
    )''')


def _background_jobs(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE jobs (
        id INTEGER PRIMARY KEY, operation TEXT NOT NULL, root TEXT NOT NULL,
        parameters TEXT NOT NULL, status TEXT NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0, total INTEGER,
        failures INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
        result TEXT, parent_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')


def _library_indexes(connection: sqlite3.Connection) -> None:
    connection.execute('CREATE INDEX files_presence_status_path ON files(is_present,status,path)')
    connection.execute('CREATE INDEX files_presence_category_path ON files(is_present,category,path)')
    connection.execute('CREATE INDEX actions_status_id ON actions(status,id)')


def _job_ai_usage(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE jobs ADD COLUMN usage TEXT NOT NULL DEFAULT '{}'")


MIGRATIONS = (_initial_schema, _legacy_file_columns, _search_index, _workspace_scopes,
              _background_jobs, _library_indexes, _job_ai_usage)


def migrate(connection: sqlite3.Connection) -> None:
    """Apply all outstanding versions atomically on an idle connection."""
    if connection.in_transaction:
        raise ValueError("Migrations require a connection without an active transaction")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise ValueError(
                f"Database schema version {version} is newer than supported "
                f"version {len(MIGRATIONS)}"
            )
        for index in range(version, len(MIGRATIONS)):
            MIGRATIONS[index](connection)
            connection.execute(f"PRAGMA user_version = {index + 1}")
