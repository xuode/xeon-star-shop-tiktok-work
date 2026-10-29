import asyncio
import os
import random
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from html import escape

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, FSInputFile
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

# ================== CONFIG ==================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB_PATH = BASE_DIR / "stars.db"

MIN_WITHDRAW = 500
REFERRAL_REWARD = 10
START_BONUS = 10
DAILY_GAME_LIMIT = 10

GAME_WIN_REWARD = 10

IMAGE_FILE = os.getenv("IMAGE_FILE", "").strip()

EMOJI = {
    "star": "6028338546736107668",
    "person": "6035084557378654059",
    "people": "6032594876506312598",
    "calendar": "5890937706803894250",
    "cross": "5774077015388852135",
    "warning": "6030563507299160824",
    "money": "5769126056262898415",
    "id": "6028435952299413210",
    "game": "5938413566624272793",
    "question": "6030848053177486888",
    "link": "6028171274939797252",
    "left": "6039539366177541657",
    "right": "6037622221625626773",
    "check": "5774022692642492953",
    "support": "6032742198179532882",   
}


def em(name, fallback="⭐"):
    emoji_id = EMOJI.get(name)
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def stars(amount):
    return f'{em("star")} <b>{amount}</b>'


if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Проверь файл .env.")

if not ADMIN_ID:
    raise RuntimeError("Не указан ADMIN_ID. Укажи свой Telegram ID в .env.")

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()


# ================== IMAGE ==================

def find_image():
    if IMAGE_FILE:
        path = BASE_DIR / IMAGE_FILE
        return path if path.is_file() else None

    extensions = {".jpg", ".jpeg", ".png", ".webp"}

    for path in BASE_DIR.iterdir():
        if path.suffix.lower() in extensions:
            if path.name.lower() not in {"qr.png", "qr.jpg", "qr.jpeg"}:
                return path

    return None


async def send_screen(message, text, keyboard=None):
    image_path = find_image()

    if image_path:
        try:
            await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=text,
                reply_markup=keyboard
            )
            return
        except TelegramBadRequest:
            pass

    await message.answer(text, reply_markup=keyboard)


async def edit_screen(callback, text, keyboard=None):
    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text,
                reply_markup=keyboard
            )
        else:
            await callback.message.edit_text(
                text=text,
                reply_markup=keyboard
            )
    except TelegramBadRequest:
        await send_screen(callback.message, text, keyboard)


# ================== DATABASE ==================

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            balance INTEGER NOT NULL DEFAULT 0,
            earned INTEGER NOT NULL DEFAULT 0,
            game_points INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            task_type TEXT NOT NULL,
            target TEXT NOT NULL,
            reward INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS completed (
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            completed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, task_id)
        );

        CREATE TABLE IF NOT EXISTS referrals (
            inviter_id INTEGER NOT NULL,
            invited_id INTEGER UNIQUE NOT NULL,
            reward INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS game_daily (
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        );
        """)


def now_text():
    return datetime.now(timezone.utc).isoformat()


def today_text():
    return datetime.now(timezone.utc).date().isoformat()


def get_user(user_id):
    with db_connect() as db:
        return db.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()


def add_transaction(user_id, amount, kind, description):
    with db_connect() as db:
        db.execute("""
            INSERT INTO transactions
            (user_id, amount, kind, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, amount, kind, description, now_text()))


