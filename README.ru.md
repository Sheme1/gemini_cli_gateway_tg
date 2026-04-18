# Gemini Telegram Gateway

**Языки:** [English](README.md) | [Русский](README.ru.md)

Telegram-шлюз для [Google Gemini CLI](https://github.com/google-gemini/gemini-cli) на Python и aiogram. Он позволяет работать с Gemini из Telegram, сохранять контекст между сообщениями, стримить частичные ответы и автоматически отправлять в чат сгенерированные файлы.

Репозиторий: [github.com/Sheme1/gemini_cli_gateway_tg](https://github.com/Sheme1/gemini_cli_gateway_tg)

## Обзор

Проект сейчас ориентирован на один основной production-сценарий:

- Linux-хост
- `systemd`-сервис `telegram-gateway`
- Gemini CLI установлен у того же пользователя, который владеет репозиторием и состоянием аутентификации Gemini CLI

Локальный запуск `python -m gateway.main` по-прежнему поддерживается, но предназначен для разработки и быстрой локальной проверки, а не как основной способ фонового production-запуска.

## Актуальная Модель Работы

Шлюз **больше не** держит один постоянный интерактивный процесс Gemini CLI.

Текущее поведение:

1. Каждый Telegram-запрос запускает отдельный headless-процесс `gemini -p ... -o stream-json`.
2. Gemini возвращает `session_id` в событии `init`.
3. Шлюз сохраняет активный `session_id` отдельно для каждого Telegram-пользователя.
4. Следующие запросы продолжают контекст через `--resume <session_id>`.

Так сохраняется история диалога, но без зависимости от одного вечного CLI-процесса.

## Что Сейчас Поддерживает Шлюз

- Сохранение контекста по пользователю через `session_id` + `--resume`
- Потоковые обновления Telegram-сообщения из Gemini `stream-json`
- Три режима отображения прогресса инструментов: `compact`, `summary`, `detailed`
- UI для просмотра и переключения MCP-серверов
- UI для просмотра и переключения Gemini skills
- Транскрибация голосовых сообщений через Gemini API
- Автоматическое обнаружение артефактов и отправка файлов обратно в Telegram
- Soft finalize, если Gemini не прислал финальный `result`, но итоговый файл уже стабилизировался
- Опциональное ограничение входящих сообщений по `TARGET_CHAT_ID`

## Важные Ограничения

- Inline-интерфейс подтверждений уже есть, и запросы подтверждения детектятся, но полноценное продолжение выполнения из Telegram пока ограничено в headless-потоке, потому что `SessionManager.answer_approval()` сейчас остаётся заглушкой.
- Для продакшна лучше использовать `GEMINI_APPROVAL_MODE=yolo`, `auto_edit` или `plan`.
- `APPROVAL_TIMEOUT` загружается из конфига, но в текущем headless-потоке подтверждений активно не применяется.
- Автоотправка оптимизирована под распространённые форматы документов, изображений, таблиц, архивов и презентаций. Файлы-спутники вроде `.md` тоже можно отправлять, если Gemini явно сослался на них в выводе.

## Структура Проекта

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

## Требования

- Python 3.12+
- Node.js + npm
- `@google/gemini-cli`, установленный у пользователя сервиса
- уже выполненная аутентификация Gemini CLI у этого же пользователя
- токен Telegram-бота от [@BotFather](https://t.me/BotFather)
- опционально Gemini API key для голосовой транскрибации

## Быстрый Локальный Запуск

Это рекомендуемый путь для локальной разработки, но не для production-демонизации.

```bash
git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
cd gemini_cli_gateway_tg

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

npm install -g @google/gemini-cli

cp .env.example .env
# отредактируйте .env

python -m gateway.main
```

На Windows активируйте окружение через `.venv\Scripts\activate`.

## Конфигурация

Вся runtime-конфигурация задаётся через `.env`.

| Переменная | Значение по умолчанию | Описание |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | нет | Обязательный токен Telegram-бота. |
| `TARGET_CHAT_ID` | пусто | Опциональное ограничение входящих сообщений одним chat ID. |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Модель, передаваемая в `gemini -m`. |
| `GEMINI_APPROVAL_MODE` | `yolo` | Режим approval для Gemini CLI. Поддерживаются `default`, `auto_edit`, `yolo`, `plan`. |
| `GEMINI_WORKING_DIR` | домашняя директория пользователя | Рабочая папка для запуска Gemini-процессов. |
| `GEMINI_ARTIFACT_ROOTS` | `GEMINI_WORKING_DIR` | Список директорий через запятую, где искать созданные файлы. |
| `GEMINI_CLI_TIMEOUT` | `600` | Таймаут неактивности одного Gemini-запроса. `600` секунд рекомендуется для долгих MCP/skill операций. |
| `GEMINI_SANDBOX` | `false` | Добавляет `--sandbox` к вызовам Gemini CLI. |
| `GEMINI_STREAM_DEBUG` | `false` | Включает сырые логи `stream-json` и диагностический вывод парсера. |
| `GEMINI_SOFT_FINALIZE_IDLE_SECONDS` | `90` | Порог мягкого завершения после отправки файла, если Gemini так и не прислал финальный `result`. |
| `ARTIFACT_WATCH_INTERVAL` | `1.0` | Интервал опроса готовности артефактов, пока запрос ещё выполняется. |
| `ARTIFACT_STABLE_SECONDS` | `5.0` | Сколько секунд файл должен не меняться перед автоотправкой. |
| `GEMINI_API_KEY` | пусто | Включает транскрибацию голосовых сообщений через Gemini API. |
| `STREAM_UPDATE_INTERVAL` | `1.5` | Минимальная задержка между edit-сообщениями в Telegram. |
| `APPROVAL_TIMEOUT` | `120` | Резервный параметр UI подтверждений; сейчас не применяется в headless-потоке подтверждений. |
| `LOG_LEVEL` | `INFO` | Уровень логирования приложения. |

## Команды Бота

| Команда | Что делает |
| --- | --- |
| `/start` | Сбрасывает текущую пользовательскую сессию и показывает основную справку. |
| `/new` | Очищает сохранённую Gemini-сессию для текущего пользователя. |
| `/sessions` | Показывает список сессий Gemini из истории CLI и позволяет выбрать одну. |
| `/mcp` | Показывает установленные MCP-серверы и кнопки переключения. |
| `/mcp <server> <prompt>` | Принудительно отправляет запрос через `@server ...`. |
| `/skills` | Показывает установленные Gemini skills и кнопки переключения. |
| `/skills <skill> <prompt>` | Принудительно отправляет запрос через `@skill ...`. |
| `/model` | Открывает inline-выбор модели. |
| `/settings` | Показывает render mode, approval mode, timeout и sandbox state; позволяет менять render и approval режимы. |
| `/status` | Возвращает простой статус работоспособности шлюза. |
| `/help` | Показывает краткую справку по командам. |

## Поведение Во Время Работы

### Стриминг и Рендеринг

- Вывод Gemini разбирается из newline-delimited событий `stream-json`.
- Частота обновлений Telegram ограничивается через `STREAM_UPDATE_INTERVAL`.
- Длинные ответы безопасно режутся, чтобы не выйти за лимит Telegram в 4096 символов.
- Пользовательские предпочтения по отображению сохраняются в `.gateway_state/user_settings.json`.

### Отправка Артефактов

- Шлюз ищет кандидатов на отправку в assistant text, tool arguments и tool results.
- Распространённые форматы вроде `.docx`, `.pdf`, `.xlsx`, `.pptx`, `.png`, `.jpg`, `.zip` отправляются автоматически.
- Явные маркеры `[SEND_FILE: path]` из вывода Gemini тоже поддерживаются.
- Если файл стабилизировался раньше, чем Gemini прислал финальный `result`, шлюз может отправить файл и мягко завершить ответ в Telegram.

### Голосовой Поток

- Голосовые сообщения скачиваются из Telegram, отправляются в Gemini API на транскрибацию и затем передаются в Gemini CLI как текст.
- Для голосовой поддержки нужен `GEMINI_API_KEY`.

## Production-Развёртывание Через systemd

`systemd` — основной production-target для этого проекта.

### Подготовка хоста

Установите Python, Node.js, npm и Gemini CLI любым удобным для вашей системы способом. Затем под тем же пользователем, который будет владельцем сервиса:

```bash
git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
cd gemini_cli_gateway_tg

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

npm install -g @google/gemini-cli

cp .env.example .env
# отредактируйте .env
```

Важно:

- Запускайте `install.sh` от того же пользователя, у которого `gemini` уже успешно работает.
- Если Gemini CLI установлен через `nvm` или другой user-local toolchain, убедитесь, что перед запуском установщика команды `gemini` и `node` доступны в текущем shell.
- Установщик не ставит Python, Node.js, npm, не создаёт `.env` и `.venv`. Он только рендерит и регистрирует `systemd`-unit.

### Автоматическая установка сервиса

```bash
chmod +x install.sh
./install.sh
```

Установщик:

- определяет корень проекта по расположению самого скрипта, а не по текущей директории shell
- проверяет Linux + `systemd` + `sudo`
- проверяет `.env`, `.venv/bin/python`, `gemini` и `node`
- рендерит `telegram-gateway.service` с конкретными путями
- добавляет `PATH`, в который входят найденные каталоги бинарников Gemini и Node
- устанавливает или обновляет `/etc/systemd/system/telegram-gateway.service`
- делает `daemon-reload`, включает сервис и запускает или перезапускает его

### Управление сервисом

```bash
sudo systemctl status telegram-gateway
sudo systemctl restart telegram-gateway
sudo systemctl stop telegram-gateway
sudo journalctl -u telegram-gateway -f
```

### Ручная установка systemd-unit

Если хотите управлять unit-файлом вручную, откройте `telegram-gateway.service` и замените все плейсхолдеры:

- `__SERVICE_USER__`
- `__PROJECT_DIR__`
- `__ENV_FILE__`
- `__HOME_DIR__`
- `__SERVICE_PATH__`
- `__PYTHON_BIN__`

Строка `PATH` должна включать каталоги, где лежат и `gemini`, и `node`, особенно если они установлены через `nvm` или другой user-local способ.

Затем установите unit:

```bash
sudo install -m 0644 telegram-gateway.service /etc/systemd/system/telegram-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable telegram-gateway
sudo systemctl start telegram-gateway
```

### Обновление деплоя

```bash
cd /path/to/gemini_cli_gateway_tg
git pull

source .venv/bin/activate
pip install -r requirements.txt

sudo systemctl restart telegram-gateway
```

Повторно запускайте `./install.sh`, если сменился пользователь сервиса, переехала директория проекта, был пересоздан `venv` или Gemini CLI переустановили в другое место.

## Docker

Docker остаётся вторичным способом деплоя, но рекомендуемый production-путь — это `systemd`.

Текущая Docker-схема:

- ставит `@google/gemini-cli` внутрь образа
- монтирует `./workspace` в `/workspace`
- сохраняет состояние Gemini CLI в volume `gemini_home`
- сохраняет UI-состояние шлюза в volume `gateway_state`

Запуск:

```bash
docker compose up -d --build
docker compose logs -f gateway
```

Если используете Docker, помните, что Gemini CLI auth хранится внутри контейнерного volume, а не в пользовательском окружении хоста.

## Разработка, Тесты и CI

Установите dev-зависимости:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Локальные проверки:

```bash
ruff check .
ruff format --check .
pytest tests -v
```

CI в `.github/workflows/ci.yml` запускает Ruff и pytest на push и pull request в `main`.

## Troubleshooting

### Сервис запускается руками, но падает под systemd

Чаще всего это означает, что сервисный пользователь не видит `gemini`, `node` или корректное состояние аутентификации Gemini CLI.

Проверьте:

```bash
sudo systemctl status telegram-gateway
sudo journalctl -u telegram-gateway -n 200 --no-pager
```

Затем убедитесь, что:

- `gemini` работает именно у того Unix-пользователя, от которого запущен сервис
- `node` установлен и доступен
- в `.env` указаны валидные директории для `GEMINI_WORKING_DIR` и `GEMINI_ARTIFACT_ROOTS`

### `/mcp` или `/skills` пустые

Проверьте CLI напрямую:

```bash
gemini mcp list
gemini skills list
```

Если списки пустые, сначала установите нужные MCP-серверы или skills в Gemini CLI.

### Файлы не отправляются автоматически

Проверьте:

- файл был создан внутри `GEMINI_WORKING_DIR` или одной из директорий `GEMINI_ARTIFACT_ROOTS`
- у файла поддерживаемое расширение, либо Gemini вывел явный маркер `[SEND_FILE: ...]`
- файл оставался неизменным не меньше `ARTIFACT_STABLE_SECONDS`

### Голосовые сообщения не работают

Убедитесь, что в `.env` задан `GEMINI_API_KEY`.

### Approval-кнопки не продолжают выполнение

Это известное текущее ограничение headless-пути подтверждений. Для продакшна используйте `yolo`, `auto_edit` или `plan`, пока интерактивный approval не будет реализован end-to-end.
