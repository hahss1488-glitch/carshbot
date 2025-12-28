import sqlite3
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"

# ===== НОРМАЛИЗАЦИЯ НОМЕРА =====
RU_TO_EN = {
    "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X"
}
ALLOWED_LETTERS = set("ABEKMHOPCTYX")
DEFAULT_REGION = "797"

def normalize_car_number(text: str) -> str | None:
    text = text.upper().replace(" ", "")
    result = ""
    for ch in text:
        result += RU_TO_EN.get(ch, ch)
    # Добавляем регион если не указано
    pattern_full = r"^[A-Z][0-9]{3}[A-Z]{2}[0-9]{3}$"
    pattern_no_region = r"^[A-Z][0-9]{3}[A-Z]{2}$"
    if re.match(pattern_full, result):
        pass
    elif re.match(pattern_no_region, result):
        result += DEFAULT_REGION
    else:
        return None
    # Проверяем буквы
    letters = result[0] + result[4:6]
    if any(l not in ALLOWED_LETTERS for l in letters):
        return None
    return result

# ===== ПРАЙС (дневной / ночной) =====
PRICE_DAY = {
    "Заправка": 203,
    "Проверка": 93,
    "Подкачка": 63
}
PRICE_NIGHT = PRICE_DAY.copy()  # для примера одинаково, позже можно отдельно

def get_price(service_name: str, dt: datetime) -> int:
    if 21 <= dt.hour or dt.hour < 9:
        return PRICE_NIGHT.get(service_name, 0)
    return PRICE_DAY.get(service_name, 0)

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

    c.execute("""CREATE TABLE IF NOT EXISTS services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_id INTEGER,
        service_name TEXT,
        quantity INTEGER,
        unit_price INTEGER,
        total_sum INTEGER,
        created_at TEXT
    )""")

    conn.commit()
    return conn, c

# ===== МЕНЮ =====
def main_menu():
    keyboard = [
        [InlineKeyboardButton("Открыть смену", callback_data="open_shift"),
         InlineKeyboardButton("Закрыть смену", callback_data="close_shift")],
        [InlineKeyboardButton("Записать машину", callback_data="record_car")],
        [InlineKeyboardButton("Отчёты", callback_data="reports")],
        [InlineKeyboardButton("История смен", callback_data="shift_history")]
    ]
    return InlineKeyboardMarkup(keyboard)

def services_menu():
    keyboard = [
        [InlineKeyboardButton("Заправка", callback_data="service_Заправка")],
        [InlineKeyboardButton("Проверка", callback_data="service_Проверка")],
        [InlineKeyboardButton("Подкачка", callback_data="service_Подкачка")],
        [InlineKeyboardButton("Назад", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ===== ХЕНДЛЕРЫ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Выбирай действие:",
        reply_markup=main_menu()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    conn, c = get_db(user_id)

    # ==== Главное меню ====
    if query.data == "open_shift":
        now = datetime.now().isoformat()
        c.execute("INSERT INTO shifts (user_id, start_time) VALUES (?, ?)", (user_id, now))
        conn.commit()
        await query.edit_message_text(f"Смена открыта: {datetime.now().strftime('%d.%m.%Y %H:%M')}", reply_markup=main_menu())

    elif query.data == "close_shift":
        c.execute("SELECT id, start_time FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
        shift = c.fetchone()
        if not shift:
            await query.edit_message_text("Нет активной смены.", reply_markup=main_menu())
            return
        shift_id, start_time = shift
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.now()
        duration = end_dt - start_dt
        c.execute("UPDATE shifts SET end_time=? WHERE id=?", (end_dt.isoformat(), shift_id))
        conn.commit()
        await query.edit_message_text(
            f"Смена закрыта:\nНачало: {start_dt.strftime('%d.%m.%Y %H:%M')}\n"
            f"Конец: {end_dt.strftime('%d.%m.%Y %H:%M')}\n"
            f"Длительность: {duration.seconds//3600} ч {(duration.seconds//60)%60} мин",
            reply_markup=main_menu()
        )

    elif query.data == "record_car":
        context.user_data["awaiting_car"] = True
        await query.edit_message_text("Введите номер машины (например H360PY или H360PY797):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back_main")]]))

    elif query.data.startswith("service_"):
        if "current_car_id" not in context.user_data:
            await query.edit_message_text("Сначала выбери машину.", reply_markup=main_menu())
            return
        service_name = query.data.split("_")[1]
        car_id = context.user_data["current_car_id"]
        now = datetime.now().isoformat()
        price = get_price(service_name, datetime.now())
        # проверка, есть ли такая услуга
        c.execute("SELECT id, quantity, total_sum, unit_price FROM services WHERE car_id=? AND service_name=?", (car_id, service_name))
        s = c.fetchone()
        if s:
            sid, qty, total, unit = s
            qty +=1
            total += price
            c.execute("UPDATE services SET quantity=?, total_sum=? WHERE id=?", (qty, total, sid))
        else:
            c.execute("INSERT INTO services (car_id, service_name, quantity, unit_price, total_sum, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (car_id, service_name, 1, price, price, now))
        conn.commit()
        # обновляем сообщение красиво
        c.execute("SELECT car_number FROM cars WHERE id=?", (car_id,))
        car_number = c.fetchone()[0]
        c.execute("SELECT service_name, quantity, total_sum FROM services WHERE car_id=?", (car_id,))
        services = c.fetchall()
        text = f"Машина записана:\nНомер: {car_number}\n\nВыбранные услуги:\n"
        total_all = 0
        for sname, qty, tsum in services:
            text += f"- {sname} ({qty}): {tsum}₽\n"
            total_all += tsum
        text += f"\nИтого: {total_all}₽"
        await query.edit_message_text(text, reply_markup=services_menu())

    elif query.data == "back_main":
        context.user_data.pop("awaiting_car", None)
        context.user_data.pop("current_car_id", None)
        await query.edit_message_text("Главное меню:", reply_markup=main_menu())

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.user_data.get("awaiting_car"):
        await update.message.reply_text("Используйте меню.", reply_markup=main_menu())
        return

    car_raw = update.message.text
    car = normalize_car_number(car_raw)
    if not car:
        await update.message.reply_text("Ошибка в номере ТС или регионе.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back_main")]]))
        return

    conn, c = get_db(user_id)
    c.execute("SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    shift = c.fetchone()
    if not shift:
        await update.message.reply_text("Нет активной смены.", reply_markup=main_menu())
        return
    shift_id = shift[0]

    # Добавляем машину
    try:
        c.execute("INSERT INTO cars (shift_id, car_number, created_at) VALUES (?, ?, ?)", (shift_id, car, datetime.now().isoformat()))
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # уже есть

    # получаем car_id
    c.execute("SELECT id FROM cars WHERE shift_id=? AND car_number=?", (shift_id, car))
    car_id = c.fetchone()[0]
    context.user_data["current_car_id"] = car_id
    context.user_data.pop("awaiting_car", None)

    await update.message.reply_text(
        f"Машина записана:\nНомер: {car}\n\nВыберите услугу:",
        reply_markup=services_menu()
    )

# ===== ЗАПУСК =====
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

if __name__ == "__main__":
    app.run_polling()

