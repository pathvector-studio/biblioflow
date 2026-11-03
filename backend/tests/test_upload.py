import uuid

import pytest
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import Paper
from tests.helpers import join_workspace, make_meta, pdf_files
from tests.pdfgen import make_pdf

API = "/api/v1"
ARXIV_LINES = ["Scalable BGP Routing in Clos Fabrics", "arXiv:2405.12345v1 [cs.NI] 20 May 2024"]
REL = "2024/2405.12345_scalable-bgp-routing-in-clos-fabrics.pdf"


async def upload(c, data, name="paper.pdf", ctype="application/pdf", **form):
    return await c.post(f"{API}/papers/upload", files={"file": (name, data, ctype)}, data=form)


def incoming_empty(root):
    d = root / ".incoming"
    return not d.exists() or not list(d.iterdir())


async def test_upload_success(login, resolver, storage, enqueued):
    user, c = await login("a@example.com")
    data = make_pdf(ARXIV_LINES, pages=2)
    r = await upload(c, data)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["identifier"] == "2405.12345" and p["identifier_type"] == "arxiv"
    assert p["has_pdf"] is True and p["relative_file_path"] == REL
    assert p["page_count"] == 2 and p["file_size"] == len(data) and p["index_status"] == "pending"
    assert p["status"] == "unread" and p["citekey"] == "smith2024scalable"
    assert (storage.library / REL).read_bytes() == data
    assert pdf_files(storage.library) == [storage.library / REL]
    assert incoming_empty(storage.library)
    assert enqueued.jobs == [("process_paper", p["id"])]
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(Paper).where(Paper.id == uuid.UUID(p["id"])))).scalar_one()
        import hashlib

        assert row.file_sha256 == hashlib.sha256(data).hexdigest() and row.created_by == user.id


async def test_upload_without_identifiers_uses_internal_id_and_pdf_title(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["no identifiers here"], title="A Study of Things"), name="my file.pdf")
    p = r.json()
    assert r.status_code == 201
    assert p["identifier_type"] == "internal" and p["identifier"].startswith("BF-")
    assert p["title"] == "A Study of Things"
    assert p["relative_file_path"] == f"Unknown/{p['identifier']}_a-study-of-things.pdf"
    # junk title -> filename stem
    r = await upload(c, make_pdf(["other"], title="Microsoft Word - draft.docx", nonce="n"), name="my_notes.pdf")
    assert r.json()["title"] == "my notes"


async def test_upload_resolver_failure_does_not_block(login, resolver, storage, enqueued):
    from app.schemas.metadata import UpstreamError

    _, c = await login("a@example.com")
    resolver.error = UpstreamError("down")
    r = await upload(c, make_pdf(ARXIV_LINES))
    assert r.status_code == 201 and r.json()["identifier_type"] == "internal"


async def test_upload_explicit_identifier(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["nothing"]), identifier="https://arxiv.org/abs/2405.12345")
    assert r.status_code == 201 and r.json()["identifier"] == "2405.12345"
    r = await upload(c, make_pdf(["x"], nonce="2"), identifier="some free text title")
    assert r.status_code == 422


