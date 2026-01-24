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
GENAPI_KEY = "sk-dd7I7EH6Gtg0zBTDManlSPCLoBN8rQPAatfF57GFebec8vgBHVbnx15JTKMa"

AITUNNEL_URL = "https://api.aitunnel.ru/v1"
GENAPI_URL = "https://api.gen-api.ru/api/v1"

ADMIN_ID = 6387718314
FREE_LIMIT = 10
PREMIUM_LIMIT = 999
DB_FILE = "bot_database.db"

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
        count = 0
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
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = c.fetchone()

    if exists:
        c.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (status, user_id))
    else:
        c.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", 
                  (user_id, status, 0, today, "", 0))
    conn.commit()
    conn.close()

def save_username(user_id, username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    clean_username = username.lstrip('@') if username else ""
    today = datetime.now().strftime("%Y-%m-%d")

    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = c.fetchone()

    if exists:
        c.execute("UPDATE users SET username = ? WHERE user_id = ?", (clean_username, user_id))
    else:
        c.execute("INSERT INTO users (user_id, username, last_reset, is_premium, messages_today, is_blocked) VALUES (?, ?, ?, ?, ?, ?)", 
                  (user_id, clean_username, today, 0, 0, 0))

    conn.commit()
    conn.close()

def get_user_by_login(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    clean_username = username.lstrip('@') if username else ""
    c.execute("SELECT user_id FROM users WHERE username = ? AND username != ''", (clean_username,))
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

# === КЛАВИАТУРЫ ===
def main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("💬 Чат с AI"), KeyboardButton("🎨 Генерация")],
        [KeyboardButton("⭐ Мой статус"), KeyboardButton("💎 Купить Premium")],
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

def chat_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("⭐ Мой статус"), KeyboardButton("🧹 Очистить чат")],
        [KeyboardButton("🔙 Главное меню")]
    ], resize_keyboard=True)

def image_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("⭐ Мой статус"), KeyboardButton("💬 Чат с AI")],
        [KeyboardButton("🔙 Главное меню")]
    ], resize_keyboard=True)

# === ХРАНИЛИЩЕ ===
user_contexts = {}
chat_mode = {}
admin_mode = {}

# === ПРОМПТ-ЭНХАНСЕР ===
async def enhance_image_prompt(user_prompt):
    """Улучшает промпт для генерации через GPT-4o-mini"""
    try:
        response = requests.post(
            f"{AITUNNEL_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AITUNNEL_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты — эксперт по промптам для AI-генераторов изображений. "
                            "Преобразуй короткий запрос в детальное художественное описание "
                            "для Flux, DALL-E, Midjourney. Добавь детали освещения, стиля, композиции, атмосферы. "
                            "Пиши на английском, максимум 100 слов. Будь конкретным и визуальным."
                        )
                    },
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 150
            },
            timeout=15
        )
        enhanced = response.json()["choices"][0]["message"]["content"]
        logger.info(f"Enhanced prompt: {enhanced}")
        return enhanced
    except Exception as e:
        logger.error(f"Prompt enhance error: {e}")
        return user_prompt

# === ГЕНЕРАЦИЯ ЧЕРЕЗ GENAPI (FLUX 2) ===
async def generate_image_genapi(prompt, model="flux-2"):
    """Генерация изображения через GenAPI"""
    try:
        response = requests.post(
            f"{GENAPI_URL}/request",
            headers={
                "Authorization": f"Bearer {GENAPI_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "prompt": prompt,
                "translate_input": True,
                "is_sync": True
            },
            timeout=120
        )
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"GenAPI response: {result}")
        
        if result.get("status") == "SUCCESS":
            image_url = result.get("result", {}).get("url") or result.get("url")
        elif result.get("url"):
            image_url = result["url"]
        else:
            raise Exception(f"No image URL in response: {result}")
        
        if not image_url:
            raise Exception(f"Empty image URL: {result}")
        
        img_response = requests.get(image_url, timeout=30)
        img_response.raise_for_status()
        
        return BytesIO(img_response.content)
            
    except Exception as e:
        logger.error(f"GenAPI generation error: {e}")
        raise

