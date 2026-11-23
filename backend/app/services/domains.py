"""Deterministic domain normalisation.

Registrable domains come from the Public Suffix List bundled with the pinned `publicsuffixlist`
package (offline: its built-in copy is read, nothing is downloaded; private suffixes such as
github.io count as suffixes, so each user site is its own domain). The previous deterministic
rule is kept as a fallback if the library cannot be loaded or gives no answer.
"""

import re
from functools import lru_cache
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


# Two-label public suffixes we expect to meet. Not a full public-suffix list:
# unknown multi-label suffixes fall back to the last two labels.
_TWO_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au", "co.nz", "co.jp", "com.br", "co.za", "com.cn",
}  # fmt: skip


# Generic second-level labels that country-code registries use as public suffixes
# ("shop.com.ua", "x.org.uk", "y.co.jp"). Under a two-letter country-code TLD these are
# treated as part of the suffix (Stage 9.2 fix: "x.com.ua" was recorded as "com.ua").
# An interim deterministic rule, not the full Public Suffix List (see docs/stage9).
_GENERIC_SECOND_LEVEL = {"com", "co", "org", "net", "ac", "gov", "edu", "ltd", "plc", "me", "biz", "info",
                         "nom", "or", "ne", "go", "gob", "mil"}  # fmt: skip


@lru_cache(maxsize=1)
def _psl():
    """The bundled Public Suffix List (no network), or None if the package is unavailable."""
    try:
        from publicsuffixlist import PublicSuffixList
    except ImportError:  # pragma: no cover - the package is a pinned requirement
        return None
    return PublicSuffixList()  # source=None: the copy shipped inside the package


def is_public_suffix(host: str) -> bool:
    """True for a public suffix ('co.uk', 'com.ua', 'github.io'), never for a registrable domain."""
    psl = _psl()
    h = host.lower().strip(".")
    if psl is not None and "." in h:
        try:
            return bool(psl.is_public(h))
        except Exception:
            pass
    return _fallback_is_public_suffix(h)


def _fallback_is_public_suffix(host: str) -> bool:
    labels = host.lower().strip(".").split(".")
    if len(labels) != 2:
        return False
    return host.lower() in _TWO_LABEL_SUFFIXES or (len(labels[1]) == 2 and labels[1].isalpha()
                                                   and labels[0] in _GENERIC_SECOND_LEVEL)  # fmt: skip


def registrable_domain(url_or_host: str) -> str:
    """The domain a company registers, e.g. 'blog.andertons.co.uk' -> 'andertons.co.uk'.

    Used to group hostnames of the same site and to deduplicate against known
    competitors. Raises ValueError like normalize_hostname.
    """
    host = normalize_hostname(url_or_host)
    psl = _psl()
    if psl is not None:
        try:
            found = psl.privatesuffix(host)
        except Exception:
            found = None
        if found:
            return found
    return fallback_registrable_domain(host)


def fallback_registrable_domain(host: str) -> str:
    """The previous deterministic rule (known two-label suffixes and generic second-level labels
    under a two-letter country code); used when the Public Suffix List gives no answer."""
    labels = host.split(".")
    if len(labels) >= 3 and _fallback_is_public_suffix(".".join(labels[-2:])):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def canonical_domain(value: str) -> str:
    """The one stored key for a domain-level record (e.g. UK eligibility): its registrable domain.

    Strict, unlike `registrable_domain`: only case, surrounding whitespace, a trailing
    dot and a leading 'www.' are normalised ('WWW.Bax-Shop.co.uk' -> 'bax-shop.co.uk').
    Anything ambiguous raises ValueError instead of being guessed at: a URL, path,
    port or credentials, another subdomain ('shop.example.co.uk'), or invalid labels.
    """
    if not isinstance(value, str):
        raise ValueError("domain must be a string")
    host = value.strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise ValueError(f"Not a bare domain name: {value!r} (no scheme, path, port or invalid characters)")
    if is_public_suffix(host):
        raise ValueError(f"{value!r} is a public suffix, not a domain")
    if registrable_domain(host) != host:
        raise ValueError(f"{value!r} is a subdomain; give the registrable domain {registrable_domain(host)!r}")
    return host
