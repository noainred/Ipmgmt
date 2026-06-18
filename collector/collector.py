"""Datacenter collector entry point.

Runs *inside each datacenter*. On a schedule it scans the configured subnets
and pushes the results to the central portal's ingest API. The push model
means collectors only need outbound HTTPS to the portal -- no inbound access
into the datacenter -- which is what makes 20+ datacenters practical.

Usage:
    python -m collector.collector --config collector/config.json
    python -m collector.collector --config collector/config.json --once
    python -m collector.collector --demo --once \
        --portal http://127.0.0.1:8000 --api-key KEY --dc dc-test-01 \
        --subnets 10.0.0.0/24

Run as a module from the repo root so `common` and `collector` import cleanly.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import requests

from collector import scanner


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    missing = [k for k in ("datacenter", "portal_url", "api_key", "subnets") if k not in cfg]
    if missing:
        raise SystemExit(f"config missing required keys: {', '.join(missing)}")
    if not isinstance(cfg["datacenter"], dict) or "id" not in cfg["datacenter"]:
        raise SystemExit("config.datacenter must be an object with an 'id'")
    return cfg


def run_once(cfg: dict, *, demo: bool = False) -> dict:
    """Scan all subnets once and push to the portal. Returns the push result."""
    dc = cfg["datacenter"]
    subnets = cfg["subnets"]
    started = _now_iso()
    print(f"[{started}] scanning {len(subnets)} subnet(s) for {dc['id']} ...", flush=True)

    hosts = scanner.scan(subnets, demo=demo)
    finished = _now_iso()
    print(f"  found {len(hosts)} live host(s)", flush=True)

    payload = {
        "datacenter": {
            "id": dc["id"],
            "name": dc.get("name", dc["id"]),
            "location": dc.get("location"),
        },
        "scan": {
            "started_at": started,
            "finished_at": finished,
            "subnets": subnets,
        },
        "hosts": hosts,
    }

    url = cfg["portal_url"].rstrip("/") + "/api/v1/ingest"
    resp = requests.post(
        url,
        json=payload,
        headers={"X-API-Key": cfg["api_key"]},
        timeout=60,
        verify=cfg.get("verify_tls", True),
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"  portal accepted: {result}", flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="IP management datacenter collector")
    parser.add_argument("--config", help="path to JSON config file")
    parser.add_argument("--once", action="store_true", help="scan once and exit")
    parser.add_argument("--demo", action="store_true",
                        help="generate synthetic hosts (no nmap/network needed)")
    # Inline overrides so the collector is usable without a config file.
    parser.add_argument("--portal", help="portal base URL (overrides config)")
    parser.add_argument("--api-key", help="ingest API key (overrides config)")
    parser.add_argument("--dc", help="datacenter id (overrides config)")
    parser.add_argument("--dc-name", help="datacenter display name")
    parser.add_argument("--subnets", help="comma-separated CIDRs (overrides config)")
    parser.add_argument("--interval", type=int, help="scan interval seconds (loop mode)")
    args = parser.parse_args(argv)

    if args.config:
        cfg = load_config(args.config)
    else:
        cfg = {
            "datacenter": {"id": args.dc or "dc-local", "name": args.dc_name},
            "portal_url": args.portal or "http://127.0.0.1:8000",
            "api_key": args.api_key or "change-me-shared-ingest-key",
            "subnets": [],
            "scan_interval_seconds": 900,
        }

    # Apply CLI overrides on top of the config.
    if args.portal:
        cfg["portal_url"] = args.portal
    if args.api_key:
        cfg["api_key"] = args.api_key
    if args.dc:
        cfg.setdefault("datacenter", {})["id"] = args.dc
    if args.dc_name:
        cfg.setdefault("datacenter", {})["name"] = args.dc_name
    if args.subnets:
        cfg["subnets"] = [s.strip() for s in args.subnets.split(",") if s.strip()]
    if args.interval:
        cfg["scan_interval_seconds"] = args.interval

    if not cfg.get("subnets"):
        raise SystemExit("no subnets configured (use --subnets or a config file)")

    interval = int(cfg.get("scan_interval_seconds", 900))

    if args.once:
        try:
            run_once(cfg, demo=args.demo)
        except Exception as exc:  # noqa: BLE001 - report and exit non-zero
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    print(f"collector loop started; interval={interval}s. Ctrl-C to stop.", flush=True)
    while True:
        try:
            run_once(cfg, demo=args.demo)
        except KeyboardInterrupt:
            print("stopping.", flush=True)
            return 0
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            print(f"scan/push error (will retry): {exc}", file=sys.stderr, flush=True)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("stopping.", flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
