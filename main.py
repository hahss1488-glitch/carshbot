import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command, Text, StateFilter
from aiogram.types import Message, CallbackQuery
import re

API_TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"
OWNER_ID = 8379101989
DB_FILENAME = "shifts.db"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# FSM
class ShiftStates(StatesGroup):
    adding_car = State()
    editing_car = State()
    none = State()

class HistoryStates(StatesGroup):
    browsing = State()

# Инициализация БД
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
    sum INTEGER,
    FOREIGN KEY (shift_id) REFERENCES shifts(id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    car_id INTEGER,
    name TEXT,
    count INTEGER,
    price INTEGER,
    FOREIGN KEY (car_id) REFERENCES cars(id)
)
""")
conn.commit()

# Прайс
SERVICES = [
    # Первая страница (самые частые)
    ("Проверка", 115),
    ("Заправка", 198),
    ("Подкачка", 75),
    ("Заливка омывайки", 66),
    ("Перегон на СТО", 254),
    # Вторая страница
    ("Зарядка АКБ", 125),
    ("Нет спутника", 398),
    ("Развоз до 3 часов", 373),
    ("Развоз до 5 часов", 747),
    ("Срочка", 220),
    ("Завершение аренды", 93),
    ("Проверка ходовой", 115),
    ("Нестандартная операция", 83),
    # Третья страница
    ("Перепарковка ТС", 150),
    ("Сугроб простой", 160),
    ("Раскладка документов", 31),
    ("Чек", 50),
    ("Перемещение ТС до 20км", 320),
    ("Проверка ходовой", 115),
    ("Замена лампочки", 31),
    ("Закрепление ГРЗ", 31),
    ("Установка дворника", 74),
    ("Установка зеркала", 74),
    ("Заправка из канистры", 278),
    ("Долив тех. жидкостей", 77),
    ("Сугроб сложный", 902),
    ("Удаленная заправка", 545)
]

SERVICES_PER_PAGE = 5

# Функции работы с БД
def get_active_shift():
    cursor.execute("SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None

def open_shift():
    cursor.execute("INSERT INTO shifts (start_time) VALUES (datetime('now','localtime'))")
    conn.commit()
    return cursor.lastrowid

def close_shift(shift_id):
    cursor.execute("""
        SELECT SUM(services.price * services.count)
        FROM cars 
        JOIN services ON cars.id = services.car_id 
        WHERE cars.shift_id = ?
    """, (shift_id,))
    total = cursor.fetchone()[0] or 0
    cursor.execute("""
        UPDATE shifts
        SET end_time = datetime('now','localtime'), total_sum = ?
        WHERE id = ?
    """, (total, shift_id))
    conn.commit()
    return total

def record_car(shift_id, car_number, services_list):
    total = 0
    cursor.execute("INSERT INTO cars (shift_id, car_number, sum) VALUES (?, ?, ?)", (shift_id, car_number, 0))
    car_id = cursor.lastrowid
    for name, count, price in services_list:
        if count > 0:
            cursor.execute("INSERT INTO services (car_id, name, count, price) VALUES (?, ?, ?, ?)",
                           (car_id, name, count, price))
            total += price * count
    cursor.execute("UPDATE cars SET sum = ? WHERE id = ?", (total, car_id))
    conn.commit()
    return total

def format_shift_summary(shift_id):
    lines = []
    cursor.execute("SELECT car_number, sum, id FROM cars WHERE shift_id = ?", (shift_id,))
    cars = cursor.fetchall()
    total = 0
    for car_number, car_sum, car_id in cars:
        lines.append(f"Машина {car_number}: {car_sum} руб.")
        total += car_sum
    summary = "\n".join(lines)
    summary += f"\n\nВсего: {total} руб."
    return summary

def format_history_item(shift_id):
    cursor.execute("SELECT start_time, end_time, total_sum FROM shifts WHERE id = ?", (shift_id,))
    row = cursor.fetchone()
    if not row:
        return "Неизвестная смена"
    start, end, total = row
    if end:
        return f"{start} – {end} (всего {total} руб.)"
    else:
        return f"{start} – *активна*"

def find_repeats(shift_id):
    cursor.execute("SELECT car_number, COUNT(*) FROM cars WHERE shift_id = ? GROUP BY car_number HAVING COUNT(*) > 1", (shift_id,))
    cars = cursor.fetchall()
    cursor.execute("""
        SELECT services.name, SUM(services.count) 
        FROM cars JOIN services ON cars.id = services.car_id
        WHERE cars.shift_id = ?
        GROUP BY services.name
        HAVING SUM(services.count) > 1
    """, (shift_id,))
    services = cursor.fetchall()
    parts = []
    if cars:
        parts.append("Повторяющиеся номера машин:")
        for car, cnt in cars:
            parts.append(f"- {car} ({cnt} раза)")
    if services:
        parts.append("Повторяющиеся услуги (суммарно):")
        for name, cnt in services:
            parts.append(f"- {name}: {cnt} раз")
    return "\n".join(parts) if parts else "Повторяющихся записей не найдено."

# Клавиатура
def get_shift_panel():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Добавить машину"))
    kb.add(KeyboardButton("📊 Итоги смены"), KeyboardButton("⏱ Информация о смене"))
    kb.add(KeyboardButton("📜 История смен"))
    kb.add(KeyboardButton("⛔ Закрыть смену"))
    return kb

# Вспомогательная функция для перевода номера в латиницу и добавления региона
def normalize_car_number(number: str) -> str:
    mapping = str.maketrans("АВСЕНКМОРТХ", "ABCEHKMOPTX")
    number = number.upper().translate(mapping)
    number = re.sub(r"[^A-Z0-9]", "", number)
    if len(number) <= 6:
        number += "797"
    return number

# --- Обработчики команд и кнопок ---

@dp.message(Command(commands=["start", "menu"]))
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await message.reply("Извините, у вас нет доступа к этому боту.")
        return
    await state.clear()
    shift_id = get_active_shift()
    if shift_id:
        text = "Смена уже открыта. Панель смены:"
    else:
        text = "Добро пожаловать! Смена еще не открыта."
    await message.answer(text, reply_markup=get_shift_panel())

@dp.message(Text(equals="Открыть смену"))
async def open_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if get_active_shift():
        await message.answer("Смена уже открыта.", reply_markup=get_shift_panel())
        return
    shift_id = open_shift()
    await message.answer("Смена открыта.", reply_markup=get_shift_panel())

@dp.message(Text(equals="⛔ Закрыть смену"))
async def close_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel())
        return
    total = close_shift(shift_id)
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(text="💰 Денежный отчёт", callback_data="report_money"),
        InlineKeyboardButton(text="🔁 Отчёт повторок", callback_data="report_repeats")
    )
    await message.answer(f"Смена закрыта. Итого: {total} руб.\nВыберите отчёт:", reply_markup=markup)

# --- Отчеты ---
@dp.callback_query(Text(equals="report_money"))
async def report_money_handler(callback: CallbackQuery):
    shift_id = cursor.execute("SELECT id FROM shifts WHERE end_time IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()[0]
    text = format_shift_summary(shift_id)
    await callback.message.answer("💰 Денежный отчёт:\n" + text)
    await callback.answer()

@dp.callback_query(Text(equals="report_repeats"))
async def report_repeats_handler(callback: CallbackQuery):
    shift_id = cursor.execute("SELECT id FROM shifts WHERE end_time IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()[0]
    text = find_repeats(shift_id)
    await callback.message.answer("🔁 Отчёт повторок:\n" + text)
    await callback.answer()

# --- История смен ---
@dp.message(Text(equals="📜 История смен"))
async def history_handler(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.set_state(HistoryStates.browsing)
    cursor.execute("SELECT id FROM shifts ORDER BY id DESC")
    shifts = cursor.fetchall()
    if not shifts:
        await message.answer("История пуста.", reply_markup=get_shift_panel())
        await state.clear()
        return
    markup = InlineKeyboardMarkup(row_width=1)
    for (sid,) in shifts:
        label = format_history_item(sid)
        markup.add(InlineKeyboardButton(text=label, callback_data=f"hist_{sid}"))
    await message.answer("Выберите смену:", reply_markup=markup)

@dp.callback_query(lambda c: c.data and c.data.startswith("hist_"), state=HistoryStates.browsing)
async def history_view_handler(callback: CallbackQuery, state: FSMContext):
    _, sid_str = callback.data.split("_")
    sid = int(sid_str)
    cursor.execute("SELECT start_time, end_time, total_sum FROM shifts WHERE id = ?", (sid,))
    row = cursor.fetchone()
    text = f"Смена {sid}:\n"
    if row:
        start, end, total = row
        text += f"Начало: {start}\nКонец: {end}\nИтог: {total or 0} руб.\n\n"
        cursor.execute("SELECT car_number, sum, id FROM cars WHERE shift_id = ?", (sid,))
        cars = cursor.fetchall()
        if cars:
            text += "Машины:\n"
            for car_number, car_sum, car_id in cars:
                text += f"- {car_number}: {car_sum} руб.\n"
                cursor.execute("SELECT name, count FROM services WHERE car_id = ?", (car_id,))
                services = cursor.fetchall()
                for name, count in services:
                    text += f"    • {name} ×{count}\n"
        else:
            text += "Нет машин.\n"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="hist_back"))
    await callback.message.answer(text, reply_markup=markup)
    await callback.answer()

@dp.callback_query(Text(equals="hist_back"), state=HistoryStates.browsing)
async def history_back_handler(callback: CallbackQuery):
    await callback.message.delete()
    cursor.execute("SELECT id FROM shifts ORDER BY id DESC")
    shifts = cursor.fetchall()
    markup = InlineKeyboardMarkup(row_width=1)
    for (sid,) in shifts:
        label = format_history_item(sid)
        markup.add(InlineKeyboardButton(text=label, callback_data=f"hist_{sid}"))
    await callback.message.answer("Выберите смену:", reply_markup=markup)
    await callback.answer()

# --- Информация о смене ---
@dp.message(Text(equals="⏱ Информация о смене"))
async def shift_info_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel())
        return
    cursor.execute("SELECT start_time FROM shifts WHERE id = ?", (shift_id,))
    start = cursor.fetchone()[0]
    cursor.execute("SELECT (strftime('%s', 'now') - strftime('%s', start_time)) FROM shifts WHERE id = ?", (shift_id,))
    seconds = cursor.fetchone()[0]
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    await message.answer(f"Смена открыта: {start}\nДлительность: {hours} ч {minutes} мин.", reply_markup=get_shift_panel())

# --- Итоги смены без закрытия ---
@dp.message(Text(equals="📊 Итоги смены"))
async def interim_report_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel())
        return
    summary = format_shift_summary(shift_id)
    await message.answer("📊 Итоги текущей смены:\n" + summary, reply_markup=get_shift_panel())

# --- Добавление машины ---
@dp.message(Text(equals="➕ Добавить машину"))
async def add_car_start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    if not get_active_shift():
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel())
        return
    await state.set_state(ShiftStates.adding_car)
    await message.answer("Введите номер машины (или /cancel):")

@dp.message(ShiftStates.adding_car)
async def add_car_number(message: Message, state: FSMContext):
    car_number = normalize_car_number(message.text.strip())
    await state.update_data(car_number=car_number, services={}, delete_mode=False)
    await state.set_state(ShiftStates.editing_car)
    await show_services_page(message, state, page=0)

@dp.message(Command(commands=["cancel"]), StateFilter(ShiftStates))
async def cancel_handler(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_shift_panel())

# --- Запуск бота ---
@dp.message()
async def default_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer("Используйте кнопки меню.", reply_markup=get_shift_panel())

if __name__ == "__main__":
    print("Бот запущен...")
    dp.run_polling(bot)