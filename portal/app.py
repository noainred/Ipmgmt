"""Central portal Flask app.

Endpoints
  Web UI:
    GET  /                      aggregated dashboard across all datacenters
    GET  /dc/<dc_id>            per-datacenter host inventory

  JSON API:
    POST /api/v1/ingest         collector -> portal push (X-API-Key required)
    GET  /api/v1/stats          global counts + OS breakdown
    GET  /api/v1/datacenters    list datacenters with summary
    GET  /api/v1/hosts          query hosts (filters: dc, os_family, subnet, q)

Run:
    INGEST_API_KEY=secret python -m portal.app            # 0.0.0.0:8000
    PORTAL_DB=portal.db PORT=8000 python -m portal.app

Auth model: collectors authenticate to the ingest endpoint with a shared
`X-API-Key` (set INGEST_API_KEY). Per-datacenter keys can be layered on later.
"""
from __future__ import annotations

import os

from flask import Flask, abort, g, jsonify, render_template, request

from common.hostrecord import format_uptime, normalize_host
from portal import database as db

DB_PATH = os.environ.get("PORTAL_DB", "portal.db")
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "change-me-shared-ingest-key")
# Separate token that gates write access to the web Settings page / config API.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change-me-admin-token")

app = Flask(__name__)

# One shared connection (SQLite + WAL + module-level lock handles concurrency).
_conn = db.connect(DB_PATH)
db.init_db(_conn)


def get_conn():
    if "conn" not in g:
        g.conn = _conn
    return g.conn


# Expose uptime formatting to Jinja templates.
app.jinja_env.filters["uptime"] = format_uptime


def _is_admin() -> bool:
    return request.headers.get("X-Admin-Token", "") == ADMIN_TOKEN


def _is_collector() -> bool:
    return request.headers.get("X-API-Key", "") == INGEST_API_KEY


def _require_admin():
    if not _is_admin():
        abort(401, description="invalid or missing X-Admin-Token")


# --------------------------------------------------------------------------- #
# Ingest API
# --------------------------------------------------------------------------- #
@app.post("/api/v1/ingest")
def ingest():
    if not _is_collector():
        abort(401, description="invalid or missing X-API-Key")

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="JSON body required")

    dc = payload.get("datacenter")
    if not isinstance(dc, dict) or not dc.get("id"):
        abort(400, description="datacenter.id is required")
    scan = payload.get("scan") or {}
    raw_hosts = payload.get("hosts") or []
    if not isinstance(raw_hosts, list):
        abort(400, description="hosts must be a list")

    # Re-validate every host server-side; skip malformed entries rather than
    # rejecting the whole batch.
    clean, skipped = [], 0
    for h in raw_hosts:
        try:
            clean.append(normalize_host(h))
        except Exception:  # noqa: BLE001
            skipped += 1

    result = db.ingest(get_conn(), dc, scan, clean)
    result["skipped"] = skipped
    return jsonify(result), 200


# --------------------------------------------------------------------------- #
# Query API
# --------------------------------------------------------------------------- #
@app.get("/api/v1/stats")
def api_stats():
    return jsonify(db.global_stats(get_conn()))


@app.get("/api/v1/datacenters")
def api_datacenters():
    return jsonify({"datacenters": db.datacenters_overview(get_conn())})


# --------------------------------------------------------------------------- #
# Configuration API (web Settings page writes; collectors pull their config)
# --------------------------------------------------------------------------- #
@app.get("/api/v1/config")
def api_config_list():
    # Visible to admins (Settings UI) and collectors; subnets are not secret
    # (already shown on the dashboard) but writes are gated below.
    if not (_is_admin() or _is_collector()):
        abort(401, description="X-Admin-Token or X-API-Key required")
    return jsonify({"configs": db.list_configs(get_conn())})


@app.get("/api/v1/config/<dc_id>")
def api_config_get(dc_id):
    # Primary consumer: the collector pulling its own scan config.
    if not (_is_admin() or _is_collector()):
        abort(401, description="X-Admin-Token or X-API-Key required")
    cfg = db.get_config(get_conn(), dc_id)
    if not cfg:
        abort(404, description=f"no config for datacenter: {dc_id}")
    return jsonify(cfg)


