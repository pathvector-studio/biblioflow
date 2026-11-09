"""Import from the server folder: confinement of paths, the job, the API and its permissions."""

import asyncio
import os
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import Paper
from app.models.import_job import ImportJob
from app.services.import_folder import UnsafePath, list_candidates, resolve_in_folder
from app.services.import_job import run_import_job
from tests.helpers import FakePool, join_workspace, pdf_files
from tests.pdfgen import make_pdf

API = "/api/v1"
FIX = Path(__file__).parent / "fixtures" / "import" / "folder"
RDF = "My Library/My Library.rdf"


@pytest.fixture(autouse=True)
def _relaxed_rate_limit(monkeypatch):
    from app.api.v1 import import_jobs

    monkeypatch.setattr(import_jobs, "START_RATE_LIMIT", 1000)


# ------------------------------------------------------------------ helpers
async def personal(c) -> tuple[str, str]:
    wid = next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")
    return wid, (await c.get(f"{API}/workspaces/{wid}")).json()["slug"]


def write_pdf(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_pdf([label], title=label, nonce=label))


def zotero_export(storage, slug: str) -> Path:
    """A Zotero "Export Files" folder: ``My Library/My Library.rdf`` + ``My Library/files/<id>/<name>``."""
    root = storage.imports / slug / "My Library"
    root.mkdir(parents=True)
    shutil.copy(FIX / "My Library.rdf", root / "My Library.rdf")
    write_pdf(root / "files/2/LeCun_2015.pdf", "Deep learning full text")
    write_pdf(root / "files/5/Kingma_2014.pdf", "Adam full text")
    (root / "files/3").mkdir(parents=True)
    (root / "files/3/snapshot.html").write_text("<html></html>")
    return root


async def start(c, wid, source=RDF):
    return await c.post(f"{API}/workspaces/{wid}/import-jobs", json={"source": source})


async def run_job(job_id, resolver, storage, pool=None):
    from app.core.config import get_settings

    pool = pool or FakePool()
    status = await run_import_job(
        uuid.UUID(job_id), sessionmaker=get_sessionmaker(), import_dir=storage.imports, library_root=storage.library,
        max_bytes=get_settings().max_upload_bytes, resolver=resolver, enqueue_pool=pool,
    )
    return status, pool


async def import_once(c, wid, resolver, storage, source=RDF, pool=None):
    r = await start(c, wid, source)
    assert r.status_code == 202, r.text
    await run_job(r.json()["id"], resolver, storage, pool)
    got = await c.get(f"{API}/workspaces/{wid}/import-jobs/{r.json()['id']}")
    assert got.status_code == 200
    return got.json()


async def papers(c, wid):
    r = await c.get(f"{API}/papers/light", params={"workspace_id": wid, "limit": 200})
    assert r.status_code == 200, r.text
    return {p["title"]: p for p in r.json()}


# ------------------------------------------------------------------ path confinement
@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "import" / "lab"
    (root / "files/1").mkdir(parents=True)
    (root / "files/1/a.pdf").write_bytes(b"%PDF-1.4")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"%PDF-1.4")
    os.symlink(outside / "secret.pdf", root / "link.pdf")
    os.symlink(outside, root / "linkdir")
    return root


@pytest.mark.parametrize(
    "raw",
    [
        "../../etc/passwd", "files/../../x.pdf", "/etc/passwd", "file:///etc/passwd", "file://localhost/etc/passwd",
        "C:\\Users\\me\\a.pdf", "files\\..\\..\\x.pdf", "link.pdf", "linkdir/secret.pdf", "files", "missing.pdf",
        "", "a\x00.pdf", "file:///a/../files/1/a.pdf",
    ],
)
def test_resolve_in_folder_rejects(folder, raw):
    with pytest.raises(UnsafePath):
        resolve_in_folder(folder, raw)


def test_resolve_in_folder_percent_encoded_dots_in_file_uri(folder):
    with pytest.raises(UnsafePath):
        resolve_in_folder(folder, "file://files/%2e%2e/%2e%2e/x.pdf")


def test_resolve_in_folder_accepts(folder):
    root = str(folder)
    for raw in (
        "files/1/a.pdf", "./files/1/a.pdf", "files//1/a.pdf", "files\\1\\a.pdf", "file://files/1/a.pdf",
        "file:files/1/a.pdf", f"{root}/files/1/a.pdf", f"file://{root}/files/1/a.pdf", f"FILE://{root}/files/1/a.pdf",
        f"file://localhost{root}/files/1/a.pdf", "file://files/1/%61.pdf",
    ):
        path, rel = resolve_in_folder(folder, raw)
        assert rel == "files/1/a.pdf" and path.read_bytes().startswith(b"%PDF"), raw


def test_resolve_relative_to_base(folder):
    (folder / "sub/files").mkdir(parents=True)
    (folder / "sub/files/b.pdf").write_bytes(b"%PDF-1.4")
    assert resolve_in_folder(folder, "files/b.pdf", base="sub")[1] == "sub/files/b.pdf"
    with pytest.raises(UnsafePath):
        resolve_in_folder(folder, "files/1/a.pdf", base="sub")


def test_list_candidates_skips_symlinks_hidden_and_other_extensions(folder):
    (folder / "x.bib").write_text("@misc{a,title={t}}")
    (folder / "My Library").mkdir()
    (folder / "My Library/lib.RDF").write_text("<rdf/>")
    (folder / ".hidden.bib").write_text("x")
    (folder / "notes.txt").write_text("x")
    os.symlink(folder / "x.bib", folder / "alias.bib")
    os.symlink(folder.parents[1] / "outside", folder / "linked.bib")
    (folder.parents[1] / "outside/leak.bib").write_text("x")
    names = {c.name: c.format for c in list_candidates(folder)}
    assert names == {"My Library/lib.RDF": "zotero_rdf", "x.bib": "bibtex"}
    assert list_candidates(folder / "nope") == []


# ------------------------------------------------------------------ the job
async def test_zotero_export_with_pdfs(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    zotero_export(storage, slug)

    listing = (await c.get(f"{API}/workspaces/{wid}/import-folder")).json()
    assert listing["slug"] == slug and listing["exists"] is True
    assert [(f["name"], f["format"]) for f in listing["files"]] == [(RDF, "zotero_rdf")]

    pool = FakePool()
    job = await import_once(c, wid, resolver, storage, pool=pool)
    assert job["status"] == "done" and job["error"] is None and job["source"] == RDF
    assert (job["total"], job["new"], job["matched"], job["unreadable"]) == (4, 3, 0, 1)
    assert (job["with_attachments"], job["pdfs_processed"], job["pdfs_attached"], job["pdfs_failed"]) == (2, 2, 2, 0)
    assert [(p["stage"], p["ref"]) for p in job["problems"]] == [("record", "entry 6")] or job["problems"][0]["stage"] == "record"
    assert job["started_at"] and job["finished_at"]

    rows = await papers(c, wid)
    dl, adam, book = rows["Deep learning"], rows["Adam: A Method for Stochastic Optimization"], rows["A Book Without Identifiers"]
    assert (dl["has_pdf"], adam["has_pdf"], book["has_pdf"]) == (True, True, False)
    assert dl["status"] == "unread" and book["status"] == "wishlist"
    assert len(pdf_files(storage.library)) == 2
    for p in (dl, adam):
        assert (storage.library / p["relative_file_path"]).read_bytes().startswith(b"%PDF")
    assert {j[0] for j in pool.jobs} == {"process_paper"} and len(pool.jobs) == 2
    # Metadata, tags, collections and the note came along.
    assert sorted(dl["tags"]) == ["deep learning", "review"]
    detail = (await c.get(f"{API}/papers/{dl['id']}")).json()
    assert detail["notes_md"] == "Great survey.\n\n- read intro" and len(detail["collection_ids"]) == 2
    cols = {x["name"]: x for x in (await c.get(f"{API}/collections", params={"workspace_id": wid})).json()}
    assert set(cols) == {"Networking", "BGP", "Reading list"}
    assert cols["BGP"]["parent_id"] == cols["Networking"]["id"]


async def test_second_run_is_idempotent_and_never_replaces_a_pdf(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = zotero_export(storage, slug)
    first = await import_once(c, wid, resolver, storage)
    before = await papers(c, wid)
    shas = {}
    async with get_sessionmaker()() as db:
        for pid, sha in (await db.execute(select(Paper.id, Paper.file_sha256).where(Paper.has_pdf))).all():
            shas[pid] = sha

    # A different file under the same path must not replace what is stored.
    write_pdf(root / "files/2/LeCun_2015.pdf", "A different file")
    pool = FakePool()
    second = await import_once(c, wid, resolver, storage, pool=pool)
    assert second["id"] != first["id"] and second["status"] == "done"
    assert (second["total"], second["new"], second["matched"], second["unreadable"]) == (4, 0, 3, 1)
    assert (second["pdfs_attached"], second["pdfs_failed"]) == (0, 0)
    after = await papers(c, wid)
    assert {t: p["id"] for t, p in after.items()} == {t: p["id"] for t, p in before.items()}
    assert len(pdf_files(storage.library)) == 2 and pool.jobs == []
    async with get_sessionmaker()() as db:
        now = dict((await db.execute(select(Paper.id, Paper.file_sha256).where(Paper.has_pdf))).all())
    assert now == shas
    # Tags / notes were not duplicated either.
    detail = (await c.get(f"{API}/papers/{after['Deep learning']['id']}")).json()
    assert detail["notes_md"] == "Great survey.\n\n- read intro" and sorted(detail["tags"]) == ["deep learning", "review"]


async def test_wishlist_paper_gets_the_pdf(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    zotero_export(storage, slug)
    # Metadata only first (the upload route), as a wishlist.
    r = await c.post(
        f"{API}/imports", data={"workspace_id": wid},
        files={"file": ("lib.rdf", (FIX / "My Library.rdf").read_bytes(), "application/xml")},
    )
    assert r.status_code == 200 and r.json()["new"] == 3, r.text
    before = await papers(c, wid)
    assert {p["has_pdf"] for p in before.values()} == {False}

    job = await import_once(c, wid, resolver, storage)
    assert (job["new"], job["matched"], job["pdfs_attached"]) == (0, 3, 2)
    after = await papers(c, wid)
    assert after["Deep learning"]["id"] == before["Deep learning"]["id"]
    assert (after["Deep learning"]["has_pdf"], after["Deep learning"]["status"]) == (True, "unread")
    assert after["A Book Without Identifiers"]["has_pdf"] is False


async def test_bibtex_with_unsafe_and_safe_paths(login, resolver, storage, enqueued, tmp_path):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    write_pdf(root / "files/1/good.pdf", "good one")
    write_pdf(root / "files/2/abs.pdf", "absolute inside")
    write_pdf(root / "files/3/uri.pdf", "uri one")
    outside = tmp_path / "outside"
    write_pdf(outside / "evil.pdf", "outside")
    os.symlink(outside / "evil.pdf", root / "link.pdf")
    os.symlink(outside, root / "linkdir")
    (root / "files/4").mkdir(parents=True)
    (root / "files/4/fake.pdf").write_text("not a pdf at all")
    bib = f"""
@article{{ok2020, title={{Good paper one}}, year={{2020}}, doi={{10.1000/good}}, file={{:files/1/good.pdf:application/pdf}}}}
@article{{trav2020, title={{Traversal paper}}, year={{2020}}, doi={{10.1000/trav}}, keywords={{kept}}, file={{:../../etc/passwd:application/pdf}}}}
@article{{abs2020, title={{Absolute outside}}, year={{2020}}, doi={{10.1000/abs}}, file={{:/etc/hosts:application/pdf}}}}
@article{{absin2020, title={{Absolute inside}}, year={{2020}}, doi={{10.1000/absin}}, file={{:{root}/files/2/abs.pdf:application/pdf}}}}
@article{{link2020, title={{Symlink paper}}, year={{2020}}, doi={{10.1000/link}}, file={{:link.pdf:application/pdf;:linkdir/evil.pdf:application/pdf}}}}
@article{{uri2020, title={{File URI paper}}, year={{2020}}, doi={{10.1000/uri}}, file={{:file://files/3/uri.pdf:application/pdf}}}}
@article{{uriout2020, title={{File URI outside}}, year={{2020}}, doi={{10.1000/uriout}}, file={{:file:///etc/passwd:application/pdf}}}}
@article{{fake2020, title={{Fake pdf}}, year={{2020}}, doi={{10.1000/fake}}, file={{:files/4/fake.pdf:application/pdf}}}}
@article{{none2020, title={{No file}}, year={{2020}}, doi={{10.1000/none}}}}
""".replace("\\", "/")
    (root / "refs.bib").write_text(bib)

    job = await import_once(c, wid, resolver, storage, source="refs.bib")
    assert job["status"] == "done"
    assert (job["total"], job["new"], job["unreadable"]) == (9, 9, 0)
    assert job["with_attachments"] == 8 and job["pdfs_processed"] == 8
    assert (job["pdfs_attached"], job["pdfs_failed"]) == (3, 5)
    failed = {p["ref"]: p["reason"] for p in job["problems"] if p["stage"] == "pdf"}
    assert set(failed) == {"trav2020", "abs2020", "link2020", "uriout2020", "fake2020"}
    assert "'..'" in failed["trav2020"] and "outside the import folder" in failed["abs2020"]
    assert "symbolic" in failed["link2020"] and "outside the import folder" in failed["uriout2020"]
    assert "not a PDF" in failed["fake2020"]
    assert all("etc/passwd" in failed[k] or "etc/hosts" in failed[k] for k in ("trav2020", "abs2020", "uriout2020"))

    rows = await papers(c, wid)
    have = {t for t, p in rows.items() if p["has_pdf"]}
    assert have == {"Good paper one", "Absolute inside", "File URI paper"}
    assert sorted(rows["Traversal paper"]["tags"]) == ["kept"]  # metadata still imported
    assert len(pdf_files(storage.library)) == 3


async def test_first_unusable_attachment_falls_through_to_the_next(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    write_pdf(root / "files/9/b.pdf", "second one")
    (root / "refs.bib").write_text(
        "@article{x2020, title={Two files}, year={2020}, doi={10.1000/x}, "
        "file={:../../etc/passwd:application/pdf;:files/9/b.pdf:application/pdf}}"
    )
    job = await import_once(c, wid, resolver, storage, source="refs.bib")
    assert (job["pdfs_attached"], job["pdfs_failed"], job["problems"]) == (1, 0, [])


async def test_ris_with_file_fields(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    write_pdf(root / "pdfs/r.pdf", "ris pdf")
    (root / "refs.ris").write_text(
        "TY  - JOUR\nTI  - A RIS paper\nPY  - 2021\nDO  - 10.1000/ris\nL1  - pdfs/r.pdf\nER  - \n"
    )
    job = await import_once(c, wid, resolver, storage, source="refs.ris")
    assert (job["new"], job["pdfs_attached"], job["pdfs_failed"]) == (1, 1, 0)


async def test_unreadable_source_fails_the_job(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    root.mkdir()
    (root / "empty.bib").write_text("this is not bibtex")
    job = await import_once(c, wid, resolver, storage, source="empty.bib")
    assert job["status"] == "failed" and job["error"] and job["finished_at"]


# ------------------------------------------------------------------ API
async def test_source_must_be_a_safe_listed_style_name(login, storage, enqueued, tmp_path):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    root.mkdir()
    (root / "ok.bib").write_text("@misc{a, title={A title}}")
    (tmp_path / "outside.bib").write_text("@misc{a, title={A title}}")
    os.symlink(tmp_path / "outside.bib", root / "alias.bib")
    (root / "notes.txt").write_text("x")
    for bad in ("../outside.bib", "/etc/passwd", str(tmp_path / "outside.bib"), "alias.bib", "notes.txt", "missing.bib", "..\\x.bib", "ok.bib/../ok.bib", "files"):
        r = await start(c, wid, bad)
        assert r.status_code == 422, (bad, r.text)
    assert (await c.get(f"{API}/workspaces/{wid}/import-jobs")).json() == []
    assert (await start(c, wid, "ok.bib")).status_code == 202


async def test_one_job_at_a_time(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    root.mkdir()
    (root / "ok.bib").write_text("@misc{a, title={A title}, doi={10.1000/a}}")
    a, b = await asyncio.gather(start(c, wid, "ok.bib"), start(c, wid, "ok.bib"))
    assert sorted([a.status_code, b.status_code]) == [202, 409]
    ok = a if a.status_code == 202 else b
    assert (await start(c, wid, "ok.bib")).status_code == 409
    assert [j[0] for j in enqueued.jobs] == ["run_import_job"] and len(enqueued.jobs) == 1
    status, _ = await run_job(ok.json()["id"], resolver, storage)
    assert status == "done"
    assert (await run_job(ok.json()["id"], resolver, storage))[0] == "skipped"  # already finished: not run twice
    again = await start(c, wid, "ok.bib")
    assert again.status_code == 202
    assert len((await c.get(f"{API}/workspaces/{wid}/import-jobs")).json()) == 2


async def test_dead_job_does_not_block_forever(login, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    root.mkdir()
    (root / "ok.bib").write_text("@misc{a, title={A title}}")
    first = (await start(c, wid, "ok.bib")).json()
    assert (await start(c, wid, "ok.bib")).status_code == 409
    from sqlalchemy import text

    async with get_sessionmaker()() as db:
        await db.execute(text("UPDATE import_jobs SET updated_at = now() - interval '2 hours'"))
        await db.commit()
    assert (await start(c, wid, "ok.bib")).status_code == 202
    old = (await c.get(f"{API}/workspaces/{wid}/import-jobs/{first['id']}")).json()
    assert old["status"] == "failed" and old["error"]


async def test_queue_failure_marks_the_job_failed(login, storage, monkeypatch):
    from app.core import queue

    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    root.mkdir()
    (root / "ok.bib").write_text("@misc{a, title={A title}}")

    async def boom():
        raise ConnectionError("down")

    monkeypatch.setattr(queue, "get_arq_pool", boom)
    assert (await start(c, wid, "ok.bib")).status_code == 503
    (job,) = (await c.get(f"{API}/workspaces/{wid}/import-jobs")).json()
    assert job["status"] == "failed"
    assert (await start(c, wid, "ok.bib")).status_code == 503  # not blocked by the failed one


async def test_permissions(login, storage, enqueued, make_client):
    owner_user, owner = await login("owner@example.com")
    _, editor = await login("editor@example.com")
    _, viewer = await login("viewer@example.com")
    _, stranger = await login("stranger@example.com")
    r = await owner.post(f"{API}/workspaces", json={"name": "Lab"})
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    slug = r.json()["slug"]
    await join_workspace(owner, editor, wid, "editor@example.com", "editor")
    await join_workspace(owner, viewer, wid, "viewer@example.com", "viewer")
    root = storage.imports / slug
    root.mkdir()
    (root / "ok.bib").write_text("@misc{a, title={A title}}")

    for c in (stranger, viewer):  # no access: 404 (never confirm the workspace exists); viewers cannot start or list
        assert (await start(c, wid, "ok.bib")).status_code == 404
        assert (await c.get(f"{API}/workspaces/{wid}/import-folder")).status_code == 404
    assert (await stranger.get(f"{API}/workspaces/{wid}/import-jobs")).status_code == 404
    assert (await stranger.get(f"{API}/workspaces/{uuid.uuid4()}/import-jobs")).status_code == 404

    started = await start(editor, wid, "ok.bib")
    assert started.status_code == 202
    jid = started.json()["id"]
    assert (await viewer.get(f"{API}/workspaces/{wid}/import-jobs")).status_code == 200
    assert (await viewer.get(f"{API}/workspaces/{wid}/import-jobs/{jid}")).status_code == 200
    assert (await stranger.get(f"{API}/workspaces/{wid}/import-jobs/{jid}")).status_code == 404
    # A job id from another workspace is not readable through this one.
    other_wid, _ = await personal(stranger)
    assert (await stranger.get(f"{API}/workspaces/{other_wid}/import-jobs/{jid}")).status_code == 404


async def test_pat_scopes(login, pat_client, storage, enqueued):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    root = storage.imports / slug
    root.mkdir()
    (root / "ok.bib").write_text("@misc{a, title={A title}}")
    reader = await pat_client(c, ("read:papers",))
    writer = await pat_client(c, ("write:papers",))
    full = await pat_client(c, ("read:papers", "write:papers"))
    assert (await reader.get(f"{API}/workspaces/{wid}/import-jobs")).status_code == 200
    assert (await reader.get(f"{API}/workspaces/{wid}/import-folder")).status_code == 200
    assert (await reader.post(f"{API}/workspaces/{wid}/import-jobs", json={"source": "ok.bib"})).status_code == 403
    assert (await writer.get(f"{API}/workspaces/{wid}/import-jobs")).status_code == 403
    assert (await writer.post(f"{API}/workspaces/{wid}/import-jobs", json={"source": "ok.bib"})).status_code == 202
    assert (await full.post(f"{API}/workspaces/{wid}/import-jobs", json={"source": "ok.bib"})).status_code == 409


async def test_rate_limit(login, storage, enqueued, monkeypatch):
    from app.api.v1 import import_jobs

    monkeypatch.setattr(import_jobs, "START_RATE_LIMIT", 2)
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    codes = [(await start(c, wid, "missing.bib")).status_code for _ in range(3)]
    assert codes == [422, 422, 429]


async def test_empty_folder_listing(login, storage):
    _, c = await login("a@example.com")
    wid, slug = await personal(c)
    assert (await c.get(f"{API}/workspaces/{wid}/import-folder")).json() == {"slug": slug, "exists": False, "files": []}
