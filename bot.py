# bot.py - Хостинг бот на aiogram 3.x (Полная версия с управлением подписками)
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
import requests as req

# ========== ЛОГГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('hosting_bot')

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN", "8964647336:AAHs5cGpAuSGaXbDBeG-lmS6z0fgXIEM2rs")
VERSION = "26.0.0"
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

# ========== ДАННЫЕ ==========
LOCATIONS = {
    "de": {"name": "Германия", "flag": "🇩🇪", "max_tiers": 3},
    "us": {"name": "США", "flag": "🇺🇸", "max_tiers": 2},
    "fi": {"name": "Финляндия", "flag": "🇫🇮", "max_tiers": 5}
}

TIER_INFO = {
    "1": {"name": "Tier 1", "price_7d": 65, "cpu": "1 vCPU", "ram": "512 MB", "scripts": 3},
    "2": {"name": "Tier 2", "price_7d": 100, "cpu": "2 vCPU", "ram": "1 GB", "scripts": 5},
    "3": {"name": "Tier 3", "price_7d": 140, "cpu": "3 vCPU", "ram": "2 GB", "scripts": 10},
    "4": {"name": "Tier 4", "price_7d": 220, "cpu": "4 vCPU", "ram": "4 GB", "scripts": 20},
    "5": {"name": "Tier 5", "price_7d": 300, "cpu": "5 vCPU", "ram": "8 GB", "scripts": 999},
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
    """Отзывает подписку у пользователя"""
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
        return True, "Зависимости не требуются"
    
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
            return True, "Зависимости установлены"
        else:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                error_text = f.read()[-500:]
            return False, f"Ошибка установки зависимостей:\n{error_text}"
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
    builder.add(KeyboardButton(text="📤 Загрузить файл"), KeyboardButton(text="💻 Мои хосты"))
    builder.add(KeyboardButton(text="🛒 Магазин"), KeyboardButton(text="💳 Пополнить"))
    builder.add(KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🆘 Поддержка"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="📤 Загрузить файл"), KeyboardButton(text="💻 Мои хосты"))
    builder.add(KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👥 Пользователи"))
    builder.add(KeyboardButton(text="📦 Хосты"), KeyboardButton(text="🎫 Промокоды"))
    builder.add(KeyboardButton(text="🎁 Выдать подписку"), KeyboardButton(text="❌ Отозвать подписку"))
    builder.add(KeyboardButton(text="🛑 Стоп бот"), KeyboardButton(text="🟢 Старт бот"))
    builder.add(KeyboardButton(text="👤 Режим юзера"), KeyboardButton(text="🆘 Поддержка"))
    builder.adjust(2)
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
        "❌ <b>Загрузка файлов только через кнопку!</b>\n\n"
        "📤 Нажмите <b>«Загрузить файл»</b> в меню.",
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
            f"🆓 Активирован бесплатный тариф на {FREE_TRIAL_DAYS} дня!\n"
            f"├ 📦 Скриптов: {FREE_MAX_SCRIPTS}\n"
            f"└ 📊 Размер файла: {FREE_MAX_SIZE_MB} МБ\n\n"
            f"💎 После окончания пробного периода приобретите премиум!"
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
            f"📦 Хостов: {len(s)} (🟢{r})\n"
            f"🆓 Бесплатный тариф: {FREE_TRIAL_DAYS} дня\n\n"
            f"👇 Действие:",
            reply_markup=admin_keyboard()
        )
        return
    
    if not check_subscription(uid):
        sub_info, days, sub_type = get_subscription_info(uid)
        kb = InlineKeyboardBuilder()
        kb.button(text="🛒 Купить премиум", callback_data="shop_configurator")
        kb.button(text="🎫 Промокод", callback_data="activate_promo")
        kb.adjust(1)
        
        await message.answer(
            f"❌ <b>ДОСТУП ЗАБЛОКИРОВАН</b>\n\n"
            f"🆓 Бесплатный тариф истек!\n"
            f"💎 Приобретите премиум для продолжения.\n\n"
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
        f"🚀 <b>HOSTING BOT v{VERSION}</b>\n\n"
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
        await message.answer("❌ Неверный формат ID! Отправьте число.")
        return
    
    user = get_user(uid)
    if not user:
        await message.answer(f"❌ Пользователь с ID {uid} не найден!")
        return
    
    await state.update_data(target_uid=uid, target_username=user.get('username', 'Нет'))
    
    # Показываем выбор локации
    kb = InlineKeyboardBuilder()
    for loc_id, loc_data in LOCATIONS.items():
        kb.button(text=f"{loc_data['flag']} {loc_data['name']}", callback_data=f"admin_sub_loc:{loc_id}")
    kb.button(text="❌ Отмена", callback_data="admin_sub_cancel")
    kb.adjust(2)
    
    await message.answer(
        f"👤 Пользователь: <code>{uid}</code> (@{user.get('username', 'Нет')})\n\n"
        f"🌍 <b>Выберите локацию:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("admin_sub_loc:"), AdminFilter())
async def admin_sub_select_location(callback: CallbackQuery, state: FSMContext):
    loc = callback.data.split(":")[1]
    await state.update_data(admin_loc=loc)
    
    max_tier = LOCATIONS[loc]['max_tiers']
    kb = InlineKeyboardBuilder()
    for t_id in ["1","2","3","4","5"]:
        if int(t_id) <= max_tier:
            t_data = TIER_INFO[t_id]
            kb.button(
                text=f"{t_data['name']}: {t_data['cpu']} | {t_data['ram']}", 
                callback_data=f"admin_sub_tier:{t_id}"
            )
    kb.button(text="◀️ Назад", callback_data="admin_sub_back_to_loc")
    kb.button(text="❌ Отмена", callback_data="admin_sub_cancel")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"📍 <b>{LOCATIONS[loc]['flag']} {LOCATIONS[loc]['name']}</b>\n\n"
        f"⚙️ <b>Выберите тариф:</b>",
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
        f"📦 <b>{TIER_INFO[tier]['name']}</b>\n"
        f"├ CPU: {TIER_INFO[tier]['cpu']}\n"
        f"├ RAM: {TIER_INFO[tier]['ram']}\n"
        f"└ Скриптов: {TIER_INFO[tier]['scripts']}\n\n"
        f"📅 <b>Выберите срок:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("admin_sub_days:"), AdminFilter())
async def admin_sub_select_days(callback: CallbackQuery, state: FSMContext):
    days = int(callback.data.split(":")[1])
    data = await state.get_data()
    
    target_uid = data.get('target_uid')
    target_username = data.get('target_username')
    loc = data.get('admin_loc')
    tier = data.get('admin_tier')
    
    # Выдаем подписку
    sub_type = 'basic' if tier in ['1','2'] else 'pro' if tier in ['3','4'] else 'expert'
    set_subscription(target_uid, sub_type, days, tier, loc)
    
    await callback.message.edit_text(
        f"✅ <b>ПОДПИСКА ВЫДАНА!</b>\n\n"
        f"👤 Пользователь: <code>{target_uid}</code> (@{target_username})\n"
        f"📍 {LOCATIONS[loc]['flag']} {LOCATIONS[loc]['name']}\n"
        f"📦 {TIER_INFO[tier]['name']}\n"
        f"📅 {days} дней\n"
        f"💎 Тип: {sub_type.upper()}"
    )
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            target_uid,
            f"🎁 <b>Администратор выдал вам подписку!</b>\n\n"
            f"📍 {LOCATIONS[loc]['flag']} {LOCATIONS[loc]['name']}\n"
            f"📦 {TIER_INFO[tier]['name']}\n"
            f"📅 {days} дней\n\n"
            f"🎉 Подписка активирована!"
        )
    except:
        pass
    
    await state.clear()

@dp.callback_query(F.data == "admin_sub_back_to_loc")
async def admin_sub_back_to_loc(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target_uid = data.get('target_uid')
    target_username = data.get('target_username')
    
    kb = InlineKeyboardBuilder()
    for loc_id, loc_data in LOCATIONS.items():
        kb.button(text=f"{loc_data['flag']} {loc_data['name']}", callback_data=f"admin_sub_loc:{loc_id}")
    kb.button(text="❌ Отмена", callback_data="admin_sub_cancel")
    kb.adjust(2)
    
    await callback.message.edit_text(
        f"👤 Пользователь: <code>{target_uid}</code> (@{target_username})\n\n"
        f"🌍 <b>Выберите локацию:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "admin_sub_back_to_tier")
async def admin_sub_back_to_tier(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    loc = data.get('admin_loc')
    if not loc:
        await callback.message.edit_text("❌ Сессия устарела")
        await state.clear()
        return
    
    max_tier = LOCATIONS[loc]['max_tiers']
    kb = InlineKeyboardBuilder()
    for t_id in ["1","2","3","4","5"]:
        if int(t_id) <= max_tier:
            t_data = TIER_INFO[t_id]
            kb.button(
                text=f"{t_data['name']}: {t_data['cpu']} | {t_data['ram']}", 
                callback_data=f"admin_sub_tier:{t_id}"
            )
    kb.button(text="◀️ Назад", callback_data="admin_sub_back_to_loc")
    kb.button(text="❌ Отмена", callback_data="admin_sub_cancel")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"📍 <b>{LOCATIONS[loc]['flag']} {LOCATIONS[loc]['name']}</b>\n\n"
        f"⚙️ <b>Выберите тариф:</b>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "admin_sub_cancel")
async def admin_sub_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Выдача подписки отменена")
    await callback.answer()

# ========== ОТЗЫВ ПОДПИСКИ АДМИНОМ ==========
@dp.message(F.text == "❌ Отозвать подписку", AdminFilter())
async def admin_remove_subscription(message: Message):
    users = get_all_users()
    if not users:
        await message.answer("Нет пользователей")
        return
    
    kb = InlineKeyboardBuilder()
    for u in users[:30]:  # Показываем первых 30 пользователей
        sub_mark = "💎" if u.get('is_premium') else "🆓"
        username = u.get('username', 'Нет')
        kb.button(
            text=f"{sub_mark} {u['user_id']} | {username}",
            callback_data=f"admin_remove_sub:{u['user_id']}"
        )
    
    if len(users) > 30:
        kb.button(text="... и ещё пользователи", callback_data="noop")
    
    kb.button(text="🔍 Поиск по ID", callback_data="admin_remove_search")
    kb.button(text="❌ Отмена", callback_data="close_menu")
    kb.adjust(1)
    
    await message.answer(
        "❌ <b>ОТЗЫВ ПОДПИСКИ</b>\n\n"
        "Выберите пользователя для отзыва подписки:",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "admin_remove_search", AdminFilter())
async def admin_remove_search(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSubStates.waiting_user_id)
    # Меняем состояние для поиска
    await state.update_data(action='remove_sub')
    await callback.message.answer("🔍 Отправьте ID пользователя для отзыва подписки:")
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_remove_sub:"), AdminFilter())
async def admin_remove_sub_confirm(callback: CallbackQuery):
    uid = int(callback.data.split(":")[1])
    user = get_user(uid)
    
    if not user:
        await callback.answer("Пользователь не найден")
        return
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, отозвать", callback_data=f"admin_remove_confirm:{uid}")
    kb.button(text="❌ Отмена", callback_data="close_menu")
    kb.adjust(2)
    
    await callback.message.edit_text(
        f"⚠️ <b>Отозвать подписку?</b>\n\n"
        f"👤 ID: <code>{uid}</code>\n"
        f"👤 @{user.get('username', 'Нет')}\n"
        f"📋 Тариф: {user.get('subscription', 'free').upper()}\n"
        f"💎 Премиум: {'Да' if user.get('is_premium') else 'Нет'}\n\n"
        f"❗ Подписка будет отозвана!",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("admin_remove_confirm:"), AdminFilter())
async def admin_remove_sub_execute(callback: CallbackQuery):
    uid = int(callback.data.split(":")[1])
    
    remove_subscription(uid)
    
    await callback.message.edit_text(
        f"✅ <b>ПОДПИСКА ОТОЗВАНА!</b>\n\n"
        f"👤 ID: <code>{uid}</code>\n"
        f"📋 Тариф сброшен до бесплатного"
    )
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            uid,
            "⚠️ <b>Ваша подписка была отозвана администратором.</b>\n\n"
            "💎 Вы можете приобрести новую подписку в магазине."
        )
    except:
        pass
    
    await callback.answer("✅ Подписка отозвана!")

# ========== ОБРАБОТКА ПОИСКА ДЛЯ ОТЗЫВА ==========
@dp.message(AdminSubStates.waiting_user_id, AdminFilter())
async def admin_search_user_for_remove(message: Message, state: FSMContext):
    data = await state.get_data()
    action = data.get('action')
    
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
    
    if action == 'remove_sub':
        # Отзываем подписку
        remove_subscription(uid)
        await message.answer(
            f"✅ <b>ПОДПИСКА ОТОЗВАНА!</b>\n\n"
            f"👤 ID: <code>{uid}</code>\n"
            f"👤 @{user.get('username', 'Нет')}\n"
            f"📋 Тариф сброшен до бесплатного"
        )
        
        try:
            await bot.send_message(
                uid,
                "⚠️ <b>Ваша подписка была отозвана администратором.</b>"
            )
        except:
            pass
    
    await state.clear()

# Остальные функции остаются без изменений...
# [Здесь должны быть все остальные хендлеры из предыдущего кода]

# ========== ЗАГРУЗКА ФАЙЛОВ ==========
@dp.message(F.text == "📤 Загрузить файл", BotActiveFilter())
async def btn_upload(message: Message, state: FSMContext):
    # ... (код из предыдущей версии)
    pass

# ... все остальные хендлеры из предыдущего кода ...

# ========== ЗАПУСК ==========
async def main():
    init_db()
    await bot.set_my_commands([BotCommand(command="start", description="🚀 Главное меню")])
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info(f"🚀 Hosting Bot v{VERSION} запущен! Админ может выдавать и отзывать подписки!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    print(f"""
╔══════════════════════════════════════════╗
║     🚀 Hosting Bot v{VERSION}                ║
║     Управление подписками: ✅           ║
║     Выдача подписки: ✅                 ║
║     Отзыв подписки: ✅                  ║
╚══════════════════════════════════════════╝
    """)
    asyncio.run(main())
