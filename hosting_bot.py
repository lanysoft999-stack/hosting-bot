# hosting_bot.py - Хостинг бот v15.6 (Авто-фикс БД + всё остальное)
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BotCommand, MenuButtonCommands, MessageEntity
import os
import sys
import sqlite3
import threading
import time
import uuid
import shutil
import zipfile
import subprocess
import signal
import requests
import json
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, OrderedDict
from functools import lru_cache
import atexit

# ========== ЛОГГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('hosting_bot')

# ========== КЭШ ==========
class MemoryCache:
    def __init__(self, max_size=500):
        self.cache = OrderedDict()
        self.ttl = {}
        self.max_size = max_size
        self.lock = threading.Lock()
    
    def get(self, key):
        with self.lock:
            if key in self.cache:
                if self.ttl.get(key, 0) > time.time():
                    self.cache.move_to_end(key)
                    return self.cache[key]
                else:
                    del self.cache[key]
                    del self.ttl[key]
            return None
    
    def set(self, key, value, ttl=300):
        with self.lock:
            while len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            self.cache[key] = value
            self.ttl[key] = time.time() + ttl
    
    def delete(self, key):
        with self.lock:
            if key in self.cache:
                del self.cache[key]
            if key in self.ttl:
                del self.ttl[key]

cache = MemoryCache(max_size=500)

# ========== НАСТРОЙКИ ==========
TOKEN = "8964647336:AAEk_dWa-1XrVGs2F3OSK7ZPOwUYiQg-rkc"
VERSION = "15.6.0"
ADMIN_IDS = [314148464]
CRYPTO_TOKEN = "593773:AA2SggSE9MiTxJ6jdir8g7ufY2Cd2Pchvhu"
CRYPTO_API = "https://pay.crypt.bot/api"