def change_balance(user_id, amount, kind, description):
    with db_connect() as db:
        db.execute("""
            UPDATE users
            SET balance = balance + ?,
                earned = earned + CASE WHEN ? > 0 THEN ? ELSE 0 END
            WHERE user_id = ?
        """, (amount, amount, amount, user_id))

        db.execute("""
            INSERT INTO transactions
            (user_id, amount, kind, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, amount, kind, description, now_text()))


def register_user(user_id, username, referrer_id=None):
    with db_connect() as db:
        existing = db.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if existing:
            db.execute(
                "UPDATE users SET username = ? WHERE user_id = ?",
                (username or "", user_id)
            )
            return False

        db.execute("""
            INSERT INTO users
            (user_id, username, balance, earned, game_points, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (user_id, username or "", START_BONUS, START_BONUS, now_text()))

        db.execute("""
            INSERT INTO transactions
            (user_id, amount, kind, description, created_at)
            VALUES (?, ?, 'bonus', 'Бонус за регистрацию', ?)
        """, (user_id, START_BONUS, now_text()))

        if (
            referrer_id
            and referrer_id != user_id
            and db.execute(
                "SELECT user_id FROM users WHERE user_id = ?",
                (referrer_id,)
            ).fetchone()
        ):
            db.execute("""
                INSERT OR IGNORE INTO referrals
                (inviter_id, invited_id, reward, created_at)
                VALUES (?, ?, ?, ?)
            """, (referrer_id, user_id, REFERRAL_REWARD, now_text()))

            inserted = db.execute("SELECT changes()").fetchone()[0]

            if inserted:
                db.execute("""
                    UPDATE users
                    SET balance = balance + ?, earned = earned + ?
                    WHERE user_id = ?
                """, (REFERRAL_REWARD, REFERRAL_REWARD, referrer_id))

                db.execute("""
                    INSERT INTO transactions
                    (user_id, amount, kind, description, created_at)
                    VALUES (?, ?, 'referral', 'Бонус за приглашение', ?)
                """, (referrer_id, REFERRAL_REWARD, now_text()))

    return True


# ================== KEYBOARDS ==================

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Задания",icon_custom_emoji_id="5890937706803894250",
                callback_data="tasks",
                style="primary"
            ),
            InlineKeyboardButton(
                text="Игры",icon_custom_emoji_id="5938413566624272793",
                callback_data="games",
                style="primary"
            )
        ],
        [
            InlineKeyboardButton(
                text="Мой профиль",icon_custom_emoji_id="6035084557378654059",
                callback_data="profile",
                style="primary"
            ),
            InlineKeyboardButton(
                text="Рефералы",icon_custom_emoji_id="6032594876506312598",
                callback_data="referrals",
                style="primary"
            )
        ],
        [
            InlineKeyboardButton(
                text="Вывести средства",icon_custom_emoji_id="5769126056262898415",
                callback_data="withdraw",
                style="success"
            )
        ],
        [
            InlineKeyboardButton(
                text="Поддержка",icon_custom_emoji_id="6032742198179532882",
                url="https://t.me/StarlySuport",
                style="danger"
            )
        ]
        
    ])


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="← В главное меню",
            callback_data="home",
            style="danger"
        )]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Создать задание",
            callback_data="admin_add_task",
            style="primary"
        )],
        [InlineKeyboardButton(
            text="Заявки на вывод",
            callback_data="admin_withdrawals",
            style="primary"
        )],
        [InlineKeyboardButton(
            text="Статистика",
            callback_data="admin_stats",
            style="primary"
        )],
        [InlineKeyboardButton(
            text="← В главное меню",
            callback_data="home",
            style="danger"
        )]
    ])

# ================== TEXTS ==================

def home_text(user_id):
    user = get_user(user_id)
    balance = user["balance"] if user else 0

    return (
        f'{em("star")} <b>S T A R   Q U E S T</b> {em("star")}\n'
        '<i>Путь к наградам начинается с первого клика.</i>\n\n'
        f'{em("calendar")} Выполняй задания\n'
        f'{em("game")} Испытай удачу в играх\n'
        f'{em("people")} Зови друзей — получай бонусы\n\n'
        f'{em("star")} Твой баланс: {stars(balance)}\n'
        f'{em("right")} <b>Что будем делать?</b>'
    )


# ================== STATES ==================

class TaskCreation(StatesGroup):
    title = State()
    task_type = State()
    target = State()
    reward = State()


class WithdrawalState(StatesGroup):
    amount = State()


# ================== START ==================

