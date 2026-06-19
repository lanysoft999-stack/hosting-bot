# bot.py - Хостинг бот на aiogram 3.x (Полная рабочая версия)
import asyncio
import asyncio.subprocess
import logging
import sys
import os
import sqlite3
import uuid
import shutil
import zipfile
import subprocess
import signal
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, Filter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton,
    BotCommand, Message, CallbackQuery, FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web

# ========== ЛОГГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('hosting_bot')

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN", "8964647336:AAHs5cGpAuSGaXbDBeG-lmS6z0fgXIEM2rs")
VERSION = "33.0.0"
ADMIN_IDS = [314148464]
SUPPORT_URL = "https://t.me/hesers"
FREE_TRIAL_DAYS = 3
FREE_MAX_SCRIPTS = 3
FREE_MAX_SIZE_MB = 5
PORT = int(os.environ.get('PORT', 8000))

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR = BASE_DIR / "logs"
TEMP_DIR = BASE_DIR / "temp"
FILES_DIR = BASE_DIR / "user_files"
DATABASE_PATH = BASE_DIR / "bot_database.db"

for d in [SCRIPTS_DIR, LOGS_DIR, TEMP_DIR, FILES_DIR]:
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
                container_id TEXT, status TEXT DEFAULT 'stopped', size INTEGER,
                original_file TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY, type TEXT DEFAULT 'pro', days INTEGER DEFAULT 30,
                max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
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

def add_script(sid, uid, name, path, size, original_file=None):
    with get_db() as conn:
        conn.execute('INSERT INTO scripts (id, user_id, name, path, status, size, original_file) VALUES (?,?,?,?,?,?,?)',
                    (sid, uid, name, str(path), 'running', size, original_file))
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

async def run_script_async(sid, path):
    """Запускает Python скрипт"""
    path_obj = Path(path)
    
    # Ищем .py файлы
    if path_obj.is_file() and path_obj.suffix == '.py':
        py_files = [path_obj]
        path_obj = path_obj.parent
    elif path_obj.is_dir():
        py_files = list(path_obj.rglob("*.py"))
    else:
        return None, "Папка не найдена"
    
    if not py_files:
        return None, "Нет .py файлов"
    
    # Выбираем главный файл
    main_file = None
    for f in py_files:
        if f.name == 'main.py':
            main_file = f
            break
        elif f.name == 'bot.py':
            main_file = f
            break
    
    if not main_file:
        main_file = py_files[0]
    
    logger.info(f"[{sid}] Starting: {main_file}")
    
    try:
        # Устанавливаем зависимости
        req_file = path_obj / "requirements.txt"
        if req_file.exists():
            logger.info(f"[{sid}] Installing requirements...")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'pip', 'install', '-r', str(req_file),
                '--quiet', '--no-warn-script-location',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
        
        # Запускаем скрипт
        log_path = LOGS_DIR / f"{sid}.log"
        log_file = open(log_path, 'w')
        
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-u', str(main_file),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(path_obj)
        )
        
        logger.info(f"[{sid}] Started PID: {proc.pid}")
        return str(proc.pid), None
        
    except Exception as e:
        logger.error(f"[{sid}] Error: {e}")
        return None, str(e)

def kill_process(pid):
    try: 
        os.kill(int(pid), signal.SIGTERM)
        return True
    except: 
        return False

# ========== ВЕБ-СЕРВЕР ==========
async def health_check(request):
    scripts = get_all_scripts()
    running = len([s for s in scripts if s['status'] == 'running'])
    users = get_all_users()
    
    return web.json_response({
        'status': 'ok',
        'version': VERSION,
        'users': len(users),
        'scripts': len(scripts),
        'running': running,
        'timestamp': datetime.now().isoformat()
    })

