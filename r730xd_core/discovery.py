"""Find a BMC on the local network without sending it any credentials.

Two problems, one mechanism:

* **Bootstrapping** - a new user does not know their iDRAC's address or MAC.
  Asking the network is better than asking them to go read a router's lease
  table. Anything that answers an IPMI presence ping on UDP 623 is a BMC.
* **DHCP drift** - once picked, the MAC is the stable identity. The address it
  currently holds is looked up in the ARP table, so a new lease does not break
  the connection.

The presence ping carries no credentials, which is the whole point: the console
must never hand an iDRAC password to a candidate that has not been identified
yet.

Only the ARP *source* is platform-specific (Linux reads /proc/net/arp, Windows
shells out to `arp -a`), so callers pass the table text in and this module
parses both layouts.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import select
import socket
import time
from dataclasses import dataclass

RMCP_PORT = 623

# How many addresses one sweep may cover. 512 lets a /24 and a /23 both be
# scanned in full - a /23 is an ordinary small-office LAN, and narrowing it
# to half could leave the BMC in the half that was skipped. The cap is here
# to stop a /16 becoming 65k probes, not to shave a few hundred packets that
# a LAN answers instantly.
MAX_SCAN_HOSTS = 512

# An ASF RMCP presence ping: version 6, no sequence, class ASF, then the ASF
# header with the IANA enterprise number and the "presence ping" message type.
_PRESENCE_PREFIX = bytes.fromhex("06 00 ff 06 00 00 11 be 80")
_PONG_LENGTH = 28

_MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})\b")
_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def normalise_mac(value: str) -> str:
    """Lower case, colon separated. `arp -a` uses dashes, /proc uses colons."""
    return value.strip().replace("-", ":").casefold()


def parse_arp_pairs(text: str) -> dict[str, str]:
    """MAC -> IP from either ARP table layout.

    Rather than parse two column formats, take any line carrying both an IPv4
    address and a MAC. That is true of `/proc/net/arp` rows and of `arp -a`
    rows, and of nothing else in either output.
    """
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        mac_match = _MAC_RE.search(line)
        ip_match = _IPV4_RE.search(line)
        if not mac_match or not ip_match:
            continue
        mac = normalise_mac(mac_match.group(1))
        if mac in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
            continue
        pairs.setdefault(mac, ip_match.group(1))
    return pairs


def address_for_mac(arp_text: str, mac: str) -> str | None:
    return parse_arp_pairs(arp_text).get(normalise_mac(mac))


def read_arp_table() -> str:
    """The host ARP table as text, in whatever form this OS offers it.

    The only platform-specific thing in this module. A container reads a
    bind-mounted /proc/1/net/arp instead and passes the text in directly.
    """
    import os
    import subprocess

    if os.name == "nt":
        completed = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return completed.stdout or ""
    try:
        with open("/proc/net/arp", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def local_address() -> str | None:
    """This host's source address for outbound traffic, without sending anything.

    Connecting a UDP socket only picks a route and a source address; no packet
    leaves. On a machine attached to several LANs this is the one that answers
    "which network am I actually on".
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 53))
            return str(probe.getsockname()[0])
    except OSError:
        return None


