import os
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.redis import get_redis
from app.db.session import get_sessionmaker
from app.models import Paper, Workspace
from app.workers import tasks
from tests.helpers import FakePool, pdf_files
from tests.pdfgen import make_pdf

API = "/api/v1"
LINES = ["Scalable BGP Routing in Clos Fabrics", "arXiv:2405.12345v1 [cs.NI]"]
REL = "2024/2405.12345_scalable-bgp-routing-in-clos-fabrics.pdf"


def age(path: Path, seconds: float = 60) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t), follow_symlinks=False)


async def scan(storage, resolver, pool):
    from app.core.config import get_settings

    return await tasks.scan_watch_folder_core(
        sessionmaker=get_sessionmaker(),
        redis=get_redis(),
        watch_dir=storage.watch,
        library_root=storage.library,
        max_bytes=get_settings().max_upload_bytes,
        resolver=resolver,
        enqueue_pool=pool,
    )


async def slug_of(c) -> str:
    wid = next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")
    return (await c.get(f"{API}/workspaces/{wid}")).json()["slug"], wid


async def put(storage, slug, name, data, old=True) -> Path:
    d = storage.watch / slug
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(data)
    if old:
        age(p)
    return p


async def test_watch_ingests_after_two_stable_scans(login, resolver, storage):
    user, c = await login("a@example.com")
    slug, wid = await slug_of(c)
    pool = FakePool()
    src = await put(storage, slug, "Paper.PDF", make_pdf(LINES))

    first = await scan(storage, resolver, pool)  # first sighting: only records (size, mtime)
    assert first.ingested == [] and src.exists() and pool.jobs == []
    second = await scan(storage, resolver, pool)
    assert second.ingested == [str(src)] and not src.exists()

    rows = (await c.get(f"{API}/papers/light")).json()
    assert len(rows) == 1 and rows[0]["relative_file_path"] == REL and rows[0]["has_pdf"] is True
    assert (storage.library / REL).is_file()
    assert pool.jobs == [("process_paper", rows[0]["id"])]
    async with get_sessionmaker()() as db:
        paper = (await db.execute(select(Paper))).scalar_one()
        assert str(paper.workspace_id) == wid and paper.created_by == user.id
    assert not (storage.watch / "_rejected").exists()
    assert (await scan(storage, resolver, pool)).ingested == []


async def test_watch_unstable_file_skipped_then_processed(login, resolver, storage):
    _, c = await login("a@example.com")
    slug, _ = await slug_of(c)
    pool = FakePool()
    src = await put(storage, slug, "grow.pdf", make_pdf(LINES), old=False)  # fresh mtime

    assert (await scan(storage, resolver, pool)).ingested == []
    assert (await scan(storage, resolver, pool)).ingested == []  # unchanged but < 5s old
    assert src.exists()

    # still being written: size changes between scans
    age(src)
    assert (await scan(storage, resolver, pool)).ingested == []
    with open(src, "ab") as fh:
        fh.write(b"\n% more")
    age(src)
    res = await scan(storage, resolver, pool)
    assert res.ingested == [] and src.exists()  # changed vs previous sighting
    res = await scan(storage, resolver, pool)
    assert res.ingested == [str(src)] and not src.exists()


async def test_watch_unknown_slug_and_root_files_rejected(login, resolver, storage):
    _, c = await login("a@example.com")
    pool = FakePool()
    a = await put(storage, "no-such-workspace", "x.pdf", make_pdf(LINES))
    root = storage.watch / "stray.pdf"
    root.write_bytes(make_pdf(LINES, nonce="r"))
    age(root)
    await scan(storage, resolver, pool)
    res = await scan(storage, resolver, pool)
    assert sorted(res.rejected) == sorted([str(a), str(root)])
    rej = storage.watch / "_rejected"
    assert (rej / "no-such-workspace" / "x.pdf").is_file()
    assert "no-such-workspace" in (rej / "no-such-workspace" / "x.pdf.error.txt").read_text()
    assert (rej / "_root" / "stray.pdf").is_file()
    assert (rej / "_root" / "stray.pdf.error.txt").read_text().strip() != ""
    assert not a.exists() and not root.exists()
    assert pdf_files(storage.library) == [] and pool.jobs == []

    # same name again does not overwrite
    await put(storage, "no-such-workspace", "x.pdf", make_pdf(LINES, nonce="2"))
    await scan(storage, resolver, pool)
    await scan(storage, resolver, pool)
    assert (rej / "no-such-workspace" / "x_2.pdf").is_file()
    assert (rej / "no-such-workspace" / "x.pdf").is_file()


