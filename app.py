# ============================================================
#  Ohoster Hosting Bot (Render Stable Version)
#  Работает на telebot + веб-сервер для выживания на Render
# ============================================================

import telebot
from telebot import types
import sqlite3
import os
import sys
import uuid
import shutil
import zipfile
import subprocess
import signal
import time
import requests
import threading
import json
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==========================================================
#  1. НАСТРОЙКИ (Здесь вставь свой НОВЫЙ токен)
# ==========================================================
TOKEN = "8810746051:AAGOX0WDFdA6ZyJYUghxw4efnEsw8hpAE4c"
ADMIN_IDS = [314148464]

FREE_SCRIPTS = 5
FREE_SIZE_MB = 10

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
DB_PATH = BASE_DIR / "bot.db"

for d in [SCRIPTS_DIR, TEMP_DIR]:
    d.mkdir(exist_ok=True)

# ==========================================================
#  2. БАЗА ДАННЫХ (SQLite)
# ==========================================================
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            path TEXT,
            status TEXT,
            size INTEGER,
            pid INTEGER,
            created_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY rowid DESC', (uid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def count_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    cnt = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]
    conn.close()
    return cnt

def add_script(sid, uid, name, path, size, pid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?,?,?)', (sid, uid, name, path, 'running', size, pid, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_script_status(sid, status, pid=None):
    conn = sqlite3.connect(str(DB_PATH))
    if pid is None:
        conn.execute('UPDATE scripts SET status=?, pid=NULL WHERE id=?', (status, sid))
    else:
        conn.execute('UPDATE scripts SET status=?, pid=? WHERE id=?', (status, pid, sid))
    conn.commit()
    conn.close()

def delete_script(sid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
    conn.commit()
    conn.close()

init_db()

# ==========================================================
#  3. ВЕБ-СЕРВЕР ДЛЯ RENDER (ОБЯЗАТЕЛЬНО!)
# ==========================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args):
        pass

def start_web():
    port = int(os.environ.get('PORT', 10000))
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()

threading.Thread(target=start_web, daemon=True).start()

# ==========================================================
#  4. ЗАПУСК СКРИПТОВ
# ==========================================================
def run_script(path):
    py_files = list(Path(path).rglob("*.py"))
    if not py_files:
        return None
    main = py_files[0]
    for f in py_files:
        if f.name == 'main.py':
            main = f
            break
    try:
        proc = subprocess.Popen(
            [sys.executable, str(main)],
            cwd=str(path),
            start_new_session=True,
            preexec_fn=os.setpgrp
        )
        return proc.pid
    except Exception as e:
        print(f"Error: {e}")
        return None

# ==========================================================
#  5. TELEGRAM БОТ
# ==========================================================
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')
waiting = set()

# ==========================================================
#  6. КЛАВИАТУРЫ
# ==========================================================
def user_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📤 Загрузить", "💻 Мои хосты")
    kb.add("👤 Профиль", "🆘 Помощь")
    return kb

# ==========================================================
#  7. ОБРАБОТЧИКИ
# ==========================================================
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT OR IGNORE INTO users VALUES (?,?)', (uid, message.from_user.username))
    conn.commit()
    conn.close()

    scripts = get_scripts(uid)
    running = sum(1 for s in scripts if s['status'] == 'running')
    
    text = f"༆ <b>Ohoster Render Bot</b>\n\n📦 Запущено: {running} скриптов\n🚀 Хостинг: Render"
    bot.send_message(uid, text, reply_markup=user_kb())

@bot.message_handler(func=lambda m: m.text == '📤 Загрузить')
def upload(message):
    uid = message.from_user.id
    if count_scripts(uid) >= FREE_SCRIPTS:
        return bot.send_message(uid, f"❌ Лимит {FREE_SCRIPTS} скриптов!")
    waiting.add(uid)
    bot.send_message(uid, f"📤 Отправьте .py или .zip (до {FREE_SIZE_MB}МБ)")

@bot.message_handler(content_types=['document'])
def handle_doc(message):
    uid = message.from_user.id
    if uid not in waiting: return
    doc = message.document
    fn = doc.file_name
    fs = doc.file_size

    if not fn.endswith(('.py', '.zip')):
        waiting.discard(uid)
        return bot.send_message(uid, "❌ Только .py или .zip!")
    if fs > FREE_SIZE_MB * 1024 * 1024:
        waiting.discard(uid)
        return bot.send_message(uid, f"❌ Макс {FREE_SIZE_MB}МБ!")

    msg = bot.send_message(uid, "📥 Загрузка...")
    try:
        file_info = bot.get_file(doc.file_id)
        url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        content = requests.get(url, timeout=30).content

        tmp_dir = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]
        tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / fn).write_bytes(content)

        sid = uuid.uuid4().hex[:8]
        target_dir = SCRIPTS_DIR / str(uid) / sid
        target_dir.mkdir(parents=True, exist_ok=True)

        if fn.endswith('.zip'):
            with zipfile.ZipFile(tmp_dir / fn) as z:
                z.extractall(target_dir)
            total_size = sum(f.stat().st_size for f in target_dir.rglob('*') if f.is_file())
        else:
            shutil.copy2(str(tmp_dir / fn), str(target_dir / fn))
            total_size = fs

        bot.edit_message_text("⚡ Запуск...", uid, msg.message_id)
        pid = run_script(str(target_dir))

        if pid:
            add_script(sid, uid, fn, str(target_dir), total_size, pid)
            kb = InlineKeyboardMarkup()
            kb.add(
                InlineKeyboardButton("⏹ Стоп", callback_data=f"stop:{sid}"),
                InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{sid}")
            )
            bot.edit_message_text(f"✅ <b>Запущен!</b>\n📄 {fn}\n🆔 {sid}\nPID: {pid}", uid, msg.message_id, reply_markup=kb)
        else:
            bot.edit_message_text("❌ Ошибка запуска!", uid, msg.message_id)
            shutil.rmtree(target_dir, ignore_errors=True)
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", uid, msg.message_id)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        waiting.discard(uid)

