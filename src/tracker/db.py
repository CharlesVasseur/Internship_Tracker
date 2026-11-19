"""SQLite connection and schema initialisation helpers."""

from importlib.resources import files
import sqlite3
from os import PathLike


def connect(path: str | PathLike[str]) -> sqlite3.Connection:
    """Open a SQLite database with foreign keys and row results enabled."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.row_factory = sqlite3.Row
    return connection


def init_db(path: str | PathLike[str]) -> None:
    """Create the tracker tables and indexes if they do not already exist."""
    schema = files("tracker").joinpath("schema.sql").read_text(encoding="utf-8")
    with connect(path) as connection:
        connection.executescript(schema)
