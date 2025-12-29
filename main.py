import asyncio
import sqlite3
import re
from datetime import datetime, time

from aiogram import Bot, Dispatcher
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup,
    Message, CallbackQuery
)
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# === КОНФИГ ===
API_TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"
OWNER_ID = 8379101989
DB_FILENAME = "shifts.db"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# === FSM ===
class ShiftStates(StatesGroup):
    adding_car = State()
    editing_car = State()

# === БД ===
conn = sqlite3.connect(DB_FILENAME)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT,
    end_time TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER,
    car_number TEXT,
    total INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id INTEGER,
    name TEXT,
    count INTEGER,
    price INTEGER
)
""")

conn.commit()

# === ВРЕМЯ / ТАРИФ ===
DAY_START = time(9, 0)
DAY_END = time(21, 0)

def is_day():
    now = datetime.now().time()
    return DAY_START <= now < DAY_END

# === ПРАЙС ===

SERVICES_GROUP_1 = [
    ("Проверка", 115, 92),
    ("Заправка", 198, 158),
    ("Подкачка", 75, 59),
    ("Заливка омывайки", 66, 55),
    ("Перегон на СТО", 254, 203),
]

SERVICES_GROUP_2 = [
    ("Зарядка АКБ", 125, 98),
    ("Нет спутника", 398, 315),
    ("Развоз до 3 часов", 373, 295),
    ("Развоз до 5 часов", 747, 590),
    ("Срочка", 220, 174),
    ("Завершение аренды", 93, 74),
    ("Проверка ходовой", 115, 92),
    ("Нестандартная операция", 83, 64),
]

SERVICES_GROUP_3 = [
    ("Перепарковка ТС", 150, 118),
    ("Сугроб простой", 160, 128),
    ("Раскладка документов", 31, 25),
    ("Чек", 50, 39),
    ("Перемещение ТС до 20км", 320, 252),
    ("Замена лампочки", 31, 25),
    ("Закрепление ГРЗ", 31, 25),
    ("Установка дворника", 31, 25),
    ("Установка зеркала", 74, 59),
    ("Заправка из канистры", 278, 278),
    ("Долив тех. жидкостей", 77, 66),
    ("Сугроб сложный", 902, 686),
    ("Удаленная заправка", 545, 433),
]

ALL_GROUPS = [SERVICES_GROUP_1, SERVICES_GROUP_2, SERVICES_GROUP_3]
SERVICES_PER_PAGE = 5

# === НОМЕР АВТО ===

TRANSLIT = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X"
})

CAR_REGEX = re.compile(r"^[ABEKMHOPCTYX]\d{3}[ABEKMHOPCTYX]{2}\d{2,3}$")

def normalize_car_number(raw: str):
    s = raw.upper().replace(" ", "")
    s = s.translate(TRANSLIT)

    if re.match(r"^[ABEKMHOPCTYX]\d{3}[ABEKMHOPCTYX]{2}$", s):
        s += "797"

    if not CAR_REGEX.match(s):
        return None

    return s

# === СМЕНЫ ===

def get_active_shift():
    row = cursor.execute(
        "SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None

def open_shift():
    cursor.execute(
        "INSERT INTO shifts (start_time) VALUES (datetime('now','localtime'))"
    )
    conn.commit()

def close_shift(shift_id):
    cursor.execute(
        "UPDATE shifts SET end_time=datetime('now','localtime') WHERE id=?",
        (shift_id,)
    )
    conn.commit()

# === КЛАВИАТУРА ===

def shift_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    if get_active_shift():
        kb.add(KeyboardButton("➕ Добавить машину"))
        kb.add(KeyboardButton("⛔ Закрыть смену"))
    else:
        kb.add(KeyboardButton("🟢 Открыть смену"))

    return kb

# === ХЭНДЛЕРЫ ===

@dp.message(Command("start"))
async def start(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    await msg.answer("Панель управления", reply_markup=shift_keyboard())

@dp.message(Text("🟢 Открыть смену"))
async def open_shift_h(msg: Message):
    if not get_active_shift():
        open_shift()
    await msg.answer("Смена открыта", reply_markup=shift_keyboard())

@dp.message(Text("⛔ Закрыть смену"))
async def close_shift_h(msg: Message):
    shift = get_active_shift()
    if shift:
        close_shift(shift)
    await msg.answer("Смена закрыта", reply_markup=shift_keyboard())

@dp.message(Text("➕ Добавить машину"))
async def add_car(msg: Message, state: FSMContext):
    await state.set_state(ShiftStates.adding_car)
    await msg.answer("Введите номер автомобиля")

@dp.message(ShiftStates.adding_car)
async def car_number(msg: Message, state: FSMContext):
    norm = normalize_car_number(msg.text)
    if not norm:
        await msg.answer("Неверный номер. Введите снова")
        return

    await state.update_data(car=norm, services={})
    await state.set_state(ShiftStates.editing_car)
    await show_services(msg, state, 0, 0)

# === УСЛУГИ ===

async def show_services(target, state, group, page):
    data = await state.get_data()
    services = data["services"]

    group_data = ALL_GROUPS[group]
    start = page * SERVICES_PER_PAGE
    chunk = group_data[start:start + SERVICES_PER_PAGE]

    kb = InlineKeyboardMarkup()

    for name, day, night in chunk:
        price = day if is_day() else night
        count = services.get(name, 0)
        kb.add(
            InlineKeyboardButton(
                text=f"{name} ({price}) [{count}]",
                callback_data=f"svc:{group}:{page}:{name}"
            )
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅", callback_data=f"page:{group}:{page-1}"))
    if start + SERVICES_PER_PAGE < len(group_data):
        nav.append(InlineKeyboardButton("➡", callback_data=f"page:{group}:{page+1}"))
    if nav:
        kb.row(*nav)

    if group + 1 < len(ALL_GROUPS):
        kb.add(InlineKeyboardButton("Следующая группа", callback_data=f"group:{group+1}"))

    kb.add(InlineKeyboardButton("✅ Готово", callback_data="done"))

    await target.answer("Выберите услуги", reply_markup=kb)

@dp.callback_query(Text(startswith="svc:"))
async def svc_add(cb: CallbackQuery, state: FSMContext):
    _, g, p, name = cb.data.split(":")
    data = await state.get_data()
    services = data["services"]
    services[name] = services.get(name, 0) + 1
    await state.update_data(services=services)
    await cb.message.delete()
    await show_services(cb.message, state, int(g), int(p))
    await cb.answer()

@dp.callback_query(Text(startswith="page:"))
async def page(cb: CallbackQuery, state: FSMContext):
    _, g, p = cb.data.split(":")
    await cb.message.delete()
    await show_services(cb.message, state, int(g), int(p))
    await cb.answer()

@dp.callback_query(Text(startswith="group:"))
async def group(cb: CallbackQuery, state: FSMContext):
    _, g = cb.data.split(":")
    await cb.message.delete()
    await show_services(cb.message, state, int(g), 0)
    await cb.answer()

@dp.callback_query(Text("done"))
async def done(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    car = data["car"]
    services = data["services"]

    shift = get_active_shift()
    cursor.execute("INSERT INTO cars (shift_id, car_number, total) VALUES (?,?,0)", (shift, car))
    car_id = cursor.lastrowid

    total = 0
    for name, cnt in services.items():
        price = next(
            (d if is_day() else n for g in ALL_GROUPS for (nme, d, n) in g if nme == name),
            0
        )
        cursor.execute(
            "INSERT INTO services (car_id,name,count,price) VALUES (?,?,?,?)",
            (car_id, name, cnt, price)
        )
        total += cnt * price

    cursor.execute("UPDATE cars SET total=? WHERE id=?", (total, car_id))
    conn.commit()

    await state.clear()
    await cb.message.answer(f"{car} сохранена\nИтого: {total}", reply_markup=shift_keyboard())
    await cb.answer()

if __name__ == "__main__":
    dp.run_polling(bot)