#!/usr/bin/env python3
"""Back up a stopped BookTalks library without changing its source files."""
import argparse
import sqlite3
import tempfile
import zipfile
from pathlib import Path


def backup(data: Path, database: Path, destination: Path) -> None:
    data, database, destination = data.resolve(), database.resolve(), destination.resolve()
    if destination.is_relative_to(data):
        raise ValueError("Save the backup outside the library directory.")
    if not database.is_file():
        raise ValueError(f"Library database not found: {database}")
    # SQLite's backup API includes committed WAL data, unlike copying .db alone.
    with tempfile.TemporaryDirectory() as scratch:
        snapshot = Path(scratch) / "booktalks.db"
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source:
            with sqlite3.connect(snapshot) as target:
                source.backup(target)
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
            archive.write(snapshot, "booktalks.db")
            for folder in ("uploads", "audio"):
                root = data / folder
                for path in sorted(root.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        archive.write(path, path.relative_to(data))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__ + " Stop the server first.")
    parser.add_argument("destination", type=Path, help="New ZIP file, outside the data directory")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--database", type=Path, help="Custom BOOKTALKS_DB_PATH, if configured")
    args = parser.parse_args()
    backup(args.data_dir, args.database or args.data_dir / "booktalks.db", args.destination)
    print(f"Backup saved to {args.destination.resolve()}")
