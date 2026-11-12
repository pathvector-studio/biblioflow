# Bundled CSL styles and locales

Nothing here is fetched at run time. The files are copied unchanged from the Citation Style Language project and are
licensed under [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/); each file keeps its own header with its
authors and the license notice.

| Directory | Source repository | Commit |
|---|---|---|
| `styles/` | https://github.com/citation-style-language/styles | `af0aea3fffd44fe798cce9285598d0d5cf62ef23` (2026-11-06) |
| `locales/` | https://github.com/citation-style-language/locales | `8bef426f89f38c0789223c50e53ec2197bc50bb3` (2026-10-20) |

Files (raw URL = `https://raw.githubusercontent.com/<repository>/<commit>/<file>`):

- `styles/apa.csl`, `styles/ieee.csl`, `styles/chicago-author-date.csl`, `styles/nature.csl`, `styles/acm-sig-proceedings.csl`
- `styles/nlm-citation-sequence.csl`: "NLM/Vancouver: Citing Medicine 2nd edition (citation-sequence)". The repository has no
  independent style called `vancouver`; this is the parent of the Vancouver variants and is offered as `vancouver`.
- `locales/locales-en-US.xml`, `locales/locales-ja-JP.xml`

`styles.json` lists the styles the API offers (id, title, kind, file); `kind` follows each style's `citation-format` category.

To update: download the new files at a new commit, run `scripts/gen-csl-expected.mjs` (see its header; it needs a scratch
install of citeproc-js outside the repository), run `tests/test_csl_reference.py`, and change the commits above.
