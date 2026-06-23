# ============================================================
#  Ohoster Render — PRODUCTION READY
#  Основан на: Flask + SQLAlchemy + Pyrogram + Gunicorn
# ============================================================

import asyncio
import os
import uuid
import shutil
import zipfile
import time
import signal
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, request
from sqlalchemy import create_engine, Column, String, Integer, Float, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from pyrogram.enums import ParseMode

import aiohttp
import asyncio

# ==========================================================
#  1. НАСТРОЙКИ
# ==========================================================
TOKEN = "1456462948:AAH1wfMw5sxS9p4niC3yjoxO-ndhD3xC1gY"
ADMIN_IDS = [314148464]

FREE_SCRIPTS = 5
FREE_SIZE_MB = 10

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
DB_PATH = BASE_DIR / "bot.db"
LOG_PATH = BASE_DIR / "bot.log"

# Создаём папки
for d in [SCRIPTS_DIR, TEMP_DIR]:
    d.mkdir(exist_ok=True)

# ==========================================================
#  2. ЛОГИРОВАНИЕ (Industry Standard)
# ==========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("OhosterRender")

# ==========================================================
#  3. БАЗА ДАННЫХ (SQLAlchemy — самый популярный ORM)
# ==========================================================
engine = create_engine(f"sqlite:///{DB_PATH}?check_same_thread=False")
Base = declarative_base()
Session = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = 'users'
    user_id = Column(Integer, primary_key=True)
    username = Column(String)
    created_at = Column(String)

class Script(Base):
    __tablename__ = 'scripts'
    id = Column(String, primary_key=True)
    user_id = Column(Integer)
    name = Column(String)
    path = Column(String)
    status = Column(String)
    size = Column(Integer)
    thread_id = Column(Integer, nullable=True)
    created_at = Column(String)
    last_seen = Column(Float, default=time.time())

Base.metadata.create_all(engine)

# ==========================================================
#  4. ASYNC ДВИЖОК ЗАПУСКА (Самый популярный подход)
# ==========================================================
async def run_script_async(path):
    """Запускает Python-скрипт в асинхронном режиме."""
    py_files = list(Path(path).rglob("*.py"))
    if not py_files:
        return None
    main = py_files[0]
    for f in py_files:
        if f.name == 'main.py':
            main = f
            break

    # Используем asyncio.create_subprocess_exec — популярный способ
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(main),
        cwd=str(path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return proc.pid

async def monitor_async():
    """Асинхронный мониторинг с использованием asyncio."""
    while True:
        try:
            session = Session()
            running = session.query(Script).filter(Script.status == 'running', Script.thread_id != None).all()
            for script in running:
                # Проверяем процесс через os.kill(pid, 0) — популярный метод
                try:
                    os.kill(script.thread_id, 0)
                except OSError:
                    logger.warning(f"Скрипт {script.id} упал. Перезапуск...")
                    new_pid = await run_script_async(script.path)
                    if new_pid:
                        script.status = 'running'
                        script.thread_id = new_pid
                        script.last_seen = time.time()
                        session.commit()
                    else:
                        script.status = 'stopped'
                        script.thread_id = None
                        session.commit()
            session.close()
        except Exception as e:
            logger.error(f"Ошибка мониторинга: {e}")
        await asyncio.sleep(20)

# Запускаем мониторинг
asyncio.create_task(monitor_async())

# ==========================================================
#  5. FLASK — САМЫЙ ПОПУЛЯРНЫЙ ВЕБ-СЕРВЕР
# ==========================================================
app = Flask(__name__)

@app.route('/')
def index():
    return "<h1>Ohoster Render Web Service</h1><p>Status: Running</p>"

# ==========================================================
#  6. PYROGRAM — САМЫЙ ПОПУЛЯРНЫЙ TELEGRAM КЛИЕНТ
# ==========================================================
bot = Client("ohoster_bot", bot_token=TOKEN, parse_mode=ParseMode.HTML)

waiting = set()
active_threads = {}

# ==========================================================
#  7. КЛАВИАТУРЫ (Популярный дизайн)
# ==========================================================
def user_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📤 Загрузить"), KeyboardButton("💻 Мои хосты")],
            [KeyboardButton("👤 Профиль"), KeyboardButton("🆘 Помощь")]
        ],
        resize_keyboard=True
    )

