import html
import logging
import os
import random
import secrets
import sqlite3
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlencode

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ---------------- CONFIG ----------------
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH, encoding="utf-8-sig", override=True)

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL = os.getenv("CHANNEL")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
DONATE_CARD = os.getenv("DONATE_CARD")
DONATE_CARD_OWNER = os.getenv("DONATE_CARD_OWNER")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------- GAME CONSTANTS ----------------
CHOICES = {
    "rock": ("🪨", "سنگ"),
    "paper": ("📄", "کاغذ"),
    "scissors": ("✂️", "قیچی"),
}

# هر کلید، روی کدوم گزینه برد داره
BEATS = {
    "rock": "scissors",
    "scissors": "paper",
    "paper": "rock",
}

# اولین کسی که به این تعداد امتیاز برسه، برنده‌ی بازی میشه
WIN_SCORE = 3


# ---------------- DATABASE ----------------
DB_PATH = Path(__file__).resolve().parent / "stats.db"


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                name    TEXT,
                wins    INTEGER NOT NULL DEFAULT 0,
                losses  INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def record_result(user_id: int, name: str, won: bool) -> None:
    col = "wins" if won else "losses"
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            f"""
            INSERT INTO players (user_id, name, {col}) VALUES (?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE
                SET {col} = {col} + 1, name = excluded.name
            """,
            (user_id, name),
        )


def get_stats(user_id: int):
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(
            "SELECT wins, losses FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()


def get_top(limit: int = 10):
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(
            """
            SELECT name, wins, losses FROM players
            ORDER BY wins DESC, losses ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


# ---------------- MEMBERSHIP ----------------

async def is_member(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL, user_id=user_id)
    except TelegramError as e:
        logger.error("Membership check failed for %s: %s", user_id, e)
        return False

    if member.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    ):
        return True

    if member.status == ChatMemberStatus.RESTRICTED:
        return member.is_member

    return False


# ---------------- KEYBOARDS ----------------
def join_keyboard():
    channel_username = CHANNEL.lstrip("@")

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{channel_username}")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])


def menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 بازی سنگ کاغذ قیچی", callback_data="play")],
        [
            InlineKeyboardButton("📊 آمار من", callback_data="my_stats"),
            InlineKeyboardButton("🏆 برترین‌ها", callback_data="leaderboard"),
        ],
        [InlineKeyboardButton("💖 حمایت از ما", callback_data="support")],
        [InlineKeyboardButton("ℹ️ درباره ما", callback_data="about")],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]
    ])


# ----- support keyboards -----
def support_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 حمایت مالی", callback_data="donate")],
        [InlineKeyboardButton("📢 تبلیغات", callback_data="ads")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def support_back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data="support")]
    ])


def ads_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 ارتباط با ادمین", url=f"https://t.me/{ADMIN_USERNAME}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="support")],
    ])


# ----- game keyboards -----
def play_mode_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 بازی با ربات", callback_data="play_bot")],
        [InlineKeyboardButton("👥 بازی با دوستان", callback_data="play_friend")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def play_back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت به منوی بازی", callback_data="play")]
    ])


def game_keyboard():
    """کیبورد بازی با ربات"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{emoji} {name}", callback_data=f"rps:{key}")
            for key, (emoji, name) in CHOICES.items()
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="play")],
    ])


def play_again_keyboard():
    """کیبورد بعد از تموم شدن بازی با ربات"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 بازی دوباره", callback_data="play_bot")],
        [InlineKeyboardButton("🔙 بازگشت به منوی بازی", callback_data="play")],
    ])


def friend_game_keyboard(gid: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{emoji} {name}", callback_data=f"fr:{gid}:{key}")
            for key, (emoji, name) in CHOICES.items()
        ],
        [InlineKeyboardButton("🚪 پایان بازی", callback_data=f"fr_end:{gid}")],
    ])


def friend_end_keyboard(gid: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚪 پایان بازی", callback_data=f"fr_end:{gid}")]
    ])


def invite_keyboard(gid: str, link: str):
    share_url = "https://t.me/share/url?" + urlencode(
        {"url": link, "text": "بیا سنگ کاغذ قیچی بازی کنیم! 🎮"},
        quote_via=quote,
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 ارسال لینک برای دوست", url=share_url)],
        [InlineKeyboardButton("❌ لغو بازی", callback_data=f"fr_end:{gid}")],
    ])


# ---------------- TEXTS ----------------
WELCOME_TEXT = """
<b>🎮 به ربات ما خوش آمدید!</b>

