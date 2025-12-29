import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command, Text
from aiogram.types import Message, CallbackQuery

# ----------------------
# Константы и конфигурация
# ----------------------
API_TOKEN = "8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c"
OWNER_ID = 8379101989  # Telegram ID владельца (админа)
DB_FILENAME = "shifts.db"

# ----------------------
# Инициализация бота и диспетчера
# ----------------------
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ----------------------
# FSM-состояния
# ----------------------
class ShiftStates(StatesGroup):
    adding_car = State()    # Ожидание ввода номера машины
    editing_car = State()   # Добавление/удаление услуг для машины
    none = State()          # Нулевое состояние (для явного сброса)

class HistoryStates(StatesGroup):
    browsing = State()      # Просмотр истории смен

# ----------------------
# Инициализация БД
# ----------------------
conn = sqlite3.connect(DB_FILENAME)
cursor = conn.cursor()

# Таблица смен
cursor.execute("""
CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT,
    end_time TEXT,
    total_sum INTEGER
)
""")

# Таблица машин в смене
cursor.execute("""
CREATE TABLE IF NOT EXISTS cars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER,
    car_number TEXT,
    sum INTEGER,
    FOREIGN KEY (shift_id) REFERENCES shifts(id)
)
""")

# Таблица услуг для каждой машины
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

# ----------------------
# Список доступных услуг (название и цена)
# ----------------------
SERVICES = [
    ("Проверка", 115), ("Заправка", 198), ("Подкачка", 75), 
    ("Заливка омывайки", 66), ("Перегон на СТО", 254),
    ("Зарядка АКБ", 125), ("Нет спутника", 398), ("Развоз до 3 часов", 373),
    ("Развоз до 5 часов", 747), ("Срочка", 220), ("Завершение аренды", 93),
    ("Проверка ходовой", 115), ("Нестандартная операция", 83),
    ("Перепарковка ТС", 150), ("Сугроб простой", 160), ("Раскладка документов", 31),
    ("Чек", 50), ("Перемещение ТС до 20км", 320), ("Замена лампочки", 31),
    ("Закрепление ГРЗ", 31), ("Установка дворника", 74), ("Установка зеркала", 74),
    ("Заправка из канистры", 278), ("Долив тех. жидкостей", 77), 
    ("Сугроб сложный", 902), ("Удаленная заправка", 545)
]

SERVICES_PER_PAGE = 5  # услуги на одной странице

# ----------------------
# Вспомогательные функции для работы с БД и FSM
# ----------------------
def get_active_shift():
    """Возвращает ID активной смены или None."""
    cursor.execute("SELECT id FROM shifts WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None

def open_shift():
    """Открывает новую смену, фиксируя время начала."""
    cursor.execute("INSERT INTO shifts (start_time) VALUES (datetime('now','localtime'))")
    conn.commit()
    return cursor.lastrowid

def close_shift(shift_id):
    """Закрывает смену, фиксируя время окончания и суммарную сумму."""
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
    """Записывает машину и её услуги в БД, возвращает итоговую сумму по машине."""
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
    """Формирует текст отчёта по смене: список машин и итог."""
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
    """Формирует текст строки для кнопки истории смены."""
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
    """Ищет повторяющиеся машины/услуги в смене и возвращает текст отчёта."""
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

# ----------------------
# Клавиатура главного меню (панель смены)
# ----------------------
def get_shift_panel(active_shift: bool = False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if active_shift:
        kb.add(KeyboardButton("➕ Добавить машину"))
        kb.add(KeyboardButton("📊 Итоги смены"), KeyboardButton("⏱ Информация о смене"))
        kb.add(KeyboardButton("📜 История смен"))
        kb.add(KeyboardButton("⛔ Закрыть смену"))
    else:
        kb.add(KeyboardButton("Открыть смену"))
    return kb

# ----------------------
# Обработчики команд
# ----------------------
@dp.message(Command(commands=["start", "menu"]))
async def cmd_start(message: Message, state: FSMContext):
    """Старт: проверка прав, показ главного меню."""
    if message.from_user.id != OWNER_ID:
        await message.reply("Извините, у вас нет доступа к этому боту.")
        return
    await state.clear()
    shift_id = get_active_shift()
    text = "Смена уже открыта. Панель смены:" if shift_id else "Добро пожаловать! Смена еще не открыта."
    await message.answer(text, reply_markup=get_shift_panel(active_shift=bool(shift_id)))

# ----------------------
# Открытие/закрытие смены
# ----------------------
@dp.message(Text(equals="Открыть смену"))
async def open_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if get_active_shift():
        await message.answer("Смена уже открыта.", reply_markup=get_shift_panel(active_shift=True))
        return
    shift_id = open_shift()
    await message.answer("Смена открыта.", reply_markup=get_shift_panel(active_shift=True))

@dp.message(Text(equals="⛔ Закрыть смену"))
async def close_shift_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel(active_shift=False))
        return
    total = close_shift(shift_id)
    # Предлагаем отчёты после закрытия
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton(text="💰 Денежный отчёт", callback_data="report_money"),
        InlineKeyboardButton(text="🔁 Отчёт повторок", callback_data="report_repeats")
    )
    await message.answer(f"Смена закрыта. Итого: {total} руб.\nВыберите отчёт:", reply_markup=markup)