# ==========================================================
#  8. ОБРАБОТЧИКИ (Популярные паттерны)
# ==========================================================
@bot.on_message(filters.command("start"))
async def start(client, message):
    uid = message.from_user.id
    session = Session()
    if not session.query(User).filter(User.user_id == uid).first():
        new_user = User(user_id=uid, username=message.from_user.username, created_at=datetime.now().isoformat())
        session.add(new_user)
        session.commit()
    session.close()

    # Получаем статистику
    session = Session()
    scripts = session.query(Script).filter(Script.user_id == uid).all()
    running = sum(1 for s in scripts if s.status == 'running')
    session.close()

    text = f"༆ <b>Ohoster Render Web</b>\n\n"
    text += f"📦 Запущено: {running} скриптов\n"
    text += f"🚀 Хостинг: Render Web Service (0.5 CPU)\n"
    text += f"🛡 Режим: Async + SQLAlchemy"
    
    await message.reply(text, reply_markup=user_kb())

@bot.on_message(filters.text == "📤 Загрузить")
async def upload(client, message):
    uid = message.from_user.id
    session = Session()
    count = session.query(Script).filter(Script.user_id == uid).count()
    session.close()
    
    if count >= FREE_SCRIPTS:
        await message.reply(f"❌ Лимит {FREE_SCRIPTS} скриптов!")
        return
    waiting.add(uid)
    await message.reply(f"📤 Отправьте .py или .zip (до {FREE_SIZE_MB}МБ)")

