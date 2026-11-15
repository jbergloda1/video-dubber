from dubber import access


def test_this_pc_and_tailscale_are_allowed():
    for ip in ("127.0.0.1", "::1", "100.116.211.82", "100.64.0.1", "fd7a:115c:a1e0::1234", "::ffff:100.116.211.82"):
        assert access.allowed(ip), ip


def test_wifi_and_internet_are_refused_by_default():
    for ip in ("192.168.0.50", "10.0.0.7", "172.16.0.2", "8.8.8.8", "100.128.0.1", "", None, "garbage"):
        assert not access.allowed(ip), ip


def test_home_wifi_only_when_enabled():
    assert access.allowed("192.168.0.50", allow_lan=True)
    assert not access.allowed("8.8.8.8", allow_lan=True)
