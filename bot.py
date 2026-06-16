import os
import sys
import time
import json
import uuid
import shutil
import zipfile
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Автоустановка библиотек
try:
    import telebot
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
except ImportError:
    os.system(f'{sys.executable} -m pip install pyTelegramBotAPI --break-system-packages')
    import telebot
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

try:
    import requests
except ImportError:
    os.system(f'{sys.executable} -m pip install requests --break-system-packages')
    import requests

VERSION = "33.0 NO-DOCKER"
TOKEN = os.getenv("BOT_TOKEN", "8964647336:AAEP1PO_NRJsGAuqWauXjf6il2mgcb2KkvM")
ADMIN_ID = int(os.getenv("ADMIN_ID", "314148464"))
CRYPTO_TOKEN = os.getenv("CRYPTO_TOKEN", "593773:AAcVRGB0bizw5hLjy0on5QmQcr6X4lHmyYX")
PORT = int(os.getenv("PORT", "10000"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
MEDIA_DIR = os.path.join(BASE_DIR, "media")
DATABASE_PATH = os.path.join(BASE_DIR, "hosting.db")

FREE_MAX_SCRIPTS = 10
FREE_MAX_SIZE_MB = 10
PREMIUM_MAX_SIZE_MB = 1024
TRIAL_DAYS = 3

PLANS = {
    '7d': {'name': '7 дней', 'days': 7, 'usdt': 1.99, 'ton': 3.0},
    '30d': {'name': '30 дней', 'days': 30, 'usdt': 4.99, 'ton': 8.0},
    '60d': {'name': '60 дней', 'days': 60, 'usdt': 7.99, 'ton': 12.0},
}

for d in [SCRIPTS_DIR, LOGS_DIR, TEMP_DIR, MEDIA_DIR]:
    os.makedirs(d, exist_ok=True)

conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def init_db():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        subscription TEXT DEFAULT 'trial', subscription_expiry TIMESTAMP, trial_start TIMESTAMP,
        referrer_id INTEGER, referral_bonus INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS scripts (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,
        main_file TEXT, pid INTEGER, status TEXT DEFAULT 'stopped',
        size INTEGER, restart_count INTEGER DEFAULT 0, total_restarts INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS promocodes (
        code TEXT PRIMARY KEY, max_uses INTEGER DEFAULT 999, used_count INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS crypto_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, payment_id TEXT UNIQUE,
        amount REAL, currency TEXT, plan TEXT, status TEXT DEFAULT 'pending')''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY AUTOINCREMENT, section TEXT, file_id TEXT, file_type TEXT,
        caption TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    for code in ['PREMIUM2024', 'ADMIN', 'MEGA', 'CRYPTO']:
        cursor.execute("INSERT OR IGNORE INTO promocodes VALUES (?, 999, 0)", (code,))
    conn.commit()

init_db()

# ========== БЕЗОПАСНОЕ РЕДАКТИРОВАНИЕ ==========
def safe_edit(chat_id, message_id, text, markup=None):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return True
    except:
        try:
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
        except:
            pass
        return False

# ========== МЕДИА ==========
def save_media(section, file_id, file_type, caption=''):
    cursor.execute("DELETE FROM media WHERE section = ?", (section,))
    cursor.execute("INSERT INTO media (section, file_id, file_type, caption) VALUES (?,?,?,?)",
                   (section, file_id, file_type, caption))
    conn.commit()

def get_media(section):
    cursor.execute("SELECT * FROM media WHERE section = ?", (section,))
    row = cursor.fetchone()
    return dict(row) if row else None

def try_send_media(chat_id, section, text, markup=None):
    media = get_media(section)
    if not media:
        return False
    try:
        if media['file_type'] == 'photo':
            bot.send_photo(chat_id, media['file_id'], caption=text, reply_markup=markup, parse_mode='Markdown')
            return True
        elif media['file_type'] == 'video':
            bot.send_video(chat_id, media['file_id'], caption=text, reply_markup=markup, parse_mode='Markdown')
            return True
        elif media['file_type'] == 'animation':
            bot.send_animation(chat_id, media['file_id'], caption=text, reply_markup=markup, parse_mode='Markdown')
            return True
    except:
        pass
    return False

# ========== ФУНКЦИИ БД ==========
def get_user(user_id):
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    return dict(row) if row else None

def create_user(user_id, username, first_name='', referrer_id=None):
    trial_start = datetime.now()
    trial_end = trial_start + timedelta(days=TRIAL_DAYS)
    try:
        cursor.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?,?)',
                      (user_id, username, first_name, 'trial', trial_end, trial_start, referrer_id, 0))
        conn.commit()
    except: pass

def is_premium(user_id):
    if user_id == ADMIN_ID: return True
    u = get_user(user_id)
    return u and u['subscription'] == 'premium'

def get_days_left(user_id):
    u = get_user(user_id)
    if not u: return 0
    if u['subscription'] == 'trial':
        ts = u.get('trial_start')
        if ts:
            try: return max(0, TRIAL_DAYS - (datetime.now() - datetime.fromisoformat(str(ts))).days)
            except: pass
    if u['subscription'] == 'premium':
        exp = u.get('subscription_expiry')
        if exp:
            try: return max(0, (datetime.fromisoformat(str(exp)) - datetime.now()).days)
            except: pass
        return 999
    return 0

def activate_premium(user_id, days):
    exp = datetime.now() + timedelta(days=days)
    cursor.execute("UPDATE users SET subscription = 'premium', subscription_expiry = ? WHERE user_id = ?", (exp, user_id))
    conn.commit()

def get_script(script_id):
    cursor.execute('SELECT * FROM scripts WHERE id = ?', (script_id,))
    row = cursor.fetchone()
    return dict(row) if row else None

def get_all_scripts():
    cursor.execute('SELECT * FROM scripts ORDER BY created_at DESC')
    return [dict(row) for row in cursor.fetchall()]

def get_user_scripts(user_id):
    cursor.execute('SELECT * FROM scripts WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    return [dict(row) for row in cursor.fetchall()]

def add_script(script_id, user_id, name, path, size, main_file=None):
    cursor.execute('INSERT INTO scripts (id, user_id, name, path, size, main_file) VALUES (?,?,?,?,?,?)',
                   (script_id, user_id, name, path, size, main_file))
    conn.commit()

def update_script_status(script_id, status, pid=None):
    if pid: cursor.execute('UPDATE scripts SET status = ?, pid = ? WHERE id = ?', (status, pid, script_id))
    else: cursor.execute('UPDATE scripts SET status = ? WHERE id = ?', (status, script_id))
    conn.commit()

def count_user_scripts(user_id):
    cursor.execute('SELECT COUNT(*) as cnt FROM scripts WHERE user_id = ?', (user_id,))
    return cursor.fetchone()['cnt']

def get_all_running_scripts():
    cursor.execute("SELECT * FROM scripts WHERE status = 'running'")
    return [dict(row) for row in cursor.fetchall()]

def get_referral_count(user_id):
    cursor.execute('SELECT COUNT(*) as cnt FROM users WHERE referrer_id = ?', (user_id,))
    return cursor.fetchone()['cnt']

def find_py_files(folder):
    py_files = []
    if os.path.exists(folder):
        for root, dirs, files in os.walk(folder):
            for file in files:
                if file.endswith('.py'): py_files.append(os.path.join(root, file))
    return py_files

def run_script(script_id, script_path):
    log_path = os.path.join(LOGS_DIR, f"{script_id}.log")
    try:
        with open(log_path, 'a') as f:
            f.write(f"\n🚀 {datetime.now()}\n{'='*40}\n")
            p = subprocess.Popen([sys.executable, script_path], stdout=f, stderr=subprocess.STDOUT, cwd=os.path.dirname(script_path))
        return p.pid, None
    except Exception as e:
        return None, str(e)

def stop_script(pid):
    try: os.kill(pid, 9); return True
    except: return False

def is_process_alive(pid):
    try: os.kill(pid, 0); return True
    except: return False

def format_size(s):
    if s < 1024: return f"{s} B"
    elif s < 1024**2: return f"{s/1024:.1f} KB"
    elif s < 1024**3: return f"{s/1024**2:.1f} MB"
    return f"{s/1024**3:.1f} GB"

def check_user_limits(user_id):
    if is_premium(user_id): return True
    return count_user_scripts(user_id) < FREE_MAX_SCRIPTS

def create_crypto_invoice(user_id, amount, currency, plan_name):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN, "Content-Type": "application/json"}
    data = {"asset": currency.upper(), "amount": str(amount), "description": f"Hosting Premium - {plan_name}"}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        result = resp.json()
        if result.get("ok"):
            inv = result["result"]
            cursor.execute("INSERT INTO crypto_payments (user_id, payment_id, amount, currency, plan) VALUES (?,?,?,?,?)",
                         (user_id, inv["invoice_id"], amount, currency.upper(), plan_name))
            conn.commit()
            return inv
    except: pass
    return None

def check_crypto_payment(payment_id):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json={"invoice_ids": str(payment_id)}, headers=headers, timeout=30)
        result = resp.json()
        if result.get("ok") and result["result"]["items"]:
            return result["result"]["items"][0]
    except: pass
    return None