async def web_status(request):
    scripts = get_all_scripts()
    running = len([s for s in scripts if s['status'] == 'running'])
    users = get_all_users()
    premium = len([u for u in users if u.get('is_premium')])
    banned = len([u for u in users if u.get('banned')])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Hosting Bot v{VERSION}</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial; margin: 20px; background: #f5f5f5; }}
            .card {{ background: white; padding: 20px; border-radius: 10px; margin: 10px 0; }}
            .online {{ color: #4CAF50; }}
            .stat {{ font-size: 24px; font-weight: bold; color: #2196F3; }}
        </style>
    </head>
    <body>
        <h1>🚀 Hosting Bot v{VERSION}</h1>
        <div class="card">
            <h2 class="online">● Online</h2>
            <p>👥 Пользователей: <span class="stat">{len(users)}</span> (💎{premium} 🚫{banned})</p>
            <p>📦 Хостов: <span class="stat">{len(scripts)}</span> (🟢{running} 🔴{len(scripts)-running})</p>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def run_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/ping', health_check)
    app.router.add_get('/status', web_status)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Web server on port {PORT}")

# ========== FSM ==========
class UploadStates(StatesGroup):
    waiting_file = State()

class SupportStates(StatesGroup):
    waiting_message = State()
    in_chat = State()

class BroadcastStates(StatesGroup):
    waiting_message = State()

class PromoCreateStates(StatesGroup):
    waiting_code = State()
    waiting_days = State()
    waiting_uses = State()

class AdminSubStates(StatesGroup):
    waiting_user_id = State()

# ========== БОТ ==========
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
bot_active = True

# ========== КЛАВИАТУРЫ ==========
def user_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📤 Загрузить"), KeyboardButton(text="💻 Хосты"))
    builder.row(KeyboardButton(text="🛒 Тарифы"), KeyboardButton(text="💳 Баланс"))
    builder.row(KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🆘 Помощь"))
    return builder.as_markup(resize_keyboard=True)

def admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="👥 Пользователи"), KeyboardButton(text="📊 Статистика"))
    builder.row(KeyboardButton(text="📨 Рассылка"), KeyboardButton(text="🎫 Промокод"))
    builder.row(KeyboardButton(text="🎁 Выдать"), KeyboardButton(text="❌ Отозвать"))
    builder.row(KeyboardButton(text="📦 Хосты"), KeyboardButton(text="💰 Баланс"))
    builder.row(KeyboardButton(text="🛑 Стоп"), KeyboardButton(text="🟢 Старт"))
    builder.row(KeyboardButton(text="👤 Юзер"), KeyboardButton(text="🆘 Помощь"))
    return builder.as_markup(resize_keyboard=True)

# ========== БЛОКИРОВКА ДОКУМЕНТОВ ==========
@dp.message(F.document)
async def block_docs(message: Message, state: FSMContext):
    if await state.get_state() == UploadStates.waiting_file:
        return
    await message.answer("❌ Используйте кнопку «📤 Загрузить»!")

# ========== СТАРТ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not get_user(uid): 
        create_user(uid, message.from_user.username)
        await message.answer(f"🎉 Добро пожаловать!\n🆓 Бесплатный тариф на {FREE_TRIAL_DAYS} дня!")
    
    await state.clear()
    
    if uid in ADMIN_IDS:
        scripts = get_all_scripts()
        running = len([s for s in scripts if s['status']=='running'])
        users = get_all_users()
        premium = len([u for u in users if u.get('is_premium')])
        banned = len([u for u in users if u.get('banned')])
        await message.answer(
            f"👑 <b>АДМИН</b> | v{VERSION}\n"
            f"👥 {len(users)} (💎{premium} 🚫{banned}) | 📦 {len(scripts)} | 🟢 {running}",
            reply_markup=admin_keyboard()
        )
        return
    
    user = get_user(uid)
    if user and user.get('banned'):
        return await message.answer("🚫 <b>ВЫ ЗАБАНЕНЫ!</b>")
    
    if not check_subscription(uid):
        kb = InlineKeyboardBuilder()
        kb.button(text="🛒 Тарифы от 29₽", callback_data="shop")
        kb.button(text="🎫 Промокод", callback_data="promo")
        kb.adjust(1)
        await message.answer("❌ Доступ закрыт!\n💎 Тарифы от 29₽", reply_markup=kb.as_markup())
        return
    
    scripts = get_user_scripts(uid)
    running = len([s for s in scripts if s['status']=='running'])
    sub_info, days = get_subscription_info(uid)
    limits = get_user_limits(uid)
    
    await message.answer(
        f"🚀 <b>HOSTING</b>\n📋 {sub_info} | 📦 {len(scripts)}/{limits[0]} | 🟢 {running}",
        reply_markup=user_keyboard()
    )

# ========== ЗАГРУЗКА ФАЙЛА ==========
@dp.message(F.text.in_(['📤 Загрузить', '📤 Загрузить файл']))
async def upload_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not check_subscription(uid):
        return await message.answer("❌ Нет доступа!")
    
    mx, size_mb = get_user_limits(uid)
    current = count_user_scripts(uid)
    if current >= mx:
        return await message.answer(f"❌ Лимит! {current}/{mx}")
    
    await state.set_state(UploadStates.waiting_file)
    await message.answer(f"📤 Отправьте .py или .zip файл (до {size_mb}МБ)\n❌ /cancel для отмены")

@dp.message(UploadStates.waiting_file, F.document)
async def upload_process(message: Message, state: FSMContext):
    uid = message.from_user.id
    doc = message.document
    fn = doc.file_name
    fs = doc.file_size
    _, mx = get_user_limits(uid)
    
    # Проверки
    if not fn.endswith(('.py', '.zip')):
        await state.clear()
        return await message.answer("❌ Только .py или .zip!")
    
    if fs > mx * 1024 * 1024:
        await state.clear()
        return await message.answer(f"❌ Макс {mx}МБ!")
    
    # Статус сообщение
    status_msg = await message.answer("📥 Скачивание файла...")
    
    # Временная папка
    tmp_dir = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / fn
    
    try:
        # Скачиваем файл
        await bot.download(doc, destination=tmp_file)
        await status_msg.edit_text("📦 Обработка файла...")
        
        # Сохраняем оригинал
        file_data = tmp_file.read_bytes()
        original_path = save_user_file(uid, fn, file_data)
        
        # ID скрипта
        sid = uuid.uuid4().hex[:8]
        script_dir = SCRIPTS_DIR / str(uid) / sid
        script_dir.mkdir(parents=True, exist_ok=True)
        
        if fn.endswith('.zip'):
            await status_msg.edit_text("📦 Распаковка архива...")
            with zipfile.ZipFile(tmp_file) as z:
                z.extractall(script_dir)
            
            py_files = list(script_dir.rglob("*.py"))
            if not py_files:
                shutil.rmtree(script_dir, ignore_errors=True)
                await status_msg.edit_text("❌ В архиве нет .py файлов!")
                await state.clear()
                return
            
            total_size = sum(f.stat().st_size for f in script_dir.rglob('*') if f.is_file())
        else:
            shutil.copy2(str(tmp_file), str(script_dir / fn))
            total_size = fs
        
        # Запуск
        await status_msg.edit_text("⚡ Запуск скрипта...")
        logger.info(f"User {uid} starting script {sid}: {fn}")
        
        pid, error = await run_script_async(sid, str(script_dir))
        
        if error:
            logger.error(f"Script {sid} error: {error}")
            await status_msg.edit_text(f"❌ Ошибка запуска:\n{error[:300]}")
            shutil.rmtree(script_dir, ignore_errors=True)
            await state.clear()
            return
        
        # Сохраняем в БД
        add_script(sid, uid, fn, str(script_dir), total_size, original_path)
        
        logger.info(f"Script {sid} started successfully with PID {pid}")
        
        kb = InlineKeyboardBuilder()
        kb.button(text="📥 Скачать файл", callback_data=f"dl:{sid}")
        kb.button(text="⏹ Остановить", callback_data=f"sc:run:{sid}")
        kb.button(text="💻 Мои хосты", callback_data="hosts")
        kb.adjust(2, 1)
        
        await status_msg.edit_text(
            f"✅ <b>ХОСТ ЗАПУЩЕН!</b>\n\n"
            f"📄 <code>{fn}</code>\n"
            f"🆔 <code>{sid}</code>\n"
            f"📦 {total_size/1024/1024:.1f} МБ\n"
            f"⚡ Статус: 🟢 Запущен\n"
            f"🔢 PID: <code>{pid}</code>",
            reply_markup=kb.as_markup()
        )
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:200]}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    
    await state.clear()

@dp.message(UploadStates.waiting_file)
async def upload_invalid(message: Message, state: FSMContext):
    if message.text and message.text == '/cancel':
        await state.clear()
        return await message.answer("❌ Отменено")
    await message.answer("❌ Отправьте .py или .zip файл!")

# ========== ХОСТЫ ==========
@dp.message(F.text.in_(['💻 Хосты', '💻 Мои хосты']))
async def show_hosts(message: Message):
    uid = message.from_user.id
    scripts = get_user_scripts(uid)
    
    if not scripts:
        kb = InlineKeyboardBuilder()
        kb.button(text="📤 Загрузить", callback_data="upload")
        return await message.answer("😔 Нет хостов", reply_markup=kb.as_markup())
    
    text = f"💻 <b>ХОСТЫ ({len(scripts)})</b>\n\n"
    kb = InlineKeyboardBuilder()
    
    for s in scripts:
        st = "🟢" if s['status']=='running' else "🔴"
        sz = (s['size'] or 0) / 1024 / 1024
        text += f"{st} <b>{s['name']}</b> | {sz:.1f}МБ | <code>{s['id']}</code>\n"
        
        kb.button(text="⏹" if s['status']=='running' else "▶️", callback_data=f"sc:run:{s['id']}")
        kb.button(text="📥", callback_data=f"dl:{s['id']}")
        kb.button(text="🗑", callback_data=f"sc:del:{s['id']}")
    
    kb.button(text="📤 Загрузить", callback_data="upload")
    kb.adjust(3, 1)
    await message.answer(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "upload")
async def cb_upload(callback: CallbackQuery, state: FSMContext):
    await upload_start(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "hosts")
async def cb_hosts(callback: CallbackQuery):
    await show_hosts(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("sc:"))
async def script_action(callback: CallbackQuery):
    _, action, sid = callback.data.split(":")
    uid = callback.from_user.id
    s = get_script(sid)
    if not s or s['user_id'] != uid:
        return await callback.answer("❌")
    
    if action == "run":
        if s['status'] == 'running':
            if s.get('container_id'): kill_process(s['container_id'])
            update_script_status(sid, 'stopped')
            await callback.answer("⏹ Остановлен")
        else:
            pid, err = await run_script_async(sid, s['path'])
            if pid:
                update_script_status(sid, 'running', pid)
                await callback.answer("▶️ Запущен")
            else:
                await callback.answer(f"❌ {err[:50]}")
    elif action == "del":
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Да", callback_data=f"sc:confirm:{sid}")
        kb.button(text="❌ Нет", callback_data="hosts")
        await callback.message.edit_text(f"🗑 Удалить <b>{s['name']}</b>?", reply_markup=kb.as_markup())
        return
    elif action == "confirm":
        if s.get('container_id'): kill_process(s['container_id'])
        if s.get('original_file') and os.path.exists(s['original_file']):
            os.unlink(s['original_file'])
        delete_script(sid, uid)
        shutil.rmtree(s['path'], ignore_errors=True)
        for lf in [f"{sid}.log", f"{sid}_install.log"]:
            p = LOGS_DIR / lf
            if p.exists(): p.unlink()
        await callback.answer("✅ Удалён")
    
    await show_hosts(callback.message)

@dp.callback_query(F.data.startswith("dl:"))
async def download_file(callback: CallbackQuery):
    sid = callback.data.split(":")[1]
    s = get_script(sid)
    if not s: return await callback.answer("❌")
    
    if s.get('original_file') and os.path.exists(s['original_file']):
        await callback.message.answer_document(FSInputFile(s['original_file']))
    else:
        zp = TEMP_DIR / f"{sid}.zip"
        with zipfile.ZipFile(zp, 'w') as zf:
            for f in Path(s['path']).rglob('*'):
                if f.is_file(): zf.write(f, f.relative_to(s['path']))
        await callback.message.answer_document(FSInputFile(zp))
        zp.unlink()
    await callback.answer("✅")

# ========== ТАРИФЫ ==========
@dp.message(F.text.in_(['🛒 Тарифы', '🛒 Магазин']))
async def shop(message: Message):
    kb = InlineKeyboardBuilder()
    for tid, t in TIER_INFO.items():
        kb.button(text=f"{t['name']} | {t['cpu']} {t['ram']} | {t['price_7d']}₽", callback_data=f"tier:{tid}")
    kb.adjust(1)
    await message.answer("<b>ТАРИФЫ</b>\n\n🌱 29₽ | ⚡ 49₽ | 👑 79₽", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "shop")
async def cb_shop(callback: CallbackQuery):
    await shop(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("tier:"))
async def select_tier(callback: CallbackQuery, state: FSMContext):
    tier = callback.data.split(":")[1]
    await state.update_data(tier=tier)
    
    kb = InlineKeyboardBuilder()
    for d, n in DAYS_NAMES.items():
        kb.button(text=f"{n} - {calc_price(tier, d)}₽", callback_data=f"days:{d}")
    kb.button(text="◀️ Назад", callback_data="shop")
    kb.adjust(1)
    
    t = TIER_INFO[tier]
    await callback.message.edit_text(
        f"{t['name']}\n├ CPU: {t['cpu']}\n├ RAM: {t['ram']}\n├ Storage: {t['storage']}\n└ Скриптов: {t['scripts']}\n\n📅 Срок:",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("days:"))
async def select_days(callback: CallbackQuery, state: FSMContext):
    days = callback.data.split(":")[1]
    data = await state.get_data()
    tier = data.get('tier')
    total = calc_price(tier, days)
    uid = callback.from_user.id
    user = get_user(uid)
    bal = user.get('balance', 0) if user else 0
    
    kb = InlineKeyboardBuilder()
    if bal >= total:
        kb.button(text=f"✅ Оплатить {total}₽", callback_data=f"pay:{tier}:{days}")
    else:
        kb.button(text=f"❌ {total}₽ (есть {bal}₽)", callback_data="bal")
    kb.button(text="💳 Пополнить", callback_data="bal")
    kb.button(text="◀️ Назад", callback_data=f"tier:{tier}")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"🧾 <b>ЗАКАЗ</b>\n\n{TIER_INFO[tier]['name']}\n📅 {DAYS_NAMES[days]}\n💰 {total}₽\n💳 Баланс: {bal}₽",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("pay:"))
