import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command, Text
from aiogram.types import Message, CallbackQuery

# Константы и конфигурация
API_TOKEN = "ВАШ_ТОКЕН_БОТА"
OWNER_ID = 123456789  # Телеграм ID владельца (админа) бота
DB_FILENAME = "shifts.db"

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
# (Здесь можно указать версию aiogram, например 3.22.0, совместимую с Python 3.10+10.)

# FSM-состояния
class ShiftStates(StatesGroup):
    adding_car = State()    # Ожидание ввода номера машины
    editing_car = State()   # Добавление услуг для машины
    none = State()          # Нулевое состояние (для явного сброса)

class HistoryStates(StatesGroup):
    browsing = State()      # Просмотр истории смен

# Инициализация БД (если не существует)
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

# Список доступных услуг (название и цена)
SERVICES = [
    ("Шиномонтаж", 1000),
    ("Мойка", 500),
    ("Замена масла", 1500),
    ("Диагностика", 800),
    ("Заправка кондиционера", 1200),
    ("Полировка", 2000),
    ("Ремонт двигателя", 3000),
    ("Балансировка", 700),
    # ... возможно десятки услуг
]

# Размер страницы услуг для инлайн-клавиатуры (скроллинг)
SERVICES_PER_PAGE = 5

# Вспомогательные функции для работы с БД и FSM
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
    # Вычислить общую сумму по смене
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
    # Обновить сумму по машине
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
    # Повторяющиеся номера машин
    cursor.execute("SELECT car_number, COUNT(*) FROM cars WHERE shift_id = ? GROUP BY car_number HAVING COUNT(*) > 1", (shift_id,))
    cars = cursor.fetchall()
    # Повторяющиеся услуги (по имени) на любой машине
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

# Клавиатура главного меню (панель смены)
def get_shift_panel():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Добавить машину"))
    kb.add(KeyboardButton("📊 Итоги смены"), KeyboardButton("⏱ Информация о смене"))
    kb.add(KeyboardButton("📜 История смен"))
    kb.add(KeyboardButton("⛔ Закрыть смену"))
    return kb

# Обработчики команд
@dp.message(Command(commands=["start", "menu"]))
async def cmd_start(message: Message, state: FSMContext):
    """Старт: проверка прав, показ главного меню."""
    if message.from_user.id != OWNER_ID:
        await message.reply("Извините, у вас нет доступа к этому боту.")
        return
    # Сброс состояния
    await state.clear()
    shift_id = get_active_shift()
    if shift_id:
        text = "Смена уже открыта. Панель смены:"
    else:
        text = "Добро пожаловать! Смена еще не открыта."
    await message.answer(text, reply_markup=get_shift_panel())

@dp.message(Text(equals="Открыть смену"))
async def open_shift_handler(message: Message):
    """Открытие новой смены."""
    if message.from_user.id != OWNER_ID:
        return
    if get_active_shift():
        await message.answer("Смена уже открыта.", reply_markup=get_shift_panel())
        return
    shift_id = open_shift()
    await message.answer("Смена открыта.", reply_markup=get_shift_panel())

@dp.message(Text(equals="⛔ Закрыть смену"))
async def close_shift_handler(message: Message):
    """Закрытие текущей смены и предложение отчётов."""
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel())
        return
    total = close_shift(shift_id)
    await message.answer(f"Смена закрыта. Итого: {total} руб.\nВыберите отчёт:",
                         reply_markup=InlineKeyboardMarkup().add(
                             InlineKeyboardButton(text="💰 Денежный отчёт", callback_data="report_money"),
                             InlineKeyboardButton(text="🔁 Отчёт повторок", callback_data="report_repeats")
                         ))

