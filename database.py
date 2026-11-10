"""PostgreSQL database adapter for the MRA attendance app.

Requires DATABASE_URL in the environment. Does not connect to SQLite.
"""
import os
import re
from contextlib import contextmanager
import psycopg2
from psycopg2.extras import DictCursor

_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+", re.I)

class Result:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid
    def fetchone(self):
        return self._cursor.fetchone()
    def fetchall(self):
        return self._cursor.fetchall()
    @property
    def rowcount(self):
        return self._cursor.rowcount

class Connection:
    def __init__(self, raw):
        self.raw = raw
    def execute(self, sql, params=()):
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            return None  # psycopg2 begins transactions automatically
        sql = sql.replace("?", "%s")
        insert = bool(_INSERT.match(sql))
        if insert and "RETURNING" not in sql.upper():
            if re.match(r"^\s*INSERT\s+INTO\s+attendance_photos\b", sql, re.I):
                sql = sql.rstrip().rstrip(";") + " RETURNING filename"
            else:
                sql = sql.rstrip().rstrip(";") + " RETURNING id"
        cursor = self.raw.cursor(cursor_factory=DictCursor)
        cursor.execute(sql, tuple(params))
        lastrowid = None
        if insert:
            record = cursor.fetchone()
            lastrowid = record[0] if record else None
        return Result(cursor, lastrowid)
    def commit(self):
        self.raw.commit()
    def rollback(self):
        self.raw.rollback()
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.raw.commit()
            else:
                self.raw.rollback()
        finally:
            self.raw.close()


def get_connection():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set to the Supabase PostgreSQL connection string")
    from urllib.parse import urlsplit, unquote

    parsed = urlsplit(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL")

    return Connection(psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=parsed.path.lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        connect_timeout=12,
        sslmode="require"
    ))


def initialize_database():
    # Existing five Supabase staging tables are retained. No destructive migrations.
    with get_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS attendance_photos (
            filename TEXT PRIMARY KEY,
            image_data BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        for table in ("employees", "attendance", "transfers", "branches", "gps_exception_requests"):
            found = conn.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0]
            if found is None:
                raise RuntimeError(f"Required staging table missing: {table}")

if __name__ == "__main__":
    initialize_database()
    print("PostgreSQL connection initialized")
