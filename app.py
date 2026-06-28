import telebot
import sqlite3
import datetime
import time
import os
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ==========================================
# НАСТРОЙКИ БОТА
# ==========================================
# Теперь токен берется из переменных окружения на Render (безопасно)
BOT_TOKEN = os.getenv('BOT_TOKEN', '8974171870:AAHB4rwB2SM5hkflzD3BZC94qm7N6Mj0ccE')
ADMIN_ID = int(os.getenv('ADMIN_ID', 314148464))

bot = telebot.TeleBot(BOT_TOKEN)
user_states = {}

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
def init_db():
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        username TEXT,
        reg_date TEXT,
        spent INTEGER DEFAULT 0,
        purchases INTEGER DEFAULT 0
    )''')
    
    cur.execute('''
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT DEFAULT 'Не установлено',
        photo_id TEXT DEFAULT '',
        is_hidden INTEGER DEFAULT 0
    )''')
    
    cur.execute('''
    CREATE TABLE IF NOT EXISTS subcategories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER,
        name TEXT NOT NULL,
        FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE
    )''')
    
    cur.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subcategory_id INTEGER DEFAULT 0,
        category_id INTEGER DEFAULT 0,
        name TEXT NOT NULL,
        price INTEGER NOT NULL,
        description TEXT DEFAULT 'Описание отсутствует',
        photo_id TEXT DEFAULT '',
        file_id TEXT DEFAULT '' 
    )''')
    
    cur.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_name TEXT,
        amount INTEGER,
        date TEXT,
        status TEXT DEFAULT 'paid'
    )''')
    conn.commit()
    conn.close()

def db_add_user(user_id, first_name, username):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (user_id, first_name, username, reg_date) VALUES (?, ?, ?, ?)", 
                    (user_id, first_name, username, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    conn.close()

def db_get_user_profile(user_id):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    res = cur.fetchone()
    conn.close()
    return res

def db_get_categories(include_hidden=False):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    if include_hidden:
        cur.execute("SELECT * FROM categories")
    else:
        cur.execute("SELECT * FROM categories WHERE is_hidden=0")
    res = cur.fetchall()
    conn.close()
    return res

def db_get_category(cat_id):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE id=?", (cat_id,))
    res = cur.fetchone()
    conn.close()
    return res

def db_add_category(name, desc, photo_id):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO categories (name, description, photo_id) VALUES (?, ?, ?)", (name, desc, photo_id))
    conn.commit()
    conn.close()

def db_update_category(cat_id, field, value):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute(f"UPDATE categories SET {field} = ? WHERE id = ?", (value, cat_id))
    conn.commit()
    conn.close()

def db_delete_category(cat_id):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()
    conn.close()

def db_get_subcategories(cat_id):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("SELECT * FROM subcategories WHERE category_id=?", (cat_id,))
    res = cur.fetchall()
    conn.close()
    return res

def db_add_subcategory(cat_id, name):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO subcategories (category_id, name) VALUES (?, ?)", (cat_id, name))
    conn.commit()
    conn.close()

def db_get_products(subcat_id=None, cat_id=None):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    if subcat_id:
        cur.execute("SELECT * FROM products WHERE subcategory_id=?", (subcat_id,))
    elif cat_id:
        cur.execute("SELECT * FROM products WHERE category_id=? AND subcategory_id=0", (cat_id,))
    else:
        cur.execute("SELECT * FROM products")
    res = cur.fetchall()
    conn.close()
    return res

def db_get_product(prod_id):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE id=?", (prod_id,))
    res = cur.fetchone()
    conn.close()
    return res

def db_add_product(cat_id, subcat_id, name, price, description, photo_id, file_id):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO products (category_id, subcategory_id, name, price, description, photo_id, file_id) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                (cat_id, subcat_id, name, price, description, photo_id, file_id))
    conn.commit()
    conn.close()

def db_add_purchase(user_id, product_name, price):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("INSERT INTO orders (user_id, product_name, amount, date, status) VALUES (?, ?, ?, ?, 'paid')", 
                (user_id, product_name, price, now))
    cur.execute("UPDATE users SET spent = spent + ?, purchases = purchases + 1 WHERE user_id=?", (price, user_id))
    conn.commit()
    conn.close()

# ==========================================
# КЛАВИАТУРЫ
# ==========================================
def welcome_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("▶️ Перейти в магазин"))
    kb.row(KeyboardButton("👤 Личный кабинет"), KeyboardButton("💰 Реферальная система"))
    kb.add(KeyboardButton("👮 Поддержка"))
    return kb

def profile_inline_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(text="📦 Мои покупки", callback_data="my_orders"),
        InlineKeyboardButton(text="ℹ️ Info", callback_data="info"),
        InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_to_main")
    )
    return kb

def categories_shop_kb():
    kb = InlineKeyboardMarkup()
    for cat in db_get_categories(include_hidden=False):
        kb.add(InlineKeyboardButton(text=f"📁 {cat[1]}", callback_data=f"shop_cat_{cat[0]}"))
    return kb

def subcategories_shop_kb(cat_id, show_products=False):
    kb = InlineKeyboardMarkup()
    subs = db_get_subcategories(cat_id)
    for sub in subs:
        kb.add(InlineKeyboardButton(text=f"📂 {sub[2]}", callback_data=f"shop_sub_{sub[0]}"))
    if show_products:
        kb.add(InlineKeyboardButton(text="📦 Товары этой категории", callback_data=f"shop_cat_prods_{cat_id}"))
    kb.add(InlineKeyboardButton(text="⬅️ К разделам", callback_data="shop_back"))
    return kb

def products_shop_kb(subcat_id=None, cat_id=None):
    kb = InlineKeyboardMarkup()
    items = db_get_products(subcat_id=subcat_id, cat_id=cat_id)
    for prod in items:
        kb.add(InlineKeyboardButton(text=f"📦 {prod[2]} - {prod[3]}₽", callback_data=f"shop_prod_{prod[0]}"))
    if subcat_id:
        kb.add(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"shop_back_sub_{subcat_id}"))
    elif cat_id:
        kb.add(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"shop_back_cat_{cat_id}"))
    return kb

def product_buy_kb(prod_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="💳 Оплатить", callback_data=f"buy_{prod_id}"))
    kb.add(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"shop_back_prod_{prod_id}"))
    return kb

def admin_main_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📦 Управление товарами"), KeyboardButton("📊 Статистика"))
    return kb

def admin_categories_kb():
    kb = InlineKeyboardMarkup()
    for cat in db_get_categories(include_hidden=True):
        eye = "👁️" if cat[4] == 0 else "🚫"
        kb.add(InlineKeyboardButton(text=f"{eye} {cat[1]}", callback_data=f"adm_cat_{cat[0]}"))
    kb.add(InlineKeyboardButton(text="➕ Создать категорию", callback_data="adm_add_cat"))
    return kb

def admin_cat_edit_kb(cat_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(text="✏️ Название", callback_data=f"adm_edit_name_{cat_id}"),
        InlineKeyboardButton(text="✏️ Описание", callback_data=f"adm_edit_desc_{cat_id}"),
        InlineKeyboardButton(text="🖼 Фото", callback_data=f"adm_edit_cat_photo_{cat_id}")
    )
    cat = db_get_category(cat_id)
    toggle_text = "🔓 Показать" if cat[4] == 1 else "🔒 Скрыть"
    kb.add(InlineKeyboardButton(text=toggle_text, callback_data=f"adm_toggle_{cat_id}"))
    kb.row(
        InlineKeyboardButton(text="➕ Подкатегорию", callback_data=f"adm_add_sub_{cat_id}"),
        InlineKeyboardButton(text="➕ Товар", callback_data=f"adm_add_prod_{cat_id}")
    )
    kb.row(
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"adm_del_cat_{cat_id}"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")
    )
    return kb

# ==========================================
# ОСНОВНЫЕ КОМАНДЫ
# ==========================================
@bot.message_handler(commands=['start'])
def start(message):
    db_add_user(message.chat.id, message.chat.first_name, message.chat.username)
    text = ("Наша команда рада приветствовать вас в нашем боте!\n\nЗдесь вы можете приобрести подписку для нашего приложения NetWing и открыть для себя все его уникальные возможности\n\nЗаходя в этого бота, вы автоматически соглашаетесь с нашими\nПолитикой конфиденциальности & Пользовательским соглашением.")
    bot.send_message(message.chat.id, text, disable_web_page_preview=True, reply_markup=welcome_keyboard())

@bot.message_handler(func=lambda message: message.text == "▶️ Перейти в магазин")
def go_to_shop(message):
    cats = db_get_categories(include_hidden=False)
    if not cats:
        bot.send_message(message.chat.id, "❗ Магазин пуст.", reply_markup=welcome_keyboard())
        return
    bot.send_message(message.chat.id, "🗂 Выберите раздел:", reply_markup=categories_shop_kb())

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop_cat_") and not call.data.startswith("shop_cat_prods_"))
def nav_cat(call):
    cat_id = int(call.data.split("_")[2])
    cat = db_get_category(cat_id)
    has_direct_products = len(db_get_products(cat_id=cat_id)) > 0
    if cat[3]:
        bot.edit_message_media(chat_id=call.message.chat.id, message_id=call.message.message_id, media=telebot.types.InputMediaPhoto(cat[3], caption=f"📁 **{cat[1]}**\n\n{cat[2]}"), reply_markup=subcategories_shop_kb(cat_id, show_products=has_direct_products))
    else:
        bot.edit_message_text(f"📁 **{cat[1]}**\n\n{cat[2]}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=subcategories_shop_kb(cat_id, show_products=has_direct_products))

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop_cat_prods_"))
def nav_cat_products(call):
    cat_id = int(call.data.split("_")[3])
    bot.edit_message_text("📦 Товары категории:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=products_shop_kb(cat_id=cat_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop_sub_"))
def nav_sub(call):
    sub_id = int(call.data.split("_")[2])
    bot.edit_message_text("📦 Выберите товар:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=products_shop_kb(subcat_id=sub_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop_prod_"))
def nav_prod(call):
    prod_id = int(call.data.split("_")[2])
    prod = db_get_product(prod_id)
    text = f"📦 **{prod[2]}**\n\n📝 {prod[4]}\n\n💰 Цена: {prod[3]}₽"
    if prod[5]:
        bot.edit_message_media(chat_id=call.message.chat.id, message_id=call.message.message_id, media=telebot.types.InputMediaPhoto(prod[5], caption=text, parse_mode="Markdown"), reply_markup=product_buy_kb(prod_id))
    else:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=product_buy_kb(prod_id))

@bot.callback_query_handler(func=lambda call: call.data == "shop_back")
def back_main(call):
    bot.edit_message_text("🗂 Выберите раздел:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=categories_shop_kb())

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop_back_sub_"))
def back_sub(call):
    sub_id = int(call.data.split("_")[3])
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("SELECT category_id FROM subcategories WHERE id=?", (sub_id,))
    res = cur.fetchone()
    conn.close()
    if res:
        cat_id = res[0]
        cat = db_get_category(cat_id)
        has_direct_products = len(db_get_products(cat_id=cat_id)) > 0
        if cat[3]:
            bot.edit_message_media(chat_id=call.message.chat.id, message_id=call.message.message_id, media=telebot.types.InputMediaPhoto(cat[3], caption=f"📁 **{cat[1]}**\n\n{cat[2]}"), reply_markup=subcategories_shop_kb(cat_id, show_products=has_direct_products))
        else:
            bot.edit_message_text(f"📁 **{cat[1]}**\n\n{cat[2]}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=subcategories_shop_kb(cat_id, show_products=has_direct_products))

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop_back_cat_"))
def back_cat(call):
    cat_id = int(call.data.split("_")[3])
    cat = db_get_category(cat_id)
    if cat[3]:
        bot.edit_message_media(chat_id=call.message.chat.id, message_id=call.message.message_id, media=telebot.types.InputMediaPhoto(cat[3], caption=f"📁 **{cat[1]}**\n\n{cat[2]}"), reply_markup=subcategories_shop_kb(cat_id, show_products=True))
    else:
        bot.edit_message_text(f"📁 **{cat[1]}**\n\n{cat[2]}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=subcategories_shop_kb(cat_id, show_products=True))

@bot.callback_query_handler(func=lambda call: call.data.startswith("shop_back_prod_"))
def back_prod(call):
    prod_id = int(call.data.split("_")[3])
    prod = db_get_product(prod_id)
    if prod[1] > 0:
        bot.edit_message_text("📦 Выберите товар:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=products_shop_kb(subcat_id=prod[1]))
    else:
        bot.edit_message_text("📦 Товары категории:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=products_shop_kb(cat_id=prod[0]))

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
def buy_process(call):
    prod_id = int(call.data.split("_")[1])
    prod = db_get_product(prod_id)
    db_add_purchase(call.from_user.id, prod[2], prod[3])
    if prod[6]:
        try:
            bot.send_document(call.message.chat.id, prod[6], caption=f"✅ Ключ/Файл для **{prod[2]}**")
        except:
            bot.send_message(call.message.chat.id, f"✅ Оплата прошла! Ваш ключ: `HESERA-{prod_id}`", parse_mode="Markdown")
    else:
        bot.send_message(call.message.chat.id, f"✅ Оплата прошла! Ваш ключ: `HESERA-{prod_id}`", parse_mode="Markdown")
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: message.text == "👤 Личный кабинет")
def user_profile(message):
    user = db_get_user_profile(message.chat.id)
    if user:
        text = f"👤 Профиль пользователя\n\n🆔 ID: {user[0]}\n👤 Имя: {user[1]} 🐱\n📅 Регистрация: {user[3]}\n💰 Потрачено всего: {user[4]}₽\n🛒 Покупок: {user[5]}"
        bot.send_photo(message.chat.id, photo="https://i.imgur.com/your_banner.png", caption=text, parse_mode="HTML", reply_markup=profile_inline_keyboard())
    else:
        bot.send_message(message.chat.id, "❗ Профиль не найден. Нажмите /start.")

@bot.callback_query_handler(func=lambda call: call.data == "my_orders")
def my_orders(call):
    bot.answer_callback_query(call.id, "📦 Здесь будут ваши покупки (раздел в разработке).")

@bot.callback_query_handler(func=lambda call: call.data == "info")
def info(call):
    bot.answer_callback_query(call.id, "ℹ️ Информация о боте (раздел в разработке).")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_profile(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    start(call.message)

@bot.message_handler(func=lambda message: message.text == "💰 Реферальная система")
def referral_system(message):
    bot.send_message(message.chat.id, "💰 Раздел в разработке.", reply_markup=welcome_keyboard())

@bot.message_handler(func=lambda message: message.text == "👮 Поддержка")
def support_chat(message):
    bot.send_message(message.chat.id, "👮 Раздел в разработке.", reply_markup=welcome_keyboard())

# ==========================================
# АДМИН-ПАНЕЛЬ
# ==========================================
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.chat.id != ADMIN_ID: return
    bot.send_message(message.chat.id, "⚙️ Панель управления", reply_markup=admin_main_kb())

@bot.message_handler(func=lambda message: message.text == "📦 Управление товарами" and message.chat.id == ADMIN_ID)
def admin_manage_items(message):
    bot.send_message(message.chat.id, "📁 Список категорий:", reply_markup=admin_categories_kb())

# === КАТЕГОРИИ ===
@bot.callback_query_handler(func=lambda call: call.data == "adm_add_cat" and call.from_user.id == ADMIN_ID)
def admin_add_cat(call):
    user_states[call.from_user.id] = 'add_category_name'
    bot.send_message(call.message.chat.id, "📦 Шаг 1. Отправьте **название** категории:")

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'add_category_name')
def admin_add_cat_desc(message):
    user_states[message.chat.id] = 'add_category_desc'
    user_states[f'{message.chat.id}_cat_name'] = message.text
    bot.send_message(message.chat.id, "📝 Шаг 2. Отправьте **описание** категории:")

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'add_category_desc')
def admin_add_cat_photo(message):
    user_states[message.chat.id] = 'add_category_photo'
    user_states[f'{message.chat.id}_cat_desc'] = message.text
    bot.send_message(message.chat.id, "🖼 Шаг 3. Отправьте **фото** категории. (Напишите `/skip_photo` если фото нет)")

@bot.message_handler(content_types=['photo'], func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'add_category_photo')
def admin_add_cat_finish_photo(message):
    name = user_states.get(f'{message.chat.id}_cat_name')
    desc = user_states.get(f'{message.chat.id}_cat_desc')
    db_add_category(name, desc, message.photo[-1].file_id)
    bot.send_message(message.chat.id, f"✅ Категория **{name}** добавлена с фото!")
    user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'add_category_photo' and message.text == '/skip_photo')
def admin_add_cat_finish_no_photo(message):
    name = user_states.get(f'{message.chat.id}_cat_name')
    desc = user_states.get(f'{message.chat.id}_cat_desc')
    db_add_category(name, desc, "")
    bot.send_message(message.chat.id, f"✅ Категория **{name}** добавлена без фото!")
    user_states.pop(message.chat.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_cat_") and call.from_user.id == ADMIN_ID)
def admin_cat_menu(call):
    cat_id = int(call.data.split("_")[2])
    cat = db_get_category(cat_id)
    text = f"🗂 **Категория: {cat[1]}**\n\n📝 Описание: {cat[2]}\n🖼 Фото: {'Есть' if cat[3] else 'Нет'}"
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=admin_cat_edit_kb(cat_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_edit_") and call.from_user.id == ADMIN_ID)
def admin_edit_cat(call):
    parts = call.data.split("_")
    action, cat_id = parts[2], int(parts[3])
    if action == "name":
        user_states[call.from_user.id] = f'edit_cat_name_{cat_id}'
        bot.send_message(call.message.chat.id, "✏️ Отправьте новое название:")
    elif action == "desc":
        user_states[call.from_user.id] = f'edit_cat_desc_{cat_id}'
        bot.send_message(call.message.chat.id, "✏️ Отправьте новое описание:")
    elif action == "cat_photo":
        user_states[call.from_user.id] = f'edit_cat_photo_{cat_id}'
        bot.send_message(call.message.chat.id, "🖼 Отправьте новое фото для категории:")

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id, "").startswith("edit_cat_"))
def admin_save_edit(message):
    state = user_states.get(message.chat.id)
    parts = state.split("_")
    action, cat_id = parts[2], int(parts[3])
    if action == "name": db_update_category(cat_id, "name", message.text)
    elif action == "desc": db_update_category(cat_id, "description", message.text)
    elif action == "photo": db_update_category(cat_id, "photo_id", message.photo[-1].file_id)
    bot.send_message(message.chat.id, "✅ Обновлено!")
    user_states.pop(message.chat.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_toggle_") and call.from_user.id == ADMIN_ID)
def admin_toggle_cat(call):
    cat_id = int(call.data.split("_")[2])
    cat = db_get_category(cat_id)
    db_update_category(cat_id, "is_hidden", 0 if cat[4] == 1 else 1)
    bot.answer_callback_query(call.id, "🔄 Статус обновлен!")

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_del_cat_") and call.from_user.id == ADMIN_ID)
def admin_del_cat(call):
    cat_id = int(call.data.split("_")[3])
    db_delete_category(cat_id)
    bot.edit_message_text("🗑 Категория удалена.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=admin_categories_kb())

# === ПОДКАТЕГОРИИ ===
@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_add_sub_") and call.from_user.id == ADMIN_ID)
def admin_add_sub(call):
    cat_id = int(call.data.split("_")[3])
    user_states[call.from_user.id] = f'add_sub_{cat_id}'
    bot.send_message(call.message.chat.id, "📂 Введите название подкатегории:")

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id, "").startswith("add_sub_"))
def admin_finish_sub(message):
    cat_id = int(user_states[message.chat.id].split("_")[2])
    db_add_subcategory(cat_id, message.text)
    bot.send_message(message.chat.id, f"✅ Подкатегория **{message.text}** добавлена!")
    user_states.pop(message.chat.id, None)

# === ТОВАРЫ ===
@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_add_prod_") and call.from_user.id == ADMIN_ID)
def admin_add_prod_start(call):
    cat_id = int(call.data.split("_")[3])
    user_states[call.from_user.id] = 'prod_name_price'
    user_states[f'{call.from_user.id}_cat_id'] = cat_id
    bot.send_message(call.message.chat.id, "📦 **Шаг 1.** Введите через пробел: `Название Цена`\nПример: `stoper 1.5 sek 67`")

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'prod_name_price')
def admin_add_prod_desc(message):
    try:
        parts = message.text.rsplit(" ", 1)
        name, price = parts[0], int(parts[1])
        user_states[f'{message.chat.id}_prod_name'] = name
        user_states[f'{message.chat.id}_prod_price'] = price
        user_states[message.chat.id] = 'prod_desc'
        bot.send_message(message.chat.id, "📝 **Шаг 2.** Отправьте **описание** товара (можно с эмодзи и переносами строк):")
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Пишите: `Название Цена` (разделите пробелом).")

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'prod_desc')
def admin_add_prod_photo(message):
    user_states[f'{message.chat.id}_prod_desc'] = message.text
    user_states[message.chat.id] = 'prod_photo'
    bot.send_message(message.chat.id, "🖼 **Шаг 3.** Отправьте **фото** товара.\n(Если фото не нужно, напишите `/skip_photo`)")

@bot.message_handler(content_types=['photo'], func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'prod_photo')
def admin_add_prod_file(message):
    user_states[f'{message.chat.id}_prod_photo'] = message.photo[-1].file_id
    user_states[message.chat.id] = 'prod_file'
    bot.send_message(message.chat.id, "📁 **Шаг 4.** Отправьте **файл** (.txt, .apk, .zip).\n(Если файла нет, напишите `/skip_file`)")

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'prod_photo' and message.text == '/skip_photo')
def admin_add_prod_file_no_photo(message):
    user_states[f'{message.chat.id}_prod_photo'] = ""
    user_states[message.chat.id] = 'prod_file'
    bot.send_message(message.chat.id, "📁 **Шаг 4.** Отправьте **файл** (.txt, .apk, .zip).\n(Если файла нет, напишите `/skip_file`)")

@bot.message_handler(content_types=['document'], func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'prod_file')
def admin_add_prod_finish(message):
    cat_id = user_states.get(f'{message.chat.id}_cat_id')
    name = user_states.get(f'{message.chat.id}_prod_name')
    price = user_states.get(f'{message.chat.id}_prod_price')
    desc = user_states.get(f'{message.chat.id}_prod_desc')
    photo = user_states.get(f'{message.chat.id}_prod_photo')
    file_id = message.document.file_id
    db_add_product(cat_id, 0, name, price, desc, photo, file_id)
    bot.send_message(message.chat.id, f"✅ Товар **{name}** за {price}₽ создан!")
    user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda message: message.chat.id == ADMIN_ID and user_states.get(message.chat.id) == 'prod_file' and message.text == '/skip_file')
def admin_add_prod_finish_no_file(message):
    cat_id = user_states.get(f'{message.chat.id}_cat_id')
    name = user_states.get(f'{message.chat.id}_prod_name')
    price = user_states.get(f'{message.chat.id}_prod_price')
    desc = user_states.get(f'{message.chat.id}_prod_desc')
    photo = user_states.get(f'{message.chat.id}_prod_photo')
    db_add_product(cat_id, 0, name, price, desc, photo, "")
    bot.send_message(message.chat.id, f"✅ Товар **{name}** за {price}₽ создан (без файла)!")
    user_states.pop(message.chat.id, None)

# === НАЗАД В АДМИНКЕ ===
@bot.callback_query_handler(func=lambda call: call.data == "adm_back" and call.from_user.id == ADMIN_ID)
def admin_back_items(call):
    bot.edit_message_text("📁 Список категорий:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=admin_categories_kb())

@bot.message_handler(func=lambda message: message.text == "📊 Статистика" and message.chat.id == ADMIN_ID)
def admin_stats(message):
    conn = sqlite3.connect('shop_data.db')
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM orders")
    orders = cur.fetchone()[0]
    conn.close()
    bot.send_message(message.chat.id, f"📊 **Статистика**\n\n👤 Пользователей: {users}\n📦 Заказов: {orders}")

# ==========================================
# ЗАПУСК
# ==========================================
if __name__ == "__main__":
    init_db()
    print("🤖 Бот успешно запущен через app.py!")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"❗ Перезапуск через 5 сек. Ошибка: {e}")
            time.sleep(5)
