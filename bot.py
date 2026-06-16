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

# ========== КОНФИГУРАЦИЯ ==========
VERSION = "15.0 WEB PANEL"
TOKEN = os.getenv("BOT_TOKEN", "8964647336:AAEP1PO_NRJsGAuqWauXjf6il2mgcb2KkvM")
ADMIN_ID = int(os.getenv("ADMIN_ID", "314148464"))
CRYPTO_TOKEN = os.getenv("CRYPTO_TOKEN", "593773:AAcVRGB0bizw5hLjy0on5QmQcr6X4lHmyYX")

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
    '7d': {'name': '7 дней / 7 days', 'days': 7, 'usdt': 1.99, 'ton': 3.0, 'btc': 0.00003},
    '30d': {'name': '30 дней / 30 days', 'days': 30, 'usdt': 4.99, 'ton': 8.0, 'btc': 0.00008},
    '60d': {'name': '60 дней / 60 days', 'days': 60, 'usdt': 7.99, 'ton': 12.0, 'btc': 0.00012},
}

CURRENCY_NAMES = {'usdt': '💵 USDT (TRC20)', 'ton': '💎 TON', 'btc': '₿ BTC'}

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

# ========== ПЕРЕВОД ==========
T = {
    'ru': {
        'welcome': '🚀 **Python Hosting Bot v{}**\n\n👤 Статус: {}\n📦 Загрузка .py / ZIP\n🔄 Автоперезапуск\n💰 Оплата криптой\n👥 Рефералы\n🌐 Веб-панель',
        'scripts': '📱 Мои скрипты', 'upload': '📤 Загрузить', 'premium': '💎 Премиум',
        'status': 'ℹ️ Статус', 'admin': '👑 Админ', 'all_scripts': '🔍 Все скрипты',
        'referral': '👥 Рефералы', 'language': '🌐 Язык', 'web': '🌐 Веб-панель',
        'no_scripts': '📭 Нет скриптов. Отправьте .py файл!',
        'script_list': '📋 **Скрипты:**',
        'premium_active': '💎 **Премиум активен**\n📅 Осталось: {} дн',
        'choose_currency': '💰 **Шаг 1: Выберите валюту:**',
        'choose_plan': '📅 **Шаг 2: Выберите срок ({}):**\n\n',
        'back': '🔙 Назад',
        'invoice_created': '💰 **Счёт создан!**\n\n📅 {}\n💎 **{} {}**\n🆔 `{}`\n\nНажмите кнопку 👇',
        'invoice_error': '❌ Ошибка создания счёта',
        'check_payment': '🔄 Проверить оплату',
        'pay_button': '💳 Оплатить',
        'payment_ok': '✅ **Оплачено!** Премиум на {} дней! 🎉',
        'payment_wait': '⏳ Ожидает оплаты...',
        'payment_no': '❌ Не оплачено',
        'enter_promo': '🔑 Отправьте промокод:',
        'promo_not_found': '❌ Промокод не найден',
        'promo_used': '❌ Промокод использован',
        'promo_ok': '✅ Премиум на 30 дней!',
        'status_premium': '💎 **Премиум: {} дн**',
        'status_trial': '🆓 **Пробный: {} дн**',
        'status_free': '🆓 **Бесплатный**',
        'referral_info': '👥 **Рефералы**\n\n🔗 Ссылка:\n`https://t.me/{}?start=ref{}`\n\n👤 Рефералов: {}\n🎁 Бонус: {} дн\n\n📋 Друг получит +7 дней, ты +3 дня!',
        'copy_ref': '🔗 Копировать ссылку',
        'ref_copied': '✅ Ссылка отправлена!',
        'lang_select': '🌐 Выберите язык:',
        'lang_changed_ru': '🇷🇺 Русский',
        'lang_changed_en': '🇬🇧 English',
        'web_panel': '🌐 **Веб-панель:**\n[Открыть панель](https://{}/panel?user_id={})',
        'help': '🆘 /start /list /promo /referral /lang /web',
        'script_started': '✅ **Запущен!**\n📄 {}\n🆔 `{}`',
        'logs_empty': '📜 Логи пустые',
        'logs_not_found': '📜 Логов нет',
        'limit_error': '❌ Лимит! Нужен 💎 Премиум!',
        'max_size_error': '❌ Максимум {} МБ!',
        'uploading': '⏳ Загружаю...',
        'uploaded': '✅ {} загружен!',
        'select_main': '📁 Главный файл:',
        'deleted': '🗑 Удалён',
        'stopped': 'Уже остановлен',
        'stopped_ok': '✅ Остановлен',
        'started_ok': '✅ Запущен!',
        'file_not_found': '❌ Файл не найден',
        'no_access': '❌ Нет доступа',
        'not_found': '❌ Не найден',
        'session_expired': '❌ Сессия устарела',
        'admin_panel': '👑 **Админ**\n📁 Скриптов: {}\n🟢 Запущено: {}',
        'give_premium': '💎 Выдать премиум',
        'give_premium_prompt': '📝 ID и дни:\n`123456 30`',
        'give_premium_ok': '✅ Премиум на {} дн для {}',
        'give_premium_error': '❌ Неверный формат',
        'notification_bot_down': '🔴 **Бот упал!**\n📄 {}\n🔄 Перезапуск...',
        'notification_restarted': '✅ **Бот перезапущен!**\n📄 {}',
        'notification_subscription': '⚠️ **Подписка заканчивается!**\n📅 Осталось: {} дн',
        'notification_trial_extended': '🎁 **Триал продлён на 3 дня!**',
    },
    'en': {
        'welcome': '🚀 **Python Hosting Bot v{}**\n\n👤 Status: {}\n📦 Upload .py / ZIP\n🔄 Auto-restart\n💰 Crypto\n👥 Referrals\n🌐 Web Panel',
        'scripts': '📱 My Scripts', 'upload': '📤 Upload', 'premium': '💎 Premium',
        'status': 'ℹ️ Status', 'admin': '👑 Admin', 'all_scripts': '🔍 All Scripts',
        'referral': '👥 Referrals', 'language': '🌐 Language', 'web': '🌐 Web Panel',
        'no_scripts': '📭 No scripts. Send .py file!',
        'script_list': '📋 **Scripts:**',
        'premium_active': '💎 **Premium Active**\n📅 Left: {} days',
        'choose_currency': '💰 **Step 1: Choose currency:**',
        'choose_plan': '📅 **Step 2: Choose period ({}):**\n\n',
        'back': '🔙 Back',
        'invoice_created': '💰 **Invoice!**\n\n📅 {}\n💎 **{} {}**\n🆔 `{}`\n\nPress button 👇',
        'invoice_error': '❌ Error creating invoice',
        'check_payment': '🔄 Check',
        'pay_button': '💳 Pay',
        'payment_ok': '✅ **Paid!** Premium for {} days! 🎉',
        'payment_wait': '⏳ Waiting...',
        'payment_no': '❌ Not paid',
        'enter_promo': '🔑 Enter promo:',
        'promo_not_found': '❌ Not found',
        'promo_used': '❌ Used',
        'promo_ok': '✅ Premium 30 days!',
        'status_premium': '💎 **Premium: {}d**',
        'status_trial': '🆓 **Trial: {}d**',
        'status_free': '🆓 **Free**',
        'referral_info': '👥 **Referrals**\n\n🔗 Link:\n`https://t.me/{}?start=ref{}`\n\n👤 Referrals: {}\n🎁 Bonus: {}d\n\n📋 Friend +7d, you +3d!',
        'copy_ref': '🔗 Copy link',
        'ref_copied': '✅ Link sent!',
        'lang_select': '🌐 Choose language:',
        'lang_changed_ru': '🇷🇺 Russian',
        'lang_changed_en': '🇬🇧 English',
        'web_panel': '🌐 **Web Panel:**\n[Open Panel](https://{}/panel?user_id={})',
        'help': '🆘 /start /list /promo /referral /lang /web',
        'script_started': '✅ **Started!**\n📄 {}\n🆔 `{}`',
        'logs_empty': '📜 Logs empty',
        'logs_not_found': '📜 No logs',
        'limit_error': '❌ Limit! Need 💎 Premium!',
        'max_size_error': '❌ Max {} MB!',
        'uploading': '⏳ Uploading...',
        'uploaded': '✅ {} uploaded!',
        'select_main': '📁 Main file:',
        'deleted': '🗑 Deleted',
        'stopped': 'Already stopped',
        'stopped_ok': '✅ Stopped',
        'started_ok': '✅ Started!',
        'file_not_found': '❌ File not found',
        'no_access': '❌ No access',
        'not_found': '❌ Not found',
        'session_expired': '❌ Session expired',
        'admin_panel': '👑 **Admin**\n📁 Scripts: {}\n🟢 Running: {}',
        'give_premium': '💎 Give premium',
        'give_premium_prompt': '📝 ID and days:\n`123456 30`',
        'give_premium_ok': '✅ Premium {}d for {}',
        'give_premium_error': '❌ Invalid format',
        'notification_bot_down': '🔴 **Bot crashed!**\n📄 {}\n🔄 Restarting...',
        'notification_restarted': '✅ **Bot restarted!**\n📄 {}',
        'notification_subscription': '⚠️ **Subscription expiring!**\n📅 Left: {}d',
        'notification_trial_extended': '🎁 **Trial +3 days!**',
    }
}

