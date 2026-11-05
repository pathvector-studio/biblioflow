# Third-party notices

BiblioFlow itself is MIT-licensed (see `LICENSE`). The source repository contains no third-party code.
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

The other npm and Python production dependencies are under MIT, BSD, Apache-2.0, ISC, 0BSD, PSF,
Unlicense or MIT-0. Their license texts are in each package (`node_modules/<name>/LICENSE`, or
`site-packages/<name>-<version>.dist-info/` in the backend image).

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