# === РЕДАКТИРОВАНИЕ ЧЕРЕЗ GENAPI (FLUX 2 EDIT) ===
async def edit_image_genapi(image_bytes, prompt, model="flux-2"):
    """Редактирование изображения через GenAPI"""
    try:
        image_bytes.seek(0)
        image_base64 = base64.b64encode(image_bytes.read()).decode('utf-8')
        
        edit_instruction = (
            f"Keep the original image composition, lighting, and all unchanged elements exactly as they are. "
            f"Only apply this specific change: {prompt}. "
            f"Preserve everything else without modifications."
        )
        
        response = requests.post(
            f"{GENAPI_URL}/request",
            headers={
                "Authorization": f"Bearer {GENAPI_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "prompt": edit_instruction,
                "image_urls": [f"data:image/png;base64,{image_base64}"],
                "translate_input": True,
                "guidance_scale": 4,
                "num_inference_steps": 28,
                "width": 1024,
                "height": 1024,
                "num_images": 1,
                "acceleration": "none",
                "enable_prompt_expansion": False,
                "enable_safety_checker": True,
                "output_format": "png",
                "is_sync": True
            },
            timeout=120
        )
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"GenAPI edit response: {result}")
        
        if result.get("status") == "SUCCESS":
            image_url = result.get("result", {}).get("url") or result.get("url")
        elif result.get("url"):
            image_url = result["url"]
        else:
            raise Exception(f"No image URL in response: {result}")
        
        if not image_url:
            raise Exception(f"Empty image URL: {result}")
        
        img_response = requests.get(image_url, timeout=30)
        img_response.raise_for_status()
        
        return BytesIO(img_response.content)
            
    except Exception as e:
        logger.error(f"GenAPI edit error: {e}")
        raise

# === КОМАНДЫ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    save_username(user_id, username)

    admin_mode.pop(user_id, None)
    chat_mode.pop(user_id, None)
    user_contexts.pop(user_id, None)

    await update.message.reply_text(
        "🤖 AI Бот - чат и генерация!\n\n"
        "💬 Чат с AI\n"
        "🎨 Генерация изображений (Flux 2)\n"
        "🖼️ Редактирование фото\n\n"
        "Free: 10 запросов/день\n"
        "Premium: безлимит + изображения",
        reply_markup=main_keyboard(user_id)
    )

