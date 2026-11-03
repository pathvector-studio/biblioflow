"""Untrusted PDFs are parsed in a killable, resource-limited child process."""

import resource
import subprocess
import sys
import time

import pytest

from app.services import pdf
from app.services.pdf import InvalidPdf, inspect_pdf_isolated
from tests.pdfgen import make_pdf


async def test_parses_in_child(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(make_pdf(["Hello", "doi:10.1145/3651890"], title="A Real Title", pages=3))
    info = await inspect_pdf_isolated(f)
    assert info.page_count == 3
    assert info.title == "A Real Title"
    assert info.doi == "10.1145/3651890"


async def test_garbage_is_invalid(tmp_path):
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"%PDF-1.7\nnot really a pdf")
    with pytest.raises(InvalidPdf):
        await inspect_pdf_isolated(f)


async def test_hung_parser_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "_probe_command", lambda path: [sys.executable, "-c", "import time; time.sleep(30)"])
    start = time.monotonic()
    with pytest.raises(InvalidPdf, match="too long"):
        await inspect_pdf_isolated(tmp_path / "x.pdf", timeout=0.5)
    assert time.monotonic() - start < 5


async def test_crashing_child_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "_probe_command", lambda path: [sys.executable, "-c", "import os; os._exit(9)"])
    with pytest.raises(InvalidPdf):
        await inspect_pdf_isolated(tmp_path / "x.pdf")


async def test_child_gets_no_secrets(tmp_path, monkeypatch):
    script = "import json, os; print(json.dumps({'error': ','.join(sorted(os.environ))}))"
    monkeypatch.setattr(pdf, "_probe_command", lambda path: [sys.executable, "-c", script])
    with pytest.raises(InvalidPdf) as exc:
        await inspect_pdf_isolated(tmp_path / "x.pdf")
    names = str(exc.value).split(",")
    assert not {"JWT_SECRET", "DATABASE_URL", "REDIS_URL", "LLM_API_KEY"} & set(names)


def test_child_limits_apply_only_to_child():
    code = (
        "from app.services.pdf import _limit_self; _limit_self(); import resource; "
        "print(resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_CPU)[0])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert int(out[0]) == pdf.PARSE_MEMORY_BYTES
    assert int(out[1]) == pdf.PARSE_CPU_SECONDS
    assert resource.getrlimit(resource.RLIMIT_AS)[0] != pdf.PARSE_MEMORY_BYTES  # the parent is untouched