SUPPORT_USERNAME = "hesers"
SUPPORT_URL = "https://t.me/hesers"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
DATABASE_PATH = os.path.join(BASE_DIR, "bot_database.db")
CHANNEL_FILE = os.path.join(BASE_DIR, "required_channel.json")
PHOTOS_FILE = os.path.join(BASE_DIR, "category_photos.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "bot_settings.json")

MY_CHANNELS = []

os.makedirs(SCRIPTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# ========== СИСТЕМА ТАРИФОВ ==========
LOCATIONS = {
    "de": {"name": "Германия", "flag": "🇩🇪", "max_tiers": 3},
    "us": {"name": "США", "flag": "🇺🇸", "max_tiers": 2},
    "fi": {"name": "Финляндия", "flag": "🇫🇮", "max_tiers": 5}
}

TIER_INFO = {
    "1": {"name": "Tier 1", "price_7d": 65,  "cpu": "1 vCPU",  "ram": "512 MB",  "scripts": 3,   "speed": "⚡ Базовый",       "docker": {"cpus": "0.5", "memory": "512m",  "cpu_shares": 256,  "pids_limit": 50,  "restart_policy": "no"}},
    "2": {"name": "Tier 2", "price_7d": 100, "cpu": "2 vCPU",  "ram": "1 GB",    "scripts": 5,   "speed": "⚡⚡ Оптимальный",  "docker": {"cpus": "1.0", "memory": "1g",    "cpu_shares": 512,  "pids_limit": 100, "restart_policy": "on-failure:2"}},
    "3": {"name": "Tier 3", "price_7d": 140, "cpu": "3 vCPU",  "ram": "2 GB",    "scripts": 10,  "speed": "⚡⚡⚡ Быстрый",    "docker": {"cpus": "1.5", "memory": "2g",    "cpu_shares": 768,  "pids_limit": 200, "restart_policy": "on-failure:3"}},
    "4": {"name": "Tier 4", "price_7d": 220, "cpu": "4 vCPU",  "ram": "4 GB",    "scripts": 20,  "speed": "🔥 Турбо",         "docker": {"cpus": "2.0", "memory": "4g",    "cpu_shares": 1024, "pids_limit": 500, "restart_policy": "always"}},
    "5": {"name": "Tier 5", "price_7d": 300, "cpu": "5 vCPU",  "ram": "8 GB",    "scripts": 999, "speed": "👑 Максимальный",  "docker": {"cpus": "4.0", "memory": "8g",    "cpu_shares": 2048, "pids_limit": 2000,"restart_policy": "always"}},
}

DAYS_MULTIPLIER = {"7": 1, "30": 4, "90": 10}
DAYS_NAMES = {"7": "7 дней", "30": "30 дней", "90": "90 дней"}

def calc_price(tier, days):
    base = TIER_INFO.get(tier, {}).get("price_7d", 0)
    mult = DAYS_MULTIPLIER.get(days, 1)
    return base * mult

DOCKER_CONFIGS = {t: TIER_INFO[t]["docker"] for t in TIER_INFO}
DOCKER_CONFIGS['free'] = {'cpus': '0.5', 'memory': '128m', 'cpu_shares': 256, 'pids_limit': 50, 'restart_policy': 'no'}

FREE_MAX_SCRIPTS = 3
FREE_MAX_SIZE_MB = 5

BASIC_MAX_SCRIPTS = 3
BASIC_MAX_SIZE_MB = 10

PRO_MAX_SCRIPTS = 10
PRO_MAX_SIZE_MB = 50

EXPERT_MAX_SCRIPTS = 999
EXPERT_MAX_SIZE_MB = 1024

bot_status = "running"
pending_payments = {}
crypto_invoices = {}
broadcast_state = {}
upload_states = {}
user_config_state = {}
executor = ThreadPoolExecutor(max_workers=5)

CATEGORY_PHOTOS = {"main": None, "shop": None, "hosts": None, "deposit": None, "profile": None, "support": None}

DEFAULT_SETTINGS = {
    "welcome_text": "🚀 <b>Добро пожаловать в Hosting Bot!</b>",
    "welcome_photo": None,
    "welcome_entities": None
}

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                for key, value in DEFAULT_SETTINGS.items():
                    if key not in settings:
                        settings[key] = value
                return settings
    except Exception as e:
        logger.error(f"Error loading settings: {e}")
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        return False

bot_settings = load_settings()

def restore_entities(entities_raw):
    if not entities_raw:
        return None
    entities = []
    for e in entities_raw:
        try:
            entity = MessageEntity(type=e['type'], offset=e['offset'], length=e['length'])
            if e['type'] == 'custom_emoji' and 'custom_emoji_id' in e:
                entity.custom_emoji_id = e['custom_emoji_id']
            if e['type'] == 'text_link' and 'url' in e:
                entity.url = e['url']
            if e['type'] == 'text_mention' and 'user' in e:
                entity.user = e['user']
            entities.append(entity)
        except Exception as ex:
            logger.error(f"Error restoring entity: {ex}")
    return entities if entities else None

def save_entities(message):
    if not hasattr(message, 'entities') or not message.entities:
        return None
    entities_to_save = []
    for entity in message.entities:
        entity_dict = {'type': entity.type, 'offset': entity.offset, 'length': entity.length}
        if entity.type == 'custom_emoji' and hasattr(entity, 'custom_emoji_id'):
            entity_dict['custom_emoji_id'] = entity.custom_emoji_id
        if entity.type == 'text_link' and hasattr(entity, 'url'):
            entity_dict['url'] = entity.url
        if entity.type == 'text_mention' and hasattr(entity, 'user'):
            entity_dict['user'] = entity.user
        entities_to_save.append(entity_dict)
    return entities_to_save

def load_photos():
    try:
        if os.path.exists(PHOTOS_FILE):
            with open(PHOTOS_FILE) as f: 
                return json.load(f)
    except: 
        pass
    return CATEGORY_PHOTOS.copy()

def save_photos(data):
    try:
        with open(PHOTOS_FILE, 'w') as f: 
            json.dump(data, f, ensure_ascii=False, indent=2)
    except: 
        pass

def get_photo(cat):
    return load_photos().get(cat)

# ========== БАЗА ДАННЫХ С АВТО-ФИКСОМ ==========
def get_db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # Создаём таблицы
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0.0, 
            subscription TEXT DEFAULT 'free', subscription_expiry TIMESTAMP, 
            free_used INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            current_tier TEXT, current_location TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, 
            path TEXT NOT NULL, container_id TEXT, status TEXT DEFAULT 'stopped', 
            size INTEGER, docker_config TEXT DEFAULT 'free', 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY, type TEXT DEFAULT 'pro', days INTEGER DEFAULT 30, 
            max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0)''')
        conn.commit()
        
        # === АВТО-ИСПРАВЛЕНИЕ СТРУКТУРЫ ===
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        
        needed_columns = {
            'balance': 'REAL DEFAULT 0.0',
            'current_tier': 'TEXT',
            'current_location': 'TEXT',
            'subscription_expiry': 'TIMESTAMP',
            'free_used': 'INTEGER DEFAULT 0',
            'subscription': "TEXT DEFAULT 'free'",
            'username': 'TEXT',
            'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
        }
        
        for col_name, col_type in needed_columns.items():
            if col_name not in columns:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                    logger.info(f"Added missing column '{col_name}' to users table")
                except Exception as e:
                    logger.error(f"Failed to add {col_name}: {e}")
        
        conn.commit()
        print(f"✅ БД проверена. Колонки users: {', '.join(columns)}")

@lru_cache(maxsize=128)
def get_cached_user(uid):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone()
        if not row: 
            return None
        user = dict(row)
    if uid in ADMIN_IDS: 
        user['subscription'] = 'expert'
        user['current_tier'] = '5'
        return user
    return user

def get_user(uid):
    cached = cache.get(f"user:{uid}")
    if cached:
        return cached
    user = get_cached_user(uid)
    if user:
        cache.set(f"user:{uid}", user, 60)
    return user

def create_user(uid, username):
    with get_db() as conn:
        conn.execute('INSERT OR IGNORE INTO users (user_id, username, free_used) VALUES (?,?,0)', (uid, username))
        conn.commit()
    cache.delete(f"user:{uid}")
    get_cached_user.cache_clear()

def check_subscription(uid):
    user = get_user(uid)
    if not user: 
        return False
    if uid in ADMIN_IDS: 
        return True
    
    if user.get('subscription_expiry'):
        try:
            expiry = datetime.fromisoformat(user['subscription_expiry'])
            if datetime.now() < expiry:
                return True
            else:
                with get_db() as conn:
                    conn.execute('UPDATE users SET subscription=?, subscription_expiry=NULL, free_used=1, current_tier=NULL, current_location=NULL WHERE user_id=?', 
                               ('free', uid))
                    conn.commit()
                cache.delete(f"user:{uid}")
                get_cached_user.cache_clear()
                return False
        except Exception as e:
            logger.error(f"Check subscription error: {e}")
    
    return False

def get_subscription_info(uid):
    user = get_user(uid)
    if not user: 
        return "Нет", 0, "free"
    if uid in ADMIN_IDS: 
        return "👑 Админ", 999, "expert"
    
    sub = user.get('subscription', 'free')
    
    if user.get('subscription_expiry'):
        try:
            delta = datetime.fromisoformat(user['subscription_expiry']) - datetime.now()
            d = delta.days
            h = int(delta.seconds / 3600)
            
            if d > 0: 
                return sub.upper(), d, sub
            if h > 0: 
                return sub.upper(), f"{h}ч", sub
            
            return "Истекла", 0, 'free'
        except: 
            pass
    
    if sub == 'free' and user.get('free_used', 0) == 0: 
        return "Не активирован", 0, 'free'
    
    return "Не активна", 0, 'free'

def set_subscription(uid, plan, days=0, tier=None, location=None):
    with get_db() as conn:
        if days > 0:
            expiry = (datetime.now() + timedelta(days=days)).isoformat()
            conn.execute('UPDATE users SET subscription=?, subscription_expiry=?, free_used=1, current_tier=?, current_location=? WHERE user_id=?', 
                        (plan, expiry, tier, location, uid))
        else:
            conn.execute('UPDATE users SET subscription=?, subscription_expiry=NULL, free_used=1 WHERE user_id=?', 
                        ('free', uid))
        conn.commit()
    cache.delete(f"user:{uid}")
    get_cached_user.cache_clear()

def get_user_limits(uid):
    user = get_user(uid)
    if not user: 
        return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB
    if uid in ADMIN_IDS: 
        return EXPERT_MAX_SCRIPTS, EXPERT_MAX_SIZE_MB
    tier = user.get('current_tier')
    if tier and tier in TIER_INFO:
        return TIER_INFO[tier]['scripts'], PRO_MAX_SIZE_MB if tier in ['3','4','5'] else BASIC_MAX_SIZE_MB
    return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB

def get_docker_config(uid):
    user = get_user(uid)
    if not user: 
        return DOCKER_CONFIGS['free'], 'free'
    if uid in ADMIN_IDS: 
        return TIER_INFO['5']['docker'], 'expert'
    tier = user.get('current_tier')
    if tier and tier in TIER_INFO:
        return TIER_INFO[tier]['docker'], f'tier_{tier}'
    return DOCKER_CONFIGS['free'], 'free'

def count_user_scripts(uid):
    with get_db() as conn: 
        return conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]

def get_user_scripts(uid):
    with get_db() as conn: 
        return [dict(r) for r in conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY created_at DESC', (uid,)).fetchall()]

def get_all_scripts():
    with get_db() as conn: 
        return [dict(r) for r in conn.execute('SELECT * FROM scripts ORDER BY created_at DESC').fetchall()]

def get_all_running_scripts():
    with get_db() as conn: 
        return [dict(r) for r in conn.execute("SELECT * FROM scripts WHERE status='running'").fetchall()]

def get_script(sid):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM scripts WHERE id=?', (sid,)).fetchone()
        return dict(row) if row else None

def add_script(sid, uid, name, path, size, dc='free'):
    with get_db() as conn:
        conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?,?,?)', (sid, uid, name, path, None, 'stopped', size, dc))
        conn.commit()

def update_script_status(sid, status, cid=None):
    with get_db() as conn:
        if cid: 
            conn.execute('UPDATE scripts SET status=?, container_id=? WHERE id=?', (status, cid, sid))
        else: 
            conn.execute('UPDATE scripts SET status=? WHERE id=?', (status, sid))
        conn.commit()

def delete_script(sid, uid=None):
    with get_db() as conn:
        if uid: 
            conn.execute('DELETE FROM scripts WHERE id=? AND user_id=?', (sid, uid))
        else: 
            conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
        conn.commit()

def get_all_users():
    with get_db() as conn: 
        return [dict(r) for r in conn.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()]

def check_user_limits(uid):
    mx, _ = get_user_limits(uid)
    return True if mx == 999 else count_user_scripts(uid) < mx

def activate_promo(uid, code):
    with get_db() as conn:
        p = conn.execute('SELECT * FROM promocodes WHERE code=?', (code,)).fetchone()
        if not p: 
            return False, "Промокод не найден"
        if p['used_count'] >= p['max_uses']: 
            return False, "Закончился"
        conn.execute('UPDATE promocodes SET used_count=used_count+1 WHERE code=?', (code,))
        expiry = (datetime.now() + timedelta(days=p['days'])).isoformat()
        conn.execute('UPDATE users SET subscription=?, subscription_expiry=?, free_used=1 WHERE user_id=?', (p['type'], expiry, uid))
        conn.commit()
    cache.delete(f"user:{uid}")
    get_cached_user.cache_clear()
    return True, f"{p['type'].upper()} на {p['days']} дн!"

def is_bot_blocked(uid):
    if uid in ADMIN_IDS: 
        return False
    return bot_status == "stopped"

def check_docker():
    try: 
        return subprocess.run(['docker', '--version'], capture_output=True).returncode == 0
    except: 
        return False

def run_docker(sid, path, tariff='free'):
    if not check_docker(): 
        return run_fallback(sid, path)
    if tariff.startswith('tier_'):
        tier_num = tariff.replace('tier_', '')
        cfg = TIER_INFO.get(tier_num, TIER_INFO['1'])['docker']
    else:
        cfg = DOCKER_CONFIGS.get(tariff, DOCKER_CONFIGS['free'])
    cname = f"script_{sid}"
    subprocess.run(['docker', 'rm', '-f', cname], capture_output=True, stderr=subprocess.DEVNULL)
    mf = find_main(path)
    if not mf: 
        return None, "Нет .py"
    cmd = ['docker', 'run', '-d', '--name', cname, '--restart', cfg['restart_policy'], '--cpus', cfg['cpus'], '--memory', cfg['memory'], '--memory-swap', cfg['memory'], '--cpu-shares', str(cfg['cpu_shares']), '--pids-limit', str(cfg['pids_limit']), '-v', f'{os.path.abspath(path)}:/app:ro', '-w', '/app', 'python:3.10-slim', 'python', '-u', os.path.basename(mf)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        container_id = r.stdout.strip()[:12]
        log_file = os.path.join(LOGS_DIR, f"{sid}.log")
        threading.Thread(target=lambda: subprocess.run(['docker', 'logs', '-f', cname], stdout=open(log_file, 'w'), stderr=subprocess.STDOUT, timeout=86400), daemon=True).start()
        return container_id, None
    return None, r.stderr[:200]

def run_fallback(sid, path):
    mf = find_main(path)
    if not mf: 
        return None, "Нет .py"
    try:
        p = subprocess.Popen(['python', mf], stdout=open(os.path.join(LOGS_DIR, f"{sid}.log"), 'ab'), stderr=subprocess.STDOUT, cwd=path)
        return str(p.pid), None
    except Exception as e: 
        return None, str(e)

def stop_container(cid):
    try:
        subprocess.run(['docker', 'stop', cid], capture_output=True, timeout=10)
        subprocess.run(['docker', 'rm', '-f', cid], capture_output=True)
        return True
    except: 
        return False

def is_alive(cid):
    try: 
        return subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', cid], capture_output=True, text=True).stdout.strip() == 'true'
    except: 
        return False

def stop_all():
    for s in get_all_running_scripts():
        if s.get('container_id'): 
            stop_container(s['container_id'])
        update_script_status(s['id'], 'stopped')

def start_all():
    n = 0
    for s in get_all_scripts():
        if s['status'] == 'stopped':
            mf = find_main(s['path'])
            if mf:
                cid, _ = run_docker(s['id'], s['path'], s.get('docker_config', 'free'))
                if cid: 
                    update_script_status(s['id'], 'running', cid)
                    n += 1
    return n

def find_main(d):
    for f in os.listdir(d) if os.path.isdir(d) else []:
        if f.endswith('.py'): 
            return os.path.join(d, f)
    return None

def find_py_files(d):
    py_files = []
    for r, _, fs in os.walk(d):
        for f in fs:
            if f.endswith('.py'):
                py_files.append(os.path.join(r, f))
    return py_files

def extract_zip(zp, et):
    try:
        with zipfile.ZipFile(zp) as z: 
            z.extractall(et)
        return True, None
    except Exception as e: 
        return False, str(e)

def cleanup(uid):
    d = os.path.join(TEMP_DIR, str(uid))
    if os.path.exists(d): 
        shutil.rmtree(d, ignore_errors=True)

def get_user_stats(uid):
    scripts = get_user_scripts(uid)
    total = len(scripts)
    running = len([s for s in scripts if s['status'] == 'running'])
    stopped = len([s for s in scripts if s['status'] == 'stopped'])
    uptime = 100.0
    if total > 0:
        uptime = round((running / total) * 100, 1)
    return {'total': total, 'running': running, 'stopped': stopped, 'uptime': uptime}

def get_all_stats():
    scripts = get_all_scripts()
    running = len(get_all_running_scripts())
    users = get_all_users()
    return {'total_scripts': len(scripts), 'running': running, 'stopped': len(scripts) - running, 'total_users': len(users)}

def load_channels():
    try:
        if os.path.exists(CHANNEL_FILE):
            with open(CHANNEL_FILE) as f:
                data = json.load(f)
            return data
    except: 
        pass
    return {"channels": [], "welcome_text": "🔒 Подпишитесь на канал!", "welcome_photo": None}

def save_channels(data):
    try:
        with open(CHANNEL_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def check_subscribed(uid):
    data = load_channels()
    channels = data.get("channels", [])
    if not channels or uid in ADMIN_IDS: 
        return True
    for ch in channels:
        try:
            if bot.get_chat_member(ch['id'], uid).status in ["left", "kicked"]: 
                return False
        except: 
            return False
    return True

def channel_keyboard():
    data = load_channels()
    channels = data.get("channels", [])
    if not channels: 
        return InlineKeyboardMarkup()
    mk = InlineKeyboardMarkup(row_width=1)
    for ch in channels: 
        mk.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))
    mk.add(InlineKeyboardButton("✅ Я подписался", callback_data="check_sub"))
    return mk

def create_invoice(amt, desc, payload):
    try:
        r = requests.post(f"{CRYPTO_API}/createInvoice", json={"asset":"USDT","amount":str(amt),"description":desc,"payload":payload}, headers={"Crypto-Pay-API-Token":CRYPTO_TOKEN}, timeout=10).json()
        if r.get('ok'): 
            return {'success':True, 'id':r['result']['invoice_id'], 'url':r['result']['bot_invoice_url']}
    except: 
        pass
    return {'success':False}

def check_invoice(iid):
    try:
        r = requests.get(f"{CRYPTO_API}/getInvoices", params={"invoice_ids":iid}, headers={"Crypto-Pay-API-Token":CRYPTO_TOKEN}, timeout=10).json()
        if r.get('ok') and r['result']['items']: 
            return r['result']['items'][0]['status'] == 'paid'
    except: 
        pass
    return False

# ========== БОТ ==========
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')

def setup_menu():
    bot.set_my_commands([BotCommand("start", "🚀 Главное меню")])
    bot.set_chat_menu_button(menu_button=MenuButtonCommands())

def user_keyboard():
    mk = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    mk.add(KeyboardButton("🛒 Магазин"), KeyboardButton("💻 Мои хосты"))
    mk.add(KeyboardButton("💳 Пополнить"), KeyboardButton("👤 Профиль"))
    mk.add(KeyboardButton("🆘 Поддержка"))
    return mk

def admin_keyboard():
    mk = ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    mk.add(KeyboardButton("📊 Стата"), KeyboardButton("👥 Юзеры"), KeyboardButton("📦 Хосты"))
    mk.add(KeyboardButton("📢 Канал"), KeyboardButton("🐳 Docker"), KeyboardButton("📨 Спам"))
    mk.add(KeyboardButton("🖼 Фото"), KeyboardButton("⚙️ Приветствие"), KeyboardButton("🎫 Промо"))
    mk.add(KeyboardButton("🔒 Подписка"), KeyboardButton("🖼 Фото ХС"), KeyboardButton("🛑 СТОП" if bot_status=="running" else "🟢 СТАРТ"))
    mk.add(KeyboardButton("⏹ Всё стоп"), KeyboardButton("▶️ Всё старт"))
    return mk

def send_with_photo(uid, cat, text, markup=None):
    photo = get_photo(cat)
    if photo:
        try:
            bot.send_photo(uid, photo, caption=text, reply_markup=markup)
            return True
        except: 
            pass
    bot.send_message(uid, text, reply_markup=markup)
    return False

# ========== КЛАВИАТУРА КОНСТРУКТОРА ==========
def configurator_keyboard(uid):
    state = user_config_state.get(uid, {"location": None, "tier": None, "days": None})
    location = state.get("location")
    tier = state.get("tier")
    days = state.get("days")
    
    max_tier = LOCATIONS[location]['max_tiers'] if location else 5
    
    kb = InlineKeyboardMarkup(row_width=3)
    
    loc_buttons = []
    for loc_id, loc_data in LOCATIONS.items():
        prefix = "✅ " if location == loc_id else ""
        loc_buttons.append(InlineKeyboardButton(f"{prefix}{loc_data['flag']} {loc_data['name']}", callback_data=f"cfg_loc:{loc_id}"))
    kb.add(*loc_buttons)
    
    tier_buttons_row1 = []
    tier_buttons_row2 = []
    
    for t_id in ["1", "2", "3"]:
        if int(t_id) <= max_tier:
            t_data = TIER_INFO[t_id]
            prefix = "✅ " if tier == t_id else ""
            tier_buttons_row1.append(InlineKeyboardButton(f"{prefix}{t_data['name']} ({t_data['price_7d']}₽)", callback_data=f"cfg_tier:{t_id}"))
    
    for t_id in ["4", "5"]:
        if int(t_id) <= max_tier:
            t_data = TIER_INFO[t_id]
            prefix = "✅ " if tier == t_id else ""
            tier_buttons_row2.append(InlineKeyboardButton(f"{prefix}{t_data['name']} ({t_data['price_7d']}₽)", callback_data=f"cfg_tier:{t_id}"))
    
    if tier_buttons_row1:
        kb.add(*tier_buttons_row1)
    if tier_buttons_row2:
        kb.add(*tier_buttons_row2)
    
    if tier:
        days_buttons = []
        for d_id, d_name in DAYS_NAMES.items():
            prefix = "✅ " if days == d_id else ""
            price_text = f" ({calc_price(tier, d_id)}₽)"
            days_buttons.append(InlineKeyboardButton(f"{prefix}{d_name}{price_text}", callback_data=f"cfg_days:{d_id}"))
        kb.add(*days_buttons)
    
    if location and tier and days:
        total = calc_price(tier, days)
        kb.add(InlineKeyboardButton(f"💰 Оплатить — {total}₽", callback_data="cfg_pay"))
    
    kb.add(InlineKeyboardButton("« В магазин", callback_data="cfg_back"))
    
    return kb, state

def get_config_description(state):
    location = state.get("location")
    tier = state.get("tier")
    days = state.get("days")
    
    text = "📦 <b>ХОСТ-СЕРВИС</b>\n\n"
    text += "📍 <b>Локация:</b> "
    text += f"{LOCATIONS[location]['flag']} {LOCATIONS[location]['name']}" if location else "❌ Не выбрана"
    text += "\n━━━━━━━━━━━━━━━━━━━━━━\n⚙️ <b>Тариф:</b> "
    if tier:
        t = TIER_INFO[tier]
        scripts = "∞" if t['scripts'] == 999 else str(t['scripts'])
        text += f"\n📦 {t['name']}: {t['ram']} RAM, {t['cpu']}\n📜 Скриптов: {scripts}\n🚀 {t['speed']}"
    else:
        text += "❌ Не выбран"
        if location:
            max_t = LOCATIONS[location]['max_tiers']
            text += f"\n<i>Доступно Tier 1-{max_t}</i>"
    text += "\n━━━━━━━━━━━━━━━━━━━━━━\n⏳ <b>Срок:</b> "
    if tier and days:
        text += f"{DAYS_NAMES[days]} — {calc_price(tier, days)}₽"
    else:
        text += "❌ Не выбран"
    text += "\n"
    if location and tier and days:
        text += f"━━━━━━━━━━━━━━━━━━━━━━\n💰 <b>ИТОГО: {calc_price(tier, days)}₽</b>\n"
    text += "\n👇 <i>Выберите параметры:</i>"
    return text

# ========== СТАРТ ==========
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id
    if not get_user(uid): 
        create_user(uid, message.from_user.username)
    user_config_state.pop(uid, None)
    if not check_subscribed(uid):
        data = load_channels()
        welcome_photo = data.get('welcome_photo')
        welcome_text = data.get('welcome_text', '🔒 Подпишитесь на канал!')
        if welcome_photo:
            try:
                bot.send_photo(uid, welcome_photo, caption=welcome_text, reply_markup=channel_keyboard())
            except:
                bot.send_message(uid, welcome_text, reply_markup=channel_keyboard())
        else:
            bot.send_message(uid, welcome_text, reply_markup=channel_keyboard())
        return
    user = get_user(uid)
    settings = load_settings()
    welcome_photo = settings.get('welcome_photo')
    if uid in ADMIN_IDS:
        all_stats = get_all_stats()
        stats_text = f"👑 <b>АДМИН</b>\n\n👥 Пользователей: {all_stats['total_users']}\n📦 Скриптов: {all_stats['total_scripts']}\n🟢 Запущено: {all_stats['running']}\n\n👇 <b>Действие:</b>"
        if welcome_photo:
            try:
                bot.send_photo(uid, welcome_photo, caption=stats_text, reply_markup=admin_keyboard())
            except:
                bot.send_message(uid, stats_text, reply_markup=admin_keyboard())
        else:
            bot.send_message(uid, stats_text, reply_markup=admin_keyboard())
        return
    if bot_status == "stopped":
        bot.send_message(uid, "🔴 <b>Бот остановлен!</b>", reply_markup=user_keyboard())
        return
    if not check_subscription(uid):
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("🛒 В магазин", callback_data="shop_configurator"))
        bot.send_message(uid, "🚀 <b>Hosting Bot</b>\n\n⚠️ Активируйте тариф!\n\n🇩🇪 Германия — Tier 1-3\n🇺🇸 США — Tier 1-2\n🇫🇮 Финляндия — Tier 1-5", reply_markup=mk)
        return
    stats = get_user_stats(uid)
    stats_text = f"📊 <b>СТАТИСТИКА:</b>\n✅ Аптайм: {stats['uptime']}%\n🟢 Запущено: {stats['running']}\n🔴 Упало: {stats['stopped']}\n\n👇 <b>Действие:</b>"
    if welcome_photo:
        try:
            bot.send_photo(uid, welcome_photo, caption=stats_text, reply_markup=user_keyboard())
        except:
            send_with_photo(uid, "main", stats_text, user_keyboard())
    else:
        send_with_photo(uid, "main", stats_text, user_keyboard())

# ========== МАГАЗИН ==========
@bot.message_handler(func=lambda m: m.text == "🛒 Магазин")
def shop_menu(message):
    uid = message.from_user.id
    if is_bot_blocked(uid): 
        bot.send_message(uid, "🔴 Бот остановлен!", reply_markup=user_keyboard())
        return
    user_config_state.pop(uid, None)
    text = "🛒 <b>МАГАЗИН</b>\n\nВыберите категорию:\n\n📦 <b>Хост-сервис</b> — тарифы на хостинг\n\n👇 <i>Нажмите на кнопку ниже:</i>"
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(InlineKeyboardButton("🖥 Хост-сервис", callback_data="shop_configurator"))
    send_with_photo(uid, "shop", text, mk)

@bot.callback_query_handler(func=lambda c: c.data == "shop_configurator")
def open_configurator(call):
    uid = call.from_user.id
    if is_bot_blocked(uid): 
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!", show_alert=True)
        return
    user_config_state[uid] = {"location": None, "tier": None, "days": None}
    kb, state = configurator_keyboard(uid)
    text = get_config_description(state)
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    except:
        bot.send_message(call.message.chat.id, text, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cfg_loc:"))
def config_select_location(call):
    uid = call.from_user.id
    if is_bot_blocked(uid): 
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!", show_alert=True)
        return
    loc = call.data.split(":")[1]
    if uid not in user_config_state:
        user_config_state[uid] = {"location": None, "tier": None, "days": None}
    user_config_state[uid]["location"] = loc
    user_config_state[uid]["tier"] = None
    user_config_state[uid]["days"] = None
    kb, state = configurator_keyboard(uid)
    text = get_config_description(state)
    bot.answer_callback_query(call.id)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cfg_tier:"))
def config_select_tier(call):
    uid = call.from_user.id
    if is_bot_blocked(uid): 
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!", show_alert=True)
        return
    tier = call.data.split(":")[1]
    if uid not in user_config_state:
        user_config_state[uid] = {"location": None, "tier": None, "days": None}
    user_config_state[uid]["tier"] = tier
    user_config_state[uid]["days"] = None
    kb, state = configurator_keyboard(uid)
    text = get_config_description(state)
    bot.answer_callback_query(call.id)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cfg_days:"))
def config_select_days(call):
    uid = call.from_user.id
    if is_bot_blocked(uid): 
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!", show_alert=True)
        return
    days = call.data.split(":")[1]
    if uid not in user_config_state:
        user_config_state[uid] = {"location": None, "tier": None, "days": None}
    user_config_state[uid]["days"] = days
    kb, state = configurator_keyboard(uid)
    text = get_config_description(state)
    bot.answer_callback_query(call.id)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "cfg_pay")
def config_pay(call):
    uid = call.from_user.id
    if is_bot_blocked(uid): 
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!", show_alert=True)
        return
    state = user_config_state.get(uid)
    if not state or not state['location'] or not state['tier'] or not state['days']:
        bot.answer_callback_query(call.id, "❌ Выберите все параметры!", show_alert=True)
        return
    tier = state['tier']
    days = state['days']
    location = state['location']
    total = calc_price(tier, days)
    user = get_user(uid)
    balance = user.get('balance', 0) if user else 0
    text = f"🧾 <b>ПОДТВЕРЖДЕНИЕ</b>\n\n📍 {LOCATIONS[location]['name']}\n📦 {TIER_INFO[tier]['name']}\n📅 {DAYS_NAMES[days]}\n💰 Итого: {total}₽\n💳 Баланс: {balance}₽\n\n👇 Оплата:"
    mk = InlineKeyboardMarkup(row_width=1)
    if balance >= total:
        mk.add(InlineKeyboardButton(f"💳 Оплатить с баланса ({balance}₽)", callback_data=f"cfg_dopay:balance"))
    else:
        mk.add(InlineKeyboardButton(f"💳 Недостаточно ({balance}₽/{total}₽)", callback_data="noop"))
    mk.add(InlineKeyboardButton("« Назад", callback_data="shop_configurator"))
    bot.answer_callback_query(call.id)
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cfg_dopay:"))
def config_do_payment(call):
    uid = call.from_user.id
    if is_bot_blocked(uid): 
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!", show_alert=True)
        return
    state = user_config_state.get(uid, {})
    tier = state.get('tier', '1')
    days = state.get('days', '7')
    location = state.get('location', 'de')
    total = calc_price(tier, days)
    bot.answer_callback_query(call.id)
    user = get_user(uid)
    balance = user.get('balance', 0) if user else 0
    if balance < total:
        bot.send_message(uid, f"❌ Недостаточно средств!\n💰 Баланс: {balance}₽\n💳 Нужно: {total}₽")
        return
    with get_db() as conn:
        conn.execute('UPDATE users SET balance=balance-? WHERE user_id=?', (total, uid))
        conn.commit()
    cache.delete(f"user:{uid}")
    get_cached_user.cache_clear()
    days_int = 7 if days == '7' else 30 if days == '30' else 90
    sub_type = 'basic' if tier in ['1','2'] else 'pro' if tier in ['3','4'] else 'expert'
    set_subscription(uid, sub_type, days_int, tier, location)
    new_balance = get_user(uid).get('balance', 0)
    bot.send_message(uid, f"✅ <b>Оплачено!</b>\n\n📦 {TIER_INFO[tier]['name']}\n📅 {DAYS_NAMES[days]}\n💰 Списано: {total}₽\n💳 Остаток: {new_balance}₽\n\nТариф активирован!", reply_markup=user_keyboard())

@bot.callback_query_handler(func=lambda c: c.data == "cfg_back")
def config_back(call):
    shop_menu(call.message)
    bot.answer_callback_query(call.id)

# ========== ПРОМОКОДЫ (из профиля) ==========
@bot.callback_query_handler(func=lambda c: c.data == "profile_promo")
def profile_promo(call):
    uid = call.from_user.id
    if is_bot_blocked(uid): 
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(uid, "🎁 Введите промокод:\n\n❌ /cancel")
    bot.register_next_step_handler(msg, process_promo_code)

def process_promo_code(message):
    uid = message.from_user.id
    if not message.text or message.text == '/cancel':
        bot.send_message(uid, "❌ Отменено", reply_markup=user_keyboard())
        return
    code = message.text.strip().upper()
    success, msg = activate_promo(uid, code)
    bot.send_message(uid, f"{'✅' if success else '❌'} {msg}", reply_markup=user_keyboard())

# ========== ОСТАЛЬНЫЕ КНОПКИ ==========
@bot.message_handler(func=lambda m: m.text == "💻 Мои хосты")
def btn_hosts(m):
    uid = m.from_user.id
    if is_bot_blocked(uid): 
        bot.send_message(uid, "🔴 Бот остановлен!", reply_markup=user_keyboard())
        return
    if not check_subscription(uid): 
        bot.send_message(uid, "❌ Нет тарифа!", reply_markup=user_keyboard())
        return
    scripts = get_user_scripts(uid)
    if not scripts: 
        bot.send_message(uid, "😔 Нет хостов.", reply_markup=user_keyboard())
        return
    cfg, _ = get_docker_config(uid)
    text = f"💻 <b>Хосты ({len(scripts)})</b>\n🐳 {cfg.get('cpus','?')} CPU | {cfg.get('memory','?')} RAM\n\n"
    mk = InlineKeyboardMarkup(row_width=3)
    for i, s in enumerate(scripts,1):
        st = "🟢" if s['status']=='running' else "🔴"
        sz = s['size']/(1024*1024) if s['size'] else 0
        text += f"{i}. {st} {s['name']} ({sz:.1f}МБ)\n"
        mk.add(InlineKeyboardButton("⏹", callback_data=f"sc:stop:{s['id']}"), InlineKeyboardButton("📄", callback_data=f"sc:log:{s['id']}"), InlineKeyboardButton("🗑", callback_data=f"sc:del:{s['id']}"))
    send_with_photo(uid, "hosts", text, mk)

@bot.message_handler(func=lambda m: m.text == "💳 Пополнить")
def btn_deposit(m):
    uid = m.from_user.id
    if is_bot_blocked(uid): 
        bot.send_message(uid, "🔴 Бот остановлен!", reply_markup=user_keyboard())
        return
    text = "💳 <b>ПОПОЛНЕНИЕ</b>\n\n💰 СБП\n💎 Crypto USDT\n⭐ Stars\n\n👇 Выберите:"
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(InlineKeyboardButton("💰 СБП", callback_data="dep_rub"), InlineKeyboardButton("💎 Crypto", callback_data="dep_crypto"), InlineKeyboardButton("⭐ Stars", callback_data="dep_stars"))
    send_with_photo(uid, "deposit", text, mk)

@bot.callback_query_handler(func=lambda c: c.data.startswith("dep_"))
def deposit_method(call):
    uid = call.from_user.id
    if is_bot_blocked(uid): 
        bot.answer_callback_query(call.id, "🔴 Бот остановлен!", show_alert=True)
        return
    method = call.data.replace("dep_", "")
    bot.answer_callback_query(call.id)
    if method == "rub":
        msg = bot.send_message(uid, "💰 Введите сумму (мин 50₽):")
        bot.register_next_step_handler(msg, process_deposit_rub)
    elif method == "crypto":
        msg = bot.send_message(uid, "💎 Введите сумму (мин $1):")
        bot.register_next_step_handler(msg, process_deposit_crypto)
    elif method == "stars":
        msg = bot.send_message(uid, "⭐ Введите сумму (мин 50⭐):")
        bot.register_next_step_handler(msg, process_deposit_stars)

def process_deposit_rub(message):
    uid = message.from_user.id
    try:
        amount = int(message.text)
        if amount < 50: bot.send_message(uid, "❌ Мин 50₽!"); return
    except: bot.send_message(uid, "❌ Число!"); return
    pending_payments[uid] = {'type': 'balance', 'amount': amount}
    mk = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Я оплатил", callback_data="confirm_sbp"))
    bot.send_message(uid, f"💰 СБП\n\n💳 <code>2202206714879132</code>\n🏦 СБЕР\n💰 {amount}₽\n\n📸 Скриншот:", reply_markup=mk)

def process_deposit_crypto(message):
    uid = message.from_user.id
    try:
        amount = float(message.text.replace(',', '.'))
        if amount < 1: bot.send_message(uid, "❌ Мин $1!"); return
    except: bot.send_message(uid, "❌ Число!"); return
    r = create_invoice(amount, "Пополнение", f"bal_{uid}_{amount}")
    if r['success']:
        crypto_invoices[r['id']] = {'uid': uid, 'type': 'balance', 'amount': amount}
        mk = InlineKeyboardMarkup().add(InlineKeyboardButton("💎 Оплатить", url=r['url']))
        bot.send_message(uid, f"💎 Счёт создан!\n💰 ${amount:.2f}", reply_markup=mk)
    else:
        bot.send_message(uid, "❌ Ошибка")

def process_deposit_stars(message):
    uid = message.from_user.id
    if not message.text.isdigit() or int(message.text) < 50:
        bot.send_message(uid, "❌ Мин 50⭐!"); return
    amount = int(message.text)
    bot.send_invoice(uid, "Пополнение", f"+{amount}⭐", f"bal_stars_{amount}", "", "XTR", [{"label":f"Пополнение {amount}⭐", "amount":amount}])

@bot.callback_query_handler(func=lambda c: c.data == "confirm_sbp")
def confirm_sbp(call):
    if call.from_user.id not in pending_payments:
        bot.answer_callback_query(call.id, "❌ Нет платежа"); return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📸 Отправьте скриншот:")

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def btn_profile(m):
    uid = m.from_user.id
    if is_bot_blocked(uid): 
        bot.send_message(uid, "🔴 Бот остановлен!", reply_markup=user_keyboard())
        return
    user = get_user(uid)
    if not user: 
        bot.send_message(uid, "❌ /start", reply_markup=user_keyboard())
        return
    
    sub, dl, _ = get_subscription_info(uid)
    active = "✅ Активна" if check_subscription(uid) else "❌ Не активна"
    ref = f"https://t.me/{bot.get_me().username}?start=ref{uid}"
    
    user_tier = user.get('current_tier')
    tier_info = ""
    if user_tier and user_tier in TIER_INFO:
        t = TIER_INFO[user_tier]
        tier_info = f"\n📦 Тариф: {t['name']}\n⚙️ {t['cpu']} | 💾 {t['ram']}"
    
    user_loc = user.get('current_location')
    loc_info = ""
    if user_loc and user_loc in LOCATIONS:
        loc_info = f"\n📍 Локация: {LOCATIONS[user_loc]['flag']} {LOCATIONS[user_loc]['name']}"
    
    expiry_info = ""
    if user.get('subscription_expiry'):
        try:
            delta = datetime.fromisoformat(user['subscription_expiry']) - datetime.now()
            d = delta.days
            h = int(delta.seconds / 3600)
            if d > 0:
                expiry_info = f"\n⏳ Осталось: {d} дн."
            elif h > 0:
                expiry_info = f"\n⏳ Осталось: {h} ч."
        except:
            pass
    
    text = (
        f"👤 <b>ЛИЧНЫЙ КАБИНЕТ</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"💰 Баланс: {user.get('balance',0)} ₽\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>ПОДПИСКА</b>\n"
        f"📊 Статус: {active}\n"
        f"🎫 Тариф: {sub}{tier_info}{loc_info}{expiry_info}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref}</code>"
    )
    
    mk = InlineKeyboardMarkup()
    mk.add(InlineKeyboardButton("🎁 Активировать промокод", callback_data="profile_promo"))
    
    send_with_photo(uid, "profile", text, mk)

@bot.message_handler(func=lambda m: m.text == "🆘 Поддержка")
def btn_support(m):
    uid = m.from_user.id
    if is_bot_blocked(uid): 
        bot.send_message(uid, "🔴 Бот остановлен!", reply_markup=user_keyboard())
        return
    mk = InlineKeyboardMarkup().add(InlineKeyboardButton("💬 @hesers", url=SUPPORT_URL))
    send_with_photo(uid, "support", "📬 <b>Поддержка</b>", mk)

# ========== АДМИН-ПАНЕЛЬ ==========
@bot.message_handler(func=lambda m: m.text == "📊 Стата" and m.from_user.id in ADMIN_IDS)
def admin_btn_stats(m):
    u = get_all_users(); s = get_all_scripts(); r = len(get_all_running_scripts())
    bot.send_message(m.chat.id, f"📊 Стата\n👥 {len(u)}\n📦 {len(s)}\n🟢 {r}\n🐳 {'✅' if check_docker() else '❌'}")

@bot.message_handler(func=lambda m: m.text == "👥 Юзеры" and m.from_user.id in ADMIN_IDS)
def admin_btn_users(m):
    u = get_all_users()
    if not u: bot.send_message(m.chat.id, "👥 Нет"); return
    text = f"👥 {len(u)}\n"
    for x in u[:20]: text += f"• <code>{x['user_id']}</code> @{x.get('username','?')}\n"
    bot.send_message(m.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "📦 Хосты" and m.from_user.id in ADMIN_IDS)
def admin_btn_hosts(m):
    ss = get_all_scripts()
    if not ss: bot.send_message(m.chat.id, "📭 Нет"); return
    mk = InlineKeyboardMarkup(row_width=2)
    for s in ss[:15]: mk.add(InlineKeyboardButton(f"🗑 {s['id'][:8]}", callback_data=f"sc:del:{s['id']}"))
    bot.send_message(m.chat.id, f"📦 {len(ss)}", reply_markup=mk)

@bot.message_handler(func=lambda m: m.text == "📢 Канал" and m.from_user.id in ADMIN_IDS)
def admin_btn_channel(m):
    channels = load_channels().get("channels",[])
    text = "📢 Каналы\n\n" + ("\n".join([f"{c['name']} ({c['id']})" for c in channels]) if channels else "Нет") + "\n\nОтправь @username"
    bot.send_message(m.chat.id, text)
    bot.register_next_step_handler(m, add_channel_admin)

def add_channel_admin(message):
    if message.from_user.id not in ADMIN_IDS: return
    ch = message.text.strip()
    if not ch.startswith("@"): ch = "@"+ch
    try:
        chat = bot.get_chat(ch)
        data = load_channels()
        if "channels" not in data: data["channels"] = []
        data["channels"].append({"id":ch,"name":chat.title,"url":f"https://t.me/{ch[1:]}"})
        save_channels(data)
        bot.send_message(message.chat.id, f"✅ {chat.title}")
    except Exception as e: bot.send_message(message.chat.id, f"❌ {e}")

@bot.message_handler(func=lambda m: m.text == "🐳 Docker" and m.from_user.id in ADMIN_IDS)
def admin_btn_docker(m):
    if check_docker():
        r = subprocess.run(['docker','ps','--format','{{.Names}}'], capture_output=True, text=True)
        bot.send_message(m.chat.id, f"🐳 Docker\n✅ Контейнеров: {len(r.stdout.strip().split(chr(10)))}")
    else: bot.send_message(m.chat.id, "🐳 Docker\n❌ Не установлен")

@bot.message_handler(func=lambda m: m.text == "📨 Спам" and m.from_user.id in ADMIN_IDS)
def admin_btn_spam(m):
    broadcast_state[m.from_user.id] = {"step": "choose_type"}
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(InlineKeyboardButton("📝 Текст", callback_data="bcast_text"), InlineKeyboardButton("🖼 Фото", callback_data="bcast_photo"), InlineKeyboardButton("« Отмена", callback_data="admin_cancel"))
    bot.send_message(m.chat.id, "📨 Рассылка\nТип:", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data == "admin_cancel")
def admin_cancel(call):
    if call.from_user.id in ADMIN_IDS:
        broadcast_state.pop(call.from_user.id, None)
        bot.edit_message_text("❌ Отменено", call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("bcast_"))
def bcast_type(call):
    if call.from_user.id not in ADMIN_IDS: return
    t = call.data.replace("bcast_", "")
    broadcast_state[call.from_user.id] = {"step": "waiting", "type": t}
    bot.edit_message_text("📝 Текст:" if t=="text" else "🖼 Фото:", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and broadcast_state.get(m.from_user.id, {}).get("step") == "waiting")
def bcast_send(message):
    uid = message.from_user.id; state = broadcast_state.pop(uid)
    users = get_all_users(); success = 0
    if state['type'] == 'text':
        for u in users:
            try: bot.send_message(u['user_id'], message.text); success += 1
            except: pass
            time.sleep(0.05)
    elif state['type'] == 'photo' and message.photo:
        for u in users:
            try: bot.send_photo(u['user_id'], message.photo[-1].file_id, caption=message.caption); success += 1
            except: pass
            time.sleep(0.05)
    bot.send_message(message.chat.id, f"📨 Готово!\n✅ {success}/{len(users)}")

@bot.message_handler(func=lambda m: m.text in ["🛑 СТОП", "🟢 СТАРТ"] and m.from_user.id in ADMIN_IDS)
def admin_btn_toggle(m):
    global bot_status
    if m.text == "🛑 СТОП":
        bot_status = "stopped"
        stop_all()
        bot.send_message(m.chat.id, "🔴 <b>Бот остановлен!</b>\n\nВсе скрипты остановлены.", reply_markup=admin_keyboard())
    else:
        bot_status = "running"
        bot.send_message(m.chat.id, "🟢 <b>Бот запущен!</b>\n\nПользователи могут работать.", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda m: m.text == "⏹ Всё стоп" and m.from_user.id in ADMIN_IDS)
def admin_btn_stop_all(m): stop_all(); bot.send_message(m.chat.id, "🛑 Всё стоп!")

@bot.message_handler(func=lambda m: m.text == "▶️ Всё старт" and m.from_user.id in ADMIN_IDS)
def admin_btn_start_all(m): n = start_all(); bot.send_message(m.chat.id, f"▶️ +{n}!")

@bot.message_handler(func=lambda m: m.text == "⚙️ Приветствие" and m.from_user.id in ADMIN_IDS)
def admin_greeting_menu(m):
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(InlineKeyboardButton("🖼 Фото", callback_data="set_welcome_photo"), InlineKeyboardButton("📝 Текст", callback_data="set_welcome_text"), InlineKeyboardButton("🔄 Сброс", callback_data="reset_welcome"))
    bot.send_message(m.chat.id, "⚙️ Приветствие", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data == "set_welcome_photo")
def set_welcome_photo(call):
    if call.from_user.id not in ADMIN_IDS: return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🖼 Фото:\n❌ /skip")
    bot.register_next_step_handler(msg, lambda m: save_welcome_photo(m) if not (m.text and m.text=='/skip') else None)

def save_welcome_photo(message):
    if not message.photo: bot.send_message(message.chat.id, "❌ Фото!"); return
    settings = load_settings(); settings['welcome_photo'] = message.photo[-1].file_id
    if save_settings(settings): global bot_settings; bot_settings = settings; bot.send_message(message.chat.id, "✅")

@bot.callback_query_handler(func=lambda c: c.data == "set_welcome_text")
def set_welcome_text(call):
    if call.from_user.id not in ADMIN_IDS: return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📝 Текст:\n❌ /skip")
    bot.register_next_step_handler(msg, lambda m: save_welcome_text(m) if not (m.text and m.text=='/skip') else None)

def save_welcome_text(message):
    settings = load_settings(); settings['welcome_text'] = message.text or ""
    if save_settings(settings): global bot_settings; bot_settings = settings; bot.send_message(message.chat.id, "✅")

@bot.callback_query_handler(func=lambda c: c.data == "reset_welcome")
def reset_welcome(call):
    if call.from_user.id not in ADMIN_IDS: return
    settings = load_settings(); settings['welcome_text'] = DEFAULT_SETTINGS['welcome_text']; settings['welcome_photo'] = None
    if save_settings(settings): global bot_settings; bot_settings = settings; bot.edit_message_text("🔄", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.text == "🖼 Фото" and m.from_user.id in ADMIN_IDS)
def admin_btn_photos(m):
    photos = load_photos()
    text = "🖼 Фото\n"
    for cat in ["main","shop","hosts","deposit","profile","support"]: text += f"{'✅' if photos.get(cat) else '❌'} {cat}\n"
    mk = InlineKeyboardMarkup(row_width=2)
    for cat in ["main","shop","hosts","deposit","profile","support"]: mk.add(InlineKeyboardButton(cat, callback_data=f"setphoto:{cat}"))
    bot.send_message(m.chat.id, text, reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data.startswith("setphoto:"))
def set_photo_category(call):
    if call.from_user.id not in ADMIN_IDS: return
    cat = call.data.split(":")[1]; bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, f"📸 {cat}:\n❌ /skip")
    bot.register_next_step_handler(msg, lambda m, c=cat: save_category_photo(m, c))

def save_category_photo(message, cat):
    if not message.photo: return
    photos = load_photos(); photos[cat] = message.photo[-1].file_id; save_photos(photos)
    bot.send_message(message.chat.id, f"✅ {cat}")

# ========== АДМИН: ФОТО ХОСТ-СЕРВИСА ==========
@bot.message_handler(func=lambda m: m.text == "🖼 Фото ХС" and m.from_user.id in ADMIN_IDS)
def admin_photo_host_service(m):
    uid = m.from_user.id
    photos = load_photos()
    current = "✅ Установлено" if photos.get("shop") else "❌ Не установлено"
    
    text = f"🖼 <b>ФОТО ХОСТ-СЕРВИСА</b>\n\nСтатус: {current}\n\nОтправьте фото для установки или нажмите кнопку:"
    
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(
        InlineKeyboardButton("🖼 Установить фото", callback_data="setphoto_hs"),
        InlineKeyboardButton("🗑 Удалить фото", callback_data="delphoto_hs")
    )
    
    bot.send_message(uid, text, reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data == "setphoto_hs")
def set_photo_hs(call):
    if call.from_user.id not in ADMIN_IDS: return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🖼 <b>Отправьте фото для хост-сервиса</b>\n\n❌ /skip для отмены")
    bot.register_next_step_handler(msg, save_photo_hs)


def save_photo_hs(message):
    if message.from_user.id not in ADMIN_IDS: return
    if message.text and message.text == '/skip':
        bot.send_message(message.chat.id, "❌ Отменено")
        return
    if not message.photo:
        bot.send_message(message.chat.id, "❌ Отправьте фото!")
        return
    photos = load_photos()
    photos['shop'] = message.photo[-1].file_id
    save_photos(photos)
    bot.send_photo(message.chat.id, message.photo[-1].file_id, caption="✅ Фото для хост-сервиса сохранено!")


@bot.callback_query_handler(func=lambda c: c.data == "delphoto_hs")
def del_photo_hs(call):
    if call.from_user.id not in ADMIN_IDS: return
    bot.answer_callback_query(call.id)
    photos = load_photos()
    photos['shop'] = None
    save_photos(photos)
    bot.edit_message_text("🗑 <b>Фото хост-сервиса удалено!</b>", call.message.chat.id, call.message.message_id)


@bot.message_handler(func=lambda m: m.text == "🎫 Промо" and m.from_user.id in ADMIN_IDS)
def admin_promo_menu(m):
    with get_db() as conn: promos = conn.execute('SELECT * FROM promocodes').fetchall()
    text = "🎫 Промо\n\n"
    for p in promos: text += f"• <code>{p['code']}</code> {p['type']} {p['days']}дн {p['used_count']}/{p['max_uses']}\n"
    text += "\n<code>КОД ТИП ДНИ ИСП</code>"
    mk = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Удалить все", callback_data="del_all_promos"))
    bot.send_message(m.chat.id, text, reply_markup=mk)
    bot.register_next_step_handler(m, create_promo_admin)

def create_promo_admin(message):
    if message.from_user.id not in ADMIN_IDS: return
    parts = message.text.strip().split()
    if len(parts) < 4: bot.send_message(message.chat.id, "❌ КОД ТИП ДНИ ИСП"); return
    code, ptype, days, uses = parts[0].upper(), parts[1].lower(), int(parts[2]), int(parts[3])
    with get_db() as conn: conn.execute('INSERT OR IGNORE INTO promocodes VALUES (?,?,?,?,0)', (code, ptype, days, uses)); conn.commit()
    bot.send_message(message.chat.id, f"✅ {code}")

@bot.callback_query_handler(func=lambda c: c.data == "del_all_promos")
def delete_all_promos(call):
    if call.from_user.id not in ADMIN_IDS: return
    with get_db() as conn: conn.execute('DELETE FROM promocodes'); conn.commit()
    bot.answer_callback_query(call.id, "✅")

@bot.message_handler(func=lambda m: m.text == "🔒 Подписка" and m.from_user.id in ADMIN_IDS)
def admin_subscription_settings(m):
    data = load_channels()
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(InlineKeyboardButton("🖼 Фото", callback_data="set_sub_photo"), InlineKeyboardButton("📝 Текст", callback_data="set_sub_text"), InlineKeyboardButton("🗑 Фото", callback_data="del_sub_photo"))
    bot.send_message(m.chat.id, f"🔒 Подписка\n🖼 {'✅' if data.get('welcome_photo') else '❌'}\n📝 {data.get('welcome_text','')[:50]}...", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data == "set_sub_photo")
def set_sub_photo(call):
    if call.from_user.id not in ADMIN_IDS: return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🖼 Фото:\n❌ /skip")
    bot.register_next_step_handler(msg, lambda m: save_sub_photo(m) if not (m.text and m.text=='/skip') else None)

def save_sub_photo(message):
    if not message.photo: return
    data = load_channels(); data['welcome_photo'] = message.photo[-1].file_id; save_channels(data)
    bot.send_message(message.chat.id, "✅")

@bot.callback_query_handler(func=lambda c: c.data == "set_sub_text")
def set_sub_text(call):
    if call.from_user.id not in ADMIN_IDS: return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📝 Текст:\n❌ /skip")
    bot.register_next_step_handler(msg, lambda m: save_sub_text(m) if not (m.text and m.text=='/skip') else None)

def save_sub_text(message):
    data = load_channels(); data['welcome_text'] = message.text or ""; save_channels(data)
    bot.send_message(message.chat.id, "✅")

@bot.callback_query_handler(func=lambda c: c.data == "del_sub_photo")
def del_sub_photo(call):
    if call.from_user.id not in ADMIN_IDS: return
    data = load_channels(); data['welcome_photo'] = None; save_channels(data)
    bot.edit_message_text("🗑", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

# ========== ОБРАБОТКА ОПЛАТЫ ==========
@bot.message_handler(content_types=['photo'])
def screenshot(message):
    uid = message.from_user.id
    if uid not in pending_payments: return
    pi = pending_payments.pop(uid)
    if pi.get('type') == 'balance':
        for aid in ADMIN_IDS:
            mk = InlineKeyboardMarkup(row_width=2)
            mk.add(
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"app_bal|{uid}|{pi['amount']}"), 
                InlineKeyboardButton("❌ Отклонить", callback_data=f"rej|{uid}")
            )
            try: 
                bot.send_photo(aid, message.photo[-1].file_id, 
                    caption=f"💰 Пополнение баланса!\n👤 {message.from_user.first_name}\n🆔 {uid}\n💰 Сумма: {pi['amount']}₽", 
                    reply_markup=mk)
            except: pass
        bot.send_message(uid, "✅ Чек отправлен администратору!", reply_markup=user_keyboard())

@bot.callback_query_handler(func=lambda c: c.data.startswith("app_bal|"))
def approve_balance(call):
    if call.from_user.id not in ADMIN_IDS: return
    try:
        data = call.data.replace("app_bal|", "")
        parts = data.split("|")
        if len(parts) < 2: 
            bot.answer_callback_query(call.id, "❌ Неверный формат!"); return
        uid = int(parts[0])
        amount = float(parts[1])
        
        with get_db() as conn:
            conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (amount, uid))
            conn.commit()
        cache.delete(f"user:{uid}")
        get_cached_user.cache_clear()
        
        try: 
            bot.send_message(uid, f"✅ <b>Баланс пополнен!</b>\n\n💰 +{amount}₽\n\nИспользуйте баланс для оплаты тарифов.", reply_markup=user_keyboard())
        except: pass
        
        bot.answer_callback_query(call.id, "✅ Подтверждено")
        try:
            new_caption = (call.message.caption or "") + "\n\n✅ ПОДТВЕРЖДЕНО"
            bot.edit_message_caption(call.message.chat.id, call.message.message_id, caption=new_caption)
        except: pass
    except Exception as e:
        logger.error(f"Approve balance error: {e}")
        bot.answer_callback_query(call.id, f"❌ Ошибка: {str(e)[:50]}", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data.startswith("rej|"))
def reject(call):
    if call.from_user.id not in ADMIN_IDS: return
    try:
        uid = int(call.data.replace("rej|", ""))
        try: 
            bot.send_message(uid, "❌ <b>Оплата отклонена</b>", reply_markup=user_keyboard())
        except: pass
        bot.answer_callback_query(call.id, "❌ Отклонено")
        try:
            new_caption = (call.message.caption or "") + "\n\n❌ ОТКЛОНЕНО"
            bot.edit_message_caption(call.message.chat.id, call.message.message_id, caption=new_caption)
        except: pass
    except Exception as e:
        logger.error(f"Reject error: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка!")

# ========== CALLBACKS ==========
@bot.callback_query_handler(func=lambda c: c.data == "check_sub")
def check_sub(call):
    user_id = call.from_user.id
    channels = load_channels().get("channels", [])
    if not channels: bot.answer_callback_query(call.id, "✅"); cmd_start(call.message); return
    not_subscribed = [ch['name'] for ch in channels if bot.get_chat_member(ch['id'], user_id).status in ["left","kicked"]]
    if not not_subscribed:
        bot.answer_callback_query(call.id, "✅")
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        cmd_start(call.message)
    else: bot.answer_callback_query(call.id, f"❌ {', '.join(not_subscribed)}", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data.startswith("sc:"))
def script_action(call):
    if is_bot_blocked(call.from_user.id): bot.answer_callback_query(call.id, "🔴", show_alert=True); return
    try:
        _, a, sid = call.data.split(":")
        if a == "stop":
            s = get_script(sid)
            if s and s['status']=='running':
                if s.get('container_id'): stop_container(s['container_id'])
                update_script_status(sid,'stopped')
            bot.answer_callback_query(call.id, "✅")
        elif a == "log":
            lp = os.path.join(LOGS_DIR, f"{sid}.log")
            if os.path.exists(lp):
                with open(lp,'rb') as f: bot.send_document(call.message.chat.id, f, caption=f"📄 {sid}")
            bot.answer_callback_query(call.id, "✅")
        elif a == "del":
            s = get_script(sid)
            if s and s.get('container_id'): stop_container(s['container_id'])
            delete_script(sid, call.from_user.id)
            d = os.path.join(SCRIPTS_DIR, str(call.from_user.id), sid)
            if os.path.exists(d): shutil.rmtree(d, ignore_errors=True)
            lp = os.path.join(LOGS_DIR, f"{sid}.log")
            if os.path.exists(lp): os.remove(lp)
            bot.answer_callback_query(call.id, "✅")
    except: bot.answer_callback_query(call.id, "❌", show_alert=True)

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q): bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def pay_ok(message):
    p = message.successful_payment.invoice_payload
    if p.startswith("bal_stars_"):
        amount = int(p.replace("bal_stars_", ""))
        with get_db() as conn: conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (amount, message.from_user.id)); conn.commit()
        cache.delete(f"user:{message.from_user.id}"); get_cached_user.cache_clear()
        bot.send_message(message.chat.id, f"⭐ Баланс +{amount}₽", reply_markup=user_keyboard())

# ========== ЗАГРУЗКА СКРИПТОВ ==========
@bot.message_handler(content_types=['document'])
def handle_doc(message):
    uid = message.from_user.id
    if is_bot_blocked(uid) or not get_user(uid) or not check_subscription(uid) or not check_user_limits(uid):
        bot.reply_to(message, "❌"); return
    fi = bot.get_file(message.document.file_id); fn, fs = message.document.file_name, message.document.file_size
    _, mx = get_user_limits(uid)
    if fs > mx*1024*1024: bot.reply_to(message, f"❌ Макс {mx}МБ"); return
    if not (fn.endswith('.py') or fn.endswith('.zip')): bot.reply_to(message, "❌ .py/.zip"); return
    td = os.path.join(TEMP_DIR, str(uid)); os.makedirs(td, exist_ok=True)
    tp = os.path.join(td, fn)
    with open(tp,'wb') as f: f.write(bot.download_file(fi.file_path))
    sid = str(uuid.uuid4())[:8]; upload_states[uid] = {'sid':sid, 'tp':tp, 'fn':fn, 'fs':fs}
    if fn.endswith('.zip'):
        et = os.path.join(td, sid); os.makedirs(et, exist_ok=True)
        ok, msg = extract_zip(tp, et)
        if not ok: bot.reply_to(message, f"❌ {msg}"); cleanup(uid); return
        pf = find_py_files(et)
        if not pf: bot.reply_to(message, "❌ Нет .py"); cleanup(uid); return
        upload_states[uid].update({'et':et, 'pf':pf})
        mk = InlineKeyboardMarkup(row_width=1)
        for f in pf: mk.add(InlineKeyboardButton(f"📄 {os.path.relpath(f,et)}", callback_data=f"sel:{os.path.relpath(f,et)}"))
        bot.send_message(uid, "📁 Файл:", reply_markup=mk)
    else: finish(uid, tp, fn)

@bot.callback_query_handler(func=lambda c: c.data.startswith("sel:"))
def select_file(call):
    uid = call.from_user.id
    if uid not in upload_states: bot.answer_callback_query(call.id, "❌"); return
    rel = call.data.split(":",1)[1]; state = upload_states[uid]; state['main'] = rel
    bot.edit_message_text(f"✅ {rel}", uid, call.message.message_id); bot.answer_callback_query(call.id)
    finish(uid, os.path.join(state['et'], rel), state['fn'])

def finish(uid, sp, ofn):
    state = upload_states.pop(uid, {})
    sid = state.get('sid', str(uuid.uuid4())[:8]); fs = state.get('fs', os.path.getsize(sp))
    ud = os.path.join(SCRIPTS_DIR, str(uid), sid); os.makedirs(ud, exist_ok=True)
    if 'et' in state:
        for item in os.listdir(state['et']):
            s = os.path.join(state['et'], item); d = os.path.join(ud, item)
            if os.path.isdir(s): shutil.copytree(s, d, dirs_exist_ok=True)
            else: shutil.copy2(s, d)
        mf = os.path.join(ud, state.get('main',''))
    else: shutil.move(sp, os.path.join(ud, ofn)); mf = os.path.join(ud, ofn)
    cfg, tariff = get_docker_config(uid)
    cid, err = run_docker(sid, ud, tariff)
    if err: cid, err = run_fallback(sid, ud)
    if err: bot.send_message(uid, f"❌ {err}"); return
    add_script(sid, uid, ofn, ud, fs, tariff); update_script_status(sid, 'running', cid)
    bot.send_message(uid, f"✅ Хост запущен!\n📄 {ofn}\n🆔 <code>{sid}</code>\n⚡ {cfg.get('cpus','?')} CPU | 💾 {cfg.get('memory','?')} RAM", reply_markup=user_keyboard())
    cleanup(uid)

# ========== МОНИТОРИНГ ==========
def monitor():
    while True:
        try:
            for s in get_all_running_scripts():
                if s.get('container_id') and not is_alive(s['container_id']):
                    update_script_status(s['id'], 'stopped')
            time.sleep(10)
        except Exception as e: logger.error(f"Monitor: {e}"); time.sleep(30)

def crypto_check():
    while True:
        try:
            for iid, info in list(crypto_invoices.items()):
                if check_invoice(iid):
                    if info.get('type') == 'balance':
                        rub_amount = info['amount'] * 95
                        with get_db() as conn: conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (rub_amount, info['uid'])); conn.commit()
                        cache.delete(f"user:{info['uid']}"); get_cached_user.cache_clear()
                        try: bot.send_message(info['uid'], f"💎 Баланс +{rub_amount}₽", reply_markup=user_keyboard())
                        except: pass
                    del crypto_invoices[iid]
            time.sleep(15)
        except Exception as e: logger.error(f"Crypto: {e}"); time.sleep(30)

def cleanup_resources():
    logger.info("Shutdown...")
    try: stop_all(); executor.shutdown(wait=True)
    except Exception as e: logger.error(f"Cleanup: {e}")

atexit.register(cleanup_resources)
signal.signal(signal.SIGTERM, lambda s,f: sys.exit(0))
signal.signal(signal.SIGINT, lambda s,f: sys.exit(0))

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    print(f"🚀 Hosting Bot v{VERSION}\n🇩🇪 Tier 1-3 | 🇺🇸 Tier 1-2 | 🇫🇮 Tier 1-5")
    init_db()
    if not os.path.exists(SETTINGS_FILE): save_settings(DEFAULT_SETTINGS)
    if not os.path.exists(PHOTOS_FILE): save_photos(CATEGORY_PHOTOS)
    if not os.path.exists(CHANNEL_FILE): save_channels({"channels": [], "welcome_text": "🔒 Подпишитесь на канал!", "welcome_photo": None})
    setup_menu()
    print(f"🐳 Docker: {'✅' if check_docker() else '❌'}")
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=crypto_check, daemon=True).start()
    print("✅ Бот запущен")
    bot.infinity_polling()
