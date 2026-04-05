# Gemini Telegram Gateway V2

**Читать на других языках:** [English](README.md) | [Русский](README.ru.md)

> **Репозиторий:** [github.com/Sheme1/gemini_cli_gateway_tg](https://github.com/Sheme1/gemini_cli_gateway_tg)

Современный, высокопроизводительный и интерактивный Telegram-шлюз на Python для [@google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli). Позволяет использовать интерактивную сессию Gemini CLI напрямую из Telegram, полностью сохраняя контекст через долгоживущие процессы.

---

## 📋 Содержание

- [Возможности](#возможности)
- [Требования](#требования)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)
- [Production развёртывание](#production-развёртывание)
  - [Systemd (рекомендуется)](#systemd-рекомендуется)
  - [Docker](#docker)
- [Команды бота](#команды-бота)
- [Расширенные возможности](#расширенные-возможности)
  - [Управление MCP серверами](#управление-mcp-серверами)
  - [Управление Skills](#управление-skills)
  - [Режимы подтверждения](#режимы-подтверждения)
  - [Голосовые сообщения](#голосовые-сообщения)
- [Разработка](#разработка)
- [Решение проблем](#решение-проблем)
- [Участие в проекте](#участие-в-проекте)

---

## ✨ Возможности

- **Нулевые холодные старты:** Держит `gemini` CLI запущенным в фоне как интерактивный постоянный процесс
- **Сохранение контекста:** Контекст диалога накапливается непрерывно. Легко очистить в любой момент через `/new`
- **JSON стриминг:** Плавный опыт набора текста в Telegram через обработку `stream-json` и ограничение частоты обновлений
- **Голосовые сообщения:** Поддержка аудио сообщений. Бот безопасно транскрибирует их через Gemini API и передаёт в чат
- **Интерактивные подтверждения действий:** Когда CLI требует разрешения на использование инструментов, бот предоставляет элегантные inline-кнопки (Одобрить / Отклонить / YOLO)
- **Управление прямо из Telegram:** Меняйте модели `gemini-cli` или переключайте режимы подтверждения прямо в чате
- **Интеграция MCP серверов:** Включайте/выключайте MCP серверы на лету с помощью inline-кнопок
- **Управление Skills:** Управляйте навыками Gemini CLI прямо из Telegram
- **Управление сессиями:** Возобновляйте предыдущие разговоры из истории
- **Ограничение частоты запросов:** Встроенная защита от спама и злоупотребления API

---

## 🔧 Требования

- **Python 3.12+** 
- **Node.js** (для Gemini CLI)
- **Gemini CLI** установлен глобально:
  ```bash
  npm install -g @google/gemini-cli
  ```
- **Telegram Bot Token** (получить у [@BotFather](https://t.me/BotFather))
- **Gemini API Key** (опционально, для транскрибации голоса)

---

## 🚀 Быстрый старт

### 1. Клонировать репозиторий

```bash
git clone https://github.com/Sheme1/gemini_cli_gateway_tg.git
cd gemini_cli_gateway_tg
```

### 2. Настроить виртуальное окружение и установить зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate  # На Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Настроить бота

Скопируйте пример конфигурации и отредактируйте его:

```bash
cp .env.example .env
nano .env  # или используйте ваш любимый редактор
```

**Обязательные настройки:**
- `TELEGRAM_BOT_TOKEN` — токен вашего бота от @BotFather

**Опциональные настройки:**
- `TARGET_CHAT_ID` — ограничить доступ к боту определённым chat ID
- `GEMINI_API_KEY` — для транскрибации голосовых сообщений
- `GEMINI_MODEL` — модель по умолчанию (gemini-3-flash-preview)
- `GEMINI_APPROVAL_MODE` — yolo / default / auto_edit / plan
- `GEMINI_WORKING_DIR` — рабочая директория для CLI (по умолчанию: `~`)

### 4. Запустить бота

```bash
python -m gateway.main
```

Бот должен запуститься! Откройте Telegram и отправьте `/start` вашему боту.

---

## ⚙️ Конфигурация

Вся конфигурация выполняется через файл `.env`. Вот полный справочник:

### Настройки Telegram

```bash
TELEGRAM_BOT_TOKEN=your_token_here          # Обязательно: токен бота от @BotFather
TARGET_CHAT_ID=                             # Опционально: ограничить доступ к определённому chat ID
```

### Настройки Gemini CLI

```bash
GEMINI_MODEL=gemini-3-flash-preview         # Модель по умолчанию
GEMINI_APPROVAL_MODE=yolo                   # Режим подтверждения: default/auto_edit/yolo/plan
GEMINI_WORKING_DIR=/home/user/projects      # Рабочая директория (по умолчанию: ~)
GEMINI_CLI_TIMEOUT=300                      # Таймаут в секундах
GEMINI_SANDBOX=false                        # Запуск в режиме песочницы
```

### Gemini API (для голосовых сообщений)

```bash
GEMINI_API_KEY=                             # API ключ для транскрибации голоса
```

### Настройки стриминга

```bash
STREAM_UPDATE_INTERVAL=1.5                  # Секунды между обновлениями сообщений
```

### Настройки подтверждений

```bash
APPROVAL_TIMEOUT=120                        # Секунды до авто-отклонения
```

### Логирование

```bash
LOG_LEVEL=INFO                              # DEBUG / INFO / WARNING / ERROR
```

---

## 🚢 Production развёртывание

### Systemd (рекомендуется)

Systemd обеспечивает автоматический запуск бота при загрузке системы и перезапуск при сбоях.

#### Автоматическая установка (рекомендуется)

Используйте скрипт автоматической установки — он всё настроит за вас:

```bash
# Убедитесь, что вы в директории проекта
cd gemini_cli_gateway_tg

# Сделайте скрипт исполняемым
chmod +x install.sh

# Запустите установку
./install.sh
```

Скрипт автоматически:
- ✅ Определит текущего пользователя и пути
- ✅ Проверит наличие `.env` и виртуального окружения
- ✅ Создаст systemd service файл с правильными путями
- ✅ Установит и запустит сервис
- ✅ Включит автозапуск при загрузке системы

**Требования перед запуском:**
1. Создать `.env` файл: `cp .env.example .env && nano .env`
2. Установить зависимости: `pip install -r requirements.txt`
3. Установить Gemini CLI: `npm install -g @google/gemini-cli`

---

#### Ручная установка (альтернатива)

Если предпочитаете настроить всё вручную:

<details>
<summary>Развернуть инструкцию по ручной установке</summary>

##### 1. Отредактировать service файл

Откройте `telegram-gateway.service` в редакторе и обновите пути:

```bash
nano telegram-gateway.service
```

Найдите секцию `[Service]` и измените эти строки:

```ini
[Service]
User=your_username                          # Замените на ваше имя пользователя (например: User=ubuntu)
WorkingDirectory=/path/to/gemini_cli_gateway_tg    # Полный путь к директории проекта (например: /home/ubuntu/gemini_cli_gateway_tg)
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
- Текущая директория: `pwd` (выполните в папке проекта)

##### 2. Установить и включить сервис

```bash
# Скопировать service файл в директорию systemd
sudo cp telegram-gateway.service /etc/systemd/system/

# Перезагрузить systemd для распознавания нового сервиса
sudo systemctl daemon-reload

# Включить автозапуск сервиса при загрузке
sudo systemctl enable telegram-gateway.service

# Запустить сервис сейчас
sudo systemctl start telegram-gateway.service
```

##### 3. Проверить работу сервиса

```bash
# Проверить статус сервиса
sudo systemctl status telegram-gateway

# Просмотр логов в реальном времени
sudo journalctl -u telegram-gateway -f

# Просмотр последних 100 строк логов
sudo journalctl -u telegram-gateway -n 100
```

##### 4. Полезные команды systemd

```bash
# Остановить сервис
sudo systemctl stop telegram-gateway

# Перезапустить сервис
sudo systemctl restart telegram-gateway

# Отключить автозапуск при загрузке
sudo systemctl disable telegram-gateway

# Проверить, включен ли автозапуск
sudo systemctl is-enabled telegram-gateway
```

##### 5. Обновление бота

При обновлении кода:

```bash
cd /path/to/gemini_cli_gateway_tg
git pull
pip install -r requirements.txt  # Если изменились зависимости
sudo systemctl restart telegram-gateway
```

</details>

---

### Docker

Альтернативный метод развёртывания с использованием Docker.

#### 1. Сборка и запуск с Docker Compose

```bash
docker compose up -d
```

#### 2. Просмотр логов

```bash
docker compose logs -f gateway
```

#### 3. Остановка контейнера

```bash
docker compose down
```

#### 4. Обновление бота

```bash
git pull
docker compose up -d --build
```

---

## 🤖 Команды бота

### Базовые команды

- `/start` — Запустить бота и показать приветственное сообщение
- `/new` — **Сбросить контекст** (завершает текущий процесс Gemini, начинает новый)
- `/help` — Показать справочную информацию

### Управление сессиями

- `/sessions` — Просмотр и возобновление предыдущих сессий разговоров
- `/status` — Проверить статус текущей сессии Gemini CLI

### Конфигурация

- `/model` — Переключить модель Gemini (gemini-3-flash-preview, gemini-3.1-pro-preview и т.д.)
- `/settings` — Настроить режимы подтверждения, таймаут и настройки песочницы

### MCP и Skills

- `/mcp` — Управление MCP серверами (включить/выключить, просмотр статуса)
- `/skills` — Управление навыками Gemini CLI (включить/выключить, просмотр статуса)

---

## 🎯 Расширенные возможности

### Управление MCP серверами

MCP (Model Context Protocol) серверы расширяют Gemini CLI дополнительными возможностями.

**Просмотр MCP серверов:**
```
/mcp
```

**Включение/выключение серверов:**
- Нажмите inline-кнопки рядом с названием каждого сервера
- 🟢 = Включен, 🔴 = Выключен

**Использование MCP в промптах:**
```
/mcp exa search for latest AI news
```
или упомяните напрямую в любом сообщении:
```
@exa find information about quantum computing
```

**Обновление списка:**
- Нажмите кнопку "🔄 Обновить список" (ограничение: раз в 3 секунды)

---

### Управление Skills

Skills — это специализированные возможности агентов для Gemini CLI.

**Просмотр skills:**
```
/skills
```

**Включение/выключение skills:**
- Нажмите inline-кнопки рядом с названием каждого skill
- 🟢 = Включен, 🔴 = Выключен

**Использование skills в промптах:**
```
/skills docx create a professional report about AI trends
```

**Доступные skills включают:**
- `docx` — Создание и редактирование Word документов
- `pdf` — Работа с PDF файлами
- `humanizer` — Удаление паттернов AI-написания
- `chrome-devtools` — Автоматизация браузера и отладка
- И многое другое...

---

### Режимы подтверждения

Контролируйте, как Gemini CLI обрабатывает разрешения на использование инструментов.

**Доступные режимы:**

1. **default** — Запрашивать подтверждение для каждого вызова инструмента
   - Бот показывает inline-кнопки: ✅ Одобрить / ❌ Отклонить / ⏭ YOLO
   - Таймаут: 120 секунд (настраивается)

2. **auto_edit** — Авто-одобрение для инструментов редактирования, запрос для остальных
   - Сбалансированный режим для безопасной автоматизации

3. **yolo** — Авто-одобрение всех действий
   - ⚠️ Используйте с осторожностью! Подтверждение не требуется

4. **plan** — Режим только для чтения
   - CLI только планирует действия без их выполнения

**Изменение режима подтверждения:**
```
/settings → Approval Mode → Выбрать режим
```

---

### Голосовые сообщения

Отправляйте голосовые сообщения боту для транскрибации и обработки.

**Требования:**
- `GEMINI_API_KEY` должен быть установлен в `.env`

**Как это работает:**
1. Запишите и отправьте голосовое сообщение в Telegram
2. Бот транскрибирует его через Gemini API
3. Транскрибированный текст отправляется в Gemini CLI
4. Бот отвечает с ответом

---

## 💻 Разработка

### Структура проекта

```
gemini_cli_gateway_tg/
├── gateway/
│   ├── main.py              # Точка входа
│   ├── config.py            # Загрузчик конфигурации
│   ├── bot/                 # Обработчики Telegram бота
│   │   ├── handlers/        # Обработчики команд и сообщений
│   │   ├── keyboards/       # Inline клавиатуры
│   │   └── middleware/      # Аутентификация и ограничение частоты
│   ├── gemini/              # Интеграция с Gemini CLI
│   │   ├── session.py       # Управление процессами
│   │   └── parser.py        # Парсер stream-json
│   └── streaming/
│       └── editor.py        # Стриминг сообщений
├── tests/                   # Unit тесты
├── .env.example             # Пример конфигурации
├── requirements.txt         # Production зависимости
├── requirements-dev.txt     # Development зависимости
└── telegram-gateway.service # Systemd service файл
```

### Workflow разработки

**Окружение:** Windows (локальная разработка) → Ubuntu (production)

1. Внесите изменения на Windows
2. Тестируйте локально: `python -m gateway.main`
3. Коммит: `git add . && git commit -m "описание"`
4. Push: `git push`
5. На Ubuntu сервере: `git pull`
6. Перезапуск: `sudo systemctl restart telegram-gateway`
7. Проверка логов: `sudo journalctl -u telegram-gateway -f`

### Запуск тестов

```bash
# Установить dev зависимости
pip install -r requirements-dev.txt

# Запустить тесты
pytest tests/ -v

# Запустить линтер
ruff check .
ruff format --check .
```

### Качество кода

Проект использует:
- **Ruff** для линтинга и форматирования
- **pytest** для тестирования
- **GitHub Actions** для CI/CD (`.github/workflows/ci.yml`)

---

## 🔍 Решение проблем

### Бот не отвечает

**Проверьте, запущен ли сервис:**
```bash
sudo systemctl status telegram-gateway
```

**Просмотрите логи:**
```bash
sudo journalctl -u telegram-gateway -f
```

**Частые проблемы:**
- Отсутствует `TELEGRAM_BOT_TOKEN` в `.env`
- Gemini CLI не установлен: `npm install -g @google/gemini-cli`
- Версия Python < 3.12

---

### `/mcp` или `/skills` показывают пустой список

**Проверьте установку Gemini CLI:**
```bash
gemini mcp list
gemini skills list
```

**Установите MCP серверы:**
```bash
gemini mcp install exa
gemini mcp install context7
```

**Установите skills:**
```bash
gemini skills install docx
gemini skills install pdf
```

---

### Голосовые сообщения не работают

**Убедитесь, что `GEMINI_API_KEY` установлен:**
```bash
# В файле .env
GEMINI_API_KEY=your_api_key_here
```

**Получить API ключ:**
1. Перейдите на [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Создайте новый API ключ
3. Добавьте его в `.env`

---

### Ошибка "TelegramBadRequest: message is not modified"

Эта ошибка теперь обрабатывается автоматически. Если вы всё ещё видите её в логах:
- Обновитесь до последней версии: `git pull`
- Перезапустите сервис: `sudo systemctl restart telegram-gateway`

---

### Проблемы с ограничением частоты запросов

Если видите "⏳ Подожди N сек. перед следующим обновлением":
- Это нормальное поведение для предотвращения злоупотребления API
- Подождите 3 секунды между нажатиями кнопки обновления
- При необходимости настройте `REFRESH_COOLDOWN_SECONDS` в `gateway/bot/handlers/callbacks.py`

---

## 🤝 Участие в проекте

Вклады приветствуются! Пожалуйста, следуйте этим шагам:

1. Форкните репозиторий
2. Создайте ветку функции: `git checkout -b feature/amazing-feature`
3. Внесите изменения
4. Запустите тесты: `pytest tests/ -v`
5. Запустите линтер: `ruff check . && ruff format .`
6. Коммит: `git commit -m "Add amazing feature"`
7. Push: `git push origin feature/amazing-feature`
8. Откройте Pull Request

---

## 📄 Лицензия

Этот проект является открытым исходным кодом и доступен под лицензией MIT.

---

## 🙏 Благодарности

- [Gemini CLI](https://github.com/google-gemini/gemini-cli) от Google
- [aiogram](https://github.com/aiogram/aiogram) — Современный фреймворк для Telegram ботов
- Всем участникам и пользователям этого проекта

---

## 📞 Поддержка

- **Issues:** [GitHub Issues](https://github.com/Sheme1/gemini_cli_gateway_tg/issues)
- **Обсуждения:** [GitHub Discussions](https://github.com/Sheme1/gemini_cli_gateway_tg/discussions)
- **Telegram:** Свяжитесь с разработчиком бота

---

**Сделано с ❤️ для сообщества Gemini CLI**
