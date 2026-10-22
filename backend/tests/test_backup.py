import importlib.util
import sqlite3
import zipfile
from pathlib import Path

import pytest


def test_backup_preserves_wal_and_files_and_never_overwrites(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "backup.py"
    spec = importlib.util.spec_from_file_location("backup_script", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    (data / "uploads" / "1.pdf").write_bytes(b"source PDF")
    database = data / "booktalks.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE books (title TEXT)")
        connection.execute("INSERT INTO books VALUES ('Saved book')")
        connection.commit()
        archive = tmp_path / "backup.zip"
        module.backup(data, database, archive)
        restored = tmp_path / "restored"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(restored)
        with sqlite3.connect(restored / "booktalks.db") as restored_db:
            assert restored_db.execute("SELECT title FROM books").fetchone()[0] == "Saved book"
        assert (restored / "uploads" / "1.pdf").read_bytes() == b"source PDF"
        with pytest.raises(FileExistsError):
            module.backup(data, database, archive)
        with pytest.raises(ValueError):
            module.backup(data, database, data / "bad.zip")
    finally:
        connection.close()
