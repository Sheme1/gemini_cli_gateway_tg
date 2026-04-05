#!/bin/bash

# Telegram Gateway для Gemini CLI - Автоматическая установка
# Этот скрипт автоматически настраивает systemd service для автозапуска бота

set -e  # Остановка при ошибке

echo "================================================"
echo "  Telegram Gateway для Gemini CLI - Установка"
echo "================================================"
echo ""

# Проверка, что скрипт запущен не от root
if [ "$EUID" -eq 0 ]; then 
   echo "❌ Не запускайте этот скрипт от root!"
   echo "   Используйте: ./install.sh"
   exit 1
fi

# Получаем текущего пользователя и директорию
CURRENT_USER=$(whoami)
CURRENT_DIR=$(pwd)
VENV_PYTHON="$CURRENT_DIR/.venv/bin/python"

echo "📋 Обнаруженные параметры:"
echo "   Пользователь: $CURRENT_USER"
echo "   Директория: $CURRENT_DIR"
echo "   Python: $VENV_PYTHON"
echo ""

# Проверка наличия .env файла
if [ ! -f "$CURRENT_DIR/.env" ]; then
    echo "❌ Файл .env не найден!"
    echo "   Создайте .env файл перед установкой:"
    echo "   cp .env.example .env"
    echo "   nano .env"
    exit 1
fi

# Проверка наличия виртуального окружения
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Виртуальное окружение не найдено!"
    echo "   Создайте venv перед установкой:"
    echo "   python3 -m venv .venv"
    echo "   source .venv/bin/activate"
    echo "   pip install -r requirements.txt"
    exit 1
fi

# Проверка наличия Gemini CLI
if ! command -v gemini &> /dev/null; then
    echo "⚠️  Gemini CLI не найден!"
    echo "   Установите его командой:"
    echo "   npm install -g @google/gemini-cli"
    read -p "   Продолжить установку без Gemini CLI? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "🔧 Создание systemd service файла..."

# Создаём временный service файл с правильными путями
cat > telegram-gateway.service.tmp << EOF
[Unit]
Description=Telegram Gateway for Gemini CLI
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$CURRENT_DIR
EnvironmentFile=$CURRENT_DIR/.env
ExecStart=$VENV_PYTHON -m gateway.main
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "✅ Service файл создан"
echo ""

# Показываем содержимое для проверки
echo "📄 Содержимое service файла:"
echo "----------------------------------------"
cat telegram-gateway.service.tmp
echo "----------------------------------------"
echo ""

read -p "❓ Продолжить установку? (Y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Nn]$ ]]; then
    rm telegram-gateway.service.tmp
    echo "❌ Установка отменена"
    exit 0
fi

echo ""
echo "🚀 Установка systemd service..."

# Копируем service файл (требуется sudo)
sudo cp telegram-gateway.service.tmp /etc/systemd/system/telegram-gateway.service
rm telegram-gateway.service.tmp

# Перезагружаем systemd
sudo systemctl daemon-reload

# Включаем автозапуск
sudo systemctl enable telegram-gateway.service

# Запускаем сервис
sudo systemctl start telegram-gateway.service

echo ""
echo "✅ Установка завершена!"
echo ""
echo "📊 Статус сервиса:"
sudo systemctl status telegram-gateway.service --no-pager -l
echo ""
echo "================================================"
echo "  Полезные команды:"
echo "================================================"
echo ""
echo "  Просмотр логов:"
echo "    sudo journalctl -u telegram-gateway -f"
echo ""
echo "  Перезапуск бота:"
echo "    sudo systemctl restart telegram-gateway"
echo ""
echo "  Остановка бота:"
echo "    sudo systemctl stop telegram-gateway"
echo ""
echo "  Отключение автозапуска:"
echo "    sudo systemctl disable telegram-gateway"
echo ""
echo "  Проверка статуса:"
echo "    sudo systemctl status telegram-gateway"
echo ""
echo "================================================"
echo "  Бот успешно установлен и запущен! 🎉"
echo "================================================"
