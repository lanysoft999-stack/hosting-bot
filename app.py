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
    for f in Path(path).rglob("*.py"):
        if f.name == 'main.py':
            return subprocess.Popen([sys.executable, str(f)], cwd=path, start_new_session=True).pid
    return 0

# Веб-сервер
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'OK')

def web():
    HTTPServer(('0.0.0.0', PORT), H).serve_forever()

BOT = telebot.TeleBot(TOKEN)

@BOT.message_handler(commands=['start'])
def start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📤 Загрузить", "💻 Хосты")
    BOT.send_message(msg.chat.id, "🚀 Ohoster\n📤 Отправь .py файл", reply_markup=kb)

@BOT.message_handler(func=lambda m: m.text == '📤 Загрузить')
def upload(msg):
    BOT.send_message(msg.chat.id, "📤 Отправь .py или .zip")

@BOT.message_handler(content_types=['document'])
def doc(msg):
    fn = msg.document.file_name
    if not fn.endswith(('.py', '.zip')):
        return BOT.send_message(msg.chat.id, "❌ .py или .zip!")
    
    fi = BOT.get_file(msg.document.file_id)
    dl = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{fi.file_path}").content
    
    sid = uuid.uuid4().hex[:8]
    path = f"scripts/{msg.from_user.id}/{sid}"
    Path(path).mkdir(parents=True, exist_ok=True)
    
    tmp = f"temp/{sid}"
    Path(tmp).mkdir(parents=True, exist_ok=True)
    Path(f"{tmp}/{fn}").write_bytes(dl)
    
    if fn.endswith('.zip'):
        with zipfile.ZipFile(f"{tmp}/{fn}") as z: z.extractall(path)
    else:
        shutil.copy2(f"{tmp}/{fn}", f"{path}/{fn}")
    
    pid = run(path)
    
    if pid:
        conn = db()
        conn.execute('INSERT INTO s VALUES (?,?,?,?,?)', (sid, msg.from_user.id, fn, path, 'running'))
        conn.commit(); conn.close()
        
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⏹ Стоп", callback_data=f"stop:{sid}"),
               types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{sid}"))
        BOT.reply_to(msg, f"✅ Запущен!\n📄 {fn}\nPID: {pid}", reply_markup=kb)
    else:
        BOT.reply_to(msg, "❌ Ошибка!")
    
    shutil.rmtree(tmp, ignore_errors=True)

@BOT.message_handler(func=lambda m: m.text == '💻 Хосты')
def hosts(msg):
    conn = db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM s WHERE uid=? ORDER BY rowid DESC', (msg.from_user.id,)).fetchall()
    conn.close()
    
    if not rows:
        return BOT.send_message(msg.chat.id, "😔 Нет хостов")
    
    text = "💻 Хосты:\n\n"
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        s = "🟢" if r['status']=='running' else "🔴"
        text += f"{s} {r['name']} | {r['id']}\n"
        kb.add(
            types.InlineKeyboardButton("⏹" if r['status']=='running' else "▶️", callback_data=f"stop:{r['id']}"),
            types.InlineKeyboardButton("🗑", callback_data=f"del:{r['id']}")
        )
    BOT.send_message(msg.chat.id, text, reply_markup=kb)

@BOT.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = call.from_user.id
    BOT.answer_callback_query(call.id)
    
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
            time.sleep(10)z
