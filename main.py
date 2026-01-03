import asyncio
import sqlite3
import os
import re
import logging
from datetime import datetime, time

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ======================
# ЛОГИ
# ======================

logging.basicConfig(level=logging.INFO)

# ======================
# КОНФИГ
# ======================

BOT_TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"
OWNER_ID = 8379101989

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "shifts.db")

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
    add_car_number = State()
    edit_services = State()

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
    "tow": {"name": "Перегон на СТО", "day": 254, "night": 210},
}

# ======================
# УТИЛИТЫ
# ======================

CYR_TO_LAT = {
    "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X"
}

def normalize_car_number(raw: str) -> str | None:
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

def get_tariff() -> str:
    now = datetime.now().time()
    return "day" if DAY_START <= now < DAY_END else "night"

def get_active_shift() -> int | None:
    cursor.execute(
        "SELECT id FROM shifts WHERE archived = 0 ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    return row[0] if row else None

# ======================
# КЛАВИАТУРЫ
# ======================

def get_main_menu(active: bool) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if active:
        kb.add(KeyboardButton("➕ Добавить машину"))
        kb.add(KeyboardButton("📊 Итоги смены"))
        kb.add(KeyboardButton("⛔ Закрыть смену"))
    else:
        kb.add(KeyboardButton("Открыть смену"))
    return kb

def services_keyboard(selected: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, svc in SERVICES.items():
        count = selected.get(key, 0)
        kb.button(
            text=f"{svc['name']} ({count})",
            callback_data=f"svc|{key}"
        )
    kb.button(text="✅ Готово", callback_data="done")
    kb.adjust(1)
    return kb.as_markup()

# ======================
# СТАРТ
# ======================

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.clear()
    active = bool(get_active_shift())
    await message.answer(
        "Панель управления сменой",
        reply_markup=get_main_menu(active)
    )

# ======================
# ОТКРЫТИЕ СМЕНЫ
# ======================

@dp.message(F.text == "Открыть смену")
async def open_shift(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    if get_active_shift():
        await message.answer("Смена уже открыта")
        return

    cursor.execute(
        "INSERT INTO shifts (start_dt) VALUES (?)",
        (datetime.now().isoformat(),)
    )
    conn.commit()

    await message.answer(
        "✅ Смена открыта",
        reply_markup=get_main_menu(True)
    )
# ======================
# ДОБАВЛЕНИЕ МАШИНЫ
# ======================

@dp.message(F.text == "➕ Добавить машину")
async def add_car_start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    if not get_active_shift():
        await message.answer("❌ Сначала открой смену")
        return

    await state.set_state(ShiftFSM.add_car_number)
    await message.answer(
        "Введи номер машины (можно кириллицей, без региона)",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(ShiftFSM.add_car_number)
async def add_car_number(message: Message, state: FSMContext):
    car_number = normalize_car_number(message.text)
    if not car_number:
        await message.answer("❌ Неверный номер, попробуй ещё раз")
        return

    await state.update_data(
        car_number=car_number,
        services={}
    )
    await state.set_state(ShiftFSM.edit_services)

    await message.answer(
        f"🚗 Машина {car_number}\nВыбери услуги:",
        reply_markup=services_keyboard({})
    )

# ======================
# ВЫБОР УСЛУГ
# ======================

@dp.callback_query(F.data.startswith("svc|"), ShiftFSM.edit_services)
async def select_service(call: CallbackQuery, state: FSMContext):
    key = call.data.split("|")[1]

    data = await state.get_data()
    services = data.get("services", {})

    services[key] = services.get(key, 0) + 1

    await state.update_data(services=services)

    await call.message.edit_reply_markup(
        reply_markup=services_keyboard(services)
    )
    await call.answer(
        f"{SERVICES[key]['name']}: {services[key]}"
    )

# ======================
# СОХРАНЕНИЕ МАШИНЫ
# ======================

@dp.callback_query(F.data == "done", ShiftFSM.edit_services)
async def save_car(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    car_number = data["car_number"]
    services = data["services"]

    shift_id = get_active_shift()
    if not shift_id:
        await call.message.answer("❌ Смена не активна")
        await state.clear()
        await call.answer()
        return

    tariff = get_tariff()

    cursor.execute(
        "INSERT INTO cars (shift_id, car_number, total_sum) VALUES (?, ?, 0)",
        (shift_id, car_number)
    )
    car_id = cursor.lastrowid

    total = 0

    for key, count in services.items():
        if count <= 0:
            continue

        svc = SERVICES[key]
        price = svc[tariff]

        cursor.execute(
            """
            INSERT INTO services
            (car_id, service_key, service_name, count, price)
            VALUES (?, ?, ?, ?, ?)
            """,
            (car_id, key, svc["name"], count, price)
        )

        total += price * count

    cursor.execute(
        "UPDATE cars SET total_sum = ? WHERE id = ?",
        (total, car_id)
    )

    conn.commit()

    await state.clear()

    await call.message.answer(
        f"✅ Машина сохранена\n"
        f"{car_number}\n"
        f"Итого: {total} ₽",
        reply_markup=get_main_menu(True)
    )
    await call.answer()

# ======================
# ПРОМЕЖУТОЧНЫЕ ИТОГИ
# ======================

@dp.message(F.text == "📊 Итоги смены")
async def interim_report(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("❌ Смена не открыта")
        return

    cursor.execute(
        "SELECT car_number, total_sum FROM cars WHERE shift_id = ?",
        (shift_id,)
    )
    rows = cursor.fetchall()

    if not rows:
        await message.answer("Машин пока нет")
        return

    total = 0
    text = "📊 Итоги смены:\n\n"

    for car, s in rows:
        text += f"🚗 {car}: {s} ₽\n"
        total += s

    text += f"\n💰 ИТОГО: {total} ₽"

    await message.answer(text)
# ======================
# ЗАКРЫТИЕ СМЕНЫ (ЛОГИКА)
# ======================

def close_shift_logic(shift_id: int) -> int:
    cursor.execute(
        "SELECT total_sum FROM cars WHERE shift_id = ?",
        (shift_id,)
    )
    rows = cursor.fetchall()

    total = sum(r[0] for r in rows if r[0])

    cursor.execute(
        """
        UPDATE shifts
        SET archived = 1,
            end_dt = ?,
            total_sum = ?
        WHERE id = ?
        """,
        (datetime.now().isoformat(), total, shift_id)
    )
    conn.commit()
    return total

# ======================
# ЗАКРЫТИЕ СМЕНЫ (ХЭНДЛЕР)
# ======================

@dp.message(F.text == "⛔ Закрыть смену")
async def close_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("❌ Нет активной смены")
        return

    total = close_shift_logic(shift_id)

    await message.answer(
        f"⛔ Смена закрыта\n"
        f"💰 Итого за смену: {total} ₽",
        reply_markup=get_main_menu(False)
    )

# ======================
# ОТМЕНА FSM
# ======================

@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Действие отменено",
        reply_markup=get_main_menu(bool(get_active_shift()))
    )

# ======================
# FALLBACK (НЕИЗВЕСТНЫЕ СООБЩЕНИЯ)
# ======================

@dp.message()
async def fallback_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    await message.answer(
        "Используй кнопки меню",
        reply_markup=get_main_menu(bool(get_active_shift()))
    )

# ======================
# ЗАПУСК БОТА
# ======================

async def main():
    logging.info("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