@dp.message(CommandStart())
async def start(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    referrer_id = None

    if len(parts) == 2:
        try:
            referrer_id = int(parts[1])
        except ValueError:
            pass

    is_new = register_user(
        message.from_user.id,
        message.from_user.username,
        referrer_id
    )

    if is_new:
        try:
            await message.answer(
                f'{em("star")} <b>Добро пожаловать в STAR QUEST!</b>\n\n'
                f'{em("star")} Тебе начислено <b>{START_BONUS} Stars</b> за регистрацию!\n'
                'Это твой стартовый капитал — вперёд к наградам!'
            )
        except Exception:
            pass

    await send_screen(
        message,
        home_text(message.from_user.id),
        main_keyboard()
    )


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(f'{em("cross")} Нет доступа.')
        return

    await message.answer(
        f'{em("question")} <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n'
        'Выбери действие:',
        reply_markup=admin_keyboard()
    )


# ================== HOME / PROFILE ==================

@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    await callback.answer()
    await edit_screen(
        callback,
        home_text(callback.from_user.id),
        main_keyboard()
    )


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    await callback.answer()
    user = get_user(callback.from_user.id)

    with db_connect() as db:
        completed_count = db.execute(
            "SELECT COUNT(*) FROM completed WHERE user_id = ?",
            (callback.from_user.id,)
        ).fetchone()[0]

        referral_count = db.execute(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id = ?",
            (callback.from_user.id,)
        ).fetchone()[0]

        pending = db.execute("""
            SELECT COUNT(*) FROM withdrawals
            WHERE user_id = ? AND status = 'pending'
        """, (callback.from_user.id,)).fetchone()[0]

    username = (
        f'@{escape(callback.from_user.username)}'
        if callback.from_user.username else 'не указан'
    )

    text = (
        f'{em("person")} <b>ТВОЙ ПРОФИЛЬ</b>\n'
        f'{em("id")} <b>ID:</b> <code>{callback.from_user.id}</code>\n'
        f'{em("person")} <b>Username:</b> {username}\n\n'
        f'{em("star")} <b>Баланс:</b> {user["balance"]} Stars\n'
        f'{em("calendar")} <b>Всего заработано:</b> {user["earned"]} Stars\n'
        f'{em("calendar")} <b>Выполнено заданий:</b> {completed_count}\n'
        f'{em("people")} <b>Приглашено друзей:</b> {referral_count}\n'
        f'{em("calendar")} <b>Заявок в обработке:</b> {pending}\n'
    )

    await edit_screen(callback, text, back_keyboard())


# ================== REFERRALS ==================

@dp.callback_query(F.data == "referrals")
async def referrals(callback: CallbackQuery):
    await callback.answer()

    user_id = callback.from_user.id
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={user_id}"

    with db_connect() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id = ?",
            (user_id,)
        ).fetchone()[0]

    text = (
        f'{em("people")} <b>РЕФЕРАЛЬНАЯ ПРОГРАММА</b>\n'
        f'{em("people")} Зови друзей — и получай за каждого\n'
        f'<b>{REFERRAL_REWARD} внутренних Stars</b> на баланс!\n\n'
        f'{em("people")} Уже приглашено: <b>{count}</b>\n\n'
        f'{em("link")} <b>Твоя ссылка:</b>\n'
        f'<code>{link}</code>\n'
        '<i>Чем больше друзей — тем больше наград!</i>'
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=' Поделиться ссылкой',
            url=(
                "https://t.me/share/url?"
                f"url={link}&text=Присоединяйся%20к%20STAR%20QUEST"
            ),
            style="primary"
        )],
        [InlineKeyboardButton(
            text="← Главное меню",
            callback_data="home",
            style="danger"
        )]
    ])

    await edit_screen(callback, text, keyboard)


# ================== TASKS ==================

