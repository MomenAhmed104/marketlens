"""Safe outbound fetching for product images (free HTTP, no paid service).

Used for two things only: reading a product page's HTML to find its image, and fetching an
image for the dashboard's cache. Every request:
  - is http(s) only, on the default port (80/443), with no user:password part;
  - goes to a public address only (`is_public_ip`: globally routable unicast, so loopback,
    private, link-local, carrier-grade NAT 100.64.0.0/10, reserved, multicast and IPv4-mapped
    forms of those are refused). The check runs twice: on the URL before the request, and again
    at connect time inside the transport (`PublicOnlyBackend`), which connects to the very
    address it validated. A DNS answer that changes between the check and the connection
    (DNS rebinding) therefore cannot reach a private address;
  - follows at most MAX_REDIRECTS redirects, each re-checked;
  - is paced per host (`HostPacer`: a few at a time and a minimum gap between request starts);
  - stops reading at a byte cap; image bytes are checked by their signature (`sniff_image_type`),
    not by the Content-Type header alone.
Environment proxies are ignored (`trust_env=False`) so the connect-time check sees the real host.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

MAX_REDIRECTS = 4
ALLOWED_PORTS = frozenset({80, 443})
USER_AGENT = "Mozilla/5.0 (compatible; MarketLensImageBot/1.0)"


class UnsafeURL(ValueError):
    """The URL is not an allowed public http(s) address."""


@dataclass(frozen=True)
class Fetched:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes
    truncated: bool


def is_public_ip(ip: str) -> bool:
    """True only for a globally routable unicast address (IPv4-mapped IPv6 is judged as IPv4)."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])  # drop an IPv6 zone id
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    # is_global excludes private, loopback, link-local, CGNAT (100.64/10), documentation and
    # reserved ranges; multicast is excluded separately (some multicast ranges are "global").
    return addr.is_global and not addr.is_multicast and not addr.is_unspecified


def check_url_syntax(url: str) -> str:
    """The URL's host if it is an allowed http(s) URL shape (no DNS). Raises UnsafeURL."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UnsafeURL("malformed URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURL("only http(s) URLs with a host are allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURL("URLs with credentials are not allowed")
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeURL("only the standard ports 80 and 443 are allowed")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    if not is_public_ip(host):
        raise UnsafeURL("host is not a public address")
    return host


async def _getaddrinfo(host: str, port: int):
    return await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)


async def resolve_public(host: str, port: int) -> list[str]:
    """Every address `host` resolves to, if all are public. Raises UnsafeURL otherwise."""
    if isinstance(host, bytes):  # httpcore passes the origin host as bytes
        host = host.decode("ascii")
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = await _getaddrinfo(host, port)
        except OSError as e:
            raise UnsafeURL(f"host does not resolve ({type(e).__name__})") from None
        addresses = list(dict.fromkeys(i[4][0] for i in infos))
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise UnsafeURL("host is not a public address")
    return addresses


async def check_public_url(url: str) -> None:
    """Raise UnsafeURL unless `url` is an allowed http(s) URL whose host resolves only to public addresses."""
    host = check_url_syntax(url)
    parts = urlsplit(url)
    await resolve_public(host, parts.port or (443 if parts.scheme == "https" else 80))


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Network backend that resolves, validates and connects to the same address.

    TLS still verifies the certificate against the URL's host name (httpcore passes the origin
    host as the TLS server name), so pinning the address does not weaken HTTPS."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None):
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = await resolve_public(host, port)
        last: Exception | None = None
        for ip in addresses:
            try:
                return await self._inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                                     socket_options=socket_options)  # fmt: skip
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        raise last or httpcore.ConnectError("no address to connect to")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise UnsafeURL("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def safe_transport() -> httpx.AsyncHTTPTransport:
    """An httpx transport whose every connection goes through PublicOnlyBackend."""
    transport = httpx.AsyncHTTPTransport(retries=0)
    pool = transport._pool  # httpx 0.28: an httpcore.AsyncConnectionPool (no proxy configured)
    if not isinstance(pool, httpcore.AsyncConnectionPool) or not hasattr(pool, "_network_backend"):
        raise RuntimeError("unsupported httpx/httpcore version for the image network guard")
    pool._network_backend = PublicOnlyBackend()
    return transport


def safe_client(timeout: float) -> httpx.AsyncClient:
    """The HTTP client used for every real image and image-page request."""
    return httpx.AsyncClient(timeout=timeout, transport=safe_transport(), trust_env=False, follow_redirects=False)


class HostPacer:
    """Politeness per host: at most `per_host` requests in flight and `min_interval` seconds
    between request starts to the same host. Shared by every request one finder (or the
    image endpoint) makes, so a catalogue collection never bursts against one site."""

    def __init__(self, per_host: int = 2, min_interval: float = 0.25):
        self.per_host, self.min_interval = max(1, per_host), max(0.0, min_interval)
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    @asynccontextmanager
    async def slot(self, url: str) -> AsyncIterator[None]:
        host = (urlsplit(url).hostname or "").lower()
        sem = self._sems.setdefault(host, asyncio.Semaphore(self.per_host))
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with sem:
            async with lock:  # spaces out request starts; the request itself runs outside the lock
                wait = self._last.get(host, float("-inf")) + self.min_interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last[host] = time.monotonic()
            yield


def sniff_image_type(data: bytes) -> str | None:
    """The raster image type from the file signature, or None (SVG and anything else -> None)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


async def safe_get(client: httpx.AsyncClient, url: str, max_bytes: int, accept: str,
                   pacer: HostPacer | None = None) -> Fetched:  # fmt: skip
    """GET with manual, re-checked redirects and a byte cap. Raises UnsafeURL or httpx errors."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        await check_public_url(current)
        async with (pacer.slot(current) if pacer else _no_slot()):
            async with client.stream("GET", current, headers={"Accept": accept, "User-Agent": USER_AGENT},
                                     follow_redirects=False) as resp:  # fmt: skip
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    current = urljoin(current, resp.headers["location"])
                    continue
                chunks: list[bytes] = []
                size = 0
                truncated = False
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        truncated = True
                        break
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                return Fetched(url=current, status=resp.status_code, content_type=ctype, body=b"".join(chunks),
                               truncated=truncated)  # fmt: skip
    raise UnsafeURL("too many redirects")


@asynccontextmanager
async def _no_slot() -> AsyncIterator[None]:
    yield
