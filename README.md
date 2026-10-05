# mac-api

A small FastAPI server that runs on your Mac and exposes macOS services:
Reminders, iMessage/SMS, Telegram, Contacts, Notes, Calendar, Shortcuts, notifications (incoming Viber, WhatsApp, Messenger, ... messages) and system controls.
It speaks two protocols on the same port, both protected by one token:

- a REST API at `/`, for scripts, your phone and home automation;
- an [MCP](https://modelcontextprotocol.io) server at `/mcp`, so AI assistants on your LAN can use the same services as tools.

| Area | What you can do |
| --- | --- |
| **Reminders** | List lists; list/search reminders (or just today's and overdue ones); create with due date and alert; edit, complete, delete |
| **Messages** | Conversations with contact names and unread counts; read a conversation; search all messages; filter by person, date or unread; download attachments; send to a person or an existing (group) chat |
| **Telegram** | Your own account through Telegram's official API: chats with unread counts, read, search, send, mark as read. Needs a one-time login, see [Telegram](#telegram). |
| **Notifications** | Notifications shown on the Mac, filterable by app. This is how incoming **Viber**, WhatsApp, Messenger, Signal or Slack messages reach the API. See [Viber, WhatsApp and other apps](#viber-whatsapp-and-other-apps). |
| **Contacts** | Search by name (accent-insensitive), phone or email; look up who a number belongs to |
| **Notes** | Folders; list/search notes; read (HTML and plain text); create; append; delete |
| **Calendar** | Calendars; events in a range, with recurring events expanded; create and delete events |
| **Shortcuts** | List your shortcuts and run any of them with text input. This reaches almost anything else on the Mac. |
| **System** | Notifications, text to speech, clipboard, volume, battery, screenshot, open URLs/files/apps, running apps, quit app, display sleep |

Interactive docs for every endpoint are served at `http://127.0.0.1:8765/docs`.

## Quick start

You need [uv](https://docs.astral.sh/uv/) (`brew install uv`) and macOS 12 or later.

```sh
uvx --from git+https://github.com/kostaslamas/Mac_api mac-api
```

Or from a local clone:

```sh
git clone https://github.com/kostaslamas/Mac_api && cd Mac_api
uvx --from . mac-api        # or: uv run mac-api
```

On first start a token is generated and saved to `~/.config/mac-api/api_key`.
Print it again at any time with `mac-api --print-key`.

```sh
KEY=$(uvx --from git+https://github.com/kostaslamas/Mac_api mac-api --print-key)
curl -H "Authorization: Bearer $KEY" "http://127.0.0.1:8765/diagnostics?automation=true"
```

## Use it from an AI on your LAN (MCP)

1. On the Mac, start the server for the LAN:

   ```sh
   uvx --from git+https://github.com/kostaslamas/Mac_api mac-api --lan
   ```

   `--lan` listens on all network interfaces but only accepts private addresses (192.168.x.x, 10.x.x.x, 172.16–31.x.x, Tailscale's 100.64.0.0/10).
   If the macOS firewall is on, it asks whether Python may accept incoming connections: allow it.

2. Print the settings for your AI client:

   ```sh
   uvx --from git+https://github.com/kostaslamas/Mac_api mac-api --print-mcp-config
   ```

   It prints the MCP URL (e.g. `http://192.168.1.20:8765/mcp`), the token, and ready-to-paste config:

   - **Claude Code**: `claude mcp add --transport http mac http://192.168.1.20:8765/mcp --header "Authorization: Bearer <token>"`
   - **Clients with a URL + headers setting** (Cursor, LM Studio, a project `.mcp.json`, ...):

     ```json
     {"mcpServers": {"mac": {"type": "http", "url": "http://192.168.1.20:8765/mcp",
                             "headers": {"Authorization": "Bearer <token>"}}}}
     ```

   - **Claude Desktop**: through [`mcp-remote`](https://www.npmjs.com/package/mcp-remote), which `--print-mcp-config` also writes out.

Every MCP request needs `Authorization: Bearer <token>`; without it the server answers `401`.
Claude's web connectors (claude.ai) connect from Anthropic's cloud, so they cannot reach a LAN address.
Use a desktop or terminal client on a computer in your network.

The AI gets 36 tools (41 with Telegram set up), such as `reminders_today`, `reminders_create`, `messages_chats`, `messages_read`, `messages_send`, `telegram_chats`, `telegram_send`, `notifications_recent`, `contacts_search`, `notes_append`, `calendar_events`, `calendar_create_event`, `shortcuts_run`, `mac_screenshot` and `mac_notify`.
Tools that only read are marked read-only and tools that delete are marked destructive, so clients can ask you before running them.
To let an AI look but never act, run with `--read-only`.
The tools that change anything (sending messages, creating, deleting, running shortcuts, ...) then don't exist at all.

## macOS permissions

macOS grants permissions to the app that *launched* mac-api (Terminal, iTerm, ...), not to mac-api itself.

| Permission | Needed for | Where |
| --- | --- | --- |
| Full Disk Access | Messages (reading), Contacts, notifications | System Settings → Privacy & Security → Full Disk Access → add your terminal, then restart it |
| Automation | Reminders, Notes, Calendar, sending Messages, running apps list | macOS asks the first time; or System Settings → Privacy & Security → Automation |
| Screen Recording | `/system/screenshot` | System Settings → Privacy & Security → Screen & System Audio Recording |

`GET /diagnostics?automation=true` touches each app once, so every Automation prompt appears together.
It then reports what is still missing.

## Telegram

mac-api uses Telegram's official API for user accounts (the same one Telegram's own apps use), so it acts as **you**.
It can read and send in your personal chats; it is not a bot.

1. At [my.telegram.org](https://my.telegram.org), sign in and open *API development tools*.
   Create an app (any name) and note its `api_id` and `api_hash`.
2. Stop mac-api if it is running, then log in once:

   ```sh
   uvx --from git+https://github.com/kostaslamas/Mac_api mac-api --telegram-login
   ```

   It asks for the `api_id` and `api_hash`, your phone number, the code Telegram sends you, and your 2FA password if you have one.
3. Start mac-api again. `GET /telegram/status` should show your account.

The login is saved in `~/.config/mac-api/telegram.session` (readable only by you).
**That file gives full access to your Telegram account**, so keep it private.
`mac-api --telegram-logout` ends the session on Telegram's side and deletes it.
The session also shows up under Telegram → Settings → Devices, where you can end it too.

Chats are addressed by id (from `/telegram/chats`), `@username`, a contact's phone number, or `me` for Saved Messages.
Telegram limits how fast accounts may act, so don't use this for bulk messaging.

## Viber, WhatsApp and other apps

Viber, WhatsApp, Messenger, Signal and most other messengers have no API for personal accounts.
Viber's REST API is only for business bots: it costs €100 a month and cannot see your own chats.
Viber Desktop encrypts its database.

These apps still show a notification for each incoming message, and macOS keeps them in the Notification Center database.
mac-api reads that database:

```sh
H="Authorization: Bearer $KEY"; API=http://127.0.0.1:8765
curl -H "$H" "$API/notifications?app=viber"          # newest first; 'app' matches the name or bundle id
curl -H "$H" "$API/notifications?app=whatsapp&q=dinner"
curl -H "$H" "$API/notifications/apps"               # which apps have notifications stored
```

Usually the title is the sender or chat, and the body is the message.
For AI clients, the tool is `notifications_recent`.

Limits:

- Only incoming messages, only while the desktop app is signed in on the Mac, and only what Notification Center still holds.
- No replying.
- If an app hides message previews in its notifications, only the sender is there.
  Turn previews on in the app's notification settings.
- Needs macOS 15 (Sequoia) or later and Full Disk Access.

## Authentication

Everything except `/health` needs the token, sent as either header:

```
Authorization: Bearer <token>
X-API-Key: <token>
```

`mac-api --rotate-key` replaces the token (restart the server and update your clients afterwards).
`--no-auth` exists for local testing and is refused unless the server only listens on `127.0.0.1`.

## Examples

```sh
H="Authorization: Bearer $KEY"; API=http://127.0.0.1:8765

# Reminders
curl -H "$H" $API/reminders/today
curl -H "$H" -X POST $API/reminders -H 'Content-Type: application/json' \
  -d '{"title": "Call the bank", "due_date": "2026-10-06T10:00:00", "list_name": "Personal"}'
curl -H "$H" -X POST $API/reminders/<id>/complete

# Messages
curl -H "$H" "$API/messages/chats?limit=10"
curl -H "$H" "$API/messages/chats/42/messages?limit=30"
curl -H "$H" "$API/messages?unread_only=true"
curl -H "$H" "$API/messages?q=dinner&since=2026-10-01T00:00:00"
curl -H "$H" -X POST $API/messages/send -H 'Content-Type: application/json' \
  -d '{"to": "+306912345678", "text": "On my way!"}'

# Calendar and Notes
curl -H "$H" "$API/calendar/events?days=1"
curl -H "$H" -X POST $API/notes -H 'Content-Type: application/json' \
  -d '{"title": "Shopping", "body": "eggs\nmilk"}'

# Shortcuts and system
curl -H "$H" -X POST $API/shortcuts/run -H 'Content-Type: application/json' -d '{"name": "Turn on lights"}'
curl -H "$H" -X POST $API/system/notify -H 'Content-Type: application/json' -d '{"message": "Build done"}'
curl -H "$H" "$API/system/screenshot?format=jpg&max_size=1600" -o screen.jpg
```

Datetimes without a timezone (`2026-10-06T10:00:00`) are interpreted as the Mac's local time.
Responses always include the offset.

## Configuration

| Flag | Environment variable | Default |
| --- | --- | --- |
| `--lan` | | off. Same as `--host 0.0.0.0` plus `--allow` for every private network. |
| `--host` | `MAC_API_HOST` | `127.0.0.1` (this Mac only) |
| `--port` | `MAC_API_PORT` | `8765` |
| `--allow CIDR` (repeatable) | `MAC_API_ALLOWED_NETWORKS` (comma-separated) | any address. With it, only those networks (and the Mac itself) may connect. |
| `--ssl-certfile`, `--ssl-keyfile` | | plain HTTP |
| `--api-key` | `MAC_API_KEY` | contents of `~/.config/mac-api/api_key` |
| `--read-only` | `MAC_API_READ_ONLY=1` | off. Rejects every request that changes something (sending, creating, deleting, notifications, ...) and hides those MCP tools. |
| `--no-mcp` | `MAC_API_NO_MCP=1` | MCP is on |
| `--no-auth` | `MAC_API_NO_AUTH=1` | off; only allowed on `127.0.0.1` |
| | `MAC_API_OSASCRIPT_TIMEOUT` | `60` seconds |
| | `MAC_API_MESSAGES_DB` | `~/Library/Messages/chat.db` |
| | `MAC_API_NOTIFICATIONS_DB` | `~/Library/Group Containers/group.com.apple.usernoted/db2/db` |
| `--telegram-login`, `--telegram-logout` | `MAC_API_TELEGRAM_API_ID`, `MAC_API_TELEGRAM_API_HASH` | read from `~/.config/mac-api/telegram.json`, which `--telegram-login` writes |
| | `MAC_API_TELEGRAM_SESSION` | `~/.config/mac-api/telegram.session` |

## Network security

The server only listens on `127.0.0.1` unless you pass `--lan` or `--host`.
**Never forward its port to the internet**: whoever has the token can read all your messages.

- Narrow the allowed clients further with `--allow`, e.g. `--allow 192.168.1.0/24` or a single address like `--allow 192.168.1.42`.
- On plain HTTP, anyone who can watch your network traffic can read the token.
  That is acceptable on your own home Wi-Fi, but not on shared or public networks.
- For HTTPS, create a certificate with [mkcert](https://github.com/FiloSottile/mkcert) (`mkcert 192.168.1.20 my-mac.local`).
  Install mkcert's root CA on the client computers and start with `--ssl-certfile ... --ssl-keyfile ...`.
- To reach the Mac from outside your home, use [Tailscale](https://tailscale.com) rather than opening a port.
  Its addresses are covered by `--lan`, and the traffic is encrypted.

## Starting at login

The simplest reliable option is a terminal profile or login item that runs `uvx ... mac-api`.
Permissions then stay attached to your terminal.
A `launchd` agent also works, but macOS then asks for permissions on behalf of the `uv`-managed Python binary.
Those prompts can be harder to approve.

## Limitations

- Reminders, Notes and Calendar go through AppleScript (JXA), which takes a moment on large libraries.
- Calendar: recurring events are expanded from their rule and excluded dates.
  A single occurrence that was moved may still show at its original time.
  Deleting a recurring event deletes the whole series.
- Messages: search is case-insensitive only for Latin letters.
  Sending confirms that Messages.app accepted the message, not that it was delivered.
- Calendars and Reminders lists are addressed by name; if two have the same name, the first match is used.

## Development

```sh
uv sync
uv run pytest
uv run mac-api --no-auth   # local testing only
```

The tests run anywhere: they use fake Messages, Contacts and Notification Center databases, a fake Telegram client, and stub out `osascript`.
The MCP tests start a real server and connect to it with the official MCP client.
They also syntax-check every JXA script with Node.js when it is installed.
