# hosting_bot.py - Хостинг бот (v6.1 - РАССЫЛКА И СБП)
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
import os
import sqlite3
import threading
import time
import uuid
import shutil
import zipfile
import subprocess
import signal
import requests
from datetime import datetime, timedelta

TOKEN = "8993679520:AAGFLEg3azqd1UV8H374hQUU8wqLYVhrdSo"
VERSION = "6.1.0"
ADMIN_IDS = [314148464]
CRYPTO_TOKEN = "593773:AA2SggSE9MiTxJ6jdir8g7ufY2Cd2Pchvhu"
CRYPTO_API = "https://pay.crypt.bot/api"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
DATABASE_PATH = os.path.join(BASE_DIR, "bot_database.db")

# Тарифы
FREE_MAX_SCRIPTS = 3
FREE_MAX_SIZE_MB = 5
FREE_DURATION_HOURS = 24

PRO_PRICES = {
    "7d": {"name": "7 дней", "price_rub": 159, "price_stars": 180, "price_usd": 1.70, "days": 7},
    "30d": {"name": "30 дней", "price_rub": 549, "price_stars": 620, "price_usd": 5.80, "days": 30},
    "180d": {"name": "180 дней", "price_rub": 1199, "price_stars": 1350, "price_usd": 12.50, "days": 180},
}
PRO_MAX_SCRIPTS = 10
PRO_MAX_SIZE_MB = 50

EXPERT_PRICES = {
    "7d": {"name": "7 дней", "price_rub": 239, "price_stars": 270, "price_usd": 2.50, "days": 7},
    "30d": {"name": "30 дней", "price_rub": 899, "price_stars": 1000, "price_usd": 9.50, "days": 30},
    "180d": {"name": "180 дней", "price_rub": 1899, "price_stars": 2100, "price_usd": 19.90, "days": 180},
}
EXPERT_MAX_SCRIPTS = 999
EXPERT_MAX_SIZE_MB = 1024

MONITOR_INTERVAL = 10

