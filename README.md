# Gemini Telegram Gateway

**Languages:** [English](README.md) | [Русский](README.ru.md)

Telegram gateway for [Google Gemini CLI](https://github.com/google-gemini/gemini-cli) built with Python and aiogram. It lets you work with Gemini from Telegram while preserving conversation context across messages, streaming partial output, and delivering generated files back to the chat.

Repository: [github.com/Sheme1/gemini_cli_gateway_tg](https://github.com/Sheme1/gemini_cli_gateway_tg)

## Overview

This gateway is optimized for one production shape:

- Linux host
- `systemd` service named `telegram-gateway`
- Gemini CLI installed for the same user that owns the repo and Gemini auth state

Local `python -m gateway.main` is still supported, but it is intended for development and smoke testing, not as the recommended long-running production process.

## Current Runtime Model

The gateway does **not** keep one permanent interactive Gemini subprocess alive anymore.

Current behavior:

1. Each Telegram prompt launches a headless `gemini -p ... -o stream-json` process.
2. Gemini returns a `session_id` in the `init` event.
3. The gateway stores the active `session_id` per Telegram user.
4. Later prompts resume that context with `--resume <session_id>`.

This preserves conversation continuity without relying on one forever-running CLI process.

## What The Gateway Currently Supports

- Per-user Gemini session continuity through `session_id` + `--resume`
- Streamed Telegram message updates from Gemini `stream-json`
- Three render modes for tool progress: `compact`, `summary`, `detailed`
- MCP server list/toggle UI
- Gemini skills list/toggle UI
- Voice message transcription through Gemini API
- Automatic artifact discovery and Telegram file delivery
- Soft finalize when Gemini does not emit a final `result` but the output file is already stable
- Optional message access restriction with `TARGET_CHAT_ID`

## Important Limits

- The inline approval UI exists, and approval requests are detected, but completing interactive approvals from Telegram is currently limited in the headless flow because `SessionManager.answer_approval()` is still a stub.
- For production use, prefer `GEMINI_APPROVAL_MODE=yolo`, `auto_edit`, or `plan`.
- `APPROVAL_TIMEOUT` is loaded from config, but the current headless approval flow does not actively enforce it.
- Auto-send is optimized for common document, image, spreadsheet, archive, and presentation formats. Sidecar files such as `.md` can still be delivered when explicitly referenced by Gemini output.

## Project Layout

```text
gemini_cli_gateway_tg/
├── gateway/
│   ├── main.py
│   ├── config.py
│   ├── artifacts.py
│   ├── user_settings.py
│   ├── bot/
│   │   ├── handlers/
│   │   ├── keyboards/
│   │   ├── middleware/
│   │   └── ui.py
│   ├── gemini/
│   │   ├── parser.py
│   │   ├── renderer.py
│   │   └── session.py
│   └── streaming/
│       └── editor.py
├── tests/
├── install.sh
├── telegram-gateway.service
├── Dockerfile
└── docker-compose.yml
```

## Requirements

- Python 3.12+
- Node.js + npm
- `@google/gemini-cli` installed for the service user
- Gemini CLI authentication already completed for that same user
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Optional Gemini API key for voice transcription

## Quick Local Run

This is the recommended path for local development, not for production daemonization.

```bash
git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
cd gemini_cli_gateway_tg

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

npm install -g @google/gemini-cli

cp .env.example .env
# edit .env

python -m gateway.main
```

On Windows, activate the virtual environment with `.venv\Scripts\activate`.

## Configuration

All runtime configuration comes from `.env`.

| Variable | Default | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | none | Required Telegram bot token. |
| `TARGET_CHAT_ID` | empty | Optional message-level restriction to a single chat ID. |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Model passed to `gemini -m`. |
| `GEMINI_APPROVAL_MODE` | `yolo` | Approval strategy forwarded to Gemini CLI. Supported: `default`, `auto_edit`, `yolo`, `plan`. |
| `GEMINI_WORKING_DIR` | user home | Working directory used for Gemini subprocesses. |
| `GEMINI_ARTIFACT_ROOTS` | `GEMINI_WORKING_DIR` | Comma-separated directories scanned for generated files. |
| `GEMINI_CLI_TIMEOUT` | `600` | Inactivity timeout for a single Gemini request. `600` seconds is recommended for long MCP/skill runs. |
| `GEMINI_SANDBOX` | `false` | Adds `--sandbox` to Gemini CLI calls. |
| `GEMINI_STREAM_DEBUG` | `false` | Logs raw `stream-json` traffic and parser diagnostics. |
| `GEMINI_SOFT_FINALIZE_IDLE_SECONDS` | `90` | Soft-finalize threshold after a file has already been delivered and Gemini stays idle without a final `result`. |
| `ARTIFACT_WATCH_INTERVAL` | `1.0` | Poll interval for ready artifacts while a prompt is still running. |
| `ARTIFACT_STABLE_SECONDS` | `5.0` | How long a file must stop changing before it is auto-sent. |
| `GEMINI_API_KEY` | empty | Enables voice transcription through Gemini API. |
| `STREAM_UPDATE_INTERVAL` | `1.5` | Minimum delay between Telegram message edits. |
| `APPROVAL_TIMEOUT` | `120` | Reserved for approval UI behavior; currently not enforced in the headless approval path. |
| `LOG_LEVEL` | `INFO` | Application log level. |

## Bot Commands

| Command | What it does |
| --- | --- |
| `/start` | Resets the current user session and shows the main help text. |
| `/new` | Clears the stored Gemini session for the current user. |
| `/sessions` | Lists Gemini sessions from CLI history and lets you resume one. |
| `/mcp` | Shows installed MCP servers and toggle buttons. |
| `/mcp <server> <prompt>` | Forces a prompt through `@server ...`. |
| `/skills` | Shows installed Gemini skills and toggle buttons. |
| `/skills <skill> <prompt>` | Forces a prompt through `@skill ...`. |
| `/model` | Opens the inline model picker. |
| `/settings` | Shows current render mode, approval mode, timeout, and sandbox state; lets you change render and approval modes. |
| `/status` | Returns a simple gateway health message. |
| `/help` | Shows the command summary. |

## Runtime Notes

### Streaming and Rendering

- Gemini output is parsed from newline-delimited `stream-json` events.
- Telegram updates are throttled by `STREAM_UPDATE_INTERVAL`.
- Long replies are split safely to stay under the Telegram 4096-character limit.
- User render preferences are stored in `.gateway_state/user_settings.json`.

### Artifact Delivery

- The gateway inspects assistant text, tool arguments, and tool results for file candidates.
- Common deliverable formats such as `.docx`, `.pdf`, `.xlsx`, `.pptx`, `.png`, `.jpg`, `.zip` are auto-sent.
- Explicit `[SEND_FILE: path]` markers from Gemini output are honored.
- If a file stabilizes before Gemini emits a final `result`, the gateway can deliver the file and soft-finalize the Telegram response.

### Voice Flow

- Voice messages are downloaded from Telegram, sent to Gemini API for transcription, and then forwarded to Gemini CLI as text.
- Voice support requires `GEMINI_API_KEY`.

## Production Deployment With systemd

`systemd` is the primary production target for this project.

### Prepare the host

Install Python, Node.js, npm, and Gemini CLI using your preferred package manager or distribution method. Then, as the same user that will own the service:

```bash
git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
cd gemini_cli_gateway_tg

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

npm install -g @google/gemini-cli

cp .env.example .env
# edit .env
```

Important:

- Run `install.sh` as the same user that can already run `gemini` successfully.
- If Gemini CLI is installed through `nvm` or another user-local toolchain, make sure `gemini` and `node` are available in the shell before running the installer.
- The installer does not bootstrap Python, Node.js, npm, `.env`, or `.venv`. It only renders and installs the `systemd` unit.

### Automatic service installation

```bash
chmod +x install.sh
./install.sh
```

The installer:

- resolves the project root from the script location, not from the current shell directory
- verifies Linux + `systemd` + `sudo`
- verifies `.env`, `.venv/bin/python`, `gemini`, and `node`
- renders `telegram-gateway.service` with concrete paths
- injects a `PATH` that includes the detected Gemini and Node binary directories
- installs or updates `/etc/systemd/system/telegram-gateway.service`
- runs `daemon-reload`, enables the unit, and starts or restarts it

### Service management

```bash
sudo systemctl status telegram-gateway
sudo systemctl restart telegram-gateway
sudo systemctl stop telegram-gateway
sudo journalctl -u telegram-gateway -f
```

### Manual systemd installation

If you prefer to manage the unit manually, edit `telegram-gateway.service` and replace all placeholders:

- `__SERVICE_USER__`
- `__PROJECT_DIR__`
- `__ENV_FILE__`
- `__HOME_DIR__`
- `__SERVICE_PATH__`
- `__PYTHON_BIN__`

The `PATH` line must include the directories that contain both `gemini` and `node`, especially if they come from `nvm` or another user-local installation.

Then install the unit:

```bash
sudo install -m 0644 telegram-gateway.service /etc/systemd/system/telegram-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable telegram-gateway
sudo systemctl start telegram-gateway
```

### Updating the deployment

```bash
cd /path/to/gemini_cli_gateway_tg
git pull

source .venv/bin/activate
pip install -r requirements.txt

sudo systemctl restart telegram-gateway
```

Re-run `./install.sh` after changing the service user, moving the project directory, recreating the virtual environment, or reinstalling Gemini CLI to a different location.

## Docker

Docker is available as a secondary deployment option, but `systemd` is the recommended production path.

Current Docker setup:

- installs `@google/gemini-cli` inside the image
- mounts `./workspace` to `/workspace`
- persists Gemini CLI state in the `gemini_home` volume
- persists gateway UI state in the `gateway_state` volume

Run it with:

```bash
docker compose up -d --build
docker compose logs -f gateway
```

If you use Docker, remember that Gemini CLI auth lives inside the container volume, not in your host user account.

## Development, Tests, and CI

Install development dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Run checks locally:

```bash
ruff check .
ruff format --check .
pytest tests -v
```

CI in `.github/workflows/ci.yml` runs Ruff and pytest on pushes and pull requests to `main`.

## Troubleshooting

### Service starts manually but fails under systemd

Most often this means the service user cannot see `gemini`, `node`, or the correct Gemini auth state.

Check:

```bash
sudo systemctl status telegram-gateway
sudo journalctl -u telegram-gateway -n 200 --no-pager
```

Then verify:

- `gemini` works for the same Unix user that owns the service
- `node` is installed and reachable
- `.env` points to valid directories for `GEMINI_WORKING_DIR` and `GEMINI_ARTIFACT_ROOTS`

### `/mcp` or `/skills` is empty

Check the CLI directly:

```bash
gemini mcp list
gemini skills list
```

If the lists are empty, install the missing MCP servers or skills in Gemini CLI first.

### Files are not auto-sent

Check:

- the file was created inside `GEMINI_WORKING_DIR` or one of `GEMINI_ARTIFACT_ROOTS`
- the file has a deliverable extension, or Gemini emitted an explicit `[SEND_FILE: ...]` marker
- the file became stable for at least `ARTIFACT_STABLE_SECONDS`

### Voice messages fail

Make sure `GEMINI_API_KEY` is present in `.env`.

### Approval buttons do not continue execution

This is a known current limitation of the headless approval path. Use `yolo`, `auto_edit`, or `plan` in production until interactive approval completion is implemented end-to-end.
