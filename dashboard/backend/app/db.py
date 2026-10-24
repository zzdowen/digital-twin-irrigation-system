"""本项目自己的 SQLite（与上游 irrigation_history.db 完全分离）。

只负责建表与连接；业务查询在 auth.py / db.py 里。
所有写入必须 BEGIN IMMEDIATE + commit，并开 WAL + busy_timeout。
"""
import sqlite3
import threading
from datetime import datetime, timezone, timedelta

from . import config

_LOCAL = threading.local()
_INIT_LOCK = threading.Lock()
_initialized = False

CST = timezone(timedelta(hours=8))


def now_iso() -> str:
    """带时区的本地时间字符串（北京时间）。"""
    return datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S")


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username    TEXT PRIMARY KEY,
    pwd_hash    TEXT NOT NULL,
    salt        TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'viewer',
    created     TEXT NOT NULL,
    disabled    INTEGER NOT NULL DEFAULT 0,
    must_change INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tokens (
    token   TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created INTEGER NOT NULL,
    expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(username);

CREATE TABLE IF NOT EXISTS login_fails (
    username   TEXT PRIMARY KEY,
    count      INTEGER NOT NULL DEFAULT 0,
    locked_ts  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS samples (
    ts         INTEGER PRIMARY KEY,
    ch1        REAL, ch2 REAL, ch3 REAL,
    pump1      INTEGER, pump2 INTEGER, pump3 INTEGER,
    avg        REAL,
    source     TEXT,
    upstream_ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    rule        TEXT NOT NULL,
    level       TEXT NOT NULL,
    channel     INTEGER,
    message     TEXT NOT NULL,
    resolved_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(rule, channel, resolved_ts);

CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    username TEXT NOT NULL,
    action   TEXT NOT NULL,
    detail   TEXT,
    ip       TEXT,
    ok       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def connect() -> sqlite3.Connection:
    """每个线程一个连接（采集线程与请求线程互不干扰）。"""
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        return conn
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    _LOCAL.conn = conn
    return conn


def init() -> None:
    """建表（幂等）。"""
    global _initialized
    with _INIT_LOCK:
        if _initialized:
            return
        conn = connect()
        conn.executescript(SCHEMA)
        _initialized = True


def write(sql: str, params=()) -> int:
    """单条写：BEGIN IMMEDIATE + commit，防采集线程撞锁。"""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(sql, params)
        conn.execute("COMMIT")
        return cur.lastrowid or 0
    except Exception:
        conn.execute("ROLLBACK")
        raise


def write_many(rows) -> None:
    """批量写：rows = [(sql, params), ...] 在一个事务里。"""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for sql, params in rows:
            conn.execute(sql, params)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def query(sql: str, params=()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def query_one(sql: str, params=()):
    return connect().execute(sql, params).fetchone()
