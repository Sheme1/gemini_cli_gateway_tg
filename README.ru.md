# Gemini Telegram Gateway

[![CI](https://github.com/Sheme1/gemini_cli_gateway_tg/actions/workflows/ci.yml/badge.svg)](https://github.com/Sheme1/gemini_cli_gateway_tg/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)

**Языки:** [English](README.md) | [Русский](README.ru.md)

Используйте [Google Gemini CLI](https://github.com/google-gemini/gemini-cli) через Telegram.

Этот проект связывает Telegram и Gemini CLI, чтобы вы могли:

- общаться с Gemini из телефона
- сохранять контекст между сообщениями
- получать потоковый ответ прямо в Telegram
- получать сгенерированные файлы прямо в чат
- переключать MCP-серверы и skills не выходя из Telegram

> [!WARNING]
> Этот репозиторий пока остаётся экспериментальным MVP.
> Он уже полезный, но ещё сырой и не рассчитан на отполированный production-опыт.
> Форки, локальные правки и адаптация под себя здесь не просто допустимы, а приветствуются.

## Зачем это нужно

Gemini CLI отлично работает в терминале, но иногда хочется тот же сценарий прямо из Telegram:

- когда вы не за компьютером
- для быстрых запросов и уточняющих сообщений
- чтобы получать документы и артефакты на телефон
- чтобы держать лёгкий self-hosted мост, а не строить отдельный веб-интерфейс

## Возможности

- Сохранение контекста через Gemini `session_id` и `--resume`
- Потоковые ответы в Telegram через Gemini `stream-json`
- Три режима отображения: `compact`, `summary`, `detailed`
- UI для просмотра и переключения MCP-серверов
- UI для просмотра и переключения Gemini skills
- Транскрибация голосовых сообщений через Gemini API
- Автоматическое обнаружение артефактов и отправка файлов в Telegram
- `systemd` как основной путь деплоя на Linux
- Docker как вторичный вариант деплоя

## Быстрый старт

### 1. Клонируйте репозиторий

```bash
git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
cd gemini_cli_gateway_tg
```

### 2. Создайте виртуальное окружение

```bash
python3 -m venv .venv
source .venv/bin/activate
```

На Windows:

```powershell
.venv\Scripts\activate
```

### 3. Установите зависимости

```bash
pip install -r requirements.txt -r requirements-dev.txt
npm install -g @google/gemini-cli
```

### 4. Настройте бота

```bash
cp .env.example .env
```

Обязательно:

- `TELEGRAM_BOT_TOKEN`

Опционально, но полезно:

- `TARGET_CHAT_ID`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `GEMINI_APPROVAL_MODE`
- `GEMINI_WORKING_DIR`

Полный набор параметров запуска описан в [.env.example](.env.example).

### 5. Запустите локально

```bash
python -m gateway.main
```

Такой запуск лучше всего подходит для локальной разработки и быстрой проверки.
Для постоянного деплоя используйте `systemd`.

## Рекомендуемый деплой

Основной целевой способ деплоя проекта:

- Linux
- `systemd`
- один пользователь, который владеет репозиторием, виртуальным окружением и состоянием аутентификации Gemini CLI

### Установка как systemd-сервиса

Сначала подготовьте машину:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install -g @google/gemini-cli
cp .env.example .env
```

Затем выполните:

```bash
chmod +x install.sh
./install.sh
```

Установщик:

- определяет корень проекта по расположению скрипта
- проверяет Linux, `systemd`, `sudo`, `gemini`, `node`, `.env` и `.venv`
- рендерит `telegram-gateway.service` с корректными путями
- задаёт явный `PATH`, чтобы сервис видел `gemini` и `node`
- устанавливает и запускает `telegram-gateway`

Полезные команды:

```bash
sudo systemctl status telegram-gateway
sudo systemctl restart telegram-gateway
sudo journalctl -u telegram-gateway -f
```

## Docker

Поддержка Docker есть, но это вторичный путь.

```bash
docker compose up -d --build
docker compose logs -f gateway
```

Если используете Docker, помните, что авторизация Gemini CLI будет жить внутри volume контейнера, а не в пользовательском профиле хоста.

## Команды

| Команда | Описание |
| --- | --- |
| `/start` | Сбрасывает текущую пользовательскую сессию и показывает вводное сообщение |
| `/new` | Начинает новый диалог с Gemini |
| `/sessions` | Показывает сохранённые сессии Gemini и позволяет выбрать одну |
| `/mcp` | Показывает установленные MCP-серверы |
| `/skills` | Показывает установленные Gemini skills |
| `/model` | Позволяет выбрать активную модель Gemini |
| `/settings` | Меняет режим отображения и режим подтверждений |
| `/status` | Показывает простой статус шлюза |
| `/help` | Показывает краткую справку |

## Как это работает

Сейчас шлюз работает по headless-модели:

1. каждый Telegram-запрос запускает `gemini -p ... -o stream-json`
2. Gemini возвращает `session_id`
3. шлюз сохраняет этот `session_id` отдельно для пользователя
4. следующие сообщения продолжают тот же контекст через `--resume`

Это позволяет держать непрерывный диалог без одного постоянно живущего процесса Gemini CLI.

## Текущие шероховатости

Здесь пока особенно видно, что проект всё ещё MVP:

- интерактивный сценарий подтверждений в headless-режиме реализован только частично
- формат вывода Gemini CLI может меняться между релизами
- основная цель проекта — Linux + `systemd`; другие варианты деплоя могут требовать локальных доработок
- многим пользователям будет удобнее держать собственный форк под свой сценарий работы, модель авторизации или способ деплоя

Если вас это устраивает, значит репозиторий, скорее всего, вам подойдёт.

## Разработка

Локальные проверки:

```bash
ruff check .
ruff format --check .
pytest tests -v
```

CI настроен в [.github/workflows/ci.yml](.github/workflows/ci.yml).

## Вклад в проект

Вклад приветствуется, но проект пока намеренно прагматичный и ранний.

- небольшие точечные фиксы очень полезны
- улучшения документации очень полезны
- улучшения деплоя очень полезны
- особенно полезны правки, повышающие надёжность логики parser/session/deployment

Короткая памятка есть в [CONTRIBUTING.md](CONTRIBUTING.md).

## Лицензия

Проект распространяется под лицензией [MIT](LICENSE).
