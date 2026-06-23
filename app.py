# ============================================================
#  Ohoster Render — ULTIMATE (С полным функционалом)
# ============================================================

import os
import sys
import uuid
import shutil
import zipfile
import time
import signal
import threading
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from pyrogram.enums import ParseMode
import sqlite3

# ==========================================================
#  1. НАСТРОЙКИ
# ==========================================================
TOKEN = "1456462948:AAHWBGmZBbb2B5lg-lTRAGXf-YhMAVwMTwM"
ADMIN_IDS = [314148464]

FREE_SCRIPTS = 5
FREE_SIZE_MB = 10

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
DB_PATH = BASE_DIR / "bot.db"
LOG_PATH = BASE_DIR / "bot.log"

for d in [SCRIPTS_DIR, TEMP_DIR]:
    d.mkdir(exist_ok=True)

# ==========================================================
#  2. ЛОГИРОВАНИЕ
# ==========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("OhosterUltimate")

# ==========================================================
#  3. БАЗА ДАННЫХ
# ==========================================================
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            created_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            path TEXT,
            status TEXT,
            size INTEGER,
            pid INTEGER,
            created_at TEXT,
            last_seen REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================================
#  4. ЗАПУСК СКРИПТОВ
# ==========================================================
def run_script(path):
    py_files = list(Path(path).rglob("*.py"))
    if not py_files: return None
    main = py_files[0]
    for f in py_files:
        if f.name == 'main.py': main = f; break
    try:
        proc = subprocess.Popen(
            [sys.executable, str(main)],
            cwd=str(path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            preexec_fn=os.setpgrp
        )
        return proc.pid
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
        return None

def monitor_loop():
    while True:
        try:
            conn = get_db()
            rows = conn.execute('SELECT id, pid, path FROM scripts WHERE status="running" AND pid IS NOT NULL').fetchall()
            for row in rows:
                pid = row['pid']
                try:
                    os.kill(pid, 0)
                except OSError:
                    logger.warning(f"Скрипт {row['id']} упал. Перезапуск...")
                    new_pid = run_script(row['path'])
                    if new_pid:
                        conn.execute('UPDATE scripts SET status="running", pid=?, last_seen=? WHERE id=?', (new_pid, time.time(), row['id']))
                    else:
                        conn.execute('UPDATE scripts SET status="stopped", pid=NULL WHERE id=?', (row['id'],))
                    conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Ошибка мониторинга: {e}")
        time.sleep(20)

threading.Thread(target=monitor_loop, daemon=True).start()

# ==========================================================
#  5. FLASK
# ==========================================================
app = Flask(__name__)

@app.route('/')
def index():
    return "<h1>Ohoster Render Ultimate</h1><p>Uptime: OK</p>"

# ==========================================================
#  6. PYROGRAM
# ==========================================================
bot = Client("ohoster_ult", bot_token=TOKEN, parse_mode=ParseMode.HTML)

waiting = set()
waiting_edit = {}

# ==========================================================
#  7. КЛАВИАТУРЫ
# ==========================================================
def user_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Загрузить"), KeyboardButton("💻 Мои хосты")],
         [KeyboardButton("👤 Профиль"), KeyboardButton("🆘 Помощь")]],
        resize_keyboard=True
    )

def admin_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📊 Статистика"), KeyboardButton("👥 Пользователи")],
         [KeyboardButton("📤 Загрузить"), KeyboardButton("💻 Мои хосты")]],
        resize_keyboard=True
    )

# ==========================================================
#  8. ОБРАБОТЧИКИ
# ==========================================================
@bot.on_message(filters.command("start"))
async def start(client, message):
    uid = message.from_user.id
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO users VALUES (?,?,?)', (uid, message.from_user.username, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    if uid in ADMIN_IDS:
        await message.reply("👑 <b>АДМИН Ohoster</b>\n\n👥 Управляй пользователями и скриптами.", reply_markup=admin_kb())
    else:
        await message.reply("༆ <b>Добро пожаловать в Ohoster!</b>", reply_markup=user_kb())