@dp.callback_query(Text(equals="report_money"))
async def report_money_handler(callback: CallbackQuery):
    """Показать денежный отчёт по недавно закрытой смене."""
    shift_id = cursor.execute("SELECT id FROM shifts WHERE end_time IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()[0]
    text = format_shift_summary(shift_id)
    await callback.message.answer("💰 Денежный отчёт:\n" + text)
    await callback.answer()

@dp.callback_query(Text(equals="report_repeats"))
async def report_repeats_handler(callback: CallbackQuery):
    """Показать отчёт повторений по недавно закрытой смене."""
    shift_id = cursor.execute("SELECT id FROM shifts WHERE end_time IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()[0]
    text = find_repeats(shift_id)
    await callback.message.answer("🔁 Отчёт повторок:\n" + text)
    await callback.answer()

@dp.message(Text(equals="📜 История смен"))
async def history_handler(message: Message, state: FSMContext):
    """Войти в режим просмотра истории смен."""
    if message.from_user.id != OWNER_ID:
        return
    await state.set_state(HistoryStates.browsing)
    # Сбор кнопок со всеми сменами
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
    """Показать детали выбранной смены из истории."""
    _, sid_str = callback.data.split("_")
    sid = int(sid_str)
    cursor.execute("SELECT start_time, end_time, total_sum FROM shifts WHERE id = ?", (sid,))
    row = cursor.fetchone()
    text = f"Смена {sid}:\n"
    if row:
        start, end, total = row
        text += f"Начало: {start}\nКонец: {end}\nИтог: {total or 0} руб.\n\n"
        # Список машин
        cursor.execute("SELECT car_number, sum, id FROM cars WHERE shift_id = ?", (sid,))
        cars = cursor.fetchall()
        if cars:
            text += "Машины:\n"
            for car_number, car_sum, car_id in cars:
                text += f"- {car_number}: {car_sum} руб.\n"
                # Список услуг
                cursor.execute("SELECT name, count FROM services WHERE car_id = ?", (car_id,))
                services = cursor.fetchall()
                for name, count in services:
                    text += f"    • {name} ×{count}\n"
        else:
            text += "Нет машин.\n"
    # Кнопка назад
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="hist_back"))
    await callback.message.answer(text, reply_markup=markup)
    await callback.answer()

@dp.callback_query(Text(equals="hist_back"), state=HistoryStates.browsing)
async def history_back_handler(callback: CallbackQuery):
    """Назад в список смен истории."""
    # Предполагаем, что state уже в HistoryStates.browsing
    await callback.message.delete()
    # Повторим показ списка смен
    cursor.execute("SELECT id FROM shifts ORDER BY id DESC")
    shifts = cursor.fetchall()
    markup = InlineKeyboardMarkup(row_width=1)
    for (sid,) in shifts:
        label = format_history_item(sid)
        markup.add(InlineKeyboardButton(text=label, callback_data=f"hist_{sid}"))
    await callback.message.answer("Выберите смену:", reply_markup=markup)
    await callback.answer()

# Информация о смене
@dp.message(Text(equals="⏱ Информация о смене"))
async def shift_info_handler(message: Message):
    """Показать информацию о текущей смене."""
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel())
        return
    # Получим время начала
    cursor.execute("SELECT start_time FROM shifts WHERE id = ?", (shift_id,))
    start = cursor.fetchone()[0]
    # Рассчитаем продолжительность
    cursor.execute("SELECT (strftime('%s', 'now') - strftime('%s', start_time)) FROM shifts WHERE id = ?", (shift_id,))
    seconds = cursor.fetchone()[0]
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    await message.answer(f"Смена открыта: {start}\nДлительность: {hours} ч {minutes} мин.\nМашин/час: N/A", reply_markup=get_shift_panel())

# Итоги смены без закрытия (промежуточно)
@dp.message(Text(equals="📊 Итоги смены"))
async def interim_report_handler(message: Message):
    """Показать промежуточный отчёт (активная смена)."""
    if message.from_user.id != OWNER_ID:
        return
    shift_id = get_active_shift()
    if not shift_id:
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel())
        return
    summary = format_shift_summary(shift_id)
    await message.answer("📊 Итоги текущей смены:\n" + summary, reply_markup=get_shift_panel())

# Добавление машины: ввод номера
@dp.message(Text(equals="➕ Добавить машину"))
async def add_car_start(message: Message, state: FSMContext):
    """Начало диалога добавления машины."""
    if message.from_user.id != OWNER_ID:
        return
    if not get_active_shift():
        await message.answer("Смена не открыта.", reply_markup=get_shift_panel())
        return
    await state.set_state(ShiftStates.adding_car)
    await message.answer("Введите номер машины (или отмените):")

@dp.message(ShiftStates.adding_car)
async def add_car_number(message: Message, state: FSMContext):
    """Обработка введённого номера машины."""
    car_number = message.text.strip()
    if not car_number:
        await message.answer("Некорректный номер. Введите снова или /cancel.")
        return
    # Сохраняем номер в context и переходим к выбору услуг
    await state.update_data(car_number=car_number, services={})
    await state.set_state(ShiftStates.editing_car)
    # Начинаем с первой страницы услуг
    await show_services_page(message, state, page=0)

@dp.message(Command(commands=["cancel"]), StateFilter(ShiftStates))
async def cancel_handler(message: Message, state: FSMContext):
    """Отмена ввода машины на любом этапе."""
    if message.from_user.id != OWNER_ID:
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_shift_panel())