# === ОБРАБОТЧИК КНОПОК ===
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if text == "🔙 Главное меню":
        admin_mode.pop(user_id, None)
        chat_mode.pop(user_id, None)
        await update.message.reply_text("📱 Главное меню:", reply_markup=main_keyboard(user_id))
        return

    if text == "⭐ Мой статус":
        remaining, is_premium = get_limit(user_id)
        status = "🌟 PREMIUM" if is_premium else f"🔒 FREE ({remaining}/{FREE_LIMIT})"

        if user_id in admin_mode and admin_mode[user_id]:
            kb = admin_keyboard()
        elif user_id in chat_mode and chat_mode[user_id] == "image":
            kb = image_keyboard()
        elif user_id in chat_mode and chat_mode[user_id] == True:
            kb = chat_keyboard()
        else:
            kb = main_keyboard(user_id)

        await update.message.reply_text(
            f"📊 Твой статус:\n\n"
            f"Status: {status}\n"
            f"🆔 ID: {user_id}\n"
            f"👤 Username: @{update.effective_user.username or 'нет'}",
            reply_markup=kb
        )
        return

    if text == "🧹 Очистить чат":
        user_contexts.pop(user_id, None)
        kb = chat_keyboard() if user_id in chat_mode else main_keyboard(user_id)
        await update.message.reply_text("✅ История чата очищена!", reply_markup=kb)
        return

    if text == "💎 Купить Premium":
        admin_mode.pop(user_id, None)
        chat_mode.pop(user_id, None)
        await update.message.reply_text(
            f"💎 Premium подписка - 200₽/месяц\n\n"
            f"✨ Безлимитный чат с AI\n"
            f"🎨 Генерация изображений (Flux 2)\n"
            f"🖼️ Редактирование фото\n\n"
            f"Ваш ID: {user_id}\n\n"
            f"Для активации напиши администратору",
            reply_markup=main_keyboard(user_id)
        )
        return

    if text == "💬 Чат с AI":
        admin_mode.pop(user_id, None)
        chat_mode[user_id] = True
        user_contexts[user_id] = []
        await update.message.reply_text(
            "💬 Режим чата включен!\n\nПиши свои вопросы:", 
            reply_markup=chat_keyboard()
        )
        return

    if text == "🎨 Генерация":
        admin_mode.pop(user_id, None)
        remaining, is_premium = get_limit(user_id)
        if not is_premium:
            await update.message.reply_text(
                "🔒 Генерация только для Premium!\n\nОбратись к администратору.",
                reply_markup=main_keyboard(user_id)
            )
            return
        chat_mode[user_id] = "image"
        await update.message.reply_text(
            "🎨 Режим генерации (Flux 2)!\n\nОпиши картинку:", 
            reply_markup=image_keyboard()
        )
        return

    if text == "👑 Админ панель" and user_id == ADMIN_ID:
        admin_mode[user_id] = "main"
        chat_mode.pop(user_id, None)
        await update.message.reply_text("👑 Админ панель\n\nВыбери действие:", reply_markup=admin_keyboard())
        return

    if user_id == ADMIN_ID:
        if text == "➕ Выдать Premium":
            admin_mode[user_id] = "grant"
            await update.message.reply_text(
                "➕ Выдать Premium\n\nОтправь @username или ID пользователя", 
                reply_markup=admin_keyboard()
            )
            return

        if text == "➖ Забрать Premium":
            admin_mode[user_id] = "revoke"
            await update.message.reply_text(
                "➖ Забрать Premium\n\nОтправь @username или ID пользователя", 
                reply_markup=admin_keyboard()
            )
            return

        if text == "🚫 Заблокировать":
            admin_mode[user_id] = "block"
            await update.message.reply_text(
                "🚫 Заблокировать пользователя\n\nОтправь @username или ID", 
                reply_markup=admin_keyboard()
            )
            return

        if text == "✅ Разблокировать":
            admin_mode[user_id] = "unblock"
            await update.message.reply_text(
                "✅ Разблокировать пользователя\n\nОтправь @username или ID", 
                reply_markup=admin_keyboard()
            )
            return

        if text == "📋 Список Premium":
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT user_id, username FROM users WHERE is_premium = 1")
            users = c.fetchall()
            conn.close()

            text_list = "\n".join([f"• {uid} (@{uname or 'нет'})" for uid, uname in users]) if users else "Список пуст"
            await update.message.reply_text(
                f"🌟 Premium пользователи ({len(users)}):\n\n{text_list}", 
                reply_markup=admin_keyboard()
            )
            return

        if text == "📊 Статистика":
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
                f"📊 Статистика бота:\n\n"
                f"👥 Всего пользователей: {total}\n"
                f"🌟 Premium: {premium}\n"
                f"🔒 FREE: {total - premium}\n"
                f"🚫 Заблокировано: {blocked}",
                reply_markup=admin_keyboard()
            )
            return

