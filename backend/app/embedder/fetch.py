"""``biblioflow-model-init``: download the pinned model once, verify it, then write the READY marker. Idempotent.

    python -m app.embedder.fetch

Downloads go to ``*.part`` files in the volume, are checked against the sha256 written in ``spec.py`` and
renamed into place, so an interrupted run never leaves a half file that looks complete. ``READY`` (holding the
model id) is written last, after every file has been verified; the embedder loads nothing without it.
Nothing is downloaded unless ``EMBEDDING_PROVIDER=local``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
from pathlib import Path

import httpx

from app.embedder import spec

logger = logging.getLogger("biblioflow.model-init")
CHUNK = 1024 * 1024


class VerificationError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def is_valid(path: Path, f: spec.ModelFile) -> bool:
    """Size first (cheap); the hash only when the size fits."""
    try:
        return path.is_file() and path.stat().st_size == f.size and sha256_of(path) == f.sha256
    except OSError:
        return False


def https_only(response: httpx.Response) -> None:
    """Response hook: every hop of a redirect chain (GitHub hands the release files to its object storage) must
    be https, so a redirect can never downgrade the download to plain http."""
    if response.url.scheme != "https":
        raise VerificationError("refusing a non-https download URL")


def new_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True, event_hooks={"response": [https_only]}
    )


def download(client: httpx.Client, url: str, f: spec.ModelFile, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    size = 0
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with part.open("wb") as out:
                for block in response.iter_bytes(CHUNK):
                    size += len(block)
                    if size > f.size:
                        raise VerificationError(f"{f.path}: larger than the expected {f.size} bytes")
                    h.update(block)
                    out.write(block)
        if size != f.size:
            raise VerificationError(f"{f.path}: got {size} bytes, expected {f.size}")
        if h.hexdigest() != f.sha256:
            raise VerificationError(f"{f.path}: sha256 mismatch")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def model_root(models_dir: Path) -> Path:
    return models_dir / "bge-m3" / spec.REVISION[:12]


def files_valid(models_dir: Path, precision: str, files: tuple[spec.ModelFile, ...] | None = None) -> bool:
    """Every file of ``precision`` is present with the pinned size and sha256."""
    root = model_root(models_dir)
    return all(is_valid(root / f.path, f) for f in (files or spec.files_for(precision)))


def read_ready(models_dir: Path) -> str | None:
    try:
        return spec.ready_file(models_dir).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_ready(models_dir: Path, model_id: str) -> None:
    """Atomic: the marker either does not exist or holds a complete model id."""
    marker = spec.ready_file(models_dir)
    tmp = marker.with_name(marker.name + ".part")
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_text(model_id + "\n", encoding="utf-8")
        os.replace(tmp, marker)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_model(
    models_dir: Path,
    precision: str,
    client: httpx.Client | None = None,
    files: tuple[spec.ModelFile, ...] | None = None,
    base_url: str | None = None,
) -> Path:
    """Make sure the model for ``precision`` is present and verified, write the READY marker last, and return
    the ONNX file to load. int8 comes from the project's GitHub Release (nothing is converted here); fp32 from
    Hugging Face."""
    files = files or spec.files_for(precision)
    base_url = (base_url or spec.source_url(precision)).rstrip("/")
    wanted = spec.model_id(precision)
    marker = spec.ready_file(models_dir)
    if read_ready(models_dir) not in (None, wanted):
        marker.unlink(missing_ok=True)  # another precision's marker must not vouch for this one
    root = model_root(models_dir)
    owns_client = client is None
    client = client or new_client()
    try:
        for f in files:
            dest = root / f.path
            if is_valid(dest, f):
                continue
            logger.info("downloading %s (%d bytes)", f.remote or f.path, f.size)
            marker.unlink(missing_ok=True)
            download(client, f"{base_url}/{f.remote or f.path}", f, dest)
    finally:
        if owns_client:
            client.close()
    write_ready(models_dir, wanted)
    return spec.model_file(models_dir, precision)


def announce(precision: str) -> None:
    """What ``EMBEDDING_PROVIDER=local`` would download, for people who left the default (``none``)."""
    if precision == "int8":
        what, where, size = "bge-m3 (int8)", f"the project's GitHub Release {spec.RELEASE_TAG}", "about 0.6 GB"
    else:
        what, where, size = "bge-m3 (fp32)", f"Hugging Face ({spec.REPO} at {spec.REVISION[:7]})", "about 2.3 GB"
    logger.info(
        "EMBEDDING_PROVIDER=none: semantic search is off and nothing is downloaded. To turn it on, set "
        "EMBEDDING_PROVIDER=local in .env and run `docker compose up -d`: this downloads %s, %s, from %s "
        "(MIT license). Your papers' text never leaves this machine.",
        what, size, where,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines carry the CDN's signed redirect URLs
    provider = spec.env_provider()
    if provider == "none":
        try:
            announce(spec.env_precision())
        except ValueError:
            announce("int8")
        return 0
    if provider != "local":
        logger.info("EMBEDDING_PROVIDER=%s: no local model needed", provider)
        return 0
    try:
        target = ensure_model(spec.env_models_dir(), spec.env_precision())
    except (httpx.HTTPError, VerificationError, OSError, ValueError) as exc:
        logger.error("model setup failed: %s", exc)
        return 1
    logger.info("model ready: %s", target)
    return 0


RETRY_FIRST_SECONDS = 60
RETRY_MAX_SECONDS = 3600


def run_forever() -> None:
    """Container entry point: set the model up, retrying with backoff on failure, then idle.

    The container must stay running: ``docker compose up --wait`` (used by install.sh) fails when a service
    without dependants exits, even with status 0. Idling costs a sleeping Python process; retrying in-process
    keeps a flaky download from turning into a restart loop that ``--wait`` would also report as a failure."""
    import signal

    # PID 1 ignores SIGTERM unless a handler is installed; exit promptly on `docker compose stop`.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    delay = RETRY_FIRST_SECONDS
    while main() != 0:
        logger.info("retrying in %d s", delay)
        time.sleep(delay)
        delay = min(delay * 2, RETRY_MAX_SECONDS)
    while True:
        signal.pause()


if __name__ == "__main__":
    if "--once" in sys.argv[1:]:
        sys.exit(main())
    run_forever()
