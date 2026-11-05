#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "onnx==1.23.2",
#     "onnxruntime==1.31.1",
#     "numpy==2.5.4",
#     "tokenizers==0.23.3",
# ]
# ///
"""Build the release assets for the quantized bge-m3 model (run by .github/workflows/model-artifact.yml).

Self-contained on purpose: the dependencies above are pinned here (same versions as backend/uv.lock when this
was written), so the script keeps working after ``onnx`` is dropped from the backend's runtime dependencies.
It only reads ``backend/app/embedder/spec.py`` (stdlib-only) by path for the pinned revision and checksums.

    uv run scripts/build-model-artifact.py --out dist/model [--source-dir DIR]

Steps: obtain + verify the fp32 files -> quantize to int8 twice (separate processes) -> compare sha256 ->
write model_int8.onnx, tokenizer.json, LICENSE, SHA256SUMS, README.txt, REPRODUCIBILITY.txt and summary.md.
Quantization settings are identical to ``app.embedder.fetch.quantize_int8``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import resource
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHUNK = 1024 * 1024
SAMPLES = [
    "The quick brown fox jumps over the lazy dog.",
    "Scalable BGP routing in Clos fabrics.",
    "Attention is all you need.",
    "量子化されたモデルでも意味検索の結果は変わらないはずです。",
    "Dense passage retrieval for open-domain question answering.",
    "ネットワークトポロジ図の解説",
]
MIT_TEXT = """MIT License

Copyright (c) BAAI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def load_spec():
    path = ROOT / "backend" / "app" / "embedder" / "spec.py"
    s = importlib.util.spec_from_file_location("bf_embedder_spec", path)
    mod = importlib.util.module_from_spec(s)
    sys.modules[s.name] = mod
    s.loader.exec_module(mod)
    return mod


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, part.open("wb") as out:
            shutil.copyfileobj(resp, out, CHUNK)
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)


def prepare_sources(spec, work: Path, source_dir: Path | None) -> Path:
    """Return a flat directory holding the verified fp32 files (model.onnx finds its external data by name)."""
    src = work / "fp32"
    src.mkdir(parents=True, exist_ok=True)
    for f in spec.FILES:
        name = Path(f.path).name
        dest = src / name
        if source_dir is not None:
            shutil.copyfile(source_dir / name, dest)
        else:
            print(f"downloading {f.path} ({f.size} bytes)", flush=True)
            fetch(f"{spec.BASE_URL}/{f.path}", dest)
        if dest.stat().st_size != f.size:
            raise SystemExit(f"{f.path}: got {dest.stat().st_size} bytes, expected {f.size}")
        if sha256_of(dest) != f.sha256:
            raise SystemExit(f"{f.path}: sha256 mismatch")
        print(f"verified {f.path}", flush=True)
    return src


def quantize_one(src_model: Path, dest: Path) -> None:
    # Same call as backend/app/embedder/fetch.py quantize_int8; keep the two in sync.
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(str(src_model), str(dest), weight_type=QuantType.QInt8)


def quantize_isolated(src_model: Path, dest: Path) -> int:
    """Run in a child process so each run is independent and its peak RSS is measurable. Returns KiB."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    subprocess.run([sys.executable, __file__, "--quantize-one", str(src_model), str(dest)], check=True)
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # ru_maxrss is KiB on Linux and bytes on macOS.
    if sys.platform == "darwin":
        peak //= 1024
        before //= 1024
    return peak if peak > before else before


def embed(model: Path, tokenizer_path: Path, texts: list[str]):
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.enable_truncation(max_length=512)
    pad_id = tok.token_to_id("<pad>")
    tok.enable_padding(pad_id=1 if pad_id is None else pad_id, pad_token="<pad>")
    enc = tok.encode_batch(texts)
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
    sess = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    out = sess.run(["sentence_embedding"], {"input_ids": ids, "attention_mask": mask})[0]
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)


def fetch_license(spec) -> tuple[str, str]:
    url = f"{spec.BASE_URL}/LICENSE"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8"), f"fetched from {url}"
    except (urllib.error.URLError, OSError, UnicodeDecodeError):
        return MIT_TEXT, (
            f"The pinned revision has no LICENSE file ({url} is not available). The model card at "
            f"https://huggingface.co/{spec.REPO} declares license: mit; the standard MIT text is used."
        )


def build(args: argparse.Namespace) -> int:
    spec = load_spec()
    out: Path = args.out.resolve()
    work: Path = (args.work or out.parent / (out.name + "-work")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    src = prepare_sources(spec, work, args.source_dir)
    peaks, hashes, files = [], [], []
    for n in (1, 2):
        d = work / f"int8-run{n}"
        d.mkdir(exist_ok=True)
        target = d / spec.INT8_FILE
        print(f"quantizing (run {n}/2)", flush=True)
        peaks.append(quantize_isolated(src / "model.onnx", target))
        hashes.append(sha256_of(target))
        files.append(target)
        print(f"run {n}: {hashes[-1]}  peak {peaks[-1] / 1024 / 1024:.2f} GiB", flush=True)

    identical = hashes[0] == hashes[1]
    repro = [
        f"model:    {spec.REPO} @ {spec.REVISION}",
        "settings: onnxruntime.quantization.quantize_dynamic(weight_type=QuantType.QInt8)",
        f"run 1 sha256: {hashes[0]}",
        f"run 2 sha256: {hashes[1]}",
        f"byte-identical: {'yes' if identical else 'NO'}",
        f"peak quantization memory (max RSS): {max(peaks) / 1024 / 1024:.2f} GiB",
    ]
    if not identical:
        a = embed(files[0], src / "tokenizer.json", SAMPLES)
        b = embed(files[1], src / "tokenizer.json", SAMPLES)
        cos = [float(x @ y) for x, y in zip(a, b, strict=True)]
        repro.append(f"cosine similarity of the two outputs on {len(SAMPLES)} fixed sentences:")
        repro += [f"  {c:.6f}  {s}" for c, s in zip(cos, SAMPLES, strict=True)]
        repro.append(f"min cosine: {min(cos):.6f} (threshold in README: 0.999)")
    (out / "REPRODUCIBILITY.txt").write_text("\n".join(repro) + "\n")

    shutil.copyfile(files[0], out / spec.INT8_FILE)
    shutil.copyfile(src / "tokenizer.json", out / "tokenizer.json")
    license_text, license_note = fetch_license(spec)
    (out / "LICENSE").write_text(license_text)

    rev7 = spec.REVISION[:7]
    readme = f"""BiblioFlow: bge-m3 int8 model ({rev7})

