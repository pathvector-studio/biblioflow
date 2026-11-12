"""Run ``format_entries`` in a resource-limited child process.

The items come from imported bibliography files and from other members of a workspace, so they are untrusted: a
record with a huge author list can keep citeproc-py busy for a long time (it is pure Python), and in the API
process that would hold the GIL and stall every request. A thread cannot be cancelled either. So the API goes
through ``format_entries_isolated``: the child gets the items on stdin, a minimal environment (no secrets), CPU and
memory limits, and is killed when the wall-clock limit expires. Same approach as
``app.services.importers.isolated`` and ``app.services.pdf.inspect_pdf_isolated``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from app.services.csl import BibliographyError, Format, format_entries

TIMEOUT_SECONDS = 30  # wall clock; the child's CPU limit is the same number
MEMORY_BYTES = 512 * 1024 * 1024  # a normal request needs well under 100 MiB; 100,000 authors on one record about 110 MiB
MAX_RESULT_BYTES = 16 * 1024 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


async def format_entries_isolated(
    items: list[dict[str, Any]],
    style: str,
    fmt: Format = "text",
    locale: str = "en-US",
    *,
    timeout: float | None = None,
    memory_bytes: int | None = None,
) -> list[str]:
    """``format_entries`` in a child process. Raises ``BibliographyError`` on bad input, a timeout or a crash."""
    timeout = TIMEOUT_SECONDS if timeout is None else timeout
    memory_bytes = MEMORY_BYTES if memory_bytes is None else memory_bytes
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_PROJECT_ROOT), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    request = json.dumps({"items": items, "style": style, "format": fmt, "locale": locale}).encode()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.services.csl.isolated", str(max(1, int(timeout))), str(int(memory_bytes)),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(_PROJECT_ROOT),
        env=env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(request), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise BibliographyError("Formatting took too long; narrow the selection") from None
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0 or len(out) > MAX_RESULT_BYTES:
        # A non-zero exit is the CPU limit (SIGXCPU), the memory limit or a crash in the formatter.
        raise BibliographyError("The bibliography could not be formatted (too large or too complex)")
    try:
        payload = json.loads(out)
    except ValueError:
        raise BibliographyError("The bibliography could not be formatted") from None
    if "error" in payload:
        raise BibliographyError(str(payload["error"])[:300])
    return [str(e) for e in payload["entries"]]


def _main(argv: list[str]) -> int:
    """Child entry point: ``<cpu seconds> <memory bytes>``, the request as JSON on stdin, the entries as JSON on stdout."""
    import resource  # noqa: PLC0415

    cpu, memory = int(argv[0]), int(argv[1])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        req = json.loads(sys.stdin.buffer.read())
        out: dict[str, Any] = {"entries": format_entries(req["items"], req["style"], req["format"], req["locale"])}
    except BibliographyError as exc:
        out = {"error": str(exc)}
    except (MemoryError, RecursionError):
        out = {"error": "The bibliography could not be formatted (too large or too complex)"}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through format_entries_isolated
    sys.exit(_main(sys.argv[1:]))