@bot.on_message(filters.document)
async def handle_doc(client, message):
    uid = message.from_user.id
    if uid not in waiting:
        return
    
    doc = message.document
    fn = doc.file_name
    fs = doc.file_size

    if not fn.endswith(('.py', '.zip')):
        waiting.discard(uid)
        await message.reply("❌ Только .py или .zip!")
        return
    
    if fs > FREE_SIZE_MB * 1024 * 1024:
        waiting.discard(uid)
        await message.reply(f"❌ Макс {FREE_SIZE_MB}МБ!")
        return

    msg = await message.reply("📥 Загрузка...")
    
    try:
        # Скачиваем через aiohttp (популярный асинхронный клиент)
        file = await bot.download_media(doc, file_name=f"{uuid.uuid4().hex[:8]}.{fn.split('.')[-1]}")
        
        tmp_dir = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]
        tmp_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(file, tmp_dir / fn)

        sid = uuid.uuid4().hex[:8]
        target_dir = SCRIPTS_DIR / str(uid) / sid
        target_dir.mkdir(parents=True, exist_ok=True)

        if fn.endswith('.zip'):
            with zipfile.ZipFile(tmp_dir / fn) as z:
                z.extractall(target_dir)
            total_size = sum(f.stat().st_size for f in target_dir.rglob('*') if f.is_file())
        else:
            shutil.move(str(tmp_dir / fn), str(target_dir / fn))
            total_size = fs

        await msg.edit_text("⚡ Запуск...")
        pid = await run_script_async(str(target_dir))

        if pid:
            session = Session()
            new_script = Script(id=sid, user_id=uid, name=fn, path=str(target_dir), status='running', size=total_size, thread_id=pid, created_at=datetime.now().isoformat())
            session.add(new_script)
            session.commit()
            session.close()
            
            active_threads[sid] = pid
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⏹ Стоп", callback_data=f"stop_{sid}"),
                 InlineKeyboardButton("🗑 Удалить", callback_data=f"del_{sid}")]
            ])
            await msg.edit_text(
                f"✅ <b>Запущен!</b>\n📄 {fn}\n🆔 {sid}\n🛡 Поток: {pid}",
                reply_markup=kb
            )
        else:
            await msg.edit_text("❌ Ошибка запуска!")
            shutil.rmtree(target_dir, ignore_errors=True)
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
        await msg.edit_text(f"❌ Ошибка: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        waiting.discard(uid)

@bot.on_message(filters.text == "💻 Мои хосты")
async def hosts(client, message):
    uid = message.from_user.id
    session = Session()
    scripts = session.query(Script).filter(Script.user_id == uid).all()
    session.close()

    if not scripts:
        await message.reply("😔 <b>Нет сервисов</b>")
        return

    running = sum(1 for s in scripts if s.status == 'running')
    text = f"💻 <b>МОИ СЕРВИСЫ</b>\n\n🟢 {running} | 🔴 {len(scripts) - running}\n\n"

    kb = InlineKeyboardMarkup()
    for i, s in enumerate(scripts, 1):
        st = "🟢" if s.status == 'running' else "🔴"
        sz = (s.size or 0) / 1024 / 1024
        text += f"{st} <b>{s.name}</b> | {sz:.1f}МБ\n"
        kb.inline_keyboard.append([
            InlineKeyboardButton(f"⏹ {i}" if s.status == 'running' else f"▶️ {i}", callback_data=f"stop_{s.id}"),
            InlineKeyboardButton(f"🗑 {i}", callback_data=f"del_{s.id}")
        ])
    await message.reply(text, reply_markup=kb)

@bot.on_message(filters.text == "👤 Профиль")
async def profile(client, message):
    uid = message.from_user.id
    session = Session()
    count = session.query(Script).filter(Script.user_id == uid).count()
    running = session.query(Script).filter(Script.user_id == uid, Script.status == 'running').count()
    session.close()
    
    text = f"👤 <b>ПРОФИЛЬ</b>\n\n🆔 <code>{uid}</code>\n📦 {count}/{FREE_SCRIPTS}\n🟢 {running}"
    await message.reply(text)

@bot.on_message(filters.text == "🆘 Помощь")
async def help_cmd(client, message):
    text = (
        f"🆘 <b>ПОМОЩЬ</b>\n\n"
        f"📤 Загрузить - .py или .zip\n"
        f"💻 Мои хосты - управление\n"
        f"👤 Профиль - статистика\n\n"
        f"📦 Лимит: {FREE_SCRIPTS} скриптов\n"
        f"📊 Размер: до {FREE_SIZE_MB}МБ\n"
        f"🛡 Режим: Async + SQLAlchemy"
    )
    await message.reply(text)

# ==========================================================
#  9. CALLBACKS (Популярный подход)
# ==========================================================
@bot.on_callback_query()
async def callback_query(client, call):
    uid = call.from_user.id
    data = call.data
    await call.answer()

    if data.startswith("stop_"):
        sid = data.split("_")[1]
        session = Session()
        script = session.query(Script).filter(Script.id == sid, Script.user_id == uid).first()
        if script and script.status == 'running':
            try:
                os.kill(script.thread_id, signal.SIGTERM)
            except:
                pass
            script.status = 'stopped'
            script.thread_id = None
            session.commit()
        session.close()
        # Обновляем список
        await hosts(client, call.message)

    elif data.startswith("del_"):
        sid = data.split("_")[1]
        session = Session()
        script = session.query(Script).filter(Script.id == sid, Script.user_id == uid).first()
        if script:
            if script.thread_id:
                try:
                    os.kill(script.thread_id, signal.SIGTERM)
                except:
                    pass
            session.delete(script)
            session.commit()
            session.close()
            shutil.rmtree(script.path, ignore_errors=True)
        session.close()
        await hosts(client, call.message)

# ==========================================================
#  10. ЗАПУСК
# ==========================================================
async def main():
    # Запускаем Flask в отдельном потоке
    from threading import Thread
    def run_flask():
        app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)
    Thread(target=run_flask, daemon=True).start()

    # Запускаем Pyrogram бота
    await bot.start()
    logger.info("🚀 Ohoster Render Web запущен (0.5 CPU)")
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
