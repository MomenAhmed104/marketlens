def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "products": 100, "competitors": 13}


def test_list_all_products(client):
    body = client.get("/api/catalogue/products").json()
    assert body["total"] == 100 == len(body["items"])
    first = body["items"][0]
    assert first["product_id"] == "SB-001"
    assert first["displayed_price"] == "189.99"  # Decimal serialised as string: no float drift
    assert first["provenance"]["source_type"]


def test_filter_and_search(client):
    loopers = client.get("/api/catalogue/products", params={"subcategory": "looper"}).json()
    assert loopers["total"] > 0
    assert all(p["subcategory"] == "Looper" for p in loopers["items"])

    hit = client.get("/api/catalogue/products", params={"q": "boss rc-5"}).json()
    assert [p["product_id"] for p in hit["items"]] == ["SB-001"]

    effects = client.get("/api/catalogue/products", params={"category": "Effects"}).json()
    assert effects["total"] == 61
    sb013 = client.get("/api/catalogue/products/SB-013").json()
    assert sb013["displayed_price"] == "65.00"

    none = client.get("/api/catalogue/products", params={"q": "zzzz-no-match"}).json()
    assert none == {"total": 0, "items": []}


def test_get_single_product(client):
    r = client.get("/api/catalogue/products/SB-100")
    assert r.status_code == 200
    assert r.json()["manufacturer_model"] == "MG-50Li"
    assert client.get("/api/catalogue/products/SB-999").status_code == 404


def test_lookup_selected_products(client):
    r = client.post(
        "/api/catalogue/products/lookup", json={"product_ids": ["SB-010", "SB-002", "SB-010"]}
    )
    assert r.status_code == 200
    assert [p["product_id"] for p in r.json()["items"]] == ["SB-010", "SB-002"]


def test_lookup_unknown_ids_rejected(client):
    r = client.post("/api/catalogue/products/lookup", json={"product_ids": ["SB-001", "SB-999"]})
    assert r.status_code == 404
    assert r.json()["detail"]["missing_ids"] == ["SB-999"]
    assert client.post("/api/catalogue/products/lookup", json={"product_ids": []}).status_code == 422


def test_categories(client):
    cats = client.get("/api/catalogue/categories").json()
    assert sum(c["product_count"] for c in cats) == 100
    assert len(cats) == 48


def test_competitors(client):
    comps = client.get("/api/competitors").json()
    assert len(comps) == 13
    assert {"domain", "company_type", "price_benchmark", "approval_status"} <= comps[0].keys()
