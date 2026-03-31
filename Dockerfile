# Dockerfile
# Вспомогательный способ деплоя. Основной - systemd.
FROM python:3.12-slim

# Установка Node.js (для gemini-cli) и зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    curl \
    && npm install -g @anthropic-ai/gemini-cli \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Определение переменной окружения PYTHONPATH
ENV PYTHONPATH=/app

CMD ["python", "-m", "gateway.main"]
