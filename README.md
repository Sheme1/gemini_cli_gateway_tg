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
- Three output modes: `compact`, `summary`, `detailed`
- MCP list and toggle UI
- Skills list and toggle UI
- Voice message transcription through Gemini API
- Automatic artifact discovery and file delivery
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

Optional but useful:

- `GEMINI_BIN`
- `TARGET_CHAT_ID`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `GEMINI_APPROVAL_MODE`
- `GEMINI_WORKING_DIR`

The full runtime configuration is documented in [.env.example](.env.example).

Check the deployment runtime without starting polling:

```bash
python -m gateway.main --check-runtime
```

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
| `/sessions` | List saved Gemini sessions and resume one |
| `/mcp` | Show installed MCP servers |
| `/skills` | Show installed Gemini skills |
| `/model` | Pick the active Gemini model |
| `/settings` | Change render mode and approval mode |
| `/cancel` | Stop the current Gemini request |
| `/status` | Show gateway, Gemini, webhook, and runtime status |
| `/diagnostics` | Show a redacted diagnostics report |
| `/help` | Show the command summary |

## How it works

This gateway currently uses a headless request model:

1. each Telegram prompt launches `gemini -p ... -o stream-json`
2. Gemini returns a `session_id`
3. the gateway stores that `session_id` per user
4. later prompts continue the same context with `--resume`

That keeps conversations continuous without depending on one forever-running subprocess.

## Current rough edges

This is where the MVP still feels like an MVP:

- interactive approval flow is only partially implemented in headless mode
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
