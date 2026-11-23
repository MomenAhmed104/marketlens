import ipaddress
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import Settings
from app.main import create_app
from app.services.catalogue import COMPETITOR_COLUMNS, PRODUCT_COLUMNS, load_catalogue


_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "testserver"}


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host is None or str(host) in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(scope="session", autouse=True)
def no_outbound_network():
    """Tests never reach the internet: DNS for a non-local name and any non-loopback socket
    connection fail loudly. (IP-literal hosts in tests go through httpx.MockTransport.)"""
    real_getaddrinfo, real_connect, real_connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            try:
                ipaddress.ip_address(str(host))  # an IP literal needs no DNS; connections are still guarded
            except ValueError:
                raise socket.gaierror(f"network disabled in tests: DNS lookup for {host!r}") from None
        return real_getaddrinfo(host, *args, **kwargs)

    def check(address):
        host = address[0] if isinstance(address, tuple) else address
        if not _is_local(host):
            raise OSError(f"network disabled in tests: connection to {address!r}")

    def guarded_connect(self, address):
        check(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        check(address)
        return real_connect_ex(self, address)

    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = (
        guarded_getaddrinfo, guarded_connect, guarded_connect_ex)  # fmt: skip
    yield
    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = real_getaddrinfo, real_connect, real_connect_ex


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    # Real catalogue, but never the real review database.
    return Settings(review_db_path=tmp_path_factory.mktemp("review") / "review.sqlite3")


@pytest.fixture(scope="session")
def catalogue(settings):
    return load_catalogue(settings.catalogue_path)


@pytest.fixture(scope="session")
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def valid_product_row(**overrides) -> dict:
    """A synthetic, obviously-fake row used only to exercise loader validation."""
    row = {
        "product_id": "SB-901",
        "product_name": "Test Pedal",
        "brand": "TestBrand",
        "manufacturer_model": "TP-1",
        "category": "Effects",
        "subcategory": "Test",
        "displayed_price": 10.5,
        "currency": "GBP",
        "product_url": "https://example.test/p/1",
        "verification_status": "test",
        "evidence_url": "https://example.test/c",
        "last_checked": "2026-11-09",
        "source_type": "test",
        "price_snapshot_date": "2026-11-09",
    }
    row.update(overrides)
    return row


def valid_competitor_row(**overrides) -> dict:
    row = {
        "competitor_id": "C-901",
        "competitor_name": "Test Shop",
        "domain_url": "https://www.example.test/",
        "priority": "High",
        "classification": "Primary retailer",
        "notes": None,
    }
    row.update(overrides)
    return row


def write_workbook(
    path: Path,
    products: list[dict],
    competitors: list[dict] | None = None,
    product_columns=PRODUCT_COLUMNS,
) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(list(product_columns))
    for p in products:
        ws.append([p.get(c) for c in product_columns])
    cs = wb.create_sheet("Competitors")
    cs.append(list(COMPETITOR_COLUMNS))
    for c in competitors if competitors is not None else [valid_competitor_row()]:
        cs.append([c.get(col) for col in COMPETITOR_COLUMNS])
    wb.save(path)
    return path