@bot.on_message(filters.text == "📤 Загрузить")
async def upload(client, message):
    uid = message.from_user.id
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]
    conn.close()
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
    if uid in waiting_edit:
        return await handle_replace_file(client, message)

    doc = message.document
    fn = doc.file_name
    fs = doc.file_size

    if not fn.endswith(('.py', '.zip')):
        waiting.discard(uid)
        return await message.reply("❌ Только .py или .zip!")
    if fs > FREE_SIZE_MB * 1024 * 1024:
        waiting.discard(uid)
        return await message.reply(f"❌ Макс {FREE_SIZE_MB}МБ!")

    msg = await message.reply("📥 Загрузка...")
    try:
        file = await bot.download_media(doc)
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
        pid = run_script(str(target_dir))

        if pid:
            conn = get_db()
            conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?,?,?,?)', (sid, uid, fn, str(target_dir), 'running', total_size, pid, datetime.now().isoformat(), time.time()))
            conn.commit()
            conn.close()
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⏹ Стоп", callback_data=f"stop_{sid}"),
                 InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_{sid}"),
                 InlineKeyboardButton("🗑 Удалить", callback_data=f"del_{sid}")]
            ])
            await msg.edit_text(f"✅ <b>Запущен!</b>\n📄 {fn}\n🆔 {sid}\n🛡 PID: {pid}", reply_markup=kb)
        else:
            await msg.edit_text("❌ Ошибка запуска!")
            shutil.rmtree(target_dir, ignore_errors=True)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        waiting.discard(uid)

@bot.on_message(filters.text == "💻 Мои хосты")
async def hosts(client, message):
    uid = message.from_user.id
    conn = get_db()
    rows = conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY created_at DESC', (uid,)).fetchall()
    conn.close()
    if not rows:
        await message.reply("😔 <b>Нет сервисов</b>")
        return

    running = sum(1 for r in rows if r['status'] == 'running')
    text = f"💻 <b>МОИ СЕРВИСЫ</b>\n\n🟢 {running} | 🔴 {len(rows) - running}\n\n"

    kb = InlineKeyboardMarkup()
    for i, r in enumerate(rows, 1):
        st = "🟢" if r['status'] == 'running' else "🔴"
        sz = (r['size'] or 0) / 1024 / 1024
        text += f"{st} <b>{r['name']}</b> | {sz:.1f}МБ\n"
        kb.inline_keyboard.append([
            InlineKeyboardButton(f"⏹ {i}" if r['status'] == 'running' else f"▶️ {i}", callback_data=f"stop_{r['id']}"),
            InlineKeyboardButton(f"✏️ {i}", callback_data=f"edit_{r['id']}"),
            InlineKeyboardButton(f"🗑 {i}", callback_data=f"del_{r['id']}")
        ])
    await message.reply(text, reply_markup=kb)

@bot.on_message(filters.text == "👤 Профиль")
async def profile(client, message):
    uid = message.from_user.id
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]
    running = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=? AND status="running"', (uid,)).fetchone()[0]
    conn.close()
    await message.reply(f"👤 <b>ПРОФИЛЬ</b>\n\n🆔 <code>{uid}</code>\n📦 {count}/{FREE_SCRIPTS}\n🟢 {running}")

@bot.on_message(filters.text == "🆘 Помощь")
async def help_cmd(client, message):
    await message.reply(
        f"🆘 <b>ПОМОЩЬ</b>\n\n📤 Загрузить - .py или .zip\n💻 Мои хосты - управление\n👤 Профиль - статистика\n\n📦 Лимит: {FREE_SCRIPTS}\n📊 Размер: до {FREE_SIZE_MB}МБ\n🛡 Режим: Auto-Heal"
    )

# ==========================================================
#  9. АДМИН-ПАНЕЛЬ
# ==========================================================
@bot.on_message(filters.text == "📊 Статистика" & filters.user(ADMIN_IDS))
async def admin_stats(client, message):
    conn = get_db()
    users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    scripts = conn.execute('SELECT COUNT(*) FROM scripts').fetchone()[0]
    running = conn.execute('SELECT COUNT(*) FROM scripts WHERE status="running"').fetchone()[0]
    conn.close()
    await message.reply(f"📊 <b>ОБЩАЯ СТАТИСТИКА</b>\n\n👥 Пользователей: {users}\n📦 Скриптов: {scripts}\n🟢 Запущено: {running}")