@bot.message_handler(func=lambda m: m.text == '💻 Мои хосты')
def hosts(message):
    uid = message.from_user.id
    scripts = get_scripts(uid)
    if not scripts:
        return bot.send_message(uid, "😔 <b>Нет сервисов</b>")
    running = sum(1 for s in scripts if s['status'] == 'running')
    text = f"💻 <b>МОИ СЕРВИСЫ</b>\n\n🟢 {running} | 🔴 {len(scripts) - running}\n\n"
    kb = InlineKeyboardMarkup()
    for i, s in enumerate(scripts, 1):
        st = "🟢" if s['status'] == 'running' else "🔴"
        sz = (s['size'] or 0) / 1024 / 1024
        text += f"{st} <b>{s['name']}</b> | {sz:.1f}МБ\n"
        kb.add(
            InlineKeyboardButton(f"⏹ {i}" if s['status'] == 'running' else f"▶️ {i}", callback_data=f"stop:{s['id']}"),
            InlineKeyboardButton(f"🗑 {i}", callback_data=f"del:{s['id']}")
        )
    bot.send_message(uid, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == '👤 Профиль')
def profile(message):
    uid = message.from_user.id
    scripts = get_scripts(uid)
    running = sum(1 for s in scripts if s['status'] == 'running')
    text = f"👤 <b>ПРОФИЛЬ</b>\n\n🆔 <code>{uid}</code>\n📦 {len(scripts)}/{FREE_SCRIPTS}\n🟢 {running}"
    bot.send_message(uid, text)

@bot.message_handler(func=lambda m: m.text == '🆘 Помощь')
def help_cmd(message):
    text = f"🆘 <b>ПОМОЩЬ</b>\n\n📤 Загрузить - .py или .zip\n💻 Мои хосты - управление\n\n📦 Лимит: {FREE_SCRIPTS} скриптов\n📊 Размер: до {FREE_SIZE_MB}МБ"
    bot.send_message(message.chat.id, text)

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = call.from_user.id
    data = call.data
    bot.answer_callback_query(call.id)

    if data.startswith("stop:"):
        sid = data.split(":")[1]
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        script = conn.execute('SELECT * FROM scripts WHERE id=? AND user_id=?', (sid, uid)).fetchone()
        if script:
            if script['status'] == 'running':
                pid = script.get('pid')
                if pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except:
                        pass
                update_script_status(sid, 'stopped')
            else:
                new_pid = run_script(script['path'])
                if new_pid:
                    update_script_status(sid, 'running', new_pid)
                else:
                    update_script_status(sid, 'stopped')
        conn.close()
        hosts(call.message)

    if data.startswith("del:"):
        sid = data.split(":")[1]
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        script = conn.execute('SELECT * FROM scripts WHERE id=? AND user_id=?', (sid, uid)).fetchone()
        if script:
            pid = script.get('pid')
            if pid:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except:
                    pass
            delete_script(sid)
            shutil.rmtree(script['path'], ignore_errors=True)
        conn.close()
        hosts(call.message)

# ==========================================================
#  8. ЗАПУСК
# ==========================================================
if __name__ == '__main__':
    print("🚀 Ohoster Render запущен...")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(5)
            bot.remove_webhook()
            time.sleep(5)
