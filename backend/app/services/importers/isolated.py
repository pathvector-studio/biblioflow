"""Run ``parse_file`` in a resource-limited child process.

A bibliography file is untrusted input and the parsers are pure Python: a large or hostile file can keep a CPU
busy for a long time. In the API process that would hold the GIL and stall every other request, and a thread
cannot be cancelled. So the API and the worker parse through ``parse_file_isolated``: the child gets the file on
stdin, a minimal environment (no secrets), CPU / memory limits, and is killed when the wall-clock limit expires
(the same approach as ``app.services.pdf.inspect_pdf_isolated``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.schemas.metadata import Author
from app.services.importers import parse_file
from app.services.importers.common import ImportFormatError, ImportItem, ParseProblem, ParseResult

UPLOAD_TIMEOUT_SECONDS = 60  # a file sent through the API (4 MiB at most)
FOLDER_TIMEOUT_SECONDS = 600  # a file in the server's import folder (64 MiB at most), parsed by the worker
MEMORY_BYTES = 1024 * 1024 * 1024
_MIN_RESULT_BYTES = 32 * 1024 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EXT = re.compile(r"^[a-z0-9]{1,10}$")


def _name_hint(filename: str | None) -> str:
    """Only the extension reaches the child's command line (``parse_file`` uses nothing else of the name)."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    return f"file.{ext}" if _EXT.match(ext) else "file"


def _dump(result: ParseResult) -> dict[str, Any]:
    items = []
    for item in result.items:
        d = asdict(item)
        d["authors"] = [a.model_dump() for a in item.authors]
        items.append(d)
    return {"format": result.format, "items": items, "problems": [asdict(p) for p in result.problems]}


def _load(data: dict[str, Any]) -> ParseResult:
    items = [ImportItem(**{**d, "authors": [Author(**a) for a in d["authors"]]}) for d in data["items"]]
    return ParseResult(format=data["format"], items=items, problems=[ParseProblem(**p) for p in data["problems"]])


async def parse_file_isolated(
    data: bytes, filename: str | None = None, *, timeout: float = UPLOAD_TIMEOUT_SECONDS
) -> ParseResult:
    """``parse_file`` in a child process. Raises ``ImportFormatError`` on a bad file, a timeout or a crash."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_PROJECT_ROOT), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.services.importers.isolated", str(int(timeout)), _name_hint(filename),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(_PROJECT_ROOT),
        env=env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(data), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ImportFormatError("The file took too long to read") from None
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0 or len(out) > max(_MIN_RESULT_BYTES, 8 * len(data)):
        # A non-zero exit is the CPU limit (SIGXCPU), the memory limit or a parser crash.
        raise ImportFormatError("The file could not be read (too large or too complex)")
    try:
        payload = json.loads(out)
    except ValueError:
        raise ImportFormatError("The file could not be read") from None
    if "error" in payload:
        raise ImportFormatError(str(payload["error"])[:300])
    return _load(payload["result"])


def _main(argv: list[str]) -> int:
    """Child entry point: ``<cpu seconds> <name hint>``, the file on stdin, JSON on stdout."""
    import resource  # noqa: PLC0415

    cpu = int(argv[0])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        out: dict[str, Any] = {"result": _dump(parse_file(sys.stdin.buffer.read(), argv[1]))}
    except ImportFormatError as exc:
        out = {"error": str(exc)}
    except (MemoryError, RecursionError):
        out = {"error": "The file could not be read (too large or too deeply nested)"}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through parse_file_isolated
    sys.exit(_main(sys.argv[1:]))
