"""Bibliography files are parsed in a killable, resource-limited child process."""

import sys
import time
from pathlib import Path

import pytest

from app.services.importers import ImportFormatError, parse_file
from app.services.importers import isolated
from app.services.importers.isolated import parse_file_isolated

FIX = Path(__file__).parent / "fixtures" / "import"


@pytest.mark.parametrize("name", ["library.bib", "mendeley.ris", "library.csl.json", "zotero.rdf"])
async def test_same_result_as_in_process(name):
    data = (FIX / name).read_bytes()
    assert await parse_file_isolated(data, name) == parse_file(data, name)


async def test_format_errors_come_back_as_import_format_error():
    with pytest.raises(ImportFormatError, match="No BibTeX entries"):
        await parse_file_isolated(b"@comment{nothing}\n", "x.bib")
    with pytest.raises(ImportFormatError, match="Unrecognized"):
        await parse_file_isolated(b"hello", "notes.txt")


async def test_slow_parse_is_killed(monkeypatch):
    # A child that never answers stands in for a parser stuck on a hostile file.
    async def spawn(*args, **kwargs):
        return await real(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)

    real = isolated.asyncio.create_subprocess_exec
    monkeypatch.setattr(isolated.asyncio, "create_subprocess_exec", spawn)
    t = time.monotonic()
    with pytest.raises(ImportFormatError, match="too long"):
        await parse_file_isolated(b"@article{k, title={T}}", "x.bib", timeout=1)
    assert time.monotonic() - t < 10


async def test_child_crash_is_an_import_format_error(monkeypatch):
    async def spawn(*args, **kwargs):
        return await real(sys.executable, "-c", "import sys; sys.exit(137)", **kwargs)

    real = isolated.asyncio.create_subprocess_exec
    monkeypatch.setattr(isolated.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(ImportFormatError, match="could not be read"):
        await parse_file_isolated(b"@article{k, title={T}}", "x.bib")


def test_only_the_extension_reaches_the_command_line():
    assert isolated._name_hint("My Library (2024).BIB") == "file.bib"
    assert isolated._name_hint("--evil\x00.rdf") == "file.rdf"
    assert isolated._name_hint("x.b;rm -rf") == "file"
    assert isolated._name_hint(None) == "file"


def test_child_environment_has_no_secrets(monkeypatch):
    import asyncio

    seen = {}

    async def spawn(*args, **kwargs):
        seen.update(kwargs["env"])
        return await real(*args, **kwargs)

    real = isolated.asyncio.create_subprocess_exec
    monkeypatch.setattr(isolated.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("JWT_SECRET", "must-not-leak")
    asyncio.run(parse_file_isolated(b"@article{k, title={T}}", "x.bib"))
    assert set(seen) == {"PATH", "PYTHONPATH", "LANG", "PYTHONDONTWRITEBYTECODE"}
