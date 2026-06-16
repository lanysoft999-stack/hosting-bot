# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# Установка зависимостей БЕЗ кеша
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY bot.py .

# Создаём папки
RUN mkdir -p scripts logs temp

# Порт для health check
EXPOSE 10000

# Запуск
CMD ["python", "bot.py"]
