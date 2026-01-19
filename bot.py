import logging
import base64
import sqlite3
from io import BytesIO
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
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

# Лимиты
FREE_LIMIT = 10
PREMIUM_LIMIT = 999
DB_FILE = "bot_database.db"

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, 
                  is_premium INTEGER DEFAULT 0, 
                  messages_today INTEGER DEFAULT 0, 
                  last_reset TEXT,
                  username TEXT,
                  is_blocked INTEGER DEFAULT 0)""")
    c.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?, ?)",
              (ADMIN_ID, 1, 0, datetime.now().strftime("%Y-%m-%d"), "admin", 0))
    conn.commit()
    conn.close()

def get_limit(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT is_premium, messages_today, last_reset, is_blocked FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row is None:
        c.execute("INSERT INTO users (user_id, last_reset) VALUES (?, ?)", (user_id, today))
        conn.commit()
        conn.close()
        return FREE_LIMIT, False
    prem, count, reset, blocked = row
    if blocked:
        conn.close()
        return 0, False
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
    c.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (status, user_id))
    if c.rowcount == 0:
        c.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", (user_id, status, 0, today, "", 0))
    conn.commit()
    conn.close()

def save_username(user_id, username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    conn.close()

def get_user_by_login(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def block_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_blocked = 1, is_premium = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def unblock_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_blocked_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_blocked = 1")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

# === КЛАВИАТУРЫ ===
def main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("💬 Чат с AI"), KeyboardButton("🎨 Генерация")],
        [KeyboardButton("⭐ Мой статус"), KeyboardButton("🧹 Очистить чат")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("👑 Админ панель")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Выдать Premium"), KeyboardButton("➖ Забрать Premium")],
        [KeyboardButton("🚫 Заблокировать"), KeyboardButton("✅ Разблокировать")],
        [KeyboardButton("📋 Список Premium"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("🔙 Главное меню")]
    ], resize_keyboard=True)

# === ХРАНИЛИЩЕ ===
user_contexts = {}
chat_mode = {}
admin_mode = {}

# === КОМАНДЫ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    save_username(user_id, username)
    
    await update.message.reply_text(
        "🤖 **AI Бот** - чат и генерация!\n\n"
        "💬 Чат с AI\n"
        "🎨 Генерация изображений\n\n"
        "Free: 10 запросов/день\n"
        "Premium: безлимит + изображения",
        reply_markup=main_keyboard(user_id),
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Обычные кнопки
    if text == "💬 Чат с AI":
        chat_mode[user_id] = True
        user_contexts[user_id] = []
        await update.message.reply_text("💬 Чат включен! Пиши:", reply_markup=main_keyboard(user_id))
    
    elif text == "🎨 Генерация":
        remaining, is_premium = get_limit(user_id)
        if not is_premium:
            await update.message.reply_text("🔒 Только Premium!", reply_markup=main_keyboard(user_id))
            return
        chat_mode[user_id] = "image"
        await update.message.reply_text("🎨 Опиши картинку:", reply_markup=main_keyboard(user_id))
    
    elif text == "⭐ Мой статус":
        remaining, is_premium = get_limit(user_id)
        status = f"🌟 PREMIUM" if is_premium else f"🔒 FREE ({remaining}/{FREE_LIMIT})"
        await update.message.reply_text(
            f"📊 **Статус:** {status}\n"
            f"🆔 ID: `{user_id}`\n"
            f"👤 @{update.effective_user.username or 'нет'}",
            reply_markup=main_keyboard(user_id),
            parse_mode='Markdown'
        )
    
    elif text == "🧹 Очистить чат":
        if user_id in user_contexts:
            del user_contexts[user_id]
        if user_id in chat_mode:
            del chat_mode[user_id]
        await update.message.reply_text("✅ Чат полностью очищен!", reply_markup=main_keyboard(user_id))
    
    # Админ кнопки
    elif user_id == ADMIN_ID:
        if text == "👑 Админ панель":
            await update.message.reply_text("👑 **Админ панель**\nВыбери действие:", 
                                          reply_markup=admin_keyboard(), parse_mode='Markdown')
        
        elif text == "➕ Выдать Premium":
            admin_mode[user_id] = "grant"
            await update.message.reply_text("➕ Отправь @username или ID:", reply_markup=admin_keyboard())
        
        elif text == "➖ Забрать Premium":
            admin_mode[user_id] = "revoke"
            await update.message.reply_text("➖ Отправь @username или ID:", reply_markup=admin_keyboard())
        
        elif text == "🚫 Заблокировать":
            admin_mode[user_id] = "block"
            await update.message.reply_text("🚫 Отправь @username или ID:", reply_markup=admin_keyboard())
        
        elif text == "✅ Разблокировать":
            admin_mode[user_id] = "unblock"
            await update.message.reply_text("✅ Отправь @username или ID:", reply_markup=admin_keyboard())
        
        elif text == "📋 Список Premium":
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT user_id, username FROM users WHERE is_premium = 1")
            users = c.fetchall()
            conn.close()
            if users:
                text_list = "\n".join([f"• {uid} (@{uname})" for uid, uname in users])
            else:
                text_list = "Пусто"
            await update.message.reply_text(f"🌟 **Premium ({len(users)}):**\n{text_list}", 
                                          reply_markup=admin_keyboard(), parse_mode='Markdown')
        
        elif text == "📊 Статистика":
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM users")
            total = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1")
            premium = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
            blocked = c.fetchone()[0]
            conn.close()
            await update.message.reply_text(
                f"📊 **Статистика:**\n"
                f"👥 Всего: {total}\n"
                f"🌟 Premium: {premium}\n"
                f"🔒 FREE: {total - premium}\n"
                f"🚫 Заблокировано: {blocked}",
                reply_markup=admin_keyboard(), parse_mode='Markdown'
            )
        
        elif text == "🔙 Главное меню":
            admin_mode[user_id] = None
            await update.message.reply_text("Главное меню:", reply_markup=main_keyboard(user_id))

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or user_id not in admin_mode:
        return False
    
    action = admin_mode[user_id]
    input_text = update.message.text
    
    try:
        if input_text.startswith('@'):
            target_id = get_user_by_login(input_text[1:])
            if not target_id:
                await update.message.reply_text("❌ Пользователь не найден!", reply_markup=admin_keyboard())
                return True
        else:
            target_id = int(input_text)
        
        if action == "grant":
            set_premium_status(target_id, 1)
            await update.message.reply_text(f"✅ Premium выдан: {target_id}", reply_markup=main_keyboard(user_id))
        elif action == "revoke":
            set_premium_status(target_id, 0)
            await update.message.reply_text(f"✅ Premium удалён: {target_id}", reply_markup=main_keyboard(user_id))
        elif action == "block":
            block_user(target_id)
            await update.message.reply_text(f"🚫 Заблокирован: {target_id}", reply_markup=main_keyboard(user_id))
        elif action == "unblock":
            unblock_user(target_id)
            await update.message.reply_text(f"✅ Разблокирован: {target_id}", reply_markup=main_keyboard(user_id))
        
        admin_mode[user_id] = None
        return True
        
    except ValueError:
        await update.message.reply_text("❌ ID или @username!", reply_markup=admin_keyboard())
        return True

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Админ ввод
    if await handle_admin_input(update, context):
        return
    
    # Кнопки
    button_texts = ["💬 Чат с AI", "🎨 Генерация", "⭐ Мой статус", "🧹 Очистить чат",
                    "👑 Админ панель", "➕ Выдать Premium", "➖ Забрать Premium", 
                    "🚫 Заблокировать", "✅ Разблокировать", "📋 Список Premium", 
                    "📊 Статистика", "🔙 Главное меню"]
    if update.message.text in button_texts:
        await button_handler(update, context)
        return
    
    # Генерация изображения (режим image)
    if user_id in chat_mode and chat_mode[user_id] == "image":
        remaining, is_premium = get_limit(user_id)
        if not is_premium:
            await update.message.reply_text("🔒 Только Premium!", reply_markup=main_keyboard(user_id))
            return
        
        prompt = update.message.text
        await update.message.reply_text("🎨 Генерирую...")
        
        try:
            response = requests.post(
                f"{PROXYAPI_URL}/images/generations",
                headers={"Authorization": f"Bearer {PROXYAPI_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-image-1-mini",
                    "prompt": prompt,
                    "quality": "high",
                    "size": "1024x1024",
                    "output_format": "png"
                },
                timeout=120
            )
            response.raise_for_status()
            img_b64 = response.json()["data"][0]["b64_json"]
            img_data = base64.b64decode(img_b64)
            await update.message.reply_photo(photo=BytesIO(img_data), caption=f"🎨 {prompt}", 
                                           reply_markup=main_keyboard(user_id))
            chat_mode[user_id] = True  # Вернуться в чат
        except Exception as e:
            await update.message.reply_text(f"❌ {str(e)}", reply_markup=main_keyboard(user_id))
        return
    
    # Чат режим
    if user_id not in chat_mode or not chat_mode[user_id]:
        await update.message.reply_text("💬 Нажми 'Чат с AI'", reply_markup=main_keyboard(user_id))
        return
    
    remaining, is_premium = get_limit(user_id)
    if remaining <= 0 and not is_premium:
        await update.message.reply_text(
            f"🔒 Лимит FREE ({FREE_LIMIT}/день)!\n💎 Нужен Premium",
            reply_markup=main_keyboard(user_id)
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
        
        await update.message.reply_text(ai_reply, reply_markup=main_keyboard(user_id))
        
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)}", reply_markup=main_keyboard(user_id))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _, is_premium = get_limit(user_id)
    
    if not is_premium:
        await update.message.reply_text("🔒 Только Premium!", reply_markup=main_keyboard(user_id))
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
        data = {
            'model': 'gpt-image-1-mini',
            'prompt': caption,
            'quality': 'high',
            'size': '1024x1024',
            'output_format': 'png'
        }
        
        response = requests.post(
            f"{PROXYAPI_URL}/images/edits",
            headers={"Authorization": f"Bearer {PROXYAPI_KEY}"},
            files=files,
            data=data,
            timeout=120
        )
        response.raise_for_status()
        
        img_b64 = response.json()["data"][0]["b64_json"]
        img_data = base64.b64decode(img_b64)
        
        await update.message.reply_photo(photo=BytesIO(img_data), caption=f"✨ {caption}", 
                                        reply_markup=main_keyboard(user_id))
        
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)}", reply_markup=main_keyboard(user_id))

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    logger.info("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