# ----------------------
# Промежуточные отчёты
# ----------------------
@dp.message(Text(equals="📊 Итоги смены"))
async def interim_report_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel(active_shift=False))
        return
    summary = format_shift_summary(shift_id)
    await message.answer("📊 Итоги текущей смены:\n" + summary, reply_markup=get_shift_panel(active_shift=True))

@dp.message(Text(equals="⏱ Информация о смене"))
async def shift_info_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel(active_shift=False))
        return
    cursor.execute("SELECT start_time FROM shifts WHERE id = ?", (shift_id,))
    start = cursor.fetchone()[0]
    cursor.execute("SELECT (strftime('%s','now') - strftime('%s',start_time)) FROM shifts WHERE id = ?", (shift_id,))
    seconds = cursor.fetchone()[0]
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    await message.answer(f"Смена открыта: {start}\nДлительность: {hours} ч {minutes} мин.\nМашин/час: N/A",
                         reply_markup=get_shift_panel(active_shift=True))

# ----------------------
# Обработчики Inline-кнопок отчётов
# ----------------------
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

# ----------------------
# История смен
# ----------------------
@dp.message(Text(equals="📜 История смен"))
async def history_handler(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.set_state(HistoryStates.browsing)
    cursor.execute("SELECT id FROM shifts ORDER BY id DESC")
    shifts = cursor.fetchall()
    if not shifts:
        await message.answer("История пуста.", reply_markup=get_shift_panel(active_shift=bool(get_active_shift())))
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

# ----------------------
# Добавление машины: ввод номера
# ----------------------
@dp.message(Text(equals="➕ Добавить машину"))
async def add_car_start(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    if not get_active_shift():
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel(active_shift=False))
        return
    await state.set_state(ShiftStates.adding_car)
    await message.answer("Введите номер машины (например, X360PY797):")

@dp.message(ShiftStates.adding_car)
async def add_car_number(message: Message, state: FSMContext):
    car_number = message.text.strip().upper()
    # Автодополнение региона 797, если нет
    if len(car_number) <= 6 or not car_number[-3:].isdigit():
        car_number += "797"
    # Проверка формата (буквы латиницей, цифры)
    import re
    if not re.match(r'^[A-Z0-9]{6,10}$', car_number):
        await message.answer("Некорректный номер машины. Попробуйте снова.")
        return
    await state.update_data(car_number=car_number, services={}, delete_mode=False)
    await state.set_state(ShiftStates.editing_car)
    await show_services_page(message, state, page=0)

# ----------------------
# Функция показа страницы услуг
# ----------------------
async def show_services_page(message_or_callback, state: FSMContext, page: int):
    data = await state.get_data()
    car_number = data.get("car_number", "")
    services_count = data.get("services", {})

    header = f"Машина {car_number}\nВыберите услуги (Добавление):"
    markup = InlineKeyboardMarkup(row_width=2)
    start = page * SERVICES_PER_PAGE
    end = start + SERVICES_PER_PAGE
    slice_services = SERVICES[start:end]

    for name, price in slice_services:
        count = services_count.get(name, 0)
        text = f"{name} (+1)" if count == 0 else f"{name} (+1) [{count}]"
        markup.insert(InlineKeyboardButton(text=text, callback_data=f"svc_{page}_{name}"))

    nav_buttons = []
    if start > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"svc_page_{page-1}"))
    if end < len(SERVICES):
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"svc_page_{page+1}"))
    if nav_buttons:
        markup.row(*nav_buttons)

    markup.row(
        InlineKeyboardButton("🗑 Удалить услугу", callback_data="toggle_delete"),
        InlineKeyboardButton("✅ Готово", callback_data="svc_done")
    )

    if hasattr(message_or_callback, 'answer'):
        await message_or_callback.answer(header, reply_markup=markup)
    else:
        await message_or_callback.reply(header, reply_markup=markup)

