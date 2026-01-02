import asyncio
import sqlite3
import re
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ======================
# КОНФИГ
# ======================

API_TOKEN = os.getenv("BOT_TOKEN") or "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"
OWNER_ID = 8379101989
DB_FILENAME = "shifts.db"
DEFAULT_REGION = "797"

# ======================
# БОТ
# ======================

bot = Bot(API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ======================
# FSM
# ======================

class ShiftStates(StatesGroup):
    adding_car = State()
    editing_car = State()

class HistoryStates(StatesGroup):
    browsing = State()

# ======================
# БАЗА ДАННЫХ
# ======================

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
    price INTEGER
)
""")

conn.commit()

# ======================
# УСЛУГИ (ПОЛНЫЙ ПРАЙС)
# ======================

SERVICES = [
    ("Проверка", 115),
    ("Заправка", 198),
    ("Подкачка", 75),
    ("Заливка омывайки", 66),
    ("Перегон на СТО", 254),

    ("Зарядка АКБ", 125),
    ("Нет спутника", 398),
    ("Развоз до 3 часов", 373),
    ("Развоз до 5 часов", 747),
    ("Срочка", 220),
    ("Завершение аренды", 93),
    ("Проверка ходовой", 115),
    ("Нестандартная операция", 83),

    ("Перепарковка ТС", 150),
    ("Сугроб простой", 160),
    ("Раскладка документов", 31),
    ("Чек", 50),
    ("Перемещение ТС до 20км", 320),
    ("Замена лампочки", 31),
    ("Закрепление ГРЗ", 31),
    ("Установка дворника", 31),
    ("Установка зеркала", 74),
    ("Заправка из канистры", 278),
    ("Долив тех. жидкостей", 77),
    ("Сугроб сложный", 902),
    ("Удаленная заправка", 545),
]

SERVICES_PER_PAGE = 5

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

    if not re.match(r"^[A-Z]\d{3}[A-Z]{2}\d{3}$", result):
        return None

    return result

def get_active_shift():
    cursor.execute(
        "SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    return row[0] if row else None

def open_shift():
    cursor.execute(
        "INSERT INTO shifts (start_time) VALUES (?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
    )
    conn.commit()

def close_shift_logic(shift_id: int) -> int:
    cursor.execute("""
        SELECT SUM(price * count)
        FROM services
        JOIN cars ON services.car_id = cars.id
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
# ======================
# КЛАВИАТУРЫ
# ======================

def get_main_menu(active_shift: bool):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if active_shift:
        kb.add(KeyboardButton("➕ Добавить машину"))
        kb.add(KeyboardButton("📊 Итоги смены"), KeyboardButton("⏱ Информация о смене"))
        kb.add(KeyboardButton("📜 История смен"))
        kb.add(KeyboardButton("⛔ Закрыть смену"))
    else:
        kb.add(KeyboardButton("Открыть смену"))
    return kb

def services_keyboard(page: int, selected: dict, delete_mode: bool):
    kb = InlineKeyboardBuilder()
    start = page * SERVICES_PER_PAGE
    end = start + SERVICES_PER_PAGE
    chunk = SERVICES[start:end]

    for name, price in chunk:
        cnt = selected.get(name, 0)
        label = f"{name} ({cnt})"
        kb.button(text=label, callback_data=f"svc|{page}|{name}")

    # Навигация страниц
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    if end < len(SERVICES):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    if nav:
        kb.row(*nav)

    kb.row(
        InlineKeyboardButton(
            "🗑 Удаление: ВКЛ" if delete_mode else "🗑 Удаление: ВЫКЛ",
            callback_data="toggle_delete"
        )
    )

    kb.row(
        InlineKeyboardButton("✅ Готово", callback_data="done")
    )

    return kb.as_markup()

# ======================
# СТАРТ / МЕНЮ
# ======================

