import asyncio
import sqlite3
import re
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.filters import Command, Text, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ======================================================
# КОНФИГУРАЦИЯ
# ======================================================
API_TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"
OWNER_ID = 8379101989
DB_FILENAME = "shifts.db"

DAY_START = 9
DAY_END = 21
DEFAULT_REGION = "797"

# ======================================================
# ИНИЦИАЛИЗАЦИЯ
# ======================================================
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ======================================================
# FSM СОСТОЯНИЯ
# ======================================================
class ShiftStates(StatesGroup):
    adding_car = State()
    editing_car = State()

class HistoryStates(StatesGroup):
    browsing = State()

# ======================================================
# БАЗА ДАННЫХ
# ======================================================
conn = sqlite3.connect(DB_FILENAME)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT,
    end_time TEXT,
    total_sum INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER,
    car_number TEXT,
    sum INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id INTEGER,
    name TEXT,
    count INTEGER,
    price INTEGER,
    is_night INTEGER
)
""")

conn.commit()

# ======================================================
# УСЛУГИ (НЕ УРЕЗАНЫ)
# ======================================================
SERVICES = [
    ("Проверка", 115), ("Заправка", 198), ("Подкачка", 75),
    ("Заливка омывайки", 66), ("Перегон на СТО", 254),

    ("Зарядка АКБ", 125), ("Нет спутника", 398),
    ("Развоз до 3 часов", 373), ("Развоз до 5 часов", 747),
    ("Срочка", 220), ("Завершение аренды", 93),
    ("Проверка ходовой", 115), ("Нестандартная операция", 83),

    ("Перепарковка ТС", 150), ("Сугроб простой", 160),
    ("Раскладка документов", 31), ("Чек", 50),
    ("Перемещение ТС до 20км", 320),

    ("Замена лампочки", 31), ("Закрепление ГРЗ", 31),
    ("Установка дворника", 31), ("Установка зеркала", 74),
    ("Заправка из канистры", 278),
    ("Долив тех. жидкостей", 77),

    ("Сугроб сложный", 902),
    ("Удаленная заправка", 545),
]

SERVICES_PER_PAGE = 5

# ======================================================
# НОРМАЛИЗАЦИЯ НОМЕРОВ ТС (КРИТИЧЕСКОЕ ОБНОВЛЕНИЕ)
# ======================================================
CYR_TO_LAT = {
    "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X"
}

def normalize_car_number(raw: str) -> str | None:
    raw = raw.upper().replace(" ", "")

    result = ""
    for ch in raw:
        if ch in CYR_TO_LAT:
            result += CYR_TO_LAT[ch]
        else:
            result += ch

    match = re.match(r"^([A-Z])(\d{3})([A-Z]{2})(\d{2,3})?$", result)
    if not match:
        return None

    letter, digits, letters, region = match.groups()
    if not region:
        region = DEFAULT_REGION

    return f"{letter}{digits}{letters}{region}"

# ======================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ======================================================
def get_active_shift():
    cursor.execute("SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None

def open_shift():
    cursor.execute(
        "INSERT INTO shifts (start_time) VALUES (?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
    )
    conn.commit()
    return cursor.lastrowid

def close_shift(shift_id: int) -> int:
    cursor.execute("""
        SELECT SUM(services.price * services.count)
        FROM services
        JOIN cars ON cars.id = services.car_id
        WHERE cars.shift_id = ?
    """, (shift_id,))
    total = cursor.fetchone()[0] or 0

    cursor.execute("""
        UPDATE shifts
        SET end_time = ?, total_sum = ?
        WHERE id = ?
    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), total, shift_id))

    conn.commit()
    return total

def is_night_time() -> int:
    hour = datetime.now().hour
    return int(hour < DAY_START or hour >= DAY_END)

# ======================================================
# КЛАВИАТУРА (ФИКС ПРОПАДАНИЯ)
# ======================================================
def get_shift_panel(active: bool):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if active:
        kb.add(KeyboardButton("➕ Добавить машину"))
        kb.add(
            KeyboardButton("📊 Итоги смены"),
            KeyboardButton("⏱ Информация о смене")
        )
        kb.add(KeyboardButton("📜 История смен"))
        kb.add(KeyboardButton("⛔ Закрыть смену"))
    else:
        kb.add(KeyboardButton("Открыть смену"))
    return kb