# ----------------------
# Смена страницы услуг
# ----------------------
@dp.callback_query(Text(startswith="svc_page_"), StateFilter(ShiftStates.editing_car))
async def service_page_change(callback: CallbackQuery, state: FSMContext):
    _, _, page_str = callback.data.partition("_")
    page = int(page_str)
    await callback.message.delete()
    await show_services_page(callback, state, page)
    await callback.answer()

# ----------------------
# Режим удаления услуг
# ----------------------
@dp.callback_query(Text(equals="toggle_delete"), StateFilter(ShiftStates.editing_car))
async def toggle_delete_mode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    delete_mode = data.get("delete_mode", False)
    await state.update_data(delete_mode=not delete_mode)
    await callback.answer("Режим удаления " + ("включен" if not delete_mode else "отключен"))

# ----------------------
# Выбор/удаление услуги
# ----------------------
@dp.callback_query(Text(startswith="svc_"), StateFilter(ShiftStates.editing_car))
async def service_selected(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    delete_mode = data.get("delete_mode", False)
    _, _, rest = callback.data.partition("_")
    parts = rest.split("_", 1)
    if parts[0].isdigit():
        name = parts[1]
    else:
        await callback.answer()
        return
    services_count = data.get("services", {})
    count = services_count.get(name, 0)
    price = dict(SERVICES).get(name, 0)
    if delete_mode:
        count = max(0, count - 1)
    else:
        count += 1
    services_count[name] = count
    await state.update_data(services=services_count)
    await callback.answer(f"{name}: {count}")
    page = int(parts[0])
    await callback.message.delete()
    await show_services_page(callback, state, page)

# ----------------------
# Завершение добавления машины
# ----------------------
@dp.callback_query(Text(equals="svc_done"), StateFilter(ShiftStates.editing_car))
async def service_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    car_number = data.get("car_number")
    services_count = data.get("services", {})
    services_list = [(name, cnt, price) for (name, price) in SERVICES for cnt in [services_count.get(name, 0)]]
    shift_id = get_active_shift()
    total = record_car(shift_id, car_number, services_list)
    svc_lines = [f"{name}×{cnt}" for name, cnt, price in services_list if cnt > 0]
    svc_text = ", ".join(svc_lines) if svc_lines else "–"
    text = f"Машина записана: {car_number}\nУслуги: {svc_text}\nИтого: {total} руб."
    await callback.message.answer(text)
    await callback.answer("Машина сохранена.")
    await state.clear()
    await callback.message.answer("Панель смены:", reply_markup=get_shift_panel(active_shift=True))
# ----------------------
# Отмена текущего действия
# ----------------------
@dp.message(Command(commands=["cancel"]), StateFilter(ShiftStates))
async def cancel_handler(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_shift_panel(active_shift=bool(get_active_shift())))

# ----------------------
# Дефолтный обработчик для любых других сообщений
# ----------------------
@dp.message()
async def default_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer("Используйте кнопки меню.", reply_markup=get_shift_panel(active_shift=bool(get_active_shift())))

# ----------------------
# Запуск бота
# ----------------------
if __name__ == "__main__":
    print("Бот запущен...")
    dp.run_polling(bot)