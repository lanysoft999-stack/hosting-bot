# bot.py - Ohoster Hosting Bot (ФИНАЛЬНАЯ ВЕРСИЯ)
import telebot
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import sqlite3, os, sys, uuid, shutil, zipfile, subprocess, signal, time, requests, threading
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

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
    conn.execute('CREATE TABLE IF NOT EXISTS scripts (id TEXT, user_id INTEGER, name TEXT, path TEXT, status TEXT, size INTEGER)')
    conn.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY)')
    conn.commit()
    conn.close()

def get_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY rowid DESC', (uid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_scripts():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM scripts ORDER BY rowid DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]

def count_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    cnt = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]
    conn.close()
    return cnt

def is_banned(uid):
    conn = sqlite3.connect(str(DB_PATH))
    banned = conn.execute('SELECT * FROM banned WHERE user_id=?', (uid,)).fetchone()
    conn.close()
    return banned is not None

def get_all_users():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM users ORDER BY rowid DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ========== ЗАПУСК СКРИПТА ==========
def run_script(path):
    for f in Path(path).rglob("*.py"):
        if f.name in ('main.py', 'bot.py'):
            try:
                proc = subprocess.Popen([sys.executable, str(f)], cwd=str(path), 
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return proc.pid
            except: pass
    py_files = list(Path(path).rglob("*.py"))
    if py_files:
        try:
            proc = subprocess.Popen([sys.executable, str(py_files[0])], cwd=str(path),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return proc.pid
        except: pass
    return None

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
broadcast_set = set()
admin_act = {}

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
    kb.add("🚫 Забанить", "🟢 Разбанить")
    kb.add("🛑 Стоп бот" if bot_active else "🟢 Старт бот", "👤 Юзер")
    return kb

# ========== СТАРТ ==========
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    
    if is_banned(uid) and uid not in ADMIN_IDS:
        return bot.send_message(uid, "🚫 ВЫ ЗАБАНЕНЫ!")
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT OR IGNORE INTO users VALUES (?,?)', (uid, message.from_user.username))
    conn.commit()
    conn.close()
    
    if uid in ADMIN_IDS:
        scripts = get_all_scripts()
        running = sum(1 for s in scripts if s['status']=='running')
        users = get_all_users()
        text = f"👑 <b>АДМИН Ohoster</b>\n\n👥 {len(users)} | 📦 {len(scripts)} | 🟢 {running}"
        bot.send_message(uid, text, reply_markup=admin_kb())
        return
    
    scripts = get_scripts(uid)
    running = sum(1 for s in scripts if s['status']=='running')
    stopped = len(scripts) - running
    uptime = 100 if len(scripts) == 0 else round((running/len(scripts))*100)
    
    text = (
        f"༆ <b>Добро пожаловать в Ohoster!</b>\n\n"
        f"⚠︎ Аптайм за 24 часа: {uptime}%\n"
        f"➪ Упало сервисов: {stopped}\n"
        f"➪ Сервисов запущено: {running}"
    )
    
    bot.send_message(uid, text, reply_markup=user_kb())

# ========== ЗАГРУЗКА ==========
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
        
        tmp = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]; tmp.mkdir(parents=True, exist_ok=True)
        (tmp/fn).write_bytes(dl)
        
        sid = uuid.uuid4().hex[:8]
        sdir = SCRIPTS_DIR / str(uid) / sid; sdir.mkdir(parents=True, exist_ok=True)
        
        # Сохраняем в файлы пользователя
        user_dir = FILES_DIR / str(uid); user_dir.mkdir(exist_ok=True)
        (user_dir / fn).write_bytes(dl)
        
        if fn.endswith('.zip'):
            with zipfile.ZipFile(tmp/fn) as z: z.extractall(sdir)
            ts = sum(f.stat().st_size for f in sdir.rglob('*') if f.is_file())
        else:
            shutil.copy2(str(tmp/fn), str(sdir/fn)); ts = fs
        
        bot.edit_message_text("⚡ Запуск...", uid, msg.message_id)
        pid = run_script(str(sdir))
        
        if pid:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?)', (sid, uid, fn, str(sdir), 'running', ts))
            conn.commit(); conn.close()
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("⏹ Стоп", callback_data=f"stop:{sid}"),
                   InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{sid}"))
            bot.edit_message_text(f"✅ <b>Запущен!</b>\n📄 {fn}\n🆔 {sid}\nPID: {pid}", uid, msg.message_id, reply_markup=kb)
        else:
            bot.edit_message_text("❌ Ошибка запуска!", uid, msg.message_id)
            shutil.rmtree(sdir, ignore_errors=True)
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", uid, msg.message_id)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        waiting.discard(uid)

