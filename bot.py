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
import urllib.parse

# Библиотеки
try:
    import telebot
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, MenuButtonWebApp
except ImportError:
    os.system(f'{sys.executable} -m pip install pyTelegramBotAPI --break-system-packages')
    import telebot
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, MenuButtonWebApp

try:
    import requests
except ImportError:
    os.system(f'{sys.executable} -m pip install requests --break-system-packages')
    import requests

# ========== КОНФИГУРАЦИЯ ==========
VERSION = "18.0 PERFECT"
TOKEN = os.getenv("BOT_TOKEN", "8964647336:AAEP1PO_NRJsGAuqWauXjf6il2mgcb2KkvM")
ADMIN_ID = int(os.getenv("ADMIN_ID", "314148464"))
CRYPTO_TOKEN = os.getenv("CRYPTO_TOKEN", "593773:AAcVRGB0bizw5hLjy0on5QmQcr6X4lHmyYX")
PORT = int(os.getenv("PORT", "10000"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
DATABASE_PATH = os.path.join(BASE_DIR, "hosting.db")

FREE_MAX_SCRIPTS = 10
FREE_MAX_SIZE_MB = 10
PREMIUM_MAX_SIZE_MB = 1024
MONITOR_INTERVAL = 10
TRIAL_DAYS = 7

PLANS = {
    '7d': {'name': '7 дней', 'days': 7, 'usdt': 1.99, 'ton': 3.0},
    '30d': {'name': '30 дней', 'days': 30, 'usdt': 4.99, 'ton': 8.0},
    '60d': {'name': '60 дней', 'days': 60, 'usdt': 7.99, 'ton': 12.0},
}

for d in [SCRIPTS_DIR, LOGS_DIR, TEMP_DIR]:
    os.makedirs(d, exist_ok=True)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def init_db():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT,
        first_name TEXT, avatar_url TEXT,
        subscription TEXT DEFAULT 'trial',
        subscription_expiry TIMESTAMP, trial_start TIMESTAMP,
        referrer_id INTEGER, referral_bonus INTEGER DEFAULT 0,
        language TEXT DEFAULT 'ru', notifications INTEGER DEFAULT 1)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS scripts (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        name TEXT NOT NULL, path TEXT NOT NULL,
        main_file TEXT, pid INTEGER, status TEXT DEFAULT 'stopped',
        size INTEGER, restart_count INTEGER DEFAULT 0,
        total_restarts INTEGER DEFAULT 0, tags TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS promocodes (
        code TEXT PRIMARY KEY, max_uses INTEGER DEFAULT 999, used_count INTEGER DEFAULT 0)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS crypto_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        payment_id TEXT UNIQUE, amount REAL, currency TEXT,
        plan TEXT, status TEXT DEFAULT 'pending')''')
    
    try: cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN referral_bonus INTEGER DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru'")
    except: pass
    try: cursor.execute("ALTER TABLE users ADD COLUMN notifications INTEGER DEFAULT 1")
    except: pass
    try: cursor.execute("ALTER TABLE scripts ADD COLUMN tags TEXT")
    except: pass
    
    for code in ['PREMIUM2024', 'ADMIN', 'MEGA', 'CRYPTO', 'REFERRAL']:
        cursor.execute("INSERT OR IGNORE INTO promocodes (code, max_uses, used_count) VALUES (?, 999, 0)", (code,))
    
    conn.commit()

init_db()

# ========== ФУНКЦИИ БД ==========
def get_user(user_id):
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    return dict(row) if row else None

def update_user_info(user_id, first_name, username):
    try:
        cursor.execute("UPDATE users SET first_name = ?, username = ? WHERE user_id = ?", (first_name, username, user_id))
        conn.commit()
    except: pass

def create_user(user_id, username, first_name='', referrer_id=None):
    trial_start = datetime.now()
    trial_end = trial_start + timedelta(days=TRIAL_DAYS)
    avatar_url = f"https://ui-avatars.com/api/?name={first_name or username or 'User'}&background=667eea&color=fff&size=200"
    try:
        cursor.execute('''INSERT INTO users (user_id, username, first_name, avatar_url, subscription, subscription_expiry, trial_start, referrer_id) 
                         VALUES (?,?,?,?,?,?,?,?)''',
                      (user_id, username, first_name, avatar_url, 'trial', trial_end, trial_start, referrer_id))
        conn.commit()
        if referrer_id and referrer_id != user_id:
            referrer = get_user(referrer_id)
            if referrer:
                cursor.execute("UPDATE users SET referral_bonus = referral_bonus + 3 WHERE user_id = ?", (referrer_id,))
                if referrer['subscription'] == 'premium':
                    old_expiry = datetime.fromisoformat(str(referrer['subscription_expiry']))
                    new_expiry = old_expiry + timedelta(days=3)
                else:
                    new_expiry = datetime.now() + timedelta(days=3)
                cursor.execute("UPDATE users SET subscription = 'premium', subscription_expiry = ? WHERE user_id = ?", (new_expiry, referrer_id))
                conn.commit()
    except: pass

def is_premium(user_id):
    if user_id == ADMIN_ID: return True
    user = get_user(user_id)
    return user and user['subscription'] == 'premium'

def get_days_left(user_id):
    user = get_user(user_id)
    if not user: return 0
    if user['subscription'] == 'trial':
        ts = user.get('trial_start')
        if ts:
            try: return max(0, TRIAL_DAYS - (datetime.now() - datetime.fromisoformat(str(ts))).days)
            except: pass
    if user['subscription'] == 'premium':
        exp = user.get('subscription_expiry')
        if exp:
            try: return max(0, (datetime.fromisoformat(str(exp)) - datetime.now()).days)
            except: pass
        return 999
    return 0

def activate_premium(user_id, days):
    expiry = datetime.now() + timedelta(days=days)
    cursor.execute("UPDATE users SET subscription = 'premium', subscription_expiry = ? WHERE user_id = ?", (expiry, user_id))
    conn.commit()

def get_referral_count(user_id):
    cursor.execute('SELECT COUNT(*) as cnt FROM users WHERE referrer_id = ?', (user_id,))
    return cursor.fetchone()['cnt']

def get_referral_bonus(user_id):
    user = get_user(user_id)
    return user['referral_bonus'] if user else 0

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

def add_script(script_id, user_id, name, path, size, main_file=None, tags=''):
    cursor.execute('INSERT INTO scripts (id, user_id, name, path, size, main_file, tags) VALUES (?,?,?,?,?,?,?)',
                   (script_id, user_id, name, path, size, main_file, tags))
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

def increment_restart(script_id):
    cursor.execute('UPDATE scripts SET restart_count = restart_count + 1, total_restarts = total_restarts + 1 WHERE id = ?', (script_id,))
    conn.commit()

def reset_restart_count(script_id):
    cursor.execute('UPDATE scripts SET restart_count = 0 WHERE id = ?', (script_id,))
    conn.commit()

def check_user_limits(user_id):
    if is_premium(user_id): return True
    return count_user_scripts(user_id) < FREE_MAX_SCRIPTS

# ========== SCRIPT MANAGER ==========
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
        with open(log_path, 'a') as log_file:
            log_file.write(f"\n🚀 {datetime.now()}\n{'='*40}\n")
            process = subprocess.Popen([sys.executable, script_path], stdout=log_file, stderr=subprocess.STDOUT, cwd=os.path.dirname(script_path))
        reset_restart_count(script_id)
        return process.pid, None
    except Exception as e:
        return None, str(e)

def stop_script(pid):
    try: os.kill(pid, 9); return True
    except: return False

def is_process_alive(pid):
    try: os.kill(pid, 0); return True
    except: return False

def get_log_path(script_id):
    return os.path.join(LOGS_DIR, f"{script_id}.log")

def format_size(s):
    if s < 1024: return f"{s} B"
    elif s < 1024**2: return f"{s/1024:.1f} KB"
    elif s < 1024**3: return f"{s/1024**2:.1f} MB"
    return f"{s/1024**3:.1f} GB"

# ========== CRYPTO BOT ==========
def create_crypto_invoice(user_id, amount, currency, plan_name):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN, "Content-Type": "application/json"}
    data = {"asset": currency.upper(), "amount": str(amount), "description": f"Hosting Premium - {plan_name}",
            "payload": json.dumps({"user_id": user_id, "plan": plan_name}), "expires_in": 3600}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        result = resp.json()
        if result.get("ok"):
            invoice = result["result"]
            cursor.execute("INSERT INTO crypto_payments (user_id, payment_id, amount, currency, plan) VALUES (?,?,?,?,?)",
                         (user_id, invoice["invoice_id"], amount, currency.upper(), plan_name))
            conn.commit()
            return invoice
        return None
    except Exception as e:
        print(f"Crypto error: {e}")
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

# ========== BOT ==========
bot = telebot.TeleBot(TOKEN)
upload_states = {}

def get_host():
    return os.getenv("RENDER_EXTERNAL_HOSTNAME", f"localhost:{PORT}")

def get_main_menu(user_id=None):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("📱 Мои скрипты"), KeyboardButton("📤 Загрузить"))
    markup.add(KeyboardButton("💎 Премиум"), KeyboardButton("ℹ️ Статус"))
    markup.add(KeyboardButton("👥 Рефералы"), KeyboardButton("🌐 Язык"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("👑 Админ"), KeyboardButton("🔍 Все скрипты"))
    return markup

@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith('ref'):
        try: referrer_id = int(args[1][3:])
        except: pass
    
    first_name = message.from_user.first_name or ''
    username = message.from_user.username or ''
    
    user = get_user(user_id)
    if not user:
        create_user(user_id, username, first_name, referrer_id)
    else:
        update_user_info(user_id, first_name, username)
    
    days = get_days_left(user_id)
    if user_id == ADMIN_ID: status = "👑 Admin"
    elif is_premium(user_id): status = f"💎 Premium: {days}d"
    elif days > 0: status = f"🆓 Trial: {days}d"
    else: status = "🆓 Free"
    
    # Устанавливаем Menu Button
    host = get_host()
    try:
        bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonWebApp(
                text="🚀 Панель",
                web_app=WebAppInfo(url=f"https://{host}/panel?user_id={user_id}")
        ))
    except: pass
    
    bot.send_message(user_id, f"🚀 **Python Hosting Bot v{VERSION}**\n\n👤 {status}\n☰ Нажми кнопку меню для веб-панели", reply_markup=get_main_menu(user_id), parse_mode='Markdown')

# ========== МЕНЮ ==========
@bot.message_handler(func=lambda m: m.text == "📱 Мои скрипты")
def menu_scripts(message):
    scripts = get_user_scripts(message.from_user.id)
    if not scripts:
        bot.reply_to(message, "📭 Нет скриптов. Отправьте .py файл!")
        return
    markup = InlineKeyboardMarkup(row_width=2)
    for s in scripts[:20]:
        emoji = "🟢" if s['status'] == 'running' else "🔴"
        markup.add(InlineKeyboardButton(f"{emoji} {s['name'][:20]}", callback_data=f"info_{s['id']}"), InlineKeyboardButton("🗑", callback_data=f"delete_{s['id']}"))
    bot.send_message(message.chat.id, "📋 **Скрипты:**", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "📤 Загрузить")
def menu_upload(message):
    bot.reply_to(message, "📤 Отправьте .py файл или ZIP!")

@bot.message_handler(func=lambda m: m.text == "💎 Премиум")
def menu_premium(message):
    user_id = message.from_user.id
    if is_premium(user_id):
        days = get_days_left(user_id)
        bot.send_message(user_id, f"💎 **Премиум: {days} дн**", parse_mode='Markdown')
        return
    text = "💰 **Выберите валюту:**"
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💵 USDT", callback_data="method_usdt"), InlineKeyboardButton("💎 TON", callback_data="method_ton"))
    markup.add(InlineKeyboardButton("🔑 Промокод", callback_data="enter_promo"))
    bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "ℹ️ Статус")
def menu_status(message):
    user_id = message.from_user.id
    days = get_days_left(user_id)
    scripts = count_user_scripts(user_id)
    if is_premium(user_id): text = f"💎 **Премиум: {days} дн**"
    elif days > 0: text = f"🆓 **Пробный: {days} дн**"
    else: text = "🆓 **Бесплатный**"
    text += f"\n📁 Скриптов: {scripts}/{FREE_MAX_SCRIPTS}"
    bot.send_message(user_id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "👥 Рефералы")
def menu_referral(message):
    user_id = message.from_user.id
    ref_count = get_referral_count(user_id)
    ref_bonus = get_referral_bonus(user_id)
    bot_username = bot.get_me().username
    text = f"👥 **Рефералы**\n\n🔗 `https://t.me/{bot_username}?start=ref{user_id}`\n\n👤 Рефералов: {ref_count}\n🎁 Бонус: {ref_bonus} дн"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔗 Копировать", callback_data="copy_ref"))
    bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "copy_ref")
def copy_referral(call):
    bot_username = bot.get_me().username
    bot.send_message(call.from_user.id, f"🔗 `https://t.me/{bot_username}?start=ref{call.from_user.id}`", parse_mode='Markdown')
    bot.answer_callback_query(call.id, "✅")

@bot.message_handler(func=lambda m: m.text == "🌐 Язык")
def menu_language(message):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"))
    bot.send_message(message.chat.id, "🌐 Выберите язык:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('lang_'))
def change_language(call):
    lang = call.data[5:]
    cursor.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, call.from_user.id))
    conn.commit()
    bot.send_message(call.message.chat.id, "✅ Язык изменён!" if lang == 'ru' else "✅ Language changed!", reply_markup=get_main_menu(call.from_user.id))
    bot.answer_callback_query(call.id, "✅")

@bot.message_handler(func=lambda m: m.text == "👑 Админ" and m.from_user.id == ADMIN_ID)
def menu_admin(message):
    scripts = get_all_scripts()
    running = [s for s in scripts if s['status'] == 'running']
    text = f"👑 **Админ**\n📁 Скриптов: {len(scripts)}\n🟢 Запущено: {len(running)}"
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🔍 Все скрипты", callback_data="admin_scripts"))
    markup.add(InlineKeyboardButton("💎 Выдать премиум", callback_data="admin_premium"))
    bot.send_message(ADMIN_ID, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "🔍 Все скрипты" and m.from_user.id == ADMIN_ID)
def menu_all_scripts(message):
    scripts = get_all_scripts()
    if not scripts:
        bot.reply_to(message, "📭 Нет скриптов")
        return
    markup = InlineKeyboardMarkup(row_width=1)
    for s in scripts[:30]:
        owner = get_user(s['user_id'])
        owner_name = owner['username'] if owner and owner['username'] else str(s['user_id'])
        emoji = "🟢" if s['status'] == 'running' else "🔴"
        markup.add(InlineKeyboardButton(f"{emoji} {s['name'][:20]} | {owner_name}", callback_data=f"adm_{s['id']}"))
    bot.send_message(ADMIN_ID, "🔍 **Все скрипты:**", reply_markup=markup, parse_mode='Markdown')

# ========== ОПЛАТА ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith('method_'))
def choose_plan(call):
    currency = call.data.replace('method_', '')
    bot.answer_callback_query(call.id)
    text = f"📅 **Выберите срок ({currency.upper()}):**\n\n"
    markup = InlineKeyboardMarkup(row_width=1)
    for key, plan in PLANS.items():
        price = plan[currency]
        text += f"• {plan['name']}: {price} {currency.upper()}\n"
        markup.add(InlineKeyboardButton(f"{plan['name']} — {price} {currency.upper()}", callback_data=f"buy_{key}_{currency}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_methods"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def create_invoice(call):
    parts = call.data.split('_')
    plan = PLANS.get(parts[1])
    if not plan: bot.answer_callback_query(call.id, "❌"); return
    amount = plan[parts[2]]
    plan_name = plan['name']
    days = plan['days']
    bot.answer_callback_query(call.id, "⏳")
    invoice = create_crypto_invoice(call.from_user.id, amount, parts[2], plan_name)
    if invoice:
        pay_url = invoice.get("bot_invoice_url", "")
        invoice_id = invoice.get("invoice_id", "")
        markup = InlineKeyboardMarkup()
        if pay_url: markup.add(InlineKeyboardButton("💳 Оплатить", url=pay_url))
        markup.add(InlineKeyboardButton("🔄 Проверить", callback_data=f"check_{invoice_id}_{days}"))
        text = f"💰 **Счёт:** {amount} {parts[2].upper()}\n📅 {plan_name}\n🆔 `{invoice_id}`"
        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(call.message.chat.id, "❌ Ошибка создания счёта")

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment(call):
    parts = call.data.split('_')
    days = int(parts[2]) if len(parts) > 2 else 30
    result = check_crypto_payment(parts[1])
    if result and result.get("status") == "paid":
        cursor.execute("UPDATE crypto_payments SET status = 'paid' WHERE payment_id = ?", (parts[1],))
        conn.commit()
        activate_premium(call.from_user.id, days)
        bot.edit_message_text(f"✅ **Оплачено! Премиум на {days} дн!** 🎉", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        bot.answer_callback_query(call.id, "✅")
    elif result and result.get("status") == "active":
        bot.answer_callback_query(call.id, "⏳ Ожидание...")
    else:
        bot.answer_callback_query(call.id, "❌ Не оплачено")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_methods")
def back_to_methods(call):
    bot.answer_callback_query(call.id)
    menu_premium(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "enter_promo")
def enter_promo(call):
    msg = bot.send_message(call.message.chat.id, "🔑 Отправьте промокод:")
    bot.register_next_step_handler(msg, process_promo)
    bot.answer_callback_query(call.id)

def process_promo(message):
    code = message.text.strip()
    cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
    promo = cursor.fetchone()
    if not promo: bot.reply_to(message, "❌ Промокод не найден")
    elif promo['used_count'] >= promo['max_uses']: bot.reply_to(message, "❌ Использован")
    else:
        activate_premium(message.from_user.id, 30)
        cursor.execute('UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?', (code,))
        conn.commit()
        bot.reply_to(message, "✅ Премиум на 30 дней!")

# ========== СКРИПТЫ ==========
def show_script_info(chat_id, script, is_admin=False):
    emoji = "🟢" if script['status'] == 'running' else "🔴"
    info = f"{emoji} **{script['name']}**\n\n🆔 `{script['id']}`\n📁 {format_size(script['size'])}\n📊 {script['status']}\n🔄 Перезапусков: {script['total_restarts']}"
    if is_admin:
        owner = get_user(script['user_id'])
        info += f"\n👤 @{owner['username']}" if owner and owner['username'] else f"\n👤 ID:{script['user_id']}"
    markup = InlineKeyboardMarkup(row_width=2)
    if script['status'] == 'running': markup.add(InlineKeyboardButton("🛑 Стоп", callback_data=f"stop_{script['id']}"))
    else: markup.add(InlineKeyboardButton("🚀 Запустить", callback_data=f"start_{script['id']}"))
    markup.add(InlineKeyboardButton("📜 Логи", callback_data=f"log_{script['id']}"), InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{script['id']}"))
    if is_admin: markup.add(InlineKeyboardButton("🔙 К списку", callback_data="admin_scripts"))
    bot.send_message(chat_id, info, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('info_'))
def info_callback(call):
    script = get_script(call.data[5:])
    if not script or (script['user_id'] != call.from_user.id and call.from_user.id != ADMIN_ID):
        bot.answer_callback_query(call.id, "❌"); return
    bot.answer_callback_query(call.id)
    show_script_info(call.message.chat.id, script)

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_'))
def admin_script_callback(call):
    script = get_script(call.data[4:])
    if not script or call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌"); return
    bot.answer_callback_query(call.id)
    show_script_info(ADMIN_ID, script, is_admin=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('start_'))
def start_callback(call):
    script = get_script(call.data[6:])
    if not script: bot.answer_callback_query(call.id, "❌"); return
    main_path = os.path.join(script['path'], script['main_file']) if script.get('main_file') else (find_py_files(script['path'])[0] if find_py_files(script['path']) else None)
    if not main_path or not os.path.exists(main_path): bot.answer_callback_query(call.id, "❌ Файл не найден"); return
    pid, _ = run_script(call.data[6:], main_path)
    if pid: update_script_status(call.data[6:], 'running', pid); bot.answer_callback_query(call.id, "✅ Запущен!")
    else: bot.answer_callback_query(call.id, "❌")

@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_'))
def stop_callback(call):
    script = get_script(call.data[5:])
    if not script: bot.answer_callback_query(call.id, "❌"); return
    if script['status'] == 'running' and script.get('pid'):
        stop_script(script['pid']); update_script_status(call.data[5:], 'stopped')
        bot.answer_callback_query(call.id, "✅ Остановлен")
    else: bot.answer_callback_query(call.id, "Уже остановлен")

@bot.callback_query_handler(func=lambda call: call.data.startswith('log_'))
def log_callback(call):
    log_path = get_log_path(call.data[4:])
    if os.path.exists(log_path):
        with open(log_path, 'r') as f: content = f.read()[-4000:]
        bot.send_message(call.message.chat.id, f"📜 Логи:\n```\n{content}\n```", parse_mode='Markdown') if content.strip() else bot.send_message(call.message.chat.id, "📜 Логи пустые")
    else: bot.send_message(call.message.chat.id, "📜 Логов нет")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_'))
def delete_callback(call):
    script = get_script(call.data[7:])
    if not script: bot.answer_callback_query(call.id, "❌"); return
    if script['status'] == 'running' and script.get('pid'): stop_script(script['pid'])
    if os.path.exists(script['path']): shutil.rmtree(script['path'], ignore_errors=True)
    log_path = get_log_path(call.data[7:])
    if os.path.exists(log_path): os.remove(log_path)
    cursor.execute('DELETE FROM scripts WHERE id = ?', (call.data[7:],))
    conn.commit()
    bot.answer_callback_query(call.id, "✅")
    try: bot.edit_message_text("🗑 Удалён", call.message.chat.id, call.message.message_id)
    except: pass

@bot.callback_query_handler(func=lambda call: call.data == "admin_scripts")
def admin_scripts_list(call): bot.answer_callback_query(call.id); menu_all_scripts(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_premium")
def admin_give_premium(call):
    msg = bot.send_message(ADMIN_ID, "📝 ID и дни:\n`123456 30`")
    bot.register_next_step_handler(msg, lambda m: activate_premium(int(m.text.split()[0]), int(m.text.split()[1]) if len(m.text.split()) > 1 else 30) or bot.reply_to(m, "✅"))
    bot.answer_callback_query(call.id)

# ========== ЗАГРУЗКА ==========
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    if not get_user(user_id): create_user(user_id, message.from_user.username)
    if not check_user_limits(user_id): bot.reply_to(message, "❌ Лимит! Нужен 💎 Премиум!"); return
    
    file_info = bot.get_file(message.document.file_id)
    file_name = message.document.file_name
    file_size = message.document.file_size
    max_size = PREMIUM_MAX_SIZE_MB if is_premium(user_id) else FREE_MAX_SIZE_MB
    if file_size > max_size * 1024 * 1024: bot.reply_to(message, f"❌ Максимум {max_size} МБ!"); return
    
    temp_dir = os.path.join(TEMP_DIR, str(user_id))
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file_name)
    msg = bot.reply_to(message, "⏳ Загружаю...")
    
    try:
        downloaded = bot.download_file(file_info.file_path)
        with open(temp_path, 'wb') as f: f.write(downloaded)
    except: bot.edit_message_text("❌", user_id, msg.message_id); return
    
    script_id = str(uuid.uuid4())[:8]
    upload_states[user_id] = {'script_id': script_id, 'temp_path': temp_path, 'file_name': file_name, 'file_size': file_size, 'msg_id': msg.message_id}
    
    if file_name.lower().endswith('.zip'):
        extract_to = os.path.join(TEMP_DIR, str(user_id), script_id)
        os.makedirs(extract_to, exist_ok=True)
        try:
            with zipfile.ZipFile(temp_path) as zf: zf.extractall(extract_to)
        except: bot.edit_message_text("❌ ZIP", user_id, msg.message_id); return
        py_files = find_py_files(extract_to)
        if not py_files: bot.edit_message_text("❌ Нет .py!", user_id, msg.message_id); return
        upload_states[user_id].update({'step': 'select', 'extract_to': extract_to, 'py_files': py_files})
        markup = InlineKeyboardMarkup(row_width=1)
        for pf in py_files[:10]:
            rel = os.path.relpath(pf, extract_to)
            markup.add(InlineKeyboardButton(rel, callback_data=f"sel_{rel}"))
        bot.edit_message_text("📁 Главный файл:", user_id, msg.message_id, reply_markup=markup)
    else:
        proceed_with_script(user_id, temp_path, file_name)
        bot.edit_message_text(f"✅ {file_name}", user_id, msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('sel_'))
def select_callback(call):
    user_id = call.from_user.id
    if user_id not in upload_states: bot.answer_callback_query(call.id, "❌"); return
    rel_path = call.data[4:]
    state = upload_states[user_id]
    state['selected_main'] = rel_path
    full_path = os.path.join(state['extract_to'], rel_path)
    bot.edit_message_text(f"✅ {rel_path}", user_id, state['msg_id'])
    bot.answer_callback_query(call.id)
    proceed_with_script(user_id, full_path, state['file_name'])

def proceed_with_script(user_id, script_path, original_filename):
    state = upload_states.get(user_id)
    script_id = state['script_id']
    file_size = state['file_size']
    user_dir = os.path.join(SCRIPTS_DIR, str(user_id), script_id)
    os.makedirs(user_dir, exist_ok=True)
    main_file = None
    if state.get('extract_to'):
        for item in os.listdir(state['extract_to']):
            s = os.path.join(state['extract_to'], item); d = os.path.join(user_dir, item)
            if os.path.isdir(s): shutil.copytree(s, d, dirs_exist_ok=True)
            else: shutil.copy2(s, d)
        main_file = state.get('selected_main')
        main_path = os.path.join(user_dir, main_file) if main_file else script_path
    else:
        dest = os.path.join(user_dir, original_filename); shutil.move(script_path, dest)
        main_path = dest; main_file = original_filename
    add_script(script_id, user_id, original_filename, user_dir, file_size, main_file)
    pid, error = run_script(script_id, main_path)
    if pid:
        update_script_status(script_id, 'running', pid)
        bot.send_message(user_id, f"✅ **Запущен!**\n📄 {original_filename}\n🆔 `{script_id}`", parse_mode='Markdown')
    else: bot.send_message(user_id, f"❌ {error}")
    try:
        if os.path.exists(state['temp_path']): os.remove(state['temp_path'])
        if state.get('extract_to') and os.path.exists(state['extract_to']): shutil.rmtree(state['extract_to'], ignore_errors=True)
    except: pass

# ========== МОНИТОРИНГ ==========
def monitor():
    while True:
        try:
            for s in get_all_running_scripts():
                if s.get('pid') and not is_process_alive(s['pid']):
                    main_path = os.path.join(s['path'], s['main_file']) if s.get('main_file') else (find_py_files(s['path'])[0] if find_py_files(s['path']) else None)
                    if main_path and os.path.exists(main_path) and s['restart_count'] < 3:
                        time.sleep(5)
                        pid, err = run_script(s['id'], main_path)
                        if pid: update_script_status(s['id'], 'running', pid); increment_restart(s['id'])
                    else: update_script_status(s['id'], 'stopped')
        except: pass
        time.sleep(MONITOR_INTERVAL)

# ========== ВЕБ-ПАНЕЛЬ ==========
WEB_HTML = """<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Hosting Panel</title><style>
:root{--bg:#0f0f1a;--card:#1a1a2e;--accent:#667eea;--green:#4CAF50;--red:#f44336;--blue:#2196F3;--text:#e0e0e0;--text2:#a0a0b0;--border:#2a2a3e;--radius:16px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:16px}
.container{max-width:800px;margin:0 auto}
.card{background:var(--card);border-radius:var(--radius);padding:20px;margin-bottom:16px;border:1px solid var(--border)}
.header{display:flex;align-items:center;gap:16px}
.avatar{width:64px;height:64px;border-radius:50%;border:3px solid var(--accent)}
.user-info h1{font-size:20px}.user-info .nick{color:var(--text2);font-size:14px}
.badge{display:inline-block;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:600;margin-top:6px}
.badge.premium{background:#ffd200;color:#000}.badge.trial{background:var(--accent);color:#fff}.badge.free{background:var(--border);color:var(--text2)}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:16px}
.stat-card{background:var(--card);border-radius:var(--radius);padding:16px;text-align:center;border:1px solid var(--border)}
.stat-card .number{font-size:32px;font-weight:700;color:var(--accent)}.stat-card .label{color:var(--text2);font-size:13px}
.script-item{display:flex;justify-content:space-between;align-items:center;padding:12px;border-radius:12px;margin-bottom:6px;background:rgba(255,255,255,0.02)}
.status-dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:8px}
.status-dot.running{background:var(--green)}.status-dot.stopped{background:var(--red)}
.script-name{font-weight:600}.script-meta{font-size:12px;color:var(--text2)}
.btn{padding:8px 14px;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600;color:#fff}
.btn-start{background:var(--green)}.btn-stop{background:var(--red)}.btn-logs{background:var(--blue)}
.log-content{background:#111;border-radius:8px;padding:12px;font-family:monospace;font-size:11px;max-height:200px;overflow-y:auto;color:#0f0;margin-top:6px;display:none;white-space:pre-wrap}
.log-content.show{display:block}
.plans{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.plan-card{background:linear-gradient(135deg,var(--accent),#764ba2);color:#fff;border-radius:var(--radius);padding:16px;text-align:center}
.plan-card .price{font-size:28px;font-weight:700}.plan-card .period{opacity:0.8;font-size:13px}
.loading{text-align:center;padding:20px;color:var(--text2)}
</style></head><body><div class="container"><div class="header card" id="header"><div class="loading">Загрузка...</div></div>
<div class="stats"><div class="stat-card"><div class="number" id="t">-</div><div class="label">Скриптов</div></div>
<div class="stat-card"><div class="number" id="r">-</div><div class="label">Запущено</div></div>
<div class="stat-card"><div class="number" id="d">-</div><div class="label">Дней</div></div>
<div class="stat-card"><div class="number" id="re">-</div><div class="label">Рестартов</div></div></div>
<div class="card"><h3>📱 Скрипты</h3><div id="scriptsList"><div class="loading">Загрузка...</div></div></div>
<div class="card"><h3>💎 Тарифы</h3><div class="plans"><div class="plan-card"><div class="price">1.99$</div><div class="period">7 дней USDT</div></div>
<div class="plan-card"><div class="price">4.99$</div><div class="period">30 дней USDT</div></div>
<div class="plan-card"><div class="price">7.99$</div><div class="period">60 дней USDT</div></div>
<div class="plan-card"><div class="price">14.99$</div><div class="period">Навсегда VIP</div></div></div></div></div>
<script>
const p=new URLSearchParams(location.search);const uid=p.get('user_id');
async function api(u){const r=await fetch('/api'+u+'?user_id='+uid);return r.json()}
async function load(){
try{const d=await api('/scripts');
document.getElementById('header').innerHTML='<img src="'+(d.user.avatar||'https://ui-avatars.com/api/?name=User&background=667eea&color=fff&size=200')+'" class="avatar" onerror="this.src=\'https://ui-avatars.com/api/?name=User&background=667eea&color=fff&size=200\'"><div class="user-info"><h1>'+(d.user.name||'User')+'</h1><div class="nick">@'+(d.user.username||'unknown')+'</div><span class="badge '+d.user.subscription+'">'+(d.user.subscription==='premium'?'💎 Премиум':d.user.subscription==='trial'?'🆓 Пробный':'Бесплатный')+'</span></div>';
document.getElementById('t').textContent=d.total;
document.getElementById('r').textContent=d.running;
document.getElementById('d').textContent=d.days_left;
document.getElementById('re').textContent=d.total_restarts;
let h='';
if(!d.scripts.length)h='<div style="text-align:center;padding:20px;color:var(--text2)">📭 Нет скриптов</div>';
else d.scripts.forEach(s=>{h+='<div class="script-item"><div><span class="status-dot '+s.status+'"></span><span class="script-name">'+s.name+'</span><div class="script-meta">'+s.size+' • '+s.created+'</div></div><div>'+(s.status==='running'?'<button class="btn btn-stop" onclick="a(\'stop\',\''+s.id+'\')">Стоп</button>':'<button class="btn btn-start" onclick="a(\'start\',\''+s.id+'\')">Пуск</button>')+' <button class="btn btn-logs" onclick="l(\''+s.id+'\')">Логи</button></div></div><div class="log-content" id="log_'+s.id+'"></div>'});
document.getElementById('scriptsList').innerHTML=h}catch(e){console.error(e)}}
async function a(t,id){await api('/'+t+'&script_id='+id);load()}
async function l(id){const d=document.getElementById('log_'+id);
if(d.classList.contains('show')){d.classList.remove('show');return}
const r=await api('/logs&script_id='+id);d.textContent=r.logs||'Логов нет';d.classList.add('show')}
load();setInterval(load,15000)
</script></body></html>"""

from http.server import HTTPServer, BaseHTTPRequestHandler

class WebAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        
        if path == '/' or path == '/panel':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(WEB_HTML.encode())
            return
        
        if path.startswith('/api/'):
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            user_id = int(params.get('user_id', [0])[0])
            api_path = path.replace('/api/', '')
            
            if api_path.startswith('/scripts'):
                user = get_user(user_id)
                scripts = get_user_scripts(user_id)
                running = len([s for s in scripts if s['status'] == 'running'])
                result = {
                    'user': {
                        'name': user['first_name'] if user else 'User',
                        'username': user['username'] if user else '',
                        'avatar': user['avatar_url'] if user else '',
                        'subscription': user['subscription'] if user else 'free'
                    },
                    'total': len(scripts), 'running': running,
                    'days_left': get_days_left(user_id),
                    'total_restarts': sum(s['total_restarts'] for s in scripts),
                    'scripts': [{'id': s['id'], 'name': s['name'], 'status': s['status'],
                                 'size': format_size(s['size']), 'created': s['created_at'][:10] if s['created_at'] else ''} for s in scripts]
                }
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            
            elif 'start' in api_path:
                script_id = params.get('script_id', [''])[0]
                script = get_script(script_id)
                if script:
                    main_path = os.path.join(script['path'], script['main_file']) if script.get('main_file') else (find_py_files(script['path'])[0] if find_py_files(script['path']) else None)
                    if main_path:
                        pid, _ = run_script(script_id, main_path)
                        if pid: update_script_status(script_id, 'running', pid)
                self.wfile.write(b'{"ok":true}')
            
            elif 'stop' in api_path:
                script_id = params.get('script_id', [''])[0]
                script = get_script(script_id)
                if script and script.get('pid'):
                    stop_script(script['pid']); update_script_status(script_id, 'stopped')
                self.wfile.write(b'{"ok":true}')
            
            elif 'logs' in api_path:
                script_id = params.get('script_id', [''])[0]
                log_path = get_log_path(script_id)
                logs = ''
                if os.path.exists(log_path):
                    with open(log_path, 'r') as f: logs = f.read()[-5000:]
                self.wfile.write(json.dumps({'logs': logs}, ensure_ascii=False).encode())
            
            return
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(WEB_HTML.encode())
    
    def log_message(self, format, *args): pass

def run_web():
    print(f"🌐 Веб-панель на порту {PORT}")
    HTTPServer(('0.0.0.0', PORT), WebAPI).serve_forever()

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print(f"🚀 HOSTING v{VERSION} | Порт: {PORT}")
    
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=run_web, daemon=True).start()
    
    # Глобальный Menu Button
    host = get_host()
    try:
        bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🚀 Панель",
                web_app=WebAppInfo(url=f"https://{host}/panel")
        ))
        print("✅ Menu Button установлен!")
    except Exception as e:
        print(f"❌ Menu Button: {e}")
    
    while True:
        try:
            print("✅ Бот запущен!")
            bot.infinity_polling()
        except Exception as e:
            print(f"❌ {e}")
            time.sleep(10)
