# bot.py - Хостинг бот на aiogram 3.x (Обновленные тарифы)
import asyncio
import logging
import sys
import os
import sqlite3
import time
import uuid
import shutil
import zipfile
import subprocess
import signal
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter, Filter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    BotCommand, Message, CallbackQuery, FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ========== ЛОГГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('hosting_bot')

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN", "8964647336:AAHs5cGpAuSGaXbDBeG-lmS6z0fgXIEM2rs")
VERSION = "28.0.0"
ADMIN_IDS = [314148464]
SUPPORT_URL = "https://t.me/hesers"

FREE_TRIAL_DAYS = 3
FREE_MAX_SCRIPTS = 3
FREE_MAX_SIZE_MB = 5

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR = BASE_DIR / "logs"
TEMP_DIR = BASE_DIR / "temp"
FILES_DIR = BASE_DIR / "user_files"
DATABASE_PATH = BASE_DIR / "bot_database.db"

for d in [SCRIPTS_DIR, LOGS_DIR, TEMP_DIR, FILES_DIR]:
    d.mkdir(exist_ok=True)

# ========== НОВЫЕ ТАРИФЫ ==========
# Пакеты вместо стран
PACKAGES = {
    "beginner": {
        "name": "🌱 Новичок",
        "emoji": "🌱",
        "description": "Для начинающих разработчиков",
        "color": "#4CAF50"
    },
    "standard": {
        "name": "⚡ Стандарт",
        "emoji": "⚡",
        "description": "Оптимальный выбор",
        "color": "#2196F3"
    },
    "expert": {
        "name": "👑 Эксперт",
        "emoji": "👑",
        "description": "Максимальные возможности",
        "color": "#FF9800"
    }
}

# Супер низкие цены
TIER_INFO = {
    "1": {
        "name": "🌱 Новичок",
        "emoji": "🌱",
        "price_7d": 29,
        "cpu": "1 vCPU",
        "ram": "512 MB",
        "scripts": 3,
        "storage": "5 GB",
        "package": "beginner"
    },
    "2": {
        "name": "⚡ Стандарт",
        "emoji": "⚡",
        "price_7d": 49,
        "cpu": "2 vCPU",
        "ram": "1 GB",
        "scripts": 5,
        "storage": "10 GB",
        "package": "standard"
    },
    "3": {
        "name": "👑 Эксперт",
        "emoji": "👑",
        "price_7d": 79,
        "cpu": "3 vCPU",
        "ram": "2 GB",
        "scripts": 10,
        "storage": "25 GB",
        "package": "expert"
    }
}

DAYS_MULTIPLIER = {"7": 1, "30": 4, "90": 10}
DAYS_NAMES = {"7": "7 дней", "30": "30 дней", "90": "90 дней"}

def calc_price(tier, days):
    return TIER_INFO.get(tier, {}).get("price_7d", 0) * DAYS_MULTIPLIER.get(days, 1)

