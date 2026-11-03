import httpx
import pytest

from app.api.v1 import papers as papers_api
from app.core.net import safe_client
from tests.helpers import make_meta, pdf_files
from tests.pdfgen import make_pdf

API = "/api/v1"
LINES = ["Scalable BGP Routing in Clos Fabrics", "arXiv:2405.12345v1 [cs.NI]"]


def mock_factory(handler, seen=None):
    def factory():
        # Same client class/options as production, but no real network.
        client = safe_client()
        client._transport = httpx.MockTransport(handler)
        return client

    return factory


async def post(c, url, **extra):
    return await c.post(f"{API}/papers/import-url", json={"url": url, **extra})


async def test_blocked_destinations(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    for url in (
        "http://127.0.0.1/x.pdf",
        "http://169.254.169.254/latest/meta-data/x.pdf",
        "http://10.0.0.5/x.pdf",
        "http://[::1]/x.pdf",
        "http://[::ffff:127.0.0.1]/x.pdf",
        "http://100.64.0.1/x.pdf",
        "ftp://example.com/x.pdf",
        "http://example.com:22/x.pdf",
        "file:///etc/passwd",
        "http://user:pw@example.com/x.pdf",
    ):
        r = await post(c, url)
        assert r.status_code == 400 and r.json() == {"detail": "URL not allowed"}, url
    assert pdf_files(storage.library) == []


async def test_blocked_redirect_target(login, resolver, storage, enqueued, monkeypatch):
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/x.pdf"})

    monkeypatch.setattr(papers_api, "_http_client_factory", mock_factory(handler))
    _, c = await login("a@example.com")
    r = await post(c, "https://example.org/paper.pdf")
    assert r.status_code == 400 and r.json() == {"detail": "URL not allowed"}


async def test_import_success_and_arxiv_rewrite(login, resolver, storage, enqueued, monkeypatch):
    data = make_pdf(LINES)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf"})

    monkeypatch.setattr(papers_api, "_http_client_factory", mock_factory(handler))
    _, c = await login("a@example.com")
    r = await post(c, "https://arxiv.org/abs/2405.12345")
    assert r.status_code == 201, r.text
    assert seen == ["https://arxiv.org/pdf/2405.12345"]
    p = r.json()
    assert p["identifier"] == "2405.12345" and p["has_pdf"] is True
    assert (storage.library / p["relative_file_path"]).read_bytes() == data
    assert enqueued.jobs == [("process_paper", p["id"])]
    # importing again -> already has pdf
    assert (await post(c, "https://example.org/other.pdf")).status_code == 409


async def test_import_follows_safe_redirect(login, resolver, storage, enqueued, monkeypatch):
    data = make_pdf(["plain"])

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final.pdf"})
        return httpx.Response(200, content=data)

    monkeypatch.setattr(papers_api, "_http_client_factory", mock_factory(handler))
    _, c = await login("a@example.com")
    r = await post(c, "https://example.org/start")
    assert r.status_code == 201 and r.json()["title"] == "final"  # filename of the final hop


async def test_import_rejections(login, resolver, storage, enqueued, monkeypatch):
    state = {}

    def handler(request):
        return state["resp"]

    monkeypatch.setattr(papers_api, "_http_client_factory", mock_factory(handler))
    _, c = await login("a@example.com")
    state["resp"] = httpx.Response(200, content=b"<html>hi</html>", headers={"content-type": "application/pdf"})
    assert (await post(c, "https://example.org/a.pdf")).status_code == 415
    state["resp"] = httpx.Response(404, content=b"nope")
    assert (await post(c, "https://example.org/a.pdf")).status_code == 502
    state["resp"] = httpx.Response(200, content=b"%PDF-1.4\n" + b"z" * 3 * 1024 * 1024)
    assert (await post(c, "https://example.org/a.pdf")).status_code == 413
    state["resp"] = httpx.Response(200, content=b"%PDF-1.4\n" + b"junk" * 40)
    assert (await post(c, "https://example.org/a.pdf")).status_code == 422
    assert pdf_files(storage.library) == []
    d = storage.library / ".incoming"
    assert not d.exists() or not list(d.iterdir())
    # body validation
    assert (await c.post(f"{API}/papers/import-url", json={"url": "x" * 8193})).status_code == 422
    assert (await c.post(f"{API}/papers/import-url", json={})).status_code == 422


async def test_import_disabled_and_permissions(login, pat_client, resolver, storage, enqueued, monkeypatch):
    from app.core.config import get_settings

    _, c = await login("a@example.com")
    ro = await pat_client(c, ("read:papers",))
    assert (await post(ro, "https://example.org/a.pdf")).status_code == 403
    monkeypatch.setattr(get_settings(), "url_import_enabled", False)
    assert (await post(c, "https://example.org/a.pdf")).status_code == 404


# ------------------------------------------------------------------ landing pages / identifiers
PII = "S0142061525003874"
ELS_DOI = "10.1016/j.ijepes.2025.110839"


def test_find_pdf_link_variants():
    from app.services.remote_pdf import find_pdf_link

    base = "https://pub.example.org/article/123"
    assert find_pdf_link('<meta name="citation_pdf_url" content="/pdf/123.pdf">', base) == "https://pub.example.org/pdf/123.pdf"
    assert find_pdf_link("<META content='https://cdn.example.org/a.pdf?x=1&amp;y=2' NAME='citation_pdf_url'/>", base) == (
        "https://cdn.example.org/a.pdf?x=1&y=2"
    )
    assert find_pdf_link('<meta name="citation_title" content="x">', base) is None


def test_resolve_source_inputs():
    from app.services.remote_pdf import RemoteFetchError, resolve_source

    assert resolve_source("arXiv:2405.12345v2").url == "https://arxiv.org/pdf/2405.12345"
    assert resolve_source("https://arxiv.org/abs/2405.12345v2").url == "https://arxiv.org/pdf/2405.12345"
    s = resolve_source("doi:10.1145/3651890")
    assert (s.url, s.identifier_hint) == ("https://doi.org/10.1145/3651890", "10.1145/3651890")
    assert resolve_source(PII).url.endswith(f"/pii/{PII}")
    signed = f"https://pdf.assets.example.com/x/main.pdf?X-Amz-Security-Token=FAKE&pii={PII}&cc=jp"
    s = resolve_source(signed)
    assert s.url == signed and s.identifier_hint == PII
    assert resolve_source("https://example.org/paper.pdf").identifier_hint is None
    with pytest.raises(RemoteFetchError):
        resolve_source("Scalable BGP Routing")


async def test_import_follows_landing_page_pdf_link(login, resolver, storage, enqueued, monkeypatch):
    data = make_pdf(["landing"])
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "doi.org":
            return httpx.Response(302, headers={"location": "https://pub.example.org/article/9"})
        if request.url.path == "/article/9":
            page = '<html><head><meta name="citation_pdf_url" content="/content/9.full.pdf"></head></html>'
            return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf"})

    monkeypatch.setattr(papers_api, "_http_client_factory", mock_factory(handler))
    resolver.add(make_meta("10.5555/oa.9", title="Open Access Paper"))
    _, c = await login("a@example.com")
    r = await post(c, "10.5555/oa.9")
    assert r.status_code == 201, r.text
    assert seen == ["https://doi.org/10.5555/oa.9", "https://pub.example.org/article/9", "https://pub.example.org/content/9.full.pdf"]
    p = r.json()
    assert p["identifier"] == "10.5555/oa.9" and p["title"] == "Open Access Paper" and p["has_pdf"] is True


async def test_import_landing_page_failures(login, resolver, storage, enqueued, monkeypatch):
    state = {}

    def handler(request):
        if request.url.path == "/pdf":
            return state["pdf"]
        return httpx.Response(200, text=state["page"], headers={"content-type": "text/html"})

    monkeypatch.setattr(papers_api, "_http_client_factory", mock_factory(handler))
    _, c = await login("a@example.com")
    state["page"] = "<html>paywall</html>"
    r = await post(c, "https://pub.example.org/article")
    assert r.status_code == 415 and "landing page" in r.json()["detail"]
    state["page"] = '<meta name="citation_pdf_url" content="http://169.254.169.254/latest">'
    assert (await post(c, "https://pub.example.org/article")).status_code == 400
    state["page"] = '<meta name="citation_pdf_url" content="/pdf">'
    state["pdf"] = httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"})
    assert (await post(c, "https://pub.example.org/article")).status_code == 415
    assert (await post(c, "just a title")).status_code == 422
    assert pdf_files(storage.library) == []


async def test_import_signed_url_pii_attaches_to_wishlist(login, resolver, storage, enqueued, monkeypatch):
    data = make_pdf(["A PDF whose text carries no identifier"])

    def handler(request):
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf"})

    monkeypatch.setattr(papers_api, "_http_client_factory", mock_factory(handler))
    meta = make_meta(ELS_DOI, title="An optimization model for identifying backbone networks")
    resolver.add(meta)
    resolver.items[PII.lower()] = meta
    _, c = await login("a@example.com")
    wish = await c.post(f"{API}/papers/metadata-only", json={"identifier": ELS_DOI})
    assert wish.status_code == 201 and wish.json()["status"] == "wishlist"
    signed = f"https://pdf.assets.example.com/271479/main.pdf?X-Amz-Security-Token=FAKE&X-Amz-Expires=300&pii={PII}"
    r = await post(c, signed)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["id"] == wish.json()["id"]  # attached, not duplicated
    assert p["identifier"] == ELS_DOI and p["has_pdf"] is True and p["status"] == "unread"
    assert p["relative_file_path"].startswith(f"2024/{ELS_DOI.replace('/', '_')}_")
    # unresolvable PII hint must not become the identifier
    resolver.items.clear()
    r = await post(c, signed.replace(PII, "S0000000000000001"))
    assert r.status_code == 201, r.text
    assert r.json()["identifier_type"] == "internal" and r.json()["identifier"].startswith("BF-")