async def test_watch_invalid_and_duplicate_files_rejected(login, resolver, storage):
    _, c = await login("a@example.com")
    slug, _ = await slug_of(c)
    pool = FakePool()
    await put(storage, slug, "fake.pdf", b"<html>nope</html>")
    await put(storage, slug, "corrupt.pdf", b"%PDF-1.4\n" + b"junk" * 40)
    await put(storage, slug, "good.pdf", make_pdf(LINES))
    await scan(storage, resolver, pool)
    res = await scan(storage, resolver, pool)
    assert len(res.ingested) == 1 and len(res.rejected) == 2  # one failure never stops the scan
    rej = storage.watch / "_rejected" / slug
    assert "not a PDF" in (rej / "fake.pdf.error.txt").read_text()
    assert "Invalid PDF" in (rej / "corrupt.pdf.error.txt").read_text()

    # duplicate identifier -> rejected with reason
    await put(storage, slug, "dup.pdf", make_pdf(LINES, nonce="dup"))
    await scan(storage, resolver, pool)
    res = await scan(storage, resolver, pool)
    assert len(res.rejected) == 1
    assert "already has a PDF" in (rej / "dup.pdf.error.txt").read_text()
    assert len(pdf_files(storage.library)) == 1


async def test_watch_ignores_symlinks_dotfiles_and_nested(login, resolver, storage, tmp_path):
    _, c = await login("a@example.com")
    slug, _ = await slug_of(c)
    pool = FakePool()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(make_pdf(LINES))
    d = storage.watch / slug
    d.mkdir()
    (d / "link.pdf").symlink_to(outside)
    await put(storage, slug, ".hidden.pdf", make_pdf(LINES, nonce="h"))
    await put(storage, slug, "notes.txt", b"hello")
    (d / "sub").mkdir()
    await put(storage, slug, "sub/nested.pdf", make_pdf(LINES, nonce="n")) if False else None
    nested = d / "sub" / "nested.pdf"
    nested.write_bytes(make_pdf(LINES, nonce="n"))
    age(nested)
    (storage.watch / "linkdir").symlink_to(d, target_is_directory=True)
    (storage.watch / "root-link.pdf").symlink_to(outside)

    for _ in range(3):
        res = await scan(storage, resolver, pool)
        assert res.ingested == [] and res.rejected == []
    assert (d / "link.pdf").is_symlink() and outside.exists()
    assert (d / ".hidden.pdf").exists() and (d / "notes.txt").exists() and nested.exists()
    assert (storage.watch / "root-link.pdf").is_symlink()
    assert not (storage.watch / "_rejected").exists()
    assert pdf_files(storage.library) == []


async def test_watch_ignores_rejected_dir(login, resolver, storage):
    _, c = await login("a@example.com")
    pool = FakePool()
    (storage.watch / "_rejected" / "x").mkdir(parents=True)
    p = storage.watch / "_rejected" / "x" / "old.pdf"
    p.write_bytes(make_pdf(LINES))
    age(p)
    for _ in range(3):
        res = await scan(storage, resolver, pool)
        assert res.ingested == [] and res.rejected == []
    assert p.exists()


async def test_watch_lock_prevents_concurrent_processing(login, resolver, storage):
    import hashlib

    _, c = await login("a@example.com")
    slug, _ = await slug_of(c)
    pool = FakePool()
    src = await put(storage, slug, "locked.pdf", make_pdf(LINES))
    await scan(storage, resolver, pool)
    await get_redis().set(tasks.LOCK_PREFIX + hashlib.sha256(str(src).encode()).hexdigest(), "1", ex=60)
    res = await scan(storage, resolver, pool)
    assert res.ingested == [] and src.exists()


def test_cron_schedule_mapping():
    from app.workers.settings import WorkerSettings, watch_cron_schedule

    assert watch_cron_schedule(10) == {"second": {0, 10, 20, 30, 40, 50}}
    assert watch_cron_schedule(1)["second"] == set(range(60))
    assert watch_cron_schedule(120) == {"minute": set(range(0, 60, 2)), "second": {0}}
    assert watch_cron_schedule(3600) == {"minute": {0}, "second": {0}}
    names = {j.name for j in WorkerSettings.cron_jobs}
    assert any("scan_watch_folder" in n for n in names)
    assert {f.__name__ for f in WorkerSettings.functions} >= {"process_paper"}
