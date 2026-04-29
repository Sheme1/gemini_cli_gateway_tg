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
- Постраничный `/sessions` с новыми диалогами сверху
- Три режима отображения: `compact`, `summary`, `detailed`
- UI для просмотра и переключения MCP-серверов
- UI для просмотра и переключения Gemini skills
- Модельные пресеты на каждого пользователя: `auto`, `pro`, `flash`, `flash-lite` и legacy manual-пресеты
- Экспериментальные личные workspace и персональный `GEMINI.md` через `/init`
- Подтверждение больших запросов и дневной учёт токенов
- Входящие вложения: фото, PDF, DOCX, audio/video и другие Telegram-файлы
- Транскрибация голосовых сообщений через Gemini API
- Автоматическое обнаружение артефактов и отправка файлов в Telegram
- Диагностика runtime, локальный `doctor`, timings последнего запроса и `/cancel`
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

Полный набор параметров запуска описан ниже и продублирован в
[.env.example](.env.example).

Проверить окружение деплоя без запуска polling:

```bash
python -m gateway.main --check-runtime
```

Проверить локальное окружение без обращения к Telegram:

```bash
python -m gateway.main --doctor
python -m gateway.main --doctor-json
```

### Справочник `.env`

Формат обычный: `KEY=value`, по одной переменной на строку. Кавычки обычно не
нужны. Значения со списком директорий пишите через запятую без пробелов.

