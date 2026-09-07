from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import database
from scanner import scan_file
from search import search_files
from reconciliation import reconcile_directory


class SearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "files"
        self.root.mkdir()
        override = patch.object(database, "DATABASE", str(self.base / "index.db"))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()

    def add(self, name, content="", **fields):
        path = self.root / name
        path.write_text(content)
        file = scan_file(str(path))
        file.content = content
        for key, value in fields.items():
            setattr(file, key, value)
        database.save_file(file)
        return file

    def search(self, query, **kwargs):
        return search_files(str(self.root), query, **kwargs)

    def test_ranked_results_and_snippets_are_read_only(self):
        strongest = self.add("employment.txt", "contract details")
        self.add("notes.txt", "employment contract details")
        before = Path(database.DATABASE).read_bytes()
        results = self.search("employment contract")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].path, strongest.path)
        self.assertLessEqual(results[0].score, results[1].score)
        self.assertTrue(all("[" in result.snippet for result in results))
        self.assertEqual(Path(database.DATABASE).read_bytes(), before)

    def test_scope_and_presence_filter_before_limit(self):
        self.add("missing.txt", "contract", is_present=False)
        self.add("../outside.txt", "contract")
        inside = self.add("inside.txt", "contract")
        self.assertEqual([r.path for r in self.search("contract", limit=1)], [inside.path])

    def test_classification_fields_are_searchable(self):
        file = self.add("file.txt", category="Finance", subcategory="Invoice",
                        description="Broadband payment")
        self.assertEqual([r.path for r in self.search("finance invoice broadband")], [file.path])

    def test_move_modification_and_missing_reconciliation(self):
        file = self.add("old.txt", "contract")
        target = self.root / "new.txt"
        Path(file.path).rename(target)
        reconcile_directory(str(self.root), apply=True)
        self.assertEqual(self.search("contract")[0].path, str(target))
        self.assertEqual(self.search("old"), [])
        target.write_text("replacement")
        reconcile_directory(str(self.root), apply=True)
        self.assertEqual(self.search("contract"), [])
        self.assertEqual(self.search("new")[0].path, str(target))
        target.unlink()
        reconcile_directory(str(self.root), apply=True)
        self.assertEqual(self.search("new"), [])

    def test_bulk_update_and_delete_keep_index_synchronized(self):
        file = self.add("file.txt", "obsolete")
        file.content = "replacement"
        database.save_files([file])
        self.assertEqual(self.search("obsolete"), [])
        self.assertEqual(len(self.search("replacement")), 1)
        with closing(database.get_connection()) as connection, connection:
            connection.execute("DELETE FROM files WHERE path = ?", (file.path,))
        self.assertEqual(self.search("replacement"), [])

    def test_migration_backfills_existing_files(self):
        with closing(database.get_connection()) as connection, connection:
            connection.execute("DROP TRIGGER files_search_insert")
            connection.execute("DROP TRIGGER files_search_update")
            connection.execute("DROP TRIGGER files_search_delete")
            connection.execute("DROP TABLE files_fts")
            connection.execute("DROP TABLE workspaces")
            connection.execute("DROP TABLE jobs")
            connection.execute("PRAGMA user_version = 2")
        file = self.add("legacy.txt", "contract")
        database.create_database()
        self.assertEqual(self.search("contract")[0].path, file.path)

    def test_plain_text_queries_and_limits(self):
        self.add("file.txt", "contract")
        self.assertEqual(self.search("   !!!"), [])
        self.assertEqual(len(self.search('"contract"*')), 1)
        self.assertEqual(self.search("contract OR nonexistent"), [])
        for limit in (0, 101):
            with self.assertRaises(ValueError):
                self.search("contract", limit=limit)


if __name__ == "__main__":
    unittest.main()
