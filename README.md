# Plex MCP Server

A self-hosted [Model Context Protocol](https://modelcontextprotocol.io) server that lets AI agents read and edit
your Plex Media Server: search libraries, fix metadata, tag items, and build collections and playlists.

It runs on the same Windows machine as Plex. Agents anywhere on your LAN connect over HTTP with a bearer token,
and local clients can also launch it over stdio.

Built on the official MCP Python SDK **v2** and [python-plexapi](https://github.com/pushingkarmaorg/python-plexapi).

---

## Tools

| Area | Tool | What it does |
|---|---|---|
| **Browse** | `server_info` | Server name/version, libraries, and which safety settings are on. Good first call. |
| | `list_libraries` | Libraries with IDs, agents, item counts, and folders. |
| | `search_library` | Title search plus advanced Plex filters (`genre`, `year>>`, `unwatched`, `addedAt>>: 30d`...), sorting, paging. |
| | `search_all` | Fuzzy search across every library, like the Plex search bar. |
| | `get_item` | Full metadata: fields, tags, ratings, locked fields, external IDs, file paths. |
| | `list_children` | Seasons/episodes, albums/tracks, or the ordered contents of a collection or playlist. |
| | `recently_added` | Newest items, per library or across all. |
| | `list_filter_options` | Valid filter fields, sort fields, and existing values (e.g. every genre). |
| | `list_collections`, `list_playlists` | Find collections and playlists by name. |
| | `find_matches`, `list_artwork` | Candidate agent matches; available posters/backgrounds. |
| **Metadata** | `edit_metadata` | Title, sort title, summary, tagline, studio, content rating, dates, ratings, edition, track/disc numbers. One request; fields locked by default. |
| | `edit_tags` | Add/remove genres, labels, collections, moods, styles, directors, writers, and more across many items. |
| | `set_field_locks` | Lock or unlock fields, tag lists, or artwork without changing values. |
| | `set_artwork` | Poster, background, logo, square art, or theme from a URL, a file on the Plex machine, or an existing option. |
| | `set_played` | Mark items watched/unwatched. |
| | `refresh_item`, `scan_library` | Refresh an item from its agent; scan a library or folder. |
| | `fix_match`, `unmatch_item` | Re-match or unmatch an item. |
| **Collections** | `create_collection` | Regular (from items) or smart (from filter rules), with mode and ordering. |
| | `update_collection` | Rename, describe, add/remove items, change smart rules, mode, order, Home/Recommended visibility. |
| | `move_item` | Reorder an item in a collection or playlist. |
| | `delete_collection` * | Delete a collection (not the media). Requires the exact title as confirmation. |
| **Playlists** | `create_playlist` | Regular (shows/albums expand to episodes/tracks) or smart. |
| | `update_playlist` | Rename, describe, append/remove items, change smart rules. |
| | `delete_playlist` * | Delete a playlist (not the media). Requires the exact title as confirmation. |

\* Only registered when `PLEX_MCP_ALLOW_DELETE=true`. With `PLEX_MCP_READ_ONLY=true`, only the Browse tools exist.
Every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`) so clients can ask before acting.

Nothing in this server deletes media files from disk.

---

## Install on the Plex machine (Windows)

**You need:** Python 3.10 or newer (`winget install Python.Python.3.13`, or python.org with "Add to PATH" checked).

1. Copy this folder to the Plex machine, e.g. `C:\plex-mcp`. A short path outside your user profile works best,
   because the startup task runs as SYSTEM.
2. Open **PowerShell as Administrator** and run:

   ```powershell
   cd C:\plex-mcp
   powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1
   ```

The installer:

- creates `.venv` and installs the server,
- writes `.env`, reading your Plex token from the registry when Plex runs under the same Windows account
  (otherwise it asks you to paste it) and generating a random MCP auth token,
- restricts `.env` so only you, Administrators, and SYSTEM can read it,
- tests the Plex connection,
- opens TCP 8765 for **Private** networks in Windows Firewall,
- registers a **"Plex MCP Server"** scheduled task that starts at boot and restarts on failure,
- prints the URL and bearer token for your agents.

Options: `-Port 9000`, `-SkipFirewall`, `-SkipTask`. To test in the foreground instead, run
`scripts\windows\run.ps1` (add `-ReadOnly` for a safe first look). To remove the task and firewall rule, run
`scripts\windows\uninstall.ps1`.

Check it from another PC: `curl http://PLEX-HOST:8765/health` should return `{"status":"ok"}`.

### Finding your Plex token

If the installer can't read it from the registry, use one of these:

- **Registry** (on the Plex machine, as the account Plex runs under):
  `Get-ItemPropertyValue "HKCU:\Software\Plex, Inc.\Plex Media Server" PlexOnlineToken`
- **Plex Web:** open any item → **⋯** → **Get Info** → **View XML**. The URL ends in `X-Plex-Token=...`.

Use the server owner's token so edits are allowed.

### Manual install (any OS)

```bash
python -m venv .venv
.venv/bin/pip install .            # Windows: .venv\Scripts\pip install .
cp .env.example .env               # then fill in PLEX_TOKEN and PLEX_MCP_AUTH_TOKEN
.venv/bin/plex-mcp --check         # test the Plex connection
.venv/bin/plex-mcp                 # serve HTTP on 0.0.0.0:8765/mcp
```

---

## Configuration

Settings come from environment variables or `.env` (see `.env.example`). Pass `--env-file PATH` to use a specific file.

| Variable | Default | Notes |
|---|---|---|
| `PLEX_URL` | `http://127.0.0.1:32400` | Plex base URL. |
| `PLEX_TOKEN` | *(required)* | Admin X-Plex-Token. |
| `PLEX_TIMEOUT` | `30` | Seconds. |
| `PLEX_VERIFY_SSL` | `true` | Set `false` only for self-signed https. |
| `PLEX_MCP_HOST` | `0.0.0.0` | `127.0.0.1` for this machine only. |
| `PLEX_MCP_PORT` | `8765` | |
| `PLEX_MCP_PATH` | `/mcp` | |
| `PLEX_MCP_AUTH_TOKEN` | *(none)* | Required when listening beyond localhost; at least 24 characters. |
| `PLEX_MCP_ALLOWED_HOSTS` | auto | Host names/IPs clients use. Empty = this machine's name and IPs. `*` disables the check. |
| `PLEX_MCP_READ_ONLY` | `false` | Only register read tools. Also `--read-only`. |
| `PLEX_MCP_ALLOW_DELETE` | `true` | `false` hides the delete tools. |
| `PLEX_MCP_ALLOWED_LIBRARIES` | all | Comma-separated titles or IDs the server may see or change. |
| `PLEX_MCP_MAX_RESULTS` | `200` | Cap for list/search results. |
| `PLEX_MCP_LOG_LEVEL` | `INFO` | |
| `PLEX_MCP_LOG_FILE` | *(none)* | The installer sets `logs\plex-mcp.log`. |

After editing `.env`, restart the task: `Stop-ScheduledTask "Plex MCP Server"; Start-ScheduledTask "Plex MCP Server"`.

---

## Connecting agents

Replace `PLEX-HOST` with the Plex machine's name or IP and `TOKEN` with `PLEX_MCP_AUTH_TOKEN`.

### Claude Code

```bash
claude mcp add --transport http plex http://PLEX-HOST:8765/mcp --header "Authorization: Bearer TOKEN"
```

### Claude Desktop (on another PC)

Claude Desktop's config file launches local commands, so bridge to the HTTP server with
[`mcp-remote`](https://github.com/geelen/mcp-remote) (needs Node.js). Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "plex": {
      "command": "npx",
      "args": ["mcp-remote", "http://PLEX-HOST:8765/mcp", "--allow-http", "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "Bearer TOKEN" }
    }
  }
}
```

(The token goes in `env` because Windows mangles spaces inside `args`.)

**Alternative without Node:** install this package on that PC too and run it over stdio, pointed at Plex across the LAN:

```json
{
  "mcpServers": {
    "plex": {
      "command": "C:\\plex-mcp\\.venv\\Scripts\\plex-mcp.exe",
      "args": ["--transport", "stdio"],
      "env": { "PLEX_URL": "http://PLEX-HOST:32400", "PLEX_TOKEN": "YOUR_PLEX_TOKEN" }
    }
  }
}
```

### Cursor / VS Code

Cursor (`~/.cursor/mcp.json`):

```json
{ "mcpServers": { "plex": { "url": "http://PLEX-HOST:8765/mcp", "headers": { "Authorization": "Bearer TOKEN" } } } }
```

VS Code (`.vscode/mcp.json`):

```json
{ "servers": { "plex": { "type": "http", "url": "http://PLEX-HOST:8765/mcp", "headers": { "Authorization": "Bearer TOKEN" } } } }
```

### Your own Python agent

```python
import anyio, httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer TOKEN"}, timeout=httpx2.Timeout(30, read=300)) as http:
        async with Client(streamable_http_client("http://PLEX-HOST:8765/mcp", http_client=http)) as plex:
            result = await plex.call_tool("search_library", {"library": "Movies", "filters": {"unwatched": True}, "limit": 5})
            print(result.structured_content)

anyio.run(main)
```

Both the 2026-07-28 MCP protocol and older (2025) clients are supported on the same endpoint.

---

## Things to ask an agent

- "Find every movie in my library with 'Alien' in the title and put them in a collection called *Alien Saga*, ordered by release date."
- "Make a smart collection of unwatched 90s action movies, newest first, and show it on my Home page."
- "The Office (US) has the wrong summary and poster. Fix the match, then lock the poster."
- "Add the label *Kids* to every G and PG movie."
- "Build a playlist of the first episode of every show added in the last month."
- "List movies whose sort title starts with 'The ' and set sort titles without the article."

---

## Safety and security

- **Start read-only.** Run with `--read-only` (or `PLEX_MCP_READ_ONLY=true`) until you trust your agent's behaviour.
- **Edits lock fields** by default so Plex won't overwrite them on refresh. Unlock with `set_field_locks`.
- **Deletes need the exact title** and only remove collections/playlists, never media. Turn them off with `PLEX_MCP_ALLOW_DELETE=false`.
- **Scope it** with `PLEX_MCP_ALLOWED_LIBRARIES` (for example, keep an agent out of *Home Videos*).
- **Traffic is plain HTTP** on your LAN, and the bearer token grants admin-level Plex edits. Don't port-forward 8765.
  For access away from home, use a VPN (e.g. Tailscale/WireGuard) or put it behind an HTTPS reverse proxy.
- The server refuses to listen beyond localhost without an auth token, and checks the `Host` header against
  this machine's names to block DNS-rebinding attacks. If agents connect through a different name (a DNS alias,
  a Tailscale name), add it to `PLEX_MCP_ALLOWED_HOSTS`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `401 Unauthorized` | Header must be exactly `Authorization: Bearer <PLEX_MCP_AUTH_TOKEN>`. |
| `421 Misdirected Request` / "Invalid Host header" | The client used a host name the server doesn't recognise. Add it to `PLEX_MCP_ALLOWED_HOSTS`. |
| Can't connect from another PC | Check `curl http://PLEX-HOST:8765/health`, the firewall rule, and that the network is set to *Private*. |
| `(401) unauthorized` from Plex | `PLEX_TOKEN` is wrong or expired. |
| Edits "succeed" but revert | The Plex account isn't the server admin, or an agent refresh ran on unlocked fields. |
| Task won't start | See `logs\plex-mcp.log`; run `scripts\windows\run.ps1` to watch output directly. |

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests run the real MCP client and real python-plexapi against `tests/fake_plex.py`, a small in-memory imitation
of the Plex HTTP API, so no Plex server is needed. They cover every tool family, the safety settings, bearer auth,
the Host allowlist, legacy-protocol clients, and the stdio transport.

Layout:

```
src/plex_mcp/
  cli.py          entry point (plex-mcp / python -m plex_mcp)
  config.py       settings from env/.env
  server.py       builds the MCPServer and its instructions
  http_app.py     Streamable HTTP app, bearer auth, Host allowlist
  plex.py         Plex connection, library allowlist, serialization
  tools/          library.py (read), metadata.py, collections.py, playlists.py
scripts/windows/  install.ps1, run.ps1, uninstall.ps1
tests/            fake Plex server + end-to-end tests
```