| Переменная | Обязательна | Как писать и для чего нужна |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Да | Токен Telegram Bot API от BotFather, например `123456789:AA...`. В логах маскируется. |
| `TARGET_CHAT_ID` | Нет | Числовой Telegram chat/user id или allowlist через запятую. Примеры: `TARGET_CHAT_ID=111111111` или `TARGET_CHAT_ID=111111111,222222222`. Пустое значение разрешает все чаты, которые могут писать боту. У групп и супергрупп chat id может быть отрицательным. |
| `GEMINI_BIN` | Нет | Имя или полный путь к Gemini CLI. Если `gemini` есть в `PATH`, оставьте `gemini`; для systemd можно указать полный путь вроде `/home/user/.npm-global/bin/gemini`. |
| `GEMINI_MODEL` | Нет | Модель или alias Gemini CLI для `gemini -m`: `auto`, `pro`, `flash`, `flash-lite` или конкретный id модели. По умолчанию `auto`. |
| `GEMINI_TARGET_VERSION` | Нет | Ожидаемая версия Gemini CLI для `doctor`. По умолчанию `0.39.1`; несовпадение даёт warning, но не блокирует запуск. |
| `GEMINI_SKIP_TRUST` | Нет | По умолчанию `true`. Передаёт `--skip-trust` при headless-запусках Gemini CLI 0.39.1, чтобы trust-check не ждал интерактивный prompt. |
| `GEMINI_APPROVAL_MODE` | Нет | `default`, `auto_edit`, `yolo` или `plan`. Передаётся как `--approval-mode=...`; deprecated `--yolo` в Gemini CLI 0.39.1 не используется. |
| `GEMINI_POLICY_PATHS` | Нет | Дополнительные пользовательские policy TOML-файлы или папки через запятую. Передаются повторяющимися `--policy`. |
| `GEMINI_ADMIN_POLICY_PATHS` | Нет | Дополнительные admin policy TOML-файлы или папки через запятую. Передаются повторяющимися `--admin-policy`. |
| `GEMINI_ALLOWED_MCP_SERVER_NAMES` | Нет | Необязательный allowlist MCP-серверов через запятую. Передаётся повторяющимися флагами `--allowed-mcp-server-names`. |
| `GEMINI_EXTENSIONS` | Нет | Необязательный выбор extensions через запятую. `none` отключает extensions для запусков gateway. Передаётся повторяющимися `--extensions`. |
| `GEMINI_SCREEN_READER` | Нет | `true` или `false`. Передаёт `--screen-reader` для accessibility-friendly вывода CLI. |
| `GEMINI_WORKING_DIR` | Нет | Основная рабочая папка Gemini CLI. Лучше держать её достаточно узкой, чтобы CLI меньше сканировал при старте. |
| `GEMINI_INCLUDE_DIRECTORIES` | Нет | Дополнительные папки workspace через запятую, например `/srv/project/shared,/srv/docs`. Передаются как `--include-directories`. |
| `GEMINI_ARTIFACT_ROOTS` | Нет | Где искать созданные файлы для отправки в Telegram. Несколько папок через запятую. По умолчанию используется `GEMINI_WORKING_DIR`. |
| `GEMINI_CLI_TIMEOUT` | Нет | Сколько секунд ждать вывода от Gemini stdout перед остановкой процесса по таймауту. |
| `GEMINI_STREAM_READER_LIMIT_BYTES` | Нет | Максимальный размер одной строки stdout/stderr Gemini. По умолчанию `8388608` (8 MiB); увеличьте, если Gemini выдаёт очень крупное `stream-json` событие. |
| `GEMINI_SHUTDOWN_GRACE_SECONDS` | Нет | Сколько секунд ждать после мягкой остановки Gemini перед принудительным kill всего process group. |
| `GEMINI_SANDBOX` | Нет | `true` или `false`. При `true` передаёт Gemini CLI флаг `--sandbox`. |
| `GEMINI_STREAM_DEBUG` | Нет | `true` или `false`. Логирует raw stream/stderr Gemini; включайте только для диагностики. |
| `GEMINI_SOFT_FINALIZE_IDLE_SECONDS` | Нет | Если файл уже отправлен, но Gemini не завершился, через столько секунд шлюз мягко завершит ожидание. |
| `ARTIFACT_WATCH_INTERVAL` | Нет | Интервал проверки файлов во время выполнения запроса. |
| `ARTIFACT_STABLE_SECONDS` | Нет | Файл должен не меняться столько секунд перед отправкой в Telegram. |
| `GEMINI_API_KEY` | Нет | API key для расшифровки голосовых сообщений. Текстовые запросы идут через авторизацию Gemini CLI. |
| `STREAM_UPDATE_INTERVAL` | Нет | Минимальная пауза между обычными edit-сообщениями после первого чанка ответа. Live-обновления идут plain text, финальный ответ рендерится как безопасный Telegram HTML. |
| `STREAM_MIN_UPDATE_CHARS` | Нет | Минимальный прирост текста, после которого планируется очередное обновление Telegram сообщения. |
| `STREAM_RETRY_MAX_DELAY` | Нет | Максимальная пауза при Telegram rate-limit/retry-after. |
| `PROMPT_WARN_CHARS` | Нет | Длина запроса, после которой бот просит inline-подтверждение перед отправкой в Gemini. |
| `PROMPT_MAX_CHARS` | Нет | Жёсткий лимит длины запроса. Более длинные запросы отклоняются до запуска Gemini. |
| `PROMPT_CONFIRM_TIMEOUT` | Нет | Сколько секунд действует подтверждение большого запроса. |
| `ATTACHMENT_MAX_BYTES` | Нет | Максимальный размер входящего Telegram-вложения. По умолчанию `20971520` (20 MiB), то есть лимит скачивания Bot API. |
| `ATTACHMENT_DOWNLOAD_TIMEOUT` | Нет | Сколько секунд ждать скачивания одного Telegram-вложения. |
| `ATTACHMENT_RETENTION_DAYS` | Нет | Сколько дней хранить скачанные входные файлы в `GATEWAY_STATE_DIR/uploads`. По умолчанию `7`. |
| `ATTACHMENT_ALBUM_DEBOUNCE_SECONDS` | Нет | Сколько секунд ждать остальные элементы Telegram media group перед одним общим запросом в Gemini. |
| `USER_DAILY_TOKEN_LIMIT` | Нет | Дневной лимит токенов на пользователя по result stats Gemini. `0` отключает лимит. |
| `GLOBAL_DAILY_TOKEN_LIMIT` | Нет | Общий дневной лимит токенов по result stats Gemini. `0` отключает лимит. |
| `POLLING_TIMEOUT` | Нет | Timeout long polling, который передаётся в aiogram. |
| `POLLING_CONCURRENCY_LIMIT` | Нет | Максимум параллельных update handlers. Для одного пользователя запросы Gemini всё равно защищены lock-ом. |
| `GATEWAY_STATE_DIR` | Нет | Папка состояния шлюза и пользовательских настроек. Относительный путь считается от рабочей директории процесса. |
| `GATEWAY_SESSION_AUTO_RESUME_LATEST` | Нет | По умолчанию `true`. Если сохранённой active session ещё нет, gateway продолжит Gemini CLI `latest` для пользовательского проекта вместо тихого старта нового чата. |
| `GATEWAY_EXPERIMENTAL_MULTI_USER_WORKSPACES` | Нет | По умолчанию `false`. При `true` каждый Telegram `from_user.id` получает отдельный workspace, project sessions, artifacts, profile и `GEMINI.md`, но Gemini CLI auth/HOME остаётся общим. |
| `GATEWAY_USER_WORKSPACES_DIR` | Нет | Базовая папка экспериментальных пользовательских workspace. Пустое значение означает `<GATEWAY_STATE_DIR>/users`; для Ubuntu/systemd удобно `/srv/gemini-gateway/users`. |
| `APPROVAL_TIMEOUT` | Нет | Сколько секунд ждать интерактивное подтверждение. Headless approval всё ещё ограничен поведением Gemini CLI. |
| `LOG_MODE` | Нет | `quiet`, `normal` или `debug`. Управляет базовым уровнем Python-логов. |
| `LOG_LEVEL` | Нет | Старый явный override уровня Python-логов. Оставьте пустым, чтобы работал `LOG_MODE`. |

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