def t(user_id, key, *args):
    user = get_user(user_id)
    lang = user['language'] if user and user.get('language') else 'ru'
    text = T.get(lang, T['ru']).get(key, T['ru'].get(key, key))
    return text.format(*args) if args else text

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

def update_script_tags(script_id, tags):
    cursor.execute('UPDATE scripts SET tags = ? WHERE id = ?', (tags, script_id))
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

def get_main_menu(user_id=None):
    user = get_user(user_id) if user_id else None
    lang = user['language'] if user else 'ru'
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton(T[lang]['scripts']), KeyboardButton(T[lang]['upload']))
    markup.add(KeyboardButton(T[lang]['premium']), KeyboardButton(T[lang]['status']))
    markup.add(KeyboardButton(T[lang]['referral']), KeyboardButton(T[lang]['language']))
    markup.add(KeyboardButton(T[lang]['web']))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton(T[lang]['admin']), KeyboardButton(T[lang]['all_scripts']))
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
    
    bot.send_message(user_id, t(user_id, 'welcome', VERSION, status), reply_markup=get_main_menu(user_id), parse_mode='Markdown')

@bot.message_handler(commands=['web', 'panel'])
def cmd_web(message):
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "localhost:10000")
    bot.send_message(message.chat.id, t(message.from_user.id, 'web_panel', host, message.from_user.id), parse_mode='Markdown', disable_web_page_preview=False)

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.send_message(message.chat.id, t(message.from_user.id, 'help'), parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text in [T['ru']['scripts'], T['en']['scripts']])
def menu_scripts(message):
    scripts = get_user_scripts(message.from_user.id)
    if not scripts:
        bot.reply_to(message, t(message.from_user.id, 'no_scripts'))
        return
    markup = InlineKeyboardMarkup(row_width=2)
    for s in scripts[:20]:
        emoji = "🟢" if s['status'] == 'running' else "🔴"
        tag = f" [{s['tags']}]" if s.get('tags') else ""
        markup.add(InlineKeyboardButton(f"{emoji} {s['name'][:20]}{tag}", callback_data=f"info_{s['id']}"), InlineKeyboardButton("🗑", callback_data=f"delete_{s['id']}"))
    bot.send_message(message.chat.id, t(message.from_user.id, 'script_list'), reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text in [T['ru']['upload'], T['en']['upload']])
