# Mac App Store — AI connectivity (MCP) build brief

**Read this after `MACOS-APP-STORE-BUILD-BRIEF.md`.** It is the detailed plan for
**one workstream the main brief deferred: AI-agent access (MCP) on the Mac App
Store build.** The owner has pulled this forward — it is now **in scope** for the
MAS edition. This document **supersedes** the main brief's "leave MCP OFF for MAS
v1" line.

> **Gate:** still do the Phase 0 spike first (prove a sandboxed PySide6 `.app`
> clears Apple's pipeline). Do not build MCP before the app itself is known to be
> MAS-acceptable — MCP is worthless if the app cannot ship.

---

## The good news: the MCP server already fits the sandbox model

Study these existing files first — you are **reusing**, not rewriting:

| File | What it already does |
|---|---|
| `app/mcp_server.py` | The MCP server (FastMCP, stdio). **Read/rate tools run locally; spend tools only *file an approval request*** a human accepts in the app, re-fetched from EasyPost. Gated by `settings.mcp_enabled`; spend gated by `settings.mcp_allow_spending` + ceilings. |
| `app/core/mcp_clients.py` | Detects MCP clients, generates the paste-in `config_snippet()` / `setup_markdown()`, and can auto-write a client's config (`install()`), backing up first. `server_command()` decides how a client launches the server (per build/platform). |
| `app/core/mcp_approvals.py` | The approval queue + audit + spend ceilings (the shared state a spend request lands in). |
| `app/ui/views/connect_agents_view.py` | The "Tools > Connect AI agents" UI (toggle, client list, snippet). |
| `app/services/mcp_runner.py` | How the app runs/embeds the server. |
| `app/config.py` | `MCP_SUPPORTED` (currently `flag OR STORE_BUILD`). |

The security model is exactly what Apple review wants to hear: the agent can
**shop rates and read**, but **cannot spend** — buying always requires a human to
approve inside the app. Preserve it unchanged.

---

## The one real problem to solve

On the Microsoft Store / direct builds, the AI client launches the helper and it
**shares the GUI's keyring credential and local SQLite database** because it runs
as the same user with the same app-data path. **Under the macOS App Sandbox that
sharing breaks:**

1. A sandboxed MAS app **cannot write another app's config file** (the AI
   client's `claude_desktop_config.json` is outside the app's container) — so
   `mcp_clients.install()` auto-write cannot work under MAS. Only the **copy-paste
   snippet** is available.
2. A helper launched by an external AI client runs **outside** the MAS app's
   sandbox, so by default it cannot reach the app's Keychain item or its
   container-private SQLite DB.
3. A sandboxed MAS app **cannot install a CLI on PATH** or download/launch an
   executable (guideline 2.5.2).

So the work is: **let a separately-distributed helper reach the app's data, and
present install + config as an instruction screen rather than an auto-write.**

---

## Chosen approach — shared App Group container + companion helper (full parity)

Build this. It reuses `app/mcp_server.py` almost verbatim and keeps **full tool
parity, including gated spend-with-in-app-approval**.

```
┌─────────────────────────────┐        shared App Group container
│  Easy-Post Desktop (MAS,     │   ~/Library/Group Containers/group.<team>.easypostdesktop/
│  sandboxed)                  │        ├── easypost_desktop.sqlite3   (data + approvals queue)
│  - writes DB + approvals     │◀──────▶└── credentials (or shared Keychain group)
│  - surfaces approval dialog  │
└─────────────────────────────┘
              ▲  same Team ID, same App Group entitlement
              │
┌─────────────────────────────┐
│  easypost-mcp helper          │  ← separately distributed (Homebrew + notarized
│  (app/mcp_server.py packaged) │     download), NOT via the App Store
│  - launched by the AI client  │
│  - reads shared DB + key      │
│  - files spend requests into  │
│    the shared approvals queue  │
└─────────────────────────────┘
```

Distributing the helper **outside** the App Store is App-Store-legal: the MAS app
only *describes* it and shows a config snippet — it never downloads or launches
it. Keep the config screen informational.

### Build steps

1. **App Group + data relocation.**
   - Define an App Group `group.<TeamID>.easypostdesktop`; add the
     `com.apple.security.application-groups` entitlement to the MAS app.
   - Under `MAS_BUILD`, point the app-data dir (see `app/config.py`
     `APP_DATA_DIR` / `DATABASE_PATH` / `SETTINGS_PATH`) at the **App Group
     container** (`FileManager.containerURL(forSecurityApplicationGroupIdentifier:)`
     via PyObjC, or read the fixed `~/Library/Group Containers/...` path) so the
     helper can reach the same SQLite DB and the approvals store
     (`app/core/mcp_approvals.py`).
   - **EasyPost key:** preferred is a **shared `keychain-access-groups`** entry
     (both signed same Team ID). If sharing a keychain item with the (non-
     sandboxed) helper proves troublesome, fall back to storing the key inside
     the App Group container (same-team, user-protected) — note this is a slight
     downgrade from Keychain and update `PRIVACY.md`/README for the MAS build
     accordingly.

