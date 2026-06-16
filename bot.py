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

try:
    import telebot
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
except ImportError:
    os.system(f'{sys.executable} -m pip install pyTelegramBotAPI --break-system-packages')
    import telebot
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo

try:
    import requests
except ImportError:
    os.system(f'{sys.executable} -m pip install requests --break-system-packages')
    import requests

VERSION = "24.0 FIXED"
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
TRIAL_DAYS = 7

PLANS = {
    '7d': {'name': '7 дней', 'days': 7, 'usdt': 1.99, 'ton': 3.0},
    '30d': {'name': '30 дней', 'days': 30, 'usdt': 4.99, 'ton': 8.0},
    '60d': {'name': '60 дней', 'days': 60, 'usdt': 7.99, 'ton': 12.0},
}

for d in [SCRIPTS_DIR, LOGS_DIR, TEMP_DIR]:
    os.makedirs(d, exist_ok=True)

conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def init_db():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, avatar_url TEXT,
        subscription TEXT DEFAULT 'trial', subscription_expiry TIMESTAMP, trial_start TIMESTAMP,
        referrer_id INTEGER, referral_bonus INTEGER DEFAULT 0,
        language TEXT DEFAULT 'ru', notifications INTEGER DEFAULT 1)''')
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
    cursor.execute('''CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
        rating INTEGER, text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS purchase_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
        plan TEXT, amount REAL, currency TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    for code in ['PREMIUM2024', 'ADMIN', 'MEGA', 'CRYPTO']:
        cursor.execute("INSERT OR IGNORE INTO promocodes VALUES (?, 999, 0)", (code,))
    conn.commit()

init_db()

def get_user(user_id):
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    return dict(row) if row else None

def create_user(user_id, username, first_name='', referrer_id=None):
    trial_start = datetime.now()
    trial_end = trial_start + timedelta(days=TRIAL_DAYS)
    avatar_url = f"https://ui-avatars.com/api/?name={first_name or username or 'User'}&background=667eea&color=fff&size=200"
    try:
        cursor.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?)',
                      (user_id, username, first_name, avatar_url, 'trial', trial_end, trial_start, referrer_id, 0, 'ru', 1))
        conn.commit()
        if referrer_id and referrer_id != user_id:
            ref = get_user(referrer_id)
            if ref:
                cursor.execute("UPDATE users SET referral_bonus = referral_bonus + 3 WHERE user_id = ?", (referrer_id,))
                exp = datetime.now() + timedelta(days=3)
                cursor.execute("UPDATE users SET subscription = 'premium', subscription_expiry = ? WHERE user_id = ?", (exp, referrer_id))
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

def increment_restart(script_id):
    cursor.execute('UPDATE scripts SET restart_count = restart_count + 1, total_restarts = total_restarts + 1 WHERE id = ?', (script_id,))
    conn.commit()

def reset_restart_count(script_id):
    cursor.execute('UPDATE scripts SET restart_count = 0 WHERE id = ?', (script_id,))
    conn.commit()

def get_referral_count(user_id):
    cursor.execute('SELECT COUNT(*) as cnt FROM users WHERE referrer_id = ?', (user_id,))
    return cursor.fetchone()['cnt']

def add_review(user_id, username, rating, text):
    cursor.execute('INSERT INTO reviews (user_id, username, rating, text) VALUES (?,?,?,?)', (user_id, username, rating, text))
    conn.commit()

def get_reviews(limit=20):
    cursor.execute('SELECT * FROM reviews ORDER BY created_at DESC LIMIT ?', (limit,))
    return [dict(row) for row in cursor.fetchall()]

def add_purchase(user_id, username, plan, amount, currency):
    cursor.execute('INSERT INTO purchase_history (user_id, username, plan, amount, currency) VALUES (?,?,?,?,?)',
                   (user_id, username, plan, amount, currency))
    conn.commit()

def get_all_purchases(limit=20):
    cursor.execute('SELECT * FROM purchase_history ORDER BY date DESC LIMIT ?', (limit,))
    return [dict(row) for row in cursor.fetchall()]

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
        reset_restart_count(script_id)
        return p.pid, None
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

def check_user_limits(user_id):
    if is_premium(user_id): return True
    return count_user_scripts(user_id) < FREE_MAX_SCRIPTS

def create_crypto_invoice(user_id, amount, currency, plan_name):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN, "Content-Type": "application/json"}
    data = {"asset": currency.upper(), "amount": str(amount), "description": f"Hosting Premium - {plan_name}",
            "payload": json.dumps({"user_id": user_id, "plan": plan_name}), "expires_in": 3600}
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

