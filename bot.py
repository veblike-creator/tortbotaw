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
TELEGRAM_TOKEN = "8385597047:AAFdgzjzXd52C2NSScipGzIpZyiOGrpSdyY"  # <--- Вставь токен
AITUNNEL_KEY = "sk-aitunnel-iP4KByEtsVaxNJoAP6O1jmPgoqAHGxiD"
PROXYAPI_KEY = "sk-o5l75oXeQIkO6dvoJN3kbBXiGYZsdyVf"

AITUNNEL_URL = "https://api.aitunnel.ru/v1"
PROXYAPI_URL = "https://api.proxyapi.ru/openai/v1"

# Администраторы
ADMINS = [6387718314]

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Клавиатуры
def get_main_keyboard(is_admin=False):
    """Основная клавиатура"""
    keyboard = [
        [KeyboardButton("💬 Новый диалог"), KeyboardButton("ℹ️ Мой статус")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton("👑 Админ панель")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    """Админ клавиатура"""
    keyboard = [
        [KeyboardButton("➕ Выдать Premium"), KeyboardButton("➖ Забрать Premium")],
        [KeyboardButton("📋 Список Premium"), KeyboardButton("◀️ Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# База данных
def init_db():
    """Инициализация БД"""
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS premium_users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  granted_by INTEGER,
                  granted_date TEXT)''')
    
    c.execute('INSERT OR IGNORE INTO premium_users VALUES (?, ?, ?, ?)',
              (6387718314, 'admin', 6387718314, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()

def is_premium(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM premium_users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

def add_premium(user_id, username, admin_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO premium_users VALUES (?, ?, ?, ?)',
              (user_id, username, admin_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def remove_premium(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('DELETE FROM premium_users WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_premium_users():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('SELECT user_id, username, granted_date FROM premium_users')
    users = c.fetchall()
    conn.close()
    return users

# Хранилище
user_contexts = {}
admin_mode = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "без username"
    premium = is_premium(user_id)
    is_admin = user_id in ADMINS
    
    status = "🌟 **PREMIUM**" if premium else "🔒 **FREE**"
    
    await update.message.reply_text(
        f"🤖 **Привет, {update.effective_user.first_name}!**\n\n"
        f"👤 @{username}\n"
        f"🆔 ID: `{user_id}`\n"
        f"📊 Статус: {status}\n\n"
        "**Как использовать:**\n"
        "• Напиши текст — получишь AI ответ\n"
        "• Отправь фото с описанием — сгенерирую новое\n"
        "• Для генерации используй: `/image описание`\n\n"
        f"{'✅ У тебя полный доступ!' if premium else '⚠️ Нужен Premium для доступа'}",
        parse_mode='Markdown',
        reply_markup=get_main_keyboard(is_admin)
    )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Кнопка "Мой статус"
    if text == "ℹ️ Мой статус":
        username = update.effective_user.username or "нет"
        premium = is_premium(user_id)
        status = "🌟 PREMIUM" if premium else "🔒 FREE"
        
        await update.message.reply_text(
            f"**Твой профиль:**\n\n"
            f"👤 Имя: {update.effective_user.first_name}\n"
            f"🔗 Username: @{username}\n"
            f"🆔 ID: `{user_id}`\n"
            f"📊 Статус: {status}",
            parse_mode='Markdown'
        )
    
    # Кнопка "Новый диалог"
    elif text == "💬 Новый диалог":
        user_contexts[user_id] = []
        await update.message.reply_text("✅ История диалога очищена!")
    
    # Админ панель
    elif text == "👑 Админ панель" and user_id in ADMINS:
        admin_mode[user_id] = "main"
        await update.message.reply_text(
            "**👑 Админ панель**\n\nВыбери действие:",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard()
        )
    
    elif text == "◀️ Назад" and user_id in ADMINS:
        admin_mode[user_id] = None
        await update.message.reply_text(
            "Вернулись в главное меню",
            reply_markup=get_main_keyboard(True)
        )
    
    # Выдать Premium
    elif text == "➕ Выдать Premium" and user_id in ADMINS:
        admin_mode[user_id] = "grant"
        await update.message.reply_text(
            "**Выдать Premium**\n\n"
            "Отправь user_id пользователя (например: 123456789)\n\n"
            "Узнать ID: попроси пользователя нажать 'Мой статус'",
            parse_mode='Markdown'
        )
    
    # Забрать Premium
    elif text == "➖ Забрать Premium" and user_id in ADMINS:
        admin_mode[user_id] = "revoke"
        await update.message.reply_text(
            "**Забрать Premium**\n\n"
            "Отправь user_id пользователя",
            parse_mode='Markdown'
        )
    
    # Список Premium
    elif text == "📋 Список Premium" and user_id in ADMINS:
        users = get_premium_users()
        
        if not users:
            await update.message.reply_text("📋 Нет Premium пользователей")
            return
        
        text_msg = "**🌟 Premium пользователи:**\n\n"
        for uid, uname, date in users:
            text_msg += f"• `{uid}` | @{uname}\n  📅 {date[:10]}\n\n"
        
        await update.message.reply_text(text_msg, parse_mode='Markdown')

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка админ ввода"""
    user_id = update.effective_user.id
    
    if user_id not in ADMINS or user_id not in admin_mode:
        return False
    
    mode = admin_mode[user_id]
    
    # Выдача Premium
    if mode == "grant":
        try:
            target_id = int(update.message.text)
            add_premium(target_id, "unknown", user_id)
            await update.message.reply_text(f"✅ Premium выдан пользователю {target_id}")
            admin_mode[user_id] = "main"
            return True
        except ValueError:
            await update.message.reply_text("❌ Неверный формат! Отправь числовой ID")
            return True
    
    # Забрать Premium
    elif mode == "revoke":
        try:
            target_id = int(update.message.text)
            remove_premium(target_id)
            await update.message.reply_text(f"✅ Premium удалён у {target_id}")
            admin_mode[user_id] = "main"
            return True
        except ValueError:
            await update.message.reply_text("❌ Неверный формат!")
            return True
    
    return False

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста"""
    user_id = update.effective_user.id
    
    # Проверка админ режима
    if await handle_admin_input(update, context):
        return
    
    # Проверка кнопок
    if update.message.text in ["ℹ️ Мой статус", "💬 Новый диалог", "👑 Админ панель", 
                                "◀️ Назад", "➕ Выдать Premium", "➖ Забрать Premium", 
                                "📋 Список Premium"]:
        await handle_buttons(update, context)
        return
    
    # AI ответ
    if not is_premium(user_id):
        await update.message.reply_text("🔒 AI диалог доступен только Premium пользователям!")
        return
    
    user_message = update.message.text
    
    if user_id not in user_contexts:
        user_contexts[user_id] = []
    
    user_contexts[user_id].append({"role": "user", "content": user_message})
    
    typing_msg = await update.message.reply_text("💭 Думаю...")
    
    try:
        response = requests.post(
            f"{AITUNNEL_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {AITUNNEL_KEY}",
                "Content-Type": "application/json"
            },
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
        
        await typing_msg.delete()
        await update.message.reply_text(ai_reply)
        
    except Exception as e:
        await typing_msg.delete()
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /image"""
    user_id = update.effective_user.id
    
    if not is_premium(user_id):
        await update.message.reply_text("🔒 Генерация доступна только Premium!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Использование: /image описание картинки")
        return
    
    prompt = " ".join(context.args)
    status_msg = await update.message.reply_text("🎨 Генерирую...")
    
    try:
        response = requests.post(
            f"{PROXYAPI_URL}/images/generations",
            headers={
                "Authorization": f"Bearer {PROXYAPI_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-image-1-mini",
                "prompt": prompt,
                "quality": "medium",
                "size": "1024x1024",
                "output_format": "png"
            },
            timeout=120
        )
        response.raise_for_status()
        
        img_b64 = response.json()["data"][0]["b64_json"]
        img_data = base64.b64decode(img_b64)
        
        await status_msg.delete()
        await update.message.reply_photo(photo=BytesIO(img_data), caption=f"🎨 {prompt}")
        
    except Exception as e:
        await status_msg.delete()
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фото"""
    user_id = update.effective_user.id
    
    if not is_premium(user_id):
        await update.message.reply_text("🔒 Редактирование фото только для Premium!")
        return
    
    caption = update.message.caption or "transform this"
    status_msg = await update.message.reply_text("🖼️ Обрабатываю...")
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        max_size = 2048
        if max(img.size) > max_size:
            ratio = max_size / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.LANCZOS)
        
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        buffered.seek(0)
        
        files = {'image[]': ('image.png', buffered, 'image/png')}
        data = {
            'model': 'gpt-image-1-mini',
            'prompt': caption,
            'quality': 'medium',
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
        
        await status_msg.delete()
        await update.message.reply_photo(photo=BytesIO(img_data), caption=f"✨ {caption}")
        
    except Exception as e:
        await status_msg.delete()
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def main():
    """Запуск"""
    init_db()
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("image", generate_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    logger.info("🤖 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

