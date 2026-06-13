# keep_alive.py - Пинг сервера для предотвращения засыпания на Render
import requests
import time
import os

# URL вашего сервиса на Render
RENDER_URL = os.environ.get("RENDER_URL", "https://ваш-сервис.onrender.com/health")

# Интервал пинга в секундах (10 минут)
PING_INTERVAL = 600

def ping_server():
    while True:
        try:
            response = requests.get(RENDER_URL, timeout=30)
            print(f"✅ Пинг: {response.status_code} - {time.strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"❌ Ошибка пинга: {e} - {time.strftime('%H:%M:%S')}")
        
        time.sleep(PING_INTERVAL)

if __name__ == "__main__":
    print(f"🔄 Запущен пинг каждые {PING_INTERVAL // 60} минут")
    print(f"📍 URL: {RENDER_URL}")
    ping_server()
