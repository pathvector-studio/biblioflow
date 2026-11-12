# Releasing

A release is a `vX.Y.Z` tag. The installer installs the latest one, so a tag is what users get; `main` is only for people who ask for it (`BIBLIOFLOW_REF=main`). The site ([biblioflow-web](https://biblioflow.pathvector.dev)) describes the latest tag.

Roles: whoever prepares the release (a person or an agent session) opens the release PR. The maintainer pushes the tag and publishes the GitHub Release.

- [Versioning](#versioning)
- [1. Prepare (release PR)](#1-prepare-release-pr)
- [2. Checklist before tagging](#2-checklist-before-tagging)
- [3. Tag and publish](#3-tag-and-publish)
- [4. After publishing](#4-after-publishing)
- [Rolling back](#rolling-back)
- [Security](#security)
- [If a secret or personal detail was published](#if-a-secret-or-personal-detail-was-published)
- [Changelog](#changelog)

## Versioning

[Semantic Versioning](https://semver.org/). The version is set in one place, [`VERSION`](VERSION). `scripts/version.py set X.Y.Z` writes every copy (pyproject, lock files, package.json, `app.__version__`, the README status line), and CI fails when a copy drifts or when a pushed tag is not `v$(cat VERSION)`.

The public interface is what other people's setups depend on: the HTTP API under `/api/v1`, personal access tokens and their scopes, the MCP tools, `.env` keys, the storage layout, the database (through migrations), backups, the compose services and volumes, and `install.sh` options.

| Bump | When | Examples |
|---|---|---|
| **MAJOR** | A change that breaks the public interface | Removing, renaming or retyping a field of `/api/v1` (e.g. `/papers/light`, or `/search` no longer returning an array, which the MCP server relies on); existing tokens stop working; removing or renaming an MCP tool or argument; removing or renaming an `.env` key without a transition; a new meaning for the default path or a template variable; a migration that needs manual steps or cannot be undone; older backups that `restore.sh` cannot restore; renaming a compose service or volume |
| **MINOR** | Backward-compatible additions | New endpoints, optional parameters or response fields; new MCP tools; new `.env` keys with defaults; migrations that run by themselves (new columns or tables); new embedding providers (re-embedded automatically); new profiles; deprecations (the removal comes in a MAJOR) |
| **PATCH** | Fixes and internal changes | Bug fixes; security fixes that keep the behaviour; documentation; refactoring; patch and minor dependency updates; UI wording |

**While the version is 0.x**: a breaking change bumps **MINOR** and must have a "Breaking" line and Upgrade notes in the changelog; features bump MINOR; fixes bump PATCH. 0.x releases are called "preview" in the docs, but they are published as normal GitHub releases (not "Pre-release"), so that the latest one is marked Latest.

**1.0.0** when all of these hold (decided 2026-11-11; the status column is updated by the PR that completes an item):

| # | Criterion | How it is checked | Status |
|---|---|---|---|
| 1 | **Migration works**: a real Zotero library (Zotero RDF with files, exported from Zotero desktop) comes in completely in one run. Mendeley is not a 1.0 condition (decided 2026-11-12): its Reference Manager exports BibTeX / RIS, which are covered, and a dedicated importer is future work | Item and PDF counts match the source; no lost tags, collections or notes; recorded in `specs/export-import.md` | Not yet (tests use hand-made fixtures) |
| 2 | **Citations out**: a bibliography in a CSL style can be produced (track D, output only; Word / Google Docs add-ins are not required) | API and UI; the output checked against citeproc for a few common styles | Implemented, in [Unreleased] |
| 3 | **Compatibility promise**: `/api/v1`, PAT scopes, the MCP tools, `.env` keys and the storage layout are documented and frozen under semver | OpenAPI published with the release; CI installs the previous tag, upgrades it, and runs `restore.sh --check` on its backup | Not yet (the upgrade is a manual checklist item) |
| 4 | **Security process**: `SECURITY.md`, private vulnerability reporting, code scanning and container image scanning run; no open finding of high severity or above | GitHub settings and CI; the security section of this file | Not yet |
| 5 | **Proven in use**: at least four weeks of daily use on the maintainer's production instance without losing data; one real restore from a backup; at least three users outside the maintainer | Notes in `STATUS.md`; restore done with `scripts/restore.sh --yes` on production data | Not yet |
| 6 | **Installable clients**: the browser extension is in the Chrome Web Store and on AMO, and `biblioflow-mcp` is on PyPI | Store and PyPI pages | Not yet |

Already done and kept as conditions: the in-app AI features (Phase 3d and 4b).

A 1.0 release also follows the normal checklist below. After 1.0, a breaking change of anything in criterion 3 is a MAJOR release.

**Pre-releases**: no alpha or beta. Use `vX.Y.Z-rc.N` only for a release that changes migrations or the install flow. The installer skips rc tags, so testers opt in with `BIBLIOFLOW_REF=vX.Y.Z-rc.N`. An rc is published as a GitHub "Pre-release".

## 1. Prepare (release PR)

On a branch `release/vX.Y.Z` from an up-to-date `main`:

```bash
scripts/version.py set X.Y.Z
```

In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a new empty `## [Unreleased]` above it, update the compare links at the bottom, and check that the Upgrade notes cover every new `.env` key and manual step. Open the PR and merge it when CI is green.

## 2. Checklist before tagging

On the merged release commit:

- [ ] **Secrets and personal details**: CI's `private-info` job passed on the push to main (all files and every commit's e-mails and messages, plus gitleaks on the whole history).
- [ ] **CI green** on that commit: backend, frontend, mcp, compose, installer, version, zizmor, audit (no high or critical in production dependencies), licenses, CodeQL, image scan.
- [ ] **Code scanning**: no open alert of high or critical severity from CodeQL or our own images (`gh api 'repos/pathvector-studio/biblioflow/code-scanning/alerts?state=open&severity=high'`, and `critical`). Alerts for the third-party images in `docker-compose.yml` are reviewed: take a newer tag when one fixes them.
- [ ] **Fresh install** of the commit with a separate `.env` (`BF_INSTANCE`, `COMPOSE_PROJECT_NAME`, `FRONTEND_PORT`; never against the dev stack): `BIBLIOFLOW_REF=<commit>`, answer both yes and no to semantic search.
- [ ] **Upgrade** from the previous tag the way users do it: install the previous tag (`BIBLIOFLOW_REF=vX.Y.(Z-1)`, separate `.env` as above), add papers (with PDFs and a wishlist entry), `scripts/backup.sh`, then run the installer again with `BIBLIOFLOW_REF=<commit>`. Check that the session survives, migrations reached head, papers, PDFs (`/papers/{id}/file`) and search still work, and `scripts/restore.sh --check` restores the old backup. (An installer checkout is shallow and follows tags only, so `scripts/deploy.sh origin/main` does not work there.)
- [ ] The changelog section reads correctly as release notes.
- [ ] For an rc: say what testers should look at.

## 3. Tag and publish

Done by the maintainer (the release commit is on `main`):

```bash
git switch main && git pull
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

The tag push runs CI again, including `version.py check --tag`. When it is green:

```bash
awk '/^## \[X.Y.Z\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md > /tmp/notes.md
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file /tmp/notes.md --latest install.sh
```

For an rc, use `--prerelease --latest=false` instead of `--latest`. Attaching `install.sh` lets the site serve the released installer. Model releases (`model-bge-m3-…`) are never Latest (the workflow passes `--latest=false`).

Do not move or delete a published tag. A mistake is fixed in the next PATCH.

## 4. After publishing

- [ ] `curl -sSL https://biblioflow.pathvector.dev/install.sh | BIBLIOFLOW_NO_START=1 bash` picks `vX.Y.Z`.
- [ ] Production: `scripts/deploy.sh vX.Y.Z` in the production clone; the script checks `/api/v1/readyz` through the gateway.
- [ ] Site (biblioflow-web): update `SITE.version` in `src/config.ts`, and remove "Coming soon" from features that shipped. Pushing to its `main` deploys it.
- [ ] Announce, linking the GitHub Release.

## Rolling back

Deploy the previous tag: `scripts/deploy.sh vX.Y.(Z-1)`. Migrations are not undone by rolling back the code. If the new version migrated the database, restore the backup that `deploy.sh` took (`scripts/restore.sh --yes <backup>`). Then fix the problem and release a PATCH.

## Security

Reports arrive through GitHub's private vulnerability reporting ([SECURITY.md](SECURITY.md) sets the response times: acknowledge within 7 days, assess within 14, a fixed release within 30 for high or critical).

1. **Triage** in the draft advisory GitHub creates for the report: reproduce, set the severity (CVSS), the affected versions (`< X.Y.Z`) and the patched version. Reply to the reporter.
2. **Fix privately**: from the advisory, create a temporary private fork and fix it there, with a test that fails before the fix. Do not push the fix to a public branch or describe it in a public PR, issue or commit message before the release.
3. **Release**: merge the fix from the private fork into `main` and release a PATCH as usual (release PR, checklist, tag). The changelog line goes under `### Security` and says what was affected and who should update, without a working exploit.
4. **Publish the advisory** once the release is out, request a CVE from GitHub when the issue has one, and credit the reporter unless they declined. Production is updated with `scripts/deploy.sh` like any release.

If the issue is being exploited or is already public, skip the wait: release the fix as soon as it is ready and publish the advisory with it.

## If a secret or personal detail was published

1. Revoke or rotate the secret first (PAT, Cloudflare tokens, LLM keys, database and JWT secrets). A rewritten history does not undo a leak: forks, caches and clones keep it.
2. Remove it from the files in a normal PR.
3. Only with the maintainer's approval: rewrite the history (see the 2026-11-05 recreation in AGENTS.md) or accept it and add the commit to `KNOWN_MESSAGES` in `scripts/check-private-info.py` with the reason.

## Changelog

[`CHANGELOG.md`](CHANGELOG.md) uses [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with an extra **Upgrade notes** section for anything an operator has to do.

- **Who and when**: the pull request that makes the change adds one to three lines under `[Unreleased]`, written for users, not for reviewers. An agent session that merged a change without a line adds it before the session ends.
- **CI**: a pull request that does not touch `CHANGELOG.md` fails the `changelog` job, unless it has the `skip-changelog` label (for CI-only, test-only or typo changes) or comes from Dependabot.
- **At release**: `[Unreleased]` becomes the version's section, and that section is the release notes.