@dp.message(F.text.in_({"start", "menu"}))
async def start_cmd(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.clear()
    active = bool(get_active_shift())
    await message.answer("Панель смены", reply_markup=get_main_menu(active))

# ======================
# ОТКРЫТИЕ СМЕНЫ
# ======================

@dp.message(F.text == "Открыть смену")
async def open_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if get_active_shift():
        await message.answer("Смена уже открыта")
        return
    open_shift()
    await message.answer("Смена открыта", reply_markup=get_main_menu(True))

# ======================
# ЗАКРЫТИЕ СМЕНЫ
# ======================

@dp.message(F.text == "⛔ Закрыть смену")
async def close_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Нет активной смены")
        return
    total = close_shift_logic(shift_id)

    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Денежный отчёт", callback_data="report_money")
    kb.button(text="🔁 Повторки", callback_data="report_repeat")

    await message.answer(
        f"Смена закрыта\nИтого: {total} ₽",
        reply_markup=kb.as_markup()
    )
# ======================
# ДОБАВЛЕНИЕ МАШИНЫ
# ======================

@dp.message(F.text == "➕ Добавить машину")
async def add_car_start(message: Message, state: FSMContext):
    if not get_active_shift():
        await message.answer("Сначала открой смену")
        return
    await state.set_state(ShiftStates.adding_car)
    await message.answer(
        "Введи номер ТС (можно кириллицей, без региона)",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(ShiftStates.adding_car)
async def add_car_number(message: Message, state: FSMContext):
    normalized = normalize_car_number(message.text)
    if not normalized:
        await message.answer("❌ Неверный номер, попробуй ещё раз")
        return
    await state.update_data(
        car_number=normalized,
        services={},
        page=0,
        delete_mode=False
    )
    await state.set_state(ShiftStates.editing_car)
    await message.answer(
        f"🚗 Машина {normalized}\nВыбери услуги:",
        reply_markup=services_keyboard(0, {}, False)
    )

# ======================
# НАВИГАЦИЯ УСЛУГ
# ======================

@dp.callback_query(F.data.startswith("page|"), ShiftStates.editing_car)
async def change_page(call: CallbackQuery, state: FSMContext):
    page = int(call.data.split("|")[1])
    data = await state.get_data()
    await state.update_data(page=page)
    await call.message.edit_reply_markup(
        reply_markup=services_keyboard(page, data["services"], data["delete_mode"])
    )
    await call.answer()

# ======================
# ПЕРЕКЛЮЧЕНИЕ УДАЛЕНИЯ
# ======================

@dp.callback_query(F.data == "toggle_delete", ShiftStates.editing_car)
async def toggle_delete(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    delete_mode = not data["delete_mode"]
    await state.update_data(delete_mode=delete_mode)
    await call.message.edit_reply_markup(
        reply_markup=services_keyboard(data["page"], data["services"], delete_mode)
    )
    await call.answer()

# ======================
# ВЫБОР УСЛУГИ
# ======================

@dp.callback_query(F.data.startswith("svc|"), ShiftStates.editing_car)
async def select_service(call: CallbackQuery, state: FSMContext):
    _, page, name = call.data.split("|")
    page = int(page)
    data = await state.get_data()
    services = data["services"]
    delete_mode = data["delete_mode"]
    count = services.get(name, 0)
    services[name] = max(0, count - 1) if delete_mode else count + 1
    await state.update_data(services=services)
    await call.message.edit_reply_markup(
        reply_markup=services_keyboard(page, services, delete_mode)
    )
    await call.answer(f"{name}: {services[name]}")

# ======================
# СОХРАНЕНИЕ МАШИНЫ
# ======================

@dp.callback_query(F.data == "done", ShiftStates.editing_car)
async def save_car(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    car_number = data["car_number"]
    services = data["services"]
    shift_id = get_active_shift()

    cursor.execute(
        "INSERT INTO cars (shift_id, car_number, sum) VALUES (?, ?, 0)",
        (shift_id, car_number)
    )
    car_id = cursor.lastrowid

    total = 0
    service_dict = dict(SERVICES)
    for name, count in services.items():
        if count > 0:
            price = service_dict[name]
            cursor.execute(
                "INSERT INTO services (car_id, name, count, price) VALUES (?, ?, ?, ?)",
                (car_id, name, count, price)
            )
            total += price * count

    cursor.execute("UPDATE cars SET sum = ? WHERE id = ?", (total, car_id))
    conn.commit()
    await state.clear()
    await call.message.answer(
        f"✅ Машина сохранена\n{car_number}\nИтого: {total} ₽",
        reply_markup=get_main_menu(True)
    )
    await call.answer()
# ======================
# ИСТОРИЯ СМЕН
# ======================

@dp.message(F.text == "📜 История смен")
async def history_list(message: Message, state: FSMContext):
    cursor.execute("SELECT id, start_time, end_time, total_sum FROM shifts ORDER BY id DESC")
    rows = cursor.fetchall()
    if not rows:
        await message.answer("История смен пуста", reply_markup=get_main_menu(bool(get_active_shift())))
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    for sid, start, end, total in rows:
        label = f"{start[:16]}"
        label += f" | {total} ₽" if end else " | АКТИВНА"
        kb.button(text=label, callback_data=f"hist|{sid}")
    kb.adjust(1)
    await message.answer("Выбери смену:", reply_markup=kb.as_markup())
    await state.set_state(HistoryStates.browsing)

@dp.callback_query(F.data.startswith("hist|"), HistoryStates.browsing)
async def history_view(call: CallbackQuery):
    sid = int(call.data.split("|")[1])
    cursor.execute("SELECT start_time, end_time, total_sum FROM shifts WHERE id=?", (sid,))
    shift = cursor.fetchone()
    text = (
        f"🕒 Смена {sid}\n"
        f"Начало: {shift[0]}\n"
        f"Конец: {shift[1] or '—'}\n"
        f"Итого: {shift[2] or 0} ₽\n\n"
    )

    cursor.execute("SELECT id, car_number, sum FROM cars WHERE shift_id=?", (sid,))
    cars = cursor.fetchall()
    if not cars:
        text += "Машин нет"
    else:
        for cid, car, s in cars:
            text += f"🚗 {car} — {s} ₽\n"
            cursor.execute("SELECT name, count FROM services WHERE car_id=?", (cid,))
            for n, c in cursor.fetchall():
                text += f"  • {n} ×{c}\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="hist_back")
    await call.message.answer(text, reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data == "hist_back")
async def history_back(call: CallbackQuery):
    await call.message.delete()
    await call.answer()

# ======================
# ПОВТОРКИ
# ======================

@dp.callback_query(F.data == "report_repeats")
async def report_repeats(call: CallbackQuery):
    cursor.execute("""
        SELECT car_number, COUNT(*)
        FROM cars
        GROUP BY car_number
        HAVING COUNT(*) > 1
    """)
    cars = cursor.fetchall()

    cursor.execute("""
        SELECT name, SUM(count)
        FROM services
        GROUP BY name
        HAVING SUM(count) > 1
    """)
    services = cursor.fetchall()

    text = "🔁 ПОВТОРЫ\n\n"
    if cars:
        text += "🚗 Машины:\n"
        for c, n in cars:
            text += f"- {c} ×{n}\n"

    if services:
        text += "\n🛠 Услуги:\n"
        for s, n in services:
            text += f"- {s} ×{n}\n"

    if not cars and not services:
        text += "Повторов нет"

    await call.message.answer(text)
    await call.answer()

# ======================
# ОТМЕНА FSM
# ======================

from aiogram.filters import Command

@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Действие отменено",
        reply_markup=get_main_menu(bool(get_active_shift()))
    )

# ======================
# ОБРАБОТКА НЕИЗВЕСТНЫХ СООБЩЕНИЙ
# ======================

@dp.message()
async def fallback(message: Message):
    await message.answer(
        "Используй кнопки меню",
        reply_markup=get_main_menu(bool(get_active_shift()))
    )

# ======================
# ЗАПУСК БОТА
# ======================

async def main():
    print("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())