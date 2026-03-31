# Gemini Telegram Gateway V2

> **Repository:** [github.com/Sheme1/gemini_cli_gateway_tg](https://github.com/Sheme1/gemini_cli_gateway_tg)

A modern, high-performance, and interactive Python-based Telegram Gateway for the [@google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli). It allows you to use your Gemini CLI interactive session directly from Telegram, completely keeping the context intact through long-lived processes.

## Features

- **Zero Cold-Starts:** Keeps the `gemini` CLI running in the background as an interactive persistent process.
- **Context Preservation:** Chat context builds up continuously. Clear it easily at any time via `/new`.
- **JSON Streaming:** Smooth typing experience in Telegram through `stream-json` processing and rate-limiting updates.
- **Voice Messages:** Supports sending audio messages. The Bot securely transcribes them using the Gemini API and feeds them via STT into the chat.
- **Interactive Approvable Actions:** Whenever the CLI requires your tool usage permissions, the bot provides you with elegant inline-buttons (Approve / Reject / YOLO).
- **Control directly from Telegram:** Change the `gemini-cli` models or switch approval modes seamlessly in-chat.

## Prerequisites

- Python 3.12+ 
- Node.js (for Gemini CLI)
- `@google-gemini/gemini-cli` installed globally (`npm install -g @google/gemini-cli`)

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
   cd gemini_cli_gateway_tg
   ```

2. **Set up the virtual environment & install dependencies:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Provide your tokens:**
   Copy the example config and open it:
   ```bash
   cp .env.example .env
   # Edit .env and supply your TELEGRAM_BOT_TOKEN and TARGET_CHAT_ID
   ```

4. **Run the bot:**
   ```bash
   python -m gateway.main
   ```

## Production Deployment

Production usage is recommended using the provided `telegram-gateway.service` unit file or the `docker-compose.yml`.

### Systemd example:
```bash
sudo cp telegram-gateway.service /etc/systemd/system/
sudo systemctl enable --now telegram-gateway.service
```

## Available Bot Commands

- `/start` - Displays the bot's greeting.
- `/new` - Restarts the Gemini context (terminates current process, fires a new instance).
- `/model` - Switch your loaded Gemini Model on the fly.
- `/settings` - Configure Approval modes and other interactive limits.
- `/status` - Ping the state of your currently active subprocess.
- `/help` - Overview of functionalities.
