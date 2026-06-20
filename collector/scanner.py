"""Subnet scanner.

Wraps `nmap` to discover live hosts in one or more subnets and extract, per
host: OS guess, reverse-DNS hostname, MAC/vendor, open ports and an uptime
estimate (nmap derives uptime from TCP timestamp options when OS detection is
enabled and at least one port is open).

A `--demo` path generates deterministic synthetic hosts so the whole pipeline
and the web portal can be exercised without nmap, root, or a real network.
"""
from __future__ import annotations

import hashlib
import ipaddress
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from common.hostrecord import normalize_host

# nmap flags:
#   -O                OS detection (needs root)
#   --osscan-guess    report close matches even below the certainty threshold
#   -R                always attempt reverse DNS (hostname)
#   -T4               faster timing
#   --max-retries 2   bound probe retransmissions on large subnets
#   -oX -             emit XML to stdout for parsing
_OS_SCAN_ARGS = ["-O", "--osscan-guess", "-R", "-T4", "--max-retries", "2"]
# Without root we cannot do raw-packet OS detection; fall back to a TCP connect
# scan that still finds live hosts, hostnames and open ports.
_NO_ROOT_ARGS = ["-sT", "-R", "-T4", "--max-retries", "2", "-F"]


def nmap_available() -> bool:
    return shutil.which("nmap") is not None


def is_root() -> bool:
    import os

    return hasattr(os, "geteuid") and os.geteuid() == 0


def scan(subnets, *, demo=False, do_os=True, timeout=900):
    """Scan the given subnets and return a list of normalized host records.

    `subnets` is an iterable of CIDR strings (e.g. "10.1.0.0/24").
    """
    cidrs = [str(s).strip() for s in subnets if str(s).strip()]
    if demo or not nmap_available():
        if not demo and not nmap_available():
            # Caller asked for a real scan but nmap is missing: be explicit.
            raise RuntimeError(
                "nmap not found on PATH. Install nmap or run the collector "
                "with --demo to generate synthetic data."
            )
        return _demo_scan(cidrs)

    args = ["nmap"]
    args += _OS_SCAN_ARGS if (do_os and is_root()) else _NO_ROOT_ARGS
    args += ["-oX", "-"]
    args += cidrs

    proc = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"nmap failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return parse_nmap_xml(proc.stdout, cidrs)


def _subnet_for_ip(ip, cidrs):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for cidr in cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return cidr
        except ValueError:
            continue
    return None


def parse_nmap_xml(xml_text, cidrs=()):
    """Parse nmap's XML output into normalized host records (up hosts only)."""
    if not xml_text or not xml_text.strip():
        return []
    root = ET.fromstring(xml_text)
    hosts = []
    for host in root.findall("host"):
        status_el = host.find("status")
        state = status_el.get("state") if status_el is not None else "unknown"
        if state != "up":
            continue

        ip = mac = vendor = None
        for addr in host.findall("address"):
            atype = addr.get("addrtype")
            if atype == "ipv4" or atype == "ipv6":
                ip = addr.get("addr")
            elif atype == "mac":
                mac = addr.get("addr")
                vendor = addr.get("vendor")
        if not ip:
            continue

        hostname = None
        hostnames_el = host.find("hostnames")
        if hostnames_el is not None:
            hn = hostnames_el.find("hostname")
            if hn is not None:
                hostname = hn.get("name")

        os_name = None
        os_accuracy = None
        os_el = host.find("os")
        if os_el is not None:
            match = os_el.find("osmatch")
            if match is not None:
                os_name = match.get("name")
                os_accuracy = match.get("accuracy")

        uptime_seconds = None
        last_boot = None
        up_el = host.find("uptime")
        if up_el is not None:
            uptime_seconds = up_el.get("seconds")
            last_boot = up_el.get("lastboot")

        open_ports = []
        ports_el = host.find("ports")
        if ports_el is not None:
            for port in ports_el.findall("port"):
                pstate = port.find("state")
                if pstate is not None and pstate.get("state") == "open":
                    open_ports.append(port.get("portid"))

        hosts.append(
            normalize_host(
                {
                    "ip": ip,
                    "status": state,
                    "hostname": hostname,
                    "mac": mac,
                    "vendor": vendor,
                    "os_name": os_name,
                    "os_accuracy": os_accuracy,
                    "uptime_seconds": uptime_seconds,
                    "last_boot": last_boot,
                    "open_ports": open_ports,
                    "subnet": _subnet_for_ip(ip, cidrs),
                }
            )
        )
    return hosts


# --------------------------------------------------------------------------- #
# Demo data generation (no nmap / no network required)
# --------------------------------------------------------------------------- #
_DEMO_OSES = [
    ("Ubuntu 22.04 (Linux 5.15)", "Linux", [22, 80, 443]),
    ("Ubuntu 20.04 (Linux 5.4)", "Linux", [22, 443]),
    ("CentOS 7 (Linux 3.10)", "Linux", [22, 3306]),
    ("Debian 12 (Linux 6.1)", "Linux", [22, 5432]),
    ("Windows Server 2019", "Windows", [135, 445, 3389]),
    ("Windows Server 2022", "Windows", [135, 445, 3389, 80]),
    ("Windows 11 Pro", "Windows", [445, 3389]),
    ("VMware ESXi 7.0", "Hypervisor", [443, 902]),
    ("FreeBSD 13.1", "BSD", [22, 80]),
    ("Cisco IOS 15.x", "Network", [22, 23]),
]


def _demo_scan(cidrs):
    """Deterministically synthesise live hosts for the given subnets."""
    hosts = []
    now = datetime.now(timezone.utc)
    for cidr in cidrs or ["10.0.0.0/24"]:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        usable = list(net.hosts())
        # Pick a deterministic ~12% subset of addresses as "live".
        for host_ip in usable:
            seed = int(hashlib.md5(str(host_ip).encode()).hexdigest(), 16)
            if seed % 100 >= 12:
                continue
            os_name, family, ports = _DEMO_OSES[seed % len(_DEMO_OSES)]
            uptime = (seed % 90) * 86400 + (seed % 24) * 3600 + (seed % 60) * 60
            last_boot = (now - timedelta(seconds=uptime)).strftime("%Y-%m-%d %H:%M:%S")
            octet = str(host_ip).split(".")[-1] if "." in str(host_ip) else "h"
            hosts.append(
                normalize_host(
                    {
                        "ip": str(host_ip),
                        "status": "up",
                        "hostname": f"host-{octet}.{cidr.split('/')[0].replace('.', '-')}.local",
                        "mac": f"02:{(seed >> 8) & 0xFF:02x}:{seed & 0xFF:02x}:"
                        f"{(seed >> 16) & 0xFF:02x}:{(seed >> 24) & 0xFF:02x}:{octet[-2:].zfill(2)[:2]}",
                        "vendor": "DemoNIC",
                        "os_name": os_name,
                        "os_family": family,
                        "os_accuracy": 88 + (seed % 12),
                        "uptime_seconds": uptime,
                        "last_boot": last_boot,
                        "open_ports": ports,
                        "subnet": cidr,
                    }
                )
            )
    return hosts
