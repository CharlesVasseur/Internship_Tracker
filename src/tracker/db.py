"""SQLite connection and schema initialisation helpers."""

from importlib.resources import files
import sqlite3
from os import PathLike

SCHEMA_VERSION = 2


def connect(path: str | PathLike[str]) -> sqlite3.Connection:
    """Open a SQLite database with foreign keys and row results enabled."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.row_factory = sqlite3.Row
    return connection


def init_db(path: str | PathLike[str]) -> None:
    """Create or migrate the tracker schema to the supported version."""
    schema = files("tracker").joinpath("schema.sql").read_text(encoding="utf-8")
    connection = connect(path)
    try:
        version_row = connection.execute("PRAGMA user_version").fetchone()
        version = int(version_row[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {version} is newer than this program "
                "supports (2)"
            )
        connection.executescript(schema)
        if version < SCHEMA_VERSION:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(opportunities)"
                )
            }
            if "deadline" not in columns:
                connection.execute(
                    "ALTER TABLE opportunities ADD COLUMN deadline TEXT"
                )
            connection.execute(
                f"PRAGMA user_version = {SCHEMA_VERSION}"
            )
    finally:
        connection.close()
