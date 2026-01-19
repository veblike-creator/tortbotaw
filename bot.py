import asyncio
import logging
import sqlite3
import base64
import os
from io import BytesIO
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from openai import AsyncOpenAI

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN", "8594342469:AAEW_7iGUZrwnLGcocOLduPl14eFExMeo-4")
API_KEY = os.getenv("API_KEY", "sk-dd7I7EH6Gtg0zBTDManlSPCLoBN8rQPAatfF57GFebec8vgBHVbnx15JTKMa")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6387718314"))

BASE_URL = "https://api.aitunnel.ru/v1/"
FREE_LIMIT = 3
PREMIUM_LIMIT = 10
DB_FILE = "users.db"

client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

class GenState(StatesGroup):
    waiting_prompt = State()
    waiting_text_prompt = State()

class AdminState(StatesGroup):
    grant_premium = State()
    revoke_premium = State()
    block_user = State()
    unblock_user = State()

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        is_premium INTEGER DEFAULT 0,
        img_count INTEGER DEFAULT 0,
        last_reset TEXT,
        username TEXT,
        is_blocked INTEGER DEFAULT 0
    )""")
    c.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?, ?)",
              (ADMIN_ID, 1, 0, datetime.now().strftime("%Y-%m-%d"), "admin", 0))
    conn.commit()
    conn.close()

def get_limit(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT is_premium, img_count, last_reset, is_blocked FROM users WHERE user_id = ?", (user_id,))
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
        c.execute("UPDATE users SET img_count = 0, last_reset = ? WHERE user_id = ?", (today, user_id))
        conn.commit()
        conn.close()
        return PREMIUM_LIMIT if prem else FREE_LIMIT, bool(prem)
    limit = PREMIUM_LIMIT if prem else FREE_LIMIT
    conn.close()
    return max(0, limit - count), bool(prem)

def use_limit(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET img_count = img_count + 1 WHERE user_id = ?", (user_id,))
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
        c.execute("INSERT INTO users (user_id, username, last_reset, is_premium, img_count, is_blocked) VALUES (?, ?, ?, ?, ?, ?)", 
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
        [KeyboardButton(text="🎨 Генерация"), KeyboardButton(text="✍️ Текст в фото")],
        [KeyboardButton(text="⭐ Мой статус"), KeyboardButton(text="💎 Купить Premium")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton(text="👑 Админ панель")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Выдать Premium"), KeyboardButton(text="➖ Забрать Premium")],
        [KeyboardButton(text="🚫 Заблокировать"), KeyboardButton(text="✅ Разблокировать")],
        [KeyboardButton(text="📋 Список Premium"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔙 Главное меню")]
    ], resize_keyboard=True)

# === КОМАНДЫ ===
@router.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    init_db()
    user_id = message.from_user.id
    username = message.from_user.username or ""
    save_username(user_id, username)
    
    await message.answer(
        "╔═══════════════════╗\n"
        "║   🎨 **PhotoGen Bot**   ║\n"
        "╚═══════════════════╝\n\n"
        "**Возможности:**\n"
        "🎨 Редактирование фото по промпту\n"
        "✍️ Создание фото из текста\n\n"
        "**Тарифы:**\n"
        "🔓 FREE: 3 генерации/день\n"
        "🌟 Premium: 10 генераций/день\n\n"
        "Выбери действие в меню ⬇️",
        reply_markup=main_keyboard(user_id),
        parse_mode="Markdown"
    )

@router.message(F.text == "🔙 Главное меню")
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📱 **Главное меню**\n\nВыбери нужное действие:",
        reply_markup=main_keyboard(message.from_user.id),
        parse_mode="Markdown"
    )

@router.message(F.text == "💎 Купить Premium")
async def premium_info(message: types.Message):
    user_id = message.from_user.id
    await message.answer(
        "╔═══════════════════════╗\n"
        "║  💎 **PREMIUM ПОДПИСКА**  ║\n"
        "╚═══════════════════════╝\n\n"
        "**💰 Стоимость: 200₽/месяц**\n\n"
        "**Что получишь:**\n"
        "├ ✅ 10 генераций в день\n"
        "├ ✅ Приоритетная обработка\n"
        "├ ✅ Без рекламы\n"
        "└ ✅ Приоритетная поддержка\n\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "**📋 Твой ID для оплаты:**\n"
        f"`{user_id}`\n"
        "_(нажми чтобы скопировать)_\n\n"
        "**📱 Шаги для активации:**\n"
        "1️⃣ Скопируй свой ID выше\n"
        f"2️⃣ [Напиши админу](tg://user?id={ADMIN_ID})\n"
        "3️⃣ Отправь ID и чек оплаты\n"
        "4️⃣ Получи Premium!\n\n"
        "⚡️ Активация в течение 5 минут",
        reply_markup=main_keyboard(user_id),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

@router.message(F.text == "⭐ Мой статус")
async def my_status(message: types.Message):
    user_id = message.from_user.id
    remaining, is_premium = get_limit(user_id)
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT img_count, last_reset FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    
    images_used = row[0] if row else 0
    
    if is_premium:
        status_icon = "🌟"
        status_text = "**PREMIUM**"
        limit_info = (
            "**📊 Твои возможности:**\n"
            "├ ✅ 10 генераций в день\n"
            "├ ✅ Приоритетная обработка\n"
            "└ ✅ Приоритетная поддержка"
        )
    else:
        status_icon = "🔓"
        status_text = "**FREE**"
        
        used_percent = (images_used / FREE_LIMIT) * 10
        filled = int(used_percent)
        empty = 10 - filled
        progress_bar = "█" * filled + "░" * empty
        
        limit_info = (
            f"**📊 Использование:**\n"
            f"├ Сегодня: **{images_used}/{FREE_LIMIT}** генераций\n"
            f"├ Прогресс: [{progress_bar}] {int(used_percent * 10)}%\n"
            f"├ Осталось: **{remaining}** генераций\n"
            f"└ Обновление: завтра в 00:00"
        )
    
    username_display = f"@{message.from_user.username}" if message.from_user.username else "не установлен"
    full_name = message.from_user.full_name or "Пользователь"
    
    status_message = (
        f"{status_icon} ══════════════════════\n"
        f"       {status_text}\n"
        f"══════════════════════\n\n"
        f"**👤 Профиль:**\n"
        f"├ Имя: {full_name}\n"
        f"├ Username: {username_display}\n"
        f"└ ID: `{user_id}`\n\n"
        f"{limit_info}\n\n"
    )
    
    if not is_premium:
        status_message += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 **Хочешь больше?**\n"
            f"├ 🚀 10 генераций/день\n"
            f"├ ⚡ Приоритетная обработка\n"
            f"└ 💰 Всего 200₽/месяц\n\n"
            f"Нажми '💎 Купить Premium'"
        )
    else:
        status_message += f"✨ **Спасибо за поддержку проекта!**"
    
    await message.answer(
        status_message,
        reply_markup=main_keyboard(user_id),
        parse_mode="Markdown"
    )

# === АДМИН ПАНЕЛЬ ===
@router.message(F.text == "👑 Админ панель")
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1")
    premium = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
    blocked = c.fetchone()[0]
    conn.close()
    
    await message.answer(
        "╔═══════════════════════╗\n"
        "║   👑 **АДМИН ПАНЕЛЬ**   ║\n"
        "╚═══════════════════════╝\n\n"
        "**📊 Текущая статистика:**\n"
        f"├ 👥 Всего: {total}\n"
        f"├ 🌟 Premium: {premium}\n"
        f"├ 🔓 FREE: {total - premium - blocked}\n"
        f"└ 🚫 Заблокировано: {blocked}\n\n"
        "**⚙️ Доступные действия:**\n"
        "Выбери нужную команду ⬇️",
        reply_markup=admin_keyboard(),
        parse_mode="Markdown"
    )

@router.message(F.text == "➕ Выдать Premium")
async def grant_premium_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(AdminState.grant_premium)
    await message.answer(
        "╔═══════════════════════╗\n"
        "║  ➕ **ВЫДАТЬ PREMIUM**  ║\n"
        "╚═══════════════════════╝\n\n"
        "**Отправь один из вариантов:**\n"
        "├ @username пользователя\n"
        "└ Числовой ID\n\n"
        "**Примеры:**\n"
        "• @ivan_petrov\n"
        "• 123456789\n\n"
        "💡 '🔙 Главное меню' для отмены",
        reply_markup=admin_keyboard(),
        parse_mode="Markdown"
    )

@router.message(AdminState.grant_premium)
async def grant_premium_process(message: types.Message, state: FSMContext):
    input_text = message.text
    
    try:
        if input_text.startswith('@'):
            target_id = get_user_by_login(input_text[1:])
            if not target_id:
                await message.answer(
                    f"❌ **Пользователь не найден!**\n\n"
                    f"Username: {input_text}\n\n"
                    f"**Возможные причины:**\n"
                    f"├ Пользователь не запускал /start\n"
                    f"├ Неверный username\n"
                    f"└ Username изменён\n\n"
                    f"💡 Попробуй использовать числовой ID",
                    reply_markup=admin_keyboard(),
                    parse_mode="Markdown"
                )
                return
        else:
            target_id = int(input_text)
        
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE user_id = ?", (target_id,))
        user_info = c.fetchone()
        conn.close()
        
        user_display = f"@{user_info[0]}" if user_info and user_info[0] else f"ID: {target_id}"
        
        set_premium_status(target_id, 1)
        await message.answer(
            f"✅ **Premium успешно выдан!**\n\n"
            f"**Пользователь:** {user_display}\n"
            f"**ID:** `{target_id}`\n\n"
            f"**Активированные функции:**\n"
            f"├ ✅ 10 генераций/день\n"
            f"└ ✅ Приоритетная обработка",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown"
        )
        await state.clear()
        
    except ValueError:
        await message.answer(
            "❌ **Неверный формат!**\n\n"
            "**Правильные форматы:**\n"
            "├ Числовой ID: `123456789`\n"
            "└ Username: `@username`",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown"
        )

@router.message(F.text == "➖ Забрать Premium")
async def revoke_premium_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(AdminState.revoke_premium)
    await message.answer(
        "╔═══════════════════════╗\n"
        "║  ➖ **ЗАБРАТЬ PREMIUM**  ║\n"
        "╚═══════════════════════╝\n\n"
        "**Отправь один из вариантов:**\n"
        "├ @username пользователя\n"
        "└ Числовой ID\n\n"
        "⚠️ Premium будет удалён немедленно\n\n"
        "💡 '🔙 Главное меню' для отмены",
        reply_markup=admin_keyboard(),
        parse_mode="Markdown"
    )

@router.message(AdminState.revoke_premium)
async def revoke_premium_process(message: types.Message, state: FSMContext):
    input_text = message.text
    
    try:
        if input_text.startswith('@'):
            target_id = get_user_by_login(input_text[1:])
            if not target_id:
                await message.answer("❌ Пользователь не найден!", reply_markup=admin_keyboard())
                return
        else:
            target_id = int(input_text)
        
        set_premium_status(target_id, 0)
        await message.answer(
            f"✅ **Premium успешно удалён!**\n\n"
            f"**ID:** `{target_id}`\n\n"
            f"**Статус изменён на:** FREE\n"
            f"└ Лимит: {FREE_LIMIT} генераций/день",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown"
        )
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Неверный формат!", reply_markup=admin_keyboard())

@router.message(F.text == "🚫 Заблокировать")
async def block_user_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(AdminState.block_user)
    await message.answer(
        "╔═══════════════════════════╗\n"
        "║  🚫 **ЗАБЛОКИРОВАТЬ ЮЗЕРА**  ║\n"
        "╚═══════════════════════════╝\n\n"
        "**Отправь @username или ID**\n\n"
        "⚠️ **Последствия блокировки:**\n"
        "├ ❌ Доступ к боту закрыт\n"
        "├ ❌ Premium отменяется\n"
        "└ ❌ Лимиты обнуляются\n\n"
        "💡 '🔙 Главное меню' для отмены",
        reply_markup=admin_keyboard(),
        parse_mode="Markdown"
    )

@router.message(AdminState.block_user)
async def block_user_process(message: types.Message, state: FSMContext):
    input_text = message.text
    
    try:
        if input_text.startswith('@'):
            target_id = get_user_by_login(input_text[1:])
            if not target_id:
                await message.answer("❌ Пользователь не найден!", reply_markup=admin_keyboard())
                return
        else:
            target_id = int(input_text)
        
        block_user(target_id)
        await message.answer(
            f"🚫 **Пользователь заблокирован!**\n\n"
            f"**ID:** `{target_id}`",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown"
        )
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Неверный формат!", reply_markup=admin_keyboard())

@router.message(F.text == "✅ Разблокировать")
async def unblock_user_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    await state.set_state(AdminState.unblock_user)
    await message.answer(
        "╔═══════════════════════════╗\n"
        "║  ✅ **РАЗБЛОКИРОВАТЬ ЮЗЕРА**  ║\n"
        "╚═══════════════════════════╝\n\n"
        "**Отправь @username или ID**\n\n"
        "💡 '🔙 Главное меню' для отмены",
        reply_markup=admin_keyboard(),
        parse_mode="Markdown"
    )

@router.message(AdminState.unblock_user)
async def unblock_user_process(message: types.Message, state: FSMContext):
    input_text = message.text
    
    try:
        if input_text.startswith('@'):
            target_id = get_user_by_login(input_text[1:])
            if not target_id:
                await message.answer("❌ Пользователь не найден!", reply_markup=admin_keyboard())
                return
        else:
            target_id = int(input_text)
        
        unblock_user(target_id)
        await message.answer(
            f"✅ **Пользователь разблокирован!**\n\n"
            f"**ID:** `{target_id}`",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown"
        )
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Неверный формат!", reply_markup=admin_keyboard())

@router.message(F.text == "📋 Список Premium")
async def premium_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, username FROM users WHERE is_premium = 1 ORDER BY user_id")
    users = c.fetchall()
    conn.close()
    
    if users:
        text_list = "\n".join([f"{idx}. `{uid}` (@{uname or 'нет'})" 
                              for idx, (uid, uname) in enumerate(users, 1)])
    else:
        text_list = "_Список пуст_"
    
    await message.answer(
        f"╔════════════════════════╗\n"
        f"║  📋 **PREMIUM ЮЗЕРЫ**  ║\n"
        f"╚════════════════════════╝\n\n"
        f"**Всего Premium: {len(users)}**\n\n"
        f"{text_list}",
        reply_markup=admin_keyboard(),
        parse_mode="Markdown"
    )

@router.message(F.text == "📊 Статистика")
async def statistics(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1")
    premium = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
    blocked = c.fetchone()[0]
    
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE last_reset = ?", (today,))
    active_today = c.fetchone()[0]
    
    c.execute("SELECT SUM(img_count) FROM users WHERE last_reset = ?", (today,))
    total_images = c.fetchone()[0] or 0
    
    conn.close()
    
    free_users = total - premium - blocked
    premium_percent = int((premium / total * 100)) if total > 0 else 0
    
    await message.answer(
        f"╔═══════════════════════╗\n"
        f"║   📊 **СТАТИСТИКА**   ║\n"
        f"╚═══════════════════════╝\n\n"
        f"**👥 Пользователи:**\n"
        f"├ Всего: **{total}**\n"
        f"├ 🌟 Premium: **{premium}** ({premium_percent}%)\n"
        f"├ 🔓 FREE: **{free_users}**\n"
        f"└ 🚫 Заблокировано: **{blocked}**\n\n"
        f"**📈 Активность:**\n"
        f"├ Активных сегодня: **{active_today}**\n"
        f"└ Генераций сегодня: **{total_images}**\n\n"
        f"**💰 Конверсия:**\n"
        f"└ FREE → Premium: **{premium_percent}%**\n\n"
        f"_Обновлено: {datetime.now().strftime('%H:%M:%S')}_",
        reply_markup=admin_keyboard(),
        parse_mode="Markdown"
    )

# === ГЕНЕРАЦИЯ ===
@router.message(F.text == "🎨 Генерация")
async def generate_start(message: types.Message, state: FSMContext):
    await message.answer(
        "📤 **Отправь фото** (PNG/JPG)\n\n"
        "После этого я попрошу тебя\n"
        "написать промпт для редактирования\n\n"
        "💡 **Примеры промптов:**\n"
        "• добавь закат\n"
        "• аниме стиль\n"
        "• сделай реалистичнее",
        reply_markup=main_keyboard(message.from_user.id),
        parse_mode="Markdown"
    )

@router.message(F.text == "✍️ Текст в фото")
async def text_to_image_start(message: types.Message, state: FSMContext):
    await state.set_state(GenState.waiting_text_prompt)
    await message.answer(
        "✍️ **Создание фото из текста**\n\n"
        "Опиши что хочешь увидеть\n\n"
        "💡 **Примеры:**\n"
        "• кот в космосе\n"
        "• закат на море\n"
        "• футуристический город",
        reply_markup=main_keyboard(message.from_user.id),
        parse_mode="Markdown"
    )

@router.message(F.photo)
async def photo_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    remaining, is_premium = get_limit(user_id)
    
    if remaining <= 0:
        await message.answer(
            f"❌ **Лимит исчерпан!**\n\n"
            f"{'Premium' if is_premium else 'FREE'}: {PREMIUM_LIMIT if is_premium else FREE_LIMIT} генераций/день\n"
            f"Обновление: завтра в 00:00\n\n"
            f"💎 Нажми '💎 Купить Premium'",
            reply_markup=main_keyboard(user_id),
            parse_mode="Markdown"
        )
        return
    
    photo_file = BytesIO()
    await message.bot.download(message.photo[-1], photo_file)
    photo_bytes = photo_file.getvalue()

    if photo_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = "image/png"
    elif photo_bytes.startswith(b'\xFF\xD8'):
        mime = "image/jpeg"
    else:
        await message.answer("❌ Только PNG/JPG!", reply_markup=main_keyboard(user_id))
        return

    b64_data = base64.b64encode(photo_bytes).decode()
    image_url = f"{mime};base64,{b64_data}"

    await state.update_data(image_url=image_url)
    await message.answer(
        "✅ **Фото загружено!**\n\n"
        "Теперь напиши промпт\n"
        "для редактирования фото 💭",
        reply_markup=main_keyboard(user_id),
        parse_mode="Markdown"
    )
    await state.set_state(GenState.waiting_prompt)

@router.message(GenState.waiting_prompt)
async def generate_image(message: types.Message, state: FSMContext):
    data = await state.get_data()
    image_url = data.get("image_url")
    
    if not image_url:
        await message.answer("❌ Сначала отправь фото!", reply_markup=main_keyboard(message.from_user.id))
        await state.clear()
        return
    
    prompt = message.text or "улучши фото"
    user_id = message.from_user.id
    remaining, is_premium = get_limit(user_id)

    if remaining <= 0:
        await message.answer(
            f"❌ **Лимит исчерпан!**",
            reply_markup=main_keyboard(user_id),
            parse_mode="Markdown"
        )
        await state.clear()
        return

    await message.answer("🎨 **Генерирую фото...**", parse_mode="Markdown")

    try:
        response = await client.chat.completions.create(
            model="gemini-2.5-flash-image-preview",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Перегенерируй фото по инструкции: {prompt}"},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }],
            modalities=["image", "text"]
        )

        assistant_message = response.choices[0].message
        if assistant_message.images:
            img_url = assistant_message.images[0].image_url.url
            b64_content = img_url.split(',')[1] if ',' in img_url else img_url
            img_bytes = base64.b64decode(b64_content)
            photo = BufferedInputFile(img_bytes, filename="generated.png")

            use_limit(user_id)
            caption = f"✅ **Готово!**\n\nОсталось: {remaining - 1}/{PREMIUM_LIMIT if is_premium else FREE_LIMIT}"
            await message.answer_photo(photo, caption=caption, parse_mode="Markdown")
        else:
            await message.answer("❌ Попробуй другой промпт", reply_markup=main_keyboard(user_id))

    except Exception as e:
        await message.answer(f"🚨 Ошибка: {str(e)[:100]}", reply_markup=main_keyboard(user_id))

    await state.clear()

@router.message(GenState.waiting_text_prompt)
async def text_to_image(message: types.Message, state: FSMContext):
    prompt = message.text
    user_id = message.from_user.id
    remaining, is_premium = get_limit(user_id)

    if remaining <= 0:
        await message.answer(
            "❌ **Лимит исчерпан!**",
            reply_markup=main_keyboard(user_id),
            parse_mode="Markdown"
        )
        await state.clear()
        return

    await message.answer("🎨 **Создаю по тексту...**", parse_mode="Markdown")

    try:
        response = await client.chat.completions.create(
            model="gemini-2.5-flash-image-preview",
            messages=[{"role": "user", "content": f"Создай качественное фото: {prompt}"}],
            modalities=["image", "text"]
        )

        assistant_message = response.choices[0].message
        if assistant_message.images:
            img_url = assistant_message.images[0].image_url.url
            b64_content = img_url.split(',')[1] if ',' in img_url else img_url
            img_bytes = base64.b64decode(b64_content)
            photo = BufferedInputFile(img_bytes, filename="generated.png")

            use_limit(user_id)
            caption = f"✅ **Готово!**\n\nОсталось: {remaining - 1}/{PREMIUM_LIMIT if is_premium else FREE_LIMIT}"
            await message.answer_photo(photo, caption=caption, parse_mode="Markdown")
        else:
            await message.answer("❌ Не удалось создать фото", reply_markup=main_keyboard(user_id))

    except Exception as e:
        await message.answer(f"🚨 {str(e)[:100]}", reply_markup=main_keyboard(user_id))

    await state.clear()

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    dp.include_router(router)
    print("🤖 PhotoGen Bot запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
