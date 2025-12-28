import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import re

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"
TZ = ZoneInfo("Europe/Moscow")

# ===== ПРАЙС (ПОКА ЗАГЛУШКА, ТЫ ЗАМЕНИШЬ) =====
SERVICES = {
    "check": {"name": "Проверка", "day": 93, "night": 120, "repeatable": False},
    "fuel": {"name": "Заправка", "day": 203, "night": 250, "repeatable": False},
    "pump": {"name": "Подкачка", "day": 63, "night": 80, "repeatable": True},
}

# ===== НОРМАЛИЗАЦИЯ НОМЕРОВ =====
RU_TO_EN = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X",
})

CAR_RE = re.compile(r"^[ABEKMHOPCTYX]\d{3}[ABEKMHOPCTYX]{2}\d{3}$")

# ===== DB =====
def db(user_id):
    conn = sqlite3.connect(f"user_{user_id}.db")
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_time TEXT,
        end_time TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS cars (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shift_id INTEGER,
        plate TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        car_id INTEGER,
        code TEXT,
        qty INTEGER,
        price INTEGER
    )
    """)

    conn.commit()
    return conn, c

# ===== HELPERS =====
def now():
    return datetime.now(TZ)

def is_night(dt):
    return dt.hour >= 21 or dt.hour < 9

def normalize_plate(text):
    t = text.upper().replace(" ", "").translate(RU_TO_EN)
    return t

def valid_plate(plate):
    return bool(CAR_RE.match(plate))

# ===== UI =====
def main_menu(has_shift):
    buttons = []
    if not has_shift:
        buttons.append([InlineKeyboardButton("🟢 Открыть смену", callback_data="open_shift")])
        buttons.append([InlineKeyboardButton("📚 История смен", callback_data="history")])
    else:
        buttons.append([InlineKeyboardButton("➕ Добавить машину", callback_data="add_car")])
        buttons.append([InlineKeyboardButton("📊 Итоги смены", callback_data="shift_summary")])
        buttons.append([InlineKeyboardButton("⛔ Закрыть смену", callback_data="close_shift")])
    return InlineKeyboardMarkup(buttons)

def services_keyboard(delete_mode=False):
    rows = []
    top = [
        InlineKeyboardButton("✅ Готово", callback_data="car_done"),
        InlineKeyboardButton(
            "➖ Удаление" if not delete_mode else "➕ Добавление",
            callback_data="toggle_delete"
        ),
    ]
    rows.append(top)

    for code, s in SERVICES.items():
        rows.append([InlineKeyboardButton(s["name"], callback_data=f"svc_{code}")])

    return InlineKeyboardMarkup(rows)

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn, c = db(user_id)

    c.execute("SELECT id FROM shifts WHERE end_time IS NULL")
    active = c.fetchone()

    await update.message.reply_text(
        "Главное меню",
        reply_markup=main_menu(bool(active))
    )

# ===== CALLBACKS =====
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    conn, c = db(user_id)

    # ---- OPEN SHIFT ----
    if q.data == "open_shift":
        c.execute("SELECT id FROM shifts WHERE end_time IS NULL")
        if c.fetchone():
            await q.edit_message_text("Смена уже открыта.")
            return

        t = now().isoformat()
        c.execute("INSERT INTO shifts (start_time) VALUES (?)", (t,))
        conn.commit()
        await q.edit_message_text(
            f"Смена открыта\nНачало: {now().strftime('%d.%m %H:%M')}",
            reply_markup=main_menu(True)
        )

    # ---- CLOSE SHIFT ----
    elif q.data == "close_shift":
        c.execute("SELECT id, start_time FROM shifts WHERE end_time IS NULL")
        row = c.fetchone()
        if not row:
            await q.edit_message_text("Нет активной смены.")
            return

        shift_id = row[0]
        c.execute("UPDATE shifts SET end_time=? WHERE id=?", (now().isoformat(), shift_id))
        conn.commit()

        await q.edit_message_text(
            "Смена закрыта.\nВыберите отчёт:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Денежный отчёт", callback_data="report_money")],
                [InlineKeyboardButton("🔁 Отчёт повторок", callback_data="report_repeat")],
            ])
        )

    # ---- ADD CAR ----
    elif q.data == "add_car":
        context.user_data["mode"] = "wait_plate"
        await q.edit_message_text("Введите номер машины:")

    # ---- SERVICE BUTTONS ----
    elif q.data.startswith("svc_"):
        code = q.data.split("_")[1]
        car_id = context.user_data.get("car_id")
        delete_mode = context.user_data.get("delete", False)

        if not car_id:
            return

        c.execute("SELECT id, qty FROM services WHERE car_id=? AND code=?", (car_id, code))
        row = c.fetchone()

        price_type = "night" if is_night(now()) else "day"
        price = SERVICES[code][price_type]

        if delete_mode:
            if row:
                if row[1] <= 1:
                    c.execute("DELETE FROM services WHERE id=?", (row[0],))
                else:
                    c.execute("UPDATE services SET qty=qty-1 WHERE id=?", (row[0],))
        else:
            if row:
                c.execute("UPDATE services SET qty=qty+1 WHERE id=?", (row[0],))
            else:
                c.execute(
                    "INSERT INTO services (car_id, code, qty, price) VALUES (?, ?, 1, ?)",
                    (car_id, code, price)
                )

        conn.commit()
        await render_car(q, context, c, car_id)

    # ---- TOGGLE DELETE ----
    elif q.data == "toggle_delete":
        context.user_data["delete"] = not context.user_data.get("delete", False)
        await render_car(q, context, c, context.user_data["car_id"])

    # ---- CAR DONE ----
    elif q.data == "car_done":
        car_id = context.user_data.get("car_id")
        if not car_id:
            return

        c.execute("SELECT plate FROM cars WHERE id=?", (car_id,))
        plate = c.fetchone()[0]

        c.execute("""
        SELECT code, qty, price FROM services WHERE car_id=?
        """, (car_id,))
        rows = c.fetchall()

        total = sum(qty * price for _, qty, price in rows)

        lines = [f"Машина записана\n{plate}"]
        for code, qty, price in rows:
            lines.append(f"{SERVICES[code]['name']} ×{qty} = {qty * price} ₽")
        lines.append(f"Итого: {total} ₽")

        await q.message.reply_text("\n".join(lines))

        context.user_data.clear()

        await q.message.reply_text(
            "Главное меню",
            reply_markup=main_menu(True)
        )

    # ---- REPORTS ----
    elif q.data == "report_money":
        await q.edit_message_text(await money_report(c))

    elif q.data == "report_repeat":
        await q.edit_message_text(await repeat_report(c))

    elif q.data == "shift_summary":
        await q.edit_message_text(await money_report(c))

    elif q.data == "history":
        c.execute("SELECT id, start_time, end_time FROM shifts ORDER BY id DESC LIMIT 10")
        rows = c.fetchall()
        text = ["История смен:"]
        for sid, s, e in rows:
            text.append(f"{sid}: {s[:16]} → {e[:16] if e else '...' }")
        await q.edit_message_text("\n".join(text))

# ===== MESSAGE HANDLER =====
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("mode") != "wait_plate":
        return

    user_id = update.effective_user.id
    conn, c = db(user_id)

    plate = normalize_plate(update.message.text)
    if not valid_plate(plate):
        await update.message.reply_text("Ошибка в номере ТС или регионе.")
        return

    c.execute("SELECT id FROM shifts WHERE end_time IS NULL")
    shift = c.fetchone()
    if not shift:
        await update.message.reply_text("Смена не открыта.")
        return

    shift_id = shift[0]

    c.execute("SELECT id FROM cars WHERE shift_id=? AND plate=?", (shift_id, plate))
    row = c.fetchone()

    if row:
        car_id = row[0]
    else:
        c.execute("INSERT INTO cars (shift_id, plate) VALUES (?, ?)", (shift_id, plate))
        car_id = c.lastrowid
        conn.commit()

    context.user_data.clear()
    context.user_data["car_id"] = car_id
    context.user_data["delete"] = False

    await update.message.reply_text(
        f"Машина: {plate}",
        reply_markup=services_keyboard()
    )

# ===== RENDER CAR =====
async def render_car(q, context, c, car_id):
    c.execute("""
    SELECT code, qty, price FROM services WHERE car_id=?
    """, (car_id,))
    rows = c.fetchall()

    c.execute("SELECT plate FROM cars WHERE id=?", (car_id,))
    plate = c.fetchone()[0]

    total = sum(qty * price for _, qty, price in rows)

    lines = [f"Машина: {plate}"]
    for code, qty, price in rows:
        lines.append(f"{SERVICES[code]['name']} ×{qty} = {qty * price} ₽")
    lines.append(f"Итого: {total} ₽")

    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=services_keyboard(context.user_data.get("delete", False))
    )

# ===== REPORTS =====
async def money_report(c):
    c.execute("""
    SELECT cars.plate, services.qty, services.price
    FROM cars
    JOIN services ON services.car_id = cars.id
    """)
    rows = c.fetchall()

    total = sum(qty * price for _, qty, price in rows)
    return f"Итоги смены\nВсего: {total} ₽"

async def repeat_report(c):
    c.execute("""
    SELECT cars.plate, services.code, services.qty
    FROM cars
    JOIN services ON services.car_id = cars.id
    WHERE services.qty > 1
    """)
    rows = c.fetchall()

    if not rows:
        return "Повторок нет."

    lines = ["Повторки:"]
    for plate, code, qty in rows:
        lines.append(f"{plate} — {SERVICES[code]['name']} ×{qty}")
    return "\n".join(lines)

# ===== APP =====
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(callbacks))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

if __name__ == "__main__":
    app.run_polling()