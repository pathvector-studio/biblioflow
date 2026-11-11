# Contributing to BiblioFlow

Thank you for helping. Bug reports, fixes, documentation and ideas are all welcome.

- **Questions and ideas**: [Discussions](https://github.com/pathvector-studio/biblioflow/discussions).
- **Bugs and concrete feature requests**: [open an issue](https://github.com/pathvector-studio/biblioflow/issues/new/choose) with the form.
- **Security problems**: never in public. Follow [SECURITY.md](SECURITY.md).

Be respectful and welcoming, criticise ideas and not people, and do not harass anyone.

## Before you write code

For anything larger than a small fix, open an issue (or comment on an existing one) first and say what you plan to do. Some designs are settled on purpose (see "確定済みの設計判断" in [AGENTS.md](AGENTS.md), and [`specs/`](specs/)); agreeing on the approach first saves you a rewrite.

New dependencies need a reason in the issue. Production dependencies must not be GPL, AGPL, SSPL, BUSL, Elastic, Commons Clause or non-commercial (CI's `scripts/check-licenses.py` refuses them).

## Development setup

You need Docker with Compose v2, git, Node.js 22, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/pathvector-studio/biblioflow.git && cd biblioflow
git config core.hooksPath .githooks       # checks staged files for personal details before each commit
cp .env.example .env                       # then replace every change-me-* value: openssl rand -hex 32
docker compose up -d --build
docker compose exec biblioflow-api python -m app.cli create-user --email you@example.com --admin
```

Open http://localhost:3000. Every URL you open the app at must be listed in `CORS_ALLOWED_ORIGINS`, or writes fail with 403.

The maintainer's own setup (secrets kept outside the checkout, 1Password references) is in [LOCAL_DEV.md](LOCAL_DEV.md), in Japanese; you do not need it.

## Tests

Run what your change touches; CI runs all of it.

```bash
# backend (needs the stack's database and Valkey)
INSTALL_DEV=true docker compose build biblioflow-api
docker compose run --rm --no-deps -v "$PWD/backend/app:/app/app:ro" -v "$PWD/backend/tests:/app/tests:ro" \
  -v "$PWD/backend/alembic:/app/alembic:ro" -e ENABLE_DOCS=false biblioflow-api pytest -q -p no:cacheprovider

# frontend
(cd frontend && npm ci && npm run lint && npm run typecheck && npm run build)

# MCP server and browser extension
uv run --directory mcp pytest
node --test 'extension/test/*.test.js'

# no personal details in the files
python3 scripts/check-private-info.py
```

Integration tests must not be skipped: CI fails when pytest reports a skip.

## Pull requests

- One change per pull request, with tests for it. Say in the description what you checked by hand and what you could not check.
- Add one to three lines for users under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) (Added, Changed, Fixed, Security, and **Upgrade notes** for new `.env` keys or manual steps). Pull requests that only touch CI, tests or typos are labelled `skip-changelog` by the maintainer instead.
- User-facing text in the web UI goes into both `frontend/lib/i18n/ja.ts` and `en.ts` (`npm run typecheck` fails when one is missing). If you do not write Japanese, leave the English text in `ja.ts` and say so; it will be translated.
- Keep the public repository free of personal details: no LAN addresses, `*.local` host names, home-directory paths, private e-mail addresses or tailnet names, in files or commit messages. Use `192.168.1.10`, `papers.example.com` and `you@example.com` in examples. CI checks this, and so does the git hook above.
- Text that comes from papers and reaches a language model goes inside `<paper_context>`, never into the system prompt. All outgoing HTTP goes through `app/core/net.py`. These and the other rules in AGENTS.md are checked in review.
- `main` is protected: a pull request is merged (squashed) after CI is green and the maintainer approved it. To bring a branch up to date, merge `main` into it rather than rebasing.

## License

BiblioFlow is MIT-licensed. By contributing, you agree that your contribution is licensed under the same [MIT License](LICENSE).
