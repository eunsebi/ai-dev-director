"""[fork] Remote use through `tailscale serve`: configured *.ts.net names pass the Host/Origin check;
nothing else does, and without config the check is exactly the upstream localhost-only one."""
import pytest

import dashboard_server as d

NAME = "macbook.tail1234.ts.net"


@pytest.fixture
def remote(monkeypatch):
    def set_hosts(hosts):
        monkeypatch.setattr(d, "_REMOTE_HOSTS", None)
        monkeypatch.setattr(d, "load_config", lambda: {"remote_access": {"hosts": hosts}})
    return set_hosts


def ok(headers):
    return d.is_request_origin_allowed(headers, 8081)[0]


def test_default_is_localhost_only(remote):
    remote([])
    assert ok({"Host": "127.0.0.1:8081", "Origin": "http://127.0.0.1:8081"})
    assert not ok({"Host": NAME, "Origin": "https://" + NAME})


def test_tailscale_name_is_accepted(remote):
    remote([NAME.upper() + "."])
    assert ok({"Host": NAME, "Origin": "https://" + NAME})
    assert ok({"Host": NAME + ":443"})
    assert ok({"Host": "127.0.0.1:8081"})                                  # local use unchanged
    assert not ok({"Host": NAME, "Origin": "http://" + NAME})              # plain http origin refused
    assert not ok({"Host": NAME, "Origin": "https://evil.example"})
    assert not ok({"Host": "evil.example", "Origin": "https://" + NAME})


def test_only_ts_net_names_can_be_configured(remote):
    remote(["papercode.example.com", "", None, "evil.ts.net.example.com"])
    assert d.remote_hosts() == frozenset()