def get_host():
    return os.getenv("RENDER_EXTERNAL_HOSTNAME", f"localhost:{PORT}")

def get_main_menu(user_id=None):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("📱 Мои скрипты"), KeyboardButton("📤 Загрузить"))
    markup.add(KeyboardButton("💎 Премиум"), KeyboardButton("👤 Профиль"))
    markup.add(KeyboardButton("👥 Рефералы"), KeyboardButton("🌐 Язык"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("👑 Админ"), KeyboardButton("🔍 Все скрипты"))
    return markup

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
    if not get_user(user_id): create_user(user_id, un, fn, ref)
    days = get_days_left(user_id)
    if user_id == ADMIN_ID: st = "👑 Admin"
    elif is_premium(user_id): st = f"💎 Premium: {days}d"
    elif days > 0: st = f"🆓 Trial: {days}d"
    else: st = "🆓 Free"
    bot.send_message(user_id, f"🚀 **Hosting Bot v{VERSION}**\n\n👤 {st}\n📱 Нажми 👤 Профиль для веб-панели", reply_markup=get_main_menu(user_id), parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def menu_profile(message):
    user_id = message.from_user.id
    host = get_host()
    url = f"https://{host}/panel?user_id={user_id}"
    
    days = get_days_left(user_id)
    if is_premium(user_id): st = f"💎 Премиум: {days} дн"
    elif days > 0: st = f"🆓 Пробный: {days} дн"
    else: st = "🆓 Бесплатный"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🌐 Открыть панель в браузере", url=url))
    
    bot.send_message(user_id, 
        f"👤 **Профиль**\n\n🆔 `{user_id}`\n📊 {st}\n📁 Скриптов: {count_user_scripts(user_id)}\n\n👇 Нажми кнопку чтобы открыть панель:",
        reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "📱 Мои скрипты")
def menu_scripts(message):
    scripts = get_user_scripts(message.from_user.id)
    if not scripts: bot.reply_to(message, "📭 Нет скриптов"); return
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
    if is_premium(user_id): bot.send_message(user_id, f"💎 **Премиум: {get_days_left(user_id)} дн**", parse_mode='Markdown'); return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💵 USDT", callback_data="m_usdt"), InlineKeyboardButton("💎 TON", callback_data="m_ton"))
    markup.add(InlineKeyboardButton("🔑 Промокод", callback_data="promo"))
    bot.send_message(user_id, "💰 **Выберите валюту:**", reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('m_'))
def choose_plan(call):
    cur = call.data[2:]
    bot.answer_callback_query(call.id)
    markup = InlineKeyboardMarkup(row_width=1)
    for k, p in PLANS.items():
        markup.add(InlineKeyboardButton(f"{p['name']} — {p[cur]} {cur.upper()}", callback_data=f"buy_{k}_{cur}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_prem"))
    bot.edit_message_text(f"📅 **Срок ({cur.upper()}):**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')

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
        bot.send_message(call.message.chat.id, f"💰 **Счёт:** {p[cur]} {cur.upper()}\n📅 {p['name']}", reply_markup=markup, parse_mode='Markdown')
    else: bot.send_message(call.message.chat.id, "❌ Ошибка")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment(call):
    _, pid, days = call.data.split('_')
    r = check_crypto_payment(pid)
    if r and r.get("status") == "paid":
        cursor.execute("UPDATE crypto_payments SET status = 'paid' WHERE payment_id = ?", (pid,))
        conn.commit()
        activate_premium(call.from_user.id, int(days))
        u = get_user(call.from_user.id)
        add_purchase(call.from_user.id, u['username'] if u else 'user', 'Premium', 0, 'USDT')
        bot.edit_message_text(f"✅ **Оплачено!** 🎉", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    elif r and r.get("status") == "active": bot.answer_callback_query(call.id, "⏳ Ожидание...")
    else: bot.answer_callback_query(call.id, "❌ Не оплачено")

@bot.callback_query_handler(func=lambda call: call.data == "back_prem")
def back_prem(call): bot.answer_callback_query(call.id); menu_premium(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "promo")
def enter_promo(call):
    msg = bot.send_message(call.message.chat.id, "🔑 Промокод:")
    bot.register_next_step_handler(msg, lambda m: activate_premium(m.from_user.id, 30) or bot.reply_to(m, "✅ 30 дн!"))
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.text == "👥 Рефералы")
def menu_ref(message):
    uid = message.from_user.id
    cnt = get_referral_count(uid)
    un = bot.get_me().username
    bot.send_message(uid, f"👥 **Рефералы**\n\n🔗 `https://t.me/{un}?start=ref{uid}`\n👤 {cnt}", parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "🌐 Язык")
def menu_lang(message):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("🇷🇺 RU", callback_data="lang_ru"), InlineKeyboardButton("🇬🇧 EN", callback_data="lang_en"))
    bot.send_message(message.chat.id, "🌐 Язык:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('lang_'))
def change_lang(call):
    cursor.execute("UPDATE users SET language = ? WHERE user_id = ?", (call.data[5:], call.from_user.id))
    conn.commit()
    bot.send_message(call.message.chat.id, "✅ Готово!", reply_markup=get_main_menu(call.from_user.id))
    bot.answer_callback_query(call.id, "✅")

@bot.message_handler(func=lambda m: m.text == "👑 Админ" and m.from_user.id == ADMIN_ID)
def menu_admin(message):
    sc = get_all_scripts()
    rn = len([s for s in sc if s['status'] == 'running'])
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🔍 Все скрипты", callback_data="adm_scr"))
    markup.add(InlineKeyboardButton("💎 Выдать премиум", callback_data="adm_prem"))
    bot.send_message(ADMIN_ID, f"👑 Админ\n📁 {len(sc)}\n🟢 {rn}", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "🔍 Все скрипты" and m.from_user.id == ADMIN_ID)
def menu_all_scripts(message):
    sc = get_all_scripts()[:30]
    if not sc: bot.reply_to(message, "📭"); return
    markup = InlineKeyboardMarkup(row_width=1)
    for s in sc:
        o = get_user(s['user_id'])
        n = o['username'] if o else s['user_id']
        markup.add(InlineKeyboardButton(f"{'🟢' if s['status']=='running' else '🔴'} {s['name'][:20]} | {n}", callback_data=f"adm_{s['id']}"))
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

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_') and call.data != 'adm_scr' and call.data != 'adm_prem')
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
    lp = get_log_path(call.data[4:])
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
        lp = get_log_path(call.data[4:])
        if os.path.exists(lp): os.remove(lp)
        cursor.execute('DELETE FROM scripts WHERE id = ?', (call.data[4:],))
        conn.commit()
        bot.answer_callback_query(call.id, "✅")
        try: bot.edit_message_text("🗑", call.message.chat.id, call.message.message_id)
        except: pass

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
                        if pid: update_script_status(s['id'], 'running', pid); increment_restart(s['id'])
                    else: update_script_status(s['id'], 'stopped')
        except: pass
        time.sleep(10)

# ========== ВЕБ-ПАНЕЛЬ (РАБОЧАЯ ВЕРСИЯ) ==========
WEB_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Hosting Panel</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
<style>
:root{--bg:#06060e;--card:#0f0f23;--accent:#667eea;--green:#34c759;--red:#ff3b30;--blue:#007aff;--gold:#ffcc00;--text:#fff;--text2:#a0a0b0;--border:#282850}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:12px}
.container{max-width:440px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px;margin-bottom:12px}
.avatar{width:48px;height:48px;border-radius:14px;background:var(--accent);display:flex;align-items:center;justify-content:center;font-weight:800;font-size:20px;color:#fff}
.header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;text-align:center}
.stat-num{font-size:28px;font-weight:800;color:var(--accent)}.stat-label{color:var(--text2);font-size:12px}
.tabs{display:flex;gap:8px;margin-bottom:12px}
.tab{padding:10px 18px;border-radius:20px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer;font-weight:600;font-size:14px}
.tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.script-row{display:flex;justify-content:space-between;align-items:center;padding:12px;border-radius:12px;background:rgba(255,255,255,0.03);margin-bottom:6px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:8px}
.dot.green{background:var(--green)}.dot.red{background:var(--red)}
.btn{padding:8px 14px;border:none;border-radius:8px;color:#fff;font-weight:600;font-size:12px;cursor:pointer}
.btn-start{background:var(--green)}.btn-stop{background:var(--red)}.btn-log{background:var(--blue)}
.logs{background:#111;border-radius:8px;padding:10px;font-family:monospace;font-size:11px;color:#0f0;max-height:150px;overflow-y:auto;display:none;margin-top:6px;white-space:pre-wrap}
.plans{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}
.plan{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;text-align:center}
.plan .price{font-size:24px;font-weight:800;color:var(--accent)}.plan .days{font-size:14px;color:var(--text2)}
.review-box{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;margin-bottom:8px}
.stars{color:var(--gold)}
textarea{width:100%;background:#111;border:1px solid var(--border);color:#fff;padding:10px;border-radius:8px;height:60px;margin-bottom:8px;font-family:inherit}
.btn-submit{width:100%;padding:12px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer}
</style>
</head>
<body>
<div class="container">
<div class="header">
<div class="avatar" id="av">A</div>
<div><div style="font-weight:700" id="un">-</div><div style="color:var(--text2);font-size:13px" id="ue">-</div></div>
</div>
<div class="stats">
<div class="stat-card"><div class="stat-num" id="ts">-</div><div class="stat-label">Скриптов</div></div>
<div class="stat-card"><div class="stat-num" id="rs">-</div><div class="stat-label">Запущено</div></div>
<div class="stat-card"><div class="stat-num" id="dl">-</div><div class="stat-label">Дней</div></div>
<div class="stat-card"><div class="stat-num" id="tr">-</div><div class="stat-label">Рестартов</div></div>
</div>
<div class="plans">
<div class="plan"><div class="price">1.99$</div><div class="days">7 дней</div></div>
<div class="plan"><div class="price">4.99$</div><div class="days">30 дней</div></div>
<div class="plan"><div class="price">7.99$</div><div class="days">60 дней</div></div>
<div class="plan"><div class="price">14.99$</div><div class="days">Навсегда</div></div>
</div>
<div class="tabs">
<button class="tab active" onclick="t('scripts')">📱 Скрипты</button>
<button class="tab" onclick="t('reviews')">⭐ Отзывы</button>
</div>
<div id="scriptsTab"><div class="card"><div id="scriptsList">Загрузка...</div></div></div>
<div id="reviewsTab" style="display:none">
<div class="card"><div id="reviewsList">Загрузка...</div></div>
<div class="card">
<h4 style="margin-bottom:8px">✍️ Оставить отзыв</h4>
<div style="display:flex;gap:4px;margin-bottom:8px;font-size:28px;cursor:pointer" id="stars">
<span onclick="r(1)">★</span><span onclick="r(2)">★</span><span onclick="r(3)">★</span><span onclick="r(4)">★</span><span onclick="r(5)">★</span>
</div>
<textarea id="revText" placeholder="Ваш отзыв..."></textarea>
<button class="btn-submit" onclick="sR()">Отправить</button>
</div>
</div>
</div>
<script>
var uid=new URLSearchParams(location.search).get('user_id');
var rating=0;
async function api(u){try{var r=await fetch('/api'+u+'?user_id='+uid);return r.json()}catch(e){return null}}
function t(tab){
document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active')});
event.target.classList.add('active');
document.getElementById('scriptsTab').style.display=tab==='scripts'?'block':'none';
document.getElementById('reviewsTab').style.display=tab==='reviews'?'block':'none';
if(tab==='reviews')L();
}
function r(n){
rating=n;
document.querySelectorAll('#stars span').forEach(function(s,i){s.style.color=i<n?'#ffcc00':'#444'});
}
async function load(){
var d=await api('/scripts');
if(!d){document.getElementById('scriptsList').innerHTML='<p style="color:var(--text2);text-align:center">Ошибка загрузки. Проверьте API.</p>';return}
document.getElementById('un').textContent=d.user.name||'User';
document.getElementById('ue').textContent='@'+(d.user.username||'unknown');
document.getElementById('av').textContent=(d.user.name||'U')[0].toUpperCase();
document.getElementById('ts').textContent=d.total;
document.getElementById('rs').textContent=d.running;
document.getElementById('dl').textContent=d.days_left;
document.getElementById('tr').textContent=d.total_restarts;
var h='';
if(!d.scripts.length)h='<p style="color:var(--text2);text-align:center">Нет скриптов</p>';
else d.scripts.forEach(function(s){
h+='<div class="script-row"><div><span class="dot '+(s.status==='running'?'green':'red')+'"></span>'+s.name+'<div style="font-size:11px;color:var(--text2)">'+s.size+'</div></div><div>'+(s.status==='running'?'<button class="btn btn-stop" onclick="a(\'stop\',\''+s.id+'\')">Стоп</button>':'<button class="btn btn-start" onclick="a(\'start\',\''+s.id+'\')">Пуск</button>')+' <button class="btn btn-log" onclick="l(\''+s.id+'\')">Логи</button></div></div><div class="logs" id="log_'+s.id+'"></div>';
});
document.getElementById('scriptsList').innerHTML=h;
}
async function a(t,id){await api('/'+t+'&script_id='+id);load()}
async function l(id){var d=document.getElementById('log_'+id);if(d.style.display==='block'){d.style.display='none';return}var r=await api('/logs&script_id='+id);d.textContent=r.logs||'Нет логов';d.style.display='block'}
async function L(){var r=await api('/reviews');var c=document.getElementById('reviewsList');if(!r||!r.reviews.length){c.innerHTML='<p style="color:var(--text2);text-align:center">Нет отзывов</p>';return}c.innerHTML=r.reviews.map(function(rv){return'<div class="review-box"><div class="stars">'+'★'.repeat(rv.rating)+'☆'.repeat(5-rv.rating)+'</div><div style="font-weight:600">@'+(rv.username||'user')+'</div><div style="color:var(--text2);font-size:13px">'+rv.text+'</div></div>'}).join('')}
async function sR(){if(!rating){alert('Выберите оценку!');return}var t=document.getElementById('revText').value.trim();if(!t){alert('Напишите отзыв!');return}await api('/add_review&rating='+rating+'&text='+encodeURIComponent(t));rating=0;document.getElementById('revText').value='';document.querySelectorAll('#stars span').forEach(function(s){s.style.color='#444'});L()}
load();setInterval(load,15000);
</script>
</body>
</html>"""

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
                    'user': {'name': user['first_name'] if user else 'User', 'username': user['username'] if user else '', 'avatar': user['avatar_url'] if user else '', 'subscription': user['subscription'] if user else 'free'},
                    'total': len(scripts), 'running': running, 'days_left': get_days_left(user_id),
                    'total_restarts': sum(s['total_restarts'] for s in scripts),
                    'scripts': [{'id': s['id'], 'name': s['name'], 'status': s['status'], 'size': format_size(s['size']), 'created': s['created_at'][:10] if s['created_at'] else ''} for s in scripts]
                }
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            
            elif 'start' in api_path:
                script_id = params.get('script_id', [''])[0]
                script = get_script(script_id)
                if script:
                    mp = os.path.join(script['path'], script['main_file']) if script.get('main_file') else (find_py_files(script['path'])[0] if find_py_files(script['path']) else None)
                    if mp: pid, _ = run_script(script_id, mp)
                    if pid: update_script_status(script_id, 'running', pid)
                self.wfile.write(b'{"ok":true}')
            
            elif 'stop' in api_path:
                script_id = params.get('script_id', [''])[0]
                script = get_script(script_id)
                if script and script.get('pid'): stop_script(script['pid']); update_script_status(script_id, 'stopped')
                self.wfile.write(b'{"ok":true}')
            
            elif 'logs' in api_path:
                script_id = params.get('script_id', [''])[0]
                log_path = get_log_path(script_id)
                logs = ''
                if os.path.exists(log_path):
                    with open(log_path, 'r') as f: logs = f.read()[-5000:]
                self.wfile.write(json.dumps({'logs': logs}, ensure_ascii=False).encode())
            
            elif 'reviews' in api_path:
                reviews = get_reviews(20)
                self.wfile.write(json.dumps({'reviews': reviews}, ensure_ascii=False).encode())
            
            elif 'add_review' in api_path:
                rating = int(params.get('rating', [5])[0])
                text = params.get('text', [''])[0]
                user = get_user(user_id)
                username = user['username'] if user else 'user'
                add_review(user_id, username, rating, text)
                self.wfile.write(b'{"ok":true}')
            
            elif 'all_history' in api_path:
                purchases = get_all_purchases(20)
                self.wfile.write(json.dumps({'purchases': purchases}, ensure_ascii=False).encode())
            
            return
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(WEB_HTML.encode())
    
    def log_message(self, format, *args): pass

def run_web():
    print(f"🌐 Панель: http://0.0.0.0:{PORT}/panel")
    HTTPServer(('0.0.0.0', PORT), WebAPI).serve_forever()

if __name__ == '__main__':
    print(f"🚀 HOSTING v{VERSION}")
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=run_web, daemon=True).start()
    while True:
        try:
            print("✅ Бот запущен!")
            bot.infinity_polling()
        except Exception as e:
            print(f"❌ {e}")
            time.sleep(10)
