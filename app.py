# app.py - Ohoster Bot (Render Ready)
import telebot
from telebot import types
import sqlite3, os, sys, uuid, shutil, zipfile, subprocess, time, requests, threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

TOKEN = "1456462948:AAHFFCK2lc8eRiQCEyJSefJtv0D1uau-sfQ"
PORT = int(os.environ.get('PORT', 10000))

for d in ["scripts", "temp"]:
    Path(d).mkdir(exist_ok=True)

def db():
    conn = sqlite3.connect("bot.db")
    conn.execute('CREATE TABLE IF NOT EXISTS s (id TEXT, uid INTEGER, name TEXT, path TEXT, status TEXT)')
    return conn

def run(path):
    # Ищем main.py или bot.py
    for f in Path(path).rglob("*.py"):
        if f.name in ('main.py', 'bot.py'):
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(f)], 
                    cwd=str(path), 
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return proc.pid
            except:
                pass
    
    # Если нет main.py/bot.py - берем первый .py файл
    py_files = list(Path(path).rglob("*.py"))
    if py_files:
        try:
            proc = subprocess.Popen(
                [sys.executable, str(py_files[0])], 
                cwd=str(path), 
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return proc.pid
        except:
            pass
    return 0

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

def web():
    HTTPServer(('0.0.0.0', PORT), H).serve_forever()

BOT = telebot.TeleBot(TOKEN, parse_mode='HTML')

@BOT.message_handler(commands=['start'])
def start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📤 Загрузить", "💻 Хосты")
    BOT.send_message(msg.chat.id, 
        "༆ <b>Добро пожаловать в Ohoster!</b>\n\n"
        "⚠︎ Аптайм за 24 часа: 100%\n"
        "➪ Упало сервисов: 0\n"
        "➪ Сервисов запущено: 0",
        reply_markup=kb)

@BOT.message_handler(func=lambda m: m.text == '📤 Загрузить')
def upload(msg):
    BOT.send_message(msg.chat.id, "📤 Отправь .py или .zip файл")

@BOT.message_handler(content_types=['document'])
def doc(msg):
    fn = msg.document.file_name
    
    if not fn.endswith(('.py', '.zip')):
        return BOT.send_message(msg.chat.id, "❌ Только .py или .zip!")
    
    BOT.send_message(msg.chat.id, "📥 Скачивание...")
    
    fi = BOT.get_file(msg.document.file_id)
    dl = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{fi.file_path}").content
    
    sid = uuid.uuid4().hex[:8]
    path = f"scripts/{msg.from_user.id}/{sid}"
    Path(path).mkdir(parents=True, exist_ok=True)
    
    tmp = f"temp/{sid}"
    Path(tmp).mkdir(parents=True, exist_ok=True)
    Path(f"{tmp}/{fn}").write_bytes(dl)
    
    try:
        if fn.endswith('.zip'):
            with zipfile.ZipFile(f"{tmp}/{fn}") as z:
                z.extractall(path)
        else:
            shutil.copy2(f"{tmp}/{fn}", f"{path}/{fn}")
        
        # Считаем количество .py файлов
        py_count = len(list(Path(path).rglob("*.py")))
        
        if py_count == 0:
            return BOT.send_message(msg.chat.id, "❌ В архиве нет .py файлов!")
        
        BOT.send_message(msg.chat.id, f"⚡ Запуск... (найдено {py_count} .py файлов)")
        
        pid = run(path)
        
        if pid:
            conn = db()
            conn.execute('INSERT INTO s VALUES (?,?,?,?,?)', (sid, msg.from_user.id, fn, path, 'running'))
            conn.commit()
            conn.close()
            
            kb = types.InlineKeyboardMarkup()
            kb.add(
                types.InlineKeyboardButton("⏹ Стоп", callback_data=f"stop:{sid}"),
                types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{sid}")
            )
            BOT.send_message(msg.chat.id, 
                f"✅ <b>Запущен!</b>\n📄 {fn}\n🆔 <code>{sid}</code>\n📦 {py_count} файлов\n🔢 PID: {pid}",
                reply_markup=kb)
        else:
            BOT.send_message(msg.chat.id, "❌ Ошибка запуска! Проверьте файл.")
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        BOT.send_message(msg.chat.id, f"❌ Ошибка: {e}")
        shutil.rmtree(path, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@BOT.message_handler(func=lambda m: m.text == '💻 Хосты')
def hosts(msg):
    conn = db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM s WHERE uid=? ORDER BY rowid DESC', (msg.from_user.id,)).fetchall()
    conn.close()
    
    if not rows:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📤 Загрузить скрипт", callback_data="upload_btn"))
        return BOT.send_message(msg.chat.id, "😔 <b>Нет сервисов</b>", reply_markup=kb)
    
    text = "💻 <b>ХОСТЫ:</b>\n\n"
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        s = "🟢" if r['status']=='running' else "🔴"
        text += f"{s} <b>{r['name']}</b>\n   └ <code>{r['id']}</code>\n"
        kb.add(
            types.InlineKeyboardButton("⏹ Стоп" if r['status']=='running' else "▶️ Старт", callback_data=f"stop:{r['id']}"),
            types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{r['id']}")
        )
    kb.add(types.InlineKeyboardButton("📤 Загрузить ещё", callback_data="upload_btn"))
    BOT.send_message(msg.chat.id, text, reply_markup=kb)

@BOT.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = call.from_user.id
    BOT.answer_callback_query(call.id)
    
    if call.data == "upload_btn":
        upload(call.message)
        return
    
    conn = db()
    conn.row_factory = sqlite3.Row
    data = call.data
    
    if data.startswith("stop:"):
        sid = data.split(":")[1]
        s = conn.execute('SELECT * FROM s WHERE id=? AND uid=?', (sid, uid)).fetchone()
        if s:
            if s['status'] == 'running':
                conn.execute('UPDATE s SET status=? WHERE id=?', ('stopped', sid))
            else:
                pid = run(s['path'])
                if pid: conn.execute('UPDATE s SET status=? WHERE id=?', ('running', sid))
            conn.commit()
        conn.close()
        hosts(call.message)
    
    elif data.startswith("del:"):
        sid = data.split(":")[1]
        s = conn.execute('SELECT * FROM s WHERE id=? AND uid=?', (sid, uid)).fetchone()
        if s:
            conn.execute('DELETE FROM s WHERE id=?', (sid,))
            conn.commit()
            shutil.rmtree(s['path'], ignore_errors=True)
        conn.close()
        hosts(call.message)
    else:
        conn.close()

if __name__ == '__main__':
    threading.Thread(target=web, daemon=True).start()
    print(f"🚀 Ohoster | Port: {PORT}")
    
    while True:
        try:
            BOT.infinity_polling(timeout=60, long_polling_timeout=30)
        except:
            time.sleep(10)
