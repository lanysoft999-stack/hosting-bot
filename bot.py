# bot.py - Ohoster Hosting Bot (ПОЛНАЯ ВЕРСИЯ)
import telebot
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
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
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import random

# ========== НАСТРОЙКИ ==========
TOKEN = "1456462948:AAH1wfMw5sxS9p4niC3yjoxO-ndhD3xC1gY"
ADMIN_IDS = [314148464]
PORT = int(os.environ.get('PORT', 10000))
FREE_SCRIPTS = 3
FREE_SIZE_MB = 5

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
FILES_DIR = BASE_DIR / "user_files"
DB_PATH = BASE_DIR / "bot.db"

for d in [SCRIPTS_DIR, TEMP_DIR, FILES_DIR]:
    d.mkdir(exist_ok=True)

# ========== БД ==========
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('''CREATE TABLE IF NOT EXISTS scripts 
                   (id TEXT, user_id INTEGER, name TEXT, path TEXT, status TEXT, size INTEGER, created_at TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS users 
                   (user_id INTEGER PRIMARY KEY, username TEXT, joined_at TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS banned 
                   (user_id INTEGER PRIMARY KEY)''')
    conn.commit()
    conn.close()

def get_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY created_at DESC', (uid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_scripts():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM scripts ORDER BY created_at DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_script(sid, uid, name, path, size):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?,?)', 
                (sid, uid, name, str(path), 'running', size, datetime.now().isoformat()))
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

def delete_user_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('DELETE FROM scripts WHERE user_id=?', (uid,))
    conn.commit()
    conn.close()

def count_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    cnt = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]
    conn.close()
    return cnt

