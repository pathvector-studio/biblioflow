# Security policy

## Supported versions

Security fixes go into the latest minor release only. While BiblioFlow is 0.x, fixes are not backported to older minors: update to the latest release (see [Updating](README.md#updating)).

| Version | Supported |
|---|---|
| Latest minor (currently 0.3.x) | Yes |
| Older | No |

The browser extension and `biblioflow-mcp` follow the same version as the server.

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for a vulnerability.**

Report it privately through GitHub: [Security → Report a vulnerability](https://github.com/pathvector-studio/biblioflow/security/advisories/new). Include what you can of:

- the version or commit, and the deployment mode (LAN, Tailscale, Cloudflare Tunnel);
- the steps to reproduce, or a proof of concept;
- what an attacker gains, and what they need first (an account, a workspace role, a token scope, a crafted PDF or bibliography file).

Test only against your own installation. Do not access other people's data, and do not run denial-of-service or automated scanning against instances you do not own.

## What to expect

BiblioFlow is maintained by one person, so these are the times we can keep:

- **Acknowledgement** within 7 days.
- An assessment (accepted or not, and the severity) within 14 days.
- For **high or critical** issues, a fixed release within 30 days of the report. Lower severities are fixed in a following release.
- The advisory is published after the fixed release is out, with a CVE when it applies. You are credited in the advisory and the changelog unless you ask not to be.

There is no bug bounty.

## Scope

In scope: this repository: the API, the web UI, the worker and embedder, the gateway configuration, `install.sh` and the scripts, the browser extension and `biblioflow-mcp`.

Out of scope:

- Vulnerabilities in a language model or an external service you configured (LLM providers, embedding providers, Cloudflare, Tailscale). Report those to their vendors. Issues in **how BiblioFlow uses** them (for example, a paper's text escaping the `<paper_context>` delimiting, or a secret sent to a provider) are in scope.
- Setups changed from what ships: a compose file edited to publish the API, the database or Valkey directly instead of only the gateway, or containers run as root.
- Reports from automated scanners without a demonstrated impact, missing hardening headers on their own, and self-XSS.

## Security design

The protections BiblioFlow relies on (authentication, workspace isolation, file paths, upload checks, SSRF guard, prompt-injection delimiting, rate limits, container isolation) are described in [AGENTS.md](AGENTS.md) and the specs under [`specs/`](specs/). How releases handle a reported vulnerability is in [RELEASING.md](RELEASING.md#security).