def host_addresses() -> list[tuple[str, int]]:
    """(address, prefix length) for every IPv4 interface, as the OS reports it.

    Read rather than assumed: a /23 or /20 LAN is not exotic, and guessing /24
    there scans the wrong range and silently finds nothing.
    """
    import json
    import os
    import subprocess

    found: list[tuple[str, int]] = []
    try:
        if os.name == "nt":
            # Get-NetIPAddress rather than ipconfig: the property names are the
            # same on a Chinese Windows, the parsed text is not.
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-NetIPAddress -AddressFamily IPv4 |"
                    " Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            payload = json.loads(completed.stdout or "[]")
            if isinstance(payload, dict):
                payload = [payload]
            for item in payload:
                found.append((str(item["IPAddress"]), int(item["PrefixLength"])))
        else:
            completed = subprocess.run(
                ["ip", "-4", "-o", "addr", "show"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            for line in (completed.stdout or "").splitlines():
                match = re.search(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\b", line)
                if match:
                    found.append((match.group(1), int(match.group(2))))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return []
    return found


@dataclass(frozen=True, slots=True)
class ScanRange:
    network: str
    address: str
    prefix: int
    #: True when the interface's real network was too large to sweep and the
    #: range was cut down to a /24 around this host.
    narrowed: bool = False
    #: True when the OS lookup failed and /24 was assumed.
    assumed: bool = False

    @property
    def note(self) -> str:
        if self.narrowed:
            return f"本机网段是 /{self.prefix}，太大，只扫本机所在的 /24"
        if self.assumed:
            return "读不到子网掩码，按 /24 估算"
        return ""


def scan_range(max_hosts: int = MAX_SCAN_HOSTS) -> ScanRange | None:
    """Which range to sweep, derived from the OS rather than assumed."""
    address = local_address()
    if address is None:
        return None

    prefix = None
    for candidate, candidate_prefix in host_addresses():
        if candidate == address:
            prefix = candidate_prefix
            break

    assumed = prefix is None
    if prefix is None:
        prefix = 24

    try:
        network = ipaddress.ip_interface(f"{address}/{prefix}").network
    except ValueError:
        return None

    # A /20 LAN is legitimate but 4094 probes is not a thing to do by default.
    # Falling back to this host's own /24 is a guess, so `narrowed` says so
    # rather than letting an empty result look like "no BMC here".
    narrowed = False
    if network.num_addresses - 2 > max_hosts:
        narrowed = True
        network = ipaddress.ip_interface(f"{address}/24").network

    return ScanRange(
        network=str(network),
        address=address,
        prefix=prefix,
        narrowed=narrowed,
        assumed=assumed,
    )


def presence_ping(tag: int) -> bytes:
    """The ASF presence ping carrying a caller-chosen tag.

    Shared so both product lines put the same bytes on the wire; a silent
    divergence here would mean one of them stops recognising BMCs.
    """
    return _PRESENCE_PREFIX + bytes((tag, 0x00, 0x00))


def valid_pong(payload: bytes, peer: tuple, tag: int) -> str | None:
    """The responder's address if this is a well-formed pong for our tag.

    Protocol only. Whether that address is one the caller should trust is
    policy and stays with the caller - the Web line additionally rejects
    loopback, link-local, multicast and reserved candidates.
    """
    if len(peer) < 2 or peer[1] != RMCP_PORT or len(payload) != _PONG_LENGTH:
        return None
    if (
        payload[0:4] != b"\x06\x00\xff\x06"
        or payload[4:8] != b"\x00\x00\x11\xbe"
        or payload[8] != 0x40
        or payload[9] != tag  # our own tag, so a stray reply cannot pose as one
        or payload[10] != 0x00
        or payload[11] != 0x10
        or len(payload) != 12 + payload[11]
    ):
        return None
    try:
        return str(ipaddress.IPv4Address(str(peer[0])))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Candidate:
    address: str
    mac: str | None = None

    @property
    def label(self) -> str:
        return f"{self.address}  ({self.mac})" if self.mac else self.address


def probe_rmcp(
    network: str | ipaddress.IPv4Network,
    *,
    timeout: float = 1.5,
    max_hosts: int = MAX_SCAN_HOSTS,
) -> list[str]:
    """Addresses that answered an IPMI presence ping. No credentials are sent.

    Bounded by max_hosts so a mistyped prefix cannot turn into a huge sweep.
    """
    net = ipaddress.ip_network(str(network), strict=False)
    if not isinstance(net, ipaddress.IPv4Network):
        return []
    hosts = list(net.hosts()) if net.prefixlen < 32 else [net.network_address]
    if len(hosts) > max_hosts:
        raise ValueError(f"{network} covers {len(hosts)} hosts, over the {max_hosts} cap")

    tag = secrets.randbelow(256)
    ping = presence_ping(tag)
    responders: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.setblocking(False)
            probe.bind(("0.0.0.0", 0))
            for address in hosts:
                try:
                    probe.sendto(ping, (str(address), RMCP_PORT))
                except OSError:
                    continue
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                readable, _, _ = select.select([probe], [], [], remaining)
                if not readable:
                    break
                try:
                    payload, peer = probe.recvfrom(2048)
                except OSError:
                    continue
                found = valid_pong(payload, peer, tag)
                if found and found not in responders:
                    responders.append(found)
    except OSError:
        return []
    return responders


def discover(
    network: str | ipaddress.IPv4Network,
    arp_text: str = "",
    *,
    timeout: float = 1.5,
    max_hosts: int = MAX_SCAN_HOSTS,
) -> list[Candidate]:
    """Every BMC on the segment, with its MAC filled in where ARP knows it.

    The probe is what populates the ARP cache in the first place, so callers
    that want MACs should read the table *after* this returns.
    """
    addresses = probe_rmcp(network, timeout=timeout, max_hosts=max_hosts)
    by_ip = {ip: mac for mac, ip in parse_arp_pairs(arp_text).items()}
    return [Candidate(address=item, mac=by_ip.get(item)) for item in addresses]
