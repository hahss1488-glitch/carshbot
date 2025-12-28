import sqlite3
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

TOKEN = "ВСТАВЬ_СВОЙ_TOKEN"

# ===== НОРМАЛИЗАЦИЯ НОМЕРА =====

RU_TO_EN = {
    "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X"
}

ALLOWED_LETTERS = set("ABEKMHOPCTYX")

def normalize_car_number(text: str) -> str | None:
    text = text.upper().replace(" ", "")
    result = ""
    for ch in text:
        result += RU_TO_EN.get(ch, ch)
    # формат: A123BC777
    pattern = r"^[A-Z][0-9]{3}[A-Z]{2}[0-9]{3}$"
    if not re.match(pattern, result):
        return None
    letters = result[0] + result[4:6]
    if any(l not in ALLOWED_LETTERS for l in letters):
        return None
    return result

# ===== БАЗА =====

def get_db(user_id):
    conn = sqlite3.connect(f"user_{user_id}.db")
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        start_time TEXT,
        end_time TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS cars (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shift_id INTEGER,
        car_number TEXT,
        created_at TEXT,
        UNIQUE(shift_id, car_number)
    )""")

    conn.commit()
    return conn, c

# ===== ХЕНДЛЕРЫ =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Начать смену", callback_data="start_shift")]]
    await update.message.reply_text(
        "Готов к работе.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    conn, c = get_db(user_id)

    if query.data == "start_shift":
        c.execute(
            "INSERT INTO shifts (user_id, start_time) VALUES (?, ?)",
            (user_id, datetime.now().isoformat())
        )
        conn.commit()

        await query.edit_message_text(
            "Смена начата.\nВведи номер машины (формат A123BC777)."
        )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    car_raw = update.message.text
    car = normalize_car_number(car_raw)

    if not car:
        await update.message.reply_text("Ошибка в номере ТС или регионе.")
        return

    conn, c = get_db(user_id)

    # Находим активную смену
    c.execute("SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    shift = c.fetchone()
    if not shift:
        await update.message.reply_text("Нет активной смены. Сначала начни смену.")
        return

    shift_id = shift[0]

    try:
        c.execute(
            "INSERT INTO cars (shift_id, car_number, created_at) VALUES (?, ?, ?)",
            (shift_id, car, datetime.now().isoformat())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # машина уже есть в смене

    await update.message.reply_text(f"Машина {car} выбрана.")
    
# ===== ЗАПУСК =====

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

if __name__ == "__main__":
    app.run_polling()