# ========== БД ==========
def get_db():
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0.0,
            subscription TEXT DEFAULT 'free', subscription_expiry TIMESTAMP,
            free_used INTEGER DEFAULT 0, current_tier TEXT, current_location TEXT,
            is_premium INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
            path TEXT NOT NULL, container_id TEXT, status TEXT DEFAULT 'stopped',
            size INTEGER, original_file TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY, type TEXT DEFAULT 'pro', days INTEGER DEFAULT 30,
            max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0)''')
        conn.commit()

def get_user(uid):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone()
        return dict(row) if row else None

def create_user(uid, username):
    expiry = (datetime.now() + timedelta(days=FREE_TRIAL_DAYS)).isoformat()
    with get_db() as conn:
        conn.execute('''INSERT OR IGNORE INTO users (user_id, username, free_used, subscription_expiry) 
                       VALUES (?,?,1,?)''', (uid, username, expiry))
        conn.commit()

def check_subscription(uid):
    user = get_user(uid)
    if not user: return False
    if uid in ADMIN_IDS: return True
    
    is_premium = user.get('is_premium', 0)
    
    if is_premium == 1:
        expiry_str = user.get('subscription_expiry')
        if expiry_str:
            try:
                expiry = datetime.fromisoformat(expiry_str)
                if datetime.now() < expiry:
                    return True
                else:
                    with get_db() as conn:
                        conn.execute('UPDATE users SET is_premium = 0, subscription = ? WHERE user_id = ?', ('free', uid))
                        conn.commit()
            except:
                pass
    
    expiry_str = user.get('subscription_expiry')
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if datetime.now() < expiry:
                return True
        except:
            pass
    
    return False

def get_subscription_info(uid):
    user = get_user(uid)
    if not user: return "Нет доступа", 0, "none"
    if uid in ADMIN_IDS: return "👑 Админ", 999, "expert"
    
    is_premium = user.get('is_premium', 0)
    expiry_str = user.get('subscription_expiry')
    
    if is_premium == 1 and expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            delta = expiry - datetime.now()
            if delta.days > 0:
                return f"💎 PREMIUM", delta.days, "premium"
        except:
            pass
    
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            delta = expiry - datetime.now()
            if delta.days > 0:
                return f"🆓 Бесплатный", delta.days, "free"
            elif delta.seconds > 0:
                hours = int(delta.seconds / 3600)
                return f"🆓 Бесплатный", f"{hours}ч", "free"
            else:
                return "❌ Истек", 0, "expired"
        except:
            pass
    
    return "❌ Не активирован", 0, "none"

def set_subscription(uid, plan, days=0, tier=None, location=None):
    with get_db() as conn:
        if days > 0:
            expiry = (datetime.now() + timedelta(days=days)).isoformat()
            conn.execute('''UPDATE users SET subscription = ?, subscription_expiry = ?, is_premium = ?,
                           current_tier = ?, current_location = ?, free_used = 1 WHERE user_id = ?''',
                        (plan, expiry, 1 if plan != 'free' else 0, tier, location, uid))
        else:
            conn.execute('UPDATE users SET subscription = ?, subscription_expiry = NULL, is_premium = 0 WHERE user_id = ?', ('free', uid))
        conn.commit()

def remove_subscription(uid):
    with get_db() as conn:
        conn.execute('''UPDATE users SET subscription = 'free', subscription_expiry = NULL, 
                       is_premium = 0, current_tier = NULL, current_location = NULL WHERE user_id = ?''', (uid,))
        conn.commit()

def get_user_limits(uid):
    user = get_user(uid)
    if not user: return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB
    if uid in ADMIN_IDS: return 999, 1024
    
    if user.get('is_premium', 0) == 1:
        tier = user.get('current_tier')
        if tier and tier in TIER_INFO: 
            return TIER_INFO[tier]['scripts'], 50
    
    return FREE_MAX_SCRIPTS, FREE_MAX_SIZE_MB

def count_user_scripts(uid):
    with get_db() as conn: 
        return conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]

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

def add_script(sid, uid, name, path, size, original_file=None):
    with get_db() as conn:
        conn.execute('INSERT INTO scripts (id, user_id, name, path, status, size, original_file) VALUES (?,?,?,?,?,?,?)',
                    (sid, uid, name, str(path), 'stopped', size, original_file))
        conn.commit()

def update_script_status(sid, status, cid=None):
    with get_db() as conn:
        if cid: conn.execute('UPDATE scripts SET status=?, container_id=? WHERE id=?', (status, cid, sid))
        else: conn.execute('UPDATE scripts SET status=?, container_id=NULL WHERE id=?', (status, sid))
        conn.commit()

def delete_script(sid, uid=None):
    with get_db() as conn:
        if uid: conn.execute('DELETE FROM scripts WHERE id=? AND user_id=?', (sid, uid))
        else: conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
        conn.commit()

def get_all_users():
    with get_db() as conn: 
        return [dict(r) for r in conn.execute('SELECT * FROM users').fetchall()]

def check_user_limits(uid):
    mx, _ = get_user_limits(uid)
    return True if mx == 999 else count_user_scripts(uid) < mx

def activate_promo(uid, code):
    with get_db() as conn:
        p = conn.execute('SELECT * FROM promocodes WHERE code=?', (code,)).fetchone()
        if not p: return False, "Промокод не найден"
        if p['used_count'] >= p['max_uses']: return False, "Закончился"
        conn.execute('UPDATE promocodes SET used_count=used_count+1 WHERE code=?', (code,))
        expiry = (datetime.now() + timedelta(days=p['days'])).isoformat()
        conn.execute('UPDATE users SET subscription = ?, subscription_expiry = ?, is_premium = 1, free_used = 1 WHERE user_id = ?', (p['type'], expiry, uid))
        conn.commit()
    return True, f"{p['type'].upper()} на {p['days']} дн!"

def kill_process(pid):
    try: os.kill(int(pid), signal.SIGTERM); return True
    except: return False

def save_user_file(uid, file_name, file_data):
    user_dir = FILES_DIR / str(uid)
    user_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = f"{timestamp}_{file_name}"
    file_path = user_dir / safe_name
    file_path.write_bytes(file_data)
    return str(file_path)

async def install_requirements(script_path: str, script_id: str) -> tuple:
    req_file = Path(script_path) / "requirements.txt"
    if not req_file.exists():
        return True, "OK"
    
    log_path = LOGS_DIR / f"{script_id}_install.log"
    
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'pip', 'install', '-r', str(req_file),
            '--quiet',
            stdout=open(log_path, 'w'),
            stderr=subprocess.STDOUT
        )
        await process.wait()
        
        if process.returncode == 0:
            return True, "OK"
        else:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                error_text = f.read()[-500:]
            return False, f"Ошибка установки:\n{error_text}"
    except Exception as e:
        return False, str(e)

async def run_script_async(sid, path):
    py_files = [f for f in os.listdir(path) if f.endswith('.py')] if os.path.isdir(path) else []
    if not py_files: return None, "Нет .py файлов"
    
    success, msg = await install_requirements(path, sid)
    if not success:
        return None, msg
    
    main_file = 'main.py' if 'main.py' in py_files else 'bot.py' if 'bot.py' in py_files else py_files[0]
    
    try:
        log_path = LOGS_DIR / f"{sid}.log"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-I', str(Path(path) / main_file),
            stdout=open(log_path, 'w'), 
            stderr=subprocess.STDOUT, 
            cwd=path
        )
        return str(proc.pid), None
    except Exception as e: 
        return None, str(e)

# ========== FSM ==========
class UploadStates(StatesGroup):
    waiting_file = State()

class SupportStates(StatesGroup):
    waiting_message = State()
    in_chat = State()

class DepositStates(StatesGroup):
    waiting_amount = State()

class PromoStates(StatesGroup):
    waiting_code = State()

class AdminSubStates(StatesGroup):
    waiting_user_id = State()
    waiting_tier = State()
    waiting_days = State()

# ========== БОТ ==========
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

bot_active = True
admin_mode = {}
pending_payments = {}

# ========== ФИЛЬТРЫ ==========
class BotActiveFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if isinstance(message, Message):
            if message.from_user.id in ADMIN_IDS:
                return True
            return bot_active
        return True

class AdminFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in ADMIN_IDS

# ========== КЛАВИАТУРЫ ==========
def user_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📤 Загрузить файл"),
        KeyboardButton(text="💻 Мои хосты")
    )
    builder.row(
        KeyboardButton(text="🛒 Тарифы"),
        KeyboardButton(text="💳 Пополнить")
    )
    builder.row(
        KeyboardButton(text="👤 Профиль"),
        KeyboardButton(text="🆘 Поддержка")
    )
    return builder.as_markup(resize_keyboard=True)

def admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📤 Загрузить файл"),
        KeyboardButton(text="💻 Мои хосты")
    )
    builder.row(
        KeyboardButton(text="📊 Статистика"),
        KeyboardButton(text="👥 Пользователи")
    )
    builder.row(
        KeyboardButton(text="📦 Хосты"),
        KeyboardButton(text="🎫 Промокоды")
    )
    builder.row(
        KeyboardButton(text="🎁 Выдать подписку"),
        KeyboardButton(text="❌ Отозвать подписку")
    )
    builder.row(
        KeyboardButton(text="🛑 Стоп бот"),
        KeyboardButton(text="🟢 Старт бот")
    )
    builder.row(
        KeyboardButton(text="👤 Режим юзера"),
        KeyboardButton(text="🆘 Поддержка")
    )
    return builder.as_markup(resize_keyboard=True)

def support_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Чат с админом", callback_data="chat_to_admin")
    kb.button(text="📞 Telegram", url=SUPPORT_URL)
    kb.adjust(1)
    return kb.as_markup()

# ========== БЛОКИРОВКА ПРЯМЫХ ДОКУМЕНТОВ ==========
@dp.message(F.document)
async def block_direct_documents(message: Message, state: FSMContext):
    uid = message.from_user.id
    current_state = await state.get_state()
    if current_state == UploadStates.waiting_file:
        return
    
    await message.answer(
        "❌ Загрузка файлов только через кнопку!\n\n"
        "📤 Нажмите «Загрузить файл» в меню.",
        reply_markup=user_keyboard() if uid not in ADMIN_IDS else admin_keyboard()
    )

# ========== ХЕНДЛЕРЫ ==========
@dp.message(CommandStart(), BotActiveFilter())
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    
    if not get_user(uid): 
        create_user(uid, message.from_user.username)
        await message.answer(
            f"🎉 <b>Добро пожаловать!</b>\n\n"
            f"🆓 Бесплатный тариф на {FREE_TRIAL_DAYS} дня!\n"
            f"📦 Скриптов: {FREE_MAX_SCRIPTS}\n"
            f"📊 Размер файла: {FREE_MAX_SIZE_MB} МБ\n\n"
            f"💎 Низкие цены от 29₽!"
        )
    
    await state.clear()
    
    if uid in ADMIN_IDS and admin_mode.get(uid, True):
        s = get_all_scripts()
        r = len([x for x in s if x['status']=='running'])
        u = get_all_users()
        premium_users = len([x for x in u if x.get('is_premium', 0) == 1])
        await message.answer(
            f"👑 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
            f"👥 Пользователей: {len(u)} (💎{premium_users})\n"
            f"📦 Хостов: {len(s)} (🟢{r})\n\n"
            f"👇 Действие:",
            reply_markup=admin_keyboard()
        )
        return
    
    if not check_subscription(uid):
        kb = InlineKeyboardBuilder()
        kb.button(text="🛒 Выбрать тариф", callback_data="shop_configurator")
        kb.button(text="🎫 Промокод", callback_data="activate_promo")
        kb.adjust(1)
        
        await message.answer(
            f"❌ <b>ДОСТУП ЗАБЛОКИРОВАН</b>\n\n"
            f"Бесплатный тариф истек!\n"
            f"💎 Тарифы от 29₽!\n\n"
            f"👇 Выберите действие:",
            reply_markup=kb.as_markup()
        )
        return
    
    scripts = get_user_scripts(uid)
    running = len([s for s in scripts if s['status']=='running'])
    stopped = len(scripts) - running
    uptime = 100 if len(scripts)==0 else round((running/len(scripts))*100)
    
    sub_info, days, sub_type = get_subscription_info(uid)
    limits = get_user_limits(uid)
    
    await message.answer(
        f"🚀 <b>HOSTING BOT</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"├ 📋 Тариф: {sub_info}\n"
        f"├ 📦 Хостов: {len(scripts)}/{limits[0]}\n"
        f"├ 🟢 Запущено: {running}\n"
        f"├ 🔴 Остановлено: {stopped}\n"
        f"└ 📈 Аптайм: {uptime}%\n\n"
        f"👇 <b>Действие:</b>",
        reply_markup=user_keyboard()
    )

@dp.message(Command('admin'), AdminFilter())
async def cmd_admin(message: Message):
    admin_mode[message.from_user.id] = True
    await message.answer("👑 <b>Админ-панель активирована!</b>", reply_markup=admin_keyboard())

@dp.callback_query(F.data == "shop_configurator")
async def shop_configurator(callback: CallbackQuery):
    await shop_menu(callback.message)
    await callback.answer()

# ========== ЗАГРУЗКА ФАЙЛОВ ==========
@dp.message(F.text == "📤 Загрузить файл", BotActiveFilter())
async def btn_upload(message: Message, state: FSMContext):
    uid = message.from_user.id
    
    if not check_subscription(uid):
        await message.answer(
            f"❌ <b>ДОСТУП ЗАКРЫТ!</b>\n\n"
            f"Бесплатный тариф истек.\n"
            f"💎 Тарифы от 29₽!"
        )
        return
    
    mx_scripts, mx_size = get_user_limits(uid)
    current_scripts = count_user_scripts(uid)
    
    if current_scripts >= mx_scripts:
        await message.answer(
            f"❌ <b>Лимит хостов!</b>\n\n"
            f"├ Максимум: {mx_scripts}\n"
            f"└ Сейчас: {current_scripts}\n\n"
            f"💎 Улучшите тариф для увеличения лимита"
        )
        return
    
    await state.set_state(UploadStates.waiting_file)
    await message.answer(
        f"📤 <b>ЗАГРУЗКА ФАЙЛА</b>\n\n"
        f"├ 📄 Поддерживаются: .py, .zip\n"
        f"├ 📦 Макс. размер: {mx_size} МБ\n"
        f"├ 📊 Лимит: {current_scripts}/{mx_scripts}\n"
        f"└ ❌ /cancel для отмены\n\n"
        f"👇 <b>Отправьте файл:</b>"
    )

@dp.message(UploadStates.waiting_file, F.document, BotActiveFilter())
async def handle_upload(message: Message, state: FSMContext):
    uid = message.from_user.id
    
    if not check_subscription(uid):
        await message.answer("❌ Доступ закрыт!")
        await state.clear()
        return
    
    doc = message.document
    fn = doc.file_name
    fs = doc.file_size
    
    _, mx_size = get_user_limits(uid)
    
    if not fn.endswith(('.py', '.zip')):
        await message.answer("❌ Только .py или .zip файлы!")
        return
    
    if fs > mx_size * 1024 * 1024:
        await message.answer(f"❌ Максимальный размер: {mx_size} МБ")
        return
    
    td = TEMP_DIR / str(uid)
    td.mkdir(exist_ok=True)
    tp = td / fn
    
    status_msg = await message.answer("📥 <b>Загрузка файла...</b>")
    await bot.download(doc, destination=tp)
    
    file_data = tp.read_bytes()
    original_file_path = save_user_file(uid, fn, file_data)
    
    await status_msg.edit_text("📦 <b>Обработка файла...</b>")
    
    sid = str(uuid.uuid4())[:8]
    
    if fn.endswith('.zip'):
        et = td / sid
        et.mkdir(exist_ok=True)
        
        try:
            with zipfile.ZipFile(tp) as z:
                z.extractall(et)
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка архива: {e}")
            return
        
        py_files = list(et.rglob("*.py"))
        if not py_files:
            await status_msg.edit_text("❌ В архиве нет .py файлов!")
            return
        
        ud = SCRIPTS_DIR / str(uid) / sid
        ud.mkdir(parents=True, exist_ok=True)
        
        for item in et.iterdir():
            s = et / item
            dest = ud / item
            if s.is_dir():
                shutil.copytree(str(s), str(dest), dirs_exist_ok=True)
            else:
                shutil.copy2(str(s), str(dest))
        
        total_size = sum(f.stat().st_size for f in ud.rglob('*') if f.is_file())
        
        await status_msg.edit_text("📚 <b>Установка библиотек...</b>")
        
        cid, err = await run_script_async(sid, str(ud))
        
        if err:
            await status_msg.edit_text(f"❌ <b>Ошибка запуска:</b>\n{err}")
            shutil.rmtree(str(ud), ignore_errors=True)
            return
        
        add_script(sid, uid, fn, str(ud), total_size, original_file_path)
        update_script_status(sid, 'running', cid)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="📥 Скачать файл", callback_data=f"download_file:{sid}")
        kb.button(text="💻 Мои хосты", callback_data="my_hosts")
        kb.adjust(1)
        
        await status_msg.edit_text(
            f"✅ <b>ХОСТ ЗАПУЩЕН!</b>\n\n"
            f"├ 📄 Файл: {fn}\n"
            f"├ 🆔 ID: <code>{sid}</code>\n"
            f"├ 📦 Размер: {total_size / (1024*1024):.1f} МБ\n"
            f"└ ⚡ Статус: 🟢 Запущен",
            reply_markup=kb.as_markup()
        )
        
        shutil.rmtree(str(td), ignore_errors=True)
    else:
        ud = SCRIPTS_DIR / str(uid) / sid
        ud.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tp), str(ud / fn))
        
        await status_msg.edit_text("📚 <b>Установка библиотек...</b>")
        
        cid, err = await run_script_async(sid, str(ud))
        
        if err:
            await status_msg.edit_text(f"❌ <b>Ошибка запуска:</b>\n{err}")
            return
        
        add_script(sid, uid, fn, str(ud), fs, original_file_path)
        update_script_status(sid, 'running', cid)
        
        kb = InlineKeyboardBuilder()
        kb.button(text="📥 Скачать файл", callback_data=f"download_file:{sid}")
        kb.button(text="💻 Мои хосты", callback_data="my_hosts")
        kb.adjust(1)
        
        await status_msg.edit_text(
            f"✅ <b>ХОСТ ЗАПУЩЕН!</b>\n\n"
            f"├ 📄 Файл: {fn}\n"
            f"├ 🆔 ID: <code>{sid}</code>\n"
            f"├ 📦 Размер: {fs / (1024*1024):.1f} МБ\n"
            f"└ ⚡ Статус: 🟢 Запущен",
            reply_markup=kb.as_markup()
        )
    
    await state.clear()

@dp.message(UploadStates.waiting_file)
async def invalid_upload(message: Message, state: FSMContext):
    if message.text and message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Загрузка отменена", reply_markup=user_keyboard())
        return
    
    await message.answer("❌ Отправьте файл (.py или .zip)!")

# ========== СКАЧИВАНИЕ ФАЙЛОВ ==========
@dp.callback_query(F.data.startswith("download_file:"))
async def download_file(callback: CallbackQuery):
    script_id = callback.data.split(":")[1]
    uid = callback.from_user.id
    
    script = get_script(script_id)
    if not script:
        await callback.answer("❌ Хост не найден!")
        return
    
    if script['user_id'] != uid and uid not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа!")
        return
    
    original_file = script.get('original_file')
    
    if original_file and os.path.exists(original_file):
        await callback.message.answer_document(
            FSInputFile(original_file),
            caption=f"📄 {script['name']}\n🆔 {script_id}"
        )
        await callback.answer("✅ Файл отправлен!")
    else:
        script_path = Path(script['path'])
        if script_path.exists():
            zip_path = TEMP_DIR / f"{script_id}.zip"
            with zipfile.ZipFile(zip_path, 'w') as zf:
                for file in script_path.rglob('*'):
                    if file.is_file():
                        zf.write(file, file.relative_to(script_path))
            
            await callback.message.answer_document(
                FSInputFile(zip_path),
                caption=f"📦 {script['name']}\n🆔 {script_id}"
            )
            
            zip_path.unlink()
            await callback.answer("✅ Архив отправлен!")
        else:
            await callback.answer("❌ Файлы не найдены!")

# ========== ХОСТЫ ==========
@dp.message(F.text == "💻 Мои хосты", BotActiveFilter())
async def btn_hosts(message: Message):
    uid = message.from_user.id
    
    if not check_subscription(uid):
        await message.answer("❌ Доступ закрыт!")
        return
    
    scripts = get_user_scripts(uid)
    
    if not scripts:
        kb = InlineKeyboardBuilder()
        kb.button(text="📤 Загрузить файл", callback_data="upload_file")
        kb.adjust(1)
        await message.answer("😔 <b>Нет хостов</b>\n\nЗагрузите ваш первый скрипт!", reply_markup=kb.as_markup())
        return
    
    text = f"💻 <b>МОИ ХОСТЫ ({len(scripts)})</b>\n\n"
    kb = InlineKeyboardBuilder()
    
    for i, s in enumerate(scripts, 1):
        st = "🟢" if s['status'] == 'running' else "🔴"
        status_text = "запущен" if s['status'] == 'running' else "остановлен"
        sz = s['size'] / (1024 * 1024) if s['size'] else 0
        text += f"{i}. {st} <b>{s['name']}</b>\n   └ {sz:.1f}МБ | {status_text} | <code>{s['id']}</code>\n"
        
        if s['status'] == 'running':
            kb.button(text=f"⏹ Стоп", callback_data=f"sc:stop:{s['id']}")
        else:
            kb.button(text=f"▶️ Старт", callback_data=f"sc:start:{s['id']}")
        kb.button(text=f"📥 Скачать", callback_data=f"download_file:{s['id']}")
        kb.button(text=f"🗑 Удалить", callback_data=f"sc:del:{s['id']}")
    
    kb.button(text="📤 Загрузить ещё", callback_data="upload_file")
    kb.adjust(3, 1)
    
    await message.answer(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "upload_file")
async def upload_file_callback(callback: CallbackQuery, state: FSMContext):
    await btn_upload(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "my_hosts")
async def my_hosts_callback(callback: CallbackQuery):
    await btn_hosts(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("sc:"), BotActiveFilter())
async def script_action(callback: CallbackQuery):
    try:
        _, action, sid = callback.data.split(":")
        uid = callback.from_user.id
        s = get_script(sid)
        
        if not s or (s['user_id'] != uid and uid not in ADMIN_IDS):
            await callback.answer("❌ Нет доступа!")
            return
        
        if action == "stop":
            if s['status'] == 'running':
                if s.get('container_id'): 
                    kill_process(s['container_id'])
                update_script_status(sid, 'stopped')
                await callback.answer("✅ Остановлен!")
            else: 
                await callback.answer("Уже остановлен")
        
        elif action == "start":
            if not check_subscription(uid):
                await callback.answer("❌ Доступ закрыт!")
                return
            if not bot_active and uid not in ADMIN_IDS:
                await callback.answer("❌ Бот остановлен!")
                return
            if s['status'] == 'stopped':
                if not os.path.exists(s['path']): 
                    await callback.answer("❌ Папка не найдена!")
                    return
                
                await callback.answer("🔄 Запуск...")
                
                cid, err = await run_script_async(sid, s['path'])
                if cid: 
                    update_script_status(sid, 'running', cid)
                    await callback.answer("✅ Запущен!")
                else: 
                    await callback.answer(f"❌ {err}")
            else: 
                await callback.answer("Уже запущен")
        
        elif action == "del":
            kb = InlineKeyboardBuilder()
            kb.button(text="✅ Да, удалить", callback_data=f"sc:confirm_del:{sid}")
            kb.button(text="❌ Отмена", callback_data="my_hosts")
            kb.adjust(2)
            
            await callback.message.edit_text(
                f"⚠️ <b>Удалить хост?</b>\n\n"
                f"📄 {s['name']}\n"
                f"🆔 <code>{sid}</code>\n\n"
                f"Это действие необратимо!",
                reply_markup=kb.as_markup()
            )
            await callback.answer()
            return
        
        elif action == "confirm_del":
            if s.get('container_id'): 
                kill_process(s['container_id'])
            
            original_file = s.get('original_file')
            if original_file and os.path.exists(original_file):
                os.unlink(original_file)
            
            delete_script(sid, uid)
            
            d = SCRIPTS_DIR / str(uid) / sid
            if d.exists(): 
                shutil.rmtree(d, ignore_errors=True)
            
            for log_file in [f"{sid}.log", f"{sid}_install.log"]:
                lp = LOGS_DIR / log_file
                if lp.exists(): 
                    lp.unlink()
            
            await callback.answer("✅ Удалён!")
        
        # Обновляем список
        scripts = get_user_scripts(uid)
        if scripts:
            text = f"💻 <b>МОИ ХОСТЫ ({len(scripts)})</b>\n\n"
            kb = InlineKeyboardBuilder()
            
            for i, s in enumerate(scripts, 1):
                st = "🟢" if s['status'] == 'running' else "🔴"
                status_text = "запущен" if s['status'] == 'running' else "остановлен"
                sz = s['size'] / (1024 * 1024) if s['size'] else 0
                text += f"{i}. {st} <b>{s['name']}</b>\n   └ {sz:.1f}МБ | {status_text}\n"
                
                if s['status'] == 'running':
                    kb.button(text=f"⏹ Стоп", callback_data=f"sc:stop:{s['id']}")
                else:
                    kb.button(text=f"▶️ Старт", callback_data=f"sc:start:{s['id']}")
                kb.button(text=f"📥 Скачать", callback_data=f"download_file:{s['id']}")
                kb.button(text=f"🗑 Удалить", callback_data=f"sc:del:{s['id']}")
            
            kb.button(text="📤 Загрузить ещё", callback_data="upload_file")
            kb.adjust(3, 1)
            
            await callback.message.edit_text(text, reply_markup=kb.as_markup())
        else:
            kb = InlineKeyboardBuilder()
            kb.button(text="📤 Загрузить файл", callback_data="upload_file")
            kb.adjust(1)
            await callback.message.edit_text(
                "😔 <b>Нет хостов</b>\n\nЗагрузите ваш первый скрипт!",
                reply_markup=kb.as_markup()
            )
    
    except Exception as e:
        logger.error(f"Script action error: {e}")
        await callback.answer("❌ Ошибка!")

# ========== МАГАЗИН ТАРИФОВ ==========
@dp.message(F.text == "🛒 Тарифы", BotActiveFilter())
async def shop_menu(message: Message):
    kb = InlineKeyboardBuilder()
    
    # Показываем все тарифы сразу
    for tier_id in ["1", "2", "3"]:
        t = TIER_INFO[tier_id]
        kb.button(
            text=f"{t['emoji']} {t['name']} | {t['cpu']} {t['ram']} | от {t['price_7d']}₽",
            callback_data=f"shop_tier:{tier_id}"
        )
    
    kb.button(text="❌ Закрыть", callback_data="close_menu")
    kb.adjust(1)
    
    await message.answer(
        f"🛒 <b>ТАРИФЫ</b>\n\n"
        f"🌱 <b>Новичок</b> - от 29₽\n"
        f"⚡ <b>Стандарт</b> - от 49₽\n"
        f"👑 <b>Эксперт</b> - от 79₽\n\n"
        f"👇 <b>Выберите тариф:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("shop_tier:"))
async def shop_select_tier(callback: CallbackQuery, state: FSMContext):
    tier = callback.data.split(":")[1]
    await state.update_data(tier=tier)
    
    t_data = TIER_INFO[tier]
    kb = InlineKeyboardBuilder()
    
    # Показываем цены со скидками
    kb.button(
        text=f"7 дней — {t_data['price_7d']}₽",
        callback_data=f"shop_days:7"
    )
    kb.button(
        text=f"30 дней — {t_data['price_7d'] * 4}₽",
        callback_data=f"shop_days:30"
    )
    kb.button(
        text=f"90 дней — {t_data['price_7d'] * 10}₽",
        callback_data=f"shop_days:90"
    )
    
    kb.button(text="◀️ Назад", callback_data="back_to_shop")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"{t_data['emoji']} <b>{t_data['name']}</b>\n\n"
        f"🖥 <b>Характеристики:</b>\n"
        f"├ CPU: {t_data['cpu']}\n"
        f"├ RAM: {t_data['ram']}\n"
        f"├ Storage: {t_data['storage']}\n"
        f"└ Скриптов: {t_data['scripts']}\n\n"
        f"📅 <b>Выберите срок:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("shop_days:"))
async def shop_select_days(callback: CallbackQuery, state: FSMContext):
    days = callback.data.split(":")[1]
    data = await state.get_data()
    tier = data.get('tier')
    
    if not tier:
        await callback.answer("❌ Выберите тариф!")
        return
    
    total = calc_price(tier, days)
    user = get_user(callback.from_user.id)
    balance = user.get('balance', 0) if user else 0
    
    kb = InlineKeyboardBuilder()
    if balance >= total:
        kb.button(text=f"✅ Оплатить {total}₽", callback_data=f"pay:{tier}:{days}")
    else:
        kb.button(text=f"❌ Не хватает {total - balance}₽", callback_data="noop")
    kb.button(text="💳 Пополнить", callback_data="deposit_menu")
    kb.button(text="◀️ Назад", callback_data="back_to_shop")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"🧾 <b>ЗАКАЗ:</b>\n\n"
        f"📦 {TIER_INFO[tier]['emoji']} {TIER_INFO[tier]['name']}\n"
        f"├ CPU: {TIER_INFO[tier]['cpu']}\n"
        f"├ RAM: {TIER_INFO[tier]['ram']}\n"
        f"└ Скриптов: {TIER_INFO[tier]['scripts']}\n\n"
        f"📅 {DAYS_NAMES[days]}\n"
        f"💰 Итого: {total}₽\n"
        f"💳 Баланс: {balance}₽",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("pay:"))
async def process_payment(callback: CallbackQuery, state: FSMContext):
    _, tier, days = callback.data.split(":")
    total = calc_price(tier, days)
    uid = callback.from_user.id
    
    user = get_user(uid)
    if user.get('balance', 0) < total:
        await callback.answer("❌ Недостаточно средств!")
        return
    
    with get_db() as conn:
        conn.execute('UPDATE users SET balance=balance-? WHERE user_id=?', (total, uid))
        conn.commit()
    
    days_int = 7 if days=='7' else 30 if days=='30' else 90
    sub_type = 'pro'
    set_subscription(uid, sub_type, days_int, tier, tier)
    
    await callback.message.edit_text(
        f"✅ <b>ТАРИФ АКТИВИРОВАН!</b>\n\n"
        f"📦 {TIER_INFO[tier]['emoji']} {TIER_INFO[tier]['name']}\n"
        f"📅 {DAYS_NAMES[days]}\n"
        f"💰 Списано: {total}₽\n\n"
        f"🎉 Увеличенные лимиты доступны!"
    )
    await state.clear()

@dp.callback_query(F.data == "back_to_shop")
async def back_to_shop(callback: CallbackQuery):
    await shop_menu(callback.message)

@dp.callback_query(F.data == "close_menu")
async def close_menu(callback: CallbackQuery):
    await callback.message.delete()

# ========== ПОДДЕРЖКА ==========
@dp.message(F.text == "🆘 Поддержка")
async def btn_support(message: Message):
    await message.answer(
        "🆘 <b>ПОДДЕРЖКА</b>\n\n"
        "Выберите способ связи:",
        reply_markup=support_keyboard()
    )

@dp.callback_query(F.data == "chat_to_admin")
async def chat_to_admin(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportStates.waiting_message)
    await callback.message.answer(
        "💬 <b>ЧАТ С АДМИНОМ</b>\n\n"
        "Отправьте ваше сообщение.\n"
        "❌ /cancel для отмены"
    )
    await callback.answer()

@dp.message(SupportStates.waiting_message)
async def process_support_message(message: Message, state: FSMContext):
    if message.text and message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Чат закрыт", reply_markup=user_keyboard())
        return
    
    uid = message.from_user.id
    user = get_user(uid)
    username = f"@{user.get('username', uid)}" if user else f"#{uid}"
    
    for aid in ADMIN_IDS:
        kb = InlineKeyboardBuilder()
        kb.button(text=f"✉️ Ответить {username}", callback_data=f"reply_to:{uid}")
        kb.adjust(1)
        
        try:
            if message.text:
                await bot.send_message(
                    aid, 
                    f"📩 <b>СООБЩЕНИЕ</b>\n"
                    f"👤 {username}\n"
                    f"🆔 <code>{uid}</code>\n\n"
                    f"💬 {message.text}",
                    reply_markup=kb.as_markup()
                )
            elif message.photo:
                await bot.send_photo(
                    aid, 
                    message.photo[-1].file_id,
                    caption=f"📩 <b>ФОТО</b>\n👤 {username}\n🆔 <code>{uid}</code>",
                    reply_markup=kb.as_markup()
                )
        except Exception as e:
            logger.error(f"Error sending to admin: {e}")
    
    await state.clear()
    await message.answer("✅ <b>Отправлено!</b>", reply_markup=user_keyboard())

@dp.callback_query(F.data.startswith("reply_to:"))
async def reply_to_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    
    uid = int(callback.data.split(":")[1])
    await state.set_state(SupportStates.in_chat)
    await state.update_data(reply_to=uid)
    
    await callback.message.answer(f"✏️ Введите ответ для пользователя {uid}:")
    await callback.answer()

@dp.message(SupportStates.in_chat)
async def send_reply(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    
    data = await state.get_data()
    uid = data.get('reply_to')
    
    if not uid:
        await state.clear()
        return
    
    try:
        if message.text:
            await bot.send_message(uid, f"📩 <b>Ответ от админа:</b>\n\n{message.text}")
        elif message.photo:
            await bot.send_photo(uid, message.photo[-1].file_id, caption="📩 <b>Ответ от админа</b>")
        
        await message.answer("✅ Ответ отправлен!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    
    await state.clear()

# ========== ВЫДАЧА ПОДПИСКИ АДМИНОМ ==========
@dp.message(F.text == "🎁 Выдать подписку", AdminFilter())
async def admin_give_subscription_start(message: Message, state: FSMContext):
    await state.set_state(AdminSubStates.waiting_user_id)
    await message.answer(
        "🎁 <b>ВЫДАЧА ПОДПИСКИ</b>\n\n"
        "Отправьте ID пользователя:\n"
        "Пример: <code>123456789</code>\n\n"
        "❌ /cancel для отмены"
    )

@dp.message(AdminSubStates.waiting_user_id, AdminFilter())
async def admin_give_subscription_user_id(message: Message, state: FSMContext):
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=admin_keyboard())
        return
    
    try:
        uid = int(message.text.strip())
    except:
        await message.answer("❌ Неверный формат ID!")
        return
    
    user = get_user(uid)
    if not user:
        await message.answer(f"❌ Пользователь с ID {uid} не найден!")
        return
    
    await state.update_data(target_uid=uid, target_username=user.get('username', 'Нет'))
    
    kb = InlineKeyboardBuilder()
    for tier_id in ["1", "2", "3"]:
        t = TIER_INFO[tier_id]
        kb.button(text=f"{t['emoji']} {t['name']}", callback_data=f"admin_sub_tier:{tier_id}")
    kb.button(text="❌ Отмена", callback_data="admin_sub_cancel")
    kb.adjust(1)
    
    await message.answer(
        f"👤 Пользователь: <code>{uid}</code> (@{user.get('username', 'Нет')})\n\n"
        f"📦 <b>Выберите тариф:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("admin_sub_tier:"), AdminFilter())
async def admin_sub_select_tier(callback: CallbackQuery, state: FSMContext):
    tier = callback.data.split(":")[1]
    await state.update_data(admin_tier=tier)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="7 дней", callback_data="admin_sub_days:7")
    kb.button(text="30 дней", callback_data="admin_sub_days:30")
    kb.button(text="90 дней", callback_data="admin_sub_days:90")
    kb.button(text="365 дней", callback_data="admin_sub_days:365")
    kb.button(text="◀️ Назад", callback_data="admin_sub_back_to_tier")
    kb.button(text="❌ Отмена", callback_data="admin_sub_cancel")
    kb.adjust(2)
    
    await callback.message.edit_text(
        f"{TIER_INFO[tier]['emoji']} <b>{TIER_INFO[tier]['name']}</b>\n\n"
        f"📅 <b>Выберите срок:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("admin_sub_days:"), AdminFilter())
async def admin_sub_select_days(callback: CallbackQuery, state: FSMContext):
    days = int(callback.data.split(":")[1])
    data = await state.get_data()
    
    target_uid = data.get('target_uid')
    target_username = data.get('target_username')
    tier = data.get('admin_tier')
    
    sub_type = 'pro'
    set_subscription(target_uid, sub_type, days, tier, tier)
    
    await callback.message.edit_text(
        f"✅ <b>ПОДПИСКА ВЫДАНА!</b>\n\n"
        f"👤 Пользователь: <code>{target_uid}</code> (@{target_username})\n"
        f"📦 {TIER_INFO[tier]['emoji']} {TIER_INFO[tier]['name']}\n"
        f"📅 {days} дней"
    )
    
    try:
        await bot.send_message(
            target_uid,
            f"🎁 <b>Администратор выдал вам подписку!</b>\n\n"
            f"📦 {TIER_INFO[tier]['emoji']} {TIER_INFO[tier]['name']}\n"
            f"📅 {days} дней\n\n"
            f"🎉 Подписка активирована!"
        )
    except:
        pass
    
    await state.clear()

@dp.callback_query(F.data == "admin_sub_back_to_tier")
async def admin_sub_back_to_tier(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target_uid = data.get('target_uid')
    target_username = data.get('target_username')
    
    kb = InlineKeyboardBuilder()
    for tier_id in ["1", "2", "3"]:
        t = TIER_INFO[tier_id]
        kb.button(text=f"{t['emoji']} {t['name']}", callback_data=f"admin_sub_tier:{tier_id}")
    kb.button(text="❌ Отмена", callback_data="admin_sub_cancel")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"👤 Пользователь: <code>{target_uid}</code> (@{target_username})\n\n"
        f"📦 <b>Выберите тариф:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "admin_sub_cancel")
async def admin_sub_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Выдача подписки отменена")
    await callback.answer()

# ========== ОТЗЫВ ПОДПИСКИ ==========
@dp.message(F.text == "❌ Отозвать подписку", AdminFilter())
async def admin_remove_subscription(message: Message, state: FSMContext):
    await state.set_state(AdminSubStates.waiting_user_id)
    await state.update_data(action='remove_sub')
    await message.answer(
        "❌ <b>ОТЗЫВ ПОДПИСКИ</b>\n\n"
        "Отправьте ID пользователя:\n"
        "❌ /cancel для отмены"
    )

# ========== СТОП/СТАРТ БОТА ==========
@dp.message(F.text == "🛑 Стоп бот", AdminFilter())
async def stop_bot(message: Message):
    global bot_active
    bot_active = False
    
    all_scripts = get_all_scripts()
    stopped = 0
    for s in all_scripts:
        if s['status'] == 'running':
            if s.get('container_id'):
                kill_process(s['container_id'])
            update_script_status(s['id'], 'stopped')
            stopped += 1
    
    await message.answer(f"🔴 <b>БОТ ОСТАНОВЛЕН!</b>\n\n⏹ Остановлено хостов: {stopped}")

@dp.message(F.text == "🟢 Старт бот", AdminFilter())
async def start_bot(message: Message):
    global bot_active
    bot_active = True
    await message.answer("🟢 <b>БОТ ЗАПУЩЕН!</b>")

# ========== АДМИН-ПАНЕЛЬ ==========
@dp.message(F.text == "📊 Статистика", AdminFilter())
async def admin_stats(message: Message):
    u = get_all_users()
    s = get_all_scripts()
    r = len([x for x in s if x['status']=='running'])
    premium = len([x for x in u if x.get('is_premium', 0) == 1])
    total_balance = sum(x.get('balance', 0) for x in u)
    
    await message.answer(
        f"📊 <b>СТАТИСТИКА</b>\n\n"
        f"👥 Всего: {len(u)}\n"
        f"├ 🆓 Бесплатных: {len(u) - premium}\n"
        f"└ 💎 Премиум: {premium}\n"
        f"📦 Хостов: {len(s)} (🟢{r} 🔴{len(s)-r})\n"
        f"💰 Баланс users: {total_balance:.2f}₽\n"
        f"📈 Версия: {VERSION}"
    )

@dp.message(F.text == "👥 Пользователи", AdminFilter())
async def admin_users(message: Message):
    users = get_all_users()
    if not users:
        await message.answer("Нет пользователей")
        return
    
    text = f"👥 <b>ПОЛЬЗОВАТЕЛИ ({len(users)})</b>\n\n"
    for i, u in enumerate(users[:20], 1):
        sub = "💎" if u.get('is_premium') else "🆓"
        text += f"{i}. {sub} <code>{u['user_id']}</code> | {u.get('username', 'Нет')} | {u.get('balance', 0)}₽\n"
    
    await message.answer(text)

@dp.message(F.text == "📦 Хосты", AdminFilter())
async def admin_scripts(message: Message):
    scripts = get_all_scripts()
    if not scripts:
        await message.answer("Нет хостов")
        return
    
    text = f"📦 <b>ХОСТЫ ({len(scripts)})</b>\n\n"
    for i, s in enumerate(scripts[:15], 1):
        st = "🟢" if s['status']=='running' else "🔴"
        text += f"{i}. {st} <code>{s['id']}</code> | {s['name']} | user:{s['user_id']}\n"
    
    await message.answer(text)

@dp.message(F.text == "👤 Режим юзера", AdminFilter())
async def toggle_admin_mode(message: Message):
    uid = message.from_user.id
    if admin_mode.get(uid, True):
        admin_mode[uid] = False
        await message.answer("👤 <b>Режим пользователя</b>\n/admin для возврата", reply_markup=user_keyboard())
    else:
        admin_mode[uid] = True
        await message.answer("👑 <b>Режим админа</b>", reply_markup=admin_keyboard())

# ========== ПРОФИЛЬ И БАЛАНС ==========
@dp.message(F.text == "👤 Профиль")
async def btn_profile(message: Message):
    uid = message.from_user.id
    user = get_user(uid)
    if not user: await message.answer("❌ /start"); return
    
    sub_info, days, sub_type = get_subscription_info(uid)
    scripts = get_user_scripts(uid)
    mx_scripts, mx_size = get_user_limits(uid)
    
    text = (
        f"👤 <b>ПРОФИЛЬ</b>\n\n"
        f"├ 🆔 ID: <code>{uid}</code>\n"
        f"├ 👤 @{user.get('username', 'Нет')}\n"
        f"├ 💰 Баланс: {user.get('balance', 0):.2f}₽\n"
        f"├ 📋 Тариф: {sub_info}\n"
        f"├ 📦 Хостов: {len(scripts)}/{mx_scripts}\n"
        f"└ 📊 Макс. размер: {mx_size} МБ\n"
    )
    
    if days and isinstance(days, int) and days > 0:
        text += f"\n⏳ Осталось: {days} дн."
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🎫 Активировать промокод", callback_data="activate_promo")
    kb.button(text="💳 Пополнить баланс", callback_data="deposit_menu")
    kb.adjust(1)
    
    await message.answer(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "activate_promo")
async def activate_promo_callback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_code)
    await callback.message.answer("🎁 Введите промокод:")
    await callback.answer()

@dp.message(PromoStates.waiting_code)
async def process_promo(message: Message, state: FSMContext):
    code = message.text.strip()
    uid = message.from_user.id
    
    success, msg = activate_promo(uid, code)
    if success:
        await message.answer(f"✅ <b>{msg}</b>")
    else:
        await message.answer(f"❌ {msg}")
    
    await state.clear()

@dp.message(F.text == "💳 Пополнить")
async def btn_deposit(message: Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Карта/СБП", callback_data="dep_card")
    kb.button(text="⭐ Stars", callback_data="dep_stars")
    kb.adjust(1)
    await message.answer("💳 <b>ПОПОЛНЕНИЕ БАЛАНСА</b>\n\n👇 Выберите способ:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "dep_card")
async def dep_card(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DepositStates.waiting_amount)
    await state.update_data(dep_method="card")
    await callback.message.answer("💰 Введите сумму (мин 50₽):")
    await callback.answer()

@dp.callback_query(F.data == "dep_stars")
async def dep_stars(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DepositStates.waiting_amount)
    await state.update_data(dep_method="stars")
    await callback.message.answer("⭐ Введите сумму (мин 50⭐):")
    await callback.answer()

@dp.message(DepositStates.waiting_amount)
async def process_deposit_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    method = data.get("dep_method")
    
    try:
        amount = int(message.text)
        
        if method == "card":
            if amount < 50:
                await message.answer("❌ Минимум 50₽")
                return
            pending_payments[message.from_user.id] = {'type': 'balance', 'amount': amount}
            await message.answer(
                f"💳 <b>ОПЛАТА</b>\n\n"
                f"💰 Сумма: {amount}₽\n"
                f"🏦 Банк: СБЕР\n"
                f"💳 Номер: <code>2202206714879132</code>\n\n"
                f"📸 Отправьте скриншот оплаты"
            )
        
        elif method == "stars":
            if amount < 50:
                await message.answer("❌ Минимум 50⭐")
                return
            await message.answer_invoice(
                title="Пополнение баланса",
                description=f"+{amount}⭐ на баланс",
                payload=f"stars_{amount}",
                currency="XTR",
                prices=[{"label": f"+{amount}⭐", "amount": amount}]
            )
        
        await state.clear()
    except:
        await message.answer("❌ Неверная сумма!")

@dp.message(F.photo)
async def handle_screenshot(message: Message):
    uid = message.from_user.id
    if uid not in pending_payments: return
    
    pi = pending_payments.pop(uid)
    for aid in ADMIN_IDS:
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Подтвердить", callback_data=f"app_bal|{uid}|{pi['amount']}")
        kb.button(text="❌ Отклонить", callback_data=f"rej|{uid}")
        kb.adjust(2)
        
        await bot.send_photo(
            aid, 
            message.photo[-1].file_id,
            caption=f"💰 Пополнение +{pi['amount']}₽\n👤 {uid}",
            reply_markup=kb.as_markup()
        )
    
    await message.answer("✅ Чек отправлен на проверку!")

@dp.callback_query(F.data.startswith("app_bal|"))
async def approve_payment(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    
    try:
        _, uid, amount = callback.data.split("|")
        uid, amount = int(uid), float(amount)
        
        with get_db() as conn:
            conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (amount, uid))
            conn.commit()
        
        await bot.send_message(uid, f"✅ Баланс пополнен на {amount}₽")
        await callback.message.edit_caption(f"✅ Подтверждено +{amount}₽")
        await callback.answer("✅")
    except Exception as e:
        await callback.answer(f"❌ {e}")

@dp.callback_query(F.data.startswith("rej|"))
async def reject_payment(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    
    uid = int(callback.data.replace("rej|", ""))
    await bot.send_message(uid, "❌ Оплата отклонена")
    await callback.message.edit_caption("❌ Отклонено")
    await callback.answer()

@dp.callback_query(F.data == "deposit_menu")
async def deposit_menu(callback: CallbackQuery):
    await btn_deposit(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer("❌ Недостаточно средств")

# ========== ЗАПУСК ==========
async def main():
    init_db()
    await bot.set_my_commands([BotCommand(command="start", description="🚀 Главное меню")])
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info(f"🚀 Hosting Bot v{VERSION} запущен! Тарифы от 29₽!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    print(f"""
╔══════════════════════════════════════════╗
║     🚀 Hosting Bot v{VERSION}                ║
║     🌱 Новичок: 29₽                      ║
║     ⚡ Стандарт: 49₽                     ║
║     👑 Эксперт: 79₽                      ║
╚══════════════════════════════════════════╝
    """)
    asyncio.run(main())