@dp.callback_query(F.data == "tasks")
async def tasks_menu(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    with db_connect() as db:
        rows = db.execute("""
            SELECT t.* FROM tasks t
            WHERE t.active = 1
            AND NOT EXISTS (
                SELECT 1 FROM completed c
                WHERE c.task_id = t.id AND c.user_id = ?
            )
            ORDER BY t.id DESC
        """, (user_id,)).fetchall()

    if not rows:
        await edit_screen(
            callback,
            f'{em("calendar")} <b>ЗАДАНИЯ</b>\n'
            f'{em("calendar")} Пока новых заданий нет.\n'
            f'Загляни чуть позже — скоро что-то появится! {em("question")}',
            back_keyboard()
        )
        return

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f'{row["title"]} · +{row["reward"]}',
                callback_data=f'task_{row["id"]}',
                style="primary"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="← Главное меню",
            callback_data="home",
            style="danger"
        )
    ])

    await edit_screen(
        callback,
        f'{em("calendar")} <b>ДОСТУПНЫЕ ЗАДАНИЯ</b>\n'
        f'{em("game")} Выбирай задание, выполняй — и получай награду!\n\n'
        f'{em("right")} <b>Список заданий:</b>',
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.callback_query(F.data.startswith("task_"))
async def open_task(callback: CallbackQuery):
    await callback.answer()

    try:
        task_id = int(callback.data.split("_", 1)[1])
    except ValueError:
        return

    with db_connect() as db:
        task = db.execute(
            "SELECT * FROM tasks WHERE id = ? AND active = 1",
            (task_id,)
        ).fetchone()

        completed = db.execute("""
            SELECT 1 FROM completed
            WHERE user_id = ? AND task_id = ?
        """, (callback.from_user.id, task_id)).fetchone()

    if not task or completed:
        await edit_screen(
            callback,
            f'{em("cross")} Это задание больше недоступно.',
            back_keyboard()
        )
        return

    if task["task_type"] == "channel":
        description = (
            '1⃣ Открой канал по кнопке ниже\n'
            '2⃣ Подпишись на него\n'
            f'3⃣ Вернись и нажми «Проверить подписку» {em("check")}'
        )
    else:
        description = (
            f'{em("link")} Открой ссылку и выполни условия задания.\n\n'
            '<i>Автоматическая проверка для ссылок на сторонние '
            'боты/сайты обычно недоступна.</i>'
        )

    buttons = [[
        InlineKeyboardButton(
            text="Открыть задание",
            url=task["target"],
            style="primary"
        )
    ]]

    if task["task_type"] == "channel":
        buttons.append([
            InlineKeyboardButton(
                text="Проверить подписку",
                callback_data=f'check_{task_id}',
                style="success"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="← К заданиям",
            callback_data="tasks",
            style="danger"
        )
    ])

    await edit_screen(
        callback,
        f'{em("star")} <b>{escape(task["title"])}</b>\n'
        f'{description}\n\n'
        f'{em("star")} <b>Награда:</b> +{task["reward"]} Stars',
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.callback_query(F.data.startswith("check_"))
async def check_task(callback: CallbackQuery):
    await callback.answer()

    try:
        task_id = int(callback.data.split("_", 1)[1])
    except ValueError:
        return

    with db_connect() as db:
        task = db.execute("""
            SELECT * FROM tasks
            WHERE id = ? AND active = 1 AND task_type = 'channel'
        """, (task_id,)).fetchone()

        completed = db.execute("""
            SELECT 1 FROM completed
            WHERE user_id = ? AND task_id = ?
        """, (callback.from_user.id, task_id)).fetchone()

    if not task:
        await callback.message.answer("Задание не найдено.")
        return

    if completed:
        await callback.message.answer("Это задание уже выполнено.")
        return

    target = task["target"].strip()

    if target.startswith("https://t.me/"):
        channel = "@" + target.rstrip("/").split("/")[-1]
    else:
        channel = target

    try:
        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=callback.from_user.id
        )

        if member.status not in ("member", "administrator", "creator"):
            await callback.message.answer(
                f'{em("warning")} <b>Подписка не найдена.</b>\n\n'
                f'Подпишись на канал и попробуй снова {em("right")}'
            )
            return

    except Exception:
        await callback.message.answer(
            f'{em("warning")} <b>Не удалось проверить подписку.</b>\n\n'
            'Убедись, что бот добавлен администратором канала, '
            'а ссылка на канал указана правильно.'
        )
        return

    with db_connect() as db:
        db.execute("""
            INSERT OR IGNORE INTO completed
            (user_id, task_id, completed_at)
            VALUES (?, ?, ?)
        """, (callback.from_user.id, task_id, now_text()))

        inserted = db.execute("SELECT changes()").fetchone()[0]

        if inserted:
            db.execute("""
                UPDATE users
                SET balance = balance + ?, earned = earned + ?
                WHERE user_id = ?
            """, (
                task["reward"], task["reward"], callback.from_user.id
            ))

            db.execute("""
                INSERT INTO transactions
                (user_id, amount, kind, description, created_at)
                VALUES (?, ?, 'task', ?, ?)
            """, (
                callback.from_user.id,
                task["reward"],
                f'Задание: {task["title"]}',
                now_text()
            ))

    await callback.message.answer(
        f'{em("star")} <b>Отлично! Подписка подтверждена!</b>\n'
        f'{em("money")} Начислено: <b>+{task["reward"]} Stars</b>\n\n'
        f'Так держать! {em("right")}',
        reply_markup=back_keyboard()
    )


# ================== GAMES ==================

@dp.callback_query(F.data == "games")
async def games_menu(callback: CallbackQuery):
    await callback.answer()

    with db_connect() as db:
        row = db.execute("""
            SELECT attempts FROM game_daily
            WHERE user_id = ? AND day = ?
        """, (callback.from_user.id, today_text())).fetchone()

    used = row["attempts"] if row else 0

    text = (
        f'{em("game")} <b>АРЕНА ИГР</b>\n'
        f'{em("star")} Испытай удачу и найди счастливый подарок!\n\n'
        f'{em("star")} <b>Награда за победу:</b> +{GAME_WIN_REWARD} игровых очков\n'
        f'{em("game")} <b>Поле:</b> 3 × 3\n'
        f'{em("star")} <b>Попытки сегодня:</b> {DAILY_GAME_LIMIT - used}/{DAILY_GAME_LIMIT}\n\n'
        f'{em("right")} <i>Выбирай игру и попробуй победить!</i>'
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Открыть «Мины»",
            callback_data="mines",
            style="danger"
        )],
        [InlineKeyboardButton(
            text="← Назад в меню",
            callback_data="home",
            style="danger"
        )]
    ])

    await edit_screen(callback, text, keyboard)

    await edit_screen(callback, text, keyboard)


