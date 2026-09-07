from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import migrations


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)

    def version(self):
        return self.connection.execute("PRAGMA user_version").fetchone()[0]

    def test_fresh_database_and_repeat_startup(self):
        migrations.migrate(self.connection)
        self.assertEqual(self.version(), len(migrations.MIGRATIONS))
        before = list(self.connection.iterdump())
        migrations.migrate(self.connection)
        self.assertEqual(list(self.connection.iterdump()), before)

    def test_unversioned_legacy_database_preserves_records(self):
        self.connection.execute("""CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL, extension TEXT, size INTEGER, modified REAL,
            content TEXT, category TEXT, subcategory TEXT, description TEXT,
            confidence REAL)""")
        self.connection.execute("""INSERT INTO files
            (id, path, filename, content, category) VALUES
            (42, '/report.txt', 'report.txt', 'important text', 'Work')""")
        self.connection.commit()
        migrations.migrate(self.connection)
        row = self.connection.execute(
            "SELECT id, content, category, is_present, hash FROM files"
        ).fetchone()
        self.assertEqual(row, (42, "important text", "Work", 1, None))
        self.assertEqual(self.version(), len(migrations.MIGRATIONS))

    def test_current_unversioned_database_preserves_action_history(self):
        migrations._initial_schema(self.connection)
        self.connection.execute("""INSERT INTO actions
            (action_type, source, destination, status)
            VALUES ('move', '/old', '/new', 'executed')""")
        self.connection.commit()
        before = self.connection.execute("SELECT * FROM actions").fetchall()
        migrations.migrate(self.connection)
        self.assertEqual(self.connection.execute("SELECT * FROM actions").fetchall(), before)

    def test_failed_upgrade_rolls_back_schema_data_and_version(self):
        migrations.migrate(self.connection)
        before = list(self.connection.iterdump())

        def first(connection):
            connection.execute("ALTER TABLE files ADD COLUMN extra TEXT")

        def failing(connection):
            connection.execute("INSERT INTO files (path, filename) VALUES ('x', 'x')")
            raise RuntimeError("upgrade failed")

        with patch.object(migrations, "MIGRATIONS", migrations.MIGRATIONS + (first, failing)):
            with self.assertRaisesRegex(RuntimeError, "upgrade failed"):
                migrations.migrate(self.connection)
        self.assertEqual(list(self.connection.iterdump()), before)
        self.assertEqual(self.version(), len(migrations.MIGRATIONS))

    def test_newer_database_is_rejected(self):
        self.connection.execute("PRAGMA user_version = 999")
        with self.assertRaisesRegex(ValueError, "newer than supported"):
            migrations.migrate(self.connection)
        self.assertEqual(self.version(), 999)


if __name__ == "__main__":
    unittest.main()