2. **⚠️ Verify the sharing EARLY (a mini-spike, before any UI work).** Confirm a
   **same-Team-ID-signed helper** can actually read the MAS app's App Group
   container **and** the shared key. Start the helper as a **non-sandboxed
   Developer-ID** binary with the App Group (+ keychain-access-group) entitlement.
   If the non-sandboxed helper cannot join the keychain group, use the
   container-file key fallback, or make the helper sandboxed with the same group.
   **Do not build the screens until this is proven** — it is the one uncertain
   mechanic. If it cannot be made to work at all, drop to the Fallback below.

3. **Package `app/mcp_server.py` as the `easypost-mcp` helper for macOS.**
   A notarized standalone binary (PyInstaller or py2app), signed with the **same
   Team ID**, entitled with the App Group (+ keychain group if used). It simply
   runs `mcp.run()` against the shared data. Distribute via:
   - a **Homebrew tap** (`brew install spencerfields/tap/easypost-mcp`), and
   - a **notarized download** on `easy-post.spencerfields.com`.
   Not through the App Store.

4. **`mcp_clients.server_command()` — add a MAS branch.** Return how a client
   launches the installed helper (the Homebrew binary name on PATH, e.g.
   `("easypost-mcp", [])`, mirroring the `STORE_BUILD` alias branch). Under
   `MAS_BUILD`, **disable the client-config auto-write** (`install()`): the
   sandbox forbids writing another app's file, so the MAS "Connect AI agents"
   view offers the **copy-paste snippet + install instructions only**.
   `config_snippet()` / `setup_markdown()` already generate the text — reuse them.

5. **MAS variant of `connect_agents_view.py`.** Under `MAS_BUILD`, the screen:
   - explains AI-agent access uses the free companion helper;
   - shows the install commands (Homebrew one-liner + download link);
   - shows the copy-paste MCP config for each detected client (no auto-write
     button);
   - keeps the existing **Enable AI agent access** toggle (`settings.mcp_enabled`),
     the **spend** toggle + ceilings, and — unchanged — the **in-app approval
     queue** that surfaces requests the helper files into the shared store.

6. **Enable it:** in `app/config.py` make `MCP_SUPPORTED` also true under
   `MAS_BUILD` (currently `flag OR STORE_BUILD` → `... OR MAS_BUILD`).

7. **Tests:** extend `tests/test_mcp_clients_store.py`-style tests for the MAS
   `server_command()` and that MAS disables config auto-write; the server tools +
   guards are already covered. Keep `pytest tests/ -q` green.

8. **App Review notes:** state that the companion helper is a **separate, free,
   optional** tool the user installs themselves; the MAS app **does not download
   or execute code**; the AI-agent screen is informational; and every spend
   requires in-app human approval.

---

## Fallback (only if the sharing mini-spike in step 2 fails)

Ship the helper with its **own one-time EasyPost key setup** (its own config /
keychain entry — the user pastes their EasyPost key into the helper once), and
expose **only the EasyPost-direct tools** that need nothing but the key:
`verify_address`, `quote_by_postal_code`, `shop_rates`, `lookup_hts_code`. This
still delivers the headline "an AI agent can shop rates and verify addresses on
my account". It **drops** the local-DB listings (`list_*`) and spend-with-approval
(those need the app's shared DB/queue). Document the reduced scope in the MAS
listing so expectations are set.

---

## Future option (do NOT build now — note for the owner)

A **remote MCP over an outbound Cloudflare Worker relay** (plan doc §3.1/§3.2a)
removes the helper and all data-sharing entirely: the sandboxed app itself is the
MCP backend, reached by the AI client through the Worker over an **outbound**
WebSocket the app dials. Full parity, native in-app approval, no second install —
but it is real server infrastructure (a Durable Object, per-account auth, a
remote MCP endpoint) and it also happens to restore real-time push and could
serve non-Apple platforms. It is the elegant end-state; build it deliberately
later, with the owner (it touches the Cloudflare Worker, which is managed from the
Windows side). Do not start it as part of this workstream.

---

## Definition of done (MCP workstream)

- [ ] Sharing mini-spike (step 2) proven, or Fallback adopted and documented.
- [ ] `easypost-mcp` helper packaged, notarized, installable via Homebrew + download.
- [ ] MAS `connect_agents_view` shows install + copy-paste config (no auto-write).
- [ ] `mcp_enabled` toggle, spend ceilings, and in-app approval all work end-to-end
      with the helper against the shared store (or the reduced fallback set).
- [ ] `MCP_SUPPORTED` true under `MAS_BUILD`; `pytest tests/ -q` green.
- [ ] App Review notes written; `PRIVACY.md`/README updated for the MAS key store.
- [ ] Progress + any owner actions recorded in `PROGRESS.md` / `OWNER-ACTIONS.md`
      in this workspace folder.
