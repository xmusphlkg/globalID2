# Colombia INS original-document transport

The production outlet uses the operator's private WireGuard configuration
`.env.GIDS-CH-CO-2.conf` (mode 0600, excluded from Git). Two systemd units manage
the dedicated `gids-co-ins` network namespace and an INS-only HTTPS CONNECT
gateway: `globalid-co-ins-egress.service` and `globalid-co-ins-proxy.service`.
They are enabled at boot. The gateway binds to `169.254.231.2:18088` on a private
host/namespace veth, permits only official INS HTTPS targets, and runs as
`likangguo`. It cannot fall back through the host's default route. The host's
default route and other countries' network paths remain independent.

API and general-worker systemd drop-ins configure
`CO_INS_HTTP_PROXY=http://169.254.231.2:18088`. For a shell backfill, set the same
variable on that command. It contains no authentication secrets; the WireGuard
private key stays in the ignored configuration file and a temporary root-only
file during interface configuration.

Source units and namespace manager are in `configs/systemd/globalid-co-ins-*`
and `scripts/co_ins_wireguard.py`. The namespace has its own `resolv.conf` and
`nsswitch.conf`: `hosts: files dns` prevents the host's systemd-resolved UNIX
socket from returning proxy fake-IP records inside the tunnel.

Operational checks:

```bash
systemctl status globalid-co-ins-egress.service globalid-co-ins-proxy.service
CO_INS_HTTP_PROXY=http://169.254.231.2:18088 \
  venv/bin/python -m src.data.crawlers.co_ins_transport --preflight
```

On 2026-11-16 the tunnel established a WireGuard handshake, its public exit was
identified as Colombia, and complete INS archive HTML, SharePoint JSON and
original PDF responses were downloaded with TLS verification. Diagnostic
evidence is under `data/raw/co/network-diagnostics/20261116-wireguard/`.

The INS bulletin archive requires an approved network outlet that can fetch
complete HTTPS responses. Configure exactly one of these variables in the
collector's environment:

```bash
CO_INS_HTTP_PROXY=http://approved-proxy.example:7890
# Or, for SOCKS with remote DNS:
CO_INS_SOCKS_PROXY=socks5h://approved-proxy.example:1080
```

Credentials, when required, belong in the deployment secret store, not this
file. The transport ignores ambient proxy variables, checks the source and
each redirect against official `ins.gov.co` HTTPS hosts, and always enables
TLS certificate validation. A failed request raises an error and does not
fall back to direct access. Partial HTTP 206 responses are rejected. SOCKS
support requires PySocks in the collector's
Python environment (`pip install PySocks`); readiness reports it as unavailable
otherwise.

Check the configuration without exposing the proxy URL:

```bash
venv/bin/python -m src.data.crawlers.co_ins_transport --readiness
```

After the approved outlet is available, run one full-body official archive
probe. It verifies HTML identity and response length when uncompressed, then
prints the official final URL, byte count and SHA-256 digest:

```bash
venv/bin/python -m src.data.crawlers.co_ins_transport --preflight
```

This preflight does not certify any bulletin PDF. The BES caller must capture
and validate each complete original PDF separately before parsing or declaring
a week unavailable.
