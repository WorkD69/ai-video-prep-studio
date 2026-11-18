"""PWA: manifest, icons and favicon are served; pages link the manifest."""

from fastapi.testclient import TestClient


def test_manifest_served_with_manifest_content_type(client: TestClient) -> None:
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")
    data = resp.json()
    assert data["name"] == "AI Video Prep Studio"
    assert data["start_url"] == "/"
    assert data["display"] == "standalone"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes


def test_every_manifest_icon_is_served_as_png(client: TestClient) -> None:
    for icon in client.get("/manifest.webmanifest").json()["icons"]:
        resp = client.get(icon["src"])
        assert resp.status_code == 200, icon["src"]
        assert resp.headers["content-type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_favicon_no_longer_404(client: TestClient) -> None:
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_index_links_manifest_and_icon(client: TestClient) -> None:
    html = client.get("/").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'href="/static/favicon-32.png"' in html


def test_static_does_not_escape_its_directory(client: TestClient) -> None:
    # app/config.py really exists one level above app/static
    for path in ("/static/..%2Fconfig.py", "/static/%2e%2e/config.py", "/static/..%5Cconfig.py"):
        assert client.get(path).status_code == 404, path
