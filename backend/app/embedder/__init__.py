"""Local embedding service (bge-m3 on ONNX Runtime).

This package runs in its own containers (``biblioflow-model-init`` and ``biblioflow-embedder``) that get no
database, Redis or API-key secrets. It must therefore never import ``app.core.config`` (which needs them);
it reads its few settings straight from the environment.
"""