os.makedirs(SCRIPTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

bot_status = "running"
pending_payments = {}
crypto_invoices = {}
broadcast_state = {}  # {admin_id: {"step": "waiting", "type": None}}

# ========== БАЗА ДАННЫХ ==========
def get_db():
    conn = sqlite3.connect(DATABASE_PATH); conn.row_factory = sqlite3.Row; return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, subscription TEXT DEFAULT 'free', subscription_expiry TIMESTAMP, free_used INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS scripts (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL, pid INTEGER, status TEXT DEFAULT 'stopped', size INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS promocodes (code TEXT PRIMARY KEY, type TEXT DEFAULT 'pro', days INTEGER DEFAULT 30, max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0)''')
        conn.execute("INSERT OR IGNORE INTO promocodes (code, type, days, max_uses) VALUES ('PREMIUM2024', 'expert', 30, 100), ('HOSTINGFREE', 'pro', 30, 50)")
        conn.commit()

def get_user(user_id):
    with get_db() as conn:
        cur = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cur.fetchone()
        user = dict(row) if row else None
    if user:
        if user.get('subscription_expiry'):
            try:
                expiry = datetime.fromisoformat(user['subscription_expiry'])
                if datetime.now() > expiry:
                    if user['subscription'] != 'free' or user.get('free_used', 0) == 1:
                        user['subscription'] = 'free'
                        if user.get('free_used', 0) == 0:
                            user['free_used'] = 1
                            user['subscription_expiry'] = (datetime.now() + timedelta(hours=FREE_DURATION_HOURS)).isoformat()
                        else:
                            user['subscription_expiry'] = None
                        conn.execute('UPDATE users SET subscription=?, subscription_expiry=?, free_used=? WHERE user_id=?', 
                                   (user['subscription'], user['subscription_expiry'], user.get('free_used', 0), user_id))
                        conn.commit()
            except: pass
        if user_id in ADMIN_IDS: user['subscription'] = 'expert'
    return user

def create_user(user_id, username):
    with get_db() as conn:
        try:
            expiry = (datetime.now() + timedelta(hours=FREE_DURATION_HOURS)).isoformat()
            conn.execute('INSERT INTO users (user_id, username, subscription_expiry, free_used) VALUES (?, ?, ?, 1)', (user_id, username, expiry))
            conn.commit()
        except: pass

def set_subscription(user_id, plan, days=0):
    with get_db() as conn:
        if days > 0 and plan != 'free':
            expiry = (datetime.now() + timedelta(days=days)).isoformat()
            conn.execute('UPDATE users SET subscription=?, subscription_expiry=? WHERE user_id=?', (plan, expiry, user_id))
        else:
            conn.execute('UPDATE users SET subscription=?, subscription_expiry=NULL WHERE user_id=?', (plan, user_id))
        conn.commit()

def activate_promo(user_id, code):
    with get_db() as conn:
        cur = conn.execute('SELECT * FROM promocodes WHERE code=?', (code,))
        promo = cur.fetchone()
        if not promo: return False, "Промокод не найден"
        if promo['used_count'] >= promo['max_uses']: return False, "Промокод закончился"
        conn.execute('UPDATE promocodes SET used_count=used_count+1 WHERE code=?', (code,))
        plan, days = promo['type'], promo['days']
        expiry = (datetime.now() + timedelta(days=days)).isoformat()
        conn.execute('UPDATE users SET subscription=?, subscription_expiry=? WHERE user_id=?', (plan, expiry, user_id))
        conn.commit()
        return True, f"Тариф {plan} активирован на {days} дней!"

def add_script(script_id, user_id, name, path, size):
    with get_db() as conn: conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?,?)', (script_id, user_id, name, path, None, 'stopped', size)); conn.commit()

def get_user_scripts(user_id):
    with get_db() as conn: return [dict(row) for row in conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY created_at DESC', (user_id,)).fetchall()]

def get_script(script_id):
    with get_db() as conn: row = conn.execute('SELECT * FROM scripts WHERE id=?', (script_id,)).fetchone(); return dict(row) if row else None

def get_all_scripts():
    with get_db() as conn: return [dict(row) for row in conn.execute('SELECT * FROM scripts ORDER BY created_at DESC').fetchall()]

def update_script_status(script_id, status, pid=None):
    with get_db() as conn:
        if pid is not None: conn.execute('UPDATE scripts SET status=?, pid=? WHERE id=?', (status, pid, script_id))
        else: conn.execute('UPDATE scripts SET status=? WHERE id=?', (status, script_id))
        conn.commit()

def get_all_running_scripts():
    with get_db() as conn: return [dict(row) for row in conn.execute("SELECT * FROM scripts WHERE status='running'").fetchall()]

def count_user_scripts(user_id):
    with get_db() as conn: return conn.execute('SELECT COUNT(*) as cnt FROM scripts WHERE user_id=?', (user_id,)).fetchone()['cnt']

def delete_script(script_id, user_id=None):
    with get_db() as conn:
        if user_id: conn.execute('DELETE FROM scripts WHERE id=? AND user_id=?', (script_id, user_id))
        else: conn.execute('DELETE FROM scripts WHERE id=?', (script_id,))
        conn.commit()

def get_all_users():
    with get_db() as conn: return [dict(row) for row in conn.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()]

def stop_all_scripts():
    for s in get_all_running_scripts():
        try: stop_script(s['pid'])
        except: pass
        update_script_status(s['id'], 'stopped')

def start_all_user_scripts():
    started = 0
    for s in get_all_scripts():
        if s['status'] == 'stopped':
            mf = find_main_file(s['path'], s['name'])
            if mf:
                pid, err = run_script(s['id'], mf)
                if not err: update_script_status(s['id'], 'running', pid); started += 1
    return started

# ========== CRYPTO BOT ==========
def create_crypto_invoice(amount_usd, description, payload):
    try:
        r = requests.post(f"{CRYPTO_API}/createInvoice", json={"asset":"USDT","amount":str(amount_usd),"description":description,"payload":payload}, headers={"Crypto-Pay-API-Token":CRYPTO_TOKEN}).json()
        if r.get('ok'): return {'success':True, 'invoice_id':r['result']['invoice_id'], 'url':r['result']['bot_invoice_url']}
    except: pass
    return {'success':False}

def check_crypto_invoice(invoice_id):
    try:
        r = requests.get(f"{CRYPTO_API}/getInvoices", params={"invoice_ids":str(invoice_id)}, headers={"Crypto-Pay-API-Token":CRYPTO_TOKEN}).json()
        if r.get('ok') and r['result']['items']: return r['result']['items'][0]['status'] == 'paid'
    except: pass
    return False

# ========== УТИЛИТЫ ==========
def get_user_limits(user_id):
    user = get_user(user_id)
    if not user: return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB
    if user_id in ADMIN_IDS or user['subscription'] == 'expert': return EXPERT_MAX_SCRIPTS, EXPERT_MAX_SIZE_MB
    elif user['subscription'] == 'pro': return PRO_MAX_SCRIPTS, PRO_MAX_SIZE_MB
    else: return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB

def check_user_limits(user_id):
    max_scripts, _ = get_user_limits(user_id)
    if max_scripts == 999: return True
    return count_user_scripts(user_id) < max_scripts

def check_subscription(user_id):
    user = get_user(user_id)
    if not user: return False
    if user_id in ADMIN_IDS: return True
    if user['subscription'] == 'free' and user.get('free_used', 0) == 0:
        return True
    if user.get('subscription_expiry'):
        try:
            expiry = datetime.fromisoformat(user['subscription_expiry'])
            return datetime.now() < expiry
        except: pass
    return False

def get_subscription_info(user_id):
    user = get_user(user_id)
    if not user: return "Нет подписки", 0
    if user_id in ADMIN_IDS: return "👑 Админ (вечный)", 0
    if user.get('subscription_expiry'):
        try:
            expiry = datetime.fromisoformat(user['subscription_expiry'])
            days_left = (expiry - datetime.now()).days
            hours_left = int((expiry - datetime.now()).total_seconds() / 3600)
            if days_left > 0: return f"{user['subscription'].upper()}", days_left
            elif hours_left > 0: return f"{user['subscription'].upper()}", f"{hours_left}ч"
            else: return "Истекла", 0
        except: pass
    if user['subscription'] == 'free' and user.get('free_used', 0) == 0:
        return "🆓 Бесплатный (не активирован)", 0
    return "Истекла", 0

def cleanup_temp(user_id):
    d = os.path.join(TEMP_DIR, str(user_id))
    if os.path.exists(d):
        try: shutil.rmtree(d)
        except: pass

def extract_zip(zip_path, extract_to):
    try:
        with zipfile.ZipFile(zip_path, 'r') as z: z.extractall(extract_to)
        return True, None
    except Exception as e: return False, str(e)

def find_py_files(folder):
    py_files = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.endswith('.py'): py_files.append(os.path.join(root, f))
    return py_files

def find_main_file(script_dir, script_name):
    fp = os.path.join(script_dir, script_name)
    if os.path.exists(fp) and fp.endswith('.py'): return fp
    pf = find_py_files(script_dir)
    return pf[0] if pf else None

def run_script(script_id, script_path):
    log_path = os.path.join(LOGS_DIR, f"{script_id}.log")
    try:
        with open(log_path, 'ab') as lf: p = subprocess.Popen(['python', script_path], stdout=lf, stderr=subprocess.STDOUT, cwd=os.path.dirname(script_path))
        return p.pid, None
    except Exception as e: return None, str(e)

def stop_script(pid):
    try: os.kill(pid, signal.SIGTERM); return True, None
    except: return False, "Ошибка"

def is_process_alive(pid):
    try: os.kill(pid, 0); return True
    except: return False

def bot_blocked(call):
    if bot_status == "stopped" and call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!"); return True
    return False

# ========== БОТ ==========
bot = telebot.TeleBot(TOKEN)
upload_states = {}

def admin_panel():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📦 Запустить скрипт", callback_data="menu_upload"),
        InlineKeyboardButton("📋 Мои скрипты", callback_data="menu_list"),
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("👥 Пользователи", callback_data="admin_users_list"),
        InlineKeyboardButton("📋 Все скрипты", callback_data="admin_all_scripts"),
        InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
    )
    markup.add(InlineKeyboardButton("🟢 ОСТАНОВИТЬ БОТА" if bot_status == "running" else "🔴 ЗАПУСТИТЬ БОТА", callback_data="admin_stop_bot" if bot_status == "running" else "admin_start_bot"))
    markup.add(InlineKeyboardButton("🛑 Остановить всё", callback_data="admin_stop_all"), InlineKeyboardButton("▶️ Запустить всё", callback_data="admin_start_all"))
    return markup

def user_panel():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📦 Запустить скрипт", callback_data="menu_upload"), InlineKeyboardButton("📋 Мои скрипты", callback_data="menu_list"), InlineKeyboardButton("💎 Тарифы", callback_data="menu_tariffs"), InlineKeyboardButton("🎁 Промокод", callback_data="menu_promo"), InlineKeyboardButton("ℹ️ Помощь", callback_data="menu_help"))
    return markup

def tariffs_panel():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("💎 PRO тарифы", callback_data="tariff_pro_menu"),
        InlineKeyboardButton("👑 Expert тарифы", callback_data="tariff_expert_menu"),
        InlineKeyboardButton("« Назад", callback_data="back_to_start")
    )
    return markup

def pro_tariffs_panel():
    markup = InlineKeyboardMarkup(row_width=1)
    for k, d in PRO_PRICES.items(): markup.add(InlineKeyboardButton(f"PRO {d['name']} — {d['price_rub']}₽", callback_data=f"buy_pro:{k}"))
    markup.add(InlineKeyboardButton("« Назад", callback_data="menu_tariffs")); return markup

def expert_tariffs_panel():
    markup = InlineKeyboardMarkup(row_width=1)
    for k, d in EXPERT_PRICES.items(): markup.add(InlineKeyboardButton(f"Expert {d['name']} — {d['price_rub']}₽", callback_data=f"buy_expert:{k}"))
    markup.add(InlineKeyboardButton("« Назад", callback_data="menu_tariffs")); return markup

def payment_method_panel(pt, pk):
    markup = InlineKeyboardMarkup(row_width=1)
    d = (PRO_PRICES if pt == 'pro' else EXPERT_PRICES)[pk]
    markup.add(
        InlineKeyboardButton(f"💎 Крипта USDT — ${d['price_usd']} (авто)", callback_data=f"pay_crypto:{pt}:{pk}"),
        InlineKeyboardButton(f"⭐ Stars — {d['price_stars']}⭐ (авто)", callback_data=f"pay_stars:{pt}:{pk}"),
        InlineKeyboardButton(f"💳 СБП — {d['price_rub']}₽", callback_data=f"pay_sbp:{pt}:{pk}"),
        InlineKeyboardButton("« Назад", callback_data=f"tariff_{pt}_menu")
    )
    return markup

def broadcast_type_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📝 Только текст", callback_data="bcast_text"),
        InlineKeyboardButton("🖼 Фото + текст", callback_data="bcast_photo"),
        InlineKeyboardButton("🎥 Видео + текст", callback_data="bcast_video"),
        InlineKeyboardButton("🔗 Кнопка-ссылка + текст", callback_data="bcast_button"),
        InlineKeyboardButton("« Отмена", callback_data="admin_back")
    )
    return markup

@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id
    if not get_user(uid): create_user(uid, message.from_user.username)
    user = get_user(uid)
    
    if uid in ADMIN_IDS:
        text = f"🚀 <b>Hosting Bot v{VERSION}</b>\n\n👑 Админ\nБот: {'🟢' if bot_status=='running' else '🔴'}\nСкриптов: {count_user_scripts(uid)}/∞\n\n<b>Админ-панель:</b>"
        bot.send_message(uid, text, reply_markup=admin_panel(), parse_mode='HTML'); return
    
    if bot_status == "stopped": bot.send_message(uid, "🔴 Бот остановлен!", parse_mode='HTML'); return
    
    sub_status, days_left = get_subscription_info(uid)
    if not check_subscription(uid) and user.get('free_used', 0) == 1:
        mx, mz = get_user_limits(uid)
        text = (
            f"🚀 <b>Hosting Bot</b>\n\n"
            f"⚠️ <b>Подписка истекла!</b>\n\n"
            f"Скриптов: {count_user_scripts(uid)}/{mx}\n"
            f"Размер: {mz} МБ\n\n"
            f"💎 <b>Продлите подписку:</b>"
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("💎 Купить подписку", callback_data="menu_tariffs"))
        bot.send_message(uid, text, reply_markup=markup, parse_mode='HTML')
        return
    
    mx, mz = get_user_limits(uid)
    mx_text = "∞" if mx == 999 else mx
    
    sub_info = ""
    if isinstance(days_left, int) and days_left > 0:
        sub_info = f"\n⏳ Осталось: {days_left} дн."
    elif isinstance(days_left, str):
        sub_info = f"\n⏳ Осталось: {days_left}"
    
    text = (
        f"🚀 <b>Hosting Bot</b>\n\n"
        f"Тариф: {sub_status}{sub_info}\n"
        f"Скриптов: {count_user_scripts(uid)}/{mx_text}\n"
        f"Размер: {mz} МБ\n\n"
        f"Отправьте .py или .zip файл!"
    )
    bot.send_message(uid, text, reply_markup=user_panel(), parse_mode='HTML')

# ========== НАВИГАЦИЯ ==========
@bot.callback_query_handler(func=lambda call: call.data == "back_to_start")
def back_to_start(call):
    if bot_blocked(call): return
    cmd_start(call.message); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_back")
def admin_back(call):
    text = f"🚀 <b>Hosting Bot v{VERSION}</b>\n\n👑 Админ\n\n<b>Админ-панель:</b>"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_panel(), parse_mode='HTML') if call.message.from_user.id in ADMIN_IDS else bot.send_message(call.message.chat.id, text, reply_markup=admin_panel(), parse_mode='HTML')
    bot.answer_callback_query(call.id)

# ========== РАССЫЛКА ==========
@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast_start(call):
    if call.from_user.id not in ADMIN_IDS: bot.answer_callback_query(call.id, "❌"); return
    bot.answer_callback_query(call.id)
    broadcast_state[call.from_user.id] = {"step": "choose_type"}
    bot.edit_message_text("📢 <b>РАССЫЛКА</b>\n\nВыберите тип рассылки:", call.message.chat.id, call.message.message_id, reply_markup=broadcast_type_keyboard(), parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data.startswith("bcast_"))
def broadcast_choose_type(call):
    if call.from_user.id not in ADMIN_IDS: return
    btype = call.data.replace("bcast_", "")
    bot.answer_callback_query(call.id)
    
    broadcast_state[call.from_user.id] = {"step": "waiting_content", "type": btype}
    
    if btype == "text":
        bot.edit_message_text("📝 Отправьте текст для рассылки:", call.message.chat.id, call.message.message_id)
    elif btype == "photo":
        bot.edit_message_text("🖼 Отправьте ФОТО + подпись к нему (отправьте фото с текстом в подписи):", call.message.chat.id, call.message.message_id)
    elif btype == "video":
        bot.edit_message_text("🎥 Отправьте ВИДЕО + подпись к нему (отправьте видео с текстом в подписи):", call.message.chat.id, call.message.message_id)
    elif btype == "button":
        msg = bot.edit_message_text("🔗 Отправьте текст и ссылку в формате:\n\nТекст сообщения\nhttps://ссылка.ком\nНазвание кнопки", call.message.chat.id, call.message.message_id)
        broadcast_state[call.from_user.id]["msg_id"] = call.message.message_id

@bot.message_handler(func=lambda message: message.from_user.id in ADMIN_IDS and broadcast_state.get(message.from_user.id, {}).get("step") == "waiting_content")
def broadcast_content(message):
    uid = message.from_user.id
    state = broadcast_state.get(uid, {})
    btype = state.get("type")
    
    if btype == "text":
        # Отправляем текст всем
        data = load_data()
        success = 0
        for user_id in data.get("users", {}):
            try:
                bot.send_message(int(user_id), message.text, parse_mode='HTML')
                success += 1
            except: pass
            time.sleep(0.05)
        bot.send_message(uid, f"📢 Рассылка завершена!\n✅ {success}/{len(data['users'])}")
        broadcast_state.pop(uid, None)
    
    elif btype == "photo":
        if not message.photo:
            bot.send_message(uid, "❌ Отправьте ФОТО!")
            return
        caption = message.caption or ""
        data = load_data()
        success = 0
        for user_id in data.get("users", {}):
            try:
                bot.send_photo(int(user_id), message.photo[-1].file_id, caption=caption, parse_mode='HTML')
                success += 1
            except: pass
            time.sleep(0.05)
        bot.send_message(uid, f"📢 Рассылка завершена!\n✅ {success}/{len(data['users'])}")
        broadcast_state.pop(uid, None)
    
    elif btype == "video":
        if not message.video:
            bot.send_message(uid, "❌ Отправьте ВИДЕО!")
            return
        caption = message.caption or ""
        data = load_data()
        success = 0
        for user_id in data.get("users", {}):
            try:
                bot.send_video(int(user_id), message.video.file_id, caption=caption, parse_mode='HTML')
                success += 1
            except: pass
            time.sleep(0.05)
        bot.send_message(uid, f"📢 Рассылка завершена!\n✅ {success}/{len(data['users'])}")
        broadcast_state.pop(uid, None)
    
    elif btype == "button":
        parts = message.text.strip().split('\n')
        if len(parts) < 2:
            bot.send_message(uid, "❌ Формат:\nТекст сообщения\nhttps://ссылка.ком\nНазвание кнопки")
            return
        
        text_msg = parts[0]
        url = parts[1]
        btn_text = parts[2] if len(parts) > 2 else "Перейти"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(btn_text, url=url))
        
        data = load_data()
        success = 0
        for user_id in data.get("users", {}):
            try:
                bot.send_message(int(user_id), text_msg, reply_markup=markup, parse_mode='HTML')
                success += 1
            except: pass
            time.sleep(0.05)
        bot.send_message(uid, f"📢 Рассылка завершена!\n✅ {success}/{len(data['users'])}")
        broadcast_state.pop(uid, None)

def load_data():
    try:
        with open("bot_data.json", 'r', encoding='utf-8') as f: return json.load(f)
    except: return {"users": {}}

# ========== ТАРИФЫ ==========
@bot.callback_query_handler(func=lambda call: call.data == "menu_tariffs")
def menu_tariffs(call):
    if bot_blocked(call): return
    bot.answer_callback_query(call.id)
    text = (
        "💎 <b>Тарифы хостинга</b>\n\n"
        "🆓 <b>Бесплатный</b> — 24 часа\n• 3 скрипта, до 5 МБ\n\n"
        "💎 <b>PRO</b>\n• 10 скриптов, до 50 МБ\n"
        f"• 7 дней — {PRO_PRICES['7d']['price_rub']}₽\n"
        f"• 30 дней — {PRO_PRICES['30d']['price_rub']}₽\n"
        f"• 180 дней — {PRO_PRICES['180d']['price_rub']}₽\n\n"
        "👑 <b>Expert</b>\n• Безлимит, до 1 ГБ\n"
        f"• 7 дней — {EXPERT_PRICES['7d']['price_rub']}₽\n"
        f"• 30 дней — {EXPERT_PRICES['30d']['price_rub']}₽\n"
        f"• 180 дней — {EXPERT_PRICES['180d']['price_rub']}₽\n\n"
        "Выберите тариф:"
    )
    bot.send_message(call.message.chat.id, text, reply_markup=tariffs_panel(), parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data == "tariff_pro_menu")
def tariff_pro_menu(call):
    if bot_blocked(call): return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "💎 <b>PRO тарифы</b>\n\n10 скриптов, до 50 МБ\n\nВыберите:", reply_markup=pro_tariffs_panel(), parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data == "tariff_expert_menu")
def tariff_expert_menu(call):
    if bot_blocked(call): return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "👑 <b>Expert тарифы</b>\n\nБезлимит, до 1 ГБ\n\nВыберите:", reply_markup=expert_tariffs_panel(), parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_pro:"))
def buy_pro(call):
    if bot_blocked(call): return
    if call.from_user.id in ADMIN_IDS:
        k = call.data.split(":")[1]; set_subscription(call.from_user.id, 'pro', PRO_PRICES[k]['days'])
        bot.answer_callback_query(call.id, f"✅ PRO {PRO_PRICES[k]['name']}!"); cmd_start(call.message); return
    k = call.data.split(":")[1]
    bot.send_message(call.message.chat.id, f"💎 PRO {PRO_PRICES[k]['name']}\n\nВыберите способ оплаты:", reply_markup=payment_method_panel('pro', k), parse_mode='HTML')
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_expert:"))
def buy_expert(call):
    if bot_blocked(call): return
    if call.from_user.id in ADMIN_IDS:
        k = call.data.split(":")[1]; set_subscription(call.from_user.id, 'expert', EXPERT_PRICES[k]['days'])
        bot.answer_callback_query(call.id, f"✅ Expert {EXPERT_PRICES[k]['name']}!"); cmd_start(call.message); return
    k = call.data.split(":")[1]
    bot.send_message(call.message.chat.id, f"👑 Expert {EXPERT_PRICES[k]['name']}\n\nВыберите способ оплаты:", reply_markup=payment_method_panel('expert', k), parse_mode='HTML')
    bot.answer_callback_query(call.id)

# ========== ОПЛАТА ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_crypto:"))
def pay_crypto(call):
    if bot_blocked(call): return
    _, pt, pk = call.data.split(":"); d = (PRO_PRICES if pt == 'pro' else EXPERT_PRICES)[pk]; pn = "PRO" if pt == 'pro' else "Expert"
    r = create_crypto_invoice(d['price_usd'], f"{pn} {d['name']}", f"crypto_{pt}_{pk}")
    if r['success']:
        crypto_invoices[r['invoice_id']] = {'user_id': call.from_user.id, 'plan_type': pt, 'plan_key': pk}
        markup = InlineKeyboardMarkup(); markup.add(InlineKeyboardButton("💎 Оплатить", url=r['url']), InlineKeyboardButton("« Назад", callback_data=f"buy_{pt}:{pk}"))
        bot.send_message(call.message.chat.id, f"💎 USDT\n\n${d['price_usd']}\nНажмите для оплаты", reply_markup=markup, parse_mode='HTML')
    else: bot.send_message(call.message.chat.id, "❌ Ошибка")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_stars:"))
def pay_stars(call):
    if bot_blocked(call): return
    _, pt, pk = call.data.split(":"); d = (PRO_PRICES if pt == 'pro' else EXPERT_PRICES)[pk]; pn = "PRO" if pt == 'pro' else "Expert"
    bot.send_invoice(chat_id=call.message.chat.id, title=f"{pn} {d['name']}", description=f"Тариф {pn}", payload=f"stars_{pt}_{pk}", provider_token="", currency="XTR", prices=[{"label": f"{pn} {d['name']}", "amount": d['price_stars']}])
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_sbp:"))
def pay_sbp(call):
    if bot_blocked(call): return
    _, pt, pk = call.data.split(":"); d = (PRO_PRICES if pt == 'pro' else EXPERT_PRICES)[pk]; pn = "PRO" if pt == 'pro' else "Expert"
    text = (
        f"💳 <b>Оплата СБП</b>\n\n"
        f"Тариф: {pn} {d['name']}\n"
        f"Сумма: {d['price_rub']} ₽\n\n"
        f"📝 <b>Реквизиты:</b>\n"
        f"💰 Номер: <code>2202206714879132</code>\n"
        f"Банк: СБЕР\n\n"
        f"📸 После оплаты отправьте скриншот чека.\n"
        f"⏳ Админ проверит и активирует тариф."
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Я оплатил", callback_data=f"confirm_pay:{pt}:{pk}:sbp"))
    markup.add(InlineKeyboardButton("« Назад", callback_data=f"buy_{pt}:{pk}"))
    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode='HTML'); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_pay:"))
def confirm_payment(call):
    if bot_blocked(call): return
    _, pt, pk, m = call.data.split(":"); pending_payments[call.from_user.id] = {'plan_type': pt, 'plan_key': pk, 'method': m}
    bot.send_message(call.message.chat.id, "📸 Отправьте скриншот оплаты", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("« Отмена", callback_data="back_to_start")), parse_mode='HTML'); bot.answer_callback_query(call.id)

# ========== ПЛАТЕЖИ ==========
@bot.pre_checkout_query_handler(func=lambda query: True)
def pre_checkout(query): bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def payment_success(message):
    p = message.successful_payment.invoice_payload
    if p.startswith("stars_"):
        _, pt, pk = p.split("_"); d = (PRO_PRICES if pt == 'pro' else EXPERT_PRICES)[pk]; pn = "PRO" if pt == 'pro' else "Expert"
        set_subscription(message.from_user.id, pt, d['days'])
        bot.send_message(message.chat.id, f"🌟 <b>Оплата получена!</b>\n\n{pn} {d['name']} активирован!", parse_mode='HTML')

@bot.message_handler(content_types=['photo'])
def handle_screenshot(message):
    if message.from_user.id in ADMIN_IDS and broadcast_state.get(message.from_user.id, {}).get("step") == "waiting_content" and broadcast_state.get(message.from_user.id, {}).get("type") == "photo":
        broadcast_content(message)
        return
    if message.from_user.id not in pending_payments: return
    pi = pending_payments.pop(message.from_user.id)
    d = (PRO_PRICES if pi['plan_type'] == 'pro' else EXPERT_PRICES)[pi['plan_key']]; pn = "PRO" if pi['plan_type'] == 'pro' else "Expert"
    for aid in ADMIN_IDS:
        markup = InlineKeyboardMarkup(row_width=2); markup.add(InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_pay:{message.from_user.id}:{pi['plan_type']}:{pi['plan_key']}"), InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_pay:{message.from_user.id}"))
        try: bot.send_photo(aid, message.photo[-1].file_id, caption=f"💰 Платёж!\n👤 {message.from_user.first_name}\n📦 {pn} {d['name']}\n💰 {d['price_rub']}₽", reply_markup=markup, parse_mode='HTML')
        except: pass
    bot.send_message(message.chat.id, "✅ Чек отправлен!", parse_mode='HTML')

@bot.message_handler(content_types=['video'])
def handle_video(message):
    if message.from_user.id in ADMIN_IDS and broadcast_state.get(message.from_user.id, {}).get("step") == "waiting_content" and broadcast_state.get(message.from_user.id, {}).get("type") == "video":
        broadcast_content(message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("approve_pay:"))
def approve_payment(call):
    if call.from_user.id not in ADMIN_IDS: return
    _, uid, pt, pk = call.data.split(":"); uid = int(uid); d = (PRO_PRICES if pt == 'pro' else EXPERT_PRICES)[pk]; pn = "PRO" if pt == 'pro' else "Expert"
    set_subscription(uid, pt, d['days'])
    try: bot.send_message(uid, f"✅ Платёж подтверждён!\n\n{pn} {d['name']} активирован!", parse_mode='HTML')
    except: pass
    bot.answer_callback_query(call.id, "✅")

@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_pay:"))
def reject_payment(call):
    if call.from_user.id not in ADMIN_IDS: return
    _, uid = call.data.split(":"); uid = int(uid)
    try: bot.send_message(uid, "❌ Платёж отклонён.", parse_mode='HTML')
    except: pass
    bot.answer_callback_query(call.id, "❌")

# ========== АДМИН ==========
@bot.callback_query_handler(func=lambda call: call.data == "admin_stop_bot")
def admin_stop_bot(call):
    if call.from_user.id not in ADMIN_IDS: return
    global bot_status; bot_status = "stopped"; stop_all_scripts()
    bot.answer_callback_query(call.id, "🔴 Бот остановлен!")
    admin_back(call)

@bot.callback_query_handler(func=lambda call: call.data == "admin_start_bot")
def admin_start_bot(call):
    if call.from_user.id not in ADMIN_IDS: return
    global bot_status; bot_status = "running"
    bot.answer_callback_query(call.id, "🟢 Бот запущен!")
    admin_back(call)

@bot.callback_query_handler(func=lambda call: call.data == "admin_start_all")
def admin_start_all(call):
    if call.from_user.id not in ADMIN_IDS: return
    n = start_all_user_scripts(); bot.answer_callback_query(call.id, f"▶️ Запущено {n}!"); admin_back(call)

@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    if call.from_user.id not in ADMIN_IDS: return
    u = get_all_users(); s = get_all_scripts(); r = len(get_all_running_scripts())
    f = sum(1 for x in u if x['subscription']=='free'); p = sum(1 for x in u if x['subscription']=='pro'); e = sum(1 for x in u if x['subscription']=='expert')
    bot.send_message(call.message.chat.id, f"📊 Статистика\n\n👥 {len(u)}\n🆓 {f} | 💎 {p} | 👑 {e}\n📦 {len(s)} | 🟢 {r}", parse_mode='HTML'); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_stop_all")
def admin_stop_all(call):
    if call.from_user.id not in ADMIN_IDS: return
    stop_all_scripts(); bot.answer_callback_query(call.id, "🛑 Всё остановлено!"); admin_back(call)

@bot.callback_query_handler(func=lambda call: call.data == "admin_users_list")
def admin_users_list(call):
    if call.from_user.id not in ADMIN_IDS: return
    u = get_all_users()
    if not u: bot.send_message(call.message.chat.id, "👥 Нет"); bot.answer_callback_query(call.id); return
    mk = InlineKeyboardMarkup(row_width=1)
    for x in u[:15]: mk.add(InlineKeyboardButton(f"{x['user_id']} @{x.get('username','?')}", callback_data=f"admin_user_scripts:{x['user_id']}"))
    mk.add(InlineKeyboardButton("« Назад", callback_data="admin_back"))
    bot.send_message(call.message.chat.id, f"👥 Пользователи ({len(u)})", reply_markup=mk, parse_mode='HTML'); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_user_scripts:"))
def admin_user_scripts(call):
    if call.from_user.id not in ADMIN_IDS: return
    uid = int(call.data.split(":")[1]); ss = get_user_scripts(uid)
    if not ss: bot.send_message(call.message.chat.id, "📭 Нет"); bot.answer_callback_query(call.id); return
    mk = InlineKeyboardMarkup(row_width=3)
    for s in ss:
        st = "🟢" if s['status']=='running' else "🔴"
        mk.add(InlineKeyboardButton("⏹", callback_data=f"admin_stop:{s['id']}"), InlineKeyboardButton("📄", callback_data=f"admin_logs:{s['id']}"), InlineKeyboardButton("🗑", callback_data=f"admin_delete:{s['id']}"))
    mk.add(InlineKeyboardButton("« Назад", callback_data="admin_users_list"))
    bot.send_message(call.message.chat.id, f"📋 Скрипты {uid}", reply_markup=mk, parse_mode='HTML'); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_logs:"))
def admin_view_logs(call):
    if call.from_user.id not in ADMIN_IDS: return
    sid = call.data.split(":")[1]; lp = os.path.join(LOGS_DIR, f"{sid}.log")
    if os.path.exists(lp):
        try:
            with open(lp, 'rb') as f: bot.send_document(call.message.chat.id, f, caption=f"📄 {sid}")
            bot.answer_callback_query(call.id, "✅")
        except: bot.answer_callback_query(call.id, "❌")
    else: bot.answer_callback_query(call.id, "❌ Нет")

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_stop:"))
def admin_stop_script(call):
    if call.from_user.id not in ADMIN_IDS: return
    sid = call.data.split(":")[1]; s = get_script(sid)
    if s and s['status']=='running': stop_script(s['pid']); update_script_status(sid, 'stopped'); bot.answer_callback_query(call.id, "✅")
    else: bot.answer_callback_query(call.id, "Уже")

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_delete:"))
def admin_delete_script(call):
    if call.from_user.id not in ADMIN_IDS: return
    sid = call.data.split(":")[1]; s = get_script(sid)
    if s:
        delete_script(sid, s['user_id']); d = os.path.join(SCRIPTS_DIR, str(s['user_id']), sid)
        if os.path.exists(d): shutil.rmtree(d)
        lp = os.path.join(LOGS_DIR, f"{sid}.log")
        if os.path.exists(lp): os.remove(lp)
        bot.answer_callback_query(call.id, "✅")
    else: bot.answer_callback_query(call.id, "❌")

@bot.callback_query_handler(func=lambda call: call.data == "admin_all_scripts")
def admin_all_scripts(call):
    if call.from_user.id not in ADMIN_IDS: return
    ss = get_all_scripts()
    if not ss: bot.send_message(call.message.chat.id, "📭"); bot.answer_callback_query(call.id); return
    mk = InlineKeyboardMarkup(row_width=2)
    for s in ss[:15]: mk.add(InlineKeyboardButton("📄", callback_data=f"admin_logs:{s['id']}"), InlineKeyboardButton("🗑", callback_data=f"admin_delete:{s['id']}"))
    mk.add(InlineKeyboardButton("« Назад", callback_data="admin_back"))
    bot.send_message(call.message.chat.id, f"📋 Все скрипты ({len(ss)})", reply_markup=mk, parse_mode='HTML'); bot.answer_callback_query(call.id)

# ========== ПОЛЬЗОВАТЕЛИ ==========
@bot.callback_query_handler(func=lambda call: call.data == "menu_upload")
def menu_upload(call):
    if bot_blocked(call): return
    if not check_subscription(call.from_user.id): bot.answer_callback_query(call.id, "❌ Подписка истекла!"); return
    bot.answer_callback_query(call.id); bot.send_message(call.message.chat.id, "📦 Отправьте .py или .zip")

@bot.callback_query_handler(func=lambda call: call.data == "menu_list")
def menu_list(call):
    if bot_blocked(call): return
    bot.answer_callback_query(call.id); show_scripts(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "menu_promo")
def menu_promo(call):
    if bot_blocked(call): return
    bot.answer_callback_query(call.id)
    if call.from_user.id in ADMIN_IDS: bot.send_message(call.message.chat.id, "👑 Не нужно"); return
    msg = bot.send_message(call.message.chat.id, "🎁 Введите промокод:"); bot.register_next_step_handler(msg, process_promo)

@bot.callback_query_handler(func=lambda call: call.data == "menu_help")
def menu_help(call):
    if bot_blocked(call): return
    bot.answer_callback_query(call.id)
    text = "📚 <b>Помощь</b>\n\n🆓 Бесплатно 24ч\n💎 PRO: 10 скриптов, 50 МБ\n👑 Expert: безлимит, 1 ГБ\n\n💳 Оплата: Крипта/Stars/СБП"
    bot.send_message(call.message.chat.id, text, parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data.startswith('script:'))
def script_action(call):
    if bot_blocked(call): return
    a, sid = call.data.split(':')[1:]
    if a == "stop":
        s = get_script(sid)
        if s and s['status']=='running': stop_script(s['pid']); update_script_status(sid, 'stopped'); bot.answer_callback_query(call.id, "✅")
        else: bot.answer_callback_query(call.id, "Уже")
    elif a == "logs":
        lp = os.path.join(LOGS_DIR, f"{sid}.log")
        if os.path.exists(lp):
            try:
                with open(lp, 'rb') as f: bot.send_document(call.message.chat.id, f, caption=f"📄 {sid}")
                bot.answer_callback_query(call.id, "✅")
            except: bot.answer_callback_query(call.id, "❌")
        else: bot.answer_callback_query(call.id, "❌ Нет")
    elif a == "delete":
        delete_script(sid, call.from_user.id); d = os.path.join(SCRIPTS_DIR, str(call.from_user.id), sid)
        if os.path.exists(d): shutil.rmtree(d)
        lp = os.path.join(LOGS_DIR, f"{sid}.log")
        if os.path.exists(lp): os.remove(lp)
        bot.answer_callback_query(call.id, "✅")
    show_scripts(call.message, edit=True)

def show_scripts(message, edit=False):
    uid = message.chat.id; scripts = get_user_scripts(uid)
    if not scripts:
        mk = InlineKeyboardMarkup(); mk.add(InlineKeyboardButton("« Назад", callback_data="back_to_start" if uid not in ADMIN_IDS else "admin_back"))
        if edit: bot.edit_message_text("📭 Нет скриптов", uid, message.message_id, reply_markup=mk)
        else: bot.send_message(uid, "📭 Нет скриптов", reply_markup=mk)
        return
    mk = InlineKeyboardMarkup(row_width=3)
    for s in scripts:
        st = "🟢" if s['status']=='running' else "🔴"
        mk.add(InlineKeyboardButton("⏹", callback_data=f"script:stop:{s['id']}"), InlineKeyboardButton("📄", callback_data=f"script:logs:{s['id']}"), InlineKeyboardButton("🗑", callback_data=f"script:delete:{s['id']}"))
    mk.add(InlineKeyboardButton("« Назад", callback_data="back_to_start" if uid not in ADMIN_IDS else "admin_back"))
    if edit: bot.edit_message_text(f"📋 Скрипты ({len(scripts)})", uid, message.message_id, reply_markup=mk, parse_mode='HTML')
    else: bot.send_message(uid, f"📋 Скрипты ({len(scripts)})", reply_markup=mk, parse_mode='HTML')

def process_promo(message):
    code = message.text.strip().upper(); success, msg = activate_promo(message.from_user.id, code)
    bot.send_message(message.chat.id, f"{'✅' if success else '❌'} {msg}")

# ========== ЗАГРУЗКА ==========
@bot.message_handler(content_types=['document'])
def handle_document(message):
    if bot_status == "stopped" and message.from_user.id not in ADMIN_IDS: bot.reply_to(message, "🔴 Остановлен!"); return
    uid = message.from_user.id
    if not get_user(uid): create_user(uid, message.from_user.username)
    if not check_subscription(uid): bot.reply_to(message, "❌ Подписка истекла! Купите тариф."); return
    if not check_user_limits(uid): bot.reply_to(message, "❌ Лимит!"); return
    fi = bot.get_file(message.document.file_id); fn = message.document.file_name; fs = message.document.file_size
    _, mx = get_user_limits(uid)
    if fs > mx * 1024 * 1024: bot.reply_to(message, f"❌ Макс {mx} МБ"); return
    td = os.path.join(TEMP_DIR, str(uid)); os.makedirs(td, exist_ok=True); tp = os.path.join(td, fn)
    try:
        dl = bot.download_file(fi.file_path)
        with open(tp, 'wb') as f: f.write(dl)
    except Exception as e: bot.reply_to(message, f"❌ {e}"); return
    sid = str(uuid.uuid4())[:8]; upload_states[uid] = {'script_id': sid, 'temp_path': tp, 'file_name': fn, 'file_size': fs}
    if fn.endswith('.zip'):
        et = os.path.join(TEMP_DIR, str(uid), sid); os.makedirs(et, exist_ok=True)
        ok, msg = extract_zip(tp, et)
        if not ok: bot.reply_to(message, f"❌ {msg}"); cleanup_temp(uid); return
        pf = find_py_files(et)
        if not pf: bot.reply_to(message, "❌ Нет .py"); cleanup_temp(uid); return
        upload_states[uid].update({'extract_to': et, 'py_files': pf})
        mk = InlineKeyboardMarkup(row_width=1)
        for f in pf: rel = os.path.relpath(f, et); mk.add(InlineKeyboardButton(rel, callback_data=f"sel:{rel}"))
        bot.send_message(uid, "📁 Выберите главный файл:", reply_markup=mk)
    else: finish_script(uid, tp, fn)

@bot.callback_query_handler(func=lambda call: call.data.startswith('sel:'))
def select_callback(call):
    uid = call.from_user.id
    if uid not in upload_states: bot.answer_callback_query(call.id, "Устарело"); return
    rel = call.data.split(':', 1)[1]; state = upload_states[uid]; et = state.get('extract_to')
    fp = os.path.join(et, rel); state['selected_main'] = rel
    bot.edit_message_text(f"✅ {rel}\nЗапускаю...", uid, call.message.message_id); bot.answer_callback_query(call.id)
    finish_script(uid, fp, state['file_name'])

def finish_script(uid, sp, ofn):
    state = upload_states.get(uid); sid = state['script_id'] if state else str(uuid.uuid4())[:8]; fs = state['file_size'] if state else os.path.getsize(sp)
    ud = os.path.join(SCRIPTS_DIR, str(uid), sid); os.makedirs(ud, exist_ok=True)
    if state and 'extract_to' in state:
        for item in os.listdir(state['extract_to']):
            s = os.path.join(state['extract_to'], item); d = os.path.join(ud, item)
            if os.path.isdir(s): shutil.copytree(s, d, dirs_exist_ok=True)
            else: shutil.copy2(s, d)
        mf = os.path.join(ud, state.get('selected_main')) if state.get('selected_main') else sp
    else: dest = os.path.join(ud, ofn); shutil.move(sp, dest); mf = dest
    add_script(sid, uid, ofn, ud, fs)
    pid, err = run_script(sid, mf)
    if err: bot.send_message(uid, f"❌ {err}"); return
    update_script_status(sid, 'running', pid)
    bot.send_message(uid, f"✅ <b>Запущен!</b>\n\nID: <code>{sid}</code>", parse_mode='HTML')
    cleanup_temp(uid)

# ========== ПРОВЕРКИ ==========
def check_crypto_payments():
    while True:
        if crypto_invoices:
            for iid, info in list(crypto_invoices.items()):
                if check_crypto_invoice(iid):
                    d = (PRO_PRICES if info['plan_type']=='pro' else EXPERT_PRICES)[info['plan_key']]; pn = "PRO" if info['plan_type']=='pro' else "Expert"
                    set_subscription(info['user_id'], info['plan_type'], d['days'])
                    try: bot.send_message(info['user_id'], f"💎 Оплата получена!\n\n{pn} {d['name']} активирован!", parse_mode='HTML')
                    except: pass
                    del crypto_invoices[iid]
        time.sleep(15)

def monitor():
    while True:
        for s in get_all_running_scripts():
            if not is_process_alive(s['pid']): update_script_status(s['id'], 'stopped')
        time.sleep(MONITOR_INTERVAL)

# ========== ЗАПУСК ==========
import json
if __name__ == '__main__':
    if os.path.exists(DATABASE_PATH): os.remove(DATABASE_PATH); print("🗑 Старая база удалена")
    init_db()
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=check_crypto_payments, daemon=True).start()
    print(f"✅ Хостинг бот v{VERSION} запущен!")
    print(f"📢 Рассылка: текст/фото/видео/кнопка")
    print(f"💳 СБП: 2202206714879132")
    bot.infinity_polling()