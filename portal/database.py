"""SQLite storage for the central portal.

Two tables:
  * datacenters -- one row per collector, with last-seen / last-scan metadata.
  * hosts       -- one row per (datacenter, ip), upserted on every ingest.

SQLite in WAL mode comfortably handles periodic batch writes from 20+ collectors
plus the read-heavy dashboard. Swap the connection factory for Postgres later
without touching the query helpers' signatures.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS datacenters (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    location      TEXT,
    last_seen     TEXT,
    last_scan_started  TEXT,
    last_scan_finished TEXT,
    subnets       TEXT,        -- JSON array of CIDRs from the latest scan
    host_count    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hosts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
    open_ports    TEXT,        -- JSON array of ints
    subnet        TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    UNIQUE(datacenter_id, ip)
);

CREATE INDEX IF NOT EXISTS idx_hosts_dc       ON hosts(datacenter_id);
CREATE INDEX IF NOT EXISTS idx_hosts_family   ON hosts(os_family);
CREATE INDEX IF NOT EXISTS idx_hosts_subnet   ON hosts(subnet);
CREATE INDEX IF NOT EXISTS idx_hosts_lastseen ON hosts(last_seen);

-- Desired configuration, managed from the web Settings page. This is the
-- source of truth that collectors pull from (separate from the observed
-- `datacenters` state that ingest writes).
CREATE TABLE IF NOT EXISTS dc_config (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    location      TEXT,
    subnets       TEXT,        -- JSON array of CIDRs to scan
    scan_interval_seconds INTEGER DEFAULT 900,
    enabled       INTEGER DEFAULT 1,
    updated_at    TEXT
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    with _LOCK:
        conn.executescript(SCHEMA)
        conn.commit()


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #
def ingest(conn: sqlite3.Connection, datacenter: dict, scan: dict, hosts: list[dict]) -> dict:
    """Upsert a datacenter and its host records from one collector push."""
    now = _now_iso()
    dc_id = datacenter["id"]
    with _LOCK:
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
        conn.commit()
    return {"datacenter": dc_id, "hosts_ingested": len(hosts)}


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def _row_to_host(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["open_ports"] = json.loads(d.get("open_ports") or "[]")
    except (TypeError, ValueError):
        d["open_ports"] = []
    return d


def list_datacenters(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM datacenters ORDER BY id"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["subnets"] = json.loads(d.get("subnets") or "[]")
        except (TypeError, ValueError):
            d["subnets"] = []
        out.append(d)
    return out


def get_datacenter(conn: sqlite3.Connection, dc_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM datacenters WHERE id=?", (dc_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["subnets"] = json.loads(d.get("subnets") or "[]")
    except (TypeError, ValueError):
        d["subnets"] = []
    return d


def query_hosts(
    conn: sqlite3.Connection,
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


def count_hosts(conn: sqlite3.Connection, **filters) -> int:
    # Mirror query_hosts filters for an accurate total (without limit/offset).
    clauses = []
    params: list = []
    for col, key in (("datacenter_id", "datacenter_id"),
                     ("os_family", "os_family"),
                     ("subnet", "subnet")):
        if filters.get(key):
            clauses.append(f"{col} = ?")
            params.append(filters[key])
    if filters.get("search"):
        like = f"%{filters['search']}%"
        clauses.append("(ip LIKE ? OR hostname LIKE ? OR os_name LIKE ?)")
        params += [like, like, like]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute("SELECT COUNT(*) FROM hosts" + where, params).fetchone()[0]


def os_family_breakdown(conn: sqlite3.Connection, datacenter_id: str | None = None) -> list[dict]:
    if datacenter_id:
        rows = conn.execute(
            "SELECT os_family, COUNT(*) c FROM hosts WHERE datacenter_id=? "
            "GROUP BY os_family ORDER BY c DESC",
            (datacenter_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT os_family, COUNT(*) c FROM hosts GROUP BY os_family ORDER BY c DESC"
        ).fetchall()
    return [{"os_family": r[0] or "Unknown", "count": r[1]} for r in rows]


def global_stats(conn: sqlite3.Connection) -> dict:
    total_hosts = conn.execute("SELECT COUNT(*) FROM hosts").fetchone()[0]
    total_dcs = conn.execute("SELECT COUNT(*) FROM datacenters").fetchone()[0]
    return {
        "datacenters": total_dcs,
        "hosts": total_hosts,
        "os_breakdown": os_family_breakdown(conn),
    }


# --------------------------------------------------------------------------- #
# Datacenter configuration (managed from the web Settings page)
# --------------------------------------------------------------------------- #
def _row_to_config(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["subnets"] = json.loads(d.get("subnets") or "[]")
    except (TypeError, ValueError):
        d["subnets"] = []
    d["enabled"] = bool(d.get("enabled", 1))
    return d


def list_configs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM dc_config ORDER BY id").fetchall()
    return [_row_to_config(r) for r in rows]


def get_config(conn: sqlite3.Connection, dc_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM dc_config WHERE id=?", (dc_id,)).fetchone()
    return _row_to_config(row) if row else None


def upsert_config(
    conn: sqlite3.Connection,
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


def delete_config(conn: sqlite3.Connection, dc_id: str) -> bool:
    with _LOCK:
        cur = conn.execute("DELETE FROM dc_config WHERE id=?", (dc_id,))
        conn.commit()
    return cur.rowcount > 0


def datacenters_overview(conn: sqlite3.Connection) -> list[dict]:
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