# === ОБРАБОТЧИК ТЕКСТА ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    button_texts = [
        "💬 Чат с AI", "🎨 Генерация", "⭐ Мой статус", "🧹 Очистить чат",
        "👑 Админ панель", "➕ Выдать Premium", "➖ Забрать Premium", 
        "🚫 Заблокировать", "✅ Разблокировать", "📋 Список Premium", 
        "📊 Статистика", "🔙 Главное меню", "💎 Купить Premium"
    ]
    if text in button_texts:
        await handle_buttons(update, context)
        return

    if user_id == ADMIN_ID and user_id in admin_mode and admin_mode[user_id] not in ["main"]:
        action = admin_mode[user_id]

        try:
            if text.startswith('@'):
                target_id = get_user_by_login(text[1:])
                if not target_id:
                    await update.message.reply_text(
                        f"❌ Пользователь не найден!\n\nПользователь должен запустить бота /start", 
                        reply_markup=admin_keyboard()
                    )
                    return
            else:
                target_id = int(text)

            if action == "grant":
                set_premium_status(target_id, 1)
                await update.message.reply_text(f"✅ Premium выдан!\n\nПользователь ID: {target_id}", reply_markup=admin_keyboard())
            elif action == "revoke":
                set_premium_status(target_id, 0)
                await update.message.reply_text(f"✅ Premium удалён!\n\nПользователь ID: {target_id}", reply_markup=admin_keyboard())
            elif action == "block":
                block_user(target_id)
                await update.message.reply_text(f"🚫 Пользователь заблокирован!\n\nID: {target_id}", reply_markup=admin_keyboard())
            elif action == "unblock":
                unblock_user(target_id)
                await update.message.reply_text(f"✅ Пользователь разблокирован!\n\nID: {target_id}", reply_markup=admin_keyboard())

            admin_mode[user_id] = "main"
            return

        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат!\n\nОтправь числовой ID или @username", 
                reply_markup=admin_keyboard()
            )
            return

    if user_id in chat_mode and chat_mode[user_id] == "image":
        remaining, is_premium = get_limit(user_id)
        if not is_premium:
            await update.message.reply_text("🔒 Только Premium!", reply_markup=main_keyboard(user_id))
            return

        await update.message.reply_text("💭 Создаю промпт...")
        
        enhanced_prompt = await enhance_image_prompt(text)
        
        await update.message.reply_text("🎨 Генерирую изображение (Flux 2)...")

        try:
            img_data = await generate_image_genapi(
                prompt=enhanced_prompt,
                model="flux-2"
            )
            
            await update.message.reply_photo(
                photo=img_data,
                caption=f"🎨 {text}\n\n✨ Powered by Flux 2",
                reply_markup=image_keyboard()
            )
        except Exception as e:
            logger.error(f"Image gen error: {e}")
            await update.message.reply_text(f"❌ Ошибка генерации: {str(e)}", reply_markup=image_keyboard())
        return

    if user_id not in chat_mode or not chat_mode[user_id]:
        await update.message.reply_text("💬 Нажми 'Чат с AI'", reply_markup=main_keyboard(user_id))
        return

    remaining, is_premium = get_limit(user_id)
    if remaining <= 0 and not is_premium:
        await update.message.reply_text(
            f"🔒 Лимит исчерпан!\n\nFREE: {FREE_LIMIT} сообщений в день\n💎 Нужен Premium для безлимита",
            reply_markup=main_keyboard(user_id)
        )
        return

    user_contexts.setdefault(user_id, []).append({"role": "user", "content": text})
    await update.message.reply_text("💭 Думаю...")

    try:
        response = requests.post(
            f"{AITUNNEL_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AITUNNEL_KEY}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": user_contexts[user_id], "max_tokens": 2000},
            timeout=30
        )
        response.raise_for_status()

        ai_reply = response.json()["choices"][0]["message"]["content"]
        user_contexts[user_id].append({"role": "assistant", "content": ai_reply})

        if len(user_contexts[user_id]) > 20:
            user_contexts[user_id] = user_contexts[user_id][-20:]

        if not is_premium:
            use_limit(user_id)

        await update.message.reply_text(ai_reply, reply_markup=chat_keyboard())
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}", reply_markup=chat_keyboard())

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _, is_premium = get_limit(user_id)

    if not is_premium:
        await update.message.reply_text("🔒 Редактирование фото только для Premium!", reply_markup=main_keyboard(user_id))
        return

    caption = update.message.caption or "улучши качество"
    await update.message.reply_text("🖼️ Редактирую через Flux 2...")

    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()

        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        img.thumbnail((1024, 1024), Image.LANCZOS)

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        buffered.seek(0)

        edited_img = await edit_image_genapi(
            image_bytes=buffered,
            prompt=caption,
            model="flux-2"
        )

        kb = image_keyboard() if user_id in chat_mode and chat_mode[user_id] == "image" else main_keyboard(user_id)
        await update.message.reply_photo(
            photo=edited_img,
            caption=f"✨ {caption}\n\n🎨 Edited by Flux 2",
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"Photo edit error: {e}")
        await update.message.reply_text(f"❌ Ошибка редактирования: {str(e)}", reply_markup=main_keyboard(user_id))

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("🤖 Бот запущен с Flux 2 (генерация + редактирование)!")
    app.run_polling()

if __name__ == "__main__":
    main()