async def show_services_page(message_or_callback, state: FSMContext, page: int):
    """Показать страницу услуг для добавления/удаления."""
    data = await state.get_data()
    car_number = data.get("car_number", "")
    services_count = data.get("services", {})  # словарь {имя: count}
    # Формируем текст экрана
    header = f"Машина {car_number}\nВыберите услуги (Добавление):"
    # Inline-клавиатура услуг на странице
    markup = InlineKeyboardMarkup(row_width=2)
    start = page * SERVICES_PER_PAGE
    end = start + SERVICES_PER_PAGE
    slice_services = SERVICES[start:end]
    for name, price in slice_services:
        count = services_count.get(name, 0)
        text = f"{name} (+1)" if count == 0 else f"{name} (+1) [{count}]"
        markup.insert(InlineKeyboardButton(text=text, callback_data=f"svc_{page}_{name}"))
    # Кнопки управления
    nav_buttons = []
    if start > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"svc_page_{page-1}"))
    if end < len(SERVICES):
        nav_buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"svc_page_{page+1}"))
    if nav_buttons:
        markup.row(*nav_buttons)
    # Кнопки удаления и готовности
    markup.row(
        InlineKeyboardButton(text="🗑 Удалить услугу", callback_data="toggle_delete"),
        InlineKeyboardButton(text="✅ Готово", callback_data="svc_done")
    )
    await message_or_callback.answer(header, reply_markup=markup) if hasattr(message_or_callback, 'answer') else await message_or_callback.reply(header, reply_markup=markup)

@dp.callback_query(Text(startswith="svc_page_"), StateFilter(ShiftStates.editing_car))
async def service_page_change(callback: CallbackQuery, state: FSMContext):
    """Смена страницы услуг."""
    _, _, page_str = callback.data.partition("_")
    page = int(page_str)
    await callback.message.delete()
    await show_services_page(callback, state, page)
    await callback.answer()

@dp.callback_query(Text(equals="toggle_delete"), StateFilter(ShiftStates.editing_car))
async def toggle_delete_mode(callback: CallbackQuery, state: FSMContext):
    """Переключить режим удаления услуги (в контексте FSM)."""
    data = await state.get_data()
    delete_mode = data.get("delete_mode", False)
    await state.update_data(delete_mode=not delete_mode)
    await callback.answer("Режим удаления " + ("включен" if not delete_mode else "отключен"))

@dp.callback_query(Text(startswith="svc_"), StateFilter(ShiftStates.editing_car))
async def service_selected(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатия на услугу."""
    data = await state.get_data()
    delete_mode = data.get("delete_mode", False)
    _, _, rest = callback.data.partition("_")
    parts = rest.split("_", 1)
    if parts[0].isdigit():
        # Формат svc_{page}_{name}
        name = parts[1]
    else:
        await callback.answer()  # нет данных
        return
    services_count = data.get("services", {})
    count = services_count.get(name, 0)
    price = dict(SERVICES).get(name, 0)
    if delete_mode:
        if count > 0:
            count -= 1
        else:
            count = 0
    else:
        count += 1
    services_count[name] = count
    await state.update_data(services=services_count)
    await callback.answer(f"{name}: {count}")
    # Перерисуем страницу (номер страницы из callback.data)
    page = int(parts[0])
    await callback.message.delete()
    await show_services_page(callback, state, page)

@dp.callback_query(Text(equals="svc_done"), StateFilter(ShiftStates.editing_car))
async def service_done(callback: CallbackQuery, state: FSMContext):
    """Завершение редактирования услуг: сохранить машину."""
    data = await state.get_data()
    car_number = data.get("car_number")
    services_count = data.get("services", {})
    # Подготовим список (имя, count, price) для заноса
    services_list = [(name, cnt, price) for (name, price) in SERVICES for cnt in [services_count.get(name, 0)]]
    shift_id = get_active_shift()
    total = record_car(shift_id, car_number, services_list)
    # Сформируем ответ
    svc_lines = [f"{name}×{cnt}" for name, cnt, price in services_list if cnt > 0]
    svc_text = ", ".join(svc_lines) if svc_lines else "–"
    text = f"Машина записана: {car_number}\nУслуги: {svc_text}\nИтого: {total} руб."
    await callback.message.answer(text)
    await callback.answer("Машина сохранена.")
    # Сброс FSM, возврат на панель смены
    await state.clear()
    await callback.message.answer("Панель смены:", reply_markup=get_shift_panel())

# Обработчик на «запрос не из активного FSM»
@dp.message()
async def default_handler(message: Message):
    """Обработчик сообщений, не подходящих под другие команды."""
    if message.from_user.id != OWNER_ID:
        return
    await message.answer("Используйте кнопки меню.", reply_markup=get_shift_panel())

if __name__ == "__main__":
    print("Бот запущен...")
    dp.run_polling(bot)