import logging
import base64
import sqlite3
from io import BytesIO
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import requests
from PIL import Image

# API ключи
TELEGRAM_TOKEN = "8385597047:AAFdgzjzXd52C2NSScipGzIpZyiOGrpSdyY"
AITUNNEL_KEY = "sk-aitunnel-iP4KByEtsVaxNJoAP6O1jmPgoqAHGxiD"
PROXYAPI_KEY = "sk-o5l75oXeQIkO6dvoJN3kbBXiGYZsdyVf"

AITUNNEL_URL = "https://api.aitunnel.ru/v1"
PROXYAPI_URL = "https://api.proxyapi.ru/openai/v1"

# Администратор
ADMIN_ID = 6387718314

# Лимиты (из твоего бота)
FREE_LIMIT = 10
PREMIUM_LIMIT = 999
DB_FILE = "bot_database.db"

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === ЛОГИКА БАЗЫ ИЗ ТВОЕГО БОТА ===
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, 
                  is_premium INTEGER DEFAULT 0, 
                  messages_today INTEGER DEFAULT 0, 
                  last_reset TEXT)""")
    c.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?)",
              (ADMIN_ID, 1, 0, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def get_limit(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT is_premium, messages_today, last_reset FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row is None:
        c.execute("INSERT INTO users (user_id, last_reset) VALUES (?, ?)", (user_id, today))
        conn.commit()
        conn.close()
        return FREE_LIMIT, False
    prem, count, reset = row
    if reset != today:
        c.execute("UPDATE users SET messages_today = 0, last_reset = ? WHERE user_id = ?", (today, user_id))
        conn.commit()
        conn.close()
        return PREMIUM_LIMIT if prem else FREE_LIMIT, bool(prem)
    limit = PREMIUM_LIMIT if prem else FREE_LIMIT
    conn.close()
    return max(0, limit - count), bool(prem)

def use_limit(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET messages_today = messages_today + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def set_premium_status(user_id, status=1):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?)", (user_id, status, 0, today))
    conn.commit()
    conn.close()

# === КНОПКИ ИЗ ТВОЕГО БОТА ===
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💬 Чат с AI"), KeyboardButton("🎨 Генерация текста")],
        [KeyboardButton("⭐ Мой статус"), KeyboardButton("💎 Premium")],
    ], resize_keyboard=True, one_time_keyboard=False)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Выдать Premium"), KeyboardButton("➖ Забрать Premium")],
        [KeyboardButton("📋 Список Premium"), KeyboardButton("◀️ Главное меню")],
    ], resize_keyboard=True)

# === НАША ЛОГИКА ===
user_contexts = {}
chat_mode = {}
admin_mode = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **AI Бот** - чат и генерация!\n\n"
        "💬 Пиши в чат\n"
        "🎨 Генерация изображений\n\n"
        "Free: 10 запросов/день\n"
        "Premium: безлимит + изображения",
        reply_markup=main_keyboard(),
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Главное меню
    if text == "💬 Чат с AI":
        chat_mode[user_id] = True
        user_contexts[user_id] = []
        await update.message.reply_text("💬 Режим чата включен!\nПиши свои сообщения:")
    
    elif text == "🎨 Генерация текста":
        remaining, is_premium = get_limit(user_id)
        if not is_premium:
            await update.message.reply_text("🔒 Генерация изображений только для Premium!")
            return
        chat_mode[user_id] = False
        await update.message.reply_text("🎨 Опиши картинку для генерации:")
    
    elif text == "⭐ Мой статус":
        remaining, is_premium = get_limit(user_id)
        status = f"🌟 PREMIUM (безлимит)" if is_premium else f"🔒 FREE ({remaining}/{FREE_LIMIT})"
        await update.message.reply_text(f"📊 Статус: {status}", reply_markup=main_keyboard())
    
    elif text == "💎 Premium":
        await update.message.reply_text("💎 Premium: /set_premium ТВОЙ_ID\nУзнай ID командой /status", reply_markup=main_keyboard())
    
    # Админ кнопки
    elif user_id == ADMIN_ID:
        if text == "➕ Выдать Premium":
            admin_mode[user_id] = "grant"
            await update.message.reply_text("➕ Отправь user_id:", reply_markup=admin_keyboard())
        elif text == "➖ Забрать Premium":
            admin_mode[user_id] = "revoke"
            await update.message.reply_text("➖ Отправь user_id:", reply_markup=admin_keyboard())
        elif text == "📋 Список Premium":
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT user_id FROM users WHERE is_premium = 1")
            users = [row[0] for row in c.fetchall()]
            conn.close()
            text_list = "\n".join(map(str, users)) if users else "Пусто"
            await update.message.reply_text(f"Premium ({len(users)}):\n{text_list}", reply_markup=admin_keyboard())
        elif text == "◀️ Главное меню":
            admin_mode[user_id] = None
            await update.message.reply_text("Главное меню:", reply_markup=main_keyboard())

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ ввод"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or user_id not in admin_mode:
        return False
    
    try:
        target_id = int(update.message.text)
        if admin_mode[user_id] == "grant":
            set_premium_status(target_id, 1)
            await update.message.reply_text(f"✅ Premium выдан: {target_id}", reply_markup=main_keyboard())
        elif admin_mode[user_id] == "revoke":
            set_premium_status(target_id, 0)
            await update.message.reply_text(f"✅ Premium удалён: {target_id}", reply_markup=main_keyboard())
        admin_mode[user_id] = None
        return True
    except:
        await update.message.reply_text("❌ Числовой ID!", reply_markup=admin_keyboard())
        return True

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста"""
    user_id = update.effective_user.id
    
    # Админ ввод
    if await handle_admin_input(update, context):
        return
    
    # Кнопки
    button_texts = ["💬 Чат с AI", "🎨 Генерация текста", "⭐ Мой статус", "💎 Premium", 
                    "➕ Выдать Premium", "➖ Забрать Premium", "📋 Список Premium", "◀️ Главное меню"]
    if update.message.text in button_texts:
        await button_handler(update, context)
        return
    
    # Чат режим
    if user_id not in chat_mode or not chat_mode[user_id]:
        await update.message.reply_text("💬 Нажми 'Чат с AI' для начала диалога", reply_markup=main_keyboard())
        return
    
    remaining, is_premium = get_limit(user_id)
    if remaining <= 0 and not is_premium:
        await update.message.reply_text(
            f"🔒 Лимит FREE исчерпан ({FREE_LIMIT}/день)!\n💎 Нужен Premium",
            reply_markup=main_keyboard()
        )
        return
    
    message_text = update.message.text
    user_contexts.setdefault(user_id, []).append({"role": "user", "content": message_text})
    
    await update.message.reply_text("💭 Думаю...")
    
    try:
        response = requests.post(
            f"{AITUNNEL_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AITUNNEL_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": user_contexts[user_id],
                "max_tokens": 2000
            },
            timeout=30
        )
        response.raise_for_status()
        
        ai_reply = response.json()["choices"][0]["message"]["content"]
        user_contexts[user_id].append({"role": "assistant", "content": ai_reply})
        
        if len(user_contexts[user_id]) > 20:
            user_contexts[user_id] = user_contexts[user_id][-20:]
        
        if not is_premium:
            use_limit(user_id)
        
        await update.message.reply_text(ai_reply, reply_markup=main_keyboard())
        
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)}", reply_markup=main_keyboard())

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация по команде (для совместимости)"""
    user_id = update.effective_user.id
    _, is_premium = get_limit(user_id)
    
    if not is_premium:
        await update.message.reply_text("🔒 Только Premium!", reply_markup=main_keyboard())
        return
    
    prompt = " ".join(context.args)
    await update.message.reply_text("🎨 Генерирую...")
    
    try:
        response = requests.post(
            f"{PROXYAPI_URL}/images/generations",
            headers={"Authorization": f"Bearer {PROXYAPI_KEY}", "Content-Type": "application/json"},
            json={"model": "gpt-image-1-mini", "prompt": prompt, "size": "1024x1024", "output_format": "png"},
            timeout=120
        )
        img_b64 = response.json()["data"][0]["b64_json"]
        img_data = base64.b64decode(img_b64)
        await update.message.reply_photo(photo=BytesIO(img_data), caption=prompt, reply_markup=main_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)}", reply_markup=main_keyboard())

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Фото редактирование (только Premium)"""
    user_id = update.effective_user.id
    _, is_premium = get_limit(user_id)
    
    if not is_premium:
        await update.message.reply_text("🔒 Только Premium!", reply_markup=main_keyboard())
        return
    
    caption = update.message.caption or "улучши фото"
    await update.message.reply_text("🖼️ Редактирую...")
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        img.thumbnail((1024, 1024), Image.LANCZOS)
        
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        buffered.seek(0)
        
        files = {'image[]': ('image.png', buffered, 'image/png')}
        data = {'model': 'gpt-image-1-mini', 'prompt': caption, 'size': '1024x1024', 'output_format': 'png'}
        
        response = requests.post(f"{PROXYAPI_URL}/images/edits", 
                                headers={"Authorization": f"Bearer {PROXYAPI_KEY}"}, 
                                files=files, data=data, timeout=120)
        
        img_b64 = response.json()["data"][0]["b64_json"]
        img_data = base64.b64decode(img_b64)
        
        await update.message.reply_photo(photo=BytesIO(img_data), caption=f"✨ {caption}", reply_markup=main_keyboard())
        
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)}", reply_markup=main_keyboard())

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("image", generate_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    logger.info("🤖 Бот с кнопками запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
