"""bge-m3 on ONNX Runtime: tokenize, run, L2-normalise. No torch."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from app.embedder import spec

BATCH = 8  # texts per forward pass; bounds peak memory and the wait a query can see behind a bulk job


class OnnxEmbedder:
    def __init__(self, model_path: Path, tokenizer_path: Path, threads: int, max_tokens: int = spec.MAX_TOKENS):
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=max_tokens)
        pad_id = self.tokenizer.token_to_id("<pad>")
        self.tokenizer.enable_padding(pad_id=1 if pad_id is None else pad_id, pad_token="<pad>")
        self._lock = threading.Lock()  # one forward pass at a time; the tokenizer's padding state is shared too

    def _run(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            encodings = self.tokenizer.encode_batch(texts)
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            out = self.session.run(["sentence_embedding"], {"input_ids": ids, "attention_mask": mask})[0]
        out = np.asarray(out, dtype=np.float32)
        norm = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norm, 1e-12)

    def encode(self, texts: list[str]) -> np.ndarray:
        """Vectors in the order of ``texts``. Sorted by length first so a batch pads as little as possible;
        the lock is taken per batch, so a short query waits for at most one batch of a bulk request."""
        if not texts:
            return np.zeros((0, spec.DIMENSION), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        result = np.zeros((len(texts), spec.DIMENSION), dtype=np.float32)
        for start in range(0, len(order), BATCH):
            idx = order[start : start + BATCH]
            result[idx] = self._run([texts[i] for i in idx])
        return result


def threads_from_env() -> int:
    try:
        return max(1, min(64, int(os.environ.get("EMBEDDER_THREADS", "4"))))
    except ValueError:
        return 4
