import time
import uuid

from sqlalchemy import insert

from app.db.session import get_sessionmaker
from app.models import Collection, IdentifierType, Paper
from app.models.collection import paper_collections
from app.services.csl import isolated

API = "/api/v1"


async def personal_ws(c) -> str:
    return next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")


async def add(c, title, **kw):
    r = await c.post(f"{API}/papers/metadata-only", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def test_styles_listing(login):
    _, c = await login("a@example.com")
    r = await c.get(f"{API}/bibliography/styles")
    assert r.status_code == 200
    styles = {s["id"]: s for s in r.json()}
    assert set(styles) == {"apa", "ieee", "chicago-author-date", "nature", "vancouver", "acm-sig-proceedings"}
    assert styles["apa"]["kind"] == "author-date" and styles["ieee"]["kind"] == "numeric" and styles["apa"]["title"]


async def test_text_and_html(login):
    _, c = await login("a@example.com")
    await add(c, "Clos & fabrics", authors=[{"family": "Müller", "given": "Zoë"}], year=2023)
    await add(c, "Alpha routing", authors=[{"family": "Abe", "given": "Kei"}, {"family": "Ito", "given": "Mai"}], year=2021)

    r = await c.get(f"{API}/bibliography", params={"style": "apa"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["x-total-count"] == "2"
    lines = r.text.splitlines()
    assert lines[0].startswith("Abe, K., & Ito, M. (2021). Alpha routing.")
    assert lines[1].startswith("Müller, Z. (2023). Clos & fabrics.")

    r = await c.get(f"{API}/bibliography", params={"style": "ieee", "format": "html"})
    assert r.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in r.headers["content-security-policy"] and "sandbox" in r.headers["content-security-policy"]
    # titles are in list (title) order and numbered in that order
    assert r.text.index("[1] K. Abe") < r.text.index("[2] Z. Müller") and "Clos &amp; fabrics" in r.text
    assert r.text.startswith('<div class="csl-bib-body">')

    r = await c.get(f"{API}/bibliography", params={"style": "apa", "locale": "ja-JP"})
    assert r.status_code == 200 and "(2021年)" in r.text


async def test_html_is_escaped(login):
    _, c = await login("a@example.com")
    await add(c, "<script>alert(1)</script>", authors=[{"family": "<img src=x onerror=alert(2)>", "given": "A"}], year=2020)
    r = await c.get(f"{API}/bibliography", params={"style": "apa", "format": "html"})
    assert "<script" not in r.text and "<img" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text


async def test_validation(login):
    _, c = await login("a@example.com")
    assert (await c.get(f"{API}/bibliography", params={"style": "nope"})).status_code == 422
    assert (await c.get(f"{API}/bibliography", params={"locale": "fr-FR"})).status_code == 422
    assert (await c.get(f"{API}/bibliography", params={"format": "docx"})).status_code == 422
    assert (await c.get(f"{API}/bibliography", params={"ids": "not-a-uuid"})).status_code == 422
    too_many = ",".join(str(uuid.uuid4()) for _ in range(1001))
    assert (await c.get(f"{API}/bibliography", params={"ids": too_many})).status_code == 422
    r = await c.get(f"{API}/bibliography", params={"style": "ieee", "format": "html"})  # empty library
    assert r.status_code == 200 and r.headers["x-total-count"] == "0" and "csl-entry" not in r.text
    assert (await c.get(f"{API}/bibliography", params={"format": "text"})).text == ""


async def test_filters_and_ids(login):
    _, c = await login("a@example.com")
    p1 = await add(c, "Alpha paper", year=2023, tags=["bgp"], authors=[{"family": "Abe"}])
    p2 = await add(c, "Beta paper", year=2024, tags=["other"], authors=[{"family": "Baba"}])
    async with get_sessionmaker()() as db:
        col = Collection(workspace_id=uuid.UUID(p1["workspace_id"]), name="Reading")
        db.add(col)
        await db.flush()
        await db.execute(paper_collections.insert().values(paper_id=uuid.UUID(p1["id"]), collection_id=col.id))
        await db.commit()
        cid = str(col.id)

    async def lines(**params):
        r = await c.get(f"{API}/bibliography", params={"style": "apa", **params})
        assert r.status_code == 200, r.text
        return r.text.splitlines()

    assert len(await lines()) == 2
    assert len(await lines(tag="bgp")) == 1 and (await lines(tag="bgp"))[0].startswith("Abe")
    assert (await lines(year=2024))[0].startswith("Baba")
    assert (await lines(collection_id=cid))[0].startswith("Abe")
    assert (await lines(q="beta"))[0].startswith("Baba")
    assert await lines(status="read") == []
    assert (await lines(ids=p2["id"]))[0].startswith("Baba")
    assert len(await lines(ids=f"{p1['id']},{p2['id']}")) == 2
    assert await lines(ids=f"{uuid.uuid4()}") == []
    assert len(await lines(ids=p1["id"], tag="other")) == 0  # ids narrow the filters, they do not widen them


async def test_papers_the_caller_cannot_see_are_left_out(login, pat_client):
    _, owner = await login("a@example.com")
    mine = await add(owner, "Private paper", authors=[{"family": "Abe"}])
    _, other = await login("b@example.com")
    theirs = await add(other, "Other paper", authors=[{"family": "Baba"}])

    r = await other.get(f"{API}/bibliography", params={"ids": f"{mine['id']},{theirs['id']}"})
    assert r.status_code == 200 and r.headers["x-total-count"] == "1"
    assert "Private" not in r.text and r.text.startswith("Baba")
    assert (await other.get(f"{API}/bibliography", params={"workspace_id": await personal_ws(owner)})).status_code == 404

    ok = await pat_client(owner, scopes=("read:papers",))
    assert (await ok.get(f"{API}/bibliography", params={"style": "apa"})).status_code == 200
    assert (await ok.get(f"{API}/bibliography/styles")).status_code == 200
    no_scope = await pat_client(owner, scopes=("write:papers",))
    assert (await no_scope.get(f"{API}/bibliography")).status_code == 403
    assert (await no_scope.get(f"{API}/bibliography/styles")).status_code == 403
    anon = await pat_client(owner)
    anon.headers.pop("Authorization")
    assert (await anon.get(f"{API}/bibliography")).status_code == 401


async def test_rate_limit(login):
    _, c = await login("a@example.com")
    codes = [(await c.get(f"{API}/bibliography")).status_code for _ in range(32)]
    assert codes[:30] == [200] * 30 and 429 in codes[30:]


async def _bulk(c, n: int, **extra):
    ws = uuid.UUID(await personal_ws(c))
    rows = [
        {
            "workspace_id": ws, "identifier": f"BF-{i:06d}", "identifier_type": IdentifierType.internal,
            "citekey": f"key{i}", "title": f"Paper number {i} about routing",
            "authors": [{"family": f"Fam{i % 97}", "given": f"Given{j}"} for j in range(3)],
            "year": 1990 + i % 35, "venue": "Journal X", **extra,
        }
        for i in range(n)
    ]
    async with get_sessionmaker()() as db:
        await db.execute(insert(Paper), rows)
        await db.commit()


async def test_limit_of_1000_papers(login):
    _, c = await login("a@example.com")
    await _bulk(c, 1001)
    r = await c.get(f"{API}/bibliography", params={"style": "apa"})
    assert r.status_code == 422 and "narrow" in r.json()["detail"]
    r = await c.get(f"{API}/bibliography", params={"style": "apa", "year": 1990})
    assert r.status_code == 200


async def test_thousand_papers_in_apa_through_the_api_within_ten_seconds(login):
    _, c = await login("a@example.com")
    await _bulk(c, 1000)
    t = time.monotonic()
    r = await c.get(f"{API}/bibliography", params={"style": "apa", "format": "html"})
    elapsed = time.monotonic() - t
    assert r.status_code == 200 and r.headers["x-total-count"] == "1000" and r.text.count('class="csl-entry"') == 1000
    assert elapsed < 10, elapsed


async def test_a_hostile_record_gets_422_and_the_api_keeps_serving(login, monkeypatch):
    _, c = await login("a@example.com")
    await add(c, "Fine paper", authors=[{"family": "Abe"}], year=2020)
    hostile_authors = [{"family": f"Family{n}", "given": f"Given{n}"} for n in range(100_000)]
    ws = uuid.UUID(await personal_ws(c))
    async with get_sessionmaker()() as db:
        db.add(Paper(workspace_id=ws, identifier="BF-hostile", identifier_type=IdentifierType.internal, citekey="hostile",
                     title="Zzz hostile", authors=hostile_authors, year=2020))
        await db.commit()
    # With the production limits this record is formatted in under a second (the styles print the first few
    # authors only), so the child's memory limit is lowered to show what happens when it is exceeded.
    monkeypatch.setattr(isolated, "MEMORY_BYTES", 80 * 1024 * 1024)
    r = await c.get(f"{API}/bibliography", params={"style": "apa"})
    assert r.status_code == 422 and "too large" in r.json()["detail"]
    # the API process is fine and formats a request that does not include the hostile record
    r = await c.get(f"{API}/bibliography", params={"style": "apa", "q": "Fine"})
    assert r.status_code == 200 and r.text.startswith("Abe")
