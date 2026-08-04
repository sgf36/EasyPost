# Easy-Post Desktop — remote MCP relay (Cloudflare Worker)

A transport bridge that lets an AI client reach a **sandboxed** desktop app's MCP
server with **no inbound port on the app**. This is what makes AI-agent access
work on the Mac App Store build, where the App Sandbox forbids the local-
subprocess/stdio design used on the direct-download and Microsoft Store builds.

```
AI client ──MCP over Streamable HTTP──▶  RELAY (this Worker + Durable Object)  ◀──outbound WebSocket──  desktop app
```

The relay **never sees the EasyPost API key and never runs a tool** — it only
carries JSON-RPC frames between the two sides and matches responses to requests by
id. The desktop app executes every tool locally (the existing `app/mcp_server.py`),
and **spending still requires in-app human approval**. A leaked pairing token
exposes read/rate-shop access to that user's shipping data (never spend), only
while the app is online, and is revocable by regenerating the token in the app.

## Endpoints

Token is presented as `Authorization: Bearer <token>` **or** as a
`/t/<token>/…` path prefix (for clients that cannot set headers, and for the
desktop app's WebSocket).

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/health` | anyone | liveness (`{"ok":true}`) |
| GET/WS | `/connect` | desktop app | WebSocket upgrade — the tool executor |
| POST | `/mcp` | AI client | JSON-RPC request/notification (Streamable HTTP) |
| GET | `/mcp` | AI client | SSE stream for server-initiated messages |

Both sides of a pairing route to the same Durable Object via
`idFromName(sha256(token))`, so they meet only if they share the token.

## Deploy

```bash
cd server/mcp-relay-worker
npm install
npx wrangler deploy          # requires login to the account that owns the app's Workers
```

Deploy prints the URL, e.g. `https://easypost-mcp-relay.<subdomain>.workers.dev`.
Health check: `GET /health`.

## Desktop-app contract (the app-side WebSocket client to build)

The app is the MCP **server**; the relay carries its protocol over a WebSocket the
app dials **outbound**. Implement in the app (cross-platform — used by every build
that opts into remote MCP, not just MAS):

1. **Generate a pairing token** once: `secrets.token_urlsafe(32)` (≥20 chars).
   Persist it. Offer a "regenerate" action (revokes the old pairing).
2. **Connect** `wss://<relay>/connect` with header `Authorization: Bearer <token>`
   (the Python `websockets` library supports `additional_headers`). Reconnect with
   backoff on drop. Send `{"type":"ping"}` periodically; expect `{"type":"pong"}`.
   On connect you receive `{"type":"relay.hello"}`.
3. **Serve MCP over the socket.** Each text frame from the relay is a JSON-RPC
   message *from the AI client* (`initialize`, `tools/list`, `tools/call`, …).
   Dispatch it to the same tool handlers as the stdio server and send the JSON-RPC
   response back over the socket **with the id unchanged** (the relay rewrites ids
   on the wire and restores the client's original id from its own map — your job
   is simply: reply with the id you received). Notifications (no `id`) get no
   reply. Server-initiated notifications you send are fanned out to the client's
   SSE stream.
   - Practical approach: reuse the `mcp` SDK's server request handling but feed it
     this WebSocket as the transport instead of stdio (a thin read-frame →
     dispatch → write-frame loop around the existing FastMCP tool registry), or
     hand-dispatch the handful of JSON-RPC methods to the existing tool functions.
4. **Gate + approval unchanged.** Honour `settings.mcp_enabled` (refuse when off)
   and route spend through `app/core/mcp_approvals.py` exactly as today — the app
   is executing locally, so the in-app approval dialog works natively.
5. **Show the user** the AI-client configuration: the relay URL plus the token
   (as a bearer header, or the `/t/<token>/mcp` URL form). This replaces the
   companion-helper install path from `MACOS-AI-CONNECTIVITY-BRIEF.md` when the
   relay is used.

### AI-client configuration (what the user pastes)

Header form (preferred):

```json
{
  "mcpServers": {
    "easypost-desktop": {
      "url": "https://easypost-mcp-relay.<subdomain>.workers.dev/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Path form (for clients without header support):
`https://easypost-mcp-relay.<subdomain>.workers.dev/t/<token>/mcp`

## Protocol notes / limits

- **The relay is MCP-agnostic** — it forwards frames and correlates by id. It does
  not implement `initialize`; that flows to the app, which answers as the MCP
  server. An `Mcp-Session-Id` is issued on the `initialize` response for transport
  bookkeeping but is not hard-enforced (kept lenient for client interop).
- **One app per token.** A new app WebSocket replaces a stale one. Multiple AI
  clients on one token are correlated safely (ids are namespaced internally).
- **App offline** → client requests return a clear JSON-RPC error (code −32001)
  rather than hanging. Per-request timeout is 30 s (−32002).
- **Routing state is in memory** in the Durable Object; if it evicts, the app's
  socket drops and it reconnects. No data is persisted.

## Status / what still needs a live test

Deployed and verified at the **transport level** with a synthetic harness (a fake
app WebSocket + a client POST, proving request→app→response round-trips and the
app-offline path). **Not yet tested against a real MCP client** (Claude Desktop)
or a real app-side WebSocket implementation — that end-to-end interop check is the
next step once the app-side client (contract above) exists. Streamable HTTP
session/SSE handling is deliberately lenient to maximise client compatibility;
revisit if a specific client needs stricter behaviour.