━━━━━━━━━━━━━━━━━━

اینجا قراره کمی رقابت کنیم،
سرگرم بشیم و لحظات خوبی بسازیم! ✨

<b>👇 از منوی زیر انتخاب کن:</b>
"""

JOIN_TEXT = """
<b>🔒 دسترسی به ربات محدود است!</b>

━━━━━━━━━━━━━━━━━━

برای استفاده از امکانات ربات،
ابتدا باید عضو کانال ما بشی.

بعد از عضویت، روی دکمه
<b>«عضو شدم»</b> بزن. 👇
"""

ABOUT_TEXT = """
<b>ℹ️ درباره ربات</b>

━━━━━━━━━━━━━━━━━━

🎮 <b>معرفی:</b>
این ربات با هدف سرگرمی و تجربه
بازی‌های ساده طراحی شده است.

🐍 <b>توسعه:</b> ArTeN

⚙️ <b>وضعیت:</b> نسخه 4.0

━━━━━━━━━━━━━━━━━━

از همراهی شما سپاسگزاریم ❤️
"""

SUPPORT_TEXT = """
<b>💖 حمایت از ما</b>

━━━━━━━━━━━━━━━━━━

اگه از ربات خوشت اومده،
می‌تونی به رشدش کمک کنی. 🙏

<b>👇 یکی از گزینه‌ها رو انتخاب کن:</b>
"""

ADS_TEXT = """
<b>📢 تبلیغات در ربات</b>

━━━━━━━━━━━━━━━━━━

اگه می‌خوای کانال، گروه یا کسب‌وکارت
رو به کاربران ربات معرفی کنی، با ادمین
در ارتباط باش.

برای اطلاع از تعرفه‌ها و شرایط،
روی دکمه زیر بزن. 👇
"""

PLAY_MENU_TEXT = """
<b>🎮 سنگ | کاغذ | قیچی</b>

━━━━━━━━━━━━━━━━━━

می‌خوای با کی بازی کنی؟ 👇
"""


def donate_text() -> str:
    return f"""
<b>💰 حمایت مالی</b>

━━━━━━━━━━━━━━━━━━

ممنون که می‌خوای از ما حمایت کنی! ❤️
هر مبلغی که بتونی، برای ما ارزشمنده.

💳 <b>شماره کارت:</b>
<code>{DONATE_CARD}</code>

👤 <b>به نام:</b> {DONATE_CARD_OWNER}

<i>با زدن روی شماره کارت، کپی میشه.</i>
"""


# ----- stats texts -----
def esc(name: str) -> str:
    return html.escape(name or "بازیکن")


def stats_text(row) -> str:
    wins, losses = row if row else (0, 0)
    total = wins + losses
    rate = round(wins * 100 / total) if total else 0
    return f"""
<b>📊 آمار من</b>

━━━━━━━━━━━━━━━━━━

🎮 بازی‌های انجام‌شده: {total}
🏆 برد: {wins}
❌ باخت: {losses}
📈 درصد برد: {rate}٪

<i>فقط بازی‌هایی که تا آخر رفتن حساب می‌شن.</i>
"""


def leaderboard_text(rows) -> str:
    if not rows:
        return """
<b>🏆 برترین‌ها</b>

━━━━━━━━━━━━━━━━━━

هنوز کسی بازی نکرده! اولین نفر باش. 🔥
"""

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, wins, losses) in enumerate(rows):
        badge = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{badge} {esc(name)} — 🏆 {wins} برد | ❌ {losses} باخت")

    return "<b>🏆 برترین‌ها</b>\n\n━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines)


# ----- bot mode texts -----
def new_bot_match() -> dict:
    return {"user": 0, "bot": 0, "draw": 0, "round": 1}


def get_bot_match(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("bot_match", new_bot_match())


def bot_score_line(match: dict) -> str:
    return (
        f"🏆 تو: {match['user']}  |  "
        f"🤖 ربات: {match['bot']}  |  "
        f"🤝 مساوی: {match['draw']}"
    )


def play_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    match = get_bot_match(context)
    return f"""