async def test_upload_attaches_to_wishlist(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    w = (await c.post(f"{API}/papers/metadata-only", json={"identifier": "2405.12345", "tags": ["t"]})).json()
    r = await upload(c, make_pdf(ARXIV_LINES))
    assert r.status_code == 201
    p = r.json()
    assert p["id"] == w["id"] and p["has_pdf"] is True and p["status"] == "unread"
    assert p["tags"] == ["t"] and p["citekey"] == w["citekey"]
    assert (await c.get(f"{API}/papers/light")).headers["X-Total-Count"] == "1"

    # explicit paper_id + explicit status
    w2 = (await c.post(f"{API}/papers/metadata-only", json={"title": "Manual one"})).json()
    r = await upload(c, make_pdf(["whatever"], nonce="m"), paper_id=w2["id"], status="reading")
    assert r.status_code == 201 and r.json()["id"] == w2["id"] and r.json()["status"] == "reading"
    assert r.json()["title"] == "Manual one"
    assert r.json()["relative_file_path"].startswith("Unknown/BF-") and "manual-one" in r.json()["relative_file_path"]
    # paper already has a pdf
    r = await upload(c, make_pdf(["again"], nonce="z"), paper_id=w2["id"])
    assert r.status_code == 409 and r.json()["paper_id"] == w2["id"]


async def test_upload_paper_id_other_workspace_404(login, resolver, storage, enqueued):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    w = (await a.post(f"{API}/papers/metadata-only", json={"title": "A's"})).json()
    r = await upload(b, make_pdf(["x"]), paper_id=w["id"])
    assert r.status_code == 404
    assert pdf_files(storage.library) == [] and incoming_empty(storage.library)
    assert (await upload(b, make_pdf(["x"]), paper_id=str(uuid.uuid4()))).status_code == 404


async def test_second_upload_same_identifier_409(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    assert (await upload(c, make_pdf(ARXIV_LINES))).status_code == 201
    first = (await c.get(f"{API}/papers/light")).json()[0]
    r = await upload(c, make_pdf(ARXIV_LINES, nonce="other"))
    assert r.status_code == 409
    assert r.json() == {"detail": "Paper already has a PDF", "paper_id": first["id"]}
    assert len(pdf_files(storage.library)) == 1 and incoming_empty(storage.library)
    assert len(enqueued.jobs) == 1


async def test_shared_file_across_workspaces(login, resolver, storage, enqueued):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    data = make_pdf(ARXIV_LINES)
    ra = await upload(a, data)
    rb = await upload(b, data)
    assert ra.status_code == rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]
    assert ra.json()["relative_file_path"] == rb.json()["relative_file_path"] == REL
    assert len(pdf_files(storage.library)) == 1 and incoming_empty(storage.library)

    # different bytes, same identifier in a third workspace -> _2
    _, c = await login("c@example.com")
    rc = await upload(c, make_pdf(ARXIV_LINES, nonce="different"))
    assert rc.status_code == 201
    assert rc.json()["relative_file_path"] == REL.replace(".pdf", "_2.pdf")
    _, d = await login("d@example.com")
    rd = await upload(d, make_pdf(ARXIV_LINES, nonce="yet another"))
    assert rd.json()["relative_file_path"] == REL.replace(".pdf", "_3.pdf")
    assert len(pdf_files(storage.library)) == 3


async def test_upload_rejections(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    # fake extension: .pdf name + pdf content-type, non-PDF content
    assert (await upload(c, b"<html>not a pdf</html>")).status_code == 415
    assert (await upload(c, b"MZ\x90\x00" + b"x" * 100)).status_code == 415
    # wrong content type / extension
    good = make_pdf(["x"])
    assert (await upload(c, good, ctype="text/plain")).status_code == 415
    assert (await upload(c, good, name="paper.txt")).status_code == 415
    assert (await upload(c, good, name="paper.pdf.exe")).status_code == 415
    # corrupted PDF: right magic, unreadable body
    assert (await upload(c, b"%PDF-1.4\n" + b"garbage" * 50)).status_code == 422
    assert (await upload(c, good[: len(good) // 2])).status_code == 422
    # missing file field
    assert (await c.post(f"{API}/papers/upload", data={"status": "read"})).status_code == 422
    assert pdf_files(storage.library) == [] and incoming_empty(storage.library)
    assert enqueued.jobs == []


async def test_upload_too_large(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    big = make_pdf(["big"]) + b"\n%" + b"0" * (2 * 1024 * 1024)
    assert (await upload(c, big)).status_code == 413
    assert pdf_files(storage.library) == [] and incoming_empty(storage.library)


async def test_receive_stream_limit_and_cleanup(storage):
    from app.services.storage.files import TooLarge, discard, receive_stream

    async def gen(n, size=1000):
        for _ in range(n):
            yield b"%PDF-" + b"a" * (size - 5)

    with pytest.raises(TooLarge):
        await receive_stream(gen(5), storage.library, 3000)
    assert incoming_empty(storage.library)
    st = await receive_stream(gen(2), storage.library, 3000)
    assert st.size == 2000 and st.head.startswith(b"%PDF-") and len(st.head) == 1024
    assert oct(st.path.stat().st_mode & 0o777) == "0o640"
    discard(st)
    assert incoming_empty(storage.library)


async def test_upload_permissions(login, pat_client, resolver, storage, enqueued):
    _, owner = await login("a@example.com")
    _, viewer = await login("v@example.com")
    ws = (await owner.post(f"{API}/workspaces", json={"name": "Lab"})).json()
    await join_workspace(owner, viewer, ws["id"], "v@example.com", "viewer")
    r = await upload(viewer, make_pdf(ARXIV_LINES), workspace_id=ws["id"])
    assert r.status_code == 404
    _, outsider = await login("o@example.com")
    assert (await upload(outsider, make_pdf(ARXIV_LINES), workspace_id=ws["id"])).status_code == 404
    assert (await upload(owner, make_pdf(ARXIV_LINES), workspace_id=ws["id"])).status_code == 201

    ro = await pat_client(owner, ("read:papers",))
    assert (await upload(ro, make_pdf(["pat"], nonce="p"))).status_code == 403
    rw = await pat_client(owner, ("write:papers",))
    assert (await upload(rw, make_pdf(["pat"], nonce="p"))).status_code == 201
    assert (await upload(rw, make_pdf(["x"]), ctype="application/pdf", name="a.pdf")).status_code in (201, 409)


async def test_enqueue_failure_does_not_fail_request(login, resolver, storage, monkeypatch):
    from app.core import queue

    async def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(queue, "get_arq_pool", boom)
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(ARXIV_LINES))
    assert r.status_code == 201 and r.json()["index_status"] == "pending"


async def test_db_failure_keeps_placed_file_discards_staged(login, resolver, storage, enqueued, monkeypatch, caplog):
    from sqlalchemy.ext.asyncio import AsyncSession

    _, c = await login("a@example.com")
    orig = AsyncSession.commit
    state = {"n": 0}

    async def failing_commit(self):
        raise RuntimeError("db down")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await upload(c, make_pdf(ARXIV_LINES))
    monkeypatch.setattr(AsyncSession, "commit", orig)
    # the placed file may be shared with a concurrent request (same-sha reuse): never deleted, only the temp is
    assert pdf_files(storage.library) == [storage.library / REL] and incoming_empty(storage.library)
    assert any("orphaned file left in library" in r.message and REL in r.message for r in caplog.records)
    assert enqueued.jobs == []


async def test_template_cannot_escape_library(login, resolver, storage, enqueued, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "filename_template", "{year}/papers/{identifier}.pdf")
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(ARXIV_LINES))
    assert r.json()["relative_file_path"] == "2024/papers/2405.12345.pdf"
    assert (storage.library / "2024/papers/2405.12345.pdf").is_file()


async def test_symlinked_target_is_not_followed(login, resolver, storage, enqueued, tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"precious")
    (storage.library / "2024").mkdir()
    (storage.library / "2024" / "2405.12345_scalable-bgp-routing-in-clos-fabrics.pdf").symlink_to(outside)
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(ARXIV_LINES))
    assert r.status_code in (201, 500)
    assert outside.read_bytes() == b"precious"
    assert incoming_empty(storage.library)