def get_user(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None

def add_user(uid, username):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT OR IGNORE INTO users VALUES (?,?,?)', (uid, username, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM users ORDER BY joined_at DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]

def is_banned(uid):
    conn = sqlite3.connect(str(DB_PATH))
    banned = conn.execute('SELECT * FROM banned WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    return banned is not None

def ban_user_db(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT OR REPLACE INTO banned VALUES (?)', (uid,))
    conn.commit()
    conn.close()

def unban_user_db(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('DELETE FROM banned WHERE user_id=?', (uid,))
    conn.commit()
    conn.close()

# ========== ЗАПУСК СКРИПТА ==========
def run_script(path):
    py_files = list(Path(path).rglob("*.py"))
    if not py_files: return None
    main = py_files[0]
    for f in py_files:
        if f.name == 'main.py': main = f; break
    try:
        proc = subprocess.Popen([sys.executable, str(main)], cwd=str(path),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        print(f"[PID:{proc.pid}] Started: {main}")
        return proc.pid
    except:
        return None

def kill_process(pid):
    try: os.kill(int(pid), signal.SIGTERM); return True
    except: return False

# ========== ВЕБ-СЕРВЕР ==========
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'OK')
    def log_message(self, *args): pass

def start_web():
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()

# ========== БОТ ==========
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')
bot.remove_webhook()
time.sleep(3)

bot_active = True
waiting = set()
broadcast_waiting = set()
admin_action = {}

# Приветственные сообщения
WELCOME_TEXT = [
    "🎉 Добро пожаловать в Ohoster!\n📤 Загружайте Python скрипты и они будут работать 24/7!",
    "🚀 Привет в Ohoster!\n💻 Хостинг для твоих ботов и скриптов!",
    "💎 Ohoster - твой надежный хостинг!\n⚡ Загрузи .py файл и начни работу!"
]

def user_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📤 Загрузить", "💻 Мои хосты")
    kb.add("👤 Профиль", "🆘 Помощь")
    return kb

def admin_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("👥 Пользователи", "📊 Статистика")
    kb.add("📨 Рассылка", "📦 Все хосты")
    kb.add("📥 Файлы юзера", "🗑 Удалить хосты")
    if bot_active:
        kb.add("🚫 Забанить", "🛑 Стоп бот")
    else:
        kb.add("🟢 Разбанить", "🟢 Старт бот")
    kb.add("👤 Режим юзера")
    return kb

@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    
    if is_banned(uid) and uid not in ADMIN_IDS:
        return bot.send_message(uid, "🚫 ВЫ ЗАБАНЕНЫ!")
    
    add_user(uid, message.from_user.username)
    
    if uid in ADMIN_IDS:
        scripts = get_all_scripts()
        running = len([s for s in scripts if s['status']=='running'])
        users = get_all_users()
        text = (
            f"👑 <b>АДМИН-ПАНЕЛЬ Ohoster</b>\n\n"
            f"👥 Пользователей: {len(users)}\n"
            f"📦 Всего хостов: {len(scripts)}\n"
            f"🟢 Запущено: {running}\n"
            f"🔴 Упало: {len(scripts)-running}"
        )
        bot.send_message(uid, text, reply_markup=admin_kb())
        return
    
    scripts = get_scripts(uid)
    running = len([s for s in scripts if s['status']=='running'])
    stopped = len(scripts) - running
    uptime = 100 if len(scripts) == 0 else round((running/len(scripts))*100) if len(scripts) > 0 else 100
    
    text = (
        f"🚀 <b>Добро пожаловать в Ohoster!</b>\n\n"
        f"✅ Аптайм за 24 часа: {uptime}%\n"
        f"⏹ Упало сервисов: {stopped}\n"
        f"🟢 Сервисов запущено: {running}\n\n"
        f"👇 <b>Выберите действие:</b>"
    )
    
    bot.send_message(uid, text, reply_markup=user_kb())

@bot.message_handler(func=lambda m: m.text == '📤 Загрузить')
def upload(message):
    uid = message.from_user.id
    if not bot_active and uid not in ADMIN_IDS:
        return bot.send_message(uid, "🔴 Бот на обслуживании!")
    if is_banned(uid):
        return bot.send_message(uid, "🚫 Вы забанены!")
    if count_scripts(uid) >= FREE_SCRIPTS:
        return bot.send_message(uid, f"❌ Лимит {FREE_SCRIPTS} скриптов!")
    waiting.add(uid)
    bot.send_message(uid, f"📤 Отправьте .py или .zip (до {FREE_SIZE_MB}МБ)")

@bot.message_handler(content_types=['document'])
def handle_doc(message):
    uid = message.from_user.id
    if not bot_active and uid not in ADMIN_IDS:
        return bot.send_message(uid, "🔴 Бот на обслуживании!")
    if uid not in waiting: return
    
    doc = message.document; fn = doc.file_name; fs = doc.file_size
    
    if not fn.endswith(('.py', '.zip')):
        waiting.discard(uid); return bot.send_message(uid, "❌ .py или .zip!")
    if fs > FREE_SIZE_MB*1024*1024:
        waiting.discard(uid); return bot.send_message(uid, f"❌ Макс {FREE_SIZE_MB}МБ!")
    
    msg = bot.send_message(uid, "📥 Загрузка...")
    
    try:
        fi = bot.get_file(doc.file_id)
        url = f"https://api.telegram.org/file/bot{TOKEN}/{fi.file_path}"
        dl = requests.get(url).content
        
        tmp = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp/fn).write_bytes(dl)
        
        sid = uuid.uuid4().hex[:8]
        sdir = SCRIPTS_DIR / str(uid) / sid
        sdir.mkdir(parents=True, exist_ok=True)
        
        # Сохраняем оригинал
        user_dir = FILES_DIR / str(uid)
        user_dir.mkdir(exist_ok=True)
        (user_dir / fn).write_bytes(dl)
        
        if fn.endswith('.zip'):
            with zipfile.ZipFile(tmp/fn) as z: z.extractall(sdir)
            ts = sum(f.stat().st_size for f in sdir.rglob('*') if f.is_file())
        else:
            shutil.copy2(str(tmp/fn), str(sdir/fn)); ts = fs
        
        bot.edit_message_text("⚡ Запуск...", uid, msg.message_id)
        pid = run_script(str(sdir))
        
        if pid:
            add_script(sid, uid, fn, str(sdir), ts)
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("⏹ Стоп", callback_data=f"stop:{sid}"),
                   InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{sid}"))
            bot.edit_message_text(f"✅ Запущен!\n📄 {fn}\n🆔 {sid}\nPID: {pid}", uid, msg.message_id, reply_markup=kb)
        else:
            bot.edit_message_text("❌ Ошибка!", uid, msg.message_id)
            shutil.rmtree(sdir, ignore_errors=True)
    except Exception as e:
        bot.edit_message_text(f"❌ {e}", uid, msg.message_id)
    finally:
        if 'tmp' in locals(): shutil.rmtree(tmp, ignore_errors=True)
        waiting.discard(uid)

@bot.message_handler(func=lambda m: m.text == '💻 Мои хосты')
def hosts(message):
    uid = message.from_user.id
    scripts = get_scripts(uid)
    
    if not scripts:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📤 Загрузить скрипт", callback_data="upload_btn"))
        return bot.send_message(uid, "😔 Нет активных сервисов\n\n📤 Загрузите ваш первый скрипт!", reply_markup=kb)
    
    running = len([s for s in scripts if s['status']=='running'])
    stopped = len(scripts) - running
    uptime = round((running/len(scripts))*100) if len(scripts) > 0 else 0
    
    text = (
        f"💻 <b>МОИ СЕРВИСЫ</b>\n\n"
        f"📊 Статистика:\n"
        f"├ 🟢 Запущено: {running}\n"
        f"├ 🔴 Упало: {stopped}\n"
        f"└ 📈 Аптайм: {uptime}%\n\n"
        f"📦 <b>Список хостов:</b>\n"
    )
    
    kb = InlineKeyboardMarkup()
    for i, s in enumerate(scripts, 1):
        st = "🟢" if s['status']=='running' else "🔴"
        sz = (s['size'] or 0)/1024/1024
        text += f"{i}. {st} <b>{s['name']}</b>\n   └ {sz:.1f}МБ | <code>{s['id']}</code>\n"
        kb.add(
            InlineKeyboardButton(f"⏹ Стоп {i}" if s['status']=='running' else f"▶️ Старт {i}", callback_data=f"stop:{s['id']}"),
            InlineKeyboardButton(f"🗑 Удалить {i}", callback_data=f"del:{s['id']}")
        )
    
    kb.add(InlineKeyboardButton("📤 Загрузить ещё", callback_data="upload_btn"))
    bot.send_message(uid, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == '👤 Профиль')
def profile(message):
    uid = message.from_user.id
    u = get_user(uid)
    scripts = count_scripts(uid)
    running = len([s for s in get_scripts(uid) if s['status']=='running'])
    text = (
        f"👤 <b>ПРОФИЛЬ</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"👤 @{u.get('username','?') if u else '?'}\n"
        f"📦 Хостов: {scripts}/{FREE_SCRIPTS}\n"
        f"🟢 Запущено: {running}"
    )
    bot.send_message(uid, text)

@bot.message_handler(func=lambda m: m.text == '🆘 Помощь')
def help_cmd(message):
    text = (
        f"🆘 <b>ПОМОЩЬ Ohoster</b>\n\n"
        f"📤 <b>Загрузить</b> - отправьте .py или .zip файл\n"
        f"💻 <b>Мои хосты</b> - управление сервисами\n"
        f"👤 <b>Профиль</b> - ваша статистика\n\n"
        f"📦 Лимит: {FREE_SCRIPTS} скриптов\n"
        f"📊 Размер файла: до {FREE_SIZE_MB}МБ\n\n"
        f"📞 Поддержка: @hesers"
    )
    bot.send_message(message.chat.id, text)

# ========== АДМИН КОМАНДЫ ==========
@bot.message_handler(func=lambda m: m.text == '👥 Пользователи' and m.from_user.id in ADMIN_IDS)
def admin_users(message):
    users = get_all_users()
    if not users: return bot.send_message(message.chat.id, "Нет пользователей")
    text = f"👥 <b>ПОЛЬЗОВАТЕЛИ ({len(users)})</b>\n\n"
    for u in users[:20]:
        scripts = count_scripts(u['user_id'])
        text += f"🆔 <code>{u['user_id']}</code> | @{u.get('username','?')} | 📦{scripts}\n"
    if len(users) > 20:
        text += f"\n... и ещё {len(users)-20}"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == '📊 Статистика' and m.from_user.id in ADMIN_IDS)
def stats(message):
    users = get_all_users()
    scripts = get_all_scripts()
    running = len([s for s in scripts if s['status']=='running'])
    total_size = sum(s['size'] or 0 for s in scripts)
    text = (
        f"📊 <b>СТАТИСТИКА Ohoster</b>\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"📦 Всего хостов: {len(scripts)}\n"
        f"🟢 Запущено: {running}\n"
        f"🔴 Упало: {len(scripts)-running}\n"
        f"💾 Общий размер: {total_size/1024/1024:.1f}МБ"
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == '📨 Рассылка' and m.from_user.id in ADMIN_IDS)
def broadcast(message):
    broadcast_waiting.add(message.from_user.id)
    bot.send_message(message.chat.id, "📨 Отправьте сообщение для рассылки (текст/фото):")

@bot.message_handler(func=lambda m: m.text == '📦 Все хосты' and m.from_user.id in ADMIN_IDS)
def all_hosts(message):
    scripts = get_all_scripts()
    if not scripts: return bot.send_message(message.chat.id, "Нет хостов")
    text = f"📦 <b>ВСЕ ХОСТЫ ({len(scripts)})</b>\n\n"
    for s in scripts[:15]:
        st = "🟢" if s['status']=='running' else "🔴"
        sz = (s['size'] or 0)/1024/1024
        text += f"{st} <b>{s['name']}</b> | {sz:.1f}МБ | user{s['user_id']}\n<code>{s['id']}</code>\n\n"
    if len(scripts) > 15:
        text += f"... и ещё {len(scripts)-15}"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == '📥 Файлы юзера' and m.from_user.id in ADMIN_IDS)
def get_user_files(message):
    admin_action[message.from_user.id] = 'get_files'
    bot.send_message(message.chat.id, "🆔 Введите ID пользователя:")

@bot.message_handler(func=lambda m: m.text == '🗑 Удалить хосты' and m.from_user.id in ADMIN_IDS)
def del_user_hosts(message):
    admin_action[message.from_user.id] = 'del_hosts'
    bot.send_message(message.chat.id, "🆔 Введите ID пользователя:")

@bot.message_handler(func=lambda m: m.text in ['🚫 Забанить', '🟢 Разбанить'] and m.from_user.id in ADMIN_IDS)
def ban_unban_start(message):
    if 'Забанить' in message.text:
        admin_action[message.from_user.id] = 'ban_user'
        bot.send_message(message.chat.id, "🆔 Введите ID для бана:")
    else:
        admin_action[message.from_user.id] = 'unban_user'
        bot.send_message(message.chat.id, "🆔 Введите ID для разбана:")

@bot.message_handler(func=lambda m: m.text in ['🛑 Стоп бот', '🟢 Старт бот'] and m.from_user.id in ADMIN_IDS)
def toggle_bot(message):
    global bot_active
    if bot_active:
        bot_active = False
        stopped = 0
        for s in get_all_scripts():
            if s['status'] == 'running':
                update_status(s['id'], 'stopped')
                stopped += 1
        bot.send_message(message.chat.id, f"🔴 Бот остановлен!\n⏹ Остановлено: {stopped}", reply_markup=admin_kb())
    else:
        bot_active = True
        started = 0
        for s in get_all_scripts():
            if s['status'] == 'stopped':
                pid = run_script(s['path'])
                if pid:
                    update_status(s['id'], 'running')
                    started += 1
        bot.send_message(message.chat.id, f"🟢 Бот запущен!\n▶️ Запущено: {started}", reply_markup=admin_kb())

@bot.message_handler(func=lambda m: m.text == '👤 Режим юзера' and m.from_user.id in ADMIN_IDS)
def user_mode(message):
    scripts = get_scripts(message.from_user.id)
    running = len([s for s in scripts if s['status']=='running'])
    text = f"👤 Режим пользователя\n🟢 Запущено: {running}"
    bot.send_message(message.chat.id, text, reply_markup=user_kb())

# ========== ТЕКСТ ==========
@bot.message_handler(content_types=['text'])
def handle_text(message):
    uid = message.from_user.id
    text = message.text.strip() if message.text else ""
    
    if uid in broadcast_waiting:
        broadcast_waiting.discard(uid)
        users = get_all_users()
        sent = 0
        for u in users:
            try: bot.send_message(u['user_id'], f"📢 <b>Рассылка Ohoster</b>\n\n{text}"); sent += 1
            except: pass; time.sleep(0.03)
        bot.send_message(uid, f"✅ Отправлено: {sent}/{len(users)}")
        return
    
    if uid in admin_action:
        action = admin_action.pop(uid)
        try: target = int(text)
        except: return bot.send_message(uid, "❌ Неверный ID")
        
        if action == 'get_files':
            user_dir = FILES_DIR / str(target)
            if user_dir.exists() and list(user_dir.iterdir()):
                count = 0
                for f in user_dir.iterdir():
                    if f.is_file():
                        with open(f, 'rb') as file:
                            bot.send_document(uid, file, caption=f"📄 {f.name}")
                        count += 1
                bot.send_message(uid, f"✅ Отправлено файлов: {count}")
            else:
                bot.send_message(uid, "❌ Нет файлов")
        
        elif action == 'del_hosts':
            scripts = get_scripts(target)
            count = len(scripts)
            for s in scripts:
                if s.get('status') == 'running':
                    try: kill_process(s.get('pid', 0))
                    except: pass
                shutil.rmtree(s['path'], ignore_errors=True)
            delete_user_scripts(target)
            bot.send_message(uid, f"✅ Удалено хостов user{target}: {count}")
        
        elif action == 'ban_user':
            scripts = get_scripts(target)
            for s in scripts:
                if s.get('status') == 'running':
                    try: kill_process(s.get('pid', 0))
                    except: pass
                shutil.rmtree(s['path'], ignore_errors=True)
            delete_user_scripts(target)
            ban_user_db(target)
            bot.send_message(uid, f"🚫 user{target} забанен!")
            try: bot.send_message(target, "🚫 Вы забанены в Ohoster!")
            except: pass
        
        elif action == 'unban_user':
            unban_user_db(target)
            bot.send_message(uid, f"🟢 user{target} разбанен!")
            try: bot.send_message(target, "🟢 Вы разбанены в Ohoster!")
            except: pass
        return

# ========== ФОТО ==========
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    if uid in broadcast_waiting:
        broadcast_waiting.discard(uid)
        users = get_all_users()
        sent = 0
        for u in users:
            try: bot.send_photo(u['user_id'], message.photo[-1].file_id, caption="📢 Рассылка Ohoster"); sent += 1
            except: pass; time.sleep(0.03)
        bot.send_message(uid, f"✅ Отправлено: {sent}/{len(users)}")
        return

# ========== CALLBACKS ==========
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = call.from_user.id; data = call.data
    bot.answer_callback_query(call.id)
    
    if data == "upload_btn":
        upload(call.message)
        return
    
    if data.startswith("stop:"):
        sid = data.split(":")[1]
        for s in get_scripts(uid):
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
        for s in get_scripts(uid):
            if s['id'] == sid:
                if s.get('status') == 'running':
                    try: kill_process(s.get('pid', 0))
                    except: pass
                delete_script(sid)
                shutil.rmtree(s['path'], ignore_errors=True)
                break
        hosts(call.message)

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    init_db()
    threading.Thread(target=start_web, daemon=True).start()
    
    print(f"""
╔══════════════════════════════════════════╗
║     🚀 Ohoster Bot                       ║
║     Порт: {PORT}                           ║
║     Статус: Запущен                      ║
╚══════════════════════════════════════════╝
    """)
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(10)
            bot.remove_webhook()
            time.sleep(5)
