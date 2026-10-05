# mac-api

A small FastAPI server that runs on your Mac and exposes macOS services over HTTP:
Reminders, iMessage/SMS, Contacts, Notes, Calendar, Shortcuts and system controls.
Use it from scripts, your phone, home automation, or an AI agent.

| Area | What you can do |
| --- | --- |
| **Reminders** | List lists; list/search reminders (or just today's and overdue ones); create with due date and alert; edit, complete, delete |
| **Messages** | Conversations with contact names and unread counts; read a conversation; search all messages; filter by person, date or unread; download attachments; send to a person or an existing (group) chat |
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

On first start an API key is generated and saved to `~/.config/mac-api/api_key`.
Print it again at any time with `mac-api --print-key`.

```sh
KEY=$(uvx --from git+https://github.com/kostaslamas/Mac_api mac-api --print-key)
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8765/diagnostics?automation=true"
```

## macOS permissions

macOS grants permissions to the app that *launched* mac-api (Terminal, iTerm, ...), not to mac-api itself.

| Permission | Needed for | Where |
| --- | --- | --- |
| Full Disk Access | Messages (reading), Contacts | System Settings → Privacy & Security → Full Disk Access → add your terminal, then restart it |
| Automation | Reminders, Notes, Calendar, sending Messages, running apps list | macOS asks the first time; or System Settings → Privacy & Security → Automation |
| Screen Recording | `/system/screenshot` | System Settings → Privacy & Security → Screen & System Audio Recording |

`GET /diagnostics?automation=true` touches each app once, so every Automation prompt appears together.
It then reports what is still missing.

## Authentication

Every endpoint except `/health` needs the key, sent as either header:

```
X-API-Key: <key>
Authorization: Bearer <key>
```

## Examples

```sh
H="X-API-Key: $KEY"; API=http://127.0.0.1:8765

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
curl -H "$H" $API/system/screenshot -o screen.png
```

Datetimes without a timezone (`2026-10-06T10:00:00`) are interpreted as the Mac's local time.
Responses always include the offset.

## Configuration

| Flag | Environment variable | Default |
| --- | --- | --- |
| `--host` | `MAC_API_HOST` | `127.0.0.1` (this Mac only) |
| `--port` | `MAC_API_PORT` | `8765` |
| `--api-key` | `MAC_API_KEY` | contents of `~/.config/mac-api/api_key` |
| `--read-only` | `MAC_API_READ_ONLY=1` | off. Rejects every request that changes something (sending, creating, deleting, notifications, ...). |
| `--no-auth` | `MAC_API_NO_AUTH=1` | off |
| | `MAC_API_OSASCRIPT_TIMEOUT` | `60` seconds |
| | `MAC_API_MESSAGES_DB` | `~/Library/Messages/chat.db` |

## Reaching it from other devices

The server only listens on `127.0.0.1` by default.
To use it from your phone or another computer, put the devices on a private network such as [Tailscale](https://tailscale.com).
Then start the server with `--host 0.0.0.0` (or your Tailscale IP).
**Never expose it to the public internet**: it can read all your messages.
Plain HTTP is only acceptable inside an encrypted network like Tailscale.

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

The tests run anywhere: they use fake Messages/Contacts databases and stub out `osascript`.
They also syntax-check every JXA script with Node.js when it is installed.