# ========== ХОСТЫ ==========
@bot.message_handler(func=lambda m: m.text == '💻 Мои хосты')
def hosts(message):
    uid = message.from_user.id; scripts = get_scripts(uid)
    
    if not scripts:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📤 Загрузить скрипт", callback_data="upload_btn"))
        return bot.send_message(uid, "😔 <b>Нет сервисов</b>", reply_markup=kb)
    
    running = sum(1 for s in scripts if s['status']=='running')
    stopped = len(scripts) - running
    uptime = round((running/len(scripts))*100) if len(scripts) > 0 else 0
    
    text = (
        f"💻 <b>МОИ СЕРВИСЫ</b>\n\n"
        f"🟢 {running} | 🔴 {stopped} | 📈 {uptime}%\n\n"
    )
    
    kb = InlineKeyboardMarkup()
    for i, s in enumerate(scripts, 1):
        st = "🟢" if s['status']=='running' else "🔴"
        sz = (s['size'] or 0)/1024/1024
        text += f"{st} <b>{s['name']}</b> | {sz:.1f}МБ | <code>{s['id']}</code>\n"
        kb.add(
            InlineKeyboardButton(f"⏹ Стоп {i}" if s['status']=='running' else f"▶️ Старт {i}", callback_data=f"stop:{s['id']}"),
            InlineKeyboardButton(f"🗑 Удалить {i}", callback_data=f"del:{s['id']}")
        )
    kb.add(InlineKeyboardButton("📤 Загрузить ещё", callback_data="upload_btn"))
    bot.send_message(uid, text, reply_markup=kb)

# ========== ПРОФИЛЬ ==========
@bot.message_handler(func=lambda m: m.text == '👤 Профиль')
def profile(message):
    uid = message.from_user.id
    scripts = get_scripts(uid)
    running = sum(1 for s in scripts if s['status']=='running')
    text = f"👤 <b>ПРОФИЛЬ</b>\n\n🆔 <code>{uid}</code>\n📦 Хостов: {len(scripts)}/{FREE_SCRIPTS}\n🟢 Запущено: {running}"
    bot.send_message(uid, text)

# ========== ПОМОЩЬ ==========
@bot.message_handler(func=lambda m: m.text == '🆘 Помощь')
def help_cmd(message):
    text = f"🆘 <b>ПОМОЩЬ</b>\n\n📤 Загрузить - .py или .zip\n💻 Мои хосты - управление\n👤 Профиль - статистика\n\n📦 Лимит: {FREE_SCRIPTS} скриптов\n📊 Размер: до {FREE_SIZE_MB}МБ"
    bot.send_message(message.chat.id, text)

# ========== АДМИН КОМАНДЫ ==========
@bot.message_handler(func=lambda m: m.text == '👥 Пользователи' and m.from_user.id in ADMIN_IDS)
def admin_users(message):
    users = get_all_users()
    if not users: return bot.send_message(message.chat.id, "Нет пользователей")
    text = f"👥 <b>ПОЛЬЗОВАТЕЛИ ({len(users)})</b>\n\n"
    for u in users[:20]:
        cnt = count_scripts(u['user_id'])
        text += f"🆔 <code>{u['user_id']}</code> | @{u.get('username','?')} | 📦{cnt}\n"
    if len(users) > 20: text += f"\n... и ещё {len(users)-20}"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == '📊 Статистика' and m.from_user.id in ADMIN_IDS)
def stats(message):
    users = get_all_users()
    scripts = get_all_scripts()
    running = sum(1 for s in scripts if s['status']=='running')
    total_size = sum(s.get('size',0) or 0 for s in scripts)
    text = f"📊 <b>СТАТИСТИКА</b>\n\n👥 {len(users)}\n📦 {len(scripts)} (🟢{running})\n💾 {total_size/1024/1024:.1f}МБ"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == '📨 Рассылка' and m.from_user.id in ADMIN_IDS)
def broadcast(message):
    broadcast_set.add(message.from_user.id)
    bot.send_message(message.chat.id, "📨 Отправьте сообщение (текст/фото):")

@bot.message_handler(func=lambda m: m.text == '📦 Все хосты' and m.from_user.id in ADMIN_IDS)
def all_hosts(message):
    scripts = get_all_scripts()
    if not scripts: return bot.send_message(message.chat.id, "Нет хостов")
    text = f"📦 <b>ВСЕ ХОСТЫ ({len(scripts)})</b>\n\n"
    for s in scripts[:15]:
        st = "🟢" if s['status']=='running' else "🔴"
        text += f"{st} <b>{s['name']}</b> | user{s['user_id']}\n<code>{s['id']}</code>\n\n"
    if len(scripts) > 15: text += f"... и ещё {len(scripts)-15}"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == '📥 Файлы юзера' and m.from_user.id in ADMIN_IDS)
def get_files(message):
    admin_act[message.from_user.id] = 'get_files'
    bot.send_message(message.chat.id, "🆔 ID пользователя:")

@bot.message_handler(func=lambda m: m.text == '🗑 Удалить хосты' and m.from_user.id in ADMIN_IDS)
def del_hosts(message):
    admin_act[message.from_user.id] = 'del_hosts'
    bot.send_message(message.chat.id, "🆔 ID пользователя:")

@bot.message_handler(func=lambda m: m.text == '🚫 Забанить' and m.from_user.id in ADMIN_IDS)
def ban_user_btn(message):
    admin_act[message.from_user.id] = 'ban'
    bot.send_message(message.chat.id, "🆔 ID для бана:")

