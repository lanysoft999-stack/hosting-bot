# bot.py - Хостинг бот (ПОЛНАЯ ВЕРСИЯ - РАБОЧАЯ)
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
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from aiohttp import web

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN", "8964647336:AAEoMHcCKOeMU37VasxqbWItyFIFUg4mGFQ")
VERSION = "42.0.0"
ADMIN_IDS = [314148464]
SUPPORT_URL = "https://t.me/hesers"
FREE_TRIAL_DAYS = 3
FREE_MAX_SCRIPTS = 3
FREE_MAX_SIZE_MB = 5
PORT = int(os.environ.get('PORT', 10000))

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
FILES_DIR = BASE_DIR / "user_files"
LOGS_DIR = BASE_DIR / "logs"
DATABASE_PATH = BASE_DIR / "bot_database.db"

for d in [SCRIPTS_DIR, TEMP_DIR, FILES_DIR, LOGS_DIR]:
    d.mkdir(exist_ok=True)

# ========== ТАРИФЫ ==========
TIER_INFO = {
    "1": {"name": "🌱 Новичок", "price_7d": 29, "cpu": "1 vCPU", "ram": "512 MB", "scripts": 3, "storage": "5 GB"},
    "2": {"name": "⚡ Стандарт", "price_7d": 49, "cpu": "2 vCPU", "ram": "1 GB", "scripts": 5, "storage": "10 GB"},
    "3": {"name": "👑 Эксперт", "price_7d": 79, "cpu": "3 vCPU", "ram": "2 GB", "scripts": 10, "storage": "25 GB"},
}

DAYS_NAMES = {"7": "7 дней", "30": "30 дней", "90": "90 дней"}

def calc_price(tier, days):
    mul = {"7": 1, "30": 4, "90": 10}
    return TIER_INFO.get(tier, {}).get("price_7d", 0) * mul.get(days, 1)