@dp.callback_query(F.data == "mines")
async def start_mines(callback: CallbackQuery):
    await callback.answer()

    user_id = callback.from_user.id
    day = today_text()

    with db_connect() as db:
        row = db.execute("""
            SELECT attempts FROM game_daily
            WHERE user_id = ? AND day = ?
        """, (user_id, day)).fetchone()

        used = row["attempts"] if row else 0

        if used >= DAILY_GAME_LIMIT:
            await edit_screen(
                callback,
                f'{em("warning")} <b>Лимит попыток исчерпан.</b>\n'
                f'{em("calendar")} Возвращайся завтра — удача любит терпеливых!',
                back_keyboard()
            )
            return

        db.execute("""
            INSERT INTO game_daily (user_id, day, attempts)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, day)
            DO UPDATE SET attempts = attempts + 1
        """, (user_id, day))

    winning_cell = random.randrange(9)

    buttons = []
    for r in range(3):
        buttons.append([
            InlineKeyboardButton(
                text="Выбрать",
                callback_data=f'mine_{winning_cell}_{r * 3 + c}',
                style="primary"
            )
            for c in range(3)
        ])

    buttons.append([
        InlineKeyboardButton(
            text="← Назад",
            callback_data="games",
            style="danger"
        )
    ])

    await edit_screen(
        callback,
        f'{em("star")} <b>ВЫБЕРИ СЧАСТЛИВЫЙ ПОДАРОК</b>\n'
        f'{em("game")} Одна из девяти ячеек — выигрышная.\n'
        f'{em("star")} Каждая попытка бесплатная!\n\n'
        f'{em("right")} <i>Твой ход:</i>',
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.callback_query(F.data.startswith("mine_"))
async def mine_result(callback: CallbackQuery):
    await callback.answer()

    try:
        _, winning_cell, selected_cell = callback.data.split("_")
        winning_cell = int(winning_cell)
        selected_cell = int(selected_cell)
    except (ValueError, TypeError):
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    if selected_cell == winning_cell:
        with db_connect() as db:
            db.execute("""
                UPDATE users SET game_points = game_points + ?
                WHERE user_id = ?
            """, (GAME_WIN_REWARD, callback.from_user.id))

        result = (
            f'{em("star")} <b>ПОБЕДА!</b> {em("star")}\n'
            f'{em("star")} Ты нашёл счастливый подарок!\n'
            f'{em("money")} <b>+{GAME_WIN_REWARD} игровых очков</b>'
        )
    else:
        result = (
            f'{em("cross")} <b>В этот раз без выигрыша...</b>\n'
            f'{em("star")} Не расстраивайся — попробуй ещё раз!'
        )

    with db_connect() as db:
        user = db.execute(
            "SELECT game_points FROM users WHERE user_id = ?",
            (callback.from_user.id,)
        ).fetchone()

        row = db.execute("""
            SELECT attempts FROM game_daily
            WHERE user_id = ? AND day = ?
        """, (callback.from_user.id, today_text())).fetchone()

    used = row["attempts"] if row else 0
    points = user["game_points"] if user else 0

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Играть ещё",
            callback_data="mines",
            style="primary"
        )],
        [InlineKeyboardButton(
            text="← В игры",
            callback_data="games",
            style="danger"
        )]
    ])

    await callback.message.answer(
        f'{result}\n\n'
        '━━━━━━━━━━━━━━━━━━━━\n'
        f'{em("star")} Игровые очки: <b>{points}</b>\n'
        f'{em("star")} Попыток сегодня: <b>{used}/{DAILY_GAME_LIMIT}</b>',
        reply_markup=keyboard
    )


# ================== WITHDRAWALS ==================

@dp.callback_query(F.data == "withdraw")
async def withdraw_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    user = get_user(callback.from_user.id)

    text = (
        f'{em("money")} <b>ВЫВОД СРЕДСТВ</b>\n'
        f'{em("star")} Твой баланс: <b>{user["balance"]} Stars</b>\n'
        f'{em("calendar")} Минимальная сумма: <b>{MIN_WITHDRAW} Stars</b>\n\n'
        f'{em("calendar")} Обработка заявки — до 24 часов.\n\n'
        f'{em("right")} <b>Введи сумму для вывода:</b>'
    )

    await state.set_state(WithdrawalState.amount)
    await edit_screen(callback, text, back_keyboard())