async def pay(callback: CallbackQuery, state: FSMContext):
    _, tier, days = callback.data.split(":")
    total = calc_price(tier, days)
    uid = callback.from_user.id
    user = get_user(uid)
    
    if user.get('balance', 0) < total:
        return await callback.answer("❌ Мало средств")
    
    with get_db() as conn:
        conn.execute('UPDATE users SET balance=balance-? WHERE user_id=?', (total, uid))
    
    d = 7 if days=='7' else 30 if days=='30' else 90
    set_subscription(uid, 'pro', d, tier)
    await callback.message.edit_text(f"✅ <b>Куплено!</b>\n{TIER_INFO[tier]['name']}\n📅 {DAYS_NAMES[days]}\n💰 {total}₽")
    await state.clear()

# ========== БАЛАНС ==========
@dp.message(F.text.in_(['💳 Баланс', '💳 Пополнить']))
async def balance(message: Message):
    uid = message.from_user.id
    user = get_user(uid)
    bal = user.get('balance', 0) if user else 0
    
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Карта СБЕР", callback_data="dep_card")
    kb.button(text="⭐ Telegram Stars", callback_data="dep_stars")
    kb.adjust(1)
    await message.answer(f"💳 <b>Баланс: {bal:.0f}₽</b>\n\nВыберите способ:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "dep_card")
async def dep_card(callback: CallbackQuery):
    await callback.message.answer("💳 <b>Оплата</b>\n🏦 СБЕР\n💳 <code>2202206714879132</code>\n📸 Отправьте скриншот")
    await callback.answer()

@dp.callback_query(F.data == "dep_stars")
async def dep_stars(callback: CallbackQuery):
    await callback.message.answer("⭐ Отправьте количество звезд (мин 50):")
    await callback.answer()

@dp.callback_query(F.data == "bal")
async def cb_bal(callback: CallbackQuery):
    await balance(callback.message)
    await callback.answer()

@dp.message(F.photo)
async def screenshot(message: Message):
    uid = message.from_user.id
    for aid in ADMIN_IDS:
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ +100₽", callback_data=f"app_bal|{uid}|100")
        kb.button(text="✅ +200₽", callback_data=f"app_bal|{uid}|200")
        kb.button(text="✅ +500₽", callback_data=f"app_bal|{uid}|500")
        kb.button(text="❌ Отклонить", callback_data=f"rej|{uid}")
        kb.adjust(2)
        try:
            await bot.send_photo(aid, message.photo[-1].file_id, 
                               caption=f"📸 Оплата от {uid}", reply_markup=kb.as_markup())
        except: pass
    await message.answer("✅ Скриншот отправлен!")

@dp.callback_query(F.data.startswith("app_bal|"))
async def approve_payment(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    _, uid, amount = callback.data.split("|")
    uid, amount = int(uid), float(amount)
    with get_db() as conn:
        conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (amount, uid))
    await callback.message.edit_caption(f"✅ +{amount}₽")
    try: await bot.send_message(uid, f"✅ Баланс +{amount}₽")
    except: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("rej|"))
