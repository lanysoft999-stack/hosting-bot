import telebot, sqlite3, os, sys, uuid, shutil, zipfile, subprocess, signal, time, requests, threading, psutil, json
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== НАСТРОЙКИ (ТЕПЕРЬ МОЩНЫЕ) ==========
TOKEN = "1456462948:AAEoNXLuUJF3OwjdF9b1t7aREerbgybFH0o"
ADMIN_IDS = [314148464]
PORT = int(os.environ.get('PORT', 10000))

# ЖЕСТКИЕ ЛИМИТЫ НА ХОСТИНГ
MAX_SCRIPTS_PER_USER = 20
MAX_SIZE_MB = 500
MAX_RAM_MB = 1024
MAX_CPU_PERCENT = 80
MAX_RUNTIME_HOURS = 24
AUTO_RESTART_DELAY_SEC = 30

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
DB_PATH = BASE_DIR / "bot.db"
LOG_PATH = BASE_DIR / "hosting.log"

for d in [SCRIPTS_DIR, TEMP_DIR]:
    d.mkdir(exist_ok=True)

# ========== ЛОГГЕР ==========
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"[{ts}] {msg}")

# ========== БД ==========
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('CREATE TABLE IF NOT EXISTS scripts (id TEXT, user_id INTEGER, name TEXT, path TEXT, status TEXT, size INTEGER, pid INTEGER, start_time REAL, ram_usage INTEGER, cpu_usage REAL)')
    conn.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, joined_at TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, timestamp TEXT)')
    conn.commit()
    conn.close()

def get_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM scripts WHERE user_id=? ORDER BY rowid DESC', (uid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_scripts():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM scripts ORDER BY rowid DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_script(sid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM scripts WHERE id=?', (sid,)).fetchone()
    conn.close()
    return dict(row) if row else None

def count_scripts(uid):
    conn = sqlite3.connect(str(DB_PATH))
    cnt = conn.execute('SELECT COUNT(*) FROM scripts WHERE user_id=?', (uid,)).fetchone()[0]
    conn.close()
    return cnt

def update_script(sid, **kwargs):
    conn = sqlite3.connect(str(DB_PATH))
    sets = ', '.join([f"{k}=?" for k in kwargs.keys()])
    vals = list(kwargs.values()) + [sid]
    conn.execute(f'UPDATE scripts SET {sets} WHERE id=?', vals)
    conn.commit()
    conn.close()

def delete_script(sid):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('DELETE FROM scripts WHERE id=?', (sid,))
    conn.commit()
    conn.close()

def add_log(uid, action):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT INTO logs (user_id, action, timestamp) VALUES (?,?,?)', (uid, action, time.time()))
    conn.commit()
    conn.close()

# ========== МОНИТОРИНГ И УПРАВЛЕНИЕ ==========
def get_process_info(pid):
    try:
        proc = psutil.Process(pid)
        return {
            'pid': proc.pid,
            'cpu': proc.cpu_percent(),
            'ram': proc.memory_info().rss / 1024 / 1024,
            'status': proc.status()
        }
    except:
        return None

def kill_process(pid):
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except:
        return False

def restart_script(sid):
    s = get_script(sid)
    if not s: return False
    kill_process(s['pid'])
    time.sleep(1)
    pid = run_script(s['path'])
    if pid:
        update_script(sid, pid=pid, status='running', start_time=time.time())
        add_log(s['user_id'], f"Авто-перезапуск {s['name']}")
        return True
    return False

def auto_restart_loop():
    while True:
        time.sleep(AUTO_RESTART_DELAY_SEC)
        for s in get_all_scripts():
            if s['status'] != 'running': continue
            info = get_process_info(s['pid'])
            if not info:
                log(f"⚠️ Упал скрипт {s['name']} ({s['id']}) — перезапуск")
                restart_script(s['id'])