<b>🤖 بازی با ربات</b>

━━━━━━━━━━━━━━━━━━

🎯 اولین نفری که به {WIN_SCORE} امتیاز برسه برنده‌ست!
🎲 دور {match['round']}

{bot_score_line(match)}

<b>یکی از گزینه‌ها رو انتخاب کن 👇</b>
"""


# ----- friends mode helpers & texts -----
def get_games(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault("games", {})


def get_user_game(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault("user_game", {})


def opponent(game: dict, uid: int) -> int:
    return next(p for p in game["players"] if p != uid)


def score_line(game: dict, uid: int) -> str:
    opp = opponent(game, uid)
    return (
        f"🏆 تو: {game['scores'][uid]}  |  "
        f"{esc(game['names'][opp])}: {game['scores'][opp]}  |  "
        f"🤝 مساوی: {game['draws']}"
    )


def invite_text(link: str) -> str:
    return f"""
<b>👥 بازی با دوستان</b>

━━━━━━━━━━━━━━━━━━

لینک دعوت اختصاصی تو آماده‌ست! 🎉
اون رو برای دوستت بفرست تا بازی شروع بشه.

🎯 اولین نفری که به {WIN_SCORE} امتیاز برسه برنده‌ست!

🔗 <code>{link}</code>

<i>دوستت هم باید عضو کانال باشه.</i>

⏳ منتظر ورود حریف...
"""


def friend_round_text(game: dict, uid: int, note: str = "") -> str:
    opp = opponent(game, uid)
    return f"""
<b>👥 بازی با دوستان</b>

━━━━━━━━━━━━━━━━━━

🆚 حریف: <b>{esc(game['names'][opp])}</b>
🎯 دور {game['round']}  |  تا {WIN_SCORE} امتیاز

{score_line(game, uid)}

{note}<b>انتخاب کن 👇</b>
"""


def friend_waiting_text(game: dict, uid: int, choice: str) -> str:
    opp = opponent(game, uid)
    emoji, name = CHOICES[choice]
    return f"""
<b>👥 بازی با دوستان</b>

━━━━━━━━━━━━━━━━━━

انتخاب تو: {emoji} {name}

⏳ منتظر انتخاب <b>{esc(game['names'][opp])}</b> هستیم...
"""


def friend_result_text(
    game: dict, uid: int, my_choice: str, opp_choice: str, winner, finished: bool = False
) -> str:
    opp = opponent(game, uid)
    me_e, me_n = CHOICES[my_choice]
    op_e, op_n = CHOICES[opp_choice]

    if winner is None:
        result = "🤝 <b>مساوی شد!</b>"
    elif winner == uid:
        result = "🏆 <b>این دور رو بردی!</b>"
    else:
        result = "❌ <b>این دور رو باختی!</b>"

    if finished:
        if game["scores"][uid] >= WIN_SCORE:
            footer = "🎉🏆 <b>تبریک! تو برنده‌ی بازی شدی!</b>"
        else:
            footer = "😔 <b>حریفت برنده‌ی بازی شد!</b>"
    else:
        footer = "<b>دور بعد: انتخاب کن 👇</b>"

    return f"""
<b>👥 بازی با دوستان</b>

━━━━━━━━━━━━━━━━━━

تو: {me_e} {me_n}
{esc(game['names'][opp])}: {op_e} {op_n}

{result}

━━━━━━━━━━━━━━━━━━

{score_line(game, uid)}

{footer}
"""


def friend_final_text(game: dict, uid: int) -> str:
    if len(game["players"]) < 2:
        return "<b>❌ بازی لغو شد.</b>"

    opp = opponent(game, uid)
    mine, theirs = game["scores"][uid], game["scores"][opp]

    if mine > theirs:
        verdict = "🏆 تو برنده‌ی بازی شدی!"
    elif mine < theirs:
        verdict = "❌ حریفت برنده شد!"
    else:
        verdict = "🤝 بازی مساوی تموم شد!"

    return f"""
<b>🏁 بازی تموم شد</b>

━━━━━━━━━━━━━━━━━━

{score_line(game, uid)}

{verdict}
"""


# ---------------- GAME HELPERS ----------------
async def safe_edit(bot, chat_id: int, message_id: int, text: str, reply_markup=None) -> None:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("edit failed for %s: %s", chat_id, e)
    except TelegramError as e:
        logger.warning("edit failed for %s: %s", chat_id, e)


def close_game(context: ContextTypes.DEFAULT_TYPE, gid: str) -> None:
    """بازی رو از حافظه پاک می‌کنه (بدون ویرایش پیام‌ها)"""
    game = get_games(context).pop(gid, None)
    if not game:
        return

    user_game = get_user_game(context)
    for uid in game["players"]:
        if user_game.get(uid) == gid:
            user_game.pop(uid)


async def end_game(context: ContextTypes.DEFAULT_TYPE, gid: str) -> None:
    game = get_games(context).pop(gid, None)
    if not game:
        return

    user_game = get_user_game(context)
    for uid in game["players"]:
        if user_game.get(uid) == gid:
            user_game.pop(uid)

        message_id = game["msgs"].get(uid)
        if message_id:
            await safe_edit(
                context.bot, uid, message_id,
                friend_final_text(game, uid),
                play_back_keyboard(),
            )


async def join_game(context: ContextTypes.DEFAULT_TYPE, user, gid: str) -> None:
    games = get_games(context)
    user_game = get_user_game(context)
    game = games.get(gid)

    if not game or game["status"] != "waiting":
        await context.bot.send_message(
            user.id,
            "<b>😕 این بازی دیگه معتبر نیست.</b>\n\nلینک منقضی شده یا بازی قبلاً شروع شده.",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    if user.id in game["players"]:
        await context.bot.send_message(
            user.id,
            "😅 نمی‌تونی با خودت بازی کنی! لینک رو برای دوستت بفرست.",
            reply_markup=back_keyboard(),
        )
        return

    # اگه این کاربر توی بازی دیگه‌ای بود، اون بازی تموم میشه
    old = user_game.get(user.id)
    if old:
        await end_game(context, old)

    host = game["players"][0]
    game["players"].append(user.id)
    game["names"][user.id] = user.first_name
    game["scores"][user.id] = 0
    game["status"] = "playing"
    user_game[user.id] = gid

    msg = await context.bot.send_message(
        user.id,
        friend_round_text(game, user.id),
        reply_markup=friend_game_keyboard(gid),
        parse_mode=ParseMode.HTML,
    )
    game["msgs"][user.id] = msg.message_id

    await safe_edit(
        context.bot, host, game["msgs"][host],
        friend_round_text(game, host, note="✅ حریفت وصل شد!\n\n"),
        friend_game_keyboard(gid),
    )


# ---------------- DECORATOR ----------------

def require_membership(handler):
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user

        if user and await is_member(context.bot, user.id):
            return await handler(update, context)

        # کاربر عضو نیست؛ اگه با لینک دعوت اومده، ذخیره‌اش کن
        if update.message:
            if context.args:
                context.user_data["pending_args"] = list(context.args)
            else:
                context.user_data.pop("pending_args", None)

        query = update.callback_query
        if query:
            await query.answer(
                "🔒 برای استفاده از ربات باید عضو کانال باشی!",
                show_alert=True
            )
            await query.edit_message_text(
                JOIN_TEXT,
                reply_markup=join_keyboard(),
                parse_mode=ParseMode.HTML
            )
        elif update.message:
            await update.message.reply_text(
                JOIN_TEXT,
                reply_markup=join_keyboard(),
                parse_mode=ParseMode.HTML
            )

    return wrapper


# ---------------- HANDLERS: GENERAL ----------------

@require_membership
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args

    if args and args[0].startswith("join_"):
        await join_game(context, update.effective_user, args[0][5:])
        return

    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


# این هندلر وقتی صدا زده میشه که کاربر روی «✅ عضو شدم» بزنه
async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    if await is_member(context.bot, query.from_user.id):
        pending = context.user_data.pop("pending_args", None)
        await query.answer("✅ عضویت شما تأیید شد!")

        if pending and pending[0].startswith("join_"):
            await query.edit_message_text(
                "<b>✅ عضویت تأیید شد!</b>\n\nدر حال وصل شدن به بازی...",
                parse_mode=ParseMode.HTML
            )
            await join_game(context, query.from_user, pending[0][5:])
            return

        await query.edit_message_text(
            WELCOME_TEXT,
            reply_markup=menu_keyboard(),
            parse_mode=ParseMode.HTML
        )
    else:
        await query.answer("❌ هنوز عضو کانال نشده‌اید!", show_alert=True)


@require_membership
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        WELCOME_TEXT,
        reply_markup=menu_keyboard(),
        parse_mode=ParseMode.HTML
    )


@require_membership
async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        ABOUT_TEXT,
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.HTML
    )


# ---------------- HANDLERS: STATS ----------------

@require_membership
async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        stats_text(get_stats(query.from_user.id)),
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.HTML
    )


@require_membership
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        leaderboard_text(get_top()),
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.HTML
    )


# ---------------- HANDLERS: SUPPORT ----------------

@require_membership
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        SUPPORT_TEXT,
        reply_markup=support_keyboard(),
        parse_mode=ParseMode.HTML
    )


@require_membership
async def donate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        donate_text(),
        reply_markup=support_back_keyboard(),
        parse_mode=ParseMode.HTML
    )


@require_membership
async def ads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        ADS_TEXT,
        reply_markup=ads_keyboard(),
        parse_mode=ParseMode.HTML
    )


# ---------------- HANDLERS: GAME (MODE SELECT) ----------------

@require_membership
async def play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        PLAY_MENU_TEXT,
        reply_markup=play_mode_keyboard(),
        parse_mode=ParseMode.HTML
    )


# ---------------- HANDLERS: GAME VS BOT ----------------

@require_membership
async def play_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    # هر بار که وارد بازی با ربات میشی، یه بازی جدید شروع میشه
    context.user_data["bot_match"] = new_bot_match()

    await query.edit_message_text(
        play_text(context),
        reply_markup=game_keyboard(),
        parse_mode=ParseMode.HTML
    )


@require_membership
async def rps_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_choice = query.data.split(":")[1]
    bot_choice = random.choice(list(CHOICES))

    match = get_bot_match(context)

    # اگه بازی قبلی تموم شده بود، از اول شروع کن
    if match["user"] >= WIN_SCORE or match["bot"] >= WIN_SCORE:
        match = new_bot_match()
        context.user_data["bot_match"] = match

    if user_choice == bot_choice:
        match["draw"] += 1
        result = "🤝 <b>مساوی شد!</b>"
    elif BEATS[user_choice] == bot_choice:
        match["user"] += 1
        result = "🏆 <b>این دور رو بردی!</b>"
    else:
        match["bot"] += 1
        result = "❌ <b>این دور رو باختی!</b>"

    match["round"] += 1
    finished = match["user"] >= WIN_SCORE or match["bot"] >= WIN_SCORE

    if finished:
        user = query.from_user
        user_won = match["user"] >= WIN_SCORE
        record_result(user.id, user.first_name, won=user_won)

        if user_won:
            footer = "🎉🏆 <b>تبریک! تو برنده‌ی بازی شدی!</b>"
        else:
            footer = "😔 <b>ربات برنده‌ی بازی شد!</b>"
        keyboard = play_again_keyboard()
    else:
        footer = "<b>دور بعد: انتخاب کن 👇</b>"
        keyboard = game_keyboard()

    user_emoji, user_name = CHOICES[user_choice]
    bot_emoji, bot_name = CHOICES[bot_choice]

    text = f"""