async def reject_payment(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split("|")[1])
    await callback.message.edit_caption("❌ Отклонено")
    try: await bot.send_message(uid, "❌ Отклонено")
    except: pass
    await callback.answer()

# ========== ПРОМОКОДЫ ==========
@dp.callback_query(F.data == "promo")
async def cb_promo(callback: CallbackQuery):
    await callback.message.answer("🎁 Отправьте промокод:")
    await callback.answer()

@dp.message(F.text.regexp(r'^[A-Za-z0-9]+$'))
async def promo_activate(message: Message):
    uid = message.from_user.id
    code = message.text.strip().upper()
    success, msg = activate_promo(uid, code)
    if success: await message.answer(f"✅ {msg}")

@dp.message(F.text == "🎫 Промокод")
async def admin_promo(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(PromoCreateStates.waiting_code)
    await message.answer("🎫 Введите код промокода:")

@dp.message(PromoCreateStates.waiting_code)
async def promo_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    await state.update_data(promo_code=code)
    await state.set_state(PromoCreateStates.waiting_days)
    kb = InlineKeyboardBuilder()
    kb.button(text="7 дней", callback_data="pdays:7")
    kb.button(text="30 дней", callback_data="pdays:30")
    kb.button(text="90 дней", callback_data="pdays:90")
    kb.adjust(1)
    await message.answer(f"Код: <b>{code}</b>\n📅 Срок:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("pdays:"))
async def promo_days(callback: CallbackQuery, state: FSMContext):
    days = int(callback.data.split(":")[1])
    await state.update_data(promo_days=days)
    await state.set_state(PromoCreateStates.waiting_uses)
    await callback.message.edit_text(f"📅 {days} дней\n👥 Макс. использований:")
    await callback.answer()

@dp.message(PromoCreateStates.waiting_uses)
async def promo_uses(message: Message, state: FSMContext):
    try: uses = int(message.text)
    except: return await message.answer("❌ Число!")
    data = await state.get_data()
    create_promo(data['promo_code'], 'pro', data['promo_days'], uses)
    await message.answer(f"✅ Промокод <b>{data['promo_code']}</b>\n📅 {data['promo_days']}дн | 👥 {uses}")
    await state.clear()

# ========== ПОДДЕРЖКА ==========
@dp.message(F.text.in_(['🆘 Помощь', '🆘 Поддержка']))
async def support(message: Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Чат с админом", callback_data="chat")
    kb.button(text="📞 Telegram", url=SUPPORT_URL)
    kb.adjust(1)
    await message.answer("🆘 <b>Поддержка</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "chat")
async def chat_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportStates.waiting_message)
    await callback.message.answer("💬 Отправьте сообщение:")
    await callback.answer()

@dp.message(SupportStates.waiting_message)
async def chat_send(message: Message, state: FSMContext):
    if message.text == '/cancel': await state.clear(); return await message.answer("❌")
    uid = message.from_user.id
    user = get_user(uid)
    username = f"@{user.get('username', uid)}" if user else str(uid)
    for aid in ADMIN_IDS:
        kb = InlineKeyboardBuilder()
        kb.button(text="✉️ Ответить", callback_data=f"reply:{uid}")
        try:
            if message.text:
                await bot.send_message(aid, f"📩 <b>{username}</b>\n🆔 {uid}\n\n{message.text}", reply_markup=kb.as_markup())
            elif message.photo:
                await bot.send_photo(aid, message.photo[-1].file_id, caption=f"📩 {username}\n🆔 {uid}", reply_markup=kb.as_markup())
        except: pass
    await state.clear()
    await message.answer("✅ Отправлено!")

@dp.callback_query(F.data.startswith("reply:"))
async def reply_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    await state.set_state(SupportStates.in_chat)
    await state.update_data(reply_to=uid)
    await callback.message.answer(f"✏️ Ответ для {uid}:")
    await callback.answer()

@dp.message(SupportStates.in_chat)
async def reply_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    data = await state.get_data()
    uid = data.get('reply_to')
    try:
        if message.text: await bot.send_message(uid, f"📩 <b>Ответ админа:</b>\n\n{message.text}")
        elif message.photo: await bot.send_photo(uid, message.photo[-1].file_id, caption="📩 Ответ админа")
        await message.answer("✅")
    except: await message.answer("❌")
    await state.clear()

# ========== РАССЫЛКА ==========
@dp.message(F.text == "📨 Рассылка")
async def broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(BroadcastStates.waiting_message)
    await message.answer("📨 Отправьте сообщение для рассылки:")

@dp.message(BroadcastStates.waiting_message)
async def broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    users = get_all_users()
    sent, err = 0, 0
    msg = await message.answer(f"📨 0/{len(users)}")
    for i, u in enumerate(users):
        try:
            if message.text: await bot.send_message(u['user_id'], f"📢 <b>Рассылка</b>\n\n{message.text}")
            elif message.photo: await bot.send_photo(u['user_id'], message.photo[-1].file_id, caption="📢 Рассылка")
            sent += 1
        except: err += 1
        if i % 10 == 0: await msg.edit_text(f"📨 {i}/{len(users)}")
        await asyncio.sleep(0.05)
    await msg.edit_text(f"✅ {sent} | ❌ {err}")
    await state.clear()

# ========== СТОП/СТАРТ ==========
@dp.message(F.text.in_(['🛑 Стоп', '🛑 Стоп бот']))
async def stop_bot(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    global bot_active; bot_active = False
    stopped = 0
    for s in get_all_scripts():
        if s['status'] == 'running':
            if s.get('container_id'): kill_process(s['container_id'])
            update_script_status(s['id'], 'stopped')
            stopped += 1
    await message.answer(f"🔴 Стоп! ⏹ {stopped}")

@dp.message(F.text.in_(['🟢 Старт', '🟢 Старт бот']))
async def start_bot(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    global bot_active; bot_active = True
    started, err = 0, 0
    for s in get_all_scripts():
        try:
            if os.path.exists(s['path']):
                pid, e = await run_script_async(s['id'], s['path'])
                if pid: update_script_status(s['id'], 'running', pid); started += 1
                else: err += 1
        except: err += 1
    await message.answer(f"🟢 Старт! ▶️ {started} | ❌ {err}")

# ========== АДМИН: ПОЛЬЗОВАТЕЛИ ==========
@dp.message(F.text == "👥 Пользователи")
async def admin_users_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    users = get_all_users()
    if not users: return await message.answer("Нет")
    kb = InlineKeyboardBuilder()
    for u in users[:30]:
        sub = "💎" if u.get('is_premium') else "🚫" if u.get('banned') else "🆓"
        kb.button(text=f"{sub} {u['user_id']} | {u.get('username','Нет')}", callback_data=f"auser:{u['user_id']}")
    kb.button(text="🔍 Поиск", callback_data="search_user")
    kb.adjust(1)
    await message.answer(f"👥 <b>ПОЛЬЗОВАТЕЛИ ({len(users)})</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "search_user")
async def search_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(AdminSubStates.waiting_user_id)
    await state.update_data(admin_action='manage_user')
    await callback.message.answer("🔍 ID:")
    await callback.answer()

@dp.callback_query(F.data.startswith("auser:"))
async def admin_user_manage(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    user = get_user(uid)
    if not user: return await callback.answer("❌")
    scripts = get_user_scripts(uid)
    running = len([s for s in scripts if s['status']=='running'])
    sub_info, days = get_subscription_info(uid)
    is_banned = user.get('banned', 0)
    text = f"👤 <b>user{uid}</b>\n@{user.get('username','Нет')}\n💰 {user.get('balance',0):.0f}₽\n📋 {sub_info}\n📦 {len(scripts)} (🟢{running})"
    if days: text += f"\n⏳ {days}дн"
    if is_banned: text += "\n🚫 ЗАБАНЕН"
    kb = InlineKeyboardBuilder()
    kb.button(text="📦 Хосты", callback_data=f"auser_hosts:{uid}")
    kb.button(text="📥 Скачать", callback_data=f"auser_download:{uid}")
    kb.button(text="🎁 Выдать", callback_data=f"auser_give:{uid}")
    kb.button(text="❌ Отозвать", callback_data=f"auser_remove:{uid}")
    kb.button(text="💰 Баланс", callback_data=f"auser_balance:{uid}")
    kb.button(text="🟢 Разбанить" if is_banned else "🚫 Забанить", callback_data=f"auser_unban:{uid}" if is_banned else f"auser_ban:{uid}")
    kb.button(text="🗑 Удалить хосты", callback_data=f"auser_delall:{uid}")
    kb.button(text="◀️ Назад", callback_data="admin_users_list")
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "admin_users_list")
async def back_to_users(callback: CallbackQuery):
    await admin_users_list(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("auser_hosts:"))
async def admin_view_user_hosts(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    scripts = get_user_scripts(uid)
    if not scripts: return await callback.answer("Нет")
    text = f"📦 <b>Хосты user{uid}</b>\n\n"
    kb = InlineKeyboardBuilder()
    for s in scripts:
        st = "🟢" if s['status']=='running' else "🔴"
        sz = (s['size'] or 0) / 1024 / 1024
        text += f"{st} <b>{s['name']}</b> | {sz:.1f}МБ\n"
        kb.button(text="⏹" if s['status']=='running' else "▶️", callback_data=f"asc:run:{s['id']}")
        kb.button(text="📥", callback_data=f"dl:{s['id']}")
        kb.button(text="🗑", callback_data=f"asc:del:{s['id']}")
    kb.button(text="◀️", callback_data=f"auser:{uid}")
    kb.adjust(3, 1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("asc:"))
async def admin_script_action(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    _, action, sid = callback.data.split(":")
    s = get_script(sid)
    if not s: return await callback.answer("❌")
    if action == "run":
        if s['status'] == 'running':
            if s.get('container_id'): kill_process(s['container_id'])
            update_script_status(sid, 'stopped')
        else:
            pid, err = await run_script_async(sid, s['path'])
            if pid: update_script_status(sid, 'running', pid)
    elif action == "del":
        if s.get('container_id'): kill_process(s['container_id'])
        if s.get('original_file') and os.path.exists(s['original_file']): os.unlink(s['original_file'])
        delete_script(sid)
        shutil.rmtree(s['path'], ignore_errors=True)
        for lf in [f"{sid}.log", f"{sid}_install.log"]:
            if (LOGS_DIR/lf).exists(): (LOGS_DIR/lf).unlink()
    await admin_view_user_hosts(callback.message)

@dp.callback_query(F.data.startswith("auser_download:"))
async def admin_download_user_files(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    scripts = get_user_scripts(uid)
    if not scripts: return await callback.answer("Нет")
    zp = TEMP_DIR / f"user_{uid}.zip"
    with zipfile.ZipFile(zp, 'w') as zf:
        for s in scripts:
            if s.get('original_file') and os.path.exists(s['original_file']):
                zf.write(s['original_file'], s['name'])
            elif os.path.exists(s['path']):
                for f in Path(s['path']).rglob('*'):
                    if f.is_file(): zf.write(f, f"{s['id']}/{f.relative_to(s['path'])}")
    await callback.message.answer_document(FSInputFile(zp), caption=f"📦 user{uid}")
    zp.unlink()
    await callback.answer("✅")

@dp.callback_query(F.data.startswith("auser_give:"))
async def admin_quick_give(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    kb = InlineKeyboardBuilder()
    for tid, t in TIER_INFO.items():
        kb.button(text=f"{t['name']} 30дн", callback_data=f"agive:{uid}:{tid}")
    kb.button(text="◀️", callback_data=f"auser:{uid}")
    kb.adjust(1)
    await callback.message.edit_text(f"🎁 Выдать user{uid}", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("auser_remove:"))
async def admin_quick_remove(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да", callback_data=f"auser_remove_confirm:{uid}")
    kb.button(text="❌ Нет", callback_data=f"auser:{uid}")
    kb.adjust(2)
    await callback.message.edit_text(f"⚠️ Отозвать у user{uid}?", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("auser_remove_confirm:"))
async def admin_remove_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    remove_subscription(uid)
    await callback.message.edit_text(f"✅ Отозвано у {uid}")
    try: await bot.send_message(uid, "⚠️ Подписка отозвана")
    except: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("auser_balance:"))
async def admin_change_balance(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    user = get_user(uid)
    await state.update_data(admin_action='change_balance', target_uid=uid)
    await state.set_state(AdminSubStates.waiting_user_id)
    await callback.message.answer(f"💰 user{uid}: {user.get('balance',0):.0f}₽\nСумма (+/-):")
    await callback.answer()

@dp.callback_query(F.data.startswith("auser_ban:"))
async def admin_ban(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    ban_user(uid)
    for s in get_user_scripts(uid):
        if s.get('container_id'): kill_process(s['container_id'])
        update_script_status(s['id'], 'stopped')
    await callback.message.edit_text(f"🚫 user{uid} забанен")
    try: await bot.send_message(uid, "🚫 Вы забанены!")
    except: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("auser_unban:"))
async def admin_unban(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    unban_user(uid)
    await callback.message.edit_text(f"🟢 user{uid} разбанен")
    try: await bot.send_message(uid, "✅ Вы разбанены!")
    except: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("auser_delall:"))
async def admin_delall(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    scripts = get_user_scripts(uid)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да", callback_data=f"auser_delall_confirm:{uid}")
    kb.button(text="❌ Нет", callback_data=f"auser:{uid}")
    kb.adjust(2)
    await callback.message.edit_text(f"⚠️ Удалить {len(scripts)} хостов user{uid}?", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("auser_delall_confirm:"))
async def admin_delall_confirm(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    uid = int(callback.data.split(":")[1])
    for s in get_user_scripts(uid):
        if s.get('container_id'): kill_process(s['container_id'])
        if s.get('original_file') and os.path.exists(s['original_file']): os.unlink(s['original_file'])
        delete_script(s['id'])
        shutil.rmtree(s['path'], ignore_errors=True)
        for lf in [f"{s['id']}.log", f"{s['id']}_install.log"]:
            if (LOGS_DIR/lf).exists(): (LOGS_DIR/lf).unlink()
    await callback.message.edit_text(f"✅ Хосты user{uid} удалены")
    await callback.answer()

# ========== ОБРАБОТКА ID ==========
@dp.message(AdminSubStates.waiting_user_id)
async def admin_sub_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    data = await state.get_data()
    action = data.get('admin_action')
    
    if action == 'change_balance':
        uid = data.get('target_uid')
        try: amount = float(message.text.strip())
        except: return await message.answer("❌")
        with get_db() as conn:
            conn.execute('UPDATE users SET balance=balance+? WHERE user_id=?', (amount, uid))
        user = get_user(uid)
        await message.answer(f"✅ Баланс user{uid}: {user.get('balance',0):.0f}₽")
        await state.clear()
        return
    
    if action == 'manage_user':
        try: uid = int(message.text.strip())
        except: return await message.answer("❌")
        user = get_user(uid)
        if not user: return await message.answer("❌")
        scripts = get_user_scripts(uid)
        running = len([s for s in scripts if s['status']=='running'])
        sub_info, days = get_subscription_info(uid)
        is_banned = user.get('banned', 0)
        kb = InlineKeyboardBuilder()
        kb.button(text="📦 Хосты", callback_data=f"auser_hosts:{uid}")
        kb.button(text="📥 Скачать", callback_data=f"auser_download:{uid}")
        kb.button(text="🎁 Выдать", callback_data=f"auser_give:{uid}")
        kb.button(text="❌ Отозвать", callback_data=f"auser_remove:{uid}")
        kb.button(text="💰 Баланс", callback_data=f"auser_balance:{uid}")
        kb.button(text="🟢 Разбанить" if is_banned else "🚫 Забанить", callback_data=f"auser_unban:{uid}" if is_banned else f"auser_ban:{uid}")
        kb.button(text="🗑 Удалить", callback_data=f"auser_delall:{uid}")
        kb.adjust(1)
        await message.answer(f"👤 user{uid}\n📦 {len(scripts)} (🟢{running})\n📋 {sub_info}" + ("\n🚫" if is_banned else ""), reply_markup=kb.as_markup())
        await state.clear()
    else:
        try: uid = int(message.text.strip())
        except: return await message.answer("❌")
        if action == 'give':
            kb = InlineKeyboardBuilder()
            for tid, t in TIER_INFO.items():
                kb.button(text=f"{t['name']}", callback_data=f"agive:{uid}:{tid}")
            kb.adjust(1)
            await message.answer(f"👤 {uid}\nТариф:", reply_markup=kb.as_markup())
        elif action == 'remove':
            remove_subscription(uid)
            await message.answer(f"✅ Отозвано у {uid}")
        await state.clear()

@dp.callback_query(F.data.startswith("agive:"))
async def admin_give_final(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    _, uid, tier = callback.data.split(":")
    uid, days = int(uid), 30
    set_subscription(uid, 'pro', days, tier)
    await callback.message.edit_text(f"✅ {TIER_INFO[tier]['name']} 30дн user{uid}")
    try: await bot.send_message(uid, f"🎁 {TIER_INFO[tier]['name']} 30дн!")
    except: pass
    await callback.answer()

# ========== ВЫДАЧА/ОТЗЫВ ==========
@dp.message(F.text.in_(['🎁 Выдать', '❌ Отозвать']))
async def admin_sub(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    action = 'give' if 'Выдать' in message.text else 'remove'
    await state.set_state(AdminSubStates.waiting_user_id)
    await state.update_data(admin_action=action)
    await message.answer(f"🆔 ID для {'выдачи' if action=='give' else 'отзыва'}:")

# ========== ПРОЧЕЕ ==========
@dp.message(F.text == "👤 Юзер")
async def user_mode(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("👤 Юзер", reply_markup=user_keyboard())

@dp.message(F.text == "📊 Статистика")
async def stats(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    u, s = get_all_users(), get_all_scripts()
    r = len([x for x in s if x['status']=='running'])
    premium = len([x for x in u if x.get('is_premium')])
    banned = len([x for x in u if x.get('banned')])
    bal = sum(x.get('balance', 0) for x in u)
    await message.answer(f"📊 <b>v{VERSION}</b>\n👥 {len(u)} (💎{premium} 🚫{banned})\n📦 {len(s)} (🟢{r})\n💰 {bal:.0f}₽")

@dp.message(F.text == "📦 Хосты")
async def admin_hosts(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    scripts = get_all_scripts()
    if not scripts: return await message.answer("Нет")
    text = f"📦 <b>ХОСТЫ ({len(scripts)})</b>\n\n"
    kb = InlineKeyboardBuilder()
    for s in scripts[:20]:
        st = "🟢" if s['status']=='running' else "🔴"
        text += f"{st} <code>{s['id']}</code> | {s['name']} | u{s['user_id']}\n"
        kb.button(text="⏹" if s['status']=='running' else "▶️", callback_data=f"asc:run:{s['id']}")
        kb.button(text="📥", callback_data=f"dl:{s['id']}")
        kb.button(text="🗑", callback_data=f"asc:del:{s['id']}")
    kb.adjust(3)
    await message.answer(text, reply_markup=kb.as_markup())

@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u: return await message.answer("❌")
    sub_info, days = get_subscription_info(uid)
    scr = get_user_scripts(uid)
    mx, sz = get_user_limits(uid)
    text = f"👤 <b>Профиль</b>\n🆔 {uid}\n💰 {u.get('balance',0):.0f}₽\n📋 {sub_info}\n📦 {len(scr)}/{mx}\n📊 {sz}МБ"
    if days: text += f"\n⏳ {days}дн"
    kb = InlineKeyboardBuilder()
    kb.button(text="🎫 Промокод", callback_data="promo")
    kb.button(text="💳 Пополнить", callback_data="bal")
    kb.adjust(1)
    await message.answer(text, reply_markup=kb.as_markup())

# ========== ЗАПУСК ==========
async def main():
    init_db()
    await bot.set_my_commands([
        BotCommand(command="start", description="🚀 Меню"),
        BotCommand(command="admin", description="👑 Админ")
    ])
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(run_web_server())
    logger.info(f"🚀 Hosting Bot v{VERSION} started on port {PORT}")
    await dp.start_polling(bot)

if __name__ == '__main__':
    print(f"🚀 Hosting Bot v{VERSION}")
    asyncio.run(main())
