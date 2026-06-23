# bot.py - Хостинг бот (СТАБИЛЬНАЯ ВЕРСИЯ 24/7)
import telebot
from telebot import types
import sqlite3
import os
import uuid
import shutil
import zipfile
import subprocess
import signal
import time
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ========== НАСТРОЙКИ ==========
TOKEN = "1456462948:AAH1wfMw5sxS9p4niC3yjoxO-ndhD3xC1gY"
PORT = int(os.environ.get('PORT', 10000))
FREE_SCRIPTS = 3
FREE_SIZE_MB = 5

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
DB_PATH = BASE_DIR / "bot.db"

for d in [SCRIPTS_DIR, TEMP_DIR]:
    d.mkdir(exist_ok=True)

# ========== БД ==========
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('CREATE TABLE IF NOT EXISTS scripts (id TEXT, user_id INTEGER, name TEXT, path TEXT, status TEXT, size INTEGER)')
    conn.commit()
    conn.close()

def get_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM scripts WHERE user_id=?', (uid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_script(sid, uid, name, path, size):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?)', (sid, uid, name, str(path), 'running', size))
    conn.commit()
    conn.close()

def update_status(sid, status):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('UPDATE scripts SET status=? WHERE id=?', (status, sid))
    conn.commit()
    conn.close()

def delete_script(sid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
    conn.commit()
    conn.close()

def count_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    cnt = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]
    conn.close()
    return cnt

# ========== ЗАПУСК СКРИПТА ==========
def run_script(path):
    py_files = list(Path(path).rglob("*.py"))
    if not py_files: return None
    main = py_files[0]
    for f in py_files:
        if f.name == 'main.py': main = f; break
    try:
        proc = subprocess.Popen(['python3', str(main)], cwd=str(path), 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.pid
    except:
        try:
            proc = subprocess.Popen(['python', str(main)], cwd=str(path),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return proc.pid
        except:
            return None

def kill_process(pid):
    try: os.kill(int(pid), signal.SIGTERM); return True
    except: return False

# ========== ВЕБ-СЕРВЕР ==========
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args): pass

def start_web():
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()

# ========== БОТ ==========
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')
bot.remove_webhook()
time.sleep(3)

waiting = set()

def menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📤 Загрузить", "💻 Хосты")
    return kb

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "🚀 Хостинг бот\n📤 Загружайте .py файлы", reply_markup=menu())

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
        return bot.send_message(uid, "❌ .py или .zip!")
    
    if fs > FREE_SIZE_MB * 1024 * 1024:
        waiting.discard(uid)
        return bot.send_message(uid, f"❌ Макс {FREE_SIZE_MB}МБ!")
    
    # Скачиваем
    msg = bot.send_message(uid, "📥 Загрузка...")
    fi = bot.get_file(doc.file_id)
    dl = bot.download_file(fi.file_path)
    
    # Временная папка
    tmp = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / fn).write_bytes(dl)
    
    # Папка скрипта
    sid = uuid.uuid4().hex[:8]
    sdir = SCRIPTS_DIR / str(uid) / sid
    sdir.mkdir(parents=True, exist_ok=True)
    
    try:
        if fn.endswith('.zip'):
            with zipfile.ZipFile(tmp/fn) as z: z.extractall(sdir)
            ts = sum(f.stat().st_size for f in sdir.rglob('*') if f.is_file())
        else:
            shutil.copy2(str(tmp/fn), str(sdir/fn))
            ts = fs
        
        pid = run_script(str(sdir))
        
        if pid:
            add_script(sid, uid, fn, str(sdir), ts)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⏹ Стоп", callback_data=f"stop:{sid}"),
                   types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{sid}"))
            bot.edit_message_text(f"✅ Запущен!\n📄 {fn}\n🆔 {sid}\nPID: {pid}", uid, msg.message_id, reply_markup=kb)
        else:
            bot.edit_message_text("❌ Ошибка запуска!", uid, msg.message_id)
            shutil.rmtree(sdir, ignore_errors=True)
    except Exception as e:
        bot.edit_message_text(f"❌ {e}", uid, msg.message_id)
        shutil.rmtree(sdir, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        waiting.discard(uid)

@bot.message_handler(func=lambda m: m.text == '💻 Хосты')
def hosts(message):
    uid = message.from_user.id
    scripts = get_scripts(uid)
    
    if not scripts:
        return bot.send_message(uid, "😔 Нет хостов")
    
    text = "💻 Хосты:\n\n"
    kb = types.InlineKeyboardMarkup()
    
    for s in scripts:
        st = "🟢" if s['status']=='running' else "🔴"
        sz = (s['size'] or 0) / 1024 / 1024
        text += f"{st} {s['name']} | {sz:.1f}МБ | {s['id']}\n"
        kb.add(types.InlineKeyboardButton("⏹" if s['status']=='running' else "▶️", callback_data=f"stop:{s['id']}"),
               types.InlineKeyboardButton("🗑", callback_data=f"del:{s['id']}"))
    
    bot.send_message(uid, text, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = call.from_user.id
    data = call.data
    bot.answer_callback_query(call.id)
    
    if data.startswith("stop:"):
        sid = data.split(":")[1]
        scripts = get_scripts(uid)
        for s in scripts:
            if s['id'] == sid:
                if s['status'] == 'running':
                    update_status(sid, 'stopped')
                else:
                    pid = run_script(s['path'])
                    if pid: update_status(sid, 'running')
                break
        hosts(call.message)
    
    elif data.startswith("del:"):
        sid = data.split(":")[1]
        scripts = get_scripts(uid)
        for s in scripts:
            if s['id'] == sid:
                delete_script(sid)
                shutil.rmtree(s['path'], ignore_errors=True)
                break
        hosts(call.message)

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    init_db()
    
    # Веб-сервер в потоке
    threading.Thread(target=start_web, daemon=True).start()
    
    print(f"🚀 Бот запущен | Порт: {PORT}")
    print(f"🤖 Токен: {TOKEN[:15]}...")
    
    # Запуск с автоперезапуском
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(10)
            bot.remove_webhook()
            time.sleep(5)
