"""``biblioflow-model-init``: download the pinned model once, verify it, quantize if asked. Idempotent.

    python -m app.embedder.fetch

Downloads go to ``*.part`` files in the volume, are checked against the sha256 written in ``spec.py`` and
renamed into place, so an interrupted run never leaves a half file that looks complete. Nothing is
downloaded unless ``EMBEDDING_PROVIDER=local``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
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


def download(client: httpx.Client, f: spec.ModelFile, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    size = 0
    try:
        with client.stream("GET", f"{spec.BASE_URL}/{f.path}") as response:
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


def quantize_int8(src: Path, dest: Path) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    tmp = dest.with_name(dest.name + ".part")
    try:
        quantize_dynamic(str(src), str(tmp), weight_type=QuantType.QInt8)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_model(
    models_dir: Path,
    precision: str,
    client: httpx.Client | None = None,
    files: tuple[spec.ModelFile, ...] = spec.FILES,
) -> Path:
    """Make sure the model for ``precision`` is present and return the ONNX file to load."""
    root = models_dir / "bge-m3" / spec.REVISION[:12]
    owns_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True)
    try:
        for f in files:
            dest = root / f.path
            if is_valid(dest, f):
                continue
            logger.info("downloading %s (%d bytes)", f.path, f.size)
            download(client, f, dest)
    finally:
        if owns_client:
            client.close()
    target = spec.model_file(models_dir, precision)
    if precision == "int8" and not (target.is_file() and target.stat().st_size > 0):
        logger.info(
            "quantizing to int8: needs about 9 GiB of RAM for ~10 s, once "
            "(if this container is killed, set EMBEDDING_LOCAL_PRECISION=fp32 to skip it)"
        )
        quantize_int8(spec.model_dir(models_dir) / "model.onnx", target)
    return target


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines carry the CDN's signed redirect URLs
    provider = spec.env_provider()
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


if __name__ == "__main__":
    sys.exit(main())