What this is
  An int8 dynamic-quantized ONNX export of {spec.REPO}, plus its tokenizer, for BiblioFlow's local semantic
  search (1024-dimensional embeddings). Built by the GitHub Actions workflow "Model artifact" in this repository.

Source
  Repository: https://huggingface.co/{spec.REPO}
  Revision:   {spec.REVISION}
  Files used: {", ".join(f.path for f in spec.FILES)} (sha256-verified against backend/app/embedder/spec.py)
  tokenizer.json is the unmodified original (sha256 {spec.FILES[2].sha256}).

License
  bge-m3 is MIT-licensed (c) BAAI; see LICENSE. {license_note}

Quantization (exact call)
  from onnxruntime.quantization import QuantType, quantize_dynamic
  quantize_dynamic("model.onnx", "{spec.INT8_FILE}", weight_type=QuantType.QInt8)
  onnx==1.23.2, onnxruntime==1.31.1, numpy==2.5.4. Needs about 9 GiB of RAM.

How to reproduce
  git clone https://github.com/<owner>/<repo> && cd <repo>
  uv run scripts/build-model-artifact.py --out dist/model
  sha256sum dist/model/{spec.INT8_FILE}
  Result of the CI build (two runs): {"byte-identical" if identical else "NOT byte-identical"}; see REPRODUCIBILITY.txt.
  If the hashes differ on your machine (different CPU or library build), compare embeddings instead: embed a few
  sentences with both files and check that the cosine similarity of each pair is at least 0.999.

How to verify a download
  sha256sum -c SHA256SUMS
  gh attestation verify {spec.INT8_FILE} --repo <owner>/<repo>
  gh attestation verify tokenizer.json --repo <owner>/<repo>
"""
    # In CI, point the instructions at this repository; locally the placeholder stays.
    readme = readme.replace("<owner>/<repo>", os.environ.get("GITHUB_REPOSITORY") or "<owner>/<repo>")
    (out / "README.txt").write_text(readme)

    sums = [f"{sha256_of(out / n)}  {n}" for n in (spec.INT8_FILE, "tokenizer.json", "LICENSE", "README.txt", "REPRODUCIBILITY.txt")]
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")

    i8, tk = out / spec.INT8_FILE, out / "tokenizer.json"
    summary = f"""## bge-m3 int8 artifact

Revision `{spec.REVISION}`

Quantization run 1 and 2 byte-identical: **{"yes" if identical else "NO"}**

```
run 1 sha256: {hashes[0]}
run 2 sha256: {hashes[1]}
peak memory:  {max(peaks) / 1024 / 1024:.2f} GiB
```
{"" if identical else chr(10) + "Cosine similarity details are in REPRODUCIBILITY.txt." + chr(10)}
Paste into `backend/app/embedder/spec.py` (after publishing, with the release tag in the URL):

```python
INT8_ASSETS = (
    ModelFile("{spec.INT8_FILE}", {i8.stat().st_size}, "{sha256_of(i8)}"),
    ModelFile("tokenizer.json", {tk.stat().st_size}, "{sha256_of(tk)}"),
)
```
"""
    (out / "summary.md").write_text(summary)
    print(summary)
    json.dump({"identical": identical, "sha256": hashes, "peak_kib": peaks}, sys.stdout)
    print()
    return 0


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--quantize-one":
        quantize_one(Path(sys.argv[2]), Path(sys.argv[3]))
        return 0
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True, help="directory for the release assets")
    p.add_argument("--work", type=Path, help="scratch directory (default: <out>-work)")
    p.add_argument("--source-dir", type=Path, help="flat directory with the fp32 files instead of downloading")
    return build(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
