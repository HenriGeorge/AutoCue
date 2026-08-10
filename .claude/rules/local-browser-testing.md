---
description: "Local-server browser testing on this Mac — the Claude in Chrome block and the DevTools/Playwright bypass. Always active."
globs: ["**/*"]
---

# Local browser testing on this Mac

Last updated: 2026-06-18 11:43

This Mac is UNMANAGED (no MDM, no managed Chrome policy, personal Google account).
So when the **Claude in Chrome** extension shows **"This site is blocked by your
organization's policy"** on a local dev server, it is NOT IT/MDM — it's the
account setting at claude.ai → Settings → **Claude in Chrome** (default behavior
"Block extension"; the allow-list governs the rest). The extension asks
`api.anthropic.com` to classify each URL and prints the generic "organization's
policy" string for any `org_policy: block` — even on a personal account. Per-site
"approved sites" grants do NOT override this account-level gate.

## Using the Claude in Chrome extension on a local app
- Add bare `localhost` to the allow-list (claude.ai → Settings → Claude in Chrome).
  A bare host covers all ports (matching is hostname + path, port-agnostic).
- `127.0.0.1` and any IP are REJECTED as "invalid domain" — so open local apps at
  `http://localhost:PORT`, not the IP, when driving with the extension.
- `*` only works as `*.domain` (subdomain wildcard); NOT a port wildcard (`:*`)
  and NOT a bare global `*`.

## Preferred for automation (Chrome DevTools MCP / Playwright)
These launch their OWN Chrome and never touch the extension — fully immune to the
block. Use `127.0.0.1` here (avoids IPv6/`localhost` resolution flakiness).
