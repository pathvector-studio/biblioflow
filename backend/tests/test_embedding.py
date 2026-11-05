"""Embedding providers, the embedder server (tiny ONNX graph), model download verification."""

import hashlib
import json
from pathlib import Path

import httpx
import numpy as np
import onnx
import pytest
from fastapi.testclient import TestClient
from onnx import TensorProto, helper, numpy_helper
from tokenizers import Tokenizer, models, pre_tokenizers

from app.core.config import Settings
from app.embedder import fetch, spec
from app.embedder.model import OnnxEmbedder
from app.embedder.server import MAX_BODY_BYTES, MAX_CHARS, MAX_TEXTS, create_app
from app.services import embedding
from app.services.embedding import EmbeddingError, FakeEmbedder, build_embedder
from app.services.embedding.providers import LocalEmbedder, OpenAIEmbedder, VoyageEmbedder

VEC = [0.5] * 1024


def settings(**kw) -> Settings:
    base = {"embedding_model": None, "embedding_base_url": None, "database_url": "postgresql+asyncpg://u:p@h/d", "redis_url": "redis://h", "jwt_secret": "x" * 40}
    return Settings(_env_file=None, **{**base, **kw})


# ---------------------------------------------------------------- provider selection and clients
def test_build_embedder_per_provider():
    assert build_embedder(settings(embedding_provider="none")) is None
    local = build_embedder(settings(embedding_provider="local"))
    assert isinstance(local, LocalEmbedder) and local.model_id == "bge-m3@5617a9f:int8"
    assert build_embedder(settings(embedding_provider="local", embedding_local_precision="fp32")).model_id == "bge-m3@5617a9f:fp32"
    assert build_embedder(settings(embedding_provider="voyage")).model_id == "voyage-3.5"
    assert build_embedder(settings(embedding_provider="openai")).model_id == "openai:text-embedding-3-large"
    custom = build_embedder(settings(embedding_provider="openai", embedding_model="bge-m3", embedding_base_url="http://ollama:11434/v1"))
    assert custom.model_id == "openai:bge-m3" and custom.base_url == "http://ollama:11434/v1"


async def test_local_client_request_and_validation():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"model": "m", "vectors": [VEC] * len(body["texts"])})

    client = LocalEmbedder("http://embedder:8001/", "int8", transport=httpx.MockTransport(handler))
    assert len(await client.embed(["a", "b"], "document")) == 2
    assert str(seen[0].url) == "http://embedder:8001/embed"
    assert json.loads(seen[0].content) == {"texts": ["a", "b"], "kind": "document"}
    long = await client.embed(["x" * 20000], "query")
    assert len(json.loads(seen[1].content)["texts"][0]) == 8000 and len(long) == 1

    bad = LocalEmbedder("http://e", "int8", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"vectors": [[1.0]]})))
    with pytest.raises(EmbeddingError, match="wrong size"):
        await bad.embed(["a"], "query")
    down = LocalEmbedder("http://e", "int8", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="secret paper text")))
    with pytest.raises(EmbeddingError) as exc:
        await down.embed(["a"], "query")
    assert "500" in str(exc.value) and "secret" not in str(exc.value)

    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(EmbeddingError, match="ConnectError"):
        await LocalEmbedder("http://e", "int8", transport=httpx.MockTransport(boom)).embed(["a"], "query")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(EmbeddingError, match="timed out"):
        await LocalEmbedder("http://e", "int8", transport=httpx.MockTransport(slow)).embed(["a"], "query", timeout=2)


async def test_voyage_and_openai_requests():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        n = len(json.loads(request.content)["input"])
        # out of order on purpose: the index decides
        data = [{"index": i, "embedding": [float(i)] + [0.0] * 1023} for i in reversed(range(n))]
        return httpx.Response(200, json={"data": data})

    t = httpx.MockTransport(handler)
    out = await VoyageEmbedder("vk", "voyage-3.5", transport=t).embed(["a", "b"], "query")
    assert [v[0] for v in out] == [0.0, 1.0]
    body = json.loads(seen[0].content)
    assert body["input_type"] == "query" and body["output_dimension"] == 1024 and body["model"] == "voyage-3.5"
    assert seen[0].headers["authorization"] == "Bearer vk" and str(seen[0].url) == "https://api.voyageai.com/v1/embeddings"

    await OpenAIEmbedder("ok", "text-embedding-3-large", transport=t).embed(["a"], "document")
    assert json.loads(seen[1].content)["dimensions"] == 1024 and str(seen[1].url) == "https://api.openai.com/v1/embeddings"
    await OpenAIEmbedder("", "bge-m3", "http://ollama:11434/v1", transport=t).embed(["a"], "document")
    assert "dimensions" not in json.loads(seen[2].content) and "authorization" not in seen[2].headers
    assert str(seen[2].url) == "http://ollama:11434/v1/embeddings"


