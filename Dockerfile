FROM python:3.11-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY bot.py .

# Создаём папки
RUN mkdir -p scripts logs temp

# Health check порт
EXPOSE 10000

CMD ["python", "bot.py"]
