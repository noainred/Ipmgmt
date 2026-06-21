"""Storage layer for the central portal -- SQLite or PostgreSQL.

Backend is chosen at connect() time from the DSN:
  * a postgres URL ("postgresql://..." / "postgres://...") -> PostgreSQL (psycopg3)
  * anything else                                          -> SQLite file path

Both share one set of query helpers. A thin `Database` wrapper hides the two
differences that actually matter for this app:
  * paramstyle: helpers are written with "?"; for postgres we rewrite to "%s".
  * autoincrement PK type in the schema.

Tables:
  * datacenters -- observed state, one row per collector (last-seen / last-scan).
  * hosts       -- one row per (datacenter, ip), upserted on every ingest.
  * dc_config   -- desired config managed from the web Settings page; the
                   source of truth collectors pull from.

SQLite (WAL) is great for dev and small deployments. PostgreSQL is recommended
for 20+ datacenters in production (concurrent writers, HA, history).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_LOCK = threading.Lock()

_PG_PREFIXES = ("postgresql://", "postgres://", "postgresql+psycopg://")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_postgres_dsn(dsn: str) -> bool:
    return isinstance(dsn, str) and dsn.startswith(_PG_PREFIXES)


class Database:
    """Minimal connection wrapper that unifies SQLite and PostgreSQL access."""

    def __init__(self, dsn: str):
        if is_postgres_dsn(dsn):
            import psycopg  # lazy: only required when actually using postgres
            from psycopg.rows import dict_row

            # psycopg accepts the URL as-is (strip the SQLAlchemy-style suffix).
            conninfo = dsn.replace("postgresql+psycopg://", "postgresql://")
            self.backend = "postgres"
            self.conn = psycopg.connect(conninfo, autocommit=True, row_factory=dict_row)
        else:
            self.backend = "sqlite"
            self.conn = sqlite3.connect(dsn, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=5000")

    def execute(self, sql: str, params=()):
        if self.backend == "postgres":
            sql = sql.replace("?", "%s")
            return self.conn.execute(sql, params or None)
        return self.conn.execute(sql, params)

    def commit(self):
        # postgres connection is autocommit; only sqlite needs an explicit commit.
        if self.backend == "sqlite":
            self.conn.commit()

    @contextmanager
    def transaction(self):
        """Atomic multi-statement block (used by ingest)."""
        if self.backend == "postgres":
            with self.conn.transaction():
                yield
        else:
            try:
                yield
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def init(self):
        ddl = _schema_for(self.backend)
        with _LOCK:
            for stmt in (s.strip() for s in ddl.split(";")):
                if stmt:
                    self.conn.execute(stmt)
            self.commit()


def connect(dsn: str) -> Database:
    return Database(dsn)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def _schema_for(backend: str) -> str:
    # Only the auto-increment PK type differs between the two engines.
    serial_pk = "BIGSERIAL PRIMARY KEY" if backend == "postgres" \
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"""
CREATE TABLE IF NOT EXISTS datacenters (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    location      TEXT,
    last_seen     TEXT,
    last_scan_started  TEXT,
    last_scan_finished TEXT,
    subnets       TEXT,
    host_count    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hosts (
    id            {serial_pk},
    datacenter_id TEXT NOT NULL REFERENCES datacenters(id) ON DELETE CASCADE,
    ip            TEXT NOT NULL,
    status        TEXT,
    hostname      TEXT,
    mac           TEXT,
    vendor        TEXT,
    os_name       TEXT,
    os_family     TEXT,
    os_accuracy   INTEGER,
    uptime_seconds INTEGER,
    last_boot     TEXT,
    open_ports    TEXT,
    subnet        TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    UNIQUE(datacenter_id, ip)
);

CREATE INDEX IF NOT EXISTS idx_hosts_dc       ON hosts(datacenter_id);
CREATE INDEX IF NOT EXISTS idx_hosts_family   ON hosts(os_family);
CREATE INDEX IF NOT EXISTS idx_hosts_subnet   ON hosts(subnet);
CREATE INDEX IF NOT EXISTS idx_hosts_lastseen ON hosts(last_seen);

CREATE TABLE IF NOT EXISTS dc_config (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    location      TEXT,
    subnets       TEXT,
    scan_interval_seconds INTEGER DEFAULT 900,
    enabled       INTEGER DEFAULT 1,
    updated_at    TEXT
)
"""


def init_db(conn: Database) -> None:
    conn.init()


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #
def ingest(conn: Database, datacenter: dict, scan: dict, hosts: list[dict]) -> dict:
    """Upsert a datacenter and its host records from one collector push."""
    now = _now_iso()
    dc_id = datacenter["id"]
    with _LOCK, conn.transaction():
        conn.execute(
            """
            INSERT INTO datacenters (id, name, location, last_seen,
                                     last_scan_started, last_scan_finished,
                                     subnets, host_count)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                location=excluded.location,
                last_seen=excluded.last_seen,
                last_scan_started=excluded.last_scan_started,
                last_scan_finished=excluded.last_scan_finished,
                subnets=excluded.subnets,
                host_count=excluded.host_count
            """,
            (
                dc_id,
                datacenter.get("name") or dc_id,
                datacenter.get("location"),
                now,
                scan.get("started_at"),
                scan.get("finished_at"),
                json.dumps(scan.get("subnets") or []),
                len(hosts),
            ),
        )

        for h in hosts:
            conn.execute(
                """
                INSERT INTO hosts (datacenter_id, ip, status, hostname, mac, vendor,
                                   os_name, os_family, os_accuracy, uptime_seconds,
                                   last_boot, open_ports, subnet, first_seen, last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(datacenter_id, ip) DO UPDATE SET
                    status=excluded.status,
                    hostname=excluded.hostname,
                    mac=excluded.mac,
                    vendor=excluded.vendor,
                    os_name=excluded.os_name,
                    os_family=excluded.os_family,
                    os_accuracy=excluded.os_accuracy,
                    uptime_seconds=excluded.uptime_seconds,
                    last_boot=excluded.last_boot,
                    open_ports=excluded.open_ports,
                    subnet=excluded.subnet,
                    last_seen=excluded.last_seen
                """,
                (
                    dc_id,
                    h["ip"],
                    h.get("status"),
                    h.get("hostname"),
                    h.get("mac"),
                    h.get("vendor"),
                    h.get("os_name"),
                    h.get("os_family"),
                    h.get("os_accuracy"),
                    h.get("uptime_seconds"),
                    h.get("last_boot"),
                    json.dumps(h.get("open_ports") or []),
                    h.get("subnet"),
                    now,
                    now,
                ),
            )
    return {"datacenter": dc_id, "hosts_ingested": len(hosts)}


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def _row_to_host(row) -> dict:
    d = dict(row)
    try:
        d["open_ports"] = json.loads(d.get("open_ports") or "[]")
    except (TypeError, ValueError):
        d["open_ports"] = []
    return d


def _decode_subnets(d: dict) -> dict:
    try:
        d["subnets"] = json.loads(d.get("subnets") or "[]")
    except (TypeError, ValueError):
        d["subnets"] = []
    return d


def list_datacenters(conn: Database) -> list[dict]:
    rows = conn.execute("SELECT * FROM datacenters ORDER BY id").fetchall()
    return [_decode_subnets(dict(r)) for r in rows]


def get_datacenter(conn: Database, dc_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM datacenters WHERE id=?", (dc_id,)).fetchone()
    return _decode_subnets(dict(row)) if row else None


def query_hosts(
    conn: Database,
    *,
    datacenter_id: str | None = None,
    os_family: str | None = None,
    subnet: str | None = None,
    search: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    clauses = []
    params: list = []
    if datacenter_id:
        clauses.append("datacenter_id = ?")
        params.append(datacenter_id)
    if os_family:
        clauses.append("os_family = ?")
        params.append(os_family)
    if subnet:
        clauses.append("subnet = ?")
        params.append(subnet)
    if search:
        like = f"%{search}%"
        clauses.append("(ip LIKE ? OR hostname LIKE ? OR os_name LIKE ?)")
        params += [like, like, like]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT * FROM hosts" + where +
        " ORDER BY datacenter_id, subnet, ip LIMIT ? OFFSET ?"
    )
    params += [int(limit), int(offset)]
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_host(r) for r in rows]


def count_hosts(conn: Database, **filters) -> int:
    clauses = []
    params: list = []
    for key in ("datacenter_id", "os_family", "subnet"):
        if filters.get(key):
            clauses.append(f"{key} = ?")
            params.append(filters[key])
    if filters.get("search"):
        like = f"%{filters['search']}%"
        clauses.append("(ip LIKE ? OR hostname LIKE ? OR os_name LIKE ?)")
        params += [like, like, like]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    row = conn.execute("SELECT COUNT(*) AS n FROM hosts" + where, params).fetchone()
    return dict(row)["n"]


def os_family_breakdown(conn: Database, datacenter_id: str | None = None) -> list[dict]:
    if datacenter_id:
        rows = conn.execute(
            "SELECT os_family, COUNT(*) AS c FROM hosts WHERE datacenter_id=? "
            "GROUP BY os_family ORDER BY c DESC",
            (datacenter_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT os_family, COUNT(*) AS c FROM hosts GROUP BY os_family ORDER BY c DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        out.append({"os_family": d.get("os_family") or "Unknown", "count": d["c"]})
    return out


def global_stats(conn: Database) -> dict:
    total_hosts = dict(conn.execute("SELECT COUNT(*) AS n FROM hosts").fetchone())["n"]
    total_dcs = dict(conn.execute("SELECT COUNT(*) AS n FROM datacenters").fetchone())["n"]
    return {
        "datacenters": total_dcs,
        "hosts": total_hosts,
        "os_breakdown": os_family_breakdown(conn),
    }


# --------------------------------------------------------------------------- #
# Datacenter configuration (managed from the web Settings page)
# --------------------------------------------------------------------------- #
def _row_to_config(row) -> dict:
    d = _decode_subnets(dict(row))
    d["enabled"] = bool(d.get("enabled", 1))
    return d


def list_configs(conn: Database) -> list[dict]:
    rows = conn.execute("SELECT * FROM dc_config ORDER BY id").fetchall()
    return [_row_to_config(r) for r in rows]


def get_config(conn: Database, dc_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM dc_config WHERE id=?", (dc_id,)).fetchone()
    return _row_to_config(row) if row else None


def upsert_config(
    conn: Database,
    dc_id: str,
    *,
    name: str | None = None,
    location: str | None = None,
    subnets: list[str] | None = None,
    scan_interval_seconds: int = 900,
    enabled: bool = True,
) -> dict:
    now = _now_iso()
    with _LOCK:
        conn.execute(
            """
            INSERT INTO dc_config (id, name, location, subnets,
                                   scan_interval_seconds, enabled, updated_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                location=excluded.location,
                subnets=excluded.subnets,
                scan_interval_seconds=excluded.scan_interval_seconds,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
            (
                dc_id,
                name,
                location,
                json.dumps(subnets or []),
                int(scan_interval_seconds),
                1 if enabled else 0,
                now,
            ),
        )
        conn.commit()
    return get_config(conn, dc_id)


def delete_config(conn: Database, dc_id: str) -> bool:
    with _LOCK:
        cur = conn.execute("DELETE FROM dc_config WHERE id=?", (dc_id,))
        conn.commit()
    return cur.rowcount > 0


def datacenters_overview(conn: Database) -> list[dict]:
    """Merge desired config with observed state for the dashboard.

    A datacenter may be configured but not yet reported (pending), reported but
    not configured (e.g. ad-hoc collector run), or both.
    """
    configs = {c["id"]: c for c in list_configs(conn)}
    observed = {d["id"]: d for d in list_datacenters(conn)}
    out = []
    for dc_id in sorted(set(configs) | set(observed)):
        c = configs.get(dc_id)
        o = observed.get(dc_id)
        subnets = (o.get("subnets") if o and o.get("subnets") else
                   (c.get("subnets") if c else [])) or []
        out.append({
            "id": dc_id,
            "name": (o and o.get("name")) or (c and c.get("name")) or dc_id,
            "location": (o and o.get("location")) or (c and c.get("location")),
            "subnets": subnets,
            "host_count": (o or {}).get("host_count", 0),
            "last_seen": (o or {}).get("last_seen"),
            "last_scan_finished": (o or {}).get("last_scan_finished"),
            "enabled": (c.get("enabled", True) if c else True),
            "configured": c is not None,
            "reported": o is not None,
        })
    return out