def menu_upload(message):
    bot.reply_to(message, "📤 Отправьте .py файл или ZIP архив!")

@bot.message_handler(func=lambda m: m.text in [T['ru']['premium'], T['en']['premium']])
def menu_premium(message):
    user_id = message.from_user.id
    if is_premium(user_id):
        days = get_days_left(user_id)
        bot.send_message(user_id, t(user_id, 'premium_active', days), parse_mode='Markdown')
        return
    text = t(user_id, 'choose_currency')
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💵 USDT", callback_data="method_usdt"), InlineKeyboardButton("💎 TON", callback_data="method_ton"))
    markup.add(InlineKeyboardButton("₿ BTC", callback_data="method_btc"), InlineKeyboardButton("🔑 Промокод", callback_data="enter_promo"))
    bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text in [T['ru']['status'], T['en']['status']])
def menu_status(message):
    user_id = message.from_user.id
    days = get_days_left(user_id)
    scripts = count_user_scripts(user_id)
    ref_count = get_referral_count(user_id)
    ref_bonus = get_referral_bonus(user_id)
    if is_premium(user_id): text = t(user_id, 'status_premium', days)
    elif days > 0: text = t(user_id, 'status_trial', days)
    else: text = t(user_id, 'status_free')
    text += f"\n📁 Скриптов: {scripts}/{FREE_MAX_SCRIPTS}\n👥 Рефералов: {ref_count} | Бонус: {ref_bonus} дн"
    bot.send_message(user_id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text in [T['ru']['referral'], T['en']['referral']])
def menu_referral(message):
    user_id = message.from_user.id
    ref_count = get_referral_count(user_id)
    ref_bonus = get_referral_bonus(user_id)
    bot_username = bot.get_me().username
    text = t(user_id, 'referral_info', bot_username, user_id, ref_count, ref_bonus)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(t(user_id, 'copy_ref'), callback_data="copy_ref"))
    bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "copy_ref")
def copy_referral(call):
    bot_username = bot.get_me().username
    ref_link = f"https://t.me/{bot_username}?start=ref{call.from_user.id}"
    bot.send_message(call.from_user.id, f"🔗 `{ref_link}`", parse_mode='Markdown')
    bot.answer_callback_query(call.id, t(call.from_user.id, 'ref_copied'))

@bot.message_handler(func=lambda m: m.text in [T['ru']['language'], T['en']['language']])
def menu_language(message):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"))
    bot.send_message(message.chat.id, t(message.from_user.id, 'lang_select'), reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('lang_'))
def change_language(call):
    lang = call.data[5:]
    cursor.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, call.from_user.id))
    conn.commit()
    bot.send_message(call.message.chat.id, t(call.from_user.id, f'lang_changed_{lang}'), reply_markup=get_main_menu(call.from_user.id))
    bot.answer_callback_query(call.id, "✅")