# ========== БД ==========
def get_db():
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0.0,
                subscription TEXT DEFAULT 'free', subscription_expiry TIMESTAMP,
                free_used INTEGER DEFAULT 0, current_tier TEXT, is_premium INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY, user_id INTEGER, name TEXT, path TEXT,
                pid TEXT, status TEXT DEFAULT 'stopped', size INTEGER,
                original_file TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY, type TEXT DEFAULT 'pro', days INTEGER DEFAULT 30,
                max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0);
        ''')
        conn.commit()

def get_user(uid):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone()
        return dict(row) if row else None

def create_user(uid, username):
    expiry = (datetime.now() + timedelta(days=FREE_TRIAL_DAYS)).isoformat()
    with get_db() as conn:
        conn.execute('INSERT OR IGNORE INTO users (user_id, username, free_used, subscription_expiry) VALUES (?,?,1,?)', 
                    (uid, username, expiry))
        conn.commit()

def check_subscription(uid):
    user = get_user(uid)
    if not user: return False
    if uid in ADMIN_IDS: return True
    if user.get('banned'): return False
    if user.get('is_premium') and user.get('subscription_expiry'):
        try:
            if datetime.now() < datetime.fromisoformat(user['subscription_expiry']): return True
        except: pass
    if user.get('subscription_expiry'):
        try:
            if datetime.now() < datetime.fromisoformat(user['subscription_expiry']): return True
        except: pass
    return False

def get_subscription_info(uid):
    user = get_user(uid)
    if not user: return "Нет", 0
    if uid in ADMIN_IDS: return "👑 Админ", 999
    if user.get('banned'): return "🚫 Забанен", 0
    if user.get('is_premium') and user.get('subscription_expiry'):
        try:
            delta = datetime.fromisoformat(user['subscription_expiry']) - datetime.now()
            if delta.days > 0: return f"💎 Premium", delta.days
        except: pass
    if user.get('subscription_expiry'):
        try:
            delta = datetime.fromisoformat(user['subscription_expiry']) - datetime.now()
            if delta.days > 0: return f"🆓 Бесплатный", delta.days
        except: pass
    return "❌ Истек", 0

def get_user_scripts(uid):
    with get_db() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY created_at DESC', (uid,)).fetchall()]

def get_all_scripts():
    with get_db() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM scripts ORDER BY created_at DESC').fetchall()]

def get_script(sid):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM scripts WHERE id=?', (sid,)).fetchone()
        return dict(row) if row else None

def add_script(sid, uid, name, path, size, pid=None, original_file=None):
    with get_db() as conn:
        conn.execute('INSERT INTO scripts (id, user_id, name, path, pid, status, size, original_file) VALUES (?,?,?,?,?,?,?,?)',
                    (sid, uid, name, str(path), pid, 'running' if pid else 'stopped', size, original_file))
        conn.commit()

def update_script_status(sid, status, pid=None):
    with get_db() as conn:
        if pid: conn.execute('UPDATE scripts SET status=?, pid=? WHERE id=?', (status, pid, sid))
        else: conn.execute('UPDATE scripts SET status=?, pid=NULL WHERE id=?', (status, sid))
        conn.commit()

def delete_script(sid, uid=None):
    with get_db() as conn:
        if uid: conn.execute('DELETE FROM scripts WHERE id=? AND user_id=?', (sid, uid))
        else: conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
        conn.commit()

def get_all_users():
    with get_db() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()]

def count_user_scripts(uid):
    with get_db() as conn:
        return conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]

def set_subscription(uid, plan, days=0, tier=None):
    with get_db() as conn:
        if days > 0:
            expiry = (datetime.now() + timedelta(days=days)).isoformat()
            conn.execute('UPDATE users SET subscription=?, subscription_expiry=?, is_premium=1, current_tier=?, free_used=1 WHERE user_id=?',
                        (plan, expiry, tier, uid))
        else:
            conn.execute('UPDATE users SET subscription=?, subscription_expiry=NULL, is_premium=0 WHERE user_id=?', ('free', uid))
        conn.commit()

def remove_subscription(uid):
    with get_db() as conn:
        conn.execute('UPDATE users SET subscription=?, subscription_expiry=NULL, is_premium=0, current_tier=NULL WHERE user_id=?', ('free', uid))
        conn.commit()

def ban_user(uid):
    with get_db() as conn:
        conn.execute('UPDATE users SET banned=1 WHERE user_id=?', (uid,))
        conn.commit()

def unban_user(uid):
    with get_db() as conn:
        conn.execute('UPDATE users SET banned=0 WHERE user_id=?', (uid,))
        conn.commit()

def get_user_limits(uid):
    if uid in ADMIN_IDS: return 999, 1024
    user = get_user(uid)
    if not user: return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB
    if user.get('banned'): return 0, 0
    if user.get('is_premium'):
        tier = user.get('current_tier')
        if tier in TIER_INFO: return TIER_INFO[tier]['scripts'], 50
    return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB

def save_user_file(uid, file_name, file_data):
    user_dir = FILES_DIR / str(uid)
    user_dir.mkdir(exist_ok=True)
    file_path = user_dir / f"{uuid.uuid4().hex[:8]}_{file_name}"
    file_path.write_bytes(file_data)
    return str(file_path)

def activate_promo(uid, code):
    with get_db() as conn:
        p = conn.execute('SELECT * FROM promocodes WHERE code=?', (code,)).fetchone()
        if not p: return False, "Не найден"
        if p['used_count'] >= p['max_uses']: return False, "Закончился"
        conn.execute('UPDATE promocodes SET used_count=used_count+1 WHERE code=?', (code,))
        expiry = (datetime.now() + timedelta(days=p['days'])).isoformat()
        conn.execute('UPDATE users SET subscription=?, subscription_expiry=?, is_premium=1, free_used=1 WHERE user_id=?', 
                    (p['type'], expiry, uid))
        conn.commit()
    return True, f"{p['type'].upper()} на {p['days']}дн"

def create_promo(code, ptype, days, max_uses):
    with get_db() as conn:
        conn.execute('INSERT OR REPLACE INTO promocodes (code, type, days, max_uses) VALUES (?,?,?,?)',
                    (code, ptype, days, max_uses))
        conn.commit()

# ========== ЗАПУСК СКРИПТА ==========
def run_script(path):
    """Запускает Python скрипт"""
    py_files = list(Path(path).rglob("*.py"))
    if not py_files:
        return None
    
    main_file = py_files[0]
    for f in py_files:
        if f.name == 'main.py':
            main_file = f
            break
    
    try:
        proc = subprocess.Popen(
            [sys.executable, str(main_file)],
            cwd=str(path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return proc.pid
    except:
        return None

def kill_process(pid):
    try:
        os.kill(int(pid), signal.SIGTERM)
        return True
    except:
        return False

# ========== ВЕБ-СЕРВЕР ==========
def run_web_server():
    async def health(request):
        return web.json_response({'status': 'ok', 'version': VERSION})
    
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_get('/ping', health)
    web.run_app(app, host='0.0.0.0', port=PORT)

# ========== БОТ ==========
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')
bot.remove_webhook()
time.sleep(1)

bot_active = True
upload_waiting = set()
broadcast_waiting = set()
promo_creating = {}
admin_waiting = {}
support_chat = {}

# ========== КЛАВИАТУРЫ ==========
def user_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("📤 Загрузить"), KeyboardButton("💻 Хосты"))
    kb.add(KeyboardButton("🛒 Тарифы"), KeyboardButton("💳 Баланс"))
    kb.add(KeyboardButton("👤 Профиль"), KeyboardButton("🆘 Помощь"))
    return kb

def admin_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("👥 Пользователи"), KeyboardButton("📊 Статистика"))
    kb.add(KeyboardButton("📨 Рассылка"), KeyboardButton("🎫 Промокод"))
    kb.add(KeyboardButton("🎁 Выдать"), KeyboardButton("❌ Отозвать"))
    kb.add(KeyboardButton("📦 Хосты"), KeyboardButton("🛑 Стоп"))
    kb.add(KeyboardButton("🟢 Старт"), KeyboardButton("👤 Юзер"))
    return kb

# ========== СТАРТ ==========
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id
    if not get_user(uid): 
        create_user(uid, message.from_user.username)
        bot.send_message(uid, f"🎉 Добро пожаловать!\n🆓 Бесплатный тариф на {FREE_TRIAL_DAYS} дня!")
    
    if uid in ADMIN_IDS:
        scripts = get_all_scripts()
        running = len([s for s in scripts if s['status']=='running'])
        users = get_all_users()
        premium = len([u for u in users if u.get('is_premium')])
        bot.send_message(uid,
            f"👑 <b>АДМИН</b> | v{VERSION}\n👥 {len(users)} (💎{premium}) | 📦 {len(scripts)} | 🟢 {running}",
            reply_markup=admin_keyboard())
        return
    
    user = get_user(uid)
    if user and user.get('banned'):
        return bot.send_message(uid, "🚫 <b>ВЫ ЗАБАНЕНЫ!</b>")
    
    if not check_subscription(uid):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🛒 Тарифы от 29₽", callback_data="shop"),
               InlineKeyboardButton("🎫 Промокод", callback_data="promo"))
        bot.send_message(uid, "❌ Доступ закрыт!\n💎 Тарифы от 29₽", reply_markup=kb)
        return
    
    scripts = get_user_scripts(uid)
    running = len([s for s in scripts if s['status']=='running'])
    sub_info, _ = get_subscription_info(uid)
    limits = get_user_limits(uid)
    
    bot.send_message(uid,
        f"🚀 <b>HOSTING</b>\n📋 {sub_info} | 📦 {len(scripts)}/{limits[0]} | 🟢 {running}",
        reply_markup=user_keyboard())

# ========== ЗАГРУЗКА ФАЙЛА ==========
@bot.message_handler(func=lambda m: m.text in ['📤 Загрузить', '📤 Загрузить файл'])
def upload_start(message):
    uid = message.from_user.id
    if not check_subscription(uid):
        return bot.send_message(uid, "❌ Нет доступа!")
    
    mx, size_mb = get_user_limits(uid)
    current = count_user_scripts(uid)
    if current >= mx:
        return bot.send_message(uid, f"❌ Лимит! {current}/{mx}")
    
    upload_waiting.add(uid)
    bot.send_message(uid, f"📤 Отправьте .py или .zip файл (до {size_mb}МБ)")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    uid = message.from_user.id
    
    if uid not in upload_waiting:
        bot.reply_to(message, "❌ Используйте кнопку «📤 Загрузить»!")
        return
    
    doc = message.document
    fn = doc.file_name
    fs = doc.file_size
    _, mx = get_user_limits(uid)
    
    if not fn.endswith(('.py', '.zip')):
        upload_waiting.discard(uid)
        return bot.send_message(uid, "❌ Только .py или .zip!")
    
    if fs > mx * 1024 * 1024:
        upload_waiting.discard(uid)
        return bot.send_message(uid, f"❌ Макс {mx}МБ!")
    
    status_msg = bot.send_message(uid, "📥 Скачивание...")
    
    file_info = bot.get_file(doc.file_id)
    downloaded = bot.download_file(file_info.file_path)
    
    tmp_dir = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / fn
    
    with open(tmp_file, 'wb') as f:
        f.write(downloaded)
    
    original_path = save_user_file(uid, fn, downloaded)
    
    sid = uuid.uuid4().hex[:8]
    script_dir = SCRIPTS_DIR / str(uid) / sid
    script_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        if fn.endswith('.zip'):
            with zipfile.ZipFile(tmp_file) as z:
                z.extractall(script_dir)
            total_size = sum(f.stat().st_size for f in script_dir.rglob('*') if f.is_file())
        else:
            shutil.copy2(str(tmp_file), str(script_dir / fn))
            total_size = fs
        
        bot.edit_message_text("⚡ Запуск...", uid, status_msg.message_id)
        
        pid = run_script(str(script_dir))
        
        if pid:
            add_script(sid, uid, fn, str(script_dir), total_size, str(pid), original_path)
            
            kb = InlineKeyboardMarkup()
            kb.add(
                InlineKeyboardButton("📥 Скачать", callback_data=f"dl:{sid}"),
                InlineKeyboardButton("⏹ Стоп", callback_data=f"sc:run:{sid}")
            )
            kb.add(InlineKeyboardButton("💻 Хосты", callback_data="hosts"))
            
            bot.edit_message_text(
                f"✅ <b>ХОСТ ЗАПУЩЕН!</b>\n\n"
                f"📄 <code>{fn}</code>\n"
                f"🆔 <code>{sid}</code>\n"
                f"📦 {total_size/1024/1024:.1f} МБ\n"
                f"⚡ 🟢 Запущен\n"
                f"PID: <code>{pid}</code>",
                uid, status_msg.message_id,
                reply_markup=kb
            )
        else:
            bot.edit_message_text("❌ Ошибка запуска!", uid, status_msg.message_id)
            shutil.rmtree(script_dir, ignore_errors=True)
    
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", uid, status_msg.message_id)
        shutil.rmtree(script_dir, ignore_errors=True)
    
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        upload_waiting.discard(uid)

# ========== ХОСТЫ ==========
@bot.message_handler(func=lambda m: m.text in ['💻 Хосты', '💻 Мои хосты'])
def show_hosts(message):
    uid = message.from_user.id
    scripts = get_user_scripts(uid)
    
    if not scripts:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📤 Загрузить", callback_data="upload"))
        return bot.send_message(uid, "😔 Нет хостов", reply_markup=kb)
    
    text = f"💻 <b>ХОСТЫ ({len(scripts)})</b>\n\n"
    kb = InlineKeyboardMarkup()
    
    for s in scripts:
        st = "🟢" if s['status']=='running' else "🔴"
        sz = (s['size'] or 0) / 1024 / 1024
        text += f"{st} <b>{s['name']}</b> | {sz:.1f}МБ | <code>{s['id']}</code>\n"
        
        kb.add(
            InlineKeyboardButton("⏹" if s['status']=='running' else "▶️", callback_data=f"sc:run:{s['id']}"),
            InlineKeyboardButton("📥", callback_data=f"dl:{s['id']}"),
            InlineKeyboardButton("🗑", callback_data=f"sc:del:{s['id']}")
        )
    
    kb.add(InlineKeyboardButton("📤 Загрузить", callback_data="upload"))
    bot.send_message(uid, text, reply_markup=kb)

# ========== ТАРИФЫ ==========
@bot.message_handler(func=lambda m: m.text in ['🛒 Тарифы', '🛒 Магазин'])
def shop(message):
    kb = InlineKeyboardMarkup()
    for tid, t in TIER_INFO.items():
        kb.add(InlineKeyboardButton(f"{t['name']} | {t['cpu']} {t['ram']} | {t['price_7d']}₽", callback_data=f"tier:{tid}"))
    bot.send_message(message.chat.id, "<b>ТАРИФЫ</b>\n\n🌱 29₽ | ⚡ 49₽ | 👑 79₽", reply_markup=kb)

# ========== БАЛАНС ==========
@bot.message_handler(func=lambda m: m.text in ['💳 Баланс', '💳 Пополнить'])
def balance(message):
    uid = message.from_user.id
    user = get_user(uid)
    bal = user.get('balance', 0) if user else 0
    
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Карта СБЕР", callback_data="dep_card"))
    bot.send_message(uid, f"💳 <b>Баланс: {bal:.0f}₽</b>\n\n💳 СБЕР: <code>2202206714879132</code>", reply_markup=kb)

# ========== ПРОФИЛЬ ==========
@bot.message_handler(func=lambda m: m.text == '👤 Профиль')
def profile(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u: return bot.send_message(uid, "❌ /start")
    
    sub_info, days = get_subscription_info(uid)
    scr = get_user_scripts(uid)
    mx, _ = get_user_limits(uid)
    
    text = f"👤 <b>Профиль</b>\n🆔 {uid}\n💰 {u.get('balance',0):.0f}₽\n📋 {sub_info}\n📦 {len(scr)}/{mx}"
    if days: text += f"\n⏳ {days}дн"
    
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎫 Промокод", callback_data="promo"),
           InlineKeyboardButton("💳 Пополнить", callback_data="bal"))
    bot.send_message(uid, text, reply_markup=kb)

# ========== ПОДДЕРЖКА ==========
@bot.message_handler(func=lambda m: m.text in ['🆘 Помощь', '🆘 Поддержка'])
def support(message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💬 Чат с админом", callback_data="chat"),
           InlineKeyboardButton("📞 Telegram", url=SUPPORT_URL))
    bot.send_message(message.chat.id, "🆘 <b>Поддержка</b>", reply_markup=kb)

# ========== РАССЫЛКА ==========
@bot.message_handler(func=lambda m: m.text == '📨 Рассылка')
def broadcast_start(message):
    if message.from_user.id not in ADMIN_IDS: return
    broadcast_waiting.add(message.from_user.id)
    bot.send_message(message.chat.id, "📨 Отправьте сообщение для рассылки:")

# ========== СТОП/СТАРТ ==========
@bot.message_handler(func=lambda m: m.text in ['🛑 Стоп', '🛑 Стоп бот'])
def stop_bot(message):
    if message.from_user.id not in ADMIN_IDS: return
    global bot_active
    bot_active = False
    stopped = 0
    for s in get_all_scripts():
        if s['status'] == 'running' and s.get('pid'):
            kill_process(s['pid'])
            update_script_status(s['id'], 'stopped')
            stopped += 1
    bot.send_message(message.chat.id, f"🔴 Стоп! ⏹ {stopped}")

@bot.message_handler(func=lambda m: m.text in ['🟢 Старт', '🟢 Старт бот'])
def start_bot(message):
    if message.from_user.id not in ADMIN_IDS: return
    global bot_active
    bot_active = True
    bot.send_message(message.chat.id, "🟢 Старт!")

# ========== АДМИН: СТАТИСТИКА ==========
@bot.message_handler(func=lambda m: m.text == '📊 Статистика')
def stats(message):
    if message.from_user.id not in ADMIN_IDS: return
    u, s = get_all_users(), get_all_scripts()
    r = len([x for x in s if x['status']=='running'])
    bal = sum(x.get('balance', 0) for x in u)
    bot.send_message(message.chat.id, f"📊 v{VERSION}\n👥 {len(u)}\n📦 {len(s)} (🟢{r})\n💰 {bal:.0f}₽")

# ========== АДМИН: ПОЛЬЗОВАТЕЛИ ==========
@bot.message_handler(func=lambda m: m.text == '👥 Пользователи')
def admin_users(message):
    if message.from_user.id not in ADMIN_IDS: return
    users = get_all_users()
    if not users: return bot.send_message(message.chat.id, "Нет")
    
    kb = InlineKeyboardMarkup()
    for u in users[:30]:
        sub = "💎" if u.get('is_premium') else "🚫" if u.get('banned') else "🆓"
        kb.add(InlineKeyboardButton(f"{sub} {u['user_id']} | {u.get('username','Нет')}", callback_data=f"auser:{u['user_id']}"))
    kb.add(InlineKeyboardButton("🔍 Поиск", callback_data="search_user"))
    
    bot.send_message(message.chat.id, f"👥 <b>ПОЛЬЗОВАТЕЛИ ({len(users)})</b>", reply_markup=kb)

# ========== АДМИН: ВЫДАЧА/ОТЗЫВ ==========
@bot.message_handler(func=lambda m: m.text in ['🎁 Выдать', '❌ Отозвать'])
def admin_sub(message):
    if message.from_user.id not in ADMIN_IDS: return
    action = 'give' if 'Выдать' in message.text else 'remove'
    admin_waiting[message.from_user.id] = action
    bot.send_message(message.chat.id, f"🆔 ID для {'выдачи' if action=='give' else 'отзыва'}:")

# ========== АДМИН: ПРОМОКОД ==========
@bot.message_handler(func=lambda m: m.text == '🎫 Промокод')
def admin_promo(message):
    if message.from_user.id not in ADMIN_IDS: return
    promo_creating[message.from_user.id] = {'step': 'code'}
    bot.send_message(message.chat.id, "🎫 Введите код промокода:")

# ========== АДМИН: ЮЗЕР ==========
@bot.message_handler(func=lambda m: m.text == '👤 Юзер')
def admin_user_mode(message):
    if message.from_user.id not in ADMIN_IDS: return
    bot.send_message(message.chat.id, "👤 Режим пользователя", reply_markup=user_keyboard())

# ========== АДМИН: ХОСТЫ ==========
@bot.message_handler(func=lambda m: m.text == '📦 Хосты')
def admin_all_hosts(message):
    if message.from_user.id not in ADMIN_IDS: return
    scripts = get_all_scripts()
    if not scripts: return bot.send_message(message.chat.id, "Нет")
    
    text = f"📦 <b>ВСЕ ХОСТЫ ({len(scripts)})</b>\n\n"
    kb = InlineKeyboardMarkup()
    for s in scripts[:20]:
        st = "🟢" if s['status']=='running' else "🔴"
        text += f"{st} <code>{s['id']}</code> | {s['name']} | u{s['user_id']}\n"
        kb.add(
            InlineKeyboardButton("⏹" if s['status']=='running' else "▶️", callback_data=f"asc:run:{s['id']}"),
            InlineKeyboardButton("📥", callback_data=f"dl:{s['id']}"),
            InlineKeyboardButton("🗑", callback_data=f"asc:del:{s['id']}")
        )
    bot.send_message(message.chat.id, text, reply_markup=kb)

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
            try:
                bot.send_message(u['user_id'], f"📢 <b>Рассылка</b>\n\n{text}")
                sent += 1
            except: pass
            time.sleep(0.05)
        bot.send_message(uid, f"✅ {sent}")
        return
    
    if uid in promo_creating:
        step = promo_creating[uid]['step']
        if step == 'code':
            promo_creating[uid]['code'] = text.upper()
            promo_creating[uid]['step'] = 'days'
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("7 дней", callback_data="pdays:7"),
                   InlineKeyboardButton("30 дней", callback_data="pdays:30"),
                   InlineKeyboardButton("90 дней", callback_data="pdays:90"))
            bot.send_message(uid, f"Код: <b>{text.upper()}</b>\n📅 Срок:", reply_markup=kb)
        elif step == 'uses':
            try:
                uses = int(text)
                data = promo_creating.pop(uid)
                create_promo(data['code'], 'pro', data['days'], uses)
                bot.send_message(uid, f"✅ Промокод <b>{data['code']}</b>\n📅 {data['days']}дн | 👥 {uses}")
            except:
                bot.send_message(uid, "❌ Число!")
        return
    
    if uid in admin_waiting:
        action = admin_waiting.pop(uid)
        
        if isinstance(action, tuple) and action[0] == 'balance':
            target_uid = action[1]
            try:
                amount = float(text)
                with get_db() as conn:
                    conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (amount, target_uid))
                user = get_user(target_uid)
                bot.send_message(uid, f"✅ Баланс user{target_uid}: {user.get('balance',0):.0f}₽")
            except:
                bot.send_message(uid, "❌ Неверная сумма")
            return
        
        if action == 'search':
            try:
                target_uid = int(text)
                user = get_user(target_uid)
                if not user: return bot.send_message(uid, "❌ Не найден")
                scripts = get_user_scripts(target_uid)
                running = len([s for s in scripts if s['status']=='running'])
                is_banned = user.get('banned', 0)
                
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📦 Хосты", callback_data=f"auser_hosts:{target_uid}"),
                       InlineKeyboardButton("📥 Скачать", callback_data=f"auser_dl:{target_uid}"))
                kb.add(InlineKeyboardButton("🎁 Выдать", callback_data=f"auser_give:{target_uid}"),
                       InlineKeyboardButton("❌ Отозвать", callback_data=f"auser_remove:{target_uid}"))
                kb.add(InlineKeyboardButton("💰 Баланс", callback_data=f"auser_bal:{target_uid}"))
                kb.add(InlineKeyboardButton("🟢 Разбанить" if is_banned else "🚫 Забанить", 
                                           callback_data=f"auser_unban:{target_uid}" if is_banned else f"auser_ban:{target_uid}"))
                kb.add(InlineKeyboardButton("🗑 Удалить хосты", callback_data=f"auser_delall:{target_uid}"))
                
                bot.send_message(uid, f"👤 user{target_uid}\n📦 {len(scripts)} (🟢{running})" + ("\n🚫" if is_banned else ""), reply_markup=kb)
            except:
                bot.send_message(uid, "❌ Неверный ID")
            return
        
        try:
            target_uid = int(text)
        except:
            return bot.send_message(uid, "❌ Неверный ID")
        
        if action == 'give':
            kb = InlineKeyboardMarkup()
            for tid, t in TIER_INFO.items():
                kb.add(InlineKeyboardButton(f"{t['name']}", callback_data=f"agive:{target_uid}:{tid}"))
            bot.send_message(uid, f"👤 {target_uid}\nТариф:", reply_markup=kb)
        elif action == 'remove':
            remove_subscription(target_uid)
            bot.send_message(uid, f"✅ Отозвано у {target_uid}")
        return
    
    if text.isalnum() and len(text) >= 3:
        success, msg = activate_promo(uid, text.upper())
        if success:
            bot.send_message(uid, f"✅ {msg}")

# ========== ФОТО ==========
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    for aid in ADMIN_IDS:
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("✅ +100₽", callback_data=f"app_bal|{uid}|100"),
            InlineKeyboardButton("✅ +200₽", callback_data=f"app_bal|{uid}|200"),
            InlineKeyboardButton("✅ +500₽", callback_data=f"app_bal|{uid}|500")
        )
        kb.add(InlineKeyboardButton("❌ Отклонить", callback_data=f"rej|{uid}"))
        try:
            bot.send_photo(aid, message.photo[-1].file_id, caption=f"📸 Оплата от {uid}", reply_markup=kb)
        except: pass
    bot.send_message(uid, "✅ Скриншот отправлен!")

# ========== CALLBACKS ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid = call.from_user.id
    data = call.data
    
    if data == "upload":
        upload_start(call.message)
    elif data == "hosts":
        show_hosts(call.message)
    elif data == "shop":
        shop(call.message)
    elif data == "promo":
        bot.send_message(uid, "🎁 Отправьте промокод:")
    elif data == "bal":
        balance(call.message)
    elif data == "chat":
        support_chat[uid] = True
        bot.send_message(uid, "💬 Отправьте сообщение:")
    elif data == "dep_card":
        bot.send_message(uid, "💳 СБЕР: <code>2202206714879132</code>\n📸 Отправьте скриншот")
    elif data == "search_user":
        admin_waiting[uid] = 'search'
        bot.send_message(uid, "🔍 ID:")
    
    elif data.startswith("sc:run:"):
        sid = data.split(":")[2]
        s = get_script(sid)
        if s and s['user_id'] == uid:
            if s['status'] == 'running':
                if s.get('pid'): kill_process(s['pid'])
                update_script_status(sid, 'stopped')
            else:
                pid = run_script(s['path'])
                if pid:
                    update_script_status(sid, 'running', str(pid))
            show_hosts(call.message)
    
    elif data.startswith("sc:del:"):
        sid = data.split(":")[2]
        s = get_script(sid)
        if s and s['user_id'] == uid:
            if s.get('pid'): kill_process(s['pid'])
            if s.get('original_file') and os.path.exists(s['original_file']):
                os.unlink(s['original_file'])
            delete_script(sid, uid)
            shutil.rmtree(s['path'], ignore_errors=True)
            show_hosts(call.message)
    
    elif data.startswith("dl:"):
        sid = data.split(":")[1]
        s = get_script(sid)
        if s:
            if s.get('original_file') and os.path.exists(s['original_file']):
                with open(s['original_file'], 'rb') as f:
                    bot.send_document(uid, f, caption=f"📄 {s['name']}")
            else:
                zp = TEMP_DIR / f"{sid}.zip"
                with zipfile.ZipFile(zp, 'w') as zf:
                    for f in Path(s['path']).rglob('*'):
                        if f.is_file(): zf.write(f, f.relative_to(s['path']))
                with open(zp, 'rb') as f:
                    bot.send_document(uid, f, caption=f"📦 {s['name']}")
                zp.unlink()
    
    elif data.startswith("tier:"):
        tier = data.split(":")[1]
        kb = InlineKeyboardMarkup()
        for d, n in DAYS_NAMES.items():
            kb.add(InlineKeyboardButton(f"{n} - {calc_price(tier, d)}₽", callback_data=f"days:{tier}:{d}"))
        kb.add(InlineKeyboardButton("◀️ Назад", callback_data="shop"))
        
        t = TIER_INFO[tier]
        bot.edit_message_text(
            f"{t['name']}\n├ CPU: {t['cpu']}\n├ RAM: {t['ram']}\n├ Storage: {t['storage']}\n└ Скриптов: {t['scripts']}\n\n📅 Срок:",
            uid, call.message.message_id, reply_markup=kb)
    
    elif data.startswith("days:"):
        _, tier, days = data.split(":")
        total = calc_price(tier, days)
        user = get_user(uid)
        bal = user.get('balance', 0) if user else 0
        
        kb = InlineKeyboardMarkup()
        if bal >= total:
            kb.add(InlineKeyboardButton(f"✅ Оплатить {total}₽", callback_data=f"pay:{tier}:{days}"))
        else:
            kb.add(InlineKeyboardButton(f"❌ {total}₽ (есть {bal}₽)", callback_data="bal"))
        kb.add(InlineKeyboardButton("💳 Пополнить", callback_data="bal"))
        
        bot.edit_message_text(
            f"🧾 <b>ЗАКАЗ</b>\n\n{TIER_INFO[tier]['name']}\n📅 {DAYS_NAMES[days]}\n💰 {total}₽\n💳 Баланс: {bal}₽",
            uid, call.message.message_id, reply_markup=kb)
    
    elif data.startswith("pay:"):
        _, tier, days = data.split(":")
        total = calc_price(tier, days)
        user = get_user(uid)
        
        if user.get('balance', 0) < total:
            return bot.answer_callback_query(call.id, "❌ Мало средств")
        
        with get_db() as conn:
            conn.execute('UPDATE users SET balance=balance-? WHERE user_id=?', (total, uid))
        
        d = 7 if days=='7' else 30 if days=='30' else 90
        set_subscription(uid, 'pro', d, tier)
        bot.edit_message_text(f"✅ <b>Куплено!</b>\n{TIER_INFO[tier]['name']}\n📅 {DAYS_NAMES[days]}\n💰 {total}₽",
                            uid, call.message.message_id)
    
    elif data.startswith("asc:run:"):
        if uid not in ADMIN_IDS: return
        sid = data.split(":")[2]
        s = get_script(sid)
        if s:
            if s['status'] == 'running':
                if s.get('pid'): kill_process(s['pid'])
                update_script_status(sid, 'stopped')
            else:
                pid = run_script(s['path'])
                if pid: update_script_status(sid, 'running', str(pid))
            admin_all_hosts(call.message)
    
    elif data.startswith("asc:del:"):
        if uid not in ADMIN_IDS: return
        sid = data.split(":")[2]
        s = get_script(sid)
        if s:
            if s.get('pid'): kill_process(s['pid'])
            delete_script(sid)
            shutil.rmtree(s['path'], ignore_errors=True)
            admin_all_hosts(call.message)
    
    elif data.startswith("auser:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        user = get_user(target)
        if not user: return bot.answer_callback_query(call.id, "❌")
        
        scripts = get_user_scripts(target)
        running = len([s for s in scripts if s['status']=='running'])
        sub_info, days = get_subscription_info(target)
        is_banned = user.get('banned', 0)
        
        text = f"👤 <b>user{target}</b>\n@{user.get('username','Нет')}\n💰 {user.get('balance',0):.0f}₽\n📋 {sub_info}\n📦 {len(scripts)} (🟢{running})"
        if days: text += f"\n⏳ {days}дн"
        if is_banned: text += "\n🚫 ЗАБАНЕН"
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📦 Хосты", callback_data=f"auser_hosts:{target}"),
               InlineKeyboardButton("📥 Скачать", callback_data=f"auser_dl:{target}"))
        kb.add(InlineKeyboardButton("🎁 Выдать", callback_data=f"auser_give:{target}"),
               InlineKeyboardButton("❌ Отозвать", callback_data=f"auser_remove:{target}"))
        kb.add(InlineKeyboardButton("💰 Баланс", callback_data=f"auser_bal:{target}"))
        kb.add(InlineKeyboardButton("🟢 Разбанить" if is_banned else "🚫 Забанить", 
                                   callback_data=f"auser_unban:{target}" if is_banned else f"auser_ban:{target}"))
        kb.add(InlineKeyboardButton("🗑 Удалить хосты", callback_data=f"auser_delall:{target}"))
        
        bot.edit_message_text(text, uid, call.message.message_id, reply_markup=kb)
    
    elif data.startswith("auser_hosts:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        scripts = get_user_scripts(target)
        if not scripts: return bot.answer_callback_query(call.id, "Нет")
        
        text = f"📦 <b>Хосты user{target}</b>\n\n"
        kb = InlineKeyboardMarkup()
        for s in scripts:
            st = "🟢" if s['status']=='running' else "🔴"
            text += f"{st} <b>{s['name']}</b>\n"
            kb.add(InlineKeyboardButton("⏹" if s['status']=='running' else "▶️", callback_data=f"asc:run:{s['id']}"),
                   InlineKeyboardButton("📥", callback_data=f"dl:{s['id']}"),
                   InlineKeyboardButton("🗑", callback_data=f"asc:del:{s['id']}"))
        kb.add(InlineKeyboardButton("◀️", callback_data=f"auser:{target}"))
        bot.edit_message_text(text, uid, call.message.message_id, reply_markup=kb)
    
    elif data.startswith("auser_dl:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        scripts = get_user_scripts(target)
        if not scripts: return bot.answer_callback_query(call.id, "Нет")
        
        zp = TEMP_DIR / f"user_{target}.zip"
        with zipfile.ZipFile(zp, 'w') as zf:
            for s in scripts:
                if s.get('original_file') and os.path.exists(s['original_file']):
                    zf.write(s['original_file'], s['name'])
                elif os.path.exists(s['path']):
                    for f in Path(s['path']).rglob('*'):
                        if f.is_file(): zf.write(f, f"{s['id']}/{f.relative_to(s['path'])}")
        with open(zp, 'rb') as f:
            bot.send_document(uid, f, caption=f"📦 user{target}")
        zp.unlink()
    
    elif data.startswith("auser_give:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        kb = InlineKeyboardMarkup()
        for tid, t in TIER_INFO.items():
            kb.add(InlineKeyboardButton(f"{t['name']} 30дн", callback_data=f"agive:{target}:{tid}"))
        bot.edit_message_text(f"🎁 Выдать user{target}", uid, call.message.message_id, reply_markup=kb)
    
    elif data.startswith("auser_remove:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        remove_subscription(target)
        bot.edit_message_text(f"✅ Отозвано у {target}", uid, call.message.message_id)
    
    elif data.startswith("auser_bal:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        user = get_user(target)
        admin_waiting[uid] = ('balance', target)
        bot.send_message(uid, f"💰 user{target}: {user.get('balance',0):.0f}₽\nСумма (+/-):")
    
    elif data.startswith("auser_ban:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        ban_user(target)
        for s in get_user_scripts(target):
            if s.get('pid'): kill_process(s['pid'])
            update_script_status(s['id'], 'stopped')
        bot.edit_message_text(f"🚫 user{target} забанен", uid, call.message.message_id)
    
    elif data.startswith("auser_unban:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        unban_user(target)
        bot.edit_message_text(f"🟢 user{target} разбанен", uid, call.message.message_id)
    
    elif data.startswith("auser_delall:"):
        if uid not in ADMIN_IDS: return
        target = int(data.split(":")[1])
        for s in get_user_scripts(target):
            if s.get('pid'): kill_process(s['pid'])
            if s.get('original_file') and os.path.exists(s['original_file']): os.unlink(s['original_file'])
            delete_script(s['id'])
            shutil.rmtree(s['path'], ignore_errors=True)
        bot.edit_message_text(f"✅ Хосты user{target} удалены", uid, call.message.message_id)
    
    elif data.startswith("agive:"):
        if uid not in ADMIN_IDS: return
        _, target, tier = data.split(":")
        target = int(target)
        set_subscription(target, 'pro', 30, tier)
        bot.edit_message_text(f"✅ {TIER_INFO[tier]['name']} 30дн user{target}", uid, call.message.message_id)
    
    elif data.startswith("pdays:"):
        days = int(data.split(":")[1])
        promo_creating[uid] = {'step': 'uses', 'code': promo_creating.get(uid, {}).get('code', 'PROMO'), 'days': days}
        bot.edit_message_text(f"📅 {days} дней\n👥 Макс. использований:", uid, call.message.message_id)
    
    elif data.startswith("app_bal|"):
        if uid not in ADMIN_IDS: return
        _, target, amount = data.split("|")
        target, amount = int(target), float(amount)
        with get_db() as conn:
            conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (amount, target))
        bot.edit_message_caption(f"✅ +{amount}₽", uid, call.message.message_id)
        try: bot.send_message(target, f"✅ Баланс +{amount}₽")
        except: pass
    
    elif data.startswith("rej|"):
        if uid not in ADMIN_IDS: return
        target = int(data.split("|")[1])
        bot.edit_message_caption("❌ Отклонено", uid, call.message.message_id)
        try: bot.send_message(target, "❌ Отклонено")
        except: pass
    
    bot.answer_callback_query(call.id)

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    init_db()
    threading.Thread(target=run_web_server, daemon=True).start()
    
    print(f"""
╔══════════════════════════════════════════╗
║     🚀 Hosting Bot v{VERSION}                ║
║     ✅ Запуск ботов работает            ║
║     🌐 Port: {PORT}                          ║
║     🌱 29₽ | ⚡ 49₽ | 👑 79₽            ║
╚══════════════════════════════════════════╝
    """)
    
    bot.infinity_polling()
