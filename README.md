# Gemini Telegram Gateway V2

> **Repository:** [github.com/Sheme1/gemini_cli_gateway_tg](https://github.com/Sheme1/gemini_cli_gateway_tg)

A modern, high-performance, and interactive Python-based Telegram Gateway for the [@google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli). It allows you to use your Gemini CLI interactive session directly from Telegram, completely keeping the context intact through long-lived processes.

---

## 📋 Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Production Deployment](#production-deployment)
  - [Systemd (Recommended)](#systemd-recommended)
  - [Docker](#docker)
- [Bot Commands](#bot-commands)
- [Advanced Features](#advanced-features)
  - [MCP Servers Management](#mcp-servers-management)
  - [Skills Management](#skills-management)
  - [Approval Modes](#approval-modes)
  - [Voice Messages](#voice-messages)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## ✨ Features

- **Zero Cold-Starts:** Keeps the `gemini` CLI running in the background as an interactive persistent process
- **Context Preservation:** Chat context builds up continuously. Clear it easily at any time via `/new`
- **JSON Streaming:** Smooth typing experience in Telegram through `stream-json` processing and rate-limiting updates
- **Voice Messages:** Supports sending audio messages. The Bot securely transcribes them using the Gemini API and feeds them via STT into the chat
- **Interactive Approvable Actions:** Whenever the CLI requires your tool usage permissions, the bot provides you with elegant inline-buttons (Approve / Reject / YOLO)
- **Control directly from Telegram:** Change the `gemini-cli` models or switch approval modes seamlessly in-chat
- **MCP Servers Integration:** Enable/disable MCP servers on the fly with inline buttons
- **Skills Management:** Manage Gemini CLI skills directly from Telegram
- **Session Management:** Resume previous conversations from history
- **Rate Limiting:** Built-in protection against spam and API abuse

---

## 🔧 Prerequisites

- **Python 3.12+** 
- **Node.js** (for Gemini CLI)
- **Gemini CLI** installed globally:
  ```bash
  npm install -g @google/gemini-cli
  ```
- **Telegram Bot Token** (get it from [@BotFather](https://t.me/BotFather))
- **Gemini API Key** (optional, for voice transcription)

---

## 🚀 Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
cd gemini_cli_gateway_tg
```

### 2. Set up the virtual environment & install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure the bot

Copy the example config and edit it:

```bash
cp .env.example .env
nano .env  # or use your favorite editor
```

**Required settings:**
- `TELEGRAM_BOT_TOKEN` — your bot token from @BotFather

**Optional settings:**
- `TARGET_CHAT_ID` — restrict bot access to specific chat ID
- `GEMINI_API_KEY` — for voice message transcription
- `GEMINI_MODEL` — default model (gemini-3-flash-preview)
- `GEMINI_APPROVAL_MODE` — yolo / default / auto_edit / plan
- `GEMINI_WORKING_DIR` — working directory for CLI (default: `~`)

### 4. Run the bot

```bash
python -m gateway.main
```

The bot should now be running! Open Telegram and send `/start` to your bot.

---

## ⚙️ Configuration

All configuration is done through the `.env` file. Here's a complete reference:

### Telegram Settings

```bash
TELEGRAM_BOT_TOKEN=your_token_here          # Required: Bot token from @BotFather
TARGET_CHAT_ID=                             # Optional: Restrict access to specific chat ID
```

### Gemini CLI Settings

```bash
GEMINI_MODEL=gemini-3-flash-preview         # Default model
GEMINI_APPROVAL_MODE=yolo                   # Approval mode: default/auto_edit/yolo/plan
GEMINI_WORKING_DIR=/home/user/projects      # Working directory (default: ~)
GEMINI_CLI_TIMEOUT=300                      # Timeout in seconds
GEMINI_SANDBOX=false                        # Run in sandbox mode
```

### Gemini API (for voice messages)

```bash
GEMINI_API_KEY=                             # API key for voice transcription
```

### Streaming Settings

```bash
STREAM_UPDATE_INTERVAL=1.5                  # Seconds between message updates
```

### Approval Settings

```bash
APPROVAL_TIMEOUT=120                        # Seconds before auto-rejection
```

### Logging

```bash
LOG_LEVEL=INFO                              # DEBUG / INFO / WARNING / ERROR
```

---

## 🚢 Production Deployment

### Systemd (Recommended)

Systemd ensures the bot starts automatically on boot and restarts on failure.

#### Автоматическая установка (рекомендуется)

Используй скрипт автоматической установки — он всё настроит за тебя:

```bash
# Убедись, что ты в папке проекта
cd gemini_cli_gateway_tg

# Сделай скрипт исполняемым
chmod +x install.sh

# Запусти установку
./install.sh
```

Скрипт автоматически:
- ✅ Определит текущего пользователя и пути
- ✅ Проверит наличие `.env` и виртуального окружения
- ✅ Создаст systemd service файл с правильными путями
- ✅ Установит и запустит сервис
- ✅ Включит автозапуск при загрузке системы

**Что нужно перед запуском:**
1. Создать `.env` файл: `cp .env.example .env && nano .env`
2. Установить зависимости: `pip install -r requirements.txt`
3. Установить Gemini CLI: `npm install -g @google/gemini-cli`

---

#### Ручная установка (альтернатива)

Если хочешь настроить всё вручную:

<details>
<summary>Развернуть инструкцию по ручной установке</summary>

##### 1. Edit the service file

Открой файл `telegram-gateway.service` в редакторе и замени пути на свои:

```bash
nano telegram-gateway.service
```

Найди секцию `[Service]` и измени следующие строки:

```ini
[Service]
User=your_username                          # Замени на своё имя пользователя (например: User=ubuntu)
WorkingDirectory=/path/to/gemini_cli_gateway_tg    # Полный путь к папке проекта (например: /home/ubuntu/gemini_cli_gateway_tg)
EnvironmentFile=/path/to/gemini_cli_gateway_tg/.env    # Полный путь к .env файлу
ExecStart=/path/to/gemini_cli_gateway_tg/.venv/bin/python -m gateway.main    # Полный путь к python в venv
```

**Пример для пользователя `ubuntu` с проектом в `/home/ubuntu/gemini_cli_gateway_tg`:**

```ini
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/gemini_cli_gateway_tg
EnvironmentFile=/home/ubuntu/gemini_cli_gateway_tg/.env
ExecStart=/home/ubuntu/gemini_cli_gateway_tg/.venv/bin/python -m gateway.main
```

**Как узнать свои пути:**
- Имя пользователя: `whoami`
- Текущая директория: `pwd` (выполни в папке проекта)

##### 2. Install and enable the service

```bash
# Copy service file to systemd directory
sudo cp telegram-gateway.service /etc/systemd/system/

# Reload systemd to recognize the new service
sudo systemctl daemon-reload

# Enable the service to start on boot
sudo systemctl enable telegram-gateway.service

# Start the service now
sudo systemctl start telegram-gateway.service
```

##### 3. Verify the service is running

```bash
# Check service status
sudo systemctl status telegram-gateway

# View logs in real-time
sudo journalctl -u telegram-gateway -f

# View last 100 lines of logs
sudo journalctl -u telegram-gateway -n 100
```

##### 4. Useful systemd commands

```bash
# Stop the service
sudo systemctl stop telegram-gateway

# Restart the service
sudo systemctl restart telegram-gateway

# Disable auto-start on boot
sudo systemctl disable telegram-gateway

# Check if service is enabled
sudo systemctl is-enabled telegram-gateway
```

##### 5. Update the bot

When you update the code:

```bash
cd /path/to/gemini_cli_gateway_tg
git pull
pip install -r requirements.txt  # If dependencies changed
sudo systemctl restart telegram-gateway
```

</details>

---

### Docker

Alternative deployment method using Docker.

#### 1. Build and run with Docker Compose

```bash
docker compose up -d
```

#### 2. View logs

```bash
docker compose logs -f gateway
```

#### 3. Stop the container

```bash
docker compose down
```

#### 4. Update the bot

```bash
git pull
docker compose up -d --build
```

---

## 🤖 Bot Commands

### Basic Commands

- `/start` — Start the bot and display welcome message
- `/new` — **Reset context** (terminates current Gemini process, starts fresh)
- `/help` — Display help information

### Session Management

- `/sessions` — View and resume previous conversation sessions
- `/status` — Check the current Gemini CLI session status

### Configuration

- `/model` — Switch Gemini model (gemini-3-flash-preview, gemini-3.1-pro-preview, etc.)
- `/settings` — Configure approval modes, timeout, and sandbox settings

### MCP & Skills

- `/mcp` — Manage MCP servers (enable/disable, view status)
- `/skills` — Manage Gemini CLI skills (enable/disable, view status)

---

## 🎯 Advanced Features

### MCP Servers Management

MCP (Model Context Protocol) servers extend Gemini CLI with additional capabilities.

**View MCP servers:**
```
/mcp
```

**Enable/disable servers:**
- Click the inline buttons next to each server name
- 🟢 = Enabled, 🔴 = Disabled

**Use MCP in prompts:**
```
/mcp exa search for latest AI news
```
or mention directly in any message:
```
@exa find information about quantum computing
```

**Refresh the list:**
- Click the "🔄 Обновить список" button (rate limited to once per 3 seconds)

---

### Skills Management

Skills are specialized agent capabilities for Gemini CLI.

**View skills:**
```
/skills
```

**Enable/disable skills:**
- Click the inline buttons next to each skill name
- 🟢 = Enabled, 🔴 = Disabled

**Use skills in prompts:**
```
/skills docx create a professional report about AI trends
```

**Available skills include:**
- `docx` — Create and edit Word documents
- `pdf` — Work with PDF files
- `humanizer` — Remove AI writing patterns
- `chrome-devtools` — Browser automation and debugging
- And more...

---

### Approval Modes

Control how Gemini CLI handles tool usage permissions.

**Available modes:**

1. **default** — Request approval for every tool call
   - Bot shows inline buttons: ✅ Approve / ❌ Reject / ⏭ YOLO
   - Timeout: 120 seconds (configurable)

2. **auto_edit** — Auto-approve edit tools, request approval for others
   - Balanced mode for safe automation

3. **yolo** — Auto-approve all actions
   - ⚠️ Use with caution! No confirmation required

4. **plan** — Read-only mode
   - CLI only plans actions without executing them

**Change approval mode:**
```
/settings → Approval Mode → Select mode
```

---

### Voice Messages

Send voice messages to the bot for transcription and processing.

**Requirements:**
- `GEMINI_API_KEY` must be set in `.env`

**How it works:**
1. Record and send a voice message in Telegram
2. Bot transcribes it using Gemini API
3. Transcribed text is sent to Gemini CLI
4. Bot responds with the answer

---

## 💻 Development

### Project Structure

```
gemini_cli_gateway_tg/
├── gateway/
│   ├── main.py              # Entry point
│   ├── config.py            # Configuration loader
│   ├── bot/                 # Telegram bot handlers
│   │   ├── handlers/        # Command and message handlers
│   │   ├── keyboards/       # Inline keyboards
│   │   └── middleware/      # Auth and rate limiting
│   ├── gemini/              # Gemini CLI integration
│   │   ├── session.py       # Process management
│   │   └── parser.py        # Stream-json parser
│   └── streaming/
│       └── editor.py        # Message streaming
├── tests/                   # Unit tests
├── .env.example             # Example configuration
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development dependencies
└── telegram-gateway.service # Systemd service file
```

### Development Workflow

**Environment:** Windows (local development) → Ubuntu (production)

1. Make changes on Windows
2. Test locally: `python -m gateway.main`
3. Commit: `git add . && git commit -m "description"`
4. Push: `git push`
5. On Ubuntu server: `git pull`
6. Restart: `sudo systemctl restart telegram-gateway`
7. Check logs: `sudo journalctl -u telegram-gateway -f`

### Running Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Run linter
ruff check .
ruff format --check .
```

### Code Quality

The project uses:
- **Ruff** for linting and formatting
- **pytest** for testing
- **GitHub Actions** for CI/CD (`.github/workflows/ci.yml`)

---

## 🔍 Troubleshooting

### Bot doesn't respond

**Check if the service is running:**
```bash
sudo systemctl status telegram-gateway
```

**View logs:**
```bash
sudo journalctl -u telegram-gateway -f
```

**Common issues:**
- Missing `TELEGRAM_BOT_TOKEN` in `.env`
- Gemini CLI not installed: `npm install -g @google/gemini-cli`
- Python version < 3.12

---

### `/mcp` or `/skills` shows empty list

**Check Gemini CLI installation:**
```bash
gemini mcp list
gemini skills list
```

**Install MCP servers:**
```bash
gemini mcp install exa
gemini mcp install context7
```

**Install skills:**
```bash
gemini skills install docx
gemini skills install pdf
```

---

### Voice messages don't work

**Ensure `GEMINI_API_KEY` is set:**
```bash
# In .env file
GEMINI_API_KEY=your_api_key_here
```

**Get API key:**
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create a new API key
3. Add it to `.env`

---

### "TelegramBadRequest: message is not modified" error

This error is now handled automatically. If you still see it in logs:
- Update to the latest version: `git pull`
- Restart the service: `sudo systemctl restart telegram-gateway`

---

### Rate limiting issues

If you see "⏳ Подожди N сек. перед следующим обновлением":
- This is normal behavior to prevent API abuse
- Wait 3 seconds between refresh button clicks
- Adjust `REFRESH_COOLDOWN_SECONDS` in `gateway/bot/handlers/callbacks.py` if needed

---

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes
4. Run tests: `pytest tests/ -v`
5. Run linter: `ruff check . && ruff format .`
6. Commit: `git commit -m "Add amazing feature"`
7. Push: `git push origin feature/amazing-feature`
8. Open a Pull Request

---

## 📄 License

This project is open source and available under the MIT License.

---

## 🙏 Acknowledgments

- [Gemini CLI](https://github.com/google-gemini/gemini-cli) by Google
- [aiogram](https://github.com/aiogram/aiogram) — Modern Telegram Bot framework
- All contributors and users of this project

---

## 📞 Support

- **Issues:** [GitHub Issues](https://github.com/Sheme1/gemini_cli_gateway_tg/issues)
- **Discussions:** [GitHub Discussions](https://github.com/Sheme1/gemini_cli_gateway_tg/discussions)
- **Telegram:** Contact the bot developer

---

**Made with ❤️ for the Gemini CLI community**
