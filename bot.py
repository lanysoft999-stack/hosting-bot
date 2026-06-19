# bot.py - Хостинг бот (СТАБИЛЬНАЯ ВЕРСИЯ - НЕ ПЕРЕЗАПУСКАЕТСЯ)
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
import threading
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN", "8964647336:AAEoMHcCKOeMU37VasxqbWItyFIFUg4mGFQ")
VERSION = "50.0.0"
ADMIN_IDS = [314148464]
FREE_TRIAL_DAYS = 3
FREE_MAX_SCRIPTS = 3
FREE_MAX_SIZE_MB = 5
PORT = int(os.environ.get('PORT', 10000))

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
FILES_DIR = BASE_DIR / "user_files"
DATABASE_PATH = BASE_DIR / "bot_database.db"

for d in [SCRIPTS_DIR, TEMP_DIR, FILES_DIR]:
    d.mkdir(exist_ok=True)

# ========== ТАРИФЫ ==========
TIER_INFO = {
    "1": {"name": "🌱 Новичок", "price_7d": 29, "cpu": "1 vCPU", "ram": "512 MB", "scripts": 3},
    "2": {"name": "⚡ Стандарт", "price_7d": 49, "cpu": "2 vCPU", "ram": "1 GB", "scripts": 5},
    "3": {"name": "👑 Эксперт", "price_7d": 79, "cpu": "3 vCPU", "ram": "2 GB", "scripts": 10},
}

DAYS_NAMES = {"7": "7 дней", "30": "30 дней", "90": "90 дней"}

def calc_price(tier, days):
    return TIER_INFO.get(tier, {}).get("price_7d", 0) * {"7": 1, "30": 4, "90": 10}.get(days, 1)