Для shared/multi-user Ubuntu-сервера удобная раскладка состояния:

```env
GATEWAY_STATE_DIR=/srv/gemini-gateway/state
GATEWAY_EXPERIMENTAL_MULTI_USER_WORKSPACES=true
GATEWAY_USER_WORKSPACES_DIR=/srv/gemini-gateway/users
```

Режим включается только явно. Чтобы отключить его позже, поставьте
`GATEWAY_EXPERIMENTAL_MULTI_USER_WORKSPACES=false` и выполните
`sudo systemctl restart telegram-gateway`.

Затем выполните:

```bash
chmod +x install.sh
./install.sh
```

Установщик:

- определяет корень проекта по расположению скрипта
- проверяет Linux, `systemd`, `sudo`, `gemini`, `node`, `.env` и `.venv`
- запускает `python -m gateway.main --check-runtime` перед установкой сервиса
- рендерит `telegram-gateway.service` с корректными путями
- задаёт явный `PATH`, чтобы сервис видел `gemini` и `node`
- устанавливает и запускает `telegram-gateway`

Полезные команды:

```bash
sudo systemctl status telegram-gateway
sudo systemctl restart telegram-gateway
sudo journalctl -u telegram-gateway -f
```

### Обновление существующего systemd-деплоя

Быстрый путь:

```bash
chmod +x update.sh
./update.sh
```

Скрипт обновления выполняет `git pull --ff-only`, ставит Python-зависимости в
`.venv`, запускает `python -m gateway.main --doctor`, проверяет, отличается ли
установленный systemd unit от текущего отрендеренного шаблона, и перезапускает
`telegram-gateway`.

Ручной путь:

```bash
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m gateway.main --doctor
sudo systemctl restart telegram-gateway
sudo systemctl status telegram-gateway --no-pager -l
```

Для обычных изменений Python-кода достаточно
`sudo systemctl restart telegram-gateway`. `sudo systemctl daemon-reload` нужен
только если изменился установленный unit-файл или если вы заново запускаете
`./install.sh`, который сам выполняет daemon reload.

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
| `/start` | Показывает вводное сообщение без сброса текущей Gemini-сессии |
| `/new` | Явно очищает active session и начинает новый диалог с Gemini |
| `/sessions [фильтр\|latest]` | Показывает сохранённые сессии Gemini: поиск по title/id/index, latest, открыть, удалить или экспортировать TXT |
| `/mcp` | Показывает установленные MCP-серверы |
| `/skills` | Показывает установленные Gemini skills |
| `/init` | Запускает wizard настройки личного `GEMINI.md` |
| `/init reset` | Сбрасывает анкету и генерирует новый preview |
| `/model` | Позволяет выбрать активную модель Gemini |
| `/settings` | Меняет режим отображения и режим подтверждений |
| `/context` | Показывает модель, пресет, session id и рабочие папки текущего пользователя |
| `/usage` | Показывает дневной расход токенов и статистику последнего запроса |
| `/doctor` | Запускает локальную диагностику окружения gateway |
| `/cancel` | Останавливает текущий запрос Gemini |
| `/status` | Показывает состояние шлюза, Gemini, webhook и runtime |
| `/diagnostics` | Показывает redacted диагностический отчёт |
| `/help` | Показывает краткую справку |

## Как это работает

Сейчас шлюз работает по headless-модели:

1. каждый Telegram-запрос запускает `gemini -p ... -o stream-json --skip-trust --approval-mode=...`
2. Gemini возвращает `session_id`
3. шлюз сохраняет этот `session_id` отдельно для пользователя в `session_state.json`
4. следующие сообщения продолжают тот же контекст через `--resume`, включая случаи после `systemctl restart` или `update.sh`