@bot.message_handler(func=lambda m: m.text in [T['ru']['web'], T['en']['web']])
def menu_web(message):
    cmd_web(message)

@bot.message_handler(func=lambda m: m.text in [T['ru']['admin'], T['en']['admin']] and m.from_user.id == ADMIN_ID)
def menu_admin(message):
    scripts = get_all_scripts()
    running = [s for s in scripts if s['status'] == 'running']
    text = t(ADMIN_ID, 'admin_panel', len(scripts), len(running))
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(t(ADMIN_ID, 'all_scripts'), callback_data="admin_scripts"))
    markup.add(InlineKeyboardButton(t(ADMIN_ID, 'give_premium'), callback_data="admin_premium"))
    bot.send_message(ADMIN_ID, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text in [T['ru']['all_scripts'], T['en']['all_scripts']] and m.from_user.id == ADMIN_ID)
def menu_all_scripts(message):
    scripts = get_all_scripts()
    if not scripts:
        bot.reply_to(message, t(ADMIN_ID, 'no_scripts'))
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
    currency_name = CURRENCY_NAMES.get(currency, currency.upper())
    text = t(call.from_user.id, 'choose_plan', currency_name)
    markup = InlineKeyboardMarkup(row_width=1)
    for key, plan in PLANS.items():
        price = plan[currency]
        markup.add(InlineKeyboardButton(f"📅 {plan['name']} — {price} {currency.upper()}", callback_data=f"buy_{key}_{currency}"))
    markup.add(InlineKeyboardButton(t(call.from_user.id, 'back'), callback_data="back_to_methods"))
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
        if pay_url: markup.add(InlineKeyboardButton(t(call.from_user.id, 'pay_button'), url=pay_url))
        markup.add(InlineKeyboardButton(t(call.from_user.id, 'check_payment'), callback_data=f"check_{invoice_id}_{days}"))
        text = t(call.from_user.id, 'invoice_created', plan_name, amount, parts[2].upper(), invoice_id)
        bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(call.message.chat.id, t(call.from_user.id, 'invoice_error'))

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment(call):
    parts = call.data.split('_')
    days = int(parts[2]) if len(parts) > 2 else 30
    result = check_crypto_payment(parts[1])
    if result and result.get("status") == "paid":
        cursor.execute("UPDATE crypto_payments SET status = 'paid' WHERE payment_id = ?", (parts[1],))
        conn.commit()
        activate_premium(call.from_user.id, days)
        bot.edit_message_text(t(call.from_user.id, 'payment_ok', days), call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        bot.answer_callback_query(call.id, "✅")
    elif result and result.get("status") == "active":
        bot.answer_callback_query(call.id, t(call.from_user.id, 'payment_wait'))
    else:
        bot.answer_callback_query(call.id, t(call.from_user.id, 'payment_no'))

@bot.callback_query_handler(func=lambda call: call.data == "back_to_methods")
def back_to_methods(call):
    bot.answer_callback_query(call.id)
    menu_premium(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "enter_promo")
def enter_promo(call):
    msg = bot.send_message(call.message.chat.id, t(call.from_user.id, 'enter_promo'))
    bot.register_next_step_handler(msg, process_promo)
    bot.answer_callback_query(call.id)

def process_promo(message):
    code = message.text.strip()
    cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
    promo = cursor.fetchone()
    if not promo: bot.reply_to(message, t(message.from_user.id, 'promo_not_found'))
    elif promo['used_count'] >= promo['max_uses']: bot.reply_to(message, t(message.from_user.id, 'promo_used'))
    else:
        activate_premium(message.from_user.id, 30)
        cursor.execute('UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?', (code,))
        conn.commit()
        bot.reply_to(message, t(message.from_user.id, 'promo_ok'))

# ========== СКРИПТЫ ==========
def show_script_info(chat_id, script, is_admin=False):
    user_id = chat_id
    emoji = "🟢" if script['status'] == 'running' else "🔴"
    info = f"{emoji} **{script['name']}**\n\n🆔 `{script['id']}`\n📁 {format_size(script['size'])}\n📊 {script['status']}\n🔄 Перезапусков: {script['total_restarts']}"
    if script.get('main_file'): info += f"\n📄 {script['main_file']}"
    if script.get('tags'): info += f"\n🏷️ {script['tags']}"
    if is_admin:
        owner = get_user(script['user_id'])
        info += f"\n👤 @{owner['username']}" if owner and owner['username'] else f"\n👤 ID:{script['user_id']}"
    markup = InlineKeyboardMarkup(row_width=2)
    if script['status'] == 'running': markup.add(InlineKeyboardButton("🛑 Стоп", callback_data=f"stop_{script['id']}"))
    else: markup.add(InlineKeyboardButton("🚀 Запустить", callback_data=f"start_{script['id']}"))
    markup.add(InlineKeyboardButton("📜 Логи", callback_data=f"log_{script['id']}"), InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{script['id']}"))
    markup.add(InlineKeyboardButton("🏷️ Теги", callback_data=f"tag_{script['id']}"))
    if is_admin: markup.add(InlineKeyboardButton("🔙 К списку", callback_data="admin_scripts"))
    bot.send_message(chat_id, info, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('info_'))
def info_callback(call):
    script = get_script(call.data[5:])
    if not script or (script['user_id'] != call.from_user.id and call.from_user.id != ADMIN_ID):
        bot.answer_callback_query(call.id, t(call.from_user.id, 'no_access')); return
    bot.answer_callback_query(call.id)
    show_script_info(call.message.chat.id, script)

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_'))
def admin_script_callback(call):
    script = get_script(call.data[4:])
    if not script or call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, t(call.from_user.id, 'no_access')); return
    bot.answer_callback_query(call.id)
    show_script_info(ADMIN_ID, script, is_admin=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('tag_'))
def tag_callback(call):
    script = get_script(call.data[4:])
    if not script: bot.answer_callback_query(call.id, "❌"); return
    msg = bot.send_message(call.message.chat.id, "🏷️ Отправьте теги через запятую:\nНапример: `бот, спам`")
    bot.register_next_step_handler(msg, lambda m: update_script_tags(call.data[4:], m.text.strip()) or bot.reply_to(m, f"✅ Теги: {m.text}"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('start_'))
def start_callback(call):
    script = get_script(call.data[6:])
    if not script: bot.answer_callback_query(call.id, t(call.from_user.id, 'not_found')); return
    main_path = os.path.join(script['path'], script['main_file']) if script.get('main_file') else (find_py_files(script['path'])[0] if find_py_files(script['path']) else None)
    if not main_path or not os.path.exists(main_path): bot.answer_callback_query(call.id, t(call.from_user.id, 'file_not_found')); return
    pid, _ = run_script(call.data[6:], main_path)
    if pid: update_script_status(call.data[6:], 'running', pid); bot.answer_callback_query(call.id, t(call.from_user.id, 'started_ok'))
    else: bot.answer_callback_query(call.id, "❌")

@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_'))
def stop_callback(call):
    script = get_script(call.data[5:])
    if not script: bot.answer_callback_query(call.id, t(call.from_user.id, 'not_found')); return
    if script['status'] == 'running' and script.get('pid'):
        stop_script(script['pid']); update_script_status(call.data[5:], 'stopped')
        bot.answer_callback_query(call.id, t(call.from_user.id, 'stopped_ok'))
    else: bot.answer_callback_query(call.id, t(call.from_user.id, 'stopped'))

@bot.callback_query_handler(func=lambda call: call.data.startswith('log_'))
def log_callback(call):
    log_path = get_log_path(call.data[4:])
    if os.path.exists(log_path):
        with open(log_path, 'r') as f: content = f.read()[-4000:]
        bot.send_message(call.message.chat.id, f"📜 Логи:\n```\n{content}\n```", parse_mode='Markdown') if content.strip() else bot.send_message(call.message.chat.id, t(call.from_user.id, 'logs_empty'))
    else: bot.send_message(call.message.chat.id, t(call.from_user.id, 'logs_not_found'))
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
    try: bot.edit_message_text(t(call.from_user.id, 'deleted'), call.message.chat.id, call.message.message_id)
    except: pass

@bot.callback_query_handler(func=lambda call: call.data == "admin_scripts")
def admin_scripts_list(call): bot.answer_callback_query(call.id); menu_all_scripts(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_premium")
def admin_give_premium(call):
    msg = bot.send_message(ADMIN_ID, t(ADMIN_ID, 'give_premium_prompt'))
    bot.register_next_step_handler(msg, lambda m: activate_premium(int(m.text.split()[0]), int(m.text.split()[1]) if len(m.text.split()) > 1 else 30) or bot.reply_to(m, "✅"))
    bot.answer_callback_query(call.id)

# ========== ЗАГРУЗКА ==========
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    if not get_user(user_id): create_user(user_id, message.from_user.username)
    if not check_user_limits(user_id): bot.reply_to(message, t(user_id, 'limit_error')); return
    
    file_info = bot.get_file(message.document.file_id)
    file_name = message.document.file_name
    file_size = message.document.file_size
    max_size = PREMIUM_MAX_SIZE_MB if is_premium(user_id) else FREE_MAX_SIZE_MB
    if file_size > max_size * 1024 * 1024: bot.reply_to(message, t(user_id, 'max_size_error', max_size)); return
    
    temp_dir = os.path.join(TEMP_DIR, str(user_id))
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file_name)
    msg = bot.reply_to(message, t(user_id, 'uploading'))
    
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
        bot.edit_message_text(t(user_id, 'select_main'), user_id, msg.message_id, reply_markup=markup)
    else:
        proceed_with_script(user_id, temp_path, file_name)
        bot.edit_message_text(t(user_id, 'uploaded', file_name), user_id, msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('sel_'))
def select_callback(call):
    user_id = call.from_user.id
    if user_id not in upload_states: bot.answer_callback_query(call.id, t(user_id, 'session_expired')); return
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
        bot.send_message(user_id, t(user_id, 'script_started', original_filename, script_id), parse_mode='Markdown')
    else: bot.send_message(user_id, f"❌ {error}")
    try:
        if os.path.exists(state['temp_path']): os.remove(state['temp_path'])
        if state.get('extract_to') and os.path.exists(state['extract_to']): shutil.rmtree(state['extract_to'], ignore_errors=True)
    except: pass

# ========== МОНИТОРИНГ ==========
def send_notification(user_id, text):
    try:
        user = get_user(user_id)
        if user and user.get('notifications', 1) == 1: bot.send_message(user_id, text, parse_mode='Markdown')
    except: pass

def monitor():
    last_expiry_check = datetime.now()
    while True:
        try:
            for s in get_all_running_scripts():
                if s.get('pid') and not is_process_alive(s['pid']):
                    send_notification(s['user_id'], t(s['user_id'], 'notification_bot_down', s['name']))
                    main_path = os.path.join(s['path'], s['main_file']) if s.get('main_file') else (find_py_files(s['path'])[0] if find_py_files(s['path']) else None)
                    if main_path and os.path.exists(main_path) and s['restart_count'] < 3:
                        time.sleep(5)
                        pid, err = run_script(s['id'], main_path)
                        if pid: update_script_status(s['id'], 'running', pid); increment_restart(s['id']); send_notification(s['user_id'], t(s['user_id'], 'notification_restarted', s['name']))
                    else: update_script_status(s['id'], 'stopped')
            if (datetime.now() - last_expiry_check).seconds > 3600:
                cursor.execute("SELECT * FROM users WHERE subscription IN ('premium','trial')")
                for user in cursor.fetchall():
                    user = dict(user)
                    days_left = get_days_left(user['user_id'])
                    if 0 < days_left <= 3: send_notification(user['user_id'], t(user['user_id'], 'notification_subscription', days_left))
                    if user['subscription'] == 'trial' and days_left <= 0 and count_user_scripts(user['user_id']) > 0:
                        activate_premium(user['user_id'], 3)
                        send_notification(user['user_id'], t(user['user_id'], 'notification_trial_extended'))
                last_expiry_check = datetime.now()
        except Exception as e: print(f"Monitor: {e}")
        time.sleep(MONITOR_INTERVAL)

# ========== ВЕБ-ПАНЕЛЬ ==========
WEB_PANEL_HTML = r"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Python Hosting Panel</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
    <style>
        :root {
            --bg: #0f0f1a;
            --card: #1a1a2e;
            --accent: #667eea;
            --accent2: #764ba2;
            --green: #4CAF50;
            --red: #f44336;
            --blue: #2196F3;
            --text: #e0e0e0;
            --text2: #a0a0b0;
            --border: #2a2a3e;
            --radius: 16px;
            --shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            background-image: 
                radial-gradient(ellipse at 20% 20%, rgba(102, 126, 234, 0.15) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 80%, rgba(118, 75, 162, 0.1) 0%, transparent 50%);
        }
        .container { max-width: 1000px; margin: 0 auto; padding: 20px; }
        
        /* Header */
        .header {
            background: var(--card);
            border-radius: var(--radius);
            padding: 30px;
            margin-bottom: 24px;
            box-shadow: var(--shadow);
            border: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 20px;
        }
        .avatar {
            width: 80px; height: 80px;
            border-radius: 50%;
            border: 3px solid var(--accent);
            box-shadow: 0 0 20px rgba(102, 126, 234, 0.3);
        }
        .user-info h1 { font-size: 24px; font-weight: 700; }
        .user-info .nick { color: var(--text2); font-size: 14px; }
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            margin-top: 8px;
        }
        .badge.premium { background: linear-gradient(135deg, #f7971e, #ffd200); color: #000; }
        .badge.trial { background: var(--accent); color: #fff; }
        .badge.free { background: var(--border); color: var(--text2); }
        
        /* Stats */
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: var(--card);
            border-radius: var(--radius);
            padding: 24px;
            text-align: center;
            box-shadow: var(--shadow);
            border: 1px solid var(--border);
            transition: transform 0.2s;
        }
        .stat-card:hover { transform: translateY(-4px); }
        .stat-card .number { font-size: 42px; font-weight: 700; background: linear-gradient(135deg, var(--accent), var(--accent2)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .stat-card .label { color: var(--text2); margin-top: 8px; font-size: 14px; }
        
        /* Card */
        .card {
            background: var(--card);
            border-radius: var(--radius);
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: var(--shadow);
            border: 1px solid var(--border);
        }
        .card h2 {
            font-size: 18px;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border);
        }
        
        /* Script item */
        .script-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px;
            border-radius: 12px;
            margin-bottom: 8px;
            background: rgba(255,255,255,0.02);
            transition: background 0.2s;
        }
        .script-item:hover { background: rgba(255,255,255,0.05); }
        .script-left { display: flex; align-items: center; gap: 12px; }
        .status-dot {
            width: 12px; height: 12px;
            border-radius: 50%;
            flex-shrink: 0;
        }
        .status-dot.running { background: var(--green); box-shadow: 0 0 10px rgba(76,175,80,0.5); }
        .status-dot.stopped { background: var(--red); }
        .script-name { font-weight: 600; }
        .script-meta { font-size: 12px; color: var(--text2); }
        .script-actions { display: flex; gap: 8px; }
        
        /* Buttons */
        .btn {
            padding: 8px 16px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            font-family: 'Inter', sans-serif;
            transition: all 0.2s;
        }
        .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
        .btn-start { background: var(--green); color: white; }
        .btn-stop { background: var(--red); color: white; }
        .btn-logs { background: var(--blue); color: white; }
        .btn-delete { background: #555; color: white; }
        
        /* Logs */
        .log-content {
            background: #1a1a2e;
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            font-family: 'Fira Code', 'Courier New', monospace;
            font-size: 12px;
            max-height: 300px;
            overflow-y: auto;
            white-space: pre-wrap;
            color: #00ff88;
            margin-top: 8px;
            display: none;
        }
        .log-content.show { display: block; }
        
        /* Plans */
        .plans {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
        }
        .plan-card {
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            color: white;
            border-radius: var(--radius);
            padding: 24px;
            text-align: center;
            transition: transform 0.2s;
        }
        .plan-card:hover { transform: scale(1.05); }
        .plan-card h3 { margin-bottom: 8px; }
        .plan-card .price { font-size: 36px; font-weight: 700; }
        .plan-card .period { opacity: 0.8; font-size: 14px; }
        
        /* Loading */
        .loading { text-align: center; padding: 40px; color: var(--text2); }
        .spinner {
            width: 40px; height: 40px;
            border: 3px solid var(--border);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin: 0 auto 16px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        
        /* Responsive */
        @media (max-width: 600px) {
            .script-item { flex-direction: column; gap: 12px; align-items: flex-start; }
            .script-actions { width: 100%; justify-content: flex-end; }
            .header { flex-direction: column; text-align: center; }
        }
        
        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg); }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header" id="header">
            <div class="loading"><div class="spinner"></div>Загрузка...</div>
        </div>
        
        <div class="stats" id="stats">
            <div class="stat-card">
                <div class="number" id="totalScripts">-</div>
                <div class="label">Всего скриптов</div>
            </div>
            <div class="stat-card">
                <div class="number" id="runningScripts">-</div>
                <div class="label">Запущено</div>
            </div>
            <div class="stat-card">
                <div class="number" id="daysLeft">-</div>
                <div class="label">Дней подписки</div>
            </div>
            <div class="stat-card">
                <div class="number" id="totalRestarts">-</div>
                <div class="label">Перезапусков</div>
            </div>
        </div>
        
        <div class="card">
            <h2>📱 Мои скрипты</h2>
            <div id="scriptsList"><div class="loading"><div class="spinner"></div>Загрузка скриптов...</div></div>
        </div>
        
        <div class="card">
            <h2>💎 Тарифы</h2>
            <div class="plans">
                <div class="plan-card"><h3>7 дней</h3><div class="price">1.99$</div><div class="period">USDT</div></div>
                <div class="plan-card"><h3>30 дней</h3><div class="price">4.99$</div><div class="period">USDT</div></div>
                <div class="plan-card"><h3>60 дней</h3><div class="price">7.99$</div><div class="period">USDT</div></div>
                <div class="plan-card"><h3>Навсегда</h3><div class="price">14.99$</div><div class="period">VIP</div></div>
            </div>
        </div>
    </div>
    
    <script>
        const params = new URLSearchParams(window.location.search);
        const userId = params.get('user_id');
        
        async function api(path) {
            const resp = await fetch(`/api${path}?user_id=${userId}`);
            return resp.json();
        }
        
        async function loadAll() {
            try {
                const data = await api('/scripts');
                
                // Header
                const header = document.getElementById('header');
                header.innerHTML = `
                    <img src="${data.user.avatar || 'https://ui-avatars.com/api/?name=User&background=667eea&color=fff&size=200'}" 
                         class="avatar" alt="Avatar" onerror="this.src='https://ui-avatars.com/api/?name=User&background=667eea&color=fff&size=200'">
                    <div class="user-info">
                        <h1>${data.user.name || 'Пользователь'}</h1>
                        <div class="nick">@${data.user.username || 'unknown'}</div>
                        <span class="badge ${data.user.subscription}">${data.user.subscription === 'premium' ? '💎 Премиум' : data.user.subscription === 'trial' ? '🆓 Пробный' : 'Бесплатный'}</span>
                    </div>
                `;
                
                // Stats
                document.getElementById('totalScripts').textContent = data.total;
                document.getElementById('runningScripts').textContent = data.running;
                document.getElementById('daysLeft').textContent = data.days_left;
                document.getElementById('totalRestarts').textContent = data.total_restarts;
                
                // Scripts
                let html = '';
                if (data.scripts.length === 0) {
                    html = '<div style="text-align:center;padding:30px;color:var(--text2)">📭 Нет скриптов. Загрузите через бота!</div>';
                } else {
                    data.scripts.forEach(s => {
                        html += `
                            <div class="script-item">
                                <div class="script-left">
                                    <span class="status-dot ${s.status}"></span>
                                    <div>
                                        <div class="script-name">${s.name} ${s.tags ? `<span style="color:var(--accent);font-size:12px">[${s.tags}]</span>` : ''}</div>
                                        <div class="script-meta">${s.size} • Перезапусков: ${s.restarts} • ${s.created}</div>
                                    </div>
                                </div>
                                <div class="script-actions">
                                    ${s.status === 'running' 
                                        ? `<button class="btn btn-stop" onclick="action('stop','${s.id}')">🛑 Стоп</button>`
                                        : `<button class="btn btn-start" onclick="action('start','${s.id}')">🚀 Запустить</button>`
                                    }
                                    <button class="btn btn-logs" onclick="toggleLogs('${s.id}')">📜 Логи</button>
                                </div>
                            </div>
                            <div class="log-content" id="logs_${s.id}"></div>
                        `;
                    });
                }
                document.getElementById('scriptsList').innerHTML = html;
            } catch(e) {
                console.error(e);
                document.getElementById('scriptsList').innerHTML = '<div style="color:var(--red);text-align:center;padding:20px">❌ Ошибка загрузки</div>';
            }
        }
        
        async function action(type, id) {
            await api(`/${type}&script_id=${id}`);
            loadAll();
        }
        
        async function toggleLogs(id) {
            const div = document.getElementById(`logs_${id}`);
            if (div.classList.contains('show')) {
                div.classList.remove('show');
                return;
            }
            const data = await api(`/logs&script_id=${id}`);
            div.textContent = data.logs || 'Логов нет';
            div.classList.add('show');
        }
        
        loadAll();
        setInterval(loadAll, 15000);
    </script>
</body>
</html>
"""

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
            self.wfile.write(WEB_PANEL_HTML.encode())
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
                days = get_days_left(user_id)
                total_restarts = sum(s['total_restarts'] for s in scripts)
                
                result = {
                    'user': {
                        'name': user['first_name'] if user and user.get('first_name') else 'Пользователь',
                        'username': user['username'] if user else '',
                        'avatar': user['avatar_url'] if user else '',
                        'subscription': user['subscription'] if user else 'free'
                    },
                    'total': len(scripts),
                    'running': running,
                    'days_left': days,
                    'total_restarts': total_restarts,
                    'scripts': [{
                        'id': s['id'], 'name': s['name'],
                        'status': s['status'], 'size': format_size(s['size']),
                        'restarts': s['total_restarts'], 'tags': s.get('tags', ''),
                        'created': s['created_at'][:10] if s['created_at'] else ''
                    } for s in scripts]
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
                self.wfile.write(json.dumps({'ok': True}).encode())
            
            elif 'stop' in api_path:
                script_id = params.get('script_id', [''])[0]
                script = get_script(script_id)
                if script and script.get('pid'):
                    stop_script(script['pid'])
                    update_script_status(script_id, 'stopped')
                self.wfile.write(json.dumps({'ok': True}).encode())
            
            elif 'logs' in api_path:
                script_id = params.get('script_id', [''])[0]
                log_path = get_log_path(script_id)
                logs = ''
                if os.path.exists(log_path):
                    with open(log_path, 'r') as f: logs = f.read()[-10000:]
                self.wfile.write(json.dumps({'logs': logs}, ensure_ascii=False).encode())
            
            return
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(WEB_PANEL_HTML.encode())
    
    def log_message(self, format, *args): pass

def run_web_panel():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(('0.0.0.0', port), WebAPI)
    print(f"🌐 Веб-панель: http://0.0.0.0:{port}/panel?user_id=ТВОЙ_ID")
    server.serve_forever()

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print("=" * 50)
    print(f"  🚀 HOSTING v{VERSION}")
    print(f"  👑 Admin: {ADMIN_ID}")
    print(f"  🌐 Web Panel: ON")
    print(f"  👥 Referral: ON")
    print(f"  🔔 Notify: ON")
    print(f"  🌐 RU/EN: ON")
    print("=" * 50)
    
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=run_web_panel, daemon=True).start()
    
    while True:
        try:
            print("✅ Бот запущен!")
            bot.infinity_polling()
        except Exception as e:
            print(f"❌ {e}")
            time.sleep(10)