@app.put("/api/v1/config/<dc_id>")
@app.post("/api/v1/config/<dc_id>")
def api_config_upsert(dc_id):
    _require_admin()
    body = request.get_json(silent=True) or {}
    subnets = body.get("subnets") or []
    if isinstance(subnets, str):
        subnets = [s.strip() for s in subnets.replace("\n", ",").split(",") if s.strip()]
    # Validate each CIDR before saving so the collector never gets junk.
    import ipaddress
    bad = []
    norm = []
    for cidr in subnets:
        try:
            norm.append(str(ipaddress.ip_network(cidr, strict=False)))
        except ValueError:
            bad.append(cidr)
    if bad:
        abort(400, description=f"invalid subnet(s): {', '.join(bad)}")

    try:
        interval = int(body.get("scan_interval_seconds", 900))
    except (TypeError, ValueError):
        abort(400, description="scan_interval_seconds must be an integer")
    if interval < 30:
        abort(400, description="scan_interval_seconds must be >= 30")

    cfg = db.upsert_config(
        get_conn(), dc_id,
        name=(body.get("name") or "").strip() or None,
        location=(body.get("location") or "").strip() or None,
        subnets=norm,
        scan_interval_seconds=interval,
        enabled=bool(body.get("enabled", True)),
    )
    return jsonify(cfg), 200


@app.delete("/api/v1/config/<dc_id>")
def api_config_delete(dc_id):
    _require_admin()
    ok = db.delete_config(get_conn(), dc_id)
    if not ok:
        abort(404, description=f"no config for datacenter: {dc_id}")
    return jsonify({"deleted": dc_id}), 200


@app.get("/api/v1/hosts")
def api_hosts():
    conn = get_conn()
    try:
        limit = min(int(request.args.get("limit", 1000)), 5000)
        offset = int(request.args.get("offset", 0))
    except ValueError:
        abort(400, description="limit/offset must be integers")

    filters = dict(
        datacenter_id=request.args.get("dc") or None,
        os_family=request.args.get("os_family") or None,
        subnet=request.args.get("subnet") or None,
        search=request.args.get("q") or None,
    )
    hosts = db.query_hosts(conn, limit=limit, offset=offset, **filters)
    total = db.count_hosts(conn, **filters)
    return jsonify({"total": total, "count": len(hosts), "hosts": hosts})


# --------------------------------------------------------------------------- #
# Web UI
# --------------------------------------------------------------------------- #
@app.get("/")
def dashboard():
    conn = get_conn()
    return render_template(
        "dashboard.html",
        stats=db.global_stats(conn),
        datacenters=db.datacenters_overview(conn),
    )


@app.get("/settings")
def settings():
    # The page renders empty and loads data via the API using the admin token
    # the operator enters (kept client-side in localStorage, never in the page).
    return render_template("settings.html")


@app.get("/dc/<dc_id>")
def datacenter_view(dc_id):
    conn = get_conn()
    dc = db.get_datacenter(conn, dc_id)
    if not dc:
        abort(404, description=f"unknown datacenter: {dc_id}")
    return render_template(
        "datacenter.html",
        dc=dc,
        os_breakdown=db.os_family_breakdown(conn, dc_id),
    )


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(404)
def _json_error(err):
    return jsonify({"error": getattr(err, "description", str(err))}), err.code


def main():
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"portal on http://{host}:{port}  (db={DB_PATH})", flush=True)
    if INGEST_API_KEY == "change-me-shared-ingest-key":
        print("WARNING: using default INGEST_API_KEY; set INGEST_API_KEY in prod.",
              flush=True)
    if ADMIN_TOKEN == "change-me-admin-token":
        print("WARNING: using default ADMIN_TOKEN; set ADMIN_TOKEN in prod "
              "(gates the web Settings page).", flush=True)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
