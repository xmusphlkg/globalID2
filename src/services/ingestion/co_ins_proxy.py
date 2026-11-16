"""Small INS-only HTTPS CONNECT gateway inside the Colombia namespace.

Bind to its private veth address. Each upstream connection uses namespace DNS
and WireGuard routes; unavailable tunnels never fall back to the host network.
"""
from __future__ import annotations

import argparse
import select
import socket
import socketserver
import time
from urllib.parse import urlsplit


def official_connect_target(target):
    try:
        parsed = urlsplit("//" + target)
        host = (parsed.hostname or "").lower()
        valid = (parsed.port == 443 and not parsed.username and not parsed.password
                 and not parsed.path and not parsed.query and not parsed.fragment
                 and (host == "ins.gov.co" or host.endswith(".ins.gov.co")))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Only official INS HTTPS targets are permitted")
    return host, 443


class INSConnectHandler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(15)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = client.recv(4096)
                if not chunk:
                    return
                data += chunk
                if len(data) > 16384:
                    raise ValueError("CONNECT headers too large")
            line = data.split(b"\r\n", 1)[0].decode("ascii")
            method, target, version = line.split(" ")
            if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise ValueError("CONNECT required")
            address = official_connect_target(target)
        except (OSError, ValueError, UnicodeError):
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        try:
            with socket.create_connection(address, timeout=20) as upstream:
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                client.settimeout(60)
                upstream.settimeout(60)
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([client, upstream], [], [], 60)
                    if not ready:
                        return
                    for source in ready:
                        chunk = source.recv(256 * 1024)
                        if not chunk:
                            return
                        destination = upstream if source is client else client
                        destination.sendall(chunk)
        except OSError:
            # Request headers, proxy authorization, endpoints and exception
            # strings are intentionally absent from gateway logs.
            return


class INSConnectServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def handle_error(self, request, client_address):
        pass  # No raw request/error logging.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="169.254.231.2")
    parser.add_argument("--port", type=int, default=18088)
    args = parser.parse_args()
    with INSConnectServer((args.bind, args.port), INSConnectHandler) as server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
