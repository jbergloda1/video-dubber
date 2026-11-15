"""Who may use the server once it listens on every network card (so the iPhone can reach it over Tailscale).

The app has no login, so it must not be open to whoever shares the Wi-Fi: only this computer and devices in
your Tailscale network (100.64.0.0/10, fd7a:115c:a1e0::/48) are let in. `allow_lan` in the settings also lets
in the home network (192.168.x, 10.x, 172.16-31.x) — off by default."""
import ipaddress

TAILSCALE = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48")]


def allowed(host: str | None, allow_lan: bool = False) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_loopback or any(ip in n for n in TAILSCALE):
        return True
    return bool(allow_lan) and ip.is_private and not ip.is_link_local


_lan = [0.0, False]


def lan_enabled() -> bool:
    """settings.allow_lan, re-read at most every 5 s (asked on every request from a non-Tailscale address)."""
    import time
    if time.time() - _lan[0] > 5:
        from . import config
        _lan[:] = [time.time(), bool(config.load().get("allow_lan"))]
    return _lan[1]


def tailscale_ip() -> str | None:
    """This computer's Tailscale IPv4 (to print the address to open on the phone)."""
    import socket
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ipaddress.ip_address(ip) in TAILSCALE[0]:
                return ip
    except OSError:
        pass
    return None