Это позволяет держать непрерывный диалог без одного постоянно живущего процесса Gemini CLI.

Входящие Telegram-вложения сохраняются в `GATEWAY_STATE_DIR/uploads`, а не внутри
`GEMINI_WORKING_DIR`. Gateway передаёт папку конкретного запроса в Gemini CLI
через `--include-directories` и дописывает к пользовательскому prompt нативные
ссылки Gemini CLI вида `@{полный_путь}`. Gateway не меняет выбранную модель, а
запросы с вложениями продолжают ту же сохранённую chat-сессию, что и текстовые
запросы. PDF, изображения, audio и video читает нативный file-reading Gemini CLI.
Для DOCX и text-like файлов gateway дополнительно создаёт `.txt` sidecar и
добавляет его отдельной строкой `@{полный_путь}`. Остальные бинарные файлы
принимаются до лимита скачивания Telegram Bot API, но глубина понимания зависит
от поддержки формата в Gemini CLI.

Если включить `GATEWAY_EXPERIMENTAL_MULTI_USER_WORKSPACES=true`, gateway создаёт
отдельную файловую область для каждого Telegram `from_user.id`:

```text
<GATEWAY_USER_WORKSPACES_DIR>/tg-user-123456789/
  workspace/GEMINI.md
  artifacts/
  profile.json
```

Запросы запускаются из личного `workspace/`, поэтому project sessions Gemini CLI
и `GEMINI.md` не смешиваются между людьми. Отправка артефактов сканирует только
workspace/artifacts текущего пользователя. Gemini CLI auth, `HOME`, MCP, skills
и глобальные CLI-настройки остаются общими, потому что этот режим специально
сохраняет один серверный аккаунт и один Gemini login.

`/init` задаёт пять коротких вопросов, сохраняет ответы в `profile.json`, просит
Gemini CLI сгенерировать компактный Markdown-preview из внутренней служебной
папки gateway, проверяет что это личные инструкции без внутренних деталей
сервера/tools, и записывает `workspace/GEMINI.md` только после подтверждения
пользователя. Внутренние `/init`-запросы не меняют active session пользователя
и не попадают в его список `/sessions`.

`/sessions` использует `gemini --list-sessions`, разбирает текстовый формат
Gemini CLI 0.39.1, разворачивает порядок так, чтобы новые диалоги были сверху,
и показывает по пять диалогов на страницу. Удаление сначала использует
`gemini --delete-session <session_uuid>`, затем fallback на index из списка, если
нужно. В Gemini CLI 0.39.1 нет отдельного JSON-вывода для списка сессий, поэтому
парсер покрыт тестами.

Первый настоящий чанк ответа сразу редактирует сообщение в Telegram как plain
text с `parse_mode=None`, чтобы неполный Markdown/HTML не ломал потоковые edits.
Следующие обновления объединяются через `STREAM_UPDATE_INTERVAL` и
`STREAM_MIN_UPDATE_CHARS`, чтобы не упираться в Telegram flood-limit. После
завершения stream gateway нормализует финальный текст и рендерит безопасный
Telegram HTML; если Telegram отклонит HTML, бот вернётся к plain text.

Active session и выбор модели хранятся отдельно для каждого
Telegram-пользователя в `GATEWAY_STATE_DIR`; модель из `.env` остаётся fallback.
`/start`, `/cancel` и смена модели не сбрасывают active session. Предпочтительные
пресеты используют alias Gemini CLI (`auto`, `pro`, `flash`, `flash-lite`),
чтобы сохранялась CLI-side model routing. Счётчики usage пишутся в `usage.json`
и содержат только totals, result status, stats metadata и metadata последнего
запроса, без текста пользовательских запросов.

ACP (`gemini --acp`) не включён как основной транспорт. В Gemini CLI 0.39.1 это
JSON-RPC/stdio режим в первую очередь для IDE/editor integrations. Шлюз остаётся
на headless `stream-json`, потому что так сохраняется совместимость с MCP,
skills, extensions, обычной конфигурацией Gemini CLI, отправкой артефактов и
Telegram streaming.

## Текущие шероховатости

Здесь пока особенно видно, что проект всё ещё MVP:

- интерактивное подтверждение не продолжается из Telegram в headless-режиме; используйте approval mode или policy rules
- формат вывода Gemini CLI может меняться между релизами; если снова видны
  проблемы с пробелами или форматированием, временно включите
  `GEMINI_STREAM_DEBUG=true`
- experimental multi-user workspaces изолирует project-файлы, но не общий Gemini CLI auth/HOME
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