# ======================================================
# СТАРТ / МЕНЮ
# ======================================================
@dp.message(Command("start"))
@dp.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await message.answer("Нет доступа.")
        return

    await state.clear()
    active = bool(get_active_shift())

    text = (
        "Смена открыта. Панель смены."
        if active else
        "Добро пожаловать. Смена не открыта."
    )

    await message.answer(text, reply_markup=get_shift_panel(active))

# ======================================================
# ОТКРЫТИЕ / ЗАКРЫТИЕ СМЕНЫ
# ======================================================
@dp.message(Text("Открыть смену"))
async def open_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    if get_active_shift():
        await message.answer("Смена уже открыта.", reply_markup=get_shift_panel(True))
        return

    open_shift()
    await message.answer("Смена открыта.", reply_markup=get_shift_panel(True))

@dp.message(Text("⛔ Закрыть смену"))
async def close_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel(False))
        return

    total = close_shift(shift_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Денежный отчёт", callback_data="report_money"),
            InlineKeyboardButton(text="🔁 Отчёт повторок", callback_data="report_repeat")
        ]
    ])

    await message.answer(
        f"Смена закрыта.\nИтого: {total} ₽",
        reply_markup=kb
    )
    await message.answer("Меню:", reply_markup=get_shift_panel(False))

# ======================================================
# ЗАГЛУШКА (ПРОДОЛЖЕНИЕ ДАЛЬШЕ)
# ======================================================
print("PART 1 LOADED")
# ======================================================
# ДОБАВЛЕНИЕ МАШИНЫ — СТАРТ
# ======================================================
@dp.message(Text("➕ Добавить машину"))
async def add_car_start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    if not get_active_shift():
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel(False))
        return

    await state.clear()
    await state.set_state(ShiftStates.adding_car)

    await message.answer(
        "Введите номер машины\n"
        "Примеры:\n"
        "• Х360РУ\n"
        "• X360PY797",
        reply_markup=get_shift_panel(True)
    )

