"""Read iDRAC network/protocol settings through Redfish without modifying them."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import warnings

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning


def get_json(client: requests.Session, base_url: str, path: str) -> dict:
    response = client.get(f"{base_url}{path}", timeout=(3, 8), verify=False)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected Redfish payload at {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("IDRAC_HOST", "192.168.1.120"))
    parser.add_argument("--username", default=os.getenv("IDRAC_USER", "root"))
    args = parser.parse_args()
    host = args.host.strip()
    if not host or any(character in host for character in "/?#@"):
        parser.error("--host must be an iDRAC hostname or IPv4 address")
    base_url = f"https://{host}"

    password = getpass.getpass("iDRAC password (input hidden): ")
    client = requests.Session()
    client.trust_env = False
    client.auth = (args.username, password)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        managers = get_json(client, base_url, "/redfish/v1/Managers")
        members = managers.get("Members") or []
        manager_paths = [
            member.get("@odata.id")
            for member in members
            if isinstance(member, dict) and isinstance(member.get("@odata.id"), str)
        ]
        if not manager_paths:
            raise RuntimeError("No Redfish manager was returned")

        for manager_path in manager_paths:
            manager = get_json(client, base_url, manager_path)
            print(
                "MANAGER",
                manager.get("Id"),
                manager.get("Model"),
                f"firmware={manager.get('FirmwareVersion')}",
            )

            protocol_link = manager.get("NetworkProtocol") or {}
            protocol_path = protocol_link.get("@odata.id")
            if isinstance(protocol_path, str):
                protocols = get_json(client, base_url, protocol_path)
                for name in ("HTTPS", "IPMI", "SSH"):
                    config = protocols.get(name)
                    if isinstance(config, dict):
                        print(
                            "PROTOCOL",
                            name,
                            f"enabled={config.get('ProtocolEnabled')}",
                            f"port={config.get('Port')}",
                        )

            interfaces_link = manager.get("EthernetInterfaces") or {}
            interfaces_path = interfaces_link.get("@odata.id")
            if not isinstance(interfaces_path, str):
                continue
            interfaces = get_json(client, base_url, interfaces_path)
            for member in interfaces.get("Members") or []:
                interface_path = member.get("@odata.id") if isinstance(member, dict) else None
                if not isinstance(interface_path, str):
                    continue
                interface = get_json(client, base_url, interface_path)
                print(
                    "INTERFACE",
                    interface.get("Id"),
                    interface.get("Name"),
                    f"enabled={interface.get('InterfaceEnabled')}",
                )
                for address in interface.get("IPv4Addresses") or []:
                    if isinstance(address, dict):
                        print(
                            "IPV4",
                            f"address={address.get('Address')}",
                            f"mask={address.get('SubnetMask')}",
                            f"gateway={address.get('Gateway')}",
                            f"origin={address.get('AddressOrigin')}",
                        )

            attributes_path = f"{manager_path}/Attributes"
            try:
                attributes_payload = get_json(client, base_url, attributes_path)
            except requests.HTTPError as exc:
                print("ATTRIBUTES", f"unavailable={exc.response.status_code}")
            else:
                attributes = attributes_payload.get("Attributes") or {}
                keywords = ("iprange", "ipblock", "filter", "lockout")
                matches = {
                    str(key): value
                    for key, value in attributes.items()
                    if any(keyword in str(key).casefold() for keyword in keywords)
                }
                for key in sorted(matches):
                    print("ATTRIBUTE", key, f"value={matches[key]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"IDRAC_NETWORK_CHECK_FAILED {exc}", file=sys.stderr)
        sys.exit(1)
