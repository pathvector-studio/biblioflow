"""The one model BiblioFlow runs locally, pinned to a revision with checksums.

Importable from anywhere (no heavy imports): the API / worker need only ``model_id`` to tag chunks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
BASE_URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}"
DIMENSION = 1024
PRECISIONS = ("int8", "fp32")
MAX_TOKENS = 512
INT8_FILE = "model_int8.onnx"


@dataclass(frozen=True)
class ModelFile:
    path: str  # relative to the repository root and to the model directory
    size: int
    sha256: str


# `Constant_7_attr__value` is a non-LFS blob (git oid 3cdc05e5f550d8bd136f28efcea0ad5e6b4169c8); the
# ONNX graph references it as an external initializer, so it must sit next to model.onnx.
FILES: tuple[ModelFile, ...] = (
    ModelFile("onnx/model.onnx", 724923, "f84251230831afb359ab26d9fd37d5936d4d9bb5d1d5410e66442f630f24435b"),
    ModelFile(
        "onnx/model.onnx_data", 2266820608, "1eebfb28493f67bba03ce0ef64bfdc7fc5a3bd9d7493f818bb1d78cd798416b4"
    ),
    ModelFile("onnx/tokenizer.json", 17082821, "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"),
    ModelFile(
        "onnx/Constant_7_attr__value", 65552, "cdf16f72c5d07b36484056e601ed9687f78477e5d85cee85a34f2406b7fb5906"
    ),
)


def model_id(precision: str) -> str:
    """Value stored in ``paper_chunks.embedding_model`` for vectors produced locally."""
    return f"bge-m3@{REVISION[:7]}:{precision}"


def model_dir(models_dir: Path) -> Path:
    return models_dir / "bge-m3" / REVISION[:12] / "onnx"


def model_file(models_dir: Path, precision: str) -> Path:
    return model_dir(models_dir) / ("model.onnx" if precision == "fp32" else INT8_FILE)


def env_models_dir() -> Path:
    return Path(os.environ.get("MODELS_DIR", "/models"))


def env_precision() -> str:
    value = os.environ.get("EMBEDDING_LOCAL_PRECISION", "int8").strip().lower() or "int8"
    if value not in PRECISIONS:
        raise ValueError(f"EMBEDDING_LOCAL_PRECISION must be one of {PRECISIONS}, got {value!r}")
    return value


def env_provider() -> str:
    return os.environ.get("EMBEDDING_PROVIDER", "local").strip().lower() or "local"