async def test_fake_embedder_is_deterministic_and_word_based():
    e = FakeEmbedder()
    a, b, c = await e.embed(["alpha beta gamma", "alpha beta gamma", "zzz qqq"], "document")
    assert a == b and len(a) == 1024
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
    assert dot(a, a) == pytest.approx(1.0) and dot(a, c) < 0.1
    assert embedding.get_embedder() is None  # the test environment uses EMBEDDING_PROVIDER=none


# ---------------------------------------------------------------- ONNX engine and server
def make_tiny_model(directory: Path) -> tuple[Path, Path]:
    """input_ids/attention_mask -> sentence_embedding [b, 1024] = sin(sum of token ids * W); same I/O as bge-m3."""
    w = np.linspace(0.1, 1.0, 1024, dtype=np.float32).reshape(1, 1024)
    graph = helper.make_graph(
        [
            helper.make_node("Cast", ["input_ids"], ["f"], to=TensorProto.FLOAT),
            helper.make_node("Cast", ["attention_mask"], ["mask"], to=TensorProto.FLOAT),
            helper.make_node("Mul", ["f", "mask"], ["fm"]),  # padding must not count
            helper.make_node("ReduceSum", ["fm", "axes"], ["s"], keepdims=1),
            helper.make_node("Mul", ["s", "w"], ["m"]),
            helper.make_node("Sin", ["m"], ["sentence_embedding"]),
        ],
        "tiny",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", "s"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", "s"]),
        ],
        [helper.make_tensor_value_info("sentence_embedding", TensorProto.FLOAT, ["b", 1024])],
        [numpy_helper.from_array(w, "w"), numpy_helper.from_array(np.array([1], dtype=np.int64), "axes")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, directory / "model.onnx")
    tok = Tokenizer(models.WordLevel({"<unk>": 0, "<pad>": 1, "a": 2, "b": 3, "c": 4}, unk_token="<unk>"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.save(str(directory / "tokenizer.json"))
    return directory / "model.onnx", directory / "tokenizer.json"


@pytest.fixture
def tiny(tmp_path):
    return OnnxEmbedder(*make_tiny_model(tmp_path), threads=1, max_tokens=4)


def test_engine_normalizes_and_keeps_input_order(tiny):
    texts = ["a b c a b", "a", "b c", "c c c c c c", "a a", "b", "c", "a b", "b b b", "c a", "b zz"]  # > 1 batch
    out = tiny.encode(texts)
    assert out.shape == (len(texts), 1024)
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, rtol=1e-5)
    assert not np.allclose(out[0], out[1])  # the vectors differ per text, so a shuffled result would be noticed
    for i, t in enumerate(texts):
        np.testing.assert_allclose(out[i], tiny.encode([t])[0], rtol=1e-5)
    assert tiny.encode([]).shape == (0, 1024)


def test_engine_truncates_at_max_tokens(tmp_path):
    engine = OnnxEmbedder(*make_tiny_model(tmp_path), threads=1, max_tokens=2)
    assert len(engine.tokenizer.encode("a b c a b").ids) == 2


def test_server_embed_and_limits(tiny):
    with TestClient(create_app(tiny, "m@1:int8")) as c:
        assert c.get("/healthz").json() == {"status": "ok", "model": "ready", "model_id": "m@1:int8"}
        r = c.post("/embed", json={"texts": ["a b", "c"], "kind": "query"})
        assert r.status_code == 200 and r.json()["model"] == "m@1:int8"
        vectors = np.array(r.json()["vectors"])
        assert vectors.shape == (2, 1024)
        assert c.post("/embed", json={"texts": []}).status_code == 422
        assert c.post("/embed", json={"texts": ["a"] * (MAX_TEXTS + 1)}).status_code == 422
        assert c.post("/embed", json={"texts": ["a"] * MAX_TEXTS}).status_code == 200
        assert c.post("/embed", json={"texts": ["a" * (MAX_CHARS + 1)]}).status_code == 422
        assert c.post("/embed", json={"texts": ["a"], "kind": "other"}).status_code == 422
        assert c.post("/embed", json={"texts": ["a" * MAX_CHARS]}).status_code == 200
        big = c.post("/embed", content=b"x" * (MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})
        assert big.status_code == 413
        assert c.get("/docs").status_code == 404


def test_server_without_local_provider_answers_503(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    with TestClient(create_app()) as c:
        assert c.get("/healthz").json() == {"status": "ok", "model": "disabled", "model_id": None}
        r = c.post("/embed", json={"texts": ["a"]})
        assert r.status_code == 503 and r.json()["detail"] == "model not ready"


def test_server_default_provider_is_none(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    assert spec.env_provider() == "none"
    with TestClient(create_app()) as c:
        assert c.get("/healthz").json()["model"] == "disabled"


def wait_for(predicate, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_server_starts_without_a_model_then_loads_it_when_it_appears(tiny, monkeypatch):
    """No model-init yet: the server is up, healthy, answers 503; once the loader finds a model it serves."""
    monkeypatch.setenv("EMBEDDER_POLL_SECONDS", "0.05")
    available = []

    def loader(on_loading):
        if not available:
            return None
        on_loading()
        return tiny, "m@1:int8"

    with TestClient(create_app(loader=loader)) as c:
        assert c.get("/healthz").json() == {"status": "ok", "model": "absent", "model_id": None}
        assert c.post("/embed", json={"texts": ["a"]}).status_code == 503
        available.append(1)
        assert wait_for(lambda: c.get("/healthz").json()["model"] == "ready")
        assert c.get("/healthz").json()["model_id"] == "m@1:int8"
        assert c.post("/embed", json={"texts": ["a b"]}).status_code == 200


def test_server_keeps_polling_after_a_failed_load(tiny, monkeypatch):
    monkeypatch.setenv("EMBEDDER_POLL_SECONDS", "0.05")
    calls = []

    def loader(on_loading):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("corrupt file")
        return tiny, "m"

    with TestClient(create_app(loader=loader)) as c:
        assert wait_for(lambda: c.get("/healthz").json()["model"] == "ready")
    assert len(calls) == 3


def test_load_local_model_needs_a_matching_ready_marker_and_valid_files(tmp_path, monkeypatch):
    from app.embedder.server import load_local_model

    blobs, files = fake_files(tmp_path)
    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_LOCAL_PRECISION", "int8")
    monkeypatch.setattr(spec, "INT8_FILES", files)
    loading = []
    assert load_local_model(lambda: loading.append(1)) is None and not loading  # no marker
    root = tmp_path / "bge-m3" / spec.REVISION[:12]
    for f in files:
        (root / f.path).parent.mkdir(parents=True, exist_ok=True)
        (root / f.path).write_bytes(blobs[f.path])
    assert load_local_model() is None  # files but no marker: model-init has not finished
    fetch.write_ready(tmp_path, "bge-m3@other:fp32")
    assert load_local_model() is None  # the marker is for another model
    fetch.write_ready(tmp_path, spec.model_id("int8"))
    (root / files[0].path).write_bytes(b"tampered")
    assert load_local_model(lambda: loading.append(1)) is None  # the marker is not trusted over the checksum
    assert loading == [1]


def test_server_does_not_import_app_config():
    import subprocess
    import sys

    code = "import sys; import app.embedder.server, app.embedder.fetch; sys.exit('app.core.config' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent.parent).returncode == 0


# ---------------------------------------------------------------- model download
def fake_files(tmp_path):
    """Both layouts: fp32 files come with their path under the repository, int8 ones from the release."""
    blobs = {
        "onnx/model.onnx": b"graph", "onnx/model.onnx_data": b"weights" * 100, "onnx/tokenizer.json": b"{}",
        "onnx/model_int8.onnx": b"int8-bytes",
    }
    files = tuple(
        spec.ModelFile(
            path, len(data), hashlib.sha256(data).hexdigest(),
            remote="model_int8.onnx" if path.endswith("int8.onnx") else None,
        )
        for path, data in blobs.items()
    )
    return blobs, files


def int8_only(files):
    """The release has the two files flat, under their bare names."""
    out = []
    for f in files:
        if f.path in ("onnx/model_int8.onnx", "onnx/tokenizer.json"):
            out.append(spec.ModelFile(f.path, f.size, f.sha256, remote=f.path.rsplit("/", 1)[1]))
    return tuple(sorted(out, key=lambda f: f.remote != "model_int8.onnx"))


def fp32_only(files):
    return tuple(f for f in files if not f.path.endswith("int8.onnx"))


def serving(blobs, requests, prefix=f"{spec.REVISION}/"):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        key = request.url.path.split(prefix, 1)[1] if prefix in request.url.path else "onnx/" + request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, content=blobs[key]) if key in blobs else httpx.Response(404)

    return httpx.MockTransport(handler)


def test_pinned_spec_is_well_formed():
    assert spec.REVISION == "5617a9f61b028005a4858fdac845db406aefb181"
    assert spec.BASE_URL == "https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181"
    assert {f.path for f in spec.FILES} == {
        "onnx/model.onnx", "onnx/model.onnx_data", "onnx/tokenizer.json", "onnx/Constant_7_attr__value",
    }
    assert all(len(f.sha256) == 64 and f.size > 0 for f in spec.FILES)


def test_pinned_int8_release():
    assert spec.RELEASE_TAG == "model-bge-m3-int8-5617a9f-1"
    assert spec.RELEASE_URL == (
        "https://github.com/pathvector-studio/biblioflow/releases/download/model-bge-m3-int8-5617a9f-1"
    )
    by_path = {f.path: f for f in spec.INT8_FILES}
    assert set(by_path) == {"onnx/model_int8.onnx", "onnx/tokenizer.json"}
    assert (by_path["onnx/model_int8.onnx"].size, by_path["onnx/model_int8.onnx"].sha256) == (
        568765638, "830e6134ead4638c9ead5131e8d857790f535830ff04bcde10266a3023ba6748",
    )
    # the tokenizer is the very file fp32 uses, so the two precisions share it on disk
    fp32_tok = next(f for f in spec.FILES if f.path == "onnx/tokenizer.json")
    assert (by_path["onnx/tokenizer.json"].size, by_path["onnx/tokenizer.json"].sha256) == (fp32_tok.size, fp32_tok.sha256)
    assert spec.model_id("int8") == "bge-m3@5617a9f:int8"  # unchanged: no re-embedding
    assert spec.files_for("int8") is spec.INT8_FILES and spec.files_for("fp32") is spec.FILES
    assert spec.source_url("int8") == spec.RELEASE_URL and spec.source_url("fp32") == spec.BASE_URL


def test_fetch_int8_downloads_only_two_files_from_the_release_and_never_quantizes(tmp_path):
    blobs, files = fake_files(tmp_path)
    requests: list[str] = []
    base = "https://example.test/releases/download/tag"
    with httpx.Client(transport=serving(blobs, requests)) as client:
        target = fetch.ensure_model(tmp_path, "int8", client, int8_only(files), base)
        assert target == spec.model_dir(tmp_path) / "model_int8.onnx" and target.read_bytes() == b"int8-bytes"
        assert requests == [f"{base}/model_int8.onnx", f"{base}/tokenizer.json"]
        assert not hasattr(fetch, "quantize_int8")
        # READY is last, holds the model id, and a second run downloads nothing
        assert spec.ready_file(tmp_path).read_text().strip() == "bge-m3@5617a9f:int8"
        fetch.ensure_model(tmp_path, "int8", client, int8_only(files), base)
        assert len(requests) == 2
    assert not list(tmp_path.rglob("*.part"))


def test_fetch_accepts_files_already_in_the_volume_without_downloading(tmp_path):
    blobs, files = fake_files(tmp_path)
    for f in int8_only(files):
        dest = tmp_path / "bge-m3" / spec.REVISION[:12] / f.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blobs[f.path])
    requests: list[str] = []
    with httpx.Client(transport=serving(blobs, requests)) as client:
        fetch.ensure_model(tmp_path, "int8", client, int8_only(files), "https://example.test/r")
    assert requests == [] and fetch.read_ready(tmp_path) == "bge-m3@5617a9f:int8"


def test_fetch_downloads_verifies_and_is_idempotent(tmp_path):
    blobs, files = fake_files(tmp_path)
    files = fp32_only(files)
    requests: list[str] = []
    with httpx.Client(transport=serving(blobs, requests)) as client:
        target = fetch.ensure_model(tmp_path, "fp32", client, files)
        assert target == spec.model_dir(tmp_path) / "model.onnx" and target.read_bytes() == b"graph"
        assert len(requests) == 3 and requests[0].startswith("https://huggingface.co/BAAI/bge-m3/resolve/")
        assert fetch.read_ready(tmp_path) == "bge-m3@5617a9f:fp32"
        fetch.ensure_model(tmp_path, "fp32", client, files)
        assert len(requests) == 3  # nothing to do the second time
        assert not list(tmp_path.rglob("*.part"))
        # a damaged file is fetched again, and the marker does not vouch for it meanwhile
        (spec.model_dir(tmp_path) / "tokenizer.json").write_bytes(b"xx")
        fetch.ensure_model(tmp_path, "fp32", client, files)
        assert len(requests) == 4 and (spec.model_dir(tmp_path) / "tokenizer.json").read_bytes() == b"{}"


def test_ready_marker_for_another_precision_is_withdrawn_and_failures_leave_none(tmp_path):
    blobs, files = fake_files(tmp_path)
    fetch.write_ready(tmp_path, "bge-m3@5617a9f:fp32")
    blobs["onnx/model_int8.onnx"] = b"wrong!!!!!"  # right size, wrong hash
    with httpx.Client(transport=serving(blobs, [])) as client, pytest.raises(fetch.VerificationError):
        fetch.ensure_model(tmp_path, "int8", client, int8_only(files), "https://example.test/r")
    assert fetch.read_ready(tmp_path) is None


@pytest.mark.parametrize("tamper", ["content", "longer", "shorter"])
def test_fetch_rejects_wrong_bytes(tmp_path, tamper):
    blobs, files = fake_files(tmp_path)
    blobs["onnx/model.onnx_data"] = {
        "content": b"W" * 700, "longer": b"weights" * 100 + b"!", "shorter": b"weights" * 99
    }[tamper]
    with httpx.Client(transport=serving(blobs, [])) as client, pytest.raises(fetch.VerificationError):
        fetch.ensure_model(tmp_path, "fp32", client, fp32_only(files))
    assert not list(tmp_path.rglob("*.part"))
    assert not (spec.model_dir(tmp_path) / "model.onnx_data").exists()
    assert fetch.read_ready(tmp_path) is None


def test_fetch_http_error_leaves_nothing(tmp_path):
    blobs, files = fake_files(tmp_path)
    del blobs["onnx/tokenizer.json"]
    with httpx.Client(transport=serving(blobs, [])) as client, pytest.raises(httpx.HTTPStatusError):
        fetch.ensure_model(tmp_path, "fp32", client, fp32_only(files))
    assert not list(tmp_path.rglob("*.part"))
    assert fetch.read_ready(tmp_path) is None


def test_fetch_follows_https_redirects_but_refuses_a_downgrade(tmp_path):
    blobs, files = fake_files(tmp_path)
    files = int8_only(files)

    def redirecting(target_scheme):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "github.example":
                name = request.url.path.rsplit("/", 1)[1]
                return httpx.Response(302, headers={"location": f"{target_scheme}://objects.example/{name}?x=1"})
            return httpx.Response(200, content=blobs["onnx/" + request.url.path.rsplit("/", 1)[1]])

        return httpx.MockTransport(handler)

    with httpx.Client(transport=redirecting("https"), follow_redirects=True, event_hooks={"response": [fetch.https_only]}) as client:
        fetch.ensure_model(tmp_path, "int8", client, files, "https://github.example/dl")
    other = tmp_path / "other"
    with httpx.Client(transport=redirecting("http"), follow_redirects=True, event_hooks={"response": [fetch.https_only]}) as client:
        with pytest.raises(fetch.VerificationError, match="non-https"):
            fetch.ensure_model(other, "int8", client, files, "https://github.example/dl")
    assert fetch.read_ready(other) is None
    assert fetch.new_client().follow_redirects is True


def test_main_is_a_noop_unless_local(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    for provider in ("none", "voyage", "openai"):
        monkeypatch.setenv("EMBEDDING_PROVIDER", provider)
        assert fetch.main() == 0
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_LOCAL_PRECISION", "fp16")
    assert fetch.main() == 1


def test_main_with_none_says_what_enabling_would_download(monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    with caplog.at_level(logging.INFO, logger="biblioflow.model-init"):
        assert fetch.main() == 0
    text = caplog.text
    assert "nothing is downloaded" in text and "EMBEDDING_PROVIDER=local" in text
    assert "0.6 GB" in text and "GitHub Release" in text and "MIT" in text and "never leaves" in text
    assert list(tmp_path.iterdir()) == []


def test_model_init_retries_with_backoff_then_idles(monkeypatch):
    """The container must not exit (``up --wait`` fails on an exited service), and a failed download retries."""
    from app.embedder import fetch

    results = iter([1, 1, 0])
    sleeps: list[int] = []

    class Idle(Exception):
        pass

    def pause():
        raise Idle

    monkeypatch.setattr(fetch, "main", lambda: next(results))
    monkeypatch.setattr(fetch.time, "sleep", sleeps.append)
    monkeypatch.setattr("signal.signal", lambda *a: None)
    monkeypatch.setattr("signal.pause", pause)
    with pytest.raises(Idle):
        fetch.run_forever()
    assert sleeps == [fetch.RETRY_FIRST_SECONDS, fetch.RETRY_FIRST_SECONDS * 2]
