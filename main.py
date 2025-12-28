import sqlite3
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"
MOSCOW = ZoneInfo("Europe/Moscow")
DEFAULT_REGION = "797"

# ===== ПРАЙС =====
PRICE_DAY = {
    "Заправка": 203,
    "Проверка": 93,
    "Подкачка": 63
}
PRICE_NIGHT = PRICE_DAY.copy()  # Можно задать разные цены для ночи

def get_price(service_name: str, dt: datetime) -> int:
    if 21 <= dt.hour or dt.hour < 9:
        return PRICE_NIGHT.get(service_name, 0)
    return PRICE_DAY.get(service_name, 0)

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
    pattern_full = r"^[A-Z][0-9]{3}[A-Z]{2}[0-9]{3}$"
    pattern_no_region = r"^[A-Z][0-9]{3}[A-Z]{2}$"
    if re.match(pattern_full, result):
        pass
    elif re.match(pattern_no_region, result):
        result += DEFAULT_REGION
    else:
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
def main_menu(shift_opened: bool):
    keyboard = []
    if not shift_opened:
        keyboard.append([InlineKeyboardButton("Открыть смену", callback_data="open_shift")])
    else:
        keyboard.append([InlineKeyboardButton("Закрыть смену", callback_data="close_shift")])
    keyboard.append([InlineKeyboardButton("Записать машину", callback_data="record_car")])
    if shift_opened:
        keyboard.append([InlineKeyboardButton("Итоги текущей смены", callback_data="shift_summary")])
        keyboard.append([InlineKeyboardButton("Список машин за сегодня", callback_data="today_cars")])
    keyboard.append([InlineKeyboardButton("Отчёты", callback_data="reports")])
    keyboard.append([InlineKeyboardButton("История смен", callback_data="shift_history")])
    return InlineKeyboardMarkup(keyboard)

def services_menu(delete_mode=False):
    top_buttons = [
        InlineKeyboardButton("Готова", callback_data="car_done"),
        InlineKeyboardButton("Удалить услугу" if not delete_mode else "Отмена удаления", callback_data="toggle_delete")
    ]
    service_buttons = [
        [InlineKeyboardButton("Заправка", callback_data="service_Заправка")],
        [InlineKeyboardButton("Проверка", callback_data="service_Проверка")],
        [InlineKeyboardButton("Подкачка", callback_data="service_Подкачка")]
    ]
    return InlineKeyboardMarkup([top_buttons] + service_buttons)

