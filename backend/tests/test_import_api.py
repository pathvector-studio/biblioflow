"""File import (metadata only): preview / commit through the API."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.session import get_sessionmaker
from app.models import Collection, Paper
from app.models.collection import paper_collections
from tests.helpers import join_workspace

API = "/api/v1"
FIX = Path(__file__).parent / "fixtures" / "import"


@pytest.fixture(autouse=True)
def _relaxed_rate_limits(monkeypatch):
    from app.api.v1 import imports

    monkeypatch.setattr(imports, "PREVIEW_RATE_LIMIT", 1000)
    monkeypatch.setattr(imports, "COMMIT_RATE_LIMIT", 1000)


def fixture(name):
    return (FIX / name).read_bytes()


async def personal_ws(c) -> str:
    return next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")


async def post(c, path, data, name="x.bib", **form):
    return await c.post(f"{API}/imports{path}", files={"file": (name, data, "application/octet-stream")}, data=form)


async def preview(c, name, **form):
    r = await post(c, "/preview", fixture(name), name, **form)
    assert r.status_code == 200, r.text
    return r.json()


async def commit(c, name, **form):
    r = await post(c, "", fixture(name), name, **form)
    assert r.status_code == 200, r.text
    return r.json()


async def papers(c, **params):
    r = await c.get(f"{API}/papers/light", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_preview_counts_match_commit_and_write_nothing(login):
    _, c = await login("a@example.com")
    pv = await preview(c, "library.bib")
    assert (pv["format"], pv["dry_run"], pv["total"]) == ("bibtex", True, 5)
    assert (pv["new"], pv["matched"], pv["unreadable"]) == (4, 0, 1)
    assert [p["ref"] for p in pv["problems"]] == ["broken2020"]
    assert pv["with_attachments"] == 2
    assert await papers(c) == []  # preview wrote nothing

    done = await commit(c, "library.bib")
    assert (done["new"], done["matched"], done["unreadable"]) == (4, 0, 1) and done["dry_run"] is False
    rows = await papers(c)
    assert len(rows) == 4
    assert {p["status"] for p in rows} == {"wishlist"} and {p["has_pdf"] for p in rows} == {False}
    assert all(p["relative_file_path"] is None for p in rows)


async def test_commit_stores_metadata_tags_notes_and_original_bibtex(login, resolver):
    _, c = await login("a@example.com")
    await commit(c, "library.bib")
    rows = {p["title"]: p for p in await papers(c)}
    v = rows["Attention Is All You Need"]
    assert v["identifier"] == "1706.03762" and v["citekey"] == "vaswani2017attention" and v["year"] == 2017
    assert sorted(v["tags"]) == ["attention", "nlp", "transformers"]
    detail = (await c.get(f"{API}/papers/{v['id']}")).json()
    assert detail["identifier_type"] == "arxiv" and detail["arxiv_id"] == "1706.03762"
    assert detail["notes_md"] == "Read in the 2024 reading group"
    assert detail["bibtex"].startswith("@inproceedings{vaswani2017attention,") and "keywords" in detail["bibtex"]
    he = rows["Deep Residual Learning for Image Recognition"]
    assert he["identifier"] == "10.1109/cvpr.2016.90"
    plain = rows["A Study of Things Without Identifiers"]
    assert plain["identifier"].startswith("BF-")
    assert resolver.calls == []  # the file is the source: no external lookup


async def test_import_twice_creates_no_duplicates(login):
    _, c = await login("a@example.com")
    for name in ("library.bib", "mendeley.ris", "library.csl.json", "zotero.rdf"):
        first = await commit(c, name)
        before = len(await papers(c))
        pv = await preview(c, name)
        assert pv["new"] == 0 and pv["matched"] == first["new"] + first["matched"], name
        again = await commit(c, name)
        assert again["new"] == 0 and again["notes_added"] == 0 and again["tags_added"] == 0, name
        assert len(await papers(c)) == before, name


async def test_matching_existing_paper_adds_tags_but_never_overwrites(login, resolver):
    _, c = await login("a@example.com")
    r = await c.post(
        f"{API}/papers/metadata-only",
        json={"title": "My own title", "year": 1999, "authors": [{"family": "Mine"}], "tags": ["mine"]},
    )
    existing = r.json()
    # Give it the DOI that the Zotero fixture's "Deep learning" carries.
    r = await c.patch(f"{API}/papers/{existing['id']}", json={"notes_md": "my note"})
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as db:
        row = await db.get(Paper, uuid.UUID(existing["id"]))
        row.doi = "10.1038/nature14539"
        await db.commit()

    pv = await preview(c, "zotero.rdf")
    assert (pv["new"], pv["matched"], pv["unreadable"]) == (2, 1, 1)
    assert [i["matched_by"] for i in pv["items"] if i["status"] == "matched"] == ["existing"]
    done = await commit(c, "zotero.rdf")
    assert (done["new"], done["matched"]) == (2, 1)
    assert done["notes_added"] == 1 and done["collections_created"] == 3

    d = (await c.get(f"{API}/papers/{existing['id']}")).json()
    assert d["title"] == "My own title" and d["year"] == 1999 and d["authors"] == [{"family": "Mine", "given": None}]
    assert sorted(d["tags"]) == ["deep learning", "mine", "review"]
    assert d["notes_md"] == "my note\n\n---\n\nGreat survey.\n\n- read intro"
    assert len(d["collection_ids"]) == 2 and d["status"] == "wishlist" and d["has_pdf"] is False


async def test_zotero_collections_nesting_tags_and_notes(login):
    _, c = await login("a@example.com")
    done = await commit(c, "zotero.rdf")
    assert done["format"] == "zotero_rdf" and (done["new"], done["unreadable"]) == (3, 1)
    assert done["collections_created"] == 3 and done["collection_links"] == 4
    ws = uuid.UUID(await personal_ws(c))
    async with get_sessionmaker()() as db:
        cols = (await db.execute(select(Collection).where(Collection.workspace_id == ws))).scalars().all()
        by_name = {col.name: col for col in cols}
        assert set(by_name) == {"Networking", "BGP", "Reading list"}
        assert by_name["Networking"].parent_id is None and by_name["Reading list"].parent_id is None
        assert by_name["BGP"].parent_id == by_name["Networking"].id
        counts = dict(
            (await db.execute(
                select(paper_collections.c.collection_id, func.count()).group_by(paper_collections.c.collection_id)
            )).all()
        )
        assert counts[by_name["BGP"].id] == 2 and counts[by_name["Networking"].id] == 1
    rows = {p["title"]: p for p in await papers(c)}
    assert sorted(rows["Deep learning"]["tags"]) == ["deep learning", "review"]
    d = (await c.get(f"{API}/papers/{rows['Deep learning']['id']}")).json()
    assert d["notes_md"] == "Great survey.\n\n- read intro"
    # Filtering by the nested collection finds its papers.
    in_bgp = await papers(c, collection_id=str(by_name["BGP"].id))
    assert {p["title"] for p in in_bgp} == {"Deep learning", "Adam: A Method for Stochastic Optimization"}


async def test_formats_ris_and_csl(login):
    _, c = await login("a@example.com")
    ris = await commit(c, "mendeley.ris")
    assert ris["format"] == "ris" and (ris["new"], ris["unreadable"]) == (3, 1)
    csl = await commit(c, "library.csl.json")
    assert csl["format"] == "csljson" and (csl["new"], csl["unreadable"]) == (2, 2)
    rows = {p["title"]: p for p in await papers(c)}
    assert rows["Adam: A Method for Stochastic Optimization"]["identifier"] == "1412.6980"
    assert rows["Mastering the game of Go with deep neural networks and tree search"]["citekey"] == "silver2016"
    assert sorted(rows["Deep learning"]["tags"]) == ["deep learning", "review"]


async def test_duplicates_within_a_file_and_citekey_collisions(login):
    _, c = await login("a@example.com")
    bib = (
        b"@article{dup, title={First}, doi={10.1000/a}, keywords={x}}\n"
        b"@article{dup, title={Second copy}, doi={10.1000/A}, keywords={y}, note={n2}}\n"
        b"@article{dup, title={Other paper}, doi={10.1000/b}}\n"
    )
    r = await post(c, "/preview", bib)
    assert (r.json()["new"], r.json()["matched"]) == (2, 1)
    assert [i["matched_by"] for i in r.json()["items"]] == [None, "file", None]
    r = await post(c, "", bib)
    assert (r.json()["new"], r.json()["matched"]) == (2, 1)
    rows = await papers(c)
    assert len(rows) == 2
    assert len({p["citekey"] for p in rows}) == 2 and "dup" in {p["citekey"] for p in rows}
    first = next(p for p in rows if p["title"] == "First")
    assert sorted(first["tags"]) == ["x", "y"]  # the repeated record contributed its tag
    assert (await c.get(f"{API}/papers/{first['id']}")).json()["notes_md"] == "n2"


async def test_existing_citekey_is_not_reused(login):
    _, c = await login("a@example.com")
    await commit(c, "library.bib")
    other = b"@article{he2016deep, title={A different paper}, year={2020}, doi={10.9999/z}}\n"
    r = await post(c, "", other)
    assert r.json()["new"] == 1
    keys = [p["citekey"] for p in await papers(c) if p["identifier"] == "10.9999/z"]
    assert keys and keys[0] != "he2016deep"


async def test_unreadable_items_do_not_stop_the_rest(login):
    _, c = await login("a@example.com")
    bib = b"@article{good, title={Good one}, doi={10.2000/ok}}\n@article{bad, title={Bad}, doi={10.2000/" + b"x" * 300 + b"}}\n"
    done = (await post(c, "", bib)).json()
    assert (done["new"], done["unreadable"]) == (1, 1)
    assert done["problems"][0]["reason"] == "identifier too long"
    assert [p["title"] for p in await papers(c)] == ["Good one"]


async def test_unknown_or_broken_file_is_422(login):
    _, c = await login("a@example.com")
    assert (await post(c, "/preview", b"just some text", "notes.txt")).status_code == 422
    assert (await post(c, "", b"[1, 2", "x.json")).status_code == 422
    assert (await post(c, "", b"   \n", "x.bib")).status_code == 422
    evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "boom">]><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
    assert (await post(c, "", evil, "x.rdf")).status_code == 422
    assert await papers(c) == []


async def test_size_limit(login):
    _, c = await login("a@example.com")
    entry = b"@article{k1, title={Big}, abstract={" + b"a" * (4 * 1024 * 1024) + b"}}\n"
    r = await post(c, "/preview", entry)
    assert r.status_code == 413
    just_under = b"@article{k1, title={Fits}, abstract={" + b"a" * (4 * 1024 * 1024 - 200) + b"}}\n"
    r = await post(c, "/preview", just_under)
    assert r.status_code == 200 and r.json()["new"] == 1  # a 4 MiB file passes the body limit (5 MiB with the envelope)
    huge = b"x" * (6 * 1024 * 1024)
    assert (await post(c, "/preview", huge)).status_code == 413  # rejected by the body-size middleware


async def test_permissions(login, pat_client):
    _, owner = await login("a@example.com")
    ws = await personal_ws(owner)
    _, other = await login("b@example.com")
    bib = fixture("library.bib")
    for path in ("/preview", ""):
        assert (await post(other, path, bib, workspace_id=ws)).status_code == 404  # not a member

    r = await owner.post(f"{API}/workspaces", json={"name": "Lab"})
    lab = r.json()["id"]
    _, viewer = await login("v@example.com")
    await join_workspace(owner, viewer, lab, "v@example.com", role="viewer")
    for path in ("/preview", ""):
        assert (await post(viewer, path, bib, workspace_id=lab)).status_code == 404  # viewers cannot import
    _, editor = await login("e@example.com")
    await join_workspace(owner, editor, lab, "e@example.com", role="editor")
    r = await post(editor, "", bib, workspace_id=lab)
    assert r.status_code == 200 and r.json()["new"] == 4
    assert await papers(owner, workspace_id=ws) == []  # nothing leaked into the personal workspace

    reader = await pat_client(owner, scopes=("read:papers",))
    assert (await post(reader, "", bib)).status_code == 403
    writer = await pat_client(owner, scopes=("write:papers",))
    assert (await post(writer, "/preview", bib)).status_code == 200
    anon = await pat_client(owner)
    anon.headers.pop("Authorization")
    assert (await post(anon, "", bib)).status_code in (401, 403)
    assert (await owner.post(f"{API}/imports", data={"workspace_id": ws})).status_code == 422  # no file


async def test_rate_limit(login, monkeypatch):
    from app.api.v1 import imports

    monkeypatch.setattr(imports, "COMMIT_RATE_LIMIT", 5)
    _, c = await login("a@example.com")
    codes = [(await post(c, "", b"@article{k, title={T}, doi={10.3000/t}}")).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
