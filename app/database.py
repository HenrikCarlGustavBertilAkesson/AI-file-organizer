import sqlite3
from models import File

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
            error TEXT
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
            file.confidence
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
            confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, data)

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