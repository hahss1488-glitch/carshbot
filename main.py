BASE_DIR = os.getcwd()
DB_FILE = os.path.join(BASE_DIR, "shifts.db")

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
    date TEXT,
    start_time TEXT,
    end_time TEXT,
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

def get_tariff(dt: datetime | None = None) -> str:
    now = dt.time() if dt else datetime.now().time()
    return "day" if DAY_START <= now < DAY_END else "night"

def get_active_shift() -> int | None:
    cursor.execute("SELECT id FROM shifts WHERE archived = 0 ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None

# ======================
# КЛАВИАТУРЫ
# ======================
def get_main_menu(active_shift: bool) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if active_shift:
        kb.add(KeyboardButton("➕ Добавить машину"))
        kb.add(KeyboardButton("📊 Итоги смены"), KeyboardButton("⏱ Информация о смене"))
        kb.add(KeyboardButton("📜 История смен"))
        kb.add(KeyboardButton("⛔ Закрыть смену"))
        kb.add(KeyboardButton("🗓 Добавить задним числом"))
    else:
        kb.add(KeyboardButton("Открыть смену"))
        kb.add(KeyboardButton("🗓 Добавить задним числом"))
    return kb

def services_keyboard(page: int, selected: dict, delete_mode: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удаление: ВКЛ" if delete_mode else "🗑 Удаление: ВЫКЛ", callback_data="toggle_delete")
    start = page * 5
    end = start + 5
    chunk = list(SERVICES.items())[start:end]
    for key, svc in chunk:
        count = selected.get(key, 0)
        label = f"{svc['name']} ({count})"
        kb.button(text=label, callback_data=f"svc|{page}|{key}")
    # навигация
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page|{page-1}"))
    if end < len(SERVICES):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page|{page+1}"))
    if nav:
        kb.row(*nav)
    kb.button(text="✅ Готово", callback_data="done")
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
    cursor.execute(
        "INSERT INTO shifts (date, start_time) VALUES (?, ?)",
        (datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%H:%M:%S"))
    )
    conn.commit()
    await message.answer("Смена открыта", reply_markup=get_main_menu(True))
async def add_car_start(message: Message, state: FSMContext):
    if not get_active_shift():
        await message.answer("Сначала открой смену")
        return
    await state.set_state(ShiftFSM.add_car_number)
    await message.answer(
        "Введи номер машины (можно кириллицей, без региона)",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(ShiftFSM.add_car_number)
async def add_car_number(message: Message, state: FSMContext):
    normalized = normalize_car_number(message.text)
    if not normalized:
        await message.answer("❌ Неверный номер, попробуй ещё раз")
        return

    price_type = get_tariff()
    await state.update_data(
        car_number=normalized,
        services={},
        page=0,
        delete_mode=False,
        price_type=price_type
    )
    await state.set_state(ShiftFSM.edit_services)
    await message.answer(
        f"🚗 Машина {normalized}\nВыбери услуги:",
        reply_markup=services_keyboard(0, {}, False)
    )

# ======================
# ОБНОВЛЕНИЕ СООБЩЕНИЯ С УСЛУГАМИ
# ======================
async def update_service_message(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    car_number = data["car_number"]
    services = data["services"]
    if not services:
        msg_text = f"🚗 Машина {car_number}\nУслуги пока не выбраны"
    else:
        total = 0
        msg_text = f"🚗 Машина {car_number}\nВыбранные услуги:\n"
        for key, count in services.items():
            if count > 0:
                svc = SERVICES[key]
                msg_text += f"• {svc['name']} ×{count} = {svc[data['price_type']]*count} ₽\n"
                total += svc[data['price_type']] * count
        msg_text += f"\nИтого: {total} ₽"
    await call.message.edit_text(
        msg_text,
        reply_markup=services_keyboard(data["page"], services, data["delete_mode"])
    )

# ======================
# ПЕРЕКЛЮЧЕНИЕ УДАЛЕНИЯ
# ======================
@dp.callback_query(F.data == "toggle_delete", ShiftFSM.edit_services)
async def toggle_delete(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    delete_mode = not data["delete_mode"]
    await state.update_data(delete_mode=delete_mode)
    await update_service_message(call, state)
    await call.answer()

# ======================
# ВЫБОР УСЛУГ
# ======================
@dp.callback_query(F.data.startswith("svc|"), ShiftFSM.edit_services)
async def select_service(call: CallbackQuery, state: FSMContext):
    _, page, key = call.data.split("|")
    page = int(page)
    data = await state.get_data()
    services = data["services"]
    delete_mode = data["delete_mode"]

    count = services.get(key, 0)
    services[key] = max(0, count - 1) if delete_mode else count + 1
    await state.update_data(services=services)
    await update_service_message(call, state)
    await call.answer(f"{SERVICES[key]['name']}: {services[key]}")

# ======================
# ПЕРЕЛИСТЫВАНИЕ СТРАНИЦ
# ======================
@dp.callback_query(F.data.startswith("page|"), ShiftFSM.edit_services)
async def change_page(call: CallbackQuery, state: FSMContext):
    page = int(call.data.split("|")[1])
    await state.update_data(page=page)
    await update_service_message(call, state)
    await call.answer()

# ======================
# СОХРАНЕНИЕ МАШИНЫ
# ======================
@dp.callback_query(F.data == "done", ShiftFSM.edit_services)
async def save_car(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    car_number = data["car_number"]
    services = data["services"]
    price_type = data["price_type"]
    shift_id = get_active_shift()

    cursor.execute(
        "INSERT INTO cars (shift_id, car_number, total_sum) VALUES (?, ?, 0)",
        (shift_id, car_number)
    )
    car_id = cursor.lastrowid

    total = 0
    for key, count in services.items():
        if count > 0:
            svc = SERVICES[key]
            price = svc[price_type]
            cursor.execute(
                "INSERT INTO services (car_id, service_key, service_name, count, price) VALUES (?, ?, ?, ?, ?)",
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
        f"✅ Машина сохранена\n{car_number}\nИтого: {total} ₽",
        reply_markup=get_main_menu(True)
    )
    await call.answer()
# ======================
# ПРОМЕЖУТОЧНЫЕ ОТЧЁТЫ
# ======================
@dp.message(F.text == "📊 Итоги смены")
async def interim_report(message: Message):
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта")
        return
    cursor.execute("SELECT car_number, total_sum FROM cars WHERE shift_id = ?", (shift_id,))
    rows = cursor.fetchall()
    total = sum([s for _, s in rows])
    text = "📊 Итоги:\n"
    for car, s in rows:
        text += f"{car}: {s} ₽\n"
    text += f"\nИТОГО: {total} ₽"
    await message.answer(text)

@dp.message(F.text == "⏱ Информация о смене")
async def shift_info(message: Message):
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта")
        return
    cursor.execute("SELECT start_time FROM shifts WHERE id = ?", (shift_id,))
    start = cursor.fetchone()[0]
    dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    delta = datetime.now() - dt
    h, m = divmod(delta.seconds // 60, 60)
    await message.answer(
        f"Начало: {start}\nДлительность: {h} ч {m} мин"
    )

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
    kb = InlineKeyboardBuilder()
    for sid, start, end, total in rows:
        label = f"{start[:16]}" + (f" | {total} ₽" if end else " | АКТИВНА")
        kb.button(text=label, callback_data=f"hist|{sid}")
    kb.adjust(1)
    await message.answer("Выбери смену:", reply_markup=kb.as_markup())
    await state.set_state(HistoryFSM.browsing)

@dp.callback_query(F.data.startswith("hist|"), HistoryFSM.browsing)
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
    cursor.execute("SELECT id, car_number, total_sum FROM cars WHERE shift_id=?", (sid,))
    cars = cursor.fetchall()
    if not cars:
        text += "Машин нет"
    else:
        for cid, car, s in cars:
            text += f"🚗 {car} — {s} ₽\n"
            cursor.execute("SELECT service_name, count FROM services WHERE car_id=?", (cid,))
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
# ЗАКРЫТИЕ СМЕНЫ
# ======================
def close_shift_logic(shift_id: int) -> int:
    cursor.execute("SELECT total_sum FROM cars WHERE shift_id=?", (shift_id,))
    total = sum([row[0] for row in cursor.fetchall()])
    cursor.execute("UPDATE shifts SET total_sum=?, archived=1, end_time=? WHERE id=?",
                   (total, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), shift_id))
    conn.commit()
    return total

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
    kb.adjust(2)
    await message.answer(f"Смена закрыта\nИтого: {total} ₽", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "report_money")
async def report_money(call: CallbackQuery):
    shift_id = get_active_shift()
    if not shift_id:
        await call.answer("Нет активной смены", show_alert=True)
        return
    cursor.execute("SELECT car_number, total_sum FROM cars WHERE shift_id=?", (shift_id,))
    rows = cursor.fetchall()
    total = 0
    text = "💰 Денежный отчёт:\n"
    for car, s in rows:
        text += f"{car}: {s} ₽\n"
        total += s
    text += f"\nИТОГО: {total} ₽"
    await call.message.answer(text)
    await call.answer()

@dp.callback_query(F.data == "report_repeat")
async def report_repeat(call: CallbackQuery):
    cursor.execute("""
        SELECT car_number, COUNT(*) as cnt
        FROM cars
        GROUP BY car_number
        HAVING COUNT(*) > 1
    """)
    cars = cursor.fetchall()
    text = "🔁 Повторки:\n"
    if not cars:
        text += "Нет"
    else:
        for car, cnt in cars:
            text += f"{car}: {cnt} раз\n"
    await call.message.answer(text)
    await call.answer()
# ======================
# ОТМЕНА FSM
# ======================
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
# ДОБАВЛЕНИЕ СМЕНЫ ЗАДНИМ ЧИСЛОМ
# ======================
@dp.message(F.text == "🗓 Добавить задним числом")
async def backdate_shift_start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.set_state(ShiftFSM.backdate_date)
    await message.answer("Введите дату смены в формате ГГГГ-ММ-ДД")

@dp.message(ShiftFSM.backdate_date)
async def backdate_shift_save(message: Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text, "%Y-%m-%d")
    except ValueError:
        await message.answer("Неверный формат, попробуйте снова (ГГГГ-ММ-ДД)")
        return

    cursor.execute(
        "INSERT INTO shifts (date, start_time) VALUES (?, ?)",
        (dt.strftime("%Y-%m-%d"), "00:00:00")
    )
    conn.commit()
    await state.clear()
    await message.answer(
        f"Смена на {dt.strftime('%Y-%m-%d')} добавлена",
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