@dp.message(WithdrawalState.amount)
async def withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = int((message.text or "").strip())
    except ValueError:
        await message.answer(f"{em('warning')} Введи сумму целым числом.")
        return

    if amount < MIN_WITHDRAW:
        await message.answer(
            f'{em("warning")} Минимальная сумма вывода — <b>{MIN_WITHDRAW} Stars</b>.'
        )
        return

    user_id = message.from_user.id

    with db_connect() as db:
        user = db.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if not user or user["balance"] < amount:
            await message.answer(
                f'{em("cross")} <b>Недостаточно Stars на балансе.</b>'
            )
            return

        db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id)
        )

        cur = db.execute("""
            INSERT INTO withdrawals (user_id, amount, status, created_at)
            VALUES (?, ?, 'pending', ?)
        """, (user_id, amount, now_text()))

        withdrawal_id = cur.lastrowid

        db.execute("""
            INSERT INTO transactions
            (user_id, amount, kind, description, created_at)
            VALUES (?, ?, 'withdrawal', ?, ?)
        """, (user_id, -amount, f'Заявка #{withdrawal_id}', now_text()))

    await state.clear()

    await message.answer(
        f'{em("star")} <b>Заявка #{withdrawal_id} создана!</b>\n'
        f'{em("money")} Сумма: <b>{amount} Stars</b>\n'
        f'{em("calendar")} Обработка — до 24 часов.\n\n'
        f'{em("check")} Спасибо, что с нами!',
        reply_markup=main_keyboard()
    )

    with db_connect() as db:
        completed_count = db.execute(
            "SELECT COUNT(*) FROM completed WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]

        referral_count = db.execute(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id = ?",
            (user_id,)
        ).fetchone()[0]

        user = db.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    admin_text = (
        f'{em("money")} <b>НОВАЯ ЗАЯВКА НА ВЫВОД</b>\n'
        f'{em("link")} Заявка: <b>#{withdrawal_id}</b>\n'
        f'{em("person")} Пользователь: {user_id}\n'
        f'{em("link")} Username: @{escape(user["username"] or "не указан")}\n'
        f'{em("money")} Сумма: <b>{amount} Stars</b>\n'
        f'{em("star")} Баланс после: {user["balance"]}\n'
        f'{em("calendar")} Всего заработано: {user["earned"]}\n'
        f'{em("calendar")} Заданий выполнено: {completed_count}\n'
        f'{em("people")} Рефералов: {referral_count}\n'
        f'{em("calendar")} В боте с: {escape(user["created_at"])}\n'
        f'{em("calendar")} Время заявки: {now_text()}'
    )

    admin_buttons = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Отметить выплаченным",
                callback_data=f'paid_{withdrawal_id}',
                style="success"
            )
        ],
        [
            InlineKeyboardButton(
                text="Вернуть на баланс",
                callback_data=f'refund_{withdrawal_id}',
                style="danger"
            )
        ]
    ])

    try:
        await bot.send_message(
            ADMIN_ID,
            admin_text,
            reply_markup=admin_buttons
        )
    except Exception:
        pass


# ================== ADMIN: TASK CREATION ==================

@dp.callback_query(F.data == "admin_add_task")
async def admin_add_task(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    if callback.from_user.id != ADMIN_ID:
        return

    await state.set_state(TaskCreation.title)
    await callback.message.answer(f"{em('link')} Введи название задания:")


@dp.message(TaskCreation.title)
async def task_title(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    title = (message.text or "").strip()

    if not title or len(title) > 100:
        await message.answer(f"{em('warning')} Название: от 1 до 100 символов.")
        return

    await state.update_data(title=title)
    await state.set_state(TaskCreation.task_type)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Подписка на канал",
            callback_data="type_channel",
            style="primary"
        )],
        [InlineKeyboardButton(
            text="Ссылка на бота / сайт",
            callback_data="type_link",
            style="primary"
        )]
    ])

    await message.answer(f"{em('game')} Выбери тип задания:", reply_markup=keyboard)


@dp.callback_query(F.data.in_({"type_channel", "type_link"}))
async def task_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    if callback.from_user.id != ADMIN_ID:
        return

    task_type_value = "channel" if callback.data == "type_channel" else "link"

    await state.update_data(task_type=task_type_value)
    await state.set_state(TaskCreation.target)

    if task_type_value == "channel":
        prompt = (
            f"{em('link')} Отправь ссылку вида <code>https://t.me/channelname</code> "
            "или <code>@channelname</code>.\n\n"
            f"{em('warning')} Бот должен быть администратором канала."
        )
    else:
        prompt = f"{em('link')} Отправь полную ссылку на бота или сайт."

    await callback.message.answer(prompt)