# ===== ХЕНДЛЕРЫ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn, c = get_db(user_id)
    c.execute("SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    shift_opened = bool(c.fetchone())
    await update.message.reply_text("Главное меню:", reply_markup=main_menu(shift_opened))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    conn, c = get_db(user_id)

    c.execute("SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    shift = c.fetchone()
    shift_opened = bool(shift)

    if query.data == "open_shift":
        now = datetime.now(MOSCOW).isoformat()
        c.execute("INSERT INTO shifts (user_id, start_time) VALUES (?, ?)", (user_id, now))
        conn.commit()
        await query.edit_message_text(f"Смена открыта: {datetime.now(MOSCOW).strftime('%d.%m.%Y %H:%M')}", reply_markup=main_menu(True))

    elif query.data == "close_shift":
        if not shift_opened:
            await query.edit_message_text("Нет активной смены.", reply_markup=main_menu(False))
            return
        shift_id = shift[0]
        c.execute("SELECT start_time FROM shifts WHERE id=?", (shift_id,))
        start_time = datetime.fromisoformat(c.fetchone()[0])
        end_time = datetime.now(MOSCOW)
        duration = end_time - start_time
        c.execute("UPDATE shifts SET end_time=? WHERE id=?", (end_time.isoformat(), shift_id))
        conn.commit()
        await query.edit_message_text(
            f"Смена закрыта:\nНачало: {start_time.strftime('%d.%m.%Y %H:%M')}\n"
            f"Конец: {end_time.strftime('%d.%m.%Y %H:%M')}\n"
            f"Длительность: {duration.seconds//3600} ч {(duration.seconds//60)%60} мин",
            reply_markup=main_menu(False)
        )

    elif query.data == "record_car":
        if not shift_opened:
            await query.edit_message_text("Нет активной смены.", reply_markup=main_menu(False))
            return
        context.user_data["awaiting_car"] = True
        await query.edit_message_text("Введите номер машины (например H360PY или H360PY797):")

    elif query.data.startswith("service_") and "current_car_id" in context.user_data:
        service_name = query.data.split("_")[1]
        car_id = context.user_data["current_car_id"]
        now = datetime.now(MOSCOW).isoformat()
        price = get_price(service_name, datetime.now(MOSCOW))

        # Режим удаления
        if context.user_data.get("delete_mode"):
            c.execute("SELECT id, quantity, total_sum FROM services WHERE car_id=? AND service_name=?", (car_id, service_name))
            s = c.fetchone()
            if s:
                sid, qty, total = s
                if qty > 1:
                    qty -= 1
                    total -= price
                    c.execute("UPDATE services SET quantity=?, total_sum=? WHERE id=?", (qty, total, sid))
                else:
                    c.execute("DELETE FROM services WHERE id=?", (sid,))
                conn.commit()
        else:  # обычный режим добавления
            c.execute("SELECT id, quantity, total_sum FROM services WHERE car_id=? AND service_name=?", (car_id, service_name))
            s = c.fetchone()
            if s:
                sid, qty, total = s
                qty += 1
                total += price
                c.execute("UPDATE services SET quantity=?, total_sum=? WHERE id=?", (qty, total, sid))
            else:
                c.execute("INSERT INTO services (car_id, service_name, quantity, unit_price, total_sum, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (car_id, service_name, 1, price, price, now))
            conn.commit()

        # Обновляем сообщение карточки машины
        c.execute("SELECT car_number FROM cars WHERE id=?", (car_id,))
        car_number = c.fetchone()[0]
        c.execute("SELECT service_name, quantity, total_sum FROM services WHERE car_id=?", (car_id,))
        services = c.fetchall()
        text = f"Машина:\n{car_number}\n\nВыбранные услуги:\n"
        total_all = 0
        for sname, qty, tsum in services:
            text += f"- {sname} ({qty}): {tsum}₽\n"
            total_all += tsum
        text += f"\nИтого: {total_all}₽"
        await query.edit_message_text(text, reply_markup=services_menu(context.user_data.get("delete_mode", False)))

    elif query.data == "toggle_delete":
        context.user_data["delete_mode"] = not context.user_data.get("delete_mode", False)
        car_id = context.user_data.get("current_car_id")
        if car_id:
            c.execute("SELECT car_number FROM cars WHERE id=?", (car_id,))
            car_number = c.fetchone()[0]
            c.execute("SELECT service_name, quantity, total_sum FROM services WHERE car_id=?", (car_id,))
            services = c.fetchall()
            text = f"Машина:\n{car_number}\n\nВыбранные услуги:\n"
            total_all = 0
            for sname, qty, tsum in services:
                text += f"- {sname} ({qty}): {tsum}₽\n"
                total_all += tsum
            text += f"\nИтого: {total_all}₽"
            await query.edit_message_text(text, reply_markup=services_menu(context.user_data["delete_mode"]))

    elif query.data == "car_done":
        car_id = context.user_data.pop("current_car_id", None)
        context.user_data["delete_mode"] = False
        if car_id:
            c.execute("SELECT car_number FROM cars WHERE id=?", (car_id,))
            car_number = c.fetchone()[0]
            c.execute("SELECT service_name, quantity, total_sum FROM services WHERE car_id=?", (car_id,))
            services = c.fetchall()
            text = f"Машина записана:\n{car_number}\n\nУслуги:\n"
            total_all = 0
            for sname, qty, tsum in services:
                text += f"- {sname} ({qty}): {tsum}₽\n"
                total_all += tsum
            text += f"\nИтого: {total_all}₽"
            await query.message.reply_text(text)
        await query.edit_message_text("Выберите действие:", reply_markup=main_menu(True))

    elif query.data == "shift_summary":
        if not shift_opened:
            await query.edit_message_text("Нет активной смены.", reply_markup=main_menu(False))
            return
        shift_id = shift[0]
        c.execute("SELECT car_number, id FROM cars WHERE shift_id=?", (shift_id,))
        cars = c.fetchall()
        if not cars:
            await query.edit_message_text("Смена пуста.", reply_markup=main_menu(True))
            return
        text = "Итоги текущей смены:\n"
        total_shift = 0
        for car_number, car_id in cars:
            c.execute("SELECT service_name, quantity, total_sum FROM services WHERE car_id=?", (car_id,))
            services = c.fetchall()
            car_sum = sum(tsum for _, _, tsum in services)
            total_shift += car_sum
            text += f"\n{car_number}:\n"
            for sname, qty, tsum in services:
                text += f"- {sname} ({qty}): {tsum}₽\n"
            text += f"Итого по машине: {car_sum}₽\n"
        text += f"\nСумма по смене: {total_shift}₽"
        await query.edit_message_text(text, reply_markup=main_menu(True))

    elif query.data == "today_cars":
        if not shift_opened:
            await query.edit_message_text("Нет активной смены.", reply_markup=main_menu(False))
            return
        shift_id = shift[0]
        c.execute("SELECT car_number, id FROM cars WHERE shift_id=?", (shift_id,))
        cars = c.fetchall()
        if not cars:
            await query.edit_message_text("Сегодня ещё нет машин.", reply_markup=main_menu(True))
            return
        keyboard = [[InlineKeyboardButton(car_number, callback_data=f"edit_car_{car_id}")] for car_number, car_id in cars]
        await query.edit_message_text("Выберите машину для редактирования:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("edit_car_"):
        car_id = int(query.data.split("_")[2])
        context.user_data["current_car_id"] = car_id
        context.user_data["delete_mode"] = False
        c.execute("SELECT car_number FROM cars WHERE id=?", (car_id,))
        car_number = c.fetchone()[0]
        c.execute("SELECT service_name, quantity, total_sum FROM services WHERE car_id=?", (car_id,))
        services = c.fetchall()
        text = f"Редактирование машины:\n{car_number}\n\nУслуги:\n"
        for sname, qty, tsum in services:
            text += f"- {sname} ({qty}): {tsum}₽\n"
        await query.edit_message_text(text, reply_markup=services_menu(False))

    elif query.data == "reports":
        await query.edit_message_text("Здесь будут отчёты (заглушка).", reply_markup=main_menu(shift_opened))

    elif query.data == "shift_history":
        await query.edit_message_text("Здесь будет история смен (заглушка).", reply_markup=main_menu(shift_opened))

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.user_data.get("awaiting_car"):
        await update.message.reply_text("Используйте меню.")
        return

    car_raw = update.message.text
    car = normalize_car_number(car_raw)
    if not car:
        await update.message.reply_text("Ошибка в номере ТС или регионе.")
        return

    conn, c = get_db(user_id)
    c.execute("SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    shift = c.fetchone()
    if not shift:
        await update.message.reply_text("Нет активной смены.")
        return
    shift_id = shift[0]

    try:
        c.execute("INSERT INTO cars (shift_id, car_number, created_at) VALUES (?, ?, ?)", (shift_id, car, datetime.now(MOSCOW).isoformat()))
        conn.commit()
    except sqlite3.IntegrityError:
        pass

    c.execute("SELECT id FROM cars WHERE shift_id=? AND car_number=?", (shift_id, car))
    car_id = c.fetchone()[0]
    context.user_data["current_car_id"] = car_id
    context.user_data.pop("awaiting_car", None)

    await update.message.reply_text(
        f"Машина записана:\nНомер: {car}\n\nВыберите услугу:",
        reply_markup=services_menu(False)
    )

# ===== ЗАПУСК =====
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

if __name__ == "__main__":
    app.run_polling()
