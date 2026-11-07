# Releasing

A release is a `vX.Y.Z` tag. The installer installs the latest one, so a tag is what users get; `main` is only for people who ask for it (`BIBLIOFLOW_REF=main`). The site ([biblioflow-web](https://biblioflow.pathvector.dev)) describes the latest tag.

Roles: whoever prepares the release (a person or an agent session) opens the release PR. The maintainer pushes the tag and publishes the GitHub Release.

- [Versioning](#versioning)
- [1. Prepare (release PR)](#1-prepare-release-pr)
- [2. Checklist before tagging](#2-checklist-before-tagging)
- [3. Tag and publish](#3-tag-and-publish)
- [4. After publishing](#4-after-publishing)
- [Rolling back](#rolling-back)
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

**1.0.0** when all of these hold:
- the in-app AI features (Phase 3d and 4b) are done;
- `/api/v1` and the MCP tools are documented and frozen (OpenAPI published);
- CI tests upgrading from the previous release (migrations and `restore.sh` on its data);
- it has been used by a real lab for at least four weeks without losing data;
- no known vulnerabilities of high severity or above.

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
- [ ] **CI green** on that commit: backend, frontend, mcp, compose, installer, version, audit (no high or critical in production dependencies), licenses.
- [ ] **Fresh install** of the commit with a separate `.env` (`BF_INSTANCE`, `COMPOSE_PROJECT_NAME`, `FRONTEND_PORT`; never against the dev stack): `BIBLIOFLOW_REF=<commit>`, answer both yes and no to semantic search.
- [ ] **Upgrade** from the previous tag: start the previous tag with some papers, deploy the new commit (`scripts/deploy.sh <commit>`), check that migrations ran and the papers, files and search still work; `scripts/restore.sh --check` on a backup taken by the old version.
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

## If a secret or personal detail was published

1. Revoke or rotate the secret first (PAT, Cloudflare tokens, LLM keys, database and JWT secrets). A rewritten history does not undo a leak: forks, caches and clones keep it.
2. Remove it from the files in a normal PR.
3. Only with the maintainer's approval: rewrite the history (see the 2026-11-05 recreation in AGENTS.md) or accept it and add the commit to `KNOWN_MESSAGES` in `scripts/check-private-info.py` with the reason.

## Changelog

[`CHANGELOG.md`](CHANGELOG.md) uses [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with an extra **Upgrade notes** section for anything an operator has to do.

- **Who and when**: the pull request that makes the change adds one to three lines under `[Unreleased]`, written for users, not for reviewers. An agent session that merged a change without a line adds it before the session ends.
- **CI**: a pull request that does not touch `CHANGELOG.md` fails the `changelog` job, unless it has the `skip-changelog` label (for CI-only, test-only or typo changes) or comes from Dependabot.
- **At release**: `[Unreleased]` becomes the version's section, and that section is the release notes.
