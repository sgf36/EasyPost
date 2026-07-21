# Connecting an AI agent to Easy-Post Desktop

Easy-Post Desktop ships an MCP (Model Context Protocol) server, so an AI
agent — Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, or anything
else that speaks MCP — can work with your shipping data directly.

The app can write the configuration for you: open **Connect AI agents** in the
sidebar, tick **Enable AI agent access**, and press **Connect** next to a
detected client. It asks before editing any file, backs up what is already
there, and merges rather than replaces. This document covers the manual route
and, more importantly, what the agent is and is not allowed to do.

> **Direct download only.** The Microsoft Store build cannot run the MCP
> server. A Store package cannot reliably have another application launch a
> helper process out of its install location, nor write into other programs'
> configuration files. The app says so on the Connect AI agents page rather
> than offering a button that would not work. Everything else is identical
> between the two builds — get the direct download from
> [easy-post.spencerfields.com](https://easy-post.spencerfields.com) if you
> want this feature.

---

## Manual configuration

Add the server to your client's MCP configuration. Most clients nest servers
under `mcpServers`; VS Code uses `servers`.

```json
{
  "mcpServers": {
    "easypost-desktop": {
      "command": "C:\\Program Files\\Easy-Post Desktop\\easypost-mcp.exe",
      "args": []
    }
  }
}
```

The `command` is the `easypost-mcp` executable that sits beside the main
application binary — `easypost-mcp.exe` on Windows, `easypost-mcp` inside
`Easy-Post Desktop.app/Contents/MacOS/` on macOS. **Connect AI agents →
Manual setup** prints the exact path for your installation, so copy it from
there rather than typing the example above. Running from source instead, use
your interpreter with `["-m", "app.mcp_server"]`.

| Client | Config file | Key |
|---|---|---|
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` | `mcpServers` |
| Claude Code | `~/.claude.json` | `mcpServers` |
| Cursor | `~/.cursor/mcp.json` | `mcpServers` |
| VS Code (Windows) | `%APPDATA%\Code\User\mcp.json` | `servers` |
| VS Code (macOS) | `~/Library/Application Support/Code/User/mcp.json` | `servers` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |

Restart the client afterwards. The server refuses every call while **Enable AI
agent access** is off, whatever is left in a client's configuration — so
unticking the box is a complete off switch and does not require undoing the
config.

---

## What the agent can do

Ten tools run immediately, because none of them spends money or changes
anything:

| Tool | Does |
|---|---|
| `get_status` | Reports mode, whether access is enabled, and current ceilings |
| `list_addresses` | Address book |
| `list_shipments` | Recent shipments |
| `list_trackers` | Tracked parcels and their latest status |
| `list_claims` | Filed claims |
| `list_pickups` | Scheduled collections |
| `verify_address` | Checks an address against EasyPost |
| `lookup_hts_code` | Tariff-code search |
| `quote_by_postal_code` | Postal-code-only rate estimate |
| `shop_rates` | Full rate comparison for a shipment |

## What the agent cannot do

Four tools touch money, and none of them completes on its own:

| Tool | Does |
|---|---|
| `request_label_purchase` | Files a request to buy a label |
| `request_pickup_purchase` | Files a request to buy a pickup |
| `request_refund` | Files a request to refund a label |
| `check_approval` | Reports what a person decided |

Each one returns a request ID and stops. The purchase happens only after a
person approves it on the **Connect AI agents** page in the app.

---

## The safety model

The threat this is built against is prompt injection: an agent that has read a
malicious tracking note, a poisoned web page, or a crafted email, and is now
acting on instructions that are not yours. That agent is not assumed to be
lying deliberately — it is assumed to be capable of being wrong about what it
is asking for. Five properties follow from that.

**1. Approval happens out of band.** The agent cannot approve its own request.
The confirmation is a dialog in Easy-Post Desktop, on your screen, in a window
the agent has no channel into. No tool exists that approves anything.

**2. The summary is re-fetched, not repeated.** What the approval card shows —
carrier, service, amount, both addresses — is fetched from EasyPost using only
the identifiers the agent supplied. Nothing the agent *said* about the
purchase is displayed. An agent that claims a rate is $4 when EasyPost says
$92 produces a card reading $92. The rate is also checked against the
shipment: an agent cannot attach a cheap rate ID from one shipment to a
different, expensive one.

**3. Ceilings refuse, they do not prompt.** Anything above the per-purchase or
daily limit is rejected outright and never reaches you. This matters because a
prompt that appears often enough eventually gets approved by reflex; a limit
that refuses cannot be worn down. Both limits default to off (spending
disabled entirely) and are yours to set.

**4. Text is neutered before it is shown.** Control characters are stripped
from every string that reaches the approval card, so a value cannot fake extra
lines, overwrite the amount, or forge an "already approved" label.

**5. Every check runs twice.** Enabled state, mode, spending permission and
ceilings are all re-evaluated at the moment of execution, not only when the
request was filed. Disabling access, switching to test mode, or lowering a
limit invalidates a request that is already waiting. Requests also expire
after an hour, so an approval queue left open overnight is not a standing
authorisation.

Two habits are worth keeping alongside all of that:

- **If you did not expect an approval request, reject it.** That is the case
  the whole design exists to catch.
- **Leave spending off unless you are actively using it.** Read-only access is
  useful on its own, and it cannot cost anything.

Every filed request, approval, rejection and refusal is written to a local
audit table, so what an agent asked for is reviewable after the fact.

---

## Troubleshooting

**The client shows the server as failed.** Check the `command` path resolves —
it must point at the `easypost-mcp` executable, not the main app. Launch it in
a terminal: it should sit and wait for input rather than exit.

**Tools return "AI agent access is disabled".** The tickbox on the Connect AI
agents page is off. That is the intended behaviour of the off switch.

**A purchase tool returns a request ID and nothing happens.** That is correct.
Open Easy-Post Desktop and approve it. The queue polls, so it appears within a
few seconds.

**"Connected" but the client does not see it.** Restart the client. MCP
configuration is read at startup.

**The app refused to write a config.** The existing file is not valid JSON.
It belongs to another application and may hold settings you cannot easily
reconstruct, so the app leaves it alone. Fix the JSON, or add the snippet by
hand.
