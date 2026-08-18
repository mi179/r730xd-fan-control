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


def local_network(prefix: int = 24) -> str | None:
    """The /24 this machine sits on, without sending anything.

    Connecting a UDP socket only picks a route and a source address; no packet
    leaves. Used to default the scan range so the user is not asked for a CIDR
    before they have any idea what theirs is.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 53))
            local = probe.getsockname()[0]
    except OSError:
        return None
    try:
        interface = ipaddress.ip_interface(f"{local}/{prefix}")
    except ValueError:
        return None
    return str(interface.network)


def _valid_pong(payload: bytes, peer: tuple, tag: int) -> str | None:
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
    max_hosts: int = 256,
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
    ping = _PRESENCE_PREFIX + bytes((tag, 0x00, 0x00))
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
                found = _valid_pong(payload, peer, tag)
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
    max_hosts: int = 256,
) -> list[Candidate]:
    """Every BMC on the segment, with its MAC filled in where ARP knows it.

    The probe is what populates the ARP cache in the first place, so callers
    that want MACs should read the table *after* this returns.
    """
    addresses = probe_rmcp(network, timeout=timeout, max_hosts=max_hosts)
    by_ip = {ip: mac for mac, ip in parse_arp_pairs(arp_text).items()}
    return [Candidate(address=item, mac=by_ip.get(item)) for item in addresses]
