# Импорт необходимых модулей
import logging
import sqlite3
from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, ConversationHandler, CallbackContext

# Токен бота и ID администратора (указать реальный токен и ваш Telegram ID)
TOKEN = '8385307802:AAE0AJGb8T9RQauVVpLzmFKR1jchrcVZR2c'
ADMIN_ID = 8379101989  # замените на ваш Telegram ID

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Подключение к базе данных (создаётся файл bot.db)
conn = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц, если их ещё нет
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    price REAL,
    category_id INTEGER
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS cart (
    user_id INTEGER,
    product_id INTEGER,
    quantity INTEGER,
    PRIMARY KEY (user_id, product_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    items TEXT,
    total REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()

# Наполнение тестовых данных (если таблицы пусты)
cursor.execute("SELECT COUNT(*) FROM categories;")
if cursor.fetchone()[0] == 0:
    # Пример категорий
    cursor.execute("INSERT INTO categories (name) VALUES (?)", ("Электроника",))
    cursor.execute("INSERT INTO categories (name) VALUES (?)", ("Книги",))
    conn.commit()
cursor.execute("SELECT COUNT(*) FROM products;")
if cursor.fetchone()[0] == 0:
    # Пример товаров: имя, описание, цена, id категории
    products = [
        ("Смартфон", "Современный смартфон с хорошей камерой.", 0.0, 1),
        ("Ноутбук", "Удобный ноутбук для работы и игр.", 0.0, 1),
        ("Роман «Приключения»", "Увлекательный приключенческий роман.", 0.0, 2)
    ]
    cursor.executemany("INSERT INTO products (name, description, price, category_id) VALUES (?, ?, ?, ?);", products)
    conn.commit()

# Клавиатуры
def get_main_menu(is_admin=False):
    """Возвращает клавиатуру главного меню в зависимости от роли пользователя."""
    if is_admin:
        buttons = [
            ["Добавить товар", "Просмотр заказов"],
            ["Рассылка", "Пользователи"]
        ]
    else:
        buttons = [
            ["Каталог", "Корзина"],
            ["О нас"]
        ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# Состояния для ConversationHandler (добавление товара, рассылка)
ADD_NAME, ADD_DESC, ADD_PRICE = range(3)
BROADCAST = range(1)

# Функции-обработчики
def start(update: Update, context: CallbackContext) -> None:
    """Обработка команды /start."""
    user = update.effective_user
    user_id = user.id
    is_admin = (user_id == ADMIN_ID)
    # Добавляем пользователя в БД при первом входе
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?);", (user_id, user.username))
    conn.commit()
    # Приветствие
    if is_admin:
        update.message.reply_text(
            "Добро пожаловать, администратор! Вы можете управлять ботом через меню ниже.",
            reply_markup=get_main_menu(is_admin=True)
        )
    else:
        update.message.reply_text(
            "Привет! Это тестовый магазин-бот. Выберите пункт меню.",
            reply_markup=get_main_menu()
        )

def help_command(update: Update, context: CallbackContext) -> None:
    """Обработка команды /help."""
    help_text = (
        "Я бот магазина. С помощью этого бота можно просматривать товары, добавлять их в корзину и оформлять заказ.\n\n"
        "Доступные команды и кнопки:\n"
        "/start – главное меню\n"
        "Каталог – просмотреть список товаров\n"
        "Корзина – просмотреть корзину и оформить заказ\n"
        "О нас – информация о боте\n"
        "/cancel – отмена текущей операции (для администратора)\n"
    )
    update.message.reply_text(help_text)

def about(update: Update, context: CallbackContext) -> None:
    """Вывод информации о боте."""
    update.message.reply_text(
        "Этот бот демонстрирует базовый функционал интернет-магазина в Telegram.\n"
        "Вы можете просмотреть каталог товаров и оформить заказ. Контакт администратора можно получить из кода бота."
    )

def show_catalog(update: Update, context: CallbackContext) -> None:
    """Показать список категорий товаров (Inline-кнопки)."""
    categories = cursor.execute("SELECT id, name FROM categories;").fetchall()
    if not categories:
        update.message.reply_text("Каталог пуст.")
        return
    buttons = [[InlineKeyboardButton(name, callback_data=f"cat_{cat_id}")] for cat_id, name in categories]
    markup = InlineKeyboardMarkup(buttons)
    update.message.reply_text("Выберите категорию товара:", reply_markup=markup)

def category_selected(update: Update, context: CallbackContext) -> None:
    """Обработка выбора категории товаров."""
    query = update.callback_query
    cat_id = int(query.data.split("_")[1])
    query.answer()
    query.edit_message_reply_markup(reply_markup=None)
    products = cursor.execute(
        "SELECT id, name, description, price FROM products WHERE category_id = ?;", (cat_id,)
    ).fetchall()
    if not products:
        context.bot.send_message(chat_id=query.message.chat_id, text="В этой категории нет товаров.")
        return
    for prod_id, name, desc, price in products:
        text = f"*{name}*\n{desc}\nЦена: {price} руб."
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Добавить в корзину", callback_data=f"add_{prod_id}")]])
        context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=button,
            parse_mode='Markdown'
        )

def add_to_cart(update: Update, context: CallbackContext) -> None:
    """Добавить товар в корзину."""
    query = update.callback_query
    user_id = query.from_user.id
    prod_id = int(query.data.split("_")[1])
    current = cursor.execute(
        "SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?;", (user_id, prod_id)
    ).fetchone()
    if current:
        cursor.execute(
            "UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?;",
            (user_id, prod_id)
        )
    else:
        cursor.execute(
            "INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1);",
            (user_id, prod_id)
        )
    conn.commit()
    query.answer("Товар добавлен в корзину.")

def view_cart(update: Update, context: CallbackContext) -> None:
    """Показать содержимое корзины."""
    user_id = update.effective_user.id
    items = cursor.execute(
        "SELECT p.id, p.name, c.quantity FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?;",
        (user_id,)
    ).fetchall()
    if not items:
        update.message.reply_text("Корзина пуста.")
        return
    total = 0.0
    for prod_id, name, qty in items:
        text = f"{name} (в количестве {qty})"
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Удалить", callback_data=f"remove_{prod_id}")]])
        update.message.reply_text(text, reply_markup=button)
        price = cursor.execute("SELECT price FROM products WHERE id = ?;", (prod_id,)).fetchone()[0]
        total += (price * qty) if price else 0
    order_button = InlineKeyboardMarkup([[InlineKeyboardButton("Оформить заказ", callback_data="order")]])
    update.message.reply_text(f"Итого: {total} руб.", reply_markup=order_button)

def remove_from_cart(update: Update, context: CallbackContext) -> None:
    """Удалить товар из корзины."""
    query = update.callback_query
    user_id = query.from_user.id
    prod_id = int(query.data.split("_")[1])
    cursor.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?;", (user_id, prod_id))
    conn.commit()
    query.answer("Товар удалён из корзины.")

def place_order(update: Update, context: CallbackContext) -> None:
    """Оформить заказ."""
    query = update.callback_query
    user_id = query.from_user.id
    items = cursor.execute(
        "SELECT p.name, c.quantity, p.price FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?;",
        (user_id,)
    ).fetchall()
    if not items:
        query.answer("Корзина пуста.")
        return
    order_items = []
    total = 0.0
    for name, qty, price in items:
        order_items.append(f"{name} x{qty}")
        total += (price * qty) if price else 0
    order_text = "; ".join(order_items)
    cursor.execute("INSERT INTO orders (user_id, items, total) VALUES (?, ?, ?);", (user_id, order_text, total))
    cursor.execute("DELETE FROM cart WHERE user_id = ?;", (user_id,))
    conn.commit()
    query.answer("Заказ оформлен.")
    message = (
        f"Новый заказ (ID: {cursor.lastrowid}) от пользователя {user_id}:\n"
        f"{order_text}\nСумма: {total} руб."
    )
    context.bot.send_message(chat_id=ADMIN_ID, text=message)

def add_product_start(update: Update, context: CallbackContext) -> int:
    """Начало добавления нового товара (для администратора)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        update.message.reply_text("У вас нет прав для добавления товара.")
        return ConversationHandler.END
    update.message.reply_text("Введите название нового товара или /cancel для отмены:")
    return ADD_NAME

def add_name(update: Update, context: CallbackContext) -> int:
    """Получаем название товара."""
    context.user_data['new_name'] = update.message.text
    update.message.reply_text("Введите описание товара:")
    return ADD_DESC

def add_desc(update: Update, context: CallbackContext) -> int:
    """Получаем описание товара."""
    context.user_data['new_desc'] = update.message.text
    update.message.reply_text("Введите цену товара (числом):")
    return ADD_PRICE

def add_price(update: Update, context: CallbackContext) -> int:
    """Получаем цену и сохраняем товар."""
    text = update.message.text
    try:
        price = float(text)
    except ValueError:
        update.message.reply_text("Пожалуйста, введите корректную цену (число):")
        return ADD_PRICE
    name = context.user_data.get('new_name')
    desc = context.user_data.get('new_desc')
    cursor.execute(
        "INSERT INTO products (name, description, price, category_id) VALUES (?, ?, ?, ?);",
        (name, desc, price, 1)
    )
    conn.commit()
    update.message.reply_text("Товар успешно добавлен.")
    return ConversationHandler.END

def list_orders(update: Update, context: CallbackContext) -> None:
    """Показать список всех заказов (для администратора)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        update.message.reply_text("У вас нет доступа к заказам.")
        return
    orders = cursor.execute("SELECT id, user_id, items, total FROM orders;").fetchall()
    if not orders:
        update.message.reply_text("Нет заказов.")
        return
    for oid, uid, items, total in orders:
        text = f"Заказ ID {oid} от {uid}:\n{items}\nСумма: {total} руб."
        update.message.reply_text(text)

def broadcast_start(update: Update, context: CallbackContext) -> int:
    """Начало рассылки от администратора."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        update.message.reply_text("У вас нет прав для рассылки.")
        return ConversationHandler.END
    update.message.reply_text("Введите текст сообщения для рассылки или /cancel:")
    return BROADCAST

def broadcast_send(update: Update, context: CallbackContext) -> int:
    """Отправка рассылки всем пользователям."""
    text = update.message.text
    users = cursor.execute("SELECT user_id FROM users;").fetchall()
    for (uid,) in users:
        try:
            context.bot.send_message(chat_id=uid, text=text)
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение пользователю {uid}: {e}")
    update.message.reply_text("Сообщение отправлено всем пользователям.")
    return ConversationHandler.END

def list_users(update: Update, context: CallbackContext) -> None:
    """Показать количество зарегистрированных пользователей (для администратора)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        update.message.reply_text("У вас нет доступа к списку пользователей.")
        return
    count = cursor.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
    update.message.reply_text(f"Всего пользователей: {count}")

def cancel(update: Update, context: CallbackContext) -> int:
    """Отменить текущее действие (ConversationHandler)."""
    update.message.reply_text("Действие отменено.", reply_markup=get_main_menu(is_admin=True))
    return ConversationHandler.END

def unknown(update: Update, context: CallbackContext) -> None:
    """Обработка неизвестных сообщений."""
    update.message.reply_text("Команда не распознана. Используйте /help.")

# Подключение хендлеров к диспетчеру
updater = Updater(TOKEN)
dp = updater.dispatcher

# Команды
dp.add_handler(CommandHandler('start', start))
dp.add_handler(CommandHandler('help', help_command))
dp.add_handler(CommandHandler('cancel', cancel))

# Текстовые кнопки меню (ReplyKeyboard)
dp.add_handler(MessageHandler(Filters.regex('^(Каталог)$'), show_catalog))
dp.add_handler(MessageHandler(Filters.regex('^(Корзина)$'), view_cart))
dp.add_handler(MessageHandler(Filters.regex('^(О нас)$'), about))
dp.add_handler(MessageHandler(Filters.regex('^(Добавить товар)$'), add_product_start))
dp.add_handler(MessageHandler(Filters.regex('^(Просмотр заказов)$'), list_orders))
dp.add_handler(MessageHandler(Filters.regex('^(Рассылка)$'), broadcast_start))
dp.add_handler(MessageHandler(Filters.regex('^(Пользователи)$'), list_users))

# ConversationHandlers для администратора
dp.add_handler(ConversationHandler(
    entry_points=[
        CommandHandler('add_product', add_product_start),
        MessageHandler(Filters.regex('^(Добавить товар)$'), add_product_start)
    ],
    states={
        ADD_NAME: [MessageHandler(Filters.text & ~Filters.command, add_name)],
        ADD_DESC: [MessageHandler(Filters.text & ~Filters.command, add_desc)],
        ADD_PRICE: [MessageHandler(Filters.text & ~Filters.command, add_price)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
))
dp.add_handler(ConversationHandler(
    entry_points=[
        CommandHandler('broadcast', broadcast_start),
        MessageHandler(Filters.regex('^(Рассылка)$'), broadcast_start)
    ],
    states={
        BROADCAST: [MessageHandler(Filters.text & ~Filters.command, broadcast_send)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
))

# CallbackQueryHandlers для inline-кнопок
dp.add_handler(CallbackQueryHandler(category_selected, pattern='^cat_'))
dp.add_handler(CallbackQueryHandler(add_to_cart, pattern='^add_'))
dp.add_handler(CallbackQueryHandler(remove_from_cart, pattern='^remove_'))
dp.add_handler(CallbackQueryHandler(place_order, pattern='^order$'))

# Неизвестные команды и текст
dp.add_handler(MessageHandler(Filters.command, unknown))
dp.add_handler(MessageHandler(Filters.text, unknown))

# Запуск бота
if __name__ == '__main__':
    print("Бот запущен...")
    updater.start_polling()
    updater.idle()