# ========== ЗАПУСК СКРИПТА ==========
def run_script(path):
    py_files = list(Path(path).rglob("*.py"))
    if not py_files: return None
    main = py_files[0]
    for f in py_files:
        if f.name == 'main.py': main = f; break
    try:
        proc = subprocess.Popen([sys.executable, str(main)], cwd=str(path),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return proc.pid
    except Exception as e:
        log(f"Ошибка запуска: {e}")
        return None

# ========== ВЕБ-СЕРВЕР (мониторинг в браузере) ==========
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        scripts = get_all_scripts()
        running = sum(1 for s in scripts if s['status']=='running')
        
        html = f"""
        <html><head><title>Ohoster Hosting</title>
        <style>body{{background:#0f0f0f;color:#0f0;font-family:monospace;padding:20px;}}
        .box{{background:#1a1a1a;border:1px solid #333;padding:15px;margin:10px 0;border-radius:8px;}}
        .green{{color:#0f0;}}.red{{color:#f00;}}.yellow{{color:#ffb800;}}
        </style></head><body>
        <h1>🚀 Ohoster Hosting Dashboard</h1>
        <div class="box"><b>Всего:</b> {len(scripts)} | <b>Запущено:</b> <span class="green">{running}</span></div>
        """
        for s in scripts:
            status_color = "green" if s['status']=='running' else "red"
            size_mb = (s['size'] or 0) / 1024 / 1024
            html += f"""
            <div class="box">
                <b>{s['name']}</b> (ID: {s['id']}) <span class="{status_color}">{s['status']}</span><br>
                📦 {size_mb:.1f}MB | 🧠 RAM: {s.get('ram_usage',0)}MB | ⚡ CPU: {s.get('cpu_usage',0)}%<br>
                🕐 Запущен: {s.get('start_time','?')} | PID: {s.get('pid','?')}
            </div>
            """
        html += "</body></html>"
        self.wfile.write(html.encode('utf-8'))
    
    def log_message(self, *args): pass

def start_web():
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()

# ========== БОТ ==========
bot = telebot.TeleBot(TOKEN, parse_mode='HTML')
bot.remove_webhook()
time.sleep(3)

waiting = set()

def user_kb(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📤 Загрузить", "💻 Мои хосты")
    kb.add("📊 Статистика", "🆘 Помощь")
    if uid in ADMIN_IDS:
        kb.add("👑 Админ панель")
    return kb

# ========== СТАРТ ==========
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    username = message.from_user.username or "NoName"
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute('INSERT OR IGNORE INTO users (user_id, username, joined_at) VALUES (?,?,?)', (uid, username, time.time()))
    conn.commit()
    conn.close()
    
    add_log(uid, "Запустил бота")
    
    if uid in ADMIN_IDS:
        scripts = get_all_scripts()
        running = sum(1 for s in scripts if s['status']=='running')
        users = get_all_users()
        text = f"👑 <b>АДМИН Ohoster</b>\n\n👥 {len(users)} | 📦 {len(scripts)} | 🟢 {running}"
        bot.send_message(uid, text, reply_markup=user_kb(uid))
        return
    
    text = (
        f"༆ <b>Добро пожаловать в Ohoster v2.0!</b>\n\n"
        f"⚡ Мощный хостинг с авто-мониторингом\n"
        f"📦 Лимит: {MAX_SCRIPTS_PER_USER} скриптов\n"
        f"📊 Размер: до {MAX_SIZE_MB}МБ"
    )
    bot.send_message(uid, text, reply_markup=user_kb(uid))

# ========== ЗАГРУЗКА ==========
@bot.message_handler(func=lambda m: m.text == '📤 Загрузить')
def upload(message):
    uid = message.from_user.id
    if count_scripts(uid) >= MAX_SCRIPTS_PER_USER:
        return bot.send_message(uid, f"❌ Лимит {MAX_SCRIPTS_PER_USER} скриптов!")
    waiting.add(uid)
    bot.send_message(uid, f"📤 Отправьте .py или .zip (до {MAX_SIZE_MB}МБ)")

@bot.message_handler(content_types=['document'])
def handle_doc(message):
    uid = message.from_user.id
    if uid not in waiting: return
    
    doc = message.document
    fn = doc.file_name
    fs = doc.file_size
    
    if not fn.endswith(('.py', '.zip')):
        waiting.discard(uid)
        return bot.send_message(uid, "❌ Только .py или .zip!")
    
    if fs > MAX_SIZE_MB * 1024 * 1024:
        waiting.discard(uid)
        return bot.send_message(uid, f"❌ Макс {MAX_SIZE_MB}МБ!")
    
    msg = bot.send_message(uid, "📥 Загрузка...")
    
    try:
        fi = bot.get_file(doc.file_id)
        url = f"https://api.telegram.org/file/bot{TOKEN}/{fi.file_path}"
        dl = requests.get(url).content
        
        tmp = TEMP_DIR / str(uid) / uuid.uuid4().hex[:8]
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / fn).write_bytes(dl)
        
        sid = uuid.uuid4().hex[:8]
        sdir = SCRIPTS_DIR / str(uid) / sid
        sdir.mkdir(parents=True, exist_ok=True)
        
        if fn.endswith('.zip'):
            with zipfile.ZipFile(tmp/fn) as z:
                z.extractall(sdir)
            ts = sum(f.stat().st_size for f in sdir.rglob('*') if f.is_file())
        else:
            shutil.copy2(str(tmp/fn), str(sdir/fn))
            ts = fs
        
        bot.edit_message_text("⚡ Запуск...", uid, msg.message_id)
        pid = run_script(str(sdir))
        
        if pid:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute('INSERT INTO scripts (id, user_id, name, path, status, size, pid, start_time, ram_usage, cpu_usage) VALUES (?,?,?,?,?,?,?,?,?,?)', 
                         (sid, uid, fn, str(sdir), 'running', ts, pid, time.time(), 0, 0))
            conn.commit()
            conn.close()
            add_log(uid, f"Загружен и запущен {fn}")
            
            kb = InlineKeyboardMarkup()
            kb.add(
                InlineKeyboardButton("⏹ Стоп", callback_data=f"stop:{sid}"),
                InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{sid}")
            )
            bot.edit_message_text(
                f"✅ <b>Запущен!</b>\n📄 {fn}\n🆔 {sid}\nPID: {pid}\n📦 {ts/1024/1024:.1f}МБ",
                uid, msg.message_id, reply_markup=kb
            )
        else:
            bot.edit_message_text("❌ Ошибка запуска!", uid, msg.message_id)
            shutil.rmtree(sdir, ignore_errors=True)
            
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", uid, msg.message_id)
        log(f"Ошибка загрузки {fn}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        waiting.discard(uid)

# ========== ХОСТЫ ==========
@bot.message_handler(func=lambda m: m.text == '💻 Мои хосты')
def hosts(message):
    uid = message.from_user.id
    scripts = get_scripts(uid)
    
    if not scripts:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📤 Загрузить скрипт", callback_data="upload_btn"))
        return bot.send_message(uid, "😔 <b>Нет сервисов</b>", reply_markup=kb)
    
    running = sum(1 for s in scripts if s['status']=='running')
    text = f"💻 <b>МОИ СЕРВИСЫ</b>\n\n🟢 {running} | 🔴 {len(scripts)-running}\n\n"
    
    kb = InlineKeyboardMarkup()
    for i, s in enumerate(scripts, 1):
        st = "🟢" if s['status']=='running' else "🔴"
        sz = (s['size'] or 0) / 1024 / 1024
        info = get_process_info(s['pid'])
        ram = info['ram'] if info else 0
        cpu = info['cpu'] if info else 0
        text += f"{st} <b>{s['name']}</b> | {sz:.1f}МБ | RAM:{ram:.0f}MB | CPU:{cpu:.0f}% | <code>{s['id']}</code>\n"
        kb.add(
            InlineKeyboardButton(f"⏹ {i}" if s['status']=='running' else f"▶️ {i}", callback_data=f"stop:{s['id']}"),
            InlineKeyboardButton(f"🗑 {i}", callback_data=f"del:{s['id']}")
        )
    
    kb.add(InlineKeyboardButton("📤 Загрузить ещё", callback_data="upload_btn"))
    bot.send_message(uid, text, reply_markup=kb)

# ========== СТАТИСТИКА (ПРОФИЛЬ) ==========
@bot.message_handler(func=lambda m: m.text == '📊 Статистика')
def profile(message):
    uid = message.from_user.id
    scripts = get_scripts(uid)
    running = sum(1 for s in scripts if s['status']=='running')
    total_ram = sum(s.get('ram_usage',0) or 0 for s in scripts)
    text = f"👤 <b>ПРОФИЛЬ</b>\n\n🆔 <code>{uid}</code>\n📦 Хостов: {len(scripts)}/{MAX_SCRIPTS_PER_USER}\n🟢 Запущено: {running}\n🧠 RAM использовано: {total_ram:.0f}MB"
    bot.send_message(uid, text)

# ========== ПОМОЩЬ ==========
@bot.message_handler(func=lambda m: m.text == '🆘 Помощь')
def help_cmd(message):
    text = (
        f"🆘 <b>ПОМОЩЬ Ohoster v2.0</b>\n\n"
        f"📤 Загрузить - загрузка .py или .zip\n"
        f"💻 Мои хосты - управление сервисами\n"
        f"📊 Статистика - твой профиль\n\n"
        f"📦 Лимит: {MAX_SCRIPTS_PER_USER} скриптов\n"
        f"📊 Размер: до {MAX_SIZE_MB}МБ\n"
        f"🧠 RAM: до {MAX_RAM_MB}МБ на скрипт\n"
        f"⚡ CPU: до {MAX_CPU_PERCENT}%\n"
        f"🕐 Время: до {MAX_RUNTIME_HOURS}ч\n"
        f"🔄 Авто-перезапуск упавших скриптов"
    )
    bot.send_message(message.chat.id, text)

# ========== АДМИН ПАНЕЛЬ ==========
@bot.message_handler(func=lambda m: m.text == '👑 Админ панель' and m.from_user.id in ADMIN_IDS)
def admin_panel(message):
    uid = message.from_user.id
    users = get_all_users()
    scripts = get_all_scripts()
    running = sum(1 for s in scripts if s['status']=='running')
    
    text = f"👑 <b>АДМИН ПАНЕЛЬ</b>\n\n"
    text += f"👥 Пользователей: {len(users)}\n"
    text += f"📦 Всего скриптов: {len(scripts)} (🟢{running})\n"
    text += f"🔄 Авто-рестарт: через {AUTO_RESTART_DELAY_SEC}с\n"
    
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("🔄 Перезапустить все", callback_data="admin_restart_all"),
        InlineKeyboardButton("⏹ Остановить все", callback_data="admin_stop_all")
    )
    bot.send_message(uid, text, reply_markup=kb)

# ========== CALLBACKS ==========
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = call.from_user.id
    data = call.data
    bot.answer_callback_query(call.id)
    
    if data == "upload_btn":
        upload(call.message)
        return
    
    if data.startswith("stop:"):
        sid = data.split(":")[1]
        s = get_script(sid)
        if s and s['user_id'] == uid:
            if s['status'] == 'running':
                kill_process(s['pid'])
                update_script(sid, status='stopped', pid=0, ram_usage=0, cpu_usage=0)
                add_log(uid, f"Остановлен {s['name']}")
            else:
                pid = run_script(s['path'])
                if pid:
                    update_script(sid, status='running', pid=pid, start_time=time.time())
                    add_log(uid, f"Запущен {s['name']}")
            hosts(call.message)
    
    elif data.startswith("del:"):
        sid = data.split(":")[1]
        s = get_script(sid)
        if s and s['user_id'] == uid:
            kill_process(s['pid'])
            delete_script(sid)
            shutil.rmtree(s['path'], ignore_errors=True)
            add_log(uid, f"Удалён {s['name']}")
            hosts(call.message)
    
    # Админ команды
    elif data == "admin_restart_all" and uid in ADMIN_IDS:
        bot.send_message(uid, "🔄 Перезапуск всех скриптов...")
        for s in get_all_scripts():
            if s['status'] == 'running':
                restart_script(s['id'])
        bot.send_message(uid, "✅ Готово!")
    
    elif data == "admin_stop_all" and uid in ADMIN_IDS:
        bot.send_message(uid, "⏹ Остановка всех скриптов...")
        for s in get_all_scripts():
            if s['status'] == 'running':
                kill_process(s['pid'])
                update_script(s['id'], status='stopped', pid=0)
        bot.send_message(uid, "✅ Готово!")

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    init_db()
    threading.Thread(target=start_web, daemon=True).start()
    threading.Thread(target=auto_restart_loop, daemon=True).start()
    
    log(f"🚀 Ohoster Bot v2.0 | PORT: {PORT}")
    log(f"⚡ Авто-рестарт: {AUTO_RESTART_DELAY_SEC}с")
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            log(f"Ошибка бота: {e}")
            time.sleep(10)
            bot.remove_webhook()
            time.sleep(5),
