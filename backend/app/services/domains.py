"""Deterministic domain normalisation."""

from urllib.parse import urlsplit


def normalize_hostname(url_or_host: str) -> str:
    """Return a canonical hostname: lowercase, no scheme/port/path, no leading 'www.'.

    Accepts a full URL ("https://www.Andertons.co.uk/x") or a bare host
    ("andertons.co.uk"). Raises ValueError if no hostname can be found.
    """
    value = url_or_host.strip()
    if "://" not in value:
        value = "//" + value
    host = urlsplit(value).hostname  # lowercases and strips port/userinfo
    if not host or "." not in host:
        raise ValueError(f"Cannot extract a hostname from {url_or_host!r}")
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host
