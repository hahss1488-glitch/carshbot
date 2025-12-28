# main.py
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"  # <- Вставь сюда токен

PRICE = {
    "запр": 203,
    "пров": 93
}

def get_db(user_id):
    db_name = f"data_{user_id}.db"
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine TEXT,
            service TEXT,
            amount INTEGER,
            date TEXT
        )
    """)
    conn.commit()
    return conn, c

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Записать машину", callback_data="record")],
        [InlineKeyboardButton("Отчеты", callback_data="reports")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Выбирай действие:", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    conn, c = get_db(user_id)

    if query.data == "record":
        keyboard = [
            [InlineKeyboardButton("запр", callback_data="service_запр")],
            [InlineKeyboardButton("пров", callback_data="service_пров")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите услугу:", reply_markup=reply_markup)

    elif query.data.startswith("service_"):
        service = query.data.split("_")[1]
        context.user_data["service"] = service
        await query.edit_message_text("Введите номер машины:")

    elif query.data == "reports":
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        c.execute("SELECT COUNT(*), SUM(amount) FROM records WHERE date LIKE ?", (today_str+'%',))
        count, total = c.fetchone()
        total = total or 0

        date_from = (now - timedelta(days=9)).strftime("%Y-%m-%d")
        c.execute("SELECT SUM(amount) FROM records WHERE date BETWEEN ? AND ?", (date_from, today_str))
        dec_total = c.fetchone()[0] or 0

        c.execute("SELECT COUNT(DISTINCT machine) FROM records")
        total_machines = c.fetchone()[0] or 0

        c.execute("SELECT SUM(amount) FROM records")
        total_sum = c.fetchone()[0] or 0
        avg = total_sum / total_machines if total_machines else 0

        msg = f"Итоги:\nСегодня: {count} машин, сумма {total}₽\n" \
              f"Декада: сумма {dec_total}₽\n" \
              f"Всего машин: {total_machines}\nСредний чек: {avg:.0f}₽"
        await query.edit_message_text(msg)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    service = context.user_data.get("service")
    if not service:
        await update.message.reply_text("Сначала выбери услугу через кнопки.")
        return

    machine = update.message.text.strip()
    amount = PRICE.get(service, 0)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn, c = get_db(user_id)
    c.execute("INSERT INTO records (machine, service, amount, date) VALUES (?, ?, ?, ?)",
              (machine, service, amount, now_str))
    conn.commit()

    await update.message.reply_text(f"Записано: Машина {machine}, услуга {service}, сумма {amount}₽")
    context.user_data.pop("service")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

if __name__ == "__main__":
    app.run_polling()
