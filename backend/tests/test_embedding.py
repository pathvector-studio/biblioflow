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
    down = LocalEmbedder("http://e", "int8", transport=httpx.MockTransport(lambda r: httpx.Response(503, text="secret paper text")))
    with pytest.raises(EmbeddingError) as exc:
        await down.embed(["a"], "query")
    assert "503" in str(exc.value) and "secret" not in str(exc.value)

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
        assert c.get("/healthz").json() == {"status": "ok", "model": "m@1:int8"}
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
        assert c.get("/healthz").json() == {"status": "ok", "model": None}
        assert c.post("/embed", json={"texts": ["a"]}).status_code == 503


def test_server_does_not_import_app_config():
    import subprocess
    import sys

    code = "import sys; import app.embedder.server, app.embedder.fetch; sys.exit('app.core.config' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent.parent).returncode == 0


# ---------------------------------------------------------------- model download
def fake_files(tmp_path):
    blobs = {"onnx/model.onnx": b"graph", "onnx/model.onnx_data": b"weights" * 100, "onnx/tokenizer.json": b"{}"}
    files = tuple(
        spec.ModelFile(path, len(data), hashlib.sha256(data).hexdigest()) for path, data in blobs.items()
    )
    return blobs, files


def serving(blobs, requests):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        key = request.url.path.split(f"{spec.REVISION}/", 1)[1]
        return httpx.Response(200, content=blobs[key]) if key in blobs else httpx.Response(404)

    return httpx.MockTransport(handler)


def test_pinned_spec_is_well_formed():
    assert spec.REVISION == "5617a9f61b028005a4858fdac845db406aefb181"
    assert spec.BASE_URL == "https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181"
    assert {f.path for f in spec.FILES} == {
        "onnx/model.onnx", "onnx/model.onnx_data", "onnx/tokenizer.json", "onnx/Constant_7_attr__value",
    }
    assert all(len(f.sha256) == 64 and f.size > 0 for f in spec.FILES)


def test_fetch_downloads_verifies_and_is_idempotent(tmp_path):
    blobs, files = fake_files(tmp_path)
    requests: list[str] = []
    with httpx.Client(transport=serving(blobs, requests)) as client:
        target = fetch.ensure_model(tmp_path, "fp32", client, files)
        assert target == spec.model_dir(tmp_path) / "model.onnx" and target.read_bytes() == b"graph"
        assert len(requests) == 3 and requests[0].startswith("/BAAI/bge-m3/resolve/")
        fetch.ensure_model(tmp_path, "fp32", client, files)
        assert len(requests) == 3  # nothing to do the second time
        assert not list(tmp_path.rglob("*.part"))
        # a damaged file is fetched again
        (spec.model_dir(tmp_path) / "tokenizer.json").write_bytes(b"xx")
        fetch.ensure_model(tmp_path, "fp32", client, files)
        assert len(requests) == 4 and (spec.model_dir(tmp_path) / "tokenizer.json").read_bytes() == b"{}"


@pytest.mark.parametrize("tamper", ["content", "longer", "shorter"])
def test_fetch_rejects_wrong_bytes(tmp_path, tamper):
    blobs, files = fake_files(tmp_path)
    blobs["onnx/model.onnx_data"] = {
        "content": b"W" * 700, "longer": b"weights" * 100 + b"!", "shorter": b"weights" * 99
    }[tamper]
    with httpx.Client(transport=serving(blobs, [])) as client, pytest.raises(fetch.VerificationError):
        fetch.ensure_model(tmp_path, "fp32", client, files)
    assert not list(tmp_path.rglob("*.part"))
    assert not (spec.model_dir(tmp_path) / "model.onnx_data").exists()


def test_fetch_http_error_leaves_nothing(tmp_path):
    blobs, files = fake_files(tmp_path)
    del blobs["onnx/tokenizer.json"]
    with httpx.Client(transport=serving(blobs, [])) as client, pytest.raises(httpx.HTTPStatusError):
        fetch.ensure_model(tmp_path, "fp32", client, files)
    assert not list(tmp_path.rglob("*.part"))


def test_fetch_quantizes_for_int8(tmp_path, monkeypatch):
    blobs, files = fake_files(tmp_path)
    calls = []

    def fake_quantize(src: Path, dest: Path) -> None:
        calls.append((src.name, dest.name))
        dest.write_bytes(b"int8")

    monkeypatch.setattr(fetch, "quantize_int8", fake_quantize)
    with httpx.Client(transport=serving(blobs, [])) as client:
        target = fetch.ensure_model(tmp_path, "int8", client, files)
        fetch.ensure_model(tmp_path, "int8", client, files)
    assert target.name == "model_int8.onnx" and target.read_bytes() == b"int8"
    assert calls == [("model.onnx", "model_int8.onnx")]  # once


def test_quantize_int8_on_a_real_graph(tmp_path):
    w = np.random.default_rng(0).standard_normal((16, 16)).astype(np.float32)
    graph = helper.make_graph(
        [helper.make_node("MatMul", ["x", "w"], ["y"])], "g",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["b", 16])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, ["b", 16])],
        [numpy_helper.from_array(w, "w")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    src, dest = tmp_path / "model.onnx", tmp_path / "model_int8.onnx"
    onnx.save(model, src)
    fetch.quantize_int8(src, dest)
    assert dest.is_file() and not list(tmp_path.glob("*.part"))


def test_main_is_a_noop_unless_local(monkeypatch, tmp_path):
    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    for provider in ("none", "voyage", "openai"):
        monkeypatch.setenv("EMBEDDING_PROVIDER", provider)
        assert fetch.main() == 0
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_LOCAL_PRECISION", "fp16")
    assert fetch.main() == 1