<b>🤖 بازی با ربات</b>

━━━━━━━━━━━━━━━━━━

تو: {user_emoji} {user_name}
ربات: {bot_emoji} {bot_name}

{result}

━━━━━━━━━━━━━━━━━━

{bot_score_line(match)}

{footer}
"""

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )


# ---------------- HANDLERS: GAME VS FRIEND ----------------

@require_membership
async def play_friend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user

    user_game = get_user_game(context)
    old = user_game.get(user.id)
    if old:
        await end_game(context, old)

    gid = secrets.token_hex(4)
    get_games(context)[gid] = {
        "players": [user.id],
        "names": {user.id: user.first_name},
        "msgs": {user.id: query.message.message_id},
        "choices": {},
        "scores": {user.id: 0},
        "draws": 0,
        "round": 1,
        "status": "waiting",
    }
    user_game[user.id] = gid

    link = f"https://t.me/{context.bot.username}?start=join_{gid}"

    await query.edit_message_text(
        invite_text(link),
        reply_markup=invite_keyboard(gid, link),
        parse_mode=ParseMode.HTML
    )


@require_membership
async def friend_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, gid, choice = query.data.split(":")
    uid = query.from_user.id
    game = get_games(context).get(gid)

    if not game or game["status"] != "playing" or uid not in game["players"]:
        await query.answer("این بازی تموم شده.", show_alert=True)
        return

    if uid in game["choices"]:
        await query.answer("انتخابت ثبت شده، منتظر حریف باش ⏳")
        return

    await query.answer()
    game["choices"][uid] = choice
    opp = opponent(game, uid)

    # هنوز حریف انتخاب نکرده
    if opp not in game["choices"]:
        await safe_edit(
            context.bot, uid, game["msgs"][uid],
            friend_waiting_text(game, uid, choice),
            friend_end_keyboard(gid),
        )
        await safe_edit(
            context.bot, opp, game["msgs"][opp],
            friend_round_text(game, opp, note="⏳ حریفت انتخابش رو کرد، نوبت توئه!\n\n"),
            friend_game_keyboard(gid),
        )
        return

    # هر دو انتخاب کردن
    p1, p2 = game["players"]
    c1, c2 = game["choices"][p1], game["choices"][p2]

    if c1 == c2:
        winner = None
        game["draws"] += 1
    elif BEATS[c1] == c2:
        winner = p1
        game["scores"][p1] += 1
    else:
        winner = p2
        game["scores"][p2] += 1

    choices = dict(game["choices"])
    game["choices"].clear()
    game["round"] += 1

    finished = max(game["scores"].values()) >= WIN_SCORE

    for player in (p1, p2):
        other = opponent(game, player)
        await safe_edit(
            context.bot, player, game["msgs"][player],
            friend_result_text(
                game, player, choices[player], choices[other], winner, finished
            ),
            play_back_keyboard() if finished else friend_game_keyboard(gid),
        )

    if finished:
        for player in (p1, p2):
            record_result(
                player,
                game["names"][player],
                won=game["scores"][player] >= WIN_SCORE,
            )
        close_game(context, gid)


@require_membership
async def friend_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    gid = query.data.split(":")[1]
    await query.answer()

    if gid not in get_games(context):
        await query.edit_message_text(
            "<b>این بازی قبلاً تموم شده.</b>",
            reply_markup=play_back_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return

    await end_game(context, gid)


# ---------------- ERRORS ----------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error", exc_info=context.error)


# ---------------- MAIN ----------------

def main():
    required = {
        "BOT_TOKEN": TOKEN,
        "CHANNEL": CHANNEL,
        "ADMIN_USERNAME": ADMIN_USERNAME,
        "DONATE_CARD": DONATE_CARD,
        "DONATE_CARD_OWNER": DONATE_CARD_OWNER,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(
            f"Missing environment variables: {', '.join(missing)}\n"
            f"Looked for the .env file at: {ENV_PATH} "
            f"(exists: {ENV_PATH.exists()})"
        )

    init_db()

    app = Application.builder().token(TOKEN).build()

    # general
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(about, pattern="^about$"))
    app.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))

    # stats
    app.add_handler(CallbackQueryHandler(my_stats, pattern="^my_stats$"))
    app.add_handler(CallbackQueryHandler(leaderboard, pattern="^leaderboard$"))

    # support
    app.add_handler(CallbackQueryHandler(support, pattern="^support$"))
    app.add_handler(CallbackQueryHandler(donate, pattern="^donate$"))
    app.add_handler(CallbackQueryHandler(ads, pattern="^ads$"))

    # game
    app.add_handler(CallbackQueryHandler(play, pattern="^play$"))
    app.add_handler(CallbackQueryHandler(play_bot, pattern="^play_bot$"))
    app.add_handler(
        CallbackQueryHandler(rps_choice, pattern="^rps:(rock|paper|scissors)$")
    )
    app.add_handler(CallbackQueryHandler(play_friend, pattern="^play_friend$"))
    app.add_handler(
        CallbackQueryHandler(friend_choice, pattern="^fr:[0-9a-f]{8}:(rock|paper|scissors)$")
    )
    app.add_handler(CallbackQueryHandler(friend_end, pattern="^fr_end:[0-9a-f]{8}$"))

    app.add_error_handler(error_handler)

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()