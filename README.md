# Gemini Telegram Gateway

[![CI](https://github.com/Sheme1/gemini_cli_gateway_tg/actions/workflows/ci.yml/badge.svg)](https://github.com/Sheme1/gemini_cli_gateway_tg/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)

**Languages:** [English](README.md) | [Русский](README.ru.md)

Use [Google Gemini CLI](https://github.com/google-gemini/gemini-cli) from Telegram.

This project bridges Telegram and Gemini CLI so you can:

- chat with Gemini from your phone
- keep session context between messages
- stream partial answers back into Telegram
- receive generated files directly in chat
- toggle MCP servers and skills without leaving Telegram

> [!WARNING]
> This repository is still an experimental MVP.
> It is usable, but it is not polished or production-hardened.
> Forks, self-hosted tweaks, and local modifications are not just allowed, they are encouraged.

## Why this project

Gemini CLI is great in the terminal, but sometimes you want that same workflow from a Telegram chat:

- while away from your computer
- for quick prompts and follow-ups
- to receive generated documents and files on mobile
- to keep a lightweight self-hosted bridge instead of building a full web UI

## Features

- Session continuity via Gemini `session_id` and `--resume`
- Streaming Telegram replies from Gemini `stream-json`
- Paginated `/sessions` browser with newest conversations first
- Three output modes: `compact`, `summary`, `detailed`
- MCP list and toggle UI
- Skills list and toggle UI
- Per-user model presets: `auto`, `pro`, `flash`, `flash-lite`, plus legacy manual presets
- Oversized prompt confirmation and daily token usage tracking
- Voice message transcription through Gemini API
- Automatic artifact discovery and file delivery
- Runtime diagnostics, local `doctor`, latency timings, and `/cancel`
- `systemd`-first deployment for Linux servers
- Docker as a secondary deployment option

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
cd gemini_cli_gateway_tg
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows:

```powershell
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt -r requirements-dev.txt
npm install -g @google/gemini-cli
```

### 4. Configure the bot

```bash
cp .env.example .env
```

Required:

- `TELEGRAM_BOT_TOKEN`

The full runtime configuration is documented below and mirrored in
[.env.example](.env.example).

Check the deployment runtime without starting polling:

```bash
python -m gateway.main --check-runtime
```

Run local checks without contacting Telegram:

```bash
python -m gateway.main --doctor
python -m gateway.main --doctor-json
```

### Environment reference

Use plain `KEY=value` lines in `.env`. Do not wrap values in quotes unless your
shell tooling requires it. Comma-separated values should not contain spaces.

| Variable | Required | How to write it |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram Bot API token from BotFather, for example `123456789:AA...`. It is redacted in logs. |
| `TARGET_CHAT_ID` | No | Numeric Telegram chat/user id, or a comma-separated allowlist. Examples: `TARGET_CHAT_ID=111111111` or `TARGET_CHAT_ID=111111111,222222222`. Leave empty to allow every chat that can reach the bot. Groups and supergroups can have negative chat id values. |
| `GEMINI_BIN` | No | Gemini executable name or absolute path. Use `gemini` when it is available through `PATH`; use `/home/user/.npm-global/bin/gemini` for systemd if needed. |
| `GEMINI_MODEL` | No | Model or Gemini CLI alias passed to `gemini -m`, for example `auto`, `pro`, `flash`, `flash-lite`, or a concrete model id. Defaults to `auto`. |
| `GEMINI_TARGET_VERSION` | No | Expected Gemini CLI version for `doctor`. Defaults to `0.39.1`; mismatch is a warning, not a startup blocker. |
| `GEMINI_SKIP_TRUST` | No | `true` by default. Passes `--skip-trust` for headless Gemini CLI 0.39.1 runs so workspace trust cannot block on an interactive prompt. |
| `GEMINI_APPROVAL_MODE` | No | One of `default`, `auto_edit`, `yolo`, `plan`. Passed as `--approval-mode=...`; `--yolo` is deprecated in Gemini CLI 0.39.1 and is not used. |
| `GEMINI_POLICY_PATHS` | No | Extra user policy TOML files or directories, comma-separated. Passed as repeated `--policy` flags. |
| `GEMINI_ADMIN_POLICY_PATHS` | No | Extra admin policy TOML files or directories, comma-separated. Passed as repeated `--admin-policy` flags. |
| `GEMINI_ALLOWED_MCP_SERVER_NAMES` | No | Optional MCP server allowlist, comma-separated. Passed as repeated `--allowed-mcp-server-names` flags. |
| `GEMINI_EXTENSIONS` | No | Optional extension selection, comma-separated. Use `none` to disable extensions for gateway runs. Passed as repeated `--extensions`. |
| `GEMINI_SCREEN_READER` | No | `true` or `false`. Passes `--screen-reader` for accessibility-friendly CLI output. |
| `GEMINI_WORKING_DIR` | No | Main project/work directory for Gemini CLI. Keep it as narrow as practical to reduce startup scanning. |
| `GEMINI_INCLUDE_DIRECTORIES` | No | Extra directories for Gemini workspace access, comma-separated. Example: `/srv/project/shared,/srv/docs`. Passed as `--include-directories`. |
| `GEMINI_ARTIFACT_ROOTS` | No | Directories where generated files are searched, comma-separated. Defaults to `GEMINI_WORKING_DIR`. |
| `GEMINI_CLI_TIMEOUT` | No | Seconds without Gemini stdout before the gateway stops the process and reports a timeout. |
| `GEMINI_SHUTDOWN_GRACE_SECONDS` | No | Seconds to wait after graceful termination before killing the Gemini process group. |
| `GEMINI_SANDBOX` | No | `true` or `false`. When true, passes `--sandbox` to Gemini CLI. |
| `GEMINI_STREAM_DEBUG` | No | `true` or `false`. Enables raw stream/stderr diagnostics in logs; use only while debugging. |
| `GEMINI_SOFT_FINALIZE_IDLE_SECONDS` | No | If an artifact was delivered but Gemini does not finish, wait this many seconds before soft-finalizing. |
| `ARTIFACT_WATCH_INTERVAL` | No | Seconds between artifact watcher checks while a prompt is running. |
| `ARTIFACT_STABLE_SECONDS` | No | File must remain unchanged this long before it is sent to Telegram. |
| `GEMINI_API_KEY` | No | Gemini API key for voice transcription. Text prompts use Gemini CLI auth. |
| `STREAM_UPDATE_INTERVAL` | No | Minimum seconds between normal Telegram edit updates after the first answer chunk. |
| `STREAM_MIN_UPDATE_CHARS` | No | Minimum buffered character delta before another streamed edit is scheduled. |
| `STREAM_RETRY_MAX_DELAY` | No | Maximum seconds to sleep when Telegram asks the bot to retry later. |
| `PROMPT_WARN_CHARS` | No | Prompt length that triggers an inline confirmation before sending to Gemini. |
| `PROMPT_MAX_CHARS` | No | Hard prompt length limit. Requests above this value are rejected before starting Gemini. |
| `PROMPT_CONFIRM_TIMEOUT` | No | Seconds before an oversized prompt confirmation expires. |
| `USER_DAILY_TOKEN_LIMIT` | No | Per-user daily token limit from Gemini result stats. `0` disables the limit. |
| `GLOBAL_DAILY_TOKEN_LIMIT` | No | Global daily token limit from Gemini result stats. `0` disables the limit. |
| `POLLING_TIMEOUT` | No | Telegram long-polling timeout passed to aiogram. |
| `POLLING_CONCURRENCY_LIMIT` | No | Maximum concurrent update handlers. Per-user prompt locks still prevent overlapping Gemini prompts. |
| `GATEWAY_STATE_DIR` | No | Writable gateway state directory for user settings. Relative paths resolve from the process working directory. |
| `APPROVAL_TIMEOUT` | No | Seconds before pending interactive approval expires. Headless approval is still limited by Gemini CLI behavior. |
| `LOG_MODE` | No | `quiet`, `normal`, or `debug`. Controls default Python logging level. |
| `LOG_LEVEL` | No | Legacy explicit Python logging level override. Leave empty to use `LOG_MODE`. |

### 5. Run locally

```bash
python -m gateway.main
```

This local run mode is best for development and smoke testing.
For long-running deployment, use `systemd`.

## Recommended deployment

The main deployment target for this project is:

- Linux
- `systemd`
- one user account that owns the repo, the virtualenv, and the Gemini CLI auth state

### Install as a systemd service

Prepare the machine first:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install -g @google/gemini-cli
cp .env.example .env
```

Then run:

```bash
chmod +x install.sh
./install.sh
```

The installer:

- detects the project root from the script location
- verifies Linux, `systemd`, `sudo`, `gemini`, `node`, `.env`, and `.venv`
- runs `python -m gateway.main --check-runtime` before installing the service
- renders the `telegram-gateway.service` unit with the correct paths
- sets an explicit `PATH` so the service can find `gemini` and `node`
- installs and starts `telegram-gateway`

Useful commands:

```bash
sudo systemctl status telegram-gateway
sudo systemctl restart telegram-gateway
sudo journalctl -u telegram-gateway -f
```

### Update an existing systemd deployment

Fast path:

```bash
chmod +x update.sh
./update.sh
```

The update script runs `git pull --ff-only`, installs Python dependencies into
`.venv`, runs `python -m gateway.main --doctor`, checks whether the installed
systemd unit differs from the current rendered template, and restarts
`telegram-gateway`.

Manual path:

```bash
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m gateway.main --doctor
sudo systemctl restart telegram-gateway
sudo systemctl status telegram-gateway --no-pager -l
```

For ordinary Python code changes, `sudo systemctl restart telegram-gateway` is
enough. Run `sudo systemctl daemon-reload` only when the installed unit file
changes, or rerun `./install.sh`, which performs daemon reload for you.

## Docker

Docker support is included, but it is a secondary path.

```bash
docker compose up -d --build
docker compose logs -f gateway
```

If you use Docker, remember that Gemini CLI auth lives inside the container volume, not in your host user profile.

## Commands

| Command | Description |
| --- | --- |
| `/start` | Reset the current user session and show the intro message |
| `/new` | Start a fresh Gemini conversation |
| `/sessions [filter\|latest]` | Page through saved Gemini sessions, search by title/id/index, resume latest, delete, or export the list as TXT |
| `/mcp` | Show installed MCP servers |
| `/skills` | Show installed Gemini skills |
| `/model` | Pick the active Gemini model |
| `/settings` | Change render mode and approval mode |
| `/context` | Show the current user model, preset, session id, and workspace |
| `/usage` | Show daily token usage and the last request stats |
| `/doctor` | Run local gateway environment diagnostics |
| `/cancel` | Stop the current Gemini request |
| `/status` | Show gateway, Gemini, webhook, and runtime status |
| `/diagnostics` | Show a redacted diagnostics report |
| `/help` | Show the command summary |

## How it works

This gateway currently uses a headless request model:

1. each Telegram prompt launches `gemini -p ... -o stream-json --skip-trust --approval-mode=...`
2. Gemini returns a `session_id`
3. the gateway stores that `session_id` per user
4. later prompts continue the same context with `--resume`

That keeps conversations continuous without depending on one forever-running subprocess.

`/sessions` uses `gemini --list-sessions`, parses the Gemini CLI 0.39.1 text
format, reverses it so the newest chats appear first, and shows five dialogs per
Telegram page. Deletion uses `gemini --delete-session <session_uuid>` first, then
falls back to the listed index if needed. Gemini CLI does not expose a structured
JSON session list in 0.39.1, so this parser is covered by tests.

The first real answer chunk is edited into Telegram immediately. Later edits are
coalesced by `STREAM_UPDATE_INTERVAL` and `STREAM_MIN_UPDATE_CHARS` to avoid
Telegram flood limits.

Model selection is stored per Telegram user in `GATEWAY_STATE_DIR`; the `.env`
model is the fallback. The preferred presets use Gemini CLI aliases (`auto`,
`pro`, `flash`, `flash-lite`) so CLI-side model routing can keep working. Usage
counters are stored in `usage.json` and contain only totals, result status,
stats metadata, and last-request metadata, never prompt text.

ACP (`gemini --acp`) is not the default transport. In Gemini CLI 0.39.1 it is a
JSON-RPC/stdio mode mainly intended for IDE and editor integrations. This
gateway keeps the safer headless `stream-json` transport so MCP, skills,
extensions, normal Gemini CLI config, artifact delivery, and Telegram streaming
stay compatible.

## Current rough edges

This is where the MVP still feels like an MVP:

- interactive approval is not continued from Telegram in headless mode; use approval mode or policy rules
- Gemini CLI output formats can change between releases
- Linux + `systemd` is the main target; other deployment modes may need local tweaks
- some users will prefer keeping a fork for their own workflow, auth model, or deployment setup

If that sounds acceptable, this repo is for you.

## Development

Run the local checks:

```bash
ruff check .
ruff format --check .
pytest tests -v
```

CI is configured in [.github/workflows/ci.yml](.github/workflows/ci.yml).

## Contributing

Contributions are welcome, but the project is intentionally pragmatic and still early.

- small focused fixes are great
- documentation improvements are great
- deployment hardening is great
- parser and session robustness improvements are especially useful

See [CONTRIBUTING.md](CONTRIBUTING.md) for the short version.

## License

This project is licensed under the [MIT License](LICENSE).