@bot.on_message(filters.text == "👥 Пользователи" & filters.user(ADMIN_IDS))
async def admin_users(client, message):
    conn = get_db()
    users = conn.execute('SELECT * FROM users ORDER BY user_id DESC').fetchall()
    conn.close()
    
    if not users:
        return await message.reply("👥 <b>Нет зарегистрированных пользователей</b>")

    text = f"👥 <b>ПОЛЬЗОВАТЕЛИ ({len(users)})</b>\n\n"
    kb = InlineKeyboardMarkup()
    for user in users:
        conn = get_db()
        cnt = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (user['user_id'],)).fetchone()[0]
        conn.close()
        username = user['username'] or f"ID{user['user_id']}"
        text += f"🆔 <code>{user['user_id']}</code> | @{username} | 📦{cnt}\n"
        kb.inline_keyboard.append([InlineKeyboardButton(f"📂 Скрипты @{username}", callback_data=f"admin_user_{user['user_id']}")])
    
    await message.reply(text, reply_markup=kb)

@bot.on_callback_query()
async def admin_user_callback(client, call):
    uid = call.from_user.id
    if uid not in ADMIN_IDS:
        return await call.answer("⛔ Только для админа!")

    data = call.data
    await call.answer()

    if data.startswith("admin_user_"):
        target_uid = int(data.split("_")[2])
        conn = get_db()
        scripts = conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY created_at DESC', (target_uid,)).fetchall()
        user = conn.execute('SELECT * FROM users WHERE user_id=?', (target_uid,)).fetchone()
        conn.close()

        if not scripts:
            return await call.message.reply(f"📂 У @{user['username'] or target_uid} нет скриптов.")

        username = user['username'] or f"ID{target_uid}"
        text = f"📂 <b>СКРИПТЫ @{username}</b>\n\n"
        kb = InlineKeyboardMarkup()
        for i, s in enumerate(scripts, 1):
            st = "🟢" if s['status'] == 'running' else "🔴"
            sz = (s['size'] or 0) / 1024 / 1024
            text += f"{st} <b>{s['name']}</b> | {sz:.1f}МБ\n"
            kb.inline_keyboard.append([
                InlineKeyboardButton(f"⏹ {i}", callback_data=f"admin_stop_{s['id']}"),
                InlineKeyboardButton(f"✏️ {i}", callback_data=f"admin_edit_{s['id']}"),
                InlineKeyboardButton(f"🗑 {i}", callback_data=f"admin_del_{s['id']}")
            ])
        kb.inline_keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
        return await call.message.reply(text, reply_markup=kb)