# ========== БД ==========
def get_db():
    return sqlite3.connect(str(DATABASE_PATH), timeout=30, check_same_thread=False)

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0,
            subscription TEXT DEFAULT 'free', subscription_expiry TIMESTAMP,
            current_tier TEXT, is_premium INTEGER DEFAULT 0, banned INTEGER DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY, user_id INTEGER, name TEXT, path TEXT,
            pid TEXT, status TEXT DEFAULT 'stopped', size INTEGER, original_file TEXT)''')
        conn.commit()

def get_user(uid):
    with get_db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone()
        return dict(row) if row else None

def create_user(uid, username):
    with get_db() as conn:
        conn.execute('INSERT OR IGNORE INTO users (user_id, username, subscription_expiry) VALUES (?,?,?)',
                    (uid, username, (datetime.now() + timedelta(days=FREE_TRIAL_DAYS)).isoformat()))
        conn.commit()

def check_subscription(uid):
    if uid in ADMIN_IDS: return True
    u = get_user(uid)
    if not u or u.get('banned'): return False
    if u.get('is_premium') and u.get('subscription_expiry'):
        try:
            if datetime.now() < datetime.fromisoformat(u['subscription_expiry']): return True
        except: pass
    if u.get('subscription_expiry'):
        try:
            if datetime.now() < datetime.fromisoformat(u['subscription_expiry']): return True
        except: pass
    return False

def get_user_scripts(uid):
    with get_db() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY rowid DESC', (uid,)).fetchall()]

def get_script(sid):
    with get_db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM scripts WHERE id=?', (sid,)).fetchone()
        return dict(row) if row else None

def add_script(sid, uid, name, path, size, pid=None, original_file=None):
    with get_db() as conn:
        conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?,?,?)',
                    (sid, uid, name, str(path), pid, 'running' if pid else 'stopped', size, original_file))
        conn.commit()

def update_script(sid, status=None, pid=None, name=None, path=None, size=None, original_file=None):
    with get_db() as conn:
        if status: conn.execute('UPDATE scripts SET status=? WHERE id=?', (status, sid))
        if pid is not None: conn.execute('UPDATE scripts SET pid=? WHERE id=?', (pid, sid))
        if name: conn.execute('UPDATE scripts SET name=?, path=?, size=?, original_file=? WHERE id=?', 
                             (name, str(path), size, original_file, sid))
        conn.commit()

def delete_script(sid):
    with get_db() as conn:
        conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
        conn.commit()

def count_scripts(uid):
    with get_db() as conn:
        return conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]

def get_all_scripts():
    with get_db() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM scripts').fetchall()]

def get_all_users():
    with get_db() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM users').fetchall()]

def set_subscription(uid, days=30, tier=None):
    with get_db() as conn:
        conn.execute('UPDATE users SET subscription=?, subscription_expiry=?, is_premium=1, current_tier=? WHERE user_id=?',
                    ('pro', (datetime.now()+timedelta(days=days)).isoformat(), tier, uid))
        conn.commit()

def remove_subscription(uid):
    with get_db() as conn:
        conn.execute('UPDATE users SET subscription=?, subscription_expiry=NULL, is_premium=0 WHERE user_id=?', ('free', uid))
        conn.commit()

def ban_user(uid): 
    with get_db() as conn: conn.execute('UPDATE users SET banned=1 WHERE user_id=?', (uid,)); conn.commit()

def unban_user(uid):
    with get_db() as conn: conn.execute('UPDATE users SET banned=0 WHERE user_id=?', (uid,)); conn.commit()

def get_limits(uid):
    if uid in ADMIN_IDS: return 999, 1024
    u = get_user(uid)
    if not u or u.get('banned'): return 0, 0
    if u.get('is_premium') and u.get('current_tier') in TIER_INFO:
        return TIER_INFO[u['current_tier']]['scripts'], 50
    return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB

def save_file(uid, name, data):
    d = FILES_DIR / str(uid); d.mkdir(exist_ok=True)
    p = d / f"{uuid.uuid4().hex[:8]}_{name}"; p.write_bytes(data); return str(p)

# ========== ЗАПУСК СКРИПТА ==========
def run_script(path):
    for f in Path(path).rglob("*.py"):
        if f.name in ('main.py', 'bot.py'):
            try:
                p = subprocess.Popen([sys.executable, str(f)], cwd=str(path), 
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return str(p.pid)
            except: return None
    # Если main.py не найден - берем первый .py
    py_files = list(Path(path).rglob("*.py"))
    if py_files:
        try:
            p = subprocess.Popen([sys.executable, str(py_files[0])], cwd=str(path),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return str(p.pid)
        except: return None
    return None

def kill_process(pid):
    try: os.kill(int(pid), signal.SIGTERM); return True
    except: return False

# ========== ВЕБ-СЕРВЕР ==========
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'OK')
    def log_message(self, *args): pass

def web_server():
    HTTPServer(('0.0.0.0', PORT), PingHandler).serve_forever()

# ========== БОТ ==========
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')
bot.remove_webhook()
time.sleep(3)  # Ждем завершения старых соединений

# Состояния
upload_waiting = set()
replace_waiting = {}
broadcast_waiting = set()
promo_state = {}
admin_state = {}

# Клавиатуры
def user_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📤 Загрузить", "💻 Хосты", "🛒 Тарифы", "💳 Баланс", "👤 Профиль", "🆘 Помощь")
    return kb

def admin_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("👥 Пользователи", "📊 Статистика", "📨 Рассылка", "🎫 Промокод")
    kb.add("🎁 Выдать", "❌ Отозвать", "🛑 Стоп", "🟢 Старт", "👤 Юзер")
    return kb

# ========== ОБРАБОТЧИКИ ==========
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    if not get_user(uid): create_user(uid, message.from_user.username)
    
    if uid in ADMIN_IDS:
        scripts = get_all_scripts(); r = sum(1 for s in scripts if s['status']=='running')
        users = get_all_users(); p = sum(1 for u in users if u.get('is_premium'))
        return bot.send_message(uid, f"👑 АДМИН v{VERSION}\n👥 {len(users)} (💎{p}) | 📦 {len(scripts)} | 🟢 {r}", reply_markup=admin_kb())
    
    if not check_subscription(uid):
        kb = InlineKeyboardMarkup(); kb.add(InlineKeyboardButton("🛒 Тарифы от 29₽", callback_data="shop"))
        return bot.send_message(uid, "❌ Доступ закрыт!\n💎 Тарифы от 29₽", reply_markup=kb)
    
    scripts = get_user_scripts(uid); r = sum(1 for s in scripts if s['status']=='running')
    bot.send_message(uid, f"🚀 HOSTING\n📦 {len(scripts)} | 🟢 {r}", reply_markup=user_kb())

@bot.message_handler(func=lambda m: m.text in ['📤 Загрузить', '📤 Загрузить файл'])
def upload_start(message):
    uid = message.from_user.id
    if not check_subscription(uid): return bot.send_message(uid, "❌ Нет доступа!")
    mx, _ = get_limits(uid)
    if count_scripts(uid) >= mx: return bot.send_message(uid, f"❌ Лимит!")
    upload_waiting.add(uid)
    bot.send_message(uid, f"📤 Отправьте .py или .zip файл")

@bot.message_handler(content_types=['document'])
def handle_doc(message):
    uid = message.from_user.id
    doc = message.document; fn = doc.file_name; fs = doc.file_size
    
    # Замена файла
    if uid in replace_waiting:
        sid = replace_waiting.pop(uid); s = get_script(sid)
        if not s: return bot.send_message(uid, "❌ Не найден!")
        
        if s.get('pid'): kill_process(s['pid'])
        fi = bot.get_file(doc.file_id); dl = bot.download_file(fi.file_path)
        orig = save_file(uid, fn, dl)
        
        shutil.rmtree(s['path'], ignore_errors=True)
        Path(s['path']).mkdir(parents=True, exist_ok=True)
        
        tmp = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]; tmp.mkdir(parents=True, exist_ok=True)
        (tmp/fn).write_bytes(dl)
        
        try:
            if fn.endswith('.zip'):
                with zipfile.ZipFile(tmp/fn) as z: z.extractall(s['path'])
                ts = sum(f.stat().st_size for f in Path(s['path']).rglob('*') if f.is_file())
            else:
                shutil.copy2(str(tmp/fn), str(Path(s['path'])/fn)); ts = fs
            
            pid = run_script(s['path'])
            if pid:
                update_script(sid, status='running', pid=pid, name=fn, path=s['path'], size=ts, original_file=orig)
                bot.send_message(uid, f"✅ Заменён и запущен!\n📄 {fn}\n🟢 PID: {pid}")
            else:
                bot.send_message(uid, "❌ Ошибка запуска!")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return
    
    # Обычная загрузка
    if uid not in upload_waiting: return
    upload_waiting.discard(uid)
    
    _, mx = get_limits(uid)
    if not fn.endswith(('.py', '.zip')): return bot.send_message(uid, "❌ .py или .zip!")
    if fs > mx*1024*1024: return bot.send_message(uid, f"❌ Макс {mx}МБ!")
    
    msg = bot.send_message(uid, "📥 Загрузка...")
    fi = bot.get_file(doc.file_id); dl = bot.download_file(fi.file_path)
    orig = save_file(uid, fn, dl)
    
    sid = uuid.uuid4().hex[:8]
    script_dir = SCRIPTS_DIR / str(uid) / sid; script_dir.mkdir(parents=True, exist_ok=True)
    
    tmp = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]; tmp.mkdir(parents=True, exist_ok=True)
    (tmp/fn).write_bytes(dl)
    
    try:
        if fn.endswith('.zip'):
            with zipfile.ZipFile(tmp/fn) as z: z.extractall(script_dir)
            ts = sum(f.stat().st_size for f in script_dir.rglob('*') if f.is_file())
        else:
            shutil.copy2(str(tmp/fn), str(script_dir/fn)); ts = fs
        
        pid = run_script(str(script_dir))
        if pid:
            add_script(sid, uid, fn, str(script_dir), ts, pid, orig)
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("⏹ Стоп", callback_data=f"stop:{sid}"), 
                   InlineKeyboardButton("🔄 Заменить", callback_data=f"rep:{sid}"))
            kb.add(InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{sid}"),
                   InlineKeyboardButton("📥 Скачать", callback_data=f"dl:{sid}"))
            bot.edit_message_text(f"✅ ЗАПУЩЕН!\n📄 {fn}\n🆔 {sid}\n📦 {ts/1024/1024:.1f}МБ\n🟢 PID: {pid}", 
                                uid, msg.message_id, reply_markup=kb)
        else:
            bot.edit_message_text("❌ Ошибка запуска!", uid, msg.message_id)
            shutil.rmtree(script_dir, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@bot.message_handler(func=lambda m: m.text in ['💻 Хосты', '💻 Мои хосты'])
def hosts(message):
    uid = message.from_user.id; scripts = get_user_scripts(uid)
    if not scripts:
        kb = InlineKeyboardMarkup(); kb.add(InlineKeyboardButton("📤 Загрузить", callback_data="upload"))
        return bot.send_message(uid, "😔 Нет хостов", reply_markup=kb)
    
    text = f"💻 ХОСТЫ ({len(scripts)})\n\n"; kb = InlineKeyboardMarkup()
    for s in scripts:
        st = "🟢" if s['status']=='running' else "🔴"; sz = (s['size'] or 0)/1024/1024
        text += f"{st} {s['name']} | {sz:.1f}МБ | {s['id']}\n"
        kb.add(InlineKeyboardButton("⏹" if s['status']=='running' else "▶️", callback_data=f"stop:{s['id']}"),
               InlineKeyboardButton("🔄", callback_data=f"rep:{s['id']}"),
               InlineKeyboardButton("🗑", callback_data=f"del:{s['id']}"),
               InlineKeyboardButton("📥", callback_data=f"dl:{s['id']}"))
    kb.add(InlineKeyboardButton("📤 Загрузить", callback_data="upload"))
    bot.send_message(uid, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.text in ['🛒 Тарифы', '🛒 Магазин'])
def shop(message):
    kb = InlineKeyboardMarkup()
    for tid, t in TIER_INFO.items(): kb.add(InlineKeyboardButton(f"{t['name']} | {t['price_7d']}₽", callback_data=f"tier:{tid}"))
    bot.send_message(message.chat.id, "ТАРИФЫ\n🌱 29₽ | ⚡ 49₽ | 👑 79₽", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text in ['💳 Баланс', '💳 Пополнить'])
def balance(message):
    u = get_user(message.from_user.id); bal = u.get('balance',0) if u else 0
    bot.send_message(message.chat.id, f"💳 Баланс: {bal:.0f}₽\n💳 СБЕР: 2202206714879132")

@bot.message_handler(func=lambda m: m.text == '👤 Профиль')
def profile(message):
    uid = message.from_user.id; u = get_user(uid)
    if not u: return
    scr = get_user_scripts(uid); mx, _ = get_limits(uid)
    bot.send_message(uid, f"👤 Профиль\n🆔 {uid}\n💰 {u.get('balance',0):.0f}₽\n📦 {len(scr)}/{mx}")

@bot.message_handler(func=lambda m: m.text in ['🆘 Помощь', '🆘 Поддержка'])
def support(message):
    kb = InlineKeyboardMarkup(); kb.add(InlineKeyboardButton("📞 Telegram", url="https://t.me/hesers"))
    bot.send_message(message.chat.id, "🆘 Поддержка", reply_markup=kb)

# Админ команды
@bot.message_handler(func=lambda m: m.text == '📨 Рассылка')
def broadcast_start(message):
    if message.from_user.id not in ADMIN_IDS: return
    broadcast_waiting.add(message.from_user.id); bot.send_message(message.chat.id, "📨 Сообщение:")

@bot.message_handler(func=lambda m: m.text in ['🛑 Стоп', '🛑 Стоп бот'])
def stop_bot(message):
    if message.from_user.id not in ADMIN_IDS: return
    for s in get_all_scripts():
        if s['status']=='running' and s.get('pid'): kill_process(s['pid']); update_script(s['id'], status='stopped', pid=None)
    bot.send_message(message.chat.id, "🔴 Стоп!")

@bot.message_handler(func=lambda m: m.text in ['🟢 Старт', '🟢 Старт бот'])
def start_bot(message):
    if message.from_user.id not in ADMIN_IDS: return
    bot.send_message(message.chat.id, "🟢 Старт!")

@bot.message_handler(func=lambda m: m.text == '📊 Статистика')
def stats(message):
    if message.from_user.id not in ADMIN_IDS: return
    u, s = get_all_users(), get_all_scripts(); r = sum(1 for x in s if x['status']=='running')
    bot.send_message(message.chat.id, f"📊 v{VERSION}\n👥 {len(u)}\n📦 {len(s)} (🟢{r})")

@bot.message_handler(func=lambda m: m.text == '👥 Пользователи')
def admin_users(message):
    if message.from_user.id not in ADMIN_IDS: return
    users = get_all_users()
    if not users: return bot.send_message(message.chat.id, "Нет")
    kb = InlineKeyboardMarkup()
    for u in users[:30]:
        sub = "💎" if u.get('is_premium') else "🚫" if u.get('banned') else "🆓"
        kb.add(InlineKeyboardButton(f"{sub} {u['user_id']} | {u.get('username','?')}", callback_data=f"auser:{u['user_id']}"))
    bot.send_message(message.chat.id, f"👥 ПОЛЬЗОВАТЕЛИ ({len(users)})", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text in ['🎁 Выдать', '❌ Отозвать'])
def admin_sub(message):
    if message.from_user.id not in ADMIN_IDS: return
    admin_state[message.from_user.id] = 'give' if 'Выдать' in message.text else 'remove'
    bot.send_message(message.chat.id, "🆔 ID:")

@bot.message_handler(func=lambda m: m.text == '🎫 Промокод')
def admin_promo(message):
    if message.from_user.id not in ADMIN_IDS: return
    promo_state[message.from_user.id] = {'step': 'code'}; bot.send_message(message.chat.id, "🎫 Код:")

@bot.message_handler(func=lambda m: m.text == '👤 Юзер')
def admin_user_mode(message):
    if message.from_user.id not in ADMIN_IDS: return
    bot.send_message(message.chat.id, "👤 Юзер", reply_markup=user_kb())

@bot.message_handler(content_types=['text'])
def handle_text(message):
    uid = message.from_user.id; text = message.text.strip() if message.text else ""
    
    if uid in broadcast_waiting:
        broadcast_waiting.discard(uid); sent = 0
        for u in get_all_users():
            try: bot.send_message(u['user_id'], f"📢 Рассылка\n\n{text}"); sent += 1
            except: pass; time.sleep(0.03)
        return bot.send_message(uid, f"✅ {sent}")
    
    if uid in promo_state:
        step = promo_state[uid]['step']
        if step == 'code':
            promo_state[uid] = {'step': 'days', 'code': text.upper()}
            kb = InlineKeyboardMarkup(); kb.add(InlineKeyboardButton("7д", callback_data="pd:7"), InlineKeyboardButton("30д", callback_data="pd:30"))
            return bot.send_message(uid, f"Код: {text.upper()}\nСрок:", reply_markup=kb)
        elif step == 'days':
            try:
                days = int(text); code = promo_state[uid]['code']
                promo_state[uid] = {'step': 'uses', 'code': code, 'days': days}
                return bot.send_message(uid, f"📅 {days}д\n👥 Макс. использований:")
            except: return bot.send_message(uid, "❌ Число!")
        elif step == 'uses':
            try:
                uses = int(text); data = promo_state.pop(uid)
                with get_db() as conn:
                    conn.execute('INSERT INTO promocodes VALUES (?,?,?,?)', (data['code'], 'pro', data['days'], uses))
                    conn.commit()
                return bot.send_message(uid, f"✅ Промокод {data['code']}!")
            except: return bot.send_message(uid, "❌ Число!")
    
    if uid in admin_state:
        action = admin_state.pop(uid)
        try: target = int(text)
        except: return bot.send_message(uid, "❌ ID")
        if action == 'give':
            kb = InlineKeyboardMarkup()
            for tid, t in TIER_INFO.items(): kb.add(InlineKeyboardButton(t['name'], callback_data=f"gv:{target}:{tid}"))
            return bot.send_message(uid, f"👤 {target}\nТариф:", reply_markup=kb)
        elif action == 'remove': remove_subscription(target); return bot.send_message(uid, f"✅ Отозвано")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    for aid in ADMIN_IDS:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ +100₽", callback_data=f"app:{uid}:100"), InlineKeyboardButton("❌", callback_data=f"rej:{uid}"))
        try: bot.send_photo(aid, message.photo[-1].file_id, caption=f"📸 {uid}", reply_markup=kb)
        except: pass
    bot.send_message(uid, "✅ Отправлено!")

# ========== CALLBACKS ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid = call.from_user.id; data = call.data
    bot.answer_callback_query(call.id)
    
    if data == "upload": return upload_start(call.message)
    if data == "hosts": return hosts(call.message)
    if data == "shop": return shop(call.message)
    
    # Стоп/Старт
    if data.startswith("stop:"):
        sid = data.split(":")[1]; s = get_script(sid)
        if s and s['user_id'] == uid:
            if s['status'] == 'running':
                if s.get('pid'): kill_process(s['pid'])
                update_script(sid, status='stopped', pid=None)
            else:
                pid = run_script(s['path'])
                if pid: update_script(sid, status='running', pid=pid)
            hosts(call.message)
        return
    
    # Замена файла
    if data.startswith("rep:"):
        sid = data.split(":")[1]; s = get_script(sid)
        if s and s['user_id'] == uid:
            replace_waiting[uid] = sid
            bot.send_message(uid, f"🔄 Отправьте новый файл для {s['name']}")
        return
    
    # Удалить
    if data.startswith("del:"):
        sid = data.split(":")[1]; s = get_script(sid)
        if s and s['user_id'] == uid:
            if s.get('pid'): kill_process(s['pid'])
            if s.get('original_file') and os.path.exists(s['original_file']): os.unlink(s['original_file'])
            delete_script(sid)
            shutil.rmtree(s['path'], ignore_errors=True)
            hosts(call.message)
        return
    
    # Скачать
    if data.startswith("dl:"):
        sid = data.split(":")[1]; s = get_script(sid)
        if s:
            if s.get('original_file') and os.path.exists(s['original_file']):
                with open(s['original_file'], 'rb') as f: bot.send_document(uid, f, caption=s['name'])
            else:
                zp = TEMP_DIR / f"{sid}.zip"
                with zipfile.ZipFile(zp, 'w') as zf:
                    for f in Path(s['path']).rglob('*'):
                        if f.is_file(): zf.write(f, f.relative_to(s['path']))
                with open(zp, 'rb') as f: bot.send_document(uid, f, caption=s['name']); zp.unlink()
        return
    
    # Тарифы
    if data.startswith("tier:"):
        tier = data.split(":")[1]
        kb = InlineKeyboardMarkup()
        for d, n in DAYS_NAMES.items(): kb.add(InlineKeyboardButton(f"{n} - {calc_price(tier, d)}₽", callback_data=f"day:{tier}:{d}"))
        kb.add(InlineKeyboardButton("◀️", callback_data="shop"))
        t = TIER_INFO[tier]
        return bot.edit_message_text(f"{t['name']}\n├ {t['cpu']}\n├ {t['ram']}\n└ {t['scripts']} скр.\n📅 Срок:", uid, call.message.message_id, reply_markup=kb)
    
    if data.startswith("day:"):
        _, tier, days = data.split(":")
        total = calc_price(tier, days)
        u = get_user(uid); bal = u.get('balance',0) if u else 0
        kb = InlineKeyboardMarkup()
        if bal >= total: kb.add(InlineKeyboardButton(f"✅ Оплатить {total}₽", callback_data=f"pay:{tier}:{days}"))
        else: kb.add(InlineKeyboardButton(f"❌ {total}₽ (есть {bal}₽)", callback_data="bal"))
        return bot.edit_message_text(f"🧾 {TIER_INFO[tier]['name']}\n📅 {DAYS_NAMES[days]}\n💰 {total}₽\n💳 {bal}₽", uid, call.message.message_id, reply_markup=kb)
    
    if data.startswith("pay:"):
        _, tier, days = data.split(":")
        total = calc_price(tier, days); u = get_user(uid)
        if u.get('balance',0) < total: return
        with get_db() as conn: conn.execute('UPDATE users SET balance=balance-? WHERE user_id=?', (total, uid)); conn.commit()
        set_subscription(uid, 7 if days=='7' else 30 if days=='30' else 90, tier)
        return bot.edit_message_text(f"✅ Куплено!\n{TIER_INFO[tier]['name']}\n📅 {DAYS_NAMES[days]}\n💰 {total}₽", uid, call.message.message_id)
    
    # Админ
    if data.startswith("gv:"):
        if uid not in ADMIN_IDS: return
        _, target, tier = data.split(":"); set_subscription(int(target), 30, tier)
        return bot.send_message(uid, f"✅ Выдано user{target}")
    
    if data.startswith("pd:"):
        days = int(data.split(":")[1])
        promo_state[uid] = {'step': 'uses', 'code': promo_state.get(uid,{}).get('code','PROMO'), 'days': days}
        return bot.edit_message_text(f"📅 {days}д\n👥 Использований:", uid, call.message.message_id)
    
    if data.startswith("app:"):
        if uid not in ADMIN_IDS: return
        _, target, amount = data.split(":"); target, amount = int(target), float(amount)
        with get_db() as conn: conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (amount, target)); conn.commit()
        return bot.edit_message_caption(f"✅ +{amount}₽", uid, call.message.message_id)
    
    if data.startswith("rej:"):
        if uid not in ADMIN_IDS: return
        return bot.edit_message_caption("❌ Отклонено", uid, call.message.message_id)
    
    if data.startswith("auser:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1]); u = get_user(target)
        if not u: return
        is_banned = u.get('banned', 0)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🎁 Выдать", callback_data=f"gv:{target}:2"),
               InlineKeyboardButton("❌ Отозвать", callback_data=f"ausr:{target}"))
        kb.add(InlineKeyboardButton("🟢 Разбанить" if is_banned else "🚫 Забанить",
                                    callback_data=f"ausu:{target}" if is_banned else f"ausb:{target}"))
        return bot.send_message(uid, f"👤 user{target}\n💰 {u.get('balance',0):.0f}₽", reply_markup=kb)
    
    if data.startswith("ausr:"): target = int(data.split(":")[1]); remove_subscription(target); return bot.send_message(uid, f"✅ Отозвано")
    if data.startswith("ausb:"): target = int(data.split(":")[1]); ban_user(target); return bot.send_message(uid, f"🚫 Забанен")
    if data.startswith("ausu:"): target = int(data.split(":")[1]); unban_user(target); return bot.send_message(uid, f"🟢 Разбанен")

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    init_db()
    threading.Thread(target=web_server, daemon=True).start()
    print(f"🚀 Hosting Bot v{VERSION} | Port: {PORT} | Stable")
    
    # Бесконечный цикл с перезапуском при падении
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(10)
            bot.remove_webhook()
            time.sleep(5)ы
