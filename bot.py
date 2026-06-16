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
import requests

# Библиотеки
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ========== КОНФИГУРАЦИЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
VERSION = "11.0 RENDER"
TOKEN = os.getenv("BOT_TOKEN", "8964647336:AAEP1PO_NRJsGAuqWauXjf6il2mgcb2KkvM")
ADMIN_ID = int(os.getenv("ADMIN_ID", "314148464"))
CRYPTO_TOKEN = os.getenv("CRYPTO_TOKEN", "593773:AAcVRGB0bizw5hLjy0on5QmQcr6X4lHmyYX")

BASE_DIR = "/app" if os.path.exists("/app") else "."
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
DATABASE_PATH = os.path.join(BASE_DIR, "hosting.db")

FREE_MAX_SCRIPTS = 10
FREE_MAX_SIZE_MB = 10
PREMIUM_MAX_SIZE_MB = 1024
MONITOR_INTERVAL = 10
TRIAL_DAYS = 7

for d in [SCRIPTS_DIR, LOGS_DIR, TEMP_DIR]:
    os.makedirs(d, exist_ok=True)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def init_db():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT,
        subscription TEXT DEFAULT 'trial',
        subscription_expiry TIMESTAMP,
        trial_start TIMESTAMP)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS scripts (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        name TEXT NOT NULL, path TEXT NOT NULL,
        main_file TEXT, pid INTEGER,
        status TEXT DEFAULT 'stopped',
        size INTEGER, restart_count INTEGER DEFAULT 0,
        total_restarts INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS promocodes (
        code TEXT PRIMARY KEY,
        max_uses INTEGER DEFAULT 999,
        used_count INTEGER DEFAULT 0)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS crypto_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, payment_id TEXT UNIQUE,
        amount REAL, currency TEXT, plan TEXT,
        status TEXT DEFAULT 'pending')''')
    
    # Добавляем промокоды
    for code in ['PREMIUM2024', 'ADMIN', 'MEGA', 'CRYPTO']:
        cursor.execute("INSERT OR IGNORE INTO promocodes (code, max_uses, used_count) VALUES (?, 999, 0)", (code,))
    
    conn.commit()

init_db()

# ========== ФУНКЦИИ ==========
def get_user(user_id):
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    return dict(row) if row else None

def create_user(user_id, username):
    trial_start = datetime.now()
    trial_end = trial_start + timedelta(days=TRIAL_DAYS)
    try:
        cursor.execute('INSERT INTO users (user_id, username, subscription, subscription_expiry, trial_start) VALUES (?,?,?,?,?)',
                      (user_id, username, 'trial', trial_end, trial_start))
        conn.commit()
    except:
        pass

def is_premium(user_id):
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    return user and user['subscription'] == 'premium'

def get_days_left(user_id):
    user = get_user(user_id)
    if not user:
        return 0
    if user['subscription'] == 'trial':
        ts = user.get('trial_start')
        if ts:
            try:
                return max(0, TRIAL_DAYS - (datetime.now() - datetime.fromisoformat(str(ts))).days)
            except:
                pass
    if user['subscription'] == 'premium':
        exp = user.get('subscription_expiry')
        if exp:
            try:
                return max(0, (datetime.fromisoformat(str(exp)) - datetime.now()).days)
            except:
                pass
        return 999
    return 0

def activate_premium(user_id, days):
    expiry = datetime.now() + timedelta(days=days)
    cursor.execute("UPDATE users SET subscription = 'premium', subscription_expiry = ? WHERE user_id = ?", (expiry, user_id))
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
    if pid:
        cursor.execute('UPDATE scripts SET status = ?, pid = ? WHERE id = ?', (status, pid, script_id))
    else:
        cursor.execute('UPDATE scripts SET status = ? WHERE id = ?', (status, script_id))
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
    if is_premium(user_id):
        return True
    return count_user_scripts(user_id) < FREE_MAX_SCRIPTS

# ========== SCRIPT MANAGER ==========
def find_py_files(folder):
    py_files = []
    if os.path.exists(folder):
        for root, dirs, files in os.walk(folder):
            for file in files:
                if file.endswith('.py'):
                    py_files.append(os.path.join(root, file))
    return py_files

def run_script(script_id, script_path):
    log_path = os.path.join(LOGS_DIR, f"{script_id}.log")
    try:
        with open(log_path, 'a') as log_file:
            log_file.write(f"\n🚀 Запуск {datetime.now()}\n{'='*40}\n")
            process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=log_file, stderr=subprocess.STDOUT,
                cwd=os.path.dirname(script_path)
            )
        reset_restart_count(script_id)
        return process.pid, None
    except Exception as e:
        return None, str(e)

def stop_script(pid):
    try:
        os.kill(pid, 9)
        return True
    except:
        return False

def is_process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except:
        return False

def get_log_path(script_id):
    return os.path.join(LOGS_DIR, f"{script_id}.log")

def format_size(s):
    if s < 1024:
        return f"{s} Б"
    elif s < 1024**2:
        return f"{s/1024:.1f} КБ"
    elif s < 1024**3:
        return f"{s/1024**2:.1f} МБ"
    return f"{s/1024**3:.1f} ГБ"

# ========== CRYPTO BOT ==========
def create_crypto_invoice(user_id, amount, currency, plan):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN, "Content-Type": "application/json"}
    data = {
        "asset": currency,
        "amount": str(amount),
        "description": f"Hosting Premium - {plan}",
        "payload": json.dumps({"user_id": user_id, "plan": plan})
    }
    
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        result = resp.json()
        print(f"Crypto API: {json.dumps(result, indent=2)}")
        
        if result.get("ok"):
            invoice = result["result"]
            cursor.execute("INSERT INTO crypto_payments (user_id, payment_id, amount, currency, plan) VALUES (?,?,?,?,?)",
                         (user_id, invoice["invoice_id"], amount, currency, plan))
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
    except:
        pass
    return None

# ========== BOT ==========
bot = telebot.TeleBot(TOKEN)
upload_states = {}

def get_main_menu(user_id=None):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("📱 Мои скрипты"), KeyboardButton("📤 Загрузить"))
    markup.add(KeyboardButton("💎 Премиум"), KeyboardButton("ℹ️ Статус"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("👑 Админ"), KeyboardButton("🔍 Все скрипты"))
    return markup

@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    
    if not get_user(user_id):
        create_user(user_id, message.from_user.username)
    
    days = get_days_left(user_id)
    if user_id == ADMIN_ID:
        status = "👑 Админ"
    elif is_premium(user_id):
        status = f"💎 Премиум: {days} дн"
    elif days > 0:
        status = f"🆓 Пробный: {days} дн"
    else:
        status = "🆓 Бесплатный"
    
    bot.send_message(user_id,
        f"🚀 **Hosting Bot v{VERSION}**\n\n👤 {status}\n📦 Python хостинг\n🔄 Автоперезапуск\n💰 Крипто оплата\n☁️ Render Cloud",
        reply_markup=get_main_menu(user_id), parse_mode='Markdown')

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
        markup.add(
            InlineKeyboardButton(f"{emoji} {s['name'][:20]}", callback_data=f"info_{s['id']}"),
            InlineKeyboardButton("🗑", callback_data=f"delete_{s['id']}")
        )
    
    bot.send_message(message.chat.id, "📋 **Скрипты:**", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "📤 Загрузить")
def menu_upload(message):
    bot.reply_to(message, "📤 Отправьте .py файл или ZIP архив!")

@bot.message_handler(func=lambda m: m.text == "💎 Премиум")
def menu_premium(message):
    user_id = message.from_user.id
    if is_premium(user_id):
        days = get_days_left(user_id)
        bot.send_message(user_id, f"💎 **Премиум: {days} дн**")
        return
    
    text = "💰 **Выберите тариф:**"
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("💵 USDT 30д (3$)", callback_data="pay_usdt_30d"))
    markup.add(InlineKeyboardButton("💵 USDT навсегда (5$)", callback_data="pay_usdt_forever"))
    markup.add(InlineKeyboardButton("💎 TON 30д (6 TON)", callback_data="pay_ton_30d"))
    markup.add(InlineKeyboardButton("💎 TON навсегда (10 TON)", callback_data="pay_ton_forever"))
    markup.add(InlineKeyboardButton("🔑 Промокод", callback_data="enter_promo"))
    
    bot.send_message(user_id, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "ℹ️ Статус")
def menu_status(message):
    user_id = message.from_user.id
    days = get_days_left(user_id)
    scripts = count_user_scripts(user_id)
    
    if is_premium(user_id):
        text = f"💎 **Премиум: {days} дн**"
    elif days > 0:
        text = f"🆓 **Пробный: {days} дн**"
    else:
        text = "🆓 **Бесплатный**"
    
    text += f"\n📁 Скриптов: {scripts}/{FREE_MAX_SCRIPTS}"
    bot.send_message(user_id, text, parse_mode='Markdown')

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
        markup.add(InlineKeyboardButton(
            f"{emoji} {s['name'][:20]} | {owner_name}",
            callback_data=f"adm_{s['id']}"
        ))
    
    bot.send_message(ADMIN_ID, "🔍 **Все скрипты (нажми для управления):**", reply_markup=markup, parse_mode='Markdown')

# ========== ОПЛАТА ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith('pay_'))
def handle_payment(call):
    plans = {
        'pay_usdt_30d': ('USDT', 3.0, '30 дней', 30),
        'pay_usdt_forever': ('USDT', 5.0, 'Навсегда', 3650),
        'pay_ton_30d': ('TON', 6.0, '30 дней', 30),
        'pay_ton_forever': ('TON', 10.0, 'Навсегда', 3650),
    }
    
    if call.data not in plans:
        bot.answer_callback_query(call.id, "❌ Ошибка")
        return
    
    currency, amount, plan_name, days = plans[call.data]
    bot.answer_callback_query(call.id, "⏳ Создаю счёт...")
    
    invoice = create_crypto_invoice(call.from_user.id, amount, currency, plan_name)
    
    if invoice:
        pay_url = invoice.get("bot_invoice_url", "")
        invoice_id = invoice.get("invoice_id", "")
        
        markup = InlineKeyboardMarkup()
        if pay_url:
            markup.add(InlineKeyboardButton("💳 Оплатить", url=pay_url))
        markup.add(InlineKeyboardButton("🔄 Проверить", callback_data=f"check_{invoice_id}_{days}"))
        
        bot.send_message(call.message.chat.id,
            f"💰 **Счёт создан!**\n\n💎 {amount} {currency}\n📅 {plan_name}\n🆔 `{invoice_id}`",
            reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(call.message.chat.id, "❌ Ошибка создания счёта")

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment(call):
    parts = call.data.split('_')
    payment_id = parts[1]
    days = int(parts[2]) if len(parts) > 2 else 30
    
    result = check_crypto_payment(payment_id)
    
    if result and result.get("status") == "paid":
        cursor.execute("UPDATE crypto_payments SET status = 'paid' WHERE payment_id = ?", (payment_id,))
        conn.commit()
        activate_premium(call.from_user.id, days)
        
        bot.edit_message_text("✅ **Оплачено! Премиум активирован!** 🎉", 
                            call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        bot.answer_callback_query(call.id, "✅ Оплачено!")
    elif result and result.get("status") == "active":
        bot.answer_callback_query(call.id, "⏳ Ожидает оплаты...")
    else:
        bot.answer_callback_query(call.id, "❌ Не оплачено")

@bot.callback_query_handler(func=lambda call: call.data == "enter_promo")
def enter_promo(call):
    msg = bot.send_message(call.message.chat.id, "🔑 Отправьте промокод:")
    bot.register_next_step_handler(msg, process_promo)
    bot.answer_callback_query(call.id)

def process_promo(message):
    code = message.text.strip()
    cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
    promo = cursor.fetchone()
    
    if not promo:
        bot.reply_to(message, "❌ Промокод не найден")
        return
    if promo['used_count'] >= promo['max_uses']:
        bot.reply_to(message, "❌ Промокод использован")
        return
    
    activate_premium(message.from_user.id, 30)
    cursor.execute('UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?', (code,))
    conn.commit()
    bot.reply_to(message, "✅ Премиум на 30 дней!")

# ========== СКРИПТЫ ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith('info_'))
def info_callback(call):
    script_id = call.data[5:]
    script = get_script(script_id)
    
    if not script:
        bot.answer_callback_query(call.id, "❌ Не найден")
        return
    
    if script['user_id'] != call.from_user.id and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Нет доступа")
        return
    
    bot.answer_callback_query(call.id)
    show_script_info(call.message.chat.id, script)

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_'))
def admin_script_callback(call):
    script_id = call.data[4:]
    script = get_script(script_id)
    
    if not script:
        bot.answer_callback_query(call.id, "❌ Не найден")
        return
    
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Только админ")
        return
    
    bot.answer_callback_query(call.id)
    show_script_info(ADMIN_ID, script, is_admin=True)

def show_script_info(chat_id, script, is_admin=False):
    emoji = "🟢" if script['status'] == 'running' else "🔴"
    
    info = (
        f"{emoji} **{script['name']}**\n\n"
        f"🆔 `{script['id']}`\n"
        f"📁 Размер: {format_size(script['size'])}\n"
        f"📊 Статус: {script['status']}\n"
        f"🔄 Перезапусков: {script['total_restarts']}"
    )
    
    if script.get('main_file'):
        info += f"\n📄 Главный: {script['main_file']}"
    
    if is_admin:
        owner = get_user(script['user_id'])
        if owner and owner.get('username'):
            info += f"\n👤 @{owner['username']}"
        else:
            info += f"\n👤 ID: {script['user_id']}"
    
    markup = InlineKeyboardMarkup(row_width=2)
    
    if script['status'] == 'running':
        markup.add(InlineKeyboardButton("🛑 Остановить", callback_data=f"stop_{script['id']}"))
    else:
        markup.add(InlineKeyboardButton("🚀 Запустить", callback_data=f"start_{script['id']}"))
    
    markup.add(
        InlineKeyboardButton("📜 Логи", callback_data=f"log_{script['id']}"),
        InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{script['id']}")
    )
    
    if is_admin:
        markup.add(InlineKeyboardButton("🔙 К списку", callback_data="admin_scripts"))
    
    bot.send_message(chat_id, info, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('start_'))
def start_callback(call):
    script_id = call.data[6:]
    script = get_script(script_id)
    
    if not script:
        bot.answer_callback_query(call.id, "❌ Не найден")
        return
    
    if script['user_id'] != call.from_user.id and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌")
        return
    
    if script.get('main_file'):
        main_path = os.path.join(script['path'], script['main_file'])
    else:
        py_files = find_py_files(script['path'])
        main_path = py_files[0] if py_files else None
    
    if not main_path or not os.path.exists(main_path):
        bot.answer_callback_query(call.id, "❌ Файл не найден")
        return
    
    pid, error = run_script(script_id, main_path)
    if pid:
        update_script_status(script_id, 'running', pid)
        bot.answer_callback_query(call.id, "✅ Запущен!")
    else:
        bot.answer_callback_query(call.id, f"❌ {error}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_'))
def stop_callback(call):
    script_id = call.data[5:]
    script = get_script(script_id)
    
    if not script:
        bot.answer_callback_query(call.id, "❌ Не найден")
        return
    
    if script['status'] == 'running' and script.get('pid'):
        stop_script(script['pid'])
        update_script_status(script_id, 'stopped')
        bot.answer_callback_query(call.id, "✅ Остановлен")
    else:
        bot.answer_callback_query(call.id, "Уже остановлен")

@bot.callback_query_handler(func=lambda call: call.data.startswith('log_'))
def log_callback(call):
    script_id = call.data[4:]
    log_path = get_log_path(script_id)
    
    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            content = f.read()[-4000:]
        if content.strip():
            bot.send_message(call.message.chat.id, f"📜 **Логи:**\n```\n{content}\n```", parse_mode='Markdown')
        else:
            bot.send_message(call.message.chat.id, "📜 Логи пустые")
    else:
        bot.send_message(call.message.chat.id, "📜 Логов нет")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_'))
def delete_callback(call):
    script_id = call.data[7:]
    script = get_script(script_id)
    
    if not script:
        bot.answer_callback_query(call.id, "❌ Не найден")
        return
    
    if script['status'] == 'running' and script.get('pid'):
        stop_script(script['pid'])
    
    if os.path.exists(script['path']):
        shutil.rmtree(script['path'], ignore_errors=True)
    
    log_path = get_log_path(script_id)
    if os.path.exists(log_path):
        os.remove(log_path)
    
    cursor.execute('DELETE FROM scripts WHERE id = ?', (script_id,))
    conn.commit()
    
    bot.answer_callback_query(call.id, "✅ Удалён")
    try:
        bot.edit_message_text("🗑 Скрипт удалён", call.message.chat.id, call.message.message_id)
    except:
        pass

# ========== АДМИН ==========
@bot.callback_query_handler(func=lambda call: call.data == "admin_scripts")
def admin_scripts_list(call):
    bot.answer_callback_query(call.id)
    menu_all_scripts(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_premium")
def admin_give_premium(call):
    msg = bot.send_message(ADMIN_ID, "📝 Отправьте ID и дни:\n`123456 30`")
    bot.register_next_step_handler(msg, process_admin_premium)
    bot.answer_callback_query(call.id)

def process_admin_premium(message):
    try:
        parts = message.text.split()
        uid = int(parts[0])
        days = int(parts[1]) if len(parts) > 1 else 30
        activate_premium(uid, days)
        bot.reply_to(message, f"✅ Премиум на {days} дн для {uid}")
    except:
        bot.reply_to(message, "❌ Неверный формат")

# ========== ЗАГРУЗКА ==========
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    
    if not get_user(user_id):
        create_user(user_id, message.from_user.username)
    
    if not check_user_limits(user_id):
        bot.reply_to(message, "❌ Лимит! Нужен 💎 Премиум!")
        return
    
    file_info = bot.get_file(message.document.file_id)
    file_name = message.document.file_name
    file_size = message.document.file_size
    
    max_size = PREMIUM_MAX_SIZE_MB if is_premium(user_id) else FREE_MAX_SIZE_MB
    if file_size > max_size * 1024 * 1024:
        bot.reply_to(message, f"❌ Максимум {max_size} МБ!")
        return
    
    temp_dir = os.path.join(TEMP_DIR, str(user_id))
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file_name)
    
    msg = bot.reply_to(message, "⏳ Загружаю...")
    
    try:
        downloaded = bot.download_file(file_info.file_path)
        with open(temp_path, 'wb') as f:
            f.write(downloaded)
    except:
        bot.edit_message_text("❌ Ошибка", user_id, msg.message_id)
        return
    
    script_id = str(uuid.uuid4())[:8]
    upload_states[user_id] = {
        'script_id': script_id,
        'temp_path': temp_path,
        'file_name': file_name,
        'file_size': file_size,
        'msg_id': msg.message_id
    }
    
    if file_name.lower().endswith('.zip'):
        extract_to = os.path.join(TEMP_DIR, str(user_id), script_id)
        os.makedirs(extract_to, exist_ok=True)
        
        try:
            with zipfile.ZipFile(temp_path) as zf:
                zf.extractall(extract_to)
        except:
            bot.edit_message_text("❌ ZIP ошибка", user_id, msg.message_id)
            return
        
        py_files = find_py_files(extract_to)
        if not py_files:
            bot.edit_message_text("❌ Нет .py!", user_id, msg.message_id)
            return
        
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
    if user_id not in upload_states:
        bot.answer_callback_query(call.id, "❌")
        return
    
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
            s = os.path.join(state['extract_to'], item)
            d = os.path.join(user_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        main_file = state.get('selected_main')
        main_path = os.path.join(user_dir, main_file) if main_file else script_path
    else:
        dest = os.path.join(user_dir, original_filename)
        shutil.move(script_path, dest)
        main_path = dest
        main_file = original_filename
    
    add_script(script_id, user_id, original_filename, user_dir, file_size, main_file)
    
    pid, error = run_script(script_id, main_path)
    if pid:
        update_script_status(script_id, 'running', pid)
        bot.send_message(user_id,
            f"✅ **Запущен!**\n📄 {original_filename}\n🆔 `{script_id}`\n🔢 PID: `{pid}`" +
            (f"\n📄 Главный: {main_file}" if main_file else ""),
            parse_mode='Markdown')
    else:
        bot.send_message(user_id, f"❌ {error}")
    
    try:
        if os.path.exists(state['temp_path']):
            os.remove(state['temp_path'])
        if state.get('extract_to') and os.path.exists(state['extract_to']):
            shutil.rmtree(state['extract_to'], ignore_errors=True)
    except:
        pass

# ========== МОНИТОРИНГ ==========