@dp.message(TaskCreation.target)
async def task_target(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    target = (message.text or "").strip()
    data = await state.get_data()

    if data["task_type"] == "channel":
        if not (target.startswith("@") or target.startswith("https://t.me/")):
            await message.answer(
                f"{em('warning')} Нужна ссылка https://t.me/channelname или @channelname."
            )
            return
    elif not target.startswith(("https://", "http://")):
        await message.answer(f"{em('warning')} Введи полную ссылку (https://...).")
        return

    await state.update_data(target=target)
    await state.set_state(TaskCreation.reward)
    await message.answer(f"{em('money')} Введи награду за задание (целое число Stars):")


@dp.message(TaskCreation.reward)
async def task_reward(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        reward = int((message.text or "").strip())
    except ValueError:
        await message.answer(f"{em('warning')} Введи целое число.")
        return

    if reward < 1 or reward > 100000:
        await message.answer(f"{em('warning')} Награда: от 1 до 100000 Stars.")
        return

    data = await state.get_data()

    with db_connect() as db:
        cur = db.execute("""
            INSERT INTO tasks (title, task_type, target, reward, active)
            VALUES (?, ?, ?, ?, 1)
        """, (data["title"], data["task_type"], data["target"], reward))
        task_id = cur.lastrowid

        user_ids = [
            row["user_id"]
            for row in db.execute("SELECT user_id FROM users").fetchall()
        ]

    await state.clear()

    await message.answer(
        f'{em("star")} <b>Задание #{task_id} создано!</b>\n'
        f'{em("link")} Название: <b>{escape(data["title"])}</b>\n'
        f'{em("money")} Награда: <b>{reward} Stars</b>',
        reply_markup=admin_keyboard()
    )

    for uid in user_ids:
        try:
            await bot.send_message(
                uid,
                f'{em("calendar")} <b>НОВОЕ ЗАДАНИЕ!</b>\n'
                f'{em("link")} {escape(data["title"])}\n'
                f'{em("money")} Награда: <b>+{reward} Stars</b>\n\n'
                f'{em("right")} Открой раздел «Задания», чтобы посмотреть условия.'
            )
        except Exception:
            pass

        await asyncio.sleep(0.03)


# ================== ADMIN: WITHDRAWALS ==================

@dp.callback_query(F.data == "admin_withdrawals")
async def admin_withdrawals(callback: CallbackQuery):
    await callback.answer()

    if callback.from_user.id != ADMIN_ID:
        return

    with db_connect() as db:
        rows = db.execute("""
            SELECT * FROM withdrawals
            WHERE status = 'pending'
            ORDER BY id DESC LIMIT 30
        """).fetchall()

    if not rows:
        await edit_screen(
            callback,
            f'{em("star")} <b>Заявок нет.</b>\n\nВсе заявки на вывод обработаны!',
            admin_keyboard()
        )
        return

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f'Заявка #{row["id"]} · {row["amount"]} Stars',
                callback_data=f'view_{row["id"]}',
                style="primary"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="В админ-панель",
            callback_data="admin",
            style="danger"
        )
    ])

    await edit_screen(
        callback,
        f'{em("money")} <b>ЗАЯВКИ НА ВЫВОД</b>\n'
        'Выбери заявку для деталей:',
        InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.callback_query(F.data.startswith("view_"))
async def view_withdrawal(callback: CallbackQuery):
    await callback.answer()

    if callback.from_user.id != ADMIN_ID:
        return

    try:
        withdrawal_id = int(callback.data.split("_", 1)[1])
    except ValueError:
        return

    with db_connect() as db:
        row = db.execute(
            "SELECT * FROM withdrawals WHERE id = ?",
            (withdrawal_id,)
        ).fetchone()

    if not row:
        await callback.message.answer("Заявка не найдена.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Отметить выплаченным",
            callback_data=f'paid_{withdrawal_id}',
            style="success"
        )],
        [InlineKeyboardButton(
            text="Вернуть на баланс",
            callback_data=f'refund_{withdrawal_id}',
            style="danger"
        )],
        [InlineKeyboardButton(
            text="К заявкам",
            callback_data="admin_withdrawals",
            style="danger"
        )]
    ])

    await edit_screen(
        callback,
        f'{em("link")} <b>Заявка #{row["id"]}</b>\n'
        f'{em("person")} Пользователь: <code>{row["user_id"]}</code>\n'
        f'{em("money")} Сумма: <b>{row["amount"]} Stars</b>\n'
        f'{em("link")} Статус: <b>{escape(row["status"])}</b>\n'
        f'{em("calendar")} Создана: {escape(row["created_at"])}',
        keyboard
    )


