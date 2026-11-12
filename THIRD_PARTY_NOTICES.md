# Third-party notices

BiblioFlow itself is MIT-licensed (see `LICENSE`). The source repository contains no third-party code, except the
citation style and locale files described under "Citation styles and locales" below.
The Docker images built from it, and the images `docker-compose.yml` pulls, contain the software below.
All of it is used unmodified.

`scripts/check-licenses.py` runs in CI (the Audit workflow) on every pull request. It refuses
production dependencies under GPL, AGPL, SSPL, BUSL, Elastic, Commons Clause or non-commercial terms.

## Components that need attribution

| Component | Where | License | Source |
|---|---|---|---|
| PDF.js (`pdfjs-dist`, copied to `public/pdf.worker.min.mjs` at build time) | frontend image | Apache-2.0 | https://github.com/mozilla/pdf.js |
| libvips (bundled by `sharp` as `@img/sharp-libvips-*`, dynamically linked) | frontend image | LGPL-3.0-or-later | https://github.com/libvips/libvips |
| caniuse-lite (dependency of Next.js) | frontend image | CC-BY-4.0 | https://github.com/browserslist/caniuse-lite |
| certifi | backend image | MPL-2.0 | https://github.com/certifi/python-certifi |
| tqdm (dependency of `tokenizers` via `huggingface-hub`; the code path that uses it is never run) | backend image | MPL-2.0 AND MIT | https://github.com/tqdm/tqdm |
| bibtexparser (reads BibTeX files in the import; used unmodified, dual-licensed, used here under its BSD option) | backend image | LGPL-3.0 or BSD-3-Clause | https://github.com/sciunto-org/python-bibtexparser |
| citeproc-py (formats the bibliography; Copyright 2011-2013 Brecht Machiels. All rights reserved.) | backend image | BSD-2-Clause | https://github.com/citeproc-py/citeproc-py |
| lxml (XML parsing for citeproc-py) | backend image | BSD-3-Clause | https://github.com/lxml/lxml |
| Pillow (checks and re-encodes the cropped image of explain-region; Copyright (c) 2010 Jeffrey A. Clark and contributors, Copyright (c) 1997-2011 by Secret Labs AB, Copyright (c) 1995-2011 by Fredrik Lundh and contributors) | backend image | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |

The other npm and Python production dependencies are under MIT, BSD, Apache-2.0, ISC, 0BSD, PSF,
Unlicense or MIT-0. Their license texts are in each package (`node_modules/<name>/LICENSE`, or
`site-packages/<name>-<version>.dist-info/` in the backend image).

## Citation styles and locales

`backend/app/services/csl/styles/` and `backend/app/services/csl/locales/` contain files copied unchanged from the
Citation Style Language project: the styles APA 7th edition, IEEE, Chicago Manual of Style (author-date), Nature,
NLM/Vancouver (`nlm-citation-sequence`) and ACM SIG Proceedings, from https://github.com/citation-style-language/styles,
and the `en-US` and `ja-JP` locale files from https://github.com/citation-style-language/locales. They are licensed under
[Creative Commons Attribution-ShareAlike 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Each file keeps its own
header with the authors and contributors of that style and the license notice; the commit and the download URLs are
recorded in `backend/app/services/csl/README.md`. The ShareAlike condition applies to these files (and to any
adaptation of them), not to the rest of BiblioFlow, which only reads them as data at run time.

The expected outputs in `backend/tests/fixtures/csl/expected.json` were produced once with citeproc-js
(CPAL-1.0 OR AGPL-1.0), run from a scratch install outside the repository. citeproc-js is not part of BiblioFlow,
its images or its dependencies; only the text it produced is committed.

## Embedding model and runtime

Semantic search runs BAAI/bge-m3 on ONNX Runtime. The model is **not** part of the repository or of any image:
`biblioflow-model-init` downloads the files from https://huggingface.co/BAAI/bge-m3 (pinned revision, sha256-verified)
into a Docker volume on first start, and quantizes them to int8 there. bge-m3 is MIT-licensed.

The repository's GitHub Releases named `model-bge-m3-int8-*` redistribute an int8 quantization of the bge-m3 weights
and its tokenizer, together with bge-m3's MIT `LICENSE` (Copyright (c) BAAI) as a release asset.

| Component | Where | License |
|---|---|---|
| BAAI/bge-m3 (model weights and tokenizer, downloaded at run time) | `models` volume | MIT |
| onnxruntime | backend image | MIT |
| onnx, tokenizers, huggingface-hub, hf-xet, ml-dtypes | backend image | Apache-2.0 |
| numpy, protobuf, fsspec | backend image | BSD-3-Clause |

## Container images used by docker-compose.yml

| Image | License |
|---|---|
| `pgvector/pgvector` (PostgreSQL + pgvector) | PostgreSQL License |
| `valkey/valkey` | BSD-3-Clause |
| `caddy` | Apache-2.0 |
| `cloudflare/cloudflared` (optional profile) | Apache-2.0 |
| `tailscale/tailscale` (optional profile) | BSD-3-Clause |
| `node`, `python` (base images) | MIT (image definitions); Node.js MIT, Python PSF-2.0 |

The Alpine and Debian base layers also contain OS packages under their own licenses, some of them
GPL (for example BusyBox). They are used unmodified, and their sources are available from Alpine
and Debian.
