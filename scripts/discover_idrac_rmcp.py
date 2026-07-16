"""Find IPMI-capable devices with an unauthenticated RMCP/ASF presence ping.

This diagnostic never sends an iDRAC username or password and never changes
hardware state.  It only reports IPv4 addresses that return an ASF response on
the standard RMCP port.
"""

from __future__ import annotations

import argparse
import ipaddress
import secrets
import select
import socket
import time


def discover(network: ipaddress.IPv4Network, timeout: float) -> list[str]:
    responders: set[str] = set()
    tag = secrets.randbelow(256)
    presence_ping = bytes.fromhex("06 00 ff 06 00 00 11 be 80") + bytes(
        (tag, 0x00, 0x00)
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.setblocking(False)
        probe.bind(("0.0.0.0", 0))
        for address in network.hosts():
            try:
                probe.sendto(presence_ping, (str(address), 623))
            except OSError:
                continue

        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            readable, _, _ = select.select([probe], [], [], remaining)
            if not readable:
                break
            payload, peer = probe.recvfrom(2048)
            if (
                peer[1] == 623
                and len(payload) == 28
                and payload[0:4] == b"\x06\x00\xff\x06"
                and payload[4:8] == b"\x00\x00\x11\xbe"
                and payload[8] == 0x40
                and payload[9] == tag
                and payload[10] == 0x00
                and payload[11] == 0x10
                and len(payload) == 12 + payload[11]
                and ipaddress.ip_address(peer[0]) in network
            ):
                responders.add(peer[0])
                if network.prefixlen == 32:
                    break
    return sorted(responders, key=ipaddress.ip_address)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("network", type=ipaddress.ip_network)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()
    if not isinstance(args.network, ipaddress.IPv4Network):
        parser.error("only IPv4 networks are supported")
    if args.network.num_addresses > 256:
        parser.error("the discovery network may contain at most 256 addresses")
    for address in discover(args.network, max(0.1, min(args.timeout, 10.0))):
        print(address)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