bot = telebot.TeleBot(TOKEN)
upload_states = {}
admin_media_state = {}

def get_main_menu(user_id=None):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("📱 Мои скрипты"), KeyboardButton("📤 Загрузить"))
    markup.add(KeyboardButton("💎 Премиум"), KeyboardButton("👤 Профиль"))
    markup.add(KeyboardButton("👥 Рефералы"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("👑 Админ"), KeyboardButton("🔍 Все скрипты"))
        markup.add(KeyboardButton("🎨 Оформление"))
    return markup

# ========== СТАРТ ==========
@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    args = message.text.split()
    ref = None
    
    if len(args) > 1 and args[1].startswith('ref'):
        try: ref = int(args[1][3:])
        except: pass
    
    fn = message.from_user.first_name or ''
    un = message.from_user.username or ''
    
    if not get_user(user_id):
        create_user(user_id, un, fn, ref)
        
        if ref and ref != user_id:
            ref_user = get_user(ref)
            if ref_user:
                ref_count = get_referral_count(ref) + 1
                bonus_minutes = (ref_count // 2) * 5
                
                if bonus_minutes > 0:
                    if ref_user['subscription'] == 'premium':
                        old_expiry = datetime.fromisoformat(str(ref_user['subscription_expiry']))
                        new_expiry = old_expiry + timedelta(minutes=bonus_minutes)
                    else:
                        new_expiry = datetime.now() + timedelta(minutes=bonus_minutes)
                    
                    cursor.execute("UPDATE users SET subscription = 'premium', subscription_expiry = ? WHERE user_id = ?", 
                                 (new_expiry, ref))
                    cursor.execute("UPDATE users SET referral_bonus = referral_bonus + ? WHERE user_id = ?", 
                                 (bonus_minutes, ref))
                    conn.commit()
                    
                    try:
                        bot.send_message(ref, 
                            f"🎁 **Новый реферал!**\n\n"
                            f"👤 @{un or 'user'}\n"
                            f"⏱️ +{bonus_minutes} мин премиума\n"
                            f"👥 Рефералов: {ref_count}",
                            parse_mode='Markdown')
                    except: pass
    
    days = get_days_left(user_id)
    if user_id == ADMIN_ID: st = "👑 Admin"
    elif is_premium(user_id): st = f"💎 Premium: {days}d"
    elif days > 0: st = f"🆓 Trial: {days}d"
    else: st = "🆓 Free"
    
    text = f"🚀 **Hosting Bot v{VERSION}**\n\n👤 {st}\n📱 Управление скриптами\n💎 Премиум подписка\n👥 Рефералы"
    
    if not try_send_media(user_id, 'welcome', text):
        bot.send_message(user_id, text, reply_markup=get_main_menu(user_id), parse_mode='Markdown')
    else:
        bot.send_message(user_id, "👇 Меню:", reply_markup=get_main_menu(user_id))

# ========== ПРОФИЛЬ ==========
@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def menu_profile(message):
    user_id = message.from_user.id
    days = get_days_left(user_id)
    if is_premium(user_id): st = f"💎 Премиум: {days} дн"
    elif days > 0: st = f"🆓 Пробный: {days} дн"
    else: st = "🆓 Бесплатный"
    scripts = count_user_scripts(user_id)
    refs = get_referral_count(user_id)
    
    text = f"👤 **Профиль**\n\n🆔 `{user_id}`\n📊 {st}\n📁 Скриптов: {scripts}/{FREE_MAX_SCRIPTS}\n👥 Рефералов: {refs}"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    
    if not try_send_media(user_id, 'profile', text, markup):
        bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')

# ========== СКРИПТЫ ==========
@bot.message_handler(func=lambda m: m.text == "📱 Мои скрипты")
def menu_scripts(message):
    user_id = message.chat.id
    scripts = get_user_scripts(user_id)
    markup = InlineKeyboardMarkup(row_width=2)
    
    if not scripts:
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
        text = "📭 Нет скриптов. Отправьте .py файл!"
        if not try_send_media(user_id, 'scripts', text, markup):
            bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')
        return
    
    for s in scripts[:20]:
        emoji = "🟢" if s['status'] == 'running' else "🔴"
        markup.add(InlineKeyboardButton(f"{emoji} {s['name'][:20]}", callback_data=f"info_{s['id']}"), 
                   InlineKeyboardButton("🗑", callback_data=f"del_{s['id']}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    
    text = "📋 **Скрипты:**"
    if not try_send_media(user_id, 'scripts', text, markup):
        bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')

# ========== ЗАГРУЗКА ==========
@bot.message_handler(func=lambda m: m.text == "📤 Загрузить")
def menu_upload(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    bot.send_message(message.chat.id, "📤 Отправьте .py файл или ZIP архив!", reply_markup=markup)

# ========== ПРЕМИУМ ==========
@bot.message_handler(func=lambda m: m.text == "💎 Премиум")
def menu_premium(message):
    user_id = message.from_user.id
    if is_premium(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
        text = f"💎 **Премиум: {get_days_left(user_id)} дн**"
        if not try_send_media(user_id, 'premium', text, markup):
            bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💵 USDT", callback_data="m_usdt"), InlineKeyboardButton("💎 TON", callback_data="m_ton"))
    markup.add(InlineKeyboardButton("🔑 Промокод", callback_data="promo"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    
    text = "💰 **Выберите валюту:**"
    if not try_send_media(user_id, 'premium', text, markup):
        bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')

# ========== РЕФЕРАЛЫ ==========
@bot.message_handler(func=lambda m: m.text == "👥 Рефералы")
def menu_ref(message):
    uid = message.from_user.id
    cnt = get_referral_count(uid)
    un = bot.get_me().username
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    
    text = f"👥 **Рефералы**\n\n🔗 `https://t.me/{un}?start=ref{uid}`\n👤 Рефералов: {cnt}\n🎁 +5 мин за каждых 2 приглашённых!"
    if not try_send_media(uid, 'referral', text, markup):
        bot.send_message(uid, text, reply_markup=markup, parse_mode='Markdown')

# ========== АДМИН ==========
@bot.message_handler(func=lambda m: m.text == "👑 Админ" and m.from_user.id == ADMIN_ID)
def menu_admin(message):
    sc = get_all_scripts()
    rn = len([s for s in sc if s['status'] == 'running'])
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🔍 Все скрипты", callback_data="adm_scr"))
    markup.add(InlineKeyboardButton("💎 Выдать премиум", callback_data="adm_prem"))
    markup.add(InlineKeyboardButton("🎨 Оформление", callback_data="adm_media"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    bot.send_message(ADMIN_ID, f"👑 **Админ**\n📁 Скриптов: {len(sc)}\n🟢 Запущено: {rn}", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "🎨 Оформление" and m.from_user.id == ADMIN_ID)
def menu_media(message):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🏠 Приветствие", callback_data="media_welcome"))
    markup.add(InlineKeyboardButton("👤 Профиль", callback_data="media_profile"))
    markup.add(InlineKeyboardButton("📱 Скрипты", callback_data="media_scripts"))
    markup.add(InlineKeyboardButton("💎 Премиум", callback_data="media_premium"))
    markup.add(InlineKeyboardButton("👥 Рефералы", callback_data="media_referral"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    bot.send_message(ADMIN_ID, "🎨 **Оформление разделов**\n\nВыберите раздел для добавления фото/видео:", reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('media_'))
def media_section(call):
    if call.from_user.id != ADMIN_ID: return bot.answer_callback_query(call.id, "❌")
    section = call.data[6:]
    admin_media_state[call.from_user.id] = section
    names = {'welcome':'🏠 Приветствие','profile':'👤 Профиль','scripts':'📱 Скрипты','premium':'💎 Премиум','referral':'👥 Рефералы'}
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🗑 Удалить медиа", callback_data=f"delmedia_{section}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="adm_media"))
    safe_edit(call.message.chat.id, call.message.message_id, f"🎨 **{names.get(section, section)}**\n\nОтправьте фото, видео или GIF.", markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delmedia_'))
def delete_media(call):
    if call.from_user.id != ADMIN_ID: return bot.answer_callback_query(call.id, "❌")
    section = call.data[9:]
    cursor.execute("DELETE FROM media WHERE section = ?", (section,))
    conn.commit()
    safe_edit(call.message.chat.id, call.message.message_id, f"✅ Медиа удалено для **{section}**")
    bot.answer_callback_query(call.id, "✅")

@bot.message_handler(content_types=['photo', 'video', 'animation'])
def handle_admin_media(message):
    if message.from_user.id != ADMIN_ID or message.from_user.id not in admin_media_state: return
    section = admin_media_state.pop(message.from_user.id)
    
    if message.content_type == 'photo':
        file_id, file_type = message.photo[-1].file_id, 'photo'
    elif message.content_type == 'video':
        file_id, file_type = message.video.file_id, 'video'
    elif message.content_type == 'animation':
        file_id, file_type = message.animation.file_id, 'animation'
    else: return
    
    save_media(section, file_id, file_type, message.caption or '')
    bot.reply_to(message, f"✅ Медиа сохранено для **{section}**!")

# ========== CALLBACKS ==========
@bot.callback_query_handler(func=lambda call: call.data == "back_main")
def back_main(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "🌟 Главное меню", reply_markup=get_main_menu(call.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "back_prem")
def back_prem(call):
    bot.answer_callback_query(call.id)
    menu_premium(call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('m_'))
def choose_plan(call):
    cur = call.data[2:]
    bot.answer_callback_query(call.id)
    markup = InlineKeyboardMarkup(row_width=1)
    for k, p in PLANS.items():
        markup.add(InlineKeyboardButton(f"{p['name']} — {p[cur]} {cur.upper()}", callback_data=f"buy_{k}_{cur}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_prem"))
    safe_edit(call.message.chat.id, call.message.message_id, f"📅 **Срок ({cur.upper()}):**", markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def create_invoice(call):
    _, k, cur = call.data.split('_')
    p = PLANS.get(k)
    if not p: return
    inv = create_crypto_invoice(call.from_user.id, p[cur], cur, p['name'])
    if inv:
        url = inv.get("bot_invoice_url", "")
        markup = InlineKeyboardMarkup()
        if url: markup.add(InlineKeyboardButton("💳 Оплатить", url=url))
        markup.add(InlineKeyboardButton("🔄 Проверить", callback_data=f"check_{inv['invoice_id']}_{p['days']}"))
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_prem"))
        bot.send_message(call.message.chat.id, f"💰 **Счёт:** {p[cur]} {cur.upper()}\n📅 {p['name']}", reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(call.message.chat.id, "❌ Ошибка создания счёта")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment(call):
    _, pid, days = call.data.split('_')
    r = check_crypto_payment(pid)
    if r and r.get("status") == "paid":
        cursor.execute("UPDATE crypto_payments SET status = 'paid' WHERE payment_id = ?", (pid,))
        conn.commit()
        activate_premium(call.from_user.id, int(days))
        safe_edit(call.message.chat.id, call.message.message_id, f"✅ **Оплачено! Премиум на {days} дн!** 🎉")
    elif r and r.get("status") == "active":
        bot.answer_callback_query(call.id, "⏳ Ожидание...")
    else:
        bot.answer_callback_query(call.id, "❌ Не оплачено")

@bot.callback_query_handler(func=lambda call: call.data == "promo")
def enter_promo(call):
    msg = bot.send_message(call.message.chat.id, "🔑 Отправьте промокод:")
    bot.register_next_step_handler(msg, lambda m: activate_premium(m.from_user.id, 30) or bot.reply_to(m, "✅ Премиум на 30 дней!"))
    bot.answer_callback_query(call.id)

# ========== АДМИН СКРИПТЫ ==========
@bot.message_handler(func=lambda m: m.text == "🔍 Все скрипты" and m.from_user.id == ADMIN_ID)
def menu_all_scripts(message):
    sc = get_all_scripts()[:30]
    if not sc: bot.reply_to(message, "📭"); return
    markup = InlineKeyboardMarkup(row_width=1)
    for s in sc:
        o = get_user(s['user_id'])
        n = o['username'] if o else s['user_id']
        markup.add(InlineKeyboardButton(f"{'🟢' if s['status']=='running' else '🔴'} {s['name'][:20]} | {n}", callback_data=f"adm_{s['id']}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
    bot.send_message(ADMIN_ID, "🔍 Скрипты:", reply_markup=markup, parse_mode='Markdown')

def show_script_info(chat_id, script, is_admin=False):
    emoji = "🟢" if script['status'] == 'running' else "🔴"
    info = f"{emoji} **{script['name']}**\n\n🆔 `{script['id']}`\n📁 {format_size(script['size'])}\n📊 {script['status']}"
    if is_admin:
        o = get_user(script['user_id'])
        info += f"\n👤 @{o['username']}" if o and o['username'] else f"\n👤 {script['user_id']}"
    markup = InlineKeyboardMarkup(row_width=2)
    if script['status'] == 'running': markup.add(InlineKeyboardButton("🛑 Стоп", callback_data=f"stop_{script['id']}"))
    else: markup.add(InlineKeyboardButton("🚀 Пуск", callback_data=f"start_{script['id']}"))
    markup.add(InlineKeyboardButton("📜 Логи", callback_data=f"log_{script['id']}"), InlineKeyboardButton("🗑", callback_data=f"del_{script['id']}"))
    if is_admin: markup.add(InlineKeyboardButton("🔙", callback_data="adm_scr"))
    bot.send_message(chat_id, info, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('info_'))
def info_cb(call):
    s = get_script(call.data[5:])
    if s and (s['user_id'] == call.from_user.id or call.from_user.id == ADMIN_ID):
        bot.answer_callback_query(call.id); show_script_info(call.message.chat.id, s)
    else: bot.answer_callback_query(call.id, "❌")

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_') and call.data not in ['adm_scr','adm_prem','adm_media'])
def adm_cb(call):
    s = get_script(call.data[4:])
    if s and call.from_user.id == ADMIN_ID:
        bot.answer_callback_query(call.id); show_script_info(ADMIN_ID, s, True)
    else: bot.answer_callback_query(call.id, "❌")

@bot.callback_query_handler(func=lambda call: call.data == "adm_scr")
def adm_scr_cb(call): bot.answer_callback_query(call.id); menu_all_scripts(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "adm_prem")
def adm_prem_cb(call):
    msg = bot.send_message(ADMIN_ID, "📝 ID и дни:")
    bot.register_next_step_handler(msg, lambda m: activate_premium(int(m.text.split()[0]), int(m.text.split()[1]) if len(m.text.split())>1 else 30) or bot.reply_to(m,"✅"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "adm_media")
def adm_media_cb(call): bot.answer_callback_query(call.id); menu_media(call.message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('start_'))
def start_cb(call):
    s = get_script(call.data[6:])
    if not s: return bot.answer_callback_query(call.id, "❌")
    mp = os.path.join(s['path'], s['main_file']) if s.get('main_file') else (find_py_files(s['path'])[0] if find_py_files(s['path']) else None)
    if mp and os.path.exists(mp):
        pid, _ = run_script(call.data[6:], mp)
        if pid: update_script_status(call.data[6:], 'running', pid); bot.answer_callback_query(call.id, "✅")
    else: bot.answer_callback_query(call.id, "❌")

@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_'))
def stop_cb(call):
    s = get_script(call.data[5:])
    if s and s.get('pid'): stop_script(s['pid']); update_script_status(call.data[5:], 'stopped'); bot.answer_callback_query(call.id, "✅")
    else: bot.answer_callback_query(call.id, "❌")

@bot.callback_query_handler(func=lambda call: call.data.startswith('log_'))
def log_cb(call):
    lp = os.path.join(LOGS_DIR, f"{call.data[4:]}.log")
    if os.path.exists(lp):
        with open(lp) as f: c = f.read()[-4000:]
        bot.send_message(call.message.chat.id, f"📜\n```\n{c}\n```", parse_mode='Markdown') if c.strip() else bot.send_message(call.message.chat.id, "📜 Пусто")
    else: bot.send_message(call.message.chat.id, "📜 Нет")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_'))
def del_cb(call):
    s = get_script(call.data[4:])
    if s:
        if s.get('pid'): stop_script(s['pid'])
        if os.path.exists(s['path']): shutil.rmtree(s['path'], ignore_errors=True)
        lp = os.path.join(LOGS_DIR, f"{call.data[4:]}.log")
        if os.path.exists(lp): os.remove(lp)
        cursor.execute('DELETE FROM scripts WHERE id = ?', (call.data[4:],))
        conn.commit()
        bot.answer_callback_query(call.id, "✅")
        try: bot.edit_message_text("🗑", call.message.chat.id, call.message.message_id)
        except: pass

# ========== ЗАГРУЗКА ФАЙЛОВ ==========
@bot.message_handler(content_types=['document'])
def handle_doc(message):
    uid = message.from_user.id
    if not get_user(uid): create_user(uid, message.from_user.username)
    if not check_user_limits(uid): bot.reply_to(message, "❌ Лимит!"); return
    fi = bot.get_file(message.document.file_id)
    fn = message.document.file_name
    fs = message.document.file_size
    mx = PREMIUM_MAX_SIZE_MB if is_premium(uid) else FREE_MAX_SIZE_MB
    if fs > mx*1024*1024: bot.reply_to(message, f"❌ Макс {mx} МБ!"); return
    td = os.path.join(TEMP_DIR, str(uid))
    os.makedirs(td, exist_ok=True)
    tp = os.path.join(td, fn)
    msg = bot.reply_to(message, "⏳")
    try:
        dl = bot.download_file(fi.file_path)
        with open(tp, 'wb') as f: f.write(dl)
    except: bot.edit_message_text("❌", uid, msg.message_id); return
    sid = str(uuid.uuid4())[:8]
    upload_states[uid] = {'script_id': sid, 'temp_path': tp, 'file_name': fn, 'file_size': fs, 'msg_id': msg.message_id}
    if fn.lower().endswith('.zip'):
        et = os.path.join(TEMP_DIR, str(uid), sid)
        os.makedirs(et, exist_ok=True)
        try:
            with zipfile.ZipFile(tp) as zf: zf.extractall(et)
        except: bot.edit_message_text("❌ ZIP", uid, msg.message_id); return
        py = find_py_files(et)
        if not py: bot.edit_message_text("❌ Нет .py", uid, msg.message_id); return
        upload_states[uid].update({'step': 'select', 'extract_to': et, 'py_files': py})
        markup = InlineKeyboardMarkup(row_width=1)
        for pf in py[:10]:
            rel = os.path.relpath(pf, et)
            markup.add(InlineKeyboardButton(rel, callback_data=f"sel_{rel}"))
        bot.edit_message_text("📁 Главный файл:", uid, msg.message_id, reply_markup=markup)
    else:
        proceed_with_script(uid, tp, fn)
        bot.edit_message_text(f"✅ {fn}", uid, msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('sel_'))
def sel_cb(call):
    uid = call.from_user.id
    if uid not in upload_states: return bot.answer_callback_query(call.id, "❌")
    st = upload_states[uid]
    st['selected_main'] = call.data[4:]
    fp = os.path.join(st['extract_to'], call.data[4:])
    bot.edit_message_text(f"✅", uid, st['msg_id'])
    bot.answer_callback_query(call.id)
    proceed_with_script(uid, fp, st['file_name'])

def proceed_with_script(uid, sp, fn):
    st = upload_states.get(uid)
    sid = st['script_id']
    ud = os.path.join(SCRIPTS_DIR, str(uid), sid)
    os.makedirs(ud, exist_ok=True)
    mf = None
    if st.get('extract_to'):
        for item in os.listdir(st['extract_to']):
            s = os.path.join(st['extract_to'], item); d = os.path.join(ud, item)
            if os.path.isdir(s): shutil.copytree(s, d, dirs_exist_ok=True)
            else: shutil.copy2(s, d)
        mf = st.get('selected_main')
        mp = os.path.join(ud, mf) if mf else sp
    else:
        dest = os.path.join(ud, fn); shutil.move(sp, dest)
        mp = dest; mf = fn
    add_script(sid, uid, fn, ud, st['file_size'], mf)
    pid, err = run_script(sid, mp)
    if pid:
        update_script_status(sid, 'running', pid)
        bot.send_message(uid, f"✅ **Запущен!**\n📄 {fn}\n🆔 `{sid}`", parse_mode='Markdown')
    else: bot.send_message(uid, f"❌ {err}")
    try:
        if os.path.exists(st['temp_path']): os.remove(st['temp_path'])
        if st.get('extract_to') and os.path.exists(st['extract_to']): shutil.rmtree(st['extract_to'], ignore_errors=True)
    except: pass

def monitor():
    while True:
        try:
            for s in get_all_running_scripts():
                if s.get('pid') and not is_process_alive(s['pid']):
                    mp = os.path.join(s['path'], s['main_file']) if s.get('main_file') else (find_py_files(s['path'])[0] if find_py_files(s['path']) else None)
                    if mp and os.path.exists(mp) and s['restart_count'] < 3:
                        time.sleep(5)
                        pid, _ = run_script(s['id'], mp)
                        if pid: update_script_status(s['id'], 'running', pid)
                    else: update_script_status(s['id'], 'stopped')
        except: pass
        time.sleep(10)

# ========== HEALTH CHECK ==========
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthCheck(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, format, *args): pass

def run_health():
    print(f"💚 Health: http://0.0.0.0:{PORT}")
    HTTPServer(('0.0.0.0', PORT), HealthCheck).serve_forever()

if __name__ == '__main__':
    print(f"🚀 HOSTING v{VERSION} | Python 3")
    print(f"⏱️ Trial: {TRIAL_DAYS} дня")
    print(f"👥 Рефералы: +5 мин за 2 чел")
    print(f"💚 Порт: {PORT}")
    
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=run_health, daemon=True).start()
    
    while True:
        try:
            print("✅ Бот запущен!")
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"❌ {e}")
            time.sleep(10)