@bot.message_handler(func=lambda m: m.text == '🟢 Разбанить' and m.from_user.id in ADMIN_IDS)
def unban_user_btn(message):
    admin_act[message.from_user.id] = 'unban'
    bot.send_message(message.chat.id, "🆔 ID для разбана:")

@bot.message_handler(func=lambda m: m.text in ['🛑 Стоп бот', '🟢 Старт бот'] and m.from_user.id in ADMIN_IDS)
def toggle_bot(message):
    global bot_active
    bot_active = not bot_active
    if not bot_active:
        for s in get_all_scripts():
            if s['status'] == 'running':
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute('UPDATE scripts SET status=? WHERE id=?', ('stopped', s['id']))
                conn.commit(); conn.close()
    bot.send_message(message.chat.id, "🔴 Бот остановлен!" if not bot_active else "🟢 Бот запущен!", reply_markup=admin_kb())

@bot.message_handler(func=lambda m: m.text == '👤 Юзер' and m.from_user.id in ADMIN_IDS)
def user_mode(message):
    bot.send_message(message.chat.id, "👤 Режим пользователя", reply_markup=user_kb())

# ========== ТЕКСТ ==========
@bot.message_handler(content_types=['text'])
def handle_text(message):
    uid = message.from_user.id; text = message.text.strip() if message.text else ""
    
    # Рассылка
    if uid in broadcast_set:
        broadcast_set.discard(uid)
        users = get_all_users()
        sent = 0
        for u in users:
            try: bot.send_message(u['user_id'], f"📢 <b>Рассылка Ohoster</b>\n\n{text}"); sent += 1
            except: pass; time.sleep(0.03)
        bot.send_message(uid, f"✅ Отправлено: {sent}/{len(users)}")
        return
    
    # Админ действия
    if uid in admin_act:
        action = admin_act.pop(uid)
        try: target = int(text)
        except: return bot.send_message(uid, "❌ Неверный ID")
        
        if action == 'get_files':
            user_dir = FILES_DIR / str(target)
            if user_dir.exists() and list(user_dir.iterdir()):
                for f in user_dir.iterdir():
                    if f.is_file():
                        with open(f, 'rb') as file:
                            bot.send_document(uid, file, caption=f"📄 {f.name}")
                bot.send_message(uid, "✅ Файлы отправлены!")
            else:
                bot.send_message(uid, "❌ Нет файлов")
        
        elif action == 'del_hosts':
            conn = sqlite3.connect(str(DB_PATH))
            scripts = conn.execute('SELECT * FROM scripts WHERE user_id=?', (target,)).fetchall()
            for s in scripts: shutil.rmtree(s[3], ignore_errors=True)
            conn.execute('DELETE FROM scripts WHERE user_id=?', (target,))
            conn.commit(); conn.close()
            bot.send_message(uid, f"✅ Хосты user{target} удалены!")
        
        elif action == 'ban':
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute('INSERT OR REPLACE INTO banned VALUES (?)', (target,))
            scripts = conn.execute('SELECT * FROM scripts WHERE user_id=?', (target,)).fetchall()
            for s in scripts: shutil.rmtree(s[3], ignore_errors=True)
            conn.execute('DELETE FROM scripts WHERE user_id=?', (target,))
            conn.commit(); conn.close()
            bot.send_message(uid, f"🚫 user{target} забанен!")
            try: bot.send_message(target, "🚫 Вы забанены в Ohoster!")
            except: pass
        
        elif action == 'unban':
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute('DELETE FROM banned WHERE user_id=?', (target,))
            conn.commit(); conn.close()
            bot.send_message(uid, f"🟢 user{target} разбанен!")
            try: bot.send_message(target, "🟢 Вы разбанены в Ohoster!")
            except: pass
        return

# ========== ФОТО ==========
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    if uid in broadcast_set:
        broadcast_set.discard(uid)
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
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    if data.startswith("stop:"):
        sid = data.split(":")[1]
        s = conn.execute('SELECT * FROM scripts WHERE id=? AND user_id=?', (sid, uid)).fetchone()
        if s:
            if s['status'] == 'running':
                conn.execute('UPDATE scripts SET status=? WHERE id=?', ('stopped', sid))
            else:
                pid = run_script(s['path'])
                if pid: conn.execute('UPDATE scripts SET status=? WHERE id=?', ('running', sid))
            conn.commit()
        conn.close()
        hosts(call.message)
    
    elif data.startswith("del:"):
        sid = data.split(":")[1]
        s = conn.execute('SELECT * FROM scripts WHERE id=? AND user_id=?', (sid, uid)).fetchone()
        if s:
            conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
            conn.commit()
            shutil.rmtree(s['path'], ignore_errors=True)
        conn.close()
        hosts(call.message)
    else:
        conn.close()

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    init_db()
    threading.Thread(target=start_web, daemon=True).start()
    print(f"🚀 Ohoster Bot | Port: {PORT}")
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)
            bot.remove_webhook()
            time.sleep(5)
