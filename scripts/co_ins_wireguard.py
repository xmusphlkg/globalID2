#!/usr/bin/env python3
"""Manage the dedicated INS network namespace without changing host routes.

Run as root. The original WireGuard configuration stays private and is never
printed. Only this namespace uses its DNS and default routes.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import tempfile
from pathlib import Path

NAMESPACE = "gids-co-ins"
WG_LINK = "wg-co-ins"
HOST_LINK = "gids-co-host"
PEER_LINK = "gids-co-peer"
HOST_ADDRESS = "169.254.231.1/30"
PROXY_ADDRESS = "169.254.231.2/30"


def command(*args, check=True):
    result = subprocess.run(list(args), capture_output=True, text=True, check=False)
    if check and result.returncode:
        # wg may include secret input in diagnostics, so never relay stderr.
        raise RuntimeError(f"Network operation failed: {args[0]}, exit {result.returncode}")
    return result


def read_config(path):
    sections = []
    for raw in Path(path).read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sections.append((line[1:-1], {}))
        elif "=" in line and sections:
            key, value = line.split("=", 1)
            if key.strip() in sections[-1][1]:
                raise ValueError("Duplicate WireGuard setting")
            sections[-1][1][key.strip()] = value.strip()
        else:
            raise ValueError("Invalid WireGuard configuration")
    interfaces = [values for kind, values in sections if kind == "Interface"]
    peers = [values for kind, values in sections if kind == "Peer"]
    if len(interfaces) != 1 or not peers or any(kind not in {"Interface", "Peer"} for kind, _ in sections):
        raise ValueError("Standard WireGuard interface and peers required")
    interface = interfaces[0]
    if set(interface) - {"PrivateKey", "Address", "DNS", "MTU", "ListenPort"}:
        raise ValueError("Unsupported interface options; shell hooks are not executed")
    for peer in peers:
        if set(peer) - {"PublicKey", "PresharedKey", "AllowedIPs", "Endpoint", "PersistentKeepalive"}:
            raise ValueError("Unsupported WireGuard peer options")
    addresses = [str(ipaddress.ip_interface(value.strip())) for value in interface["Address"].split(",")]
    dns = [str(ipaddress.ip_address(value.strip())) for value in interface["DNS"].split(",")]
    networks = list(dict.fromkeys(str(ipaddress.ip_network(value.strip(), strict=False))
                                 for peer in peers for value in peer["AllowedIPs"].split(",")))
    return interface, peers, addresses, dns, networks


def up(config):
    interface, peers, addresses, dns, networks = read_config(config)
    os.chmod(config, 0o600)
    namespaces = command("ip", "netns", "list").stdout.splitlines()
    if not any(line.split()[0] == NAMESPACE for line in namespaces):
        command("ip", "netns", "add", NAMESPACE)
    if command("ip", "-n", NAMESPACE, "link", "show", WG_LINK, check=False).returncode:
        # Its UDP socket belongs to the host namespace even after moving the
        # interface, keeping the encrypted connection outside its own tunnel.
        command("ip", "link", "add", WG_LINK, "type", "wireguard")
        command("ip", "link", "set", WG_LINK, "netns", NAMESPACE)
    fd, name = tempfile.mkstemp(prefix="gids-ins-wg-", dir="/run")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("[Interface]\nPrivateKey = " + interface["PrivateKey"] + "\n")
            if "ListenPort" in interface:
                stream.write("ListenPort = " + interface["ListenPort"] + "\n")
            for peer in peers:
                stream.write("\n[Peer]\n")
                for key in ("PublicKey", "PresharedKey", "AllowedIPs", "Endpoint", "PersistentKeepalive"):
                    if key in peer:
                        stream.write(key + " = " + peer[key] + "\n")
        command("ip", "netns", "exec", NAMESPACE, "wg", "setconf", WG_LINK, name)
    finally:
        Path(name).unlink(missing_ok=True)
    command("ip", "-n", NAMESPACE, "link", "set", "lo", "up")
    for address in addresses:
        command("ip", "-n", NAMESPACE, "address", "replace", address, "dev", WG_LINK)
    command("ip", "-n", NAMESPACE, "link", "set", "dev", WG_LINK, "mtu", interface.get("MTU", "1380"), "up")
    for network in networks:
        family = "-6" if ":" in network else "-4"
        command("ip", "-n", NAMESPACE, family, "route", "replace", network, "dev", WG_LINK)
    directory = Path("/etc/netns") / NAMESPACE
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "resolv.conf").write_text("".join("nameserver " + value + "\n" for value in dns)
                                           + "options timeout:3 attempts:2\n")
    # libnss-resolve otherwise talks to the host's systemd-resolved UNIX socket
    # and returns host proxy fake-IP records despite this namespace's resolver.
    (directory / "nsswitch.conf").write_text("\n".join(
        "hosts: files dns" if line.startswith("hosts:") else line
        for line in Path("/etc/nsswitch.conf").read_text().splitlines()) + "\n")
    if command("ip", "link", "show", HOST_LINK, check=False).returncode:
        command("ip", "link", "add", HOST_LINK, "type", "veth", "peer", "name", PEER_LINK)
        command("ip", "link", "set", PEER_LINK, "netns", NAMESPACE)
    command("ip", "address", "replace", HOST_ADDRESS, "dev", HOST_LINK)
    command("ip", "link", "set", HOST_LINK, "up")
    command("ip", "-n", NAMESPACE, "address", "replace", PROXY_ADDRESS, "dev", PEER_LINK)
    command("ip", "-n", NAMESPACE, "link", "set", PEER_LINK, "up")
    return {"namespace": NAMESPACE, "proxy_bind": PROXY_ADDRESS.split("/")[0],
            "host_default_route_changed": False, "secret_values_logged": False}


def down():
    command("ip", "link", "delete", HOST_LINK, check=False)
    command("ip", "netns", "delete", NAMESPACE, check=False)
    directory = Path("/etc/netns") / NAMESPACE
    for filename in ("resolv.conf", "nsswitch.conf"):
        (directory / filename).unlink(missing_ok=True)
    if directory.exists():
        directory.rmdir()
    return {"namespace": NAMESPACE, "stopped": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down"))
    parser.add_argument("--config-path", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("This namespace manager requires root")
    if args.action == "up" and not args.config_path:
        parser.error("--config-path is required for up")
    try:
        result = up(args.config_path) if args.action == "up" else down()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
