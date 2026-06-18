"""Canonical host-record schema shared between collector and portal.

A single scanned host is represented as a plain dict so it serialises cleanly
to JSON on the wire (collector -> portal) and into SQLite (portal storage).

`normalize_host` is the single source of truth for validating / coercing a
host record. The collector uses it before sending, and the portal uses it
again on ingest so it never trusts the wire blindly.
"""
from __future__ import annotations

import ipaddress
from typing import Any

# Fields persisted per host. Keep in sync with portal/database.py schema.
HOST_FIELDS = (
    "ip",
    "status",
    "hostname",
    "mac",
    "vendor",
    "os_name",
    "os_family",
    "os_accuracy",
    "uptime_seconds",
    "last_boot",
    "open_ports",
    "subnet",
)

# Map common OS-match substrings to a coarse family for grouping in the UI.
_FAMILY_HINTS = (
    ("windows", "Windows"),
    ("linux", "Linux"),
    ("android", "Linux"),
    ("ubuntu", "Linux"),
    ("debian", "Linux"),
    ("centos", "Linux"),
    ("red hat", "Linux"),
    ("freebsd", "BSD"),
    ("openbsd", "BSD"),
    ("netbsd", "BSD"),
    ("mac os", "macOS"),
    ("macos", "macOS"),
    ("os x", "macOS"),
    ("ios", "iOS"),
    ("cisco", "Network"),
    ("juniper", "Network"),
    ("mikrotik", "Network"),
    ("vmware", "Hypervisor"),
    ("esxi", "Hypervisor"),
    ("printer", "Printer"),
)


def os_family_from_name(os_name: str | None, explicit: str | None = None) -> str:
    """Derive a coarse OS family for grouping. Prefer an explicit value."""
    if explicit:
        return explicit.strip()
    if not os_name:
        return "Unknown"
    low = os_name.lower()
    for hint, family in _FAMILY_HINTS:
        if hint in low:
            return family
    return "Other"


class HostRecordError(ValueError):
    """Raised when a host record fails validation."""


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_host(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a single host record. Raises on a bad IP."""
    ip = str(raw.get("ip", "")).strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise HostRecordError(f"invalid ip address: {ip!r}") from exc

    ports = raw.get("open_ports") or []
    if isinstance(ports, str):
        ports = [p for p in (s.strip() for s in ports.split(",")) if p]
    open_ports = sorted({p for p in (_coerce_int(p) for p in ports) if p is not None})

    os_name = (raw.get("os_name") or "").strip() or None
    rec = {
        "ip": ip,
        "status": (raw.get("status") or "up").strip() or "up",
        "hostname": (raw.get("hostname") or "").strip() or None,
        "mac": (raw.get("mac") or "").strip() or None,
        "vendor": (raw.get("vendor") or "").strip() or None,
        "os_name": os_name,
        "os_family": os_family_from_name(os_name, raw.get("os_family")),
        "os_accuracy": _coerce_int(raw.get("os_accuracy")),
        "uptime_seconds": _coerce_int(raw.get("uptime_seconds")),
        "last_boot": (raw.get("last_boot") or "").strip() or None,
        "open_ports": open_ports,
        "subnet": (raw.get("subnet") or "").strip() or None,
    }
    return rec


def format_uptime(seconds: int | None) -> str:
    """Human friendly uptime, e.g. 5d 3h 12m. Empty string when unknown."""
    if not seconds or seconds < 0:
        return ""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)
