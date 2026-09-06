import sqlite3
from models import File, ProposedAction, ActionStatus
from pathlib import Path

DATABASE = "files.db"

def get_connection():
    return sqlite3.connect(DATABASE)


def create_database():
    connection = get_connection()

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

    connection.commit()
    connection.close()

def save_files(files: list[File]):
    connection = sqlite3.connect(DATABASE)

    data = [
        (
            file.path,
            file.filename,
            file.extension,
            file.size,
            file.modified,
            file.content,
            file.category,
            file.subcategory,
            file.description,
            file.confidence,
            int(file.is_present)
        )
        for file in files
    ]

    connection.executemany("""
        INSERT OR REPLACE INTO files
        (
            path,
            filename,
            extension,
            size,
            modified,
            content,
            category,
            subcategory,
            description,
            confidence,
            is_present
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, data)

    connection.commit()
    connection.close()

def save_file(file: File):
    connection = get_connection()

    connection.execute("""
        INSERT INTO files (
            path,
            filename,
            extension,
            size,
            modified,
            hash,
            content,
            category,
            subcategory,
            description,
            confidence,
            status,
            error,
            is_present
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(path) DO UPDATE SET

            filename = excluded.filename,
            extension = excluded.extension,
            size = excluded.size,
            modified = excluded.modified,
            hash = excluded.hash,
            content = excluded.content,

            category = excluded.category,
            subcategory = excluded.subcategory,
            description = excluded.description,
            confidence = excluded.confidence,

            status = excluded.status,
            error = excluded.error
    """, (
        file.path,
        file.filename,
        file.extension,
        file.size,
        file.modified,
        file.hash,
        file.content,
        file.category,
        file.subcategory,
        file.description,
        file.confidence,
        file.status,
        file.error,
        int(file.is_present),
    ))

    connection.commit()
    connection.close()

def get_file_by_path(path: str):
    connection = get_connection()

    connection.row_factory = sqlite3.Row

    result = connection.execute("""
        SELECT *
        FROM files
        WHERE path = ?
    """, (path,)).fetchone()

    connection.close()

    if result is None:
        return None

    return dict(result)

def get_all_files():
    connection = get_connection()

    connection.row_factory = sqlite3.Row

    rows = connection.execute("""
        SELECT *
        FROM files
    """).fetchall()

    connection.close()

    return [dict(row) for row in rows]

def update_file_path(
    old_path: str,
    new_path: str,
) -> bool:

    old_path = str(Path(old_path).resolve())
    new_path = str(Path(new_path).resolve())

    connection = get_connection()

    cursor = connection.execute("""
        UPDATE files
        SET
            path = ?,
            filename = ?,
            is_present = 1
        WHERE path = ?
    """, (
        new_path,
        Path(new_path).name,
        old_path,
    ))

    connection.commit()

    updated = cursor.rowcount > 0

    connection.close()

    return updated

def save_action(action: ProposedAction) -> ProposedAction:
    connection = get_connection()
    connection.row_factory = sqlite3.Row

    existing = connection.execute("""
        SELECT id
        FROM actions
        WHERE
            action_type = 'move'
            AND source = ?
            AND status = 'pending'
    """, (
        action.source,
    )).fetchone()

    if existing:
        connection.execute("""
            UPDATE actions
            SET
                destination = ?,
                reason = ?,
                error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            action.destination,
            action.reason,
            existing["id"],
        ))

        connection.commit()

        action.id = existing["id"]
        connection.close()

        return action

    cursor = connection.execute("""
        INSERT INTO actions (
            action_type,
            source,
            destination,
            reason,
            status,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        action.action_type,
        action.source,
        action.destination,
        action.reason,
        action.status,
        action.error,
    ))

    connection.commit()
    action.id = cursor.lastrowid
    connection.close()

    return action

def update_action(action: ProposedAction):
    if action.id is None:
        raise ValueError("Cannot update an action without an id.")

    connection = get_connection()

    connection.execute("""
        UPDATE actions
        SET
            status = ?,
            error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        action.status,
        action.error,
        action.id,
    ))

    connection.commit()
    connection.close()

def get_pending_actions() -> list[ProposedAction]:
    connection = get_connection()

    connection.row_factory = sqlite3.Row

    rows = connection.execute("""
        SELECT *
        FROM actions
        WHERE status = ?
        ORDER BY id
    """, (
        ActionStatus.PENDING,
    )).fetchall()

    connection.close()

    return [
        ProposedAction(
            id=row["id"],
            action_type=row["action_type"],
            source=row["source"],
            destination=row["destination"],
            reason=row["reason"] or "",
            status=row["status"],
            error=row["error"] or "",
        )
        for row in rows
    ]

def fail_other_pending_moves(
    source: str,
    executed_action_id: int,
):
    connection = get_connection()

    connection.execute("""
        UPDATE actions
        SET
            status = 'failed',
            error = 'Source was moved by another approved action',
            updated_at = CURRENT_TIMESTAMP
        WHERE
            action_type = 'move'
            AND source = ?
            AND status = 'pending'
            AND id != ?
    """, (
        source,
        executed_action_id,
    ))

    connection.commit()
    connection.close()