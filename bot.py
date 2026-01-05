import pandas as pd
from databases import Database
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import asyncio
import datetime
import shutil
import json

load_dotenv()

bot = Bot(token=os.getenv("BOT_TOKEN"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

ADMIN_IDS = [int(uid) for uid in os.getenv("ADMIN_IDS", "").split(",") if uid.strip()]  # Список админов

# PostgreSQL connection
DATABASE_URL = f"postgresql://{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', '')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'orders_db')}"
db = Database(DATABASE_URL)

async def init_db():
    await db.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            user_id BIGINT,
            username TEXT,
            day TEXT,
            dish TEXT,
            quantity INTEGER DEFAULT 1,
            UNIQUE(user_id, day, dish)
        )
    """)
    
    await db.execute("""
        CREATE TABLE IF NOT EXISTS menu (
            day TEXT PRIMARY KEY,
            dishes TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def load_menu():
    """Загружает меню из базы данных"""
    # Эта функция будет использоваться асинхронно, поэтому создаём её для совместимости
    # Реальная загрузка происходит в async функциях через db.fetch_all()
    return {}


async def load_menu_from_db():
    """Асинхронная загрузка меню из БД"""
    try:
        rows = await db.fetch_all("SELECT day, dishes FROM menu ORDER BY day")
        menu = {}
        for row in rows:
            menu[row['day']] = json.loads(row['dishes'])
        return menu
    except Exception:
        return {}


@dp.message(Command("start"))
async def start(message: types.Message):
    menu = await load_menu_from_db()
    if not menu:
        await message.answer("Меню пока не загружено.")
        return

    kb = InlineKeyboardBuilder()
    days = list(menu.keys())
    for idx, day in enumerate(days, start=1):
        kb.button(text=day, callback_data=f"day:{idx}")
    kb.adjust(2)
    await message.answer("Выбери день:", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("day:"))
async def select_day(callback: types.CallbackQuery):
    try:
        day_idx = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    menu = await load_menu_from_db()
    days = list(menu.keys())
    if day_idx < 1 or day_idx > len(days):
        await callback.answer("Недействительный день.", show_alert=True)
        return

    day = days[day_idx - 1]
    dishes = menu.get(day, [])

    if not dishes:
        await callback.message.answer("Для этого дня нет блюд.")
        return

    user_orders = await db.fetch_all("SELECT dish, quantity FROM orders WHERE user_id = :user_id AND day = :day", values={"user_id": callback.from_user.id, "day": day})
    user_orders = [f"{row['dish']} x{row['quantity']}" for row in user_orders]

    text = f"----------------------Выбери блюдо на {day}:----------------------"
    if user_orders:
        text = f"Ваши текущие заказы на {day}: " + ", ".join(user_orders) + "\n\n" + text

    kb = InlineKeyboardBuilder()
    for idx, dish in enumerate(dishes):
        kb.button(text=f"➕ {dish}", callback_data=f"cart_add:{day_idx}:{idx}")
    kb.button(text="🧾 Посмотреть корзину", callback_data=f"cart_view:{day_idx}")
    kb.button(text="🗑 Очистить корзину", callback_data=f"cart_clear:{day_idx}")
    kb.button(text="◀️ Назад к выбору дня", callback_data="back_to_days")
    kb.adjust(1)
    await callback.message.answer(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "back_to_days")
async def back_to_days(callback: types.CallbackQuery):
    menu = await load_menu_from_db()
    if not menu:
        await callback.answer("Меню пока не загружено.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    days = list(menu.keys())
    for idx, day in enumerate(days, start=1):
        kb.button(text=day, callback_data=f"day:{idx}")
    kb.adjust(2)
    await callback.message.answer("Выбери день:", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("cart_add:"))
async def cart_add(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return
    _, day_idx_s, dish_idx_s = parts
    try:
        day_idx = int(day_idx_s); dish_idx = int(dish_idx_s)
    except ValueError:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    menu = await load_menu_from_db()
    days = list(menu.keys())
    if day_idx < 1 or day_idx > len(days):
        await callback.answer("Недействительный день.", show_alert=True)
        return
    day = days[day_idx - 1]
    dishes = menu.get(day, [])
    if dish_idx < 0 or dish_idx >= len(dishes):
        await callback.answer("Недействительное блюдо.", show_alert=True)
        return

    dish = dishes[dish_idx].strip()

    row = await db.fetch_one("SELECT quantity FROM orders WHERE user_id = :user_id AND day = :day AND dish = :dish", values={"user_id": callback.from_user.id, "day": day, "dish": dish})
    if row:
        new_q = row['quantity'] + 1
        await db.execute("UPDATE orders SET quantity = :quantity WHERE user_id = :user_id AND day = :day AND dish = :dish", values={"quantity": new_q, "user_id": callback.from_user.id, "day": day, "dish": dish})
    else:
        await db.execute("INSERT INTO orders (user_id, username, day, dish, quantity) VALUES (:user_id, :username, :day, :dish, :quantity)",
                    values={"user_id": callback.from_user.id, "username": callback.from_user.username, "day": day, "dish": dish, "quantity": 1})

    await callback.answer(f"✅ Добавлено: {dish}", show_alert=False)


@dp.callback_query(F.data.startswith("cart_view:"))
async def cart_view(callback: types.CallbackQuery):
    try:
        day_idx = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    menu = await load_menu_from_db()
    days = list(menu.keys())
    if day_idx < 1 or day_idx > len(days):
        await callback.answer("Недействительный день.", show_alert=True)
        return
    day = days[day_idx - 1]
    dishes = menu.get(day, [])

    rows = await db.fetch_all("SELECT dish, quantity FROM orders WHERE user_id = :user_id AND day = :day", values={"user_id": callback.from_user.id, "day": day})

    if not rows:
        await callback.answer("Корзина пуста.", show_alert=True)
        return

    text = f"Ваши заказы на {day}:\n"
    kb = InlineKeyboardBuilder()
    for row in rows:
        dish = row['dish']
        qty = row['quantity']
        text += f"{dish} — {qty} шт.\n"
        try:
            idx = dishes.index(dish)
        except ValueError:
            idx = -1
        if idx >= 0:
            kb.button(text=f"+ {dish[:20]}", callback_data=f"cart_inc:{day_idx}:{idx}")
            kb.button(text=f"- {dish[:20]}", callback_data=f"cart_dec:{day_idx}:{idx}")
    kb.button(text="🧾 Посмотреть корзину", callback_data=f"cart_view:{day_idx}")
    kb.button(text="◀️ Назад к меню", callback_data=f"day:{day_idx}")
    kb.button(text="🗑 Очистить корзину", callback_data=f"cart_clear:{day_idx}")
    kb.adjust(2)
    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("cart_inc:"))
async def cart_inc(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return
    _, day_idx_s, dish_idx_s = parts
    try:
        day_idx = int(day_idx_s); dish_idx = int(dish_idx_s)
    except ValueError:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    menu = await load_menu_from_db()
    days = list(menu.keys())
    if day_idx < 1 or day_idx > len(days):
        await callback.answer("Недействительный день.", show_alert=True)
        return
    day = days[day_idx - 1]
    dishes = menu.get(day, [])
    if dish_idx < 0 or dish_idx >= len(dishes):
        await callback.answer("Недействительное блюдо.", show_alert=True)
        return
    dish = dishes[dish_idx].strip()

    row = await db.fetch_one("SELECT quantity FROM orders WHERE user_id = :user_id AND day = :day AND dish = :dish", values={"user_id": callback.from_user.id, "day": day, "dish": dish})
    if row:
        await db.execute("UPDATE orders SET quantity = :quantity WHERE user_id = :user_id AND day = :day AND dish = :dish", values={"quantity": row['quantity']+1, "user_id": callback.from_user.id, "day": day, "dish": dish})
    await callback.answer("Количество увеличено.", show_alert=False)


@dp.callback_query(F.data.startswith("cart_dec:"))
async def cart_dec(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return
    _, day_idx_s, dish_idx_s = parts
    try:
        day_idx = int(day_idx_s); dish_idx = int(dish_idx_s)
    except ValueError:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    menu = await load_menu_from_db()
    days = list(menu.keys())
    if day_idx < 1 or day_idx > len(days):
        await callback.answer("Недействительный день.", show_alert=True)
        return
    day = days[day_idx - 1]
    dishes = menu.get(day, [])
    if dish_idx < 0 or dish_idx >= len(dishes):
        await callback.answer("Недействительное блюдо.", show_alert=True)
        return
    dish = dishes[dish_idx].strip()

    row = await db.fetch_one("SELECT quantity FROM orders WHERE user_id = :user_id AND day = :day AND dish = :dish", values={"user_id": callback.from_user.id, "day": day, "dish": dish})
    if row:
        if row['quantity'] > 1:
            await db.execute("UPDATE orders SET quantity = :quantity WHERE user_id = :user_id AND day = :day AND dish = :dish", values={"quantity": row['quantity']-1, "user_id": callback.from_user.id, "day": day, "dish": dish})
        else:
            await db.execute("DELETE FROM orders WHERE user_id = :user_id AND day = :day AND dish = :dish", values={"user_id": callback.from_user.id, "day": day, "dish": dish})
    await callback.answer("Количество обновлено.", show_alert=False)


@dp.callback_query(F.data.startswith("cart_clear:"))
async def cart_clear(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    try:
        day_idx = int(parts[1])
    except ValueError:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    menu = load_menu()
    days = list(menu.keys())
    if day_idx < 1 or day_idx > len(days):
        await callback.answer("Недействительный день.", show_alert=True)
        return
    day = days[day_idx - 1]

    target_user = callback.from_user.id

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить очистку", callback_data=f"cart_clear_confirm:{day_idx}:{target_user}")
    kb.button(text="❌ Отмена", callback_data=f"cart_clear_cancel:{day_idx}")
    kb.adjust(2)
    await callback.message.answer(f"Вы действительно хотите очистить корзину на {day}?", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("cart_clear_confirm:"))
async def cart_clear_confirm(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return
    try:
        day_idx = int(parts[1])
        target_user = int(parts[2])
    except ValueError:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    if target_user != callback.from_user.id:
        await callback.answer("У вас нет прав очищать чужую корзину.", show_alert=True)
        return

    menu = load_menu()
    days = list(menu.keys())
    if day_idx < 1 or day_idx > len(days):
        await callback.answer("Недействительный день.", show_alert=True)
        return
    day = days[day_idx - 1]

    await db.execute("DELETE FROM orders WHERE user_id = :user_id AND day = :day", values={"user_id": target_user, "day": day})

    await callback.answer("Ваша корзина очищена.", show_alert=True)


@dp.callback_query(F.data.startswith("cart_clear_cancel:"))
async def cart_clear_cancel(callback: types.CallbackQuery):
    await callback.answer("Отмена.", show_alert=True)


@dp.message(Command("orders_day"))
async def orders_day_command(message: types.Message):
    menu = await load_menu_from_db()
    if not menu:
        await message.answer("Меню не загружено.")
        return

    kb = InlineKeyboardBuilder()
    days = list(menu.keys())
    for idx, day in enumerate(days, start=1):
        kb.button(text=day, callback_data=f"admin_day:{idx}")
    kb.adjust(2)
    await message.answer("Выберите день для просмотра заказов:", reply_markup=kb.as_markup())

@dp.message(Command("report"))
async def report(message: types.Message):
    rows = await db.fetch_all("SELECT day, dish, quantity FROM orders")

    if not rows:
        await message.answer("Заказов пока нет.")
        return

    report_text = ""
    days_dict = {}
    for row in rows:
        day = row['day']
        dish = row['dish']
        quantity = row['quantity']
        if day not in days_dict:
            days_dict[day] = {}
        if dish not in days_dict[day]:
            days_dict[day][dish] = 0
        days_dict[day][dish] += quantity

    for day, dishes_dict in days_dict.items():
        report_text += f"\n📅 *{day}*\n"
        for dish, count in dishes_dict.items():
            report_text += f"{dish}: {int(count)}\n"
    await message.answer(report_text, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("admin_day:"))
async def admin_day_view(callback: types.CallbackQuery):
    try:
        day_idx = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Недействительная кнопка.", show_alert=True)
        return

    menu = await load_menu_from_db()
    days = list(menu.keys())
    if day_idx < 1 or day_idx > len(days):
        await callback.answer("Недействительный день.", show_alert=True)
        return
    day = days[day_idx - 1]

    rows = await db.fetch_all("""
        SELECT user_id, username, dish, SUM(quantity) as qty
        FROM orders
        WHERE day = :day
        GROUP BY user_id, username, dish
        ORDER BY username
    """, values={"day": day})

    totals = await db.fetch_all("""
        SELECT dish, SUM(quantity) as total_qty
        FROM orders
        WHERE day = :day
        GROUP BY dish
        ORDER BY total_qty DESC, dish
    """, values={"day": day})

    if not rows:
        await callback.message.answer(f"Заказов на {day} нет.")
        return

    users = {}
    for row in rows:
        key = (row['user_id'], row['username'] or "")
        if key not in users:
            users[key] = []
        users[key].append((row['dish'], int(row['qty'])))

    text = f"📅 Заказы на {day}:\n\n"
    for (user_id, username), items in users.items():
        user_label = f"@{username}" if username else f"id:{user_id}"
        user_text = f"{user_label} ({user_id}):\n"
        for dish, q in items:
            user_text += f"  - {dish} — {q} шт.\n"
        user_text += "\n"
        
        if len(text) + len(user_text) > 3500:
            await callback.message.answer(text)
            text = user_text
        else:
            text += user_text

    text += "🧾 Общая сводка по блюдам:\n"
    for row in totals:
        text += f"  • {row['dish']}: {int(row['total_qty'])} шт.\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data="admin_back_days")
    kb.adjust(1)
    await callback.message.answer(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_back_days")
async def admin_back_days(callback: types.CallbackQuery):
    menu = await load_menu_from_db()
    kb = InlineKeyboardBuilder()
    days = list(menu.keys())
    for idx, day in enumerate(days, start=1):
        kb.button(text=day, callback_data=f"admin_day:{idx}")
    kb.adjust(2)
    await callback.message.answer("Выберите день для просмотра заказов:", reply_markup=kb.as_markup())


@dp.message(F.document)
async def update_menu(message: types.Message):
    """Обновление меню через любой Excel файл"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для обновления меню.")
        return
    
    file_name = message.document.file_name or ""
    
    # Проверяем расширение файла
    if not file_name.lower().endswith(('.xlsx', '.xls')):
        await message.answer("❌ Пожалуйста, отправьте Excel файл (.xlsx или .xls)")
        return
    
    try:
        file = await bot.get_file(message.document.file_id)
        file_path = f"downloads/{file_name}"

        os.makedirs("downloads", exist_ok=True)
        await bot.download_file(file.file_path, file_path)
        
        # Парсим Excel файл
        xls = pd.ExcelFile(file_path)
        df = pd.read_excel(xls, xls.sheet_names[0], header=None)
        xls.close()
        
        menu_dict = {}
        current_day = None

        for col in df.columns:
            for value in df[col]:
                if isinstance(value, str):
                    val = value.strip()
                    if val.startswith("Меню"):
                        current_day = val.replace("Меню", "").strip()
                        menu_dict[current_day] = []
                    elif current_day and val not in ("Завтрак", "Салаты", "Супы", "супы", "Второе Горячее") and val != "":
                        menu_dict[current_day].append(val)
        
        # Очищаем старое меню и сохраняем новое в БД
        await db.execute("DELETE FROM menu")
        
        for day, dishes in menu_dict.items():
            dishes_json = json.dumps(dishes)
            await db.execute(
                "INSERT INTO menu (day, dishes) VALUES (:day, :dishes)",
                values={"day": day, "dishes": dishes_json}
            )
        
        # Очищаем базу данных заказов
        await db.execute("DELETE FROM orders")

        await message.answer(f"✅ Меню успешно обновлено!\n🗑 База заказов очищена.\n\nДобавлено дней: {len(menu_dict)}")
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении меню: {str(e)}")


@dp.message(Command("update_menu"))
async def update_menu_command(message: types.Message, state: FSMContext):
    """Команда для запуска режима обновления меню"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для выполнения этой команды.")
        return
    
    await state.set_state(MenuUpdate.waiting_for_menu)
    await message.answer(
        "📝 Введите меню в формате:\n\n"
        "<b>Меню Понедельник</b>\n"
        "Салат Цезарь\n"
        "Борщ\n"
        "Котлета\n\n"
        "<b>Меню Вторник</b>\n"
        "Салат Овощной\n"
        "Суп\n"
        "Рыба\n\n"
        "И так далее для каждого дня.\n\n"
        "Используйте <b>Меню [День]</b> для начала нового дня.",
        parse_mode="HTML"
    )


class MenuUpdate(StatesGroup):
    waiting_for_menu = State()


@dp.message(MenuUpdate.waiting_for_menu)
async def process_menu_text(message: types.Message, state: FSMContext):
    """Обрабатывает текстовое меню и сохраняет его в БД"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для обновления меню.")
        return
    
    try:
        menu_text = message.text
        menu_dict = {}
        current_day = None
        
        for line in menu_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('Меню'):
                current_day = line.replace('Меню', '').strip()
                menu_dict[current_day] = []
            elif current_day and line and not line.startswith('Меню'):
                menu_dict[current_day].append(line)
        
        if not menu_dict:
            await message.answer("❌ Не удалось парсить меню. Проверьте формат.")
            return
        
        # Очищаем старое меню и сохраняем новое в БД
        await db.execute("DELETE FROM menu")
        
        for day, dishes in menu_dict.items():
            dishes_json = json.dumps(dishes)
            await db.execute(
                "INSERT INTO menu (day, dishes) VALUES (:day, :dishes)",
                values={"day": day, "dishes": dishes_json}
            )
        
        # Очищаем базу данных заказов
        await db.execute("DELETE FROM orders")
        
        await message.answer(f"✅ Меню успешно обновлено!\n🗑 База заказов очищена.\n\nДобавлено дней: {len(menu_dict)}")
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении меню: {str(e)}")


if __name__ == "__main__":
    async def main():
        await db.connect()
        await init_db()
        try:
            await dp.start_polling(bot)
        finally:
            await db.disconnect()

    asyncio.run(main())