# ======================================================
# ВВОД НОМЕРА МАШИНЫ (КИРИЛЛИЦА + АВТОРЕГИОН)
# ======================================================
@dp.message(StateFilter(ShiftStates.adding_car))
async def add_car_number(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    normalized = normalize_car_number(message.text)

    if not normalized:
        await message.answer(
            "❌ Неверный формат номера.\n"
            "Пример: X360PY797 или Х360РУ",
            reply_markup=get_shift_panel(True)
        )
        return

    await state.update_data(
        car_number=normalized,
        services={},
        delete_mode=False,
        page=0
    )

    await state.set_state(ShiftStates.editing_car)
    await show_services_page(message, state)

# ======================================================
# ПОКАЗ СТРАНИЦЫ УСЛУГ (БЕЗ ПРОПАДАНИЯ КЛАВИАТУРЫ)
# ======================================================
async def show_services_page(target, state: FSMContext):
    data = await state.get_data()
    car_number = data["car_number"]
    services = data["services"]
    delete_mode = data["delete_mode"]
    page = data["page"]

    start = page * SERVICES_PER_PAGE
    end = start + SERVICES_PER_PAGE
    chunk = SERVICES[start:end]

    title = (
        f"🚗 Машина: {car_number}\n"
        f"{'🗑 РЕЖИМ УДАЛЕНИЯ' if delete_mode else '➕ Добавление услуг'}"
    )

    kb = InlineKeyboardMarkup(row_width=2)

    for name, price in chunk:
        count = services.get(name, 0)
        label = f"{name} [{count}]" if count else name
        kb.insert(
            InlineKeyboardButton(
                text=label,
                callback_data=f"svc|{name}"
            )
        )

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data="page_prev"))
    if end < len(SERVICES):
        nav.append(InlineKeyboardButton("➡️", callback_data="page_next"))
    if nav:
        kb.row(*nav)

    kb.row(
        InlineKeyboardButton(
            "🗑 Удалить услугу" if not delete_mode else "❌ Выход из удаления",
            callback_data="toggle_delete"
        )
    )
    kb.row(
        InlineKeyboardButton("✅ Готово", callback_data="finish_car"),
        InlineKeyboardButton("↩ Отмена", callback_data="cancel_car")
    )

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(title, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(title, reply_markup=kb)

# ======================================================
# ПЕРЕКЛЮЧЕНИЕ СТРАНИЦ
# ======================================================
@dp.callback_query(Text("page_next"), StateFilter(ShiftStates.editing_car))
async def page_next(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(page=data["page"] + 1)
    await show_services_page(callback, state)

@dp.callback_query(Text("page_prev"), StateFilter(ShiftStates.editing_car))
async def page_prev(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(page=max(0, data["page"] - 1))
    await show_services_page(callback, state)

# ======================================================
# ВКЛ / ВЫКЛ РЕЖИМ УДАЛЕНИЯ
# ======================================================
@dp.callback_query(Text("toggle_delete"), StateFilter(ShiftStates.editing_car))
async def toggle_delete(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(delete_mode=not data["delete_mode"])
    await show_services_page(callback, state)

# ======================================================
# НАЖАТИЕ НА УСЛУГУ
# ======================================================
@dp.callback_query(Text(startswith="svc|"), StateFilter(ShiftStates.editing_car))
async def service_click(callback: CallbackQuery, state: FSMContext):
    service_name = callback.data.split("|", 1)[1]

    data = await state.get_data()
    services = data["services"]
    delete_mode = data["delete_mode"]

    count = services.get(service_name, 0)
    services[service_name] = max(0, count - 1) if delete_mode else count + 1

    await state.update_data(services=services)
    await show_services_page(callback, state)

# ======================================================
# ОТМЕНА ДОБАВЛЕНИЯ МАШИНЫ
# ======================================================
@dp.callback_query(Text("cancel_car"), StateFilter(ShiftStates.editing_car))
async def cancel_car(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "Добавление машины отменено.",
        reply_markup=get_shift_panel(True)
    )
    await callback.answer()

# ======================================================
# СОХРАНЕНИЕ МАШИНЫ
# ======================================================
@dp.callback_query(Text("finish_car"), StateFilter(ShiftStates.editing_car))
async def finish_car(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    car_number = data["car_number"]
    services = data["services"]
    shift_id = get_active_shift()
    night = is_night_time()

    cursor.execute(
        "INSERT INTO cars (shift_id, car_number, sum) VALUES (?, ?, 0)",
        (shift_id, car_number)
    )
    car_id = cursor.lastrowid

    total = 0
    lines = []

    for name, count in services.items():
        if count <= 0:
            continue
        price = dict(SERVICES)[name]
        cursor.execute(
            """
            INSERT INTO services (car_id, name, count, price, is_night)
            VALUES (?, ?, ?, ?, ?)
            """,
            (car_id, name, count, price, night)
        )
        total += price * count
        lines.append(f"{name} ×{count}")

    cursor.execute(
        "UPDATE cars SET sum = ? WHERE id = ?",
        (total, car_id)
    )
    conn.commit()

    await state.clear()

    await callback.message.answer(
        f"✅ Машина сохранена\n"
        f"{car_number}\n"
        f"Услуги: {', '.join(lines) if lines else '—'}\n"
        f"Итого: {total} ₽"
    )

    await callback.message.answer(
        "Панель смены:",
        reply_markup=get_shift_panel(True)
    )
    await callback.answer()

print("PART 2 LOADED")
# ======================================================
# ИТОГИ СМЕНЫ
# ======================================================
@dp.message(Text("📊 Итоги смены"))
async def shift_summary(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.")
        return

    cursor.execute(
        "SELECT COUNT(*), COALESCE(SUM(sum),0) FROM cars WHERE shift_id = ?",
        (shift_id,)
    )
    cars_count, total_sum = cursor.fetchone()

    cursor.execute(
        """
        SELECT name, SUM(count), SUM(count * price)
        FROM services
        WHERE car_id IN (
            SELECT id FROM cars WHERE shift_id = ?
        )
        GROUP BY name
        ORDER BY SUM(count * price) DESC
        """,
        (shift_id,)
    )
    services = cursor.fetchall()

    text = [
        "📊 Итоги смены",
        f"🚗 Машин: {cars_count}",
        f"💰 Выручка: {total_sum} ₽",
        "",
        "Услуги:"
    ]

    if services:
        for name, cnt, money in services:
            text.append(f"• {name}: {cnt} шт / {money} ₽")
    else:
        text.append("— нет услуг")

    await message.answer("\n".join(text), reply_markup=get_shift_panel(True))


# ======================================================
# ЗАКРЫТИЕ СМЕНЫ
# ======================================================
@dp.message(Text("🔒 Закрыть смену"))
async def close_shift(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена уже закрыта.")
        return

    cursor.execute(
        "SELECT COALESCE(SUM(sum),0) FROM cars WHERE shift_id = ?",
        (shift_id,)
    )
    total = cursor.fetchone()[0]

    cursor.execute(
        "UPDATE shifts SET end_time = CURRENT_TIMESTAMP, total = ? WHERE id = ?",
        (total, shift_id)
    )
    conn.commit()

    await message.answer(
        f"🔒 Смена закрыта\n"
        f"💰 Итого: {total} ₽",
        reply_markup=get_main_menu()
    )


# ======================================================
# ИСТОРИЯ СМЕН
# ======================================================
@dp.message(Text("📜 История смен"))
async def shift_history(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    cursor.execute(
        """
        SELECT id, start_time, end_time, total
        FROM shifts
        ORDER BY id DESC
        LIMIT 10
        """
    )
    shifts = cursor.fetchall()

    if not shifts:
        await message.answer("История пуста.")
        return

    kb = InlineKeyboardMarkup()
    for sid, start, end, total in shifts:
        label = f"#{sid} | {start[:16]} | {total or 0} ₽"
        kb.add(
            InlineKeyboardButton(
                label,
                callback_data=f"shift_view|{sid}"
            )
        )

    await message.answer("📜 История смен:", reply_markup=kb)


# ======================================================
# ПРОСМОТР КОНКРЕТНОЙ СМЕНЫ
# ======================================================
@dp.callback_query(Text(startswith="shift_view|"))
async def view_shift(callback: CallbackQuery):
    shift_id = int(callback.data.split("|")[1])

    cursor.execute(
        """
        SELECT start_time, end_time, total
        FROM shifts WHERE id = ?
        """,
        (shift_id,)
    )
    shift = cursor.fetchone()

    cursor.execute(
        "SELECT car_number, sum FROM cars WHERE shift_id = ?",
        (shift_id,)
    )
    cars = cursor.fetchall()

    text = [
        f"📜 Смена #{shift_id}",
        f"🕒 Начало: {shift[0][:16]}",
        f"🕓 Конец: {shift[1][:16] if shift[1] else '—'}",
        f"💰 Итого: {shift[2] or 0} ₽",
        "",
        "🚗 Машины:"
    ]

    if cars:
        for num, s in cars:
            text.append(f"• {num}: {s} ₽")
    else:
        text.append("— нет машин")

    await callback.message.answer("\n".join(text))
    await callback.answer()


# ======================================================
# ОТЧЁТ ПО ПОВТОРЯЮЩИМСЯ МАШИНАМ
# ======================================================
@dp.message(Text("🔁 Повторки"))
async def repeated_cars(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    cursor.execute(
        """
        SELECT car_number, COUNT(*) as cnt, SUM(sum)
        FROM cars
        GROUP BY car_number
        HAVING cnt > 1
        ORDER BY cnt DESC
        """
    )
    rows = cursor.fetchall()

    if not rows:
        await message.answer("Повторяющихся машин нет.")
        return

    text = ["🔁 Повторяющиеся машины:"]
    for num, cnt, total in rows:
        text.append(f"• {num}: {cnt} раз / {total} ₽")

    await message.answer("\n".join(text))


# ======================================================
# FALLBACK (ЧТОБ НИЧЕГО НЕ ЛОМАЛОСЬ)
# ======================================================
@dp.message()
async def fallback(message: Message):
    await message.answer(
        "Используй кнопки меню.",
        reply_markup=get_main_menu() if not get_active_shift() else get_shift_panel(True)
    )


# ======================================================
# ЗАПУСК БОТА
# ======================================================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    print("BOT STARTED")
    asyncio.run(main())
