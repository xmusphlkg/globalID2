"""The local VPN gateway cannot relay arbitrary destinations or protocols."""
import pytest

from src.services.ingestion.co_ins_proxy import official_connect_target


@pytest.mark.parametrize("target", ["www.ins.gov.co:443", "ins.gov.co:443"])
def test_official_https_target(target):
    assert official_connect_target(target) == (target.split(":")[0], 443)


@pytest.mark.parametrize("target", [
    "example.com:443", "evilins.gov.co:443", "ins.gov.co.evil.example:443",
    "127.0.0.1:443", "www.ins.gov.co:80", "user:password@www.ins.gov.co:443",
    "www.ins.gov.co:443/", "www.ins.gov.co:443?x=1", "www.ins.gov.co:443#bad",
])
def test_refuses_other_hosts_ports_and_authorities(target):
    with pytest.raises(ValueError):
        official_connect_target(target)