# ==========================================================
#  10. ОБЩИЙ ОБРАБОТЧИК CALLBACK
# ==========================================================
@bot.on_callback_query()
async def callback_query(client, call):
    uid = call.from_user.id
    data = call.data
    await call.answer()

    # Пользовательские кнопки
    if data.startswith("stop_"):
        sid = data.split("_")[1]
        conn = get_db()
        row = conn.execute('SELECT * FROM scripts WHERE id=? AND user_id=?', (sid, uid)).fetchone()
        if row:
            if row['status'] == 'running':
                try: os.kill(row['pid'], signal.SIGTERM)
                except: pass
                conn.execute('UPDATE scripts SET status="stopped", pid=NULL WHERE id=?', (sid,))
            else:
                new_pid = run_script(row['path'])
                if new_pid: conn.execute('UPDATE scripts SET status="running", pid=? WHERE id=?', (new_pid, sid))
                else: conn.execute('UPDATE scripts SET status="stopped", pid=NULL WHERE id=?', (sid,))
            conn.commit()
        conn.close()
        return await hosts(client, call.message)

    if data.startswith("del_"):
        sid = data.split("_")[1]
        conn = get_db()
        row = conn.execute('SELECT * FROM scripts WHERE id=? AND user_id=?', (sid, uid)).fetchone()
        if row:
            try: os.kill(row['pid'], signal.SIGTERM)
            except: pass
            conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
            conn.commit()
            conn.close()
            shutil.rmtree(row['path'], ignore_errors=True)
        else:
            conn.close()
        return await hosts(client, call.message)

    if data.startswith("edit_"):
        sid = data.split("_")[1]
        conn = get_db()
        row = conn.execute('SELECT * FROM scripts WHERE id=? AND user_id=?', (sid, uid)).fetchone()
        conn.close()
        if not row:
            return await call.message.reply("❌ Скрипт не найден!")
        waiting_edit[uid] = sid
        return await call.message.reply("📤 Отправьте новый .py файл для замены.")

    # Админские кнопки
    if data.startswith("admin_stop_"):
        sid = data.split("_")[2]
        conn = get_db()
        script = conn.execute('SELECT * FROM scripts WHERE id=?', (sid,)).fetchone()
        if script:
            if script['status'] == 'running':
                try: os.kill(script['pid'], signal.SIGTERM)
                except: pass
                conn.execute('UPDATE scripts SET status="stopped", pid=NULL WHERE id=?', (sid,))
            else:
                new_pid = run_script(script['path'])
                if new_pid: conn.execute('UPDATE scripts SET status="running", pid=? WHERE id=?', (new_pid, sid))
                else: conn.execute('UPDATE scripts SET status="stopped", pid=NULL WHERE id=?', (sid,))
            conn.commit()
        conn.close()
        return await admin_user_callback(client, call)

    if data.startswith("admin_del_"):
        sid = data.split("_")[2]
        conn = get_db()
        script = conn.execute('SELECT * FROM scripts WHERE id=?', (sid,)).fetchone()
        if script:
            try: os.kill(script['pid'], signal.SIGTERM)
            except: pass
            conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
            conn.commit()
            conn.close()
            shutil.rmtree(script['path'], ignore_errors=True)
        else: conn.close()
        return await admin_user_callback(client, call)

    if data.startswith("admin_edit_"):
        sid = data.split("_")[2]
        conn = get_db()
        script = conn.execute('SELECT * FROM scripts WHERE id=?', (sid,)).fetchone()
        conn.close()
        if not script:
            return await call.message.reply("❌ Скрипт не найден!")
        waiting_edit[uid] = sid
        return await call.message.reply(f"📤 <b>Админ:</b> Отправьте новый файл для замены {script['name']}")

    if data == "admin_back":
        return await admin_users(client, call.message)

async def handle_replace_file(client, message):
    uid = message.from_user.id
    if uid not in waiting_edit: return
    sid = waiting_edit[uid]
    doc = message.document
    if not doc.file_name.endswith('.py'):
        return await message.reply("❌ Только .py!")
    try:
        new_file = await bot.download_media(doc)
        conn = get_db()
        row = conn.execute('SELECT * FROM scripts WHERE id=? AND user_id=?', (sid, uid)).fetchone()
        if row:
            if row['status'] == 'running':
                try: os.kill(row['pid'], signal.SIGTERM)
                except: pass
            for old in Path(row['path']).rglob("*.py"):
                try: os.remove(old)
                except: pass
            shutil.move(new_file, Path(row['path']) / "main.py")
            new_pid = run_script(row['path'])
            if new_pid: conn.execute('UPDATE scripts SET status="running", pid=? WHERE id=?', (new_pid, sid))
            else: conn.execute('UPDATE scripts SET status="stopped", pid=NULL WHERE id=?', (sid,))
            conn.commit()
        conn.close()
        await message.reply("✅ Файл заменён!")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")
    finally:
        waiting_edit.pop(uid, None)

# ==========================================================
#  11. ЗАПУСК
# ==========================================================
if __name__ == '__main__':
    logger.info("🚀 Ohoster Ultimate запущен...")
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False), daemon=True).start()
    bot.run()