@dp.callback_query(F.data.startswith("paid_"))
async def withdrawal_paid(callback: CallbackQuery):
    await callback.answer()

    if callback.from_user.id != ADMIN_ID:
        return

    withdrawal_id = int(callback.data.split("_", 1)[1])

    with db_connect() as db:
        row = db.execute(
            "SELECT * FROM withdrawals WHERE id = ?",
            (withdrawal_id,)
        ).fetchone()

        if not row or row["status"] != "pending":
            await callback.message.answer(
                f"{em('warning')} Заявка уже обработана или не найдена."
            )
            return

        db.execute("""
            UPDATE withdrawals SET status = 'paid'
            WHERE id = ? AND status = 'pending'
        """, (withdrawal_id,))

    await callback.message.answer(
        f'{em("check")} Заявка #{withdrawal_id} отмечена как выплаченная.\n\n'
        '<i>Фактический перевод денег необходимо выполнить отдельно.</i>'
    )

    try:
        await bot.send_message(
            row["user_id"],
            f'{em("star")} <b>Заявка #{withdrawal_id} выплачена!</b>\n\n'
            f'Спасибо, что был с нами! {em("star")}'
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("refund_"))
async def withdrawal_refund(callback: CallbackQuery):
    await callback.answer()

    if callback.from_user.id != ADMIN_ID:
        return

    withdrawal_id = int(callback.data.split("_", 1)[1])

    with db_connect() as db:
        row = db.execute(
            "SELECT * FROM withdrawals WHERE id = ?",
            (withdrawal_id,)
        ).fetchone()

        if not row or row["status"] != "pending":
            await callback.message.answer(
                f"{em('warning')} Заявка уже обработана или не найдена."
            )
            return

        db.execute("""
            UPDATE withdrawals SET status = 'refunded'
            WHERE id = ? AND status = 'pending'
        """, (withdrawal_id,))

        db.execute("""
            UPDATE users SET balance = balance + ?
            WHERE user_id = ?
        """, (row["amount"], row["user_id"]))

        db.execute("""
            INSERT INTO transactions
            (user_id, amount, kind, description, created_at)
            VALUES (?, ?, 'refund', ?, ?)
        """, (
            row["user_id"],
            row["amount"],
            f'Возврат заявки #{withdrawal_id}',
            now_text()
        ))

    await callback.message.answer(
        f'{em("left")} Заявка #{withdrawal_id}: сумма возвращена на баланс.'
    )

    try:
        await bot.send_message(
            row["user_id"],
            f'{em("star")} <b>Заявка #{withdrawal_id} отменена.</b>\n\n'
            f'{em("money")} На баланс возвращено: <b>{row["amount"]} Stars</b>.'
        )
    except Exception:
        pass


# ================== ADMIN: STATS ==================

@dp.callback_query(F.data == "admin")
async def admin_home(callback: CallbackQuery):
    await callback.answer()

    if callback.from_user.id != ADMIN_ID:
        return

    await edit_screen(
        callback,
        f'{em("question")} <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n'
        'Выбери действие:',
        admin_keyboard()
    )


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    await callback.answer()

    if callback.from_user.id != ADMIN_ID:
        return

    with db_connect() as db:
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        tasks = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE active = 1"
        ).fetchone()[0]
        completed = db.execute(
            "SELECT COUNT(*) FROM completed"
        ).fetchone()[0]
        referrals = db.execute(
            "SELECT COUNT(*) FROM referrals"
        ).fetchone()[0]
        pending = db.execute("""
            SELECT COUNT(*) FROM withdrawals
            WHERE status = 'pending'
        """).fetchone()[0]
        total_balance = db.execute(
            "SELECT COALESCE(SUM(balance), 0) FROM users"
        ).fetchone()[0]

    text = (
        f'{em("calendar")} <b>СТАТИСТИКА БОТА</b>\n'
        f'{em("people")} Пользователей: <b>{users}</b>\n'
        f'{em("link")} Активных заданий: <b>{tasks}</b>\n'
        f'{em("check")} Выполнений заданий: <b>{completed}</b>\n'
        f'{em("people")} Приглашений: <b>{referrals}</b>\n'
        f'{em("calendar")} Заявок в ожидании: <b>{pending}</b>\n'
        f'{em("star")} Суммарный баланс: <b>{total_balance} Stars</b>\n'
    )

    await edit_screen(callback, text, admin_keyboard())


# ================== RUN ==================

async def main():
    init_db()
    print("STAR QUEST bot is starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
