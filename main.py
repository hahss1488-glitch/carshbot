import asyncio
import sqlite3
import os
import re
from datetime import datetime, time

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardButton, ReplyKeyboardRemove
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ======================
# КОНФИГ
# ======================

BOT_TOKEN = os.getenv("8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c")
OWNER_ID = 8379101989

DB_FILE = "shifts.db"
DEFAULT_REGION = "797"

DAY_START = time(9, 0)
DAY_END = time(21, 0)

# ======================
# БОТ
# ======================

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ======================
# FSM
# ======================

class ShiftFSM(StatesGroup):
    add_car = State()
    edit_services = State()
    backdate_date = State()

class HistoryFSM(StatesGroup):
    browsing = State()

# ======================
# БАЗА ДАННЫХ
# ======================

conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_dt TEXT,
    end_dt TEXT,
    total_sum INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER,
    car_number TEXT,
    total_sum INTEGER
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id INTEGER,
    service_key TEXT,
    service_name TEXT,
    count INTEGER,
    price INTEGER
);
""")
conn.commit()

# ======================
# ПРАЙС
# ======================

SERVICES = {
    "check": {"name": "Проверка", "day": 115, "night": 92},
    "fuel": {"name": "Заправка", "day": 198, "night": 165},
    "pump": {"name": "Подкачка", "day": 75, "night": 60},
    "washer": {"name": "Омывайка", "day": 66, "night": 55},
    "tow": {"name": "СТО", "day": 254, "night": 210},
}

# ======================
# УТИЛИТЫ
# ======================

CYR_TO_LAT = {
    "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X"
}

def normalize_car_number(raw: str):
    raw = raw.upper().replace(" ", "")
    result = ""

    for ch in raw:
        if ch.isdigit():
            result += ch
        elif ch in CYR_TO_LAT:
            result += CYR_TO_LAT[ch]
        elif "A" <= ch <= "Z":
            result += ch
        else:
            return None

    if len(result) in (5, 6):
        result += DEFAULT_REGION

    if not re.fullmatch(r"[A-Z]\d{3}[A-Z]{2}\d{3}", result):
        return None

    return result

def get_tariff():
    now = datetime.now().time()
    return "day" if DAY_START <= now < DAY_END else "night"

def get_active_shift():
    cursor.execute("SELECT id FROM shifts WHERE archived=0 ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None

# ======================
# КЛАВИАТУРЫ
# ======================

def main_menu(active):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if active:
        kb.add(KeyboardButton("➕ Добавить машину"))
        kb.add(KeyboardButton("📊 Итоги смены"))
        kb.add(KeyboardButton("⛔ Закрыть смену"))
    else:
        kb.add(KeyboardButton("Открыть смену"))
    return kb

def services_kb(page, selected):
    kb = InlineKeyboardBuilder()
    items = list(SERVICES.items())[page*5:(page+1)*5]

    for key, svc in items:
        cnt = selected.get(key, 0)
        kb.button(text=f"{svc['name']} ({cnt})", callback_data=f"svc|{key}")

    kb.button(text="✅ Готово", callback_data="done")
    kb.adjust(1)
    return kb.as_markup()

# ======================
# СТАРТ
# ======================

@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.clear()
    await message.answer("Панель", reply_markup=main_menu(bool(get_active_shift())))

# ======================
# СМЕНА
# ======================

@dp.message(F.text == "Открыть смену")
async def open_shift(message: Message):
    if get_active_shift():
        await message.answer("Смена уже открыта")
        return
    cursor.execute(
        "INSERT INTO shifts (start_dt) VALUES (?)",
        (datetime.now().isoformat(),)
    )
    conn.commit()
    await message.answer("Смена открыта", reply_markup=main_menu(True))

# ======================
# ДОБАВЛЕНИЕ МАШИНЫ
# ======================

@dp.message(F.text == "➕ Добавить машину")
async def add_car(message: Message, state: FSMContext):
    if not get_active_shift():
        await message.answer("Нет активной смены")
        return
    await state.set_state(ShiftFSM.add_car)
    await message.answer("Введите номер машины", reply_markup=ReplyKeyboardRemove())

@dp.message(ShiftFSM.add_car)
async def car_number(message: Message, state: FSMContext):
    num = normalize_car_number(message.text)
    if not num:
        await message.answer("Неверный номер")
        return

    await state.update_data(car=num, services={})
    await state.set_state(ShiftFSM.edit_services)
    await message.answer(
        f"🚗 {num}",
        reply_markup=services_kb(0, {})
    )

# ======================
# УСЛУГИ
# ======================

@dp.callback_query(F.data.startswith("svc|"), ShiftFSM.edit_services)
async def service_add(call: CallbackQuery, state: FSMContext):
    key = call.data.split("|")[1]
    data = await state.get_data()
    services = data["services"]
    services[key] = services.get(key, 0) + 1
    await state.update_data(services=services)
    await call.message.edit_reply_markup(services_kb(0, services))
    await call.answer()

@dp.callback_query(F.data == "done", ShiftFSM.edit_services)
async def save_car(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    shift_id = get_active_shift()
    tariff = get_tariff()

    cursor.execute(
        "INSERT INTO cars (shift_id, car_number, total_sum) VALUES (?, ?, 0)",
        (shift_id, data["car"])
    )
    car_id = cursor.lastrowid

    total = 0
    for k, c in data["services"].items():
        price = SERVICES[k][tariff]
        cursor.execute(
            "INSERT INTO services (car_id, service_key, service_name, count, price) VALUES (?, ?, ?, ?, ?)",
            (car_id, k, SERVICES[k]["name"], c, price)
        )
        total += price * c

    cursor.execute("UPDATE cars SET total_sum=? WHERE id=?", (total, car_id))
    conn.commit()

    await state.clear()
    await call.message.answer(f"✅ Сохранено\nИтого: {total} ₽", reply_markup=main_menu(True))
    await call.answer()

# ======================
# ЗАКРЫТИЕ СМЕНЫ
# ======================

def close_shift_logic(shift_id):
    cursor.execute("SELECT total_sum FROM cars WHERE shift_id=?", (shift_id,))
    total = sum(x[0] for x in cursor.fetchall())
    cursor.execute(
        "UPDATE shifts SET archived=1, end_dt=?, total_sum=? WHERE id=?",
        (datetime.now().isoformat(), total, shift_id)
    )
    conn.commit()
    return total

@dp.message(F.text == "⛔ Закрыть смену")
async def close_shift(message: Message):
    sid = get_active_shift()
    if not sid:
        await message.answer("Нет смены")
        return
    total = close_shift_logic(sid)
    await message.answer(f"Смена закрыта\nИтого: {total} ₽", reply_markup=main_menu(False))

# ======================
# ЗАПУСК
# ======================

async def main():
    print("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())