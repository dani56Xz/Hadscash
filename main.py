import os
import logging
import aiohttp
import asyncpg
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)
import random

# Bot configuration
TOKEN = "8272958952:AAEixe1Zn3Ba8cZeUMSw8WFxxrVFuk9QOpI"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = "https://hadscash.onrender.com/webhook"
ADMIN_ID = 5542927340
CHANNEL_ID = "@hadscash"
TRON_ADDRESS = "TJ4xrwKJzKjk6FgKfuuqwah3Az5Ur22kJb"
MIN_BALANCE_FOR_GUESS = 20000  # 20,000 Toman
REFERRAL_BONUS = 5000  # 5,000 Toman
PRIZE_AMOUNT = 1000000  # 1,000,000 Toman prize

# Database configuration
DATABASE_URL = "postgresql://neondb_owner:npg_sAQj9gCK3wly@ep-winter-cherry-aezv1w77-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI()

# Telegram bot application
application = Application.builder().token(TOKEN).build()

# Database connection pool
db_pool = None

# Bot status
BOT_ENABLED = True

async def init_db():
    """Initialize database connection pool"""
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("✅ Database connection pool created successfully")
        
        # Create users table if not exists
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    balance INTEGER DEFAULT 0,
                    guesses_left INTEGER DEFAULT 1,
                    last_free_guess TIMESTAMP DEFAULT NOW(),
                    referrals INTEGER DEFAULT 0,
                    total_earned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    winning_number INTEGER
                )
            ''')
            # Create deposits table to track total revenue
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS deposits (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount INTEGER,
                    tron_amount FLOAT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            logger.info("✅ Tables created/verified")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")

async def get_user(user_id: int):
    """Get user from database"""
    try:
        async with db_pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1", user_id
            )
            return dict(user) if user else None
    except Exception as e:
        logger.error(f"❌ Error getting user {user_id}: {e}")
        return None

async def create_user(user_id: int, username: str):
    """Create new user in database"""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, username, winning_number) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
                user_id, username, random.randint(1, 1000)
            )
            logger.info(f"✅ New user created: {user_id}")
    except Exception as e:
        logger.error(f"❌ Error creating user {user_id}: {e}")

async def update_user(user_id: int, **kwargs):
    """Update user data in database"""
    try:
        async with db_pool.acquire() as conn:
            set_clause = ", ".join([f"{key} = ${i+2}" for i, key in enumerate(kwargs.keys())])
            values = [user_id] + list(kwargs.values())
            await conn.execute(
                f"UPDATE users SET {set_clause} WHERE user_id = $1",
                *values
            )
            logger.debug(f"✅ User {user_id} updated: {kwargs}")
    except Exception as e:
        logger.error(f"❌ Error updating user {user_id}: {e}")

# Main menu keyboard
def get_main_menu():
    keyboard = [
        ["🎮 شروع بازی", "👤 پروفایل"],
        ["📩 دعوت دوستان", "💰 موجودی"],
        ["ℹ️ راهنما", "☰ منو"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Balance menu keyboard
def get_balance_menu():
    keyboard = [
        ["💸 نمایش موجودی", "💳 افزایش موجودی"],
        ["🔙 بازگشت به منو"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Hamburger menu
def get_hamburger_menu(user_id: int):
    keyboard = [[InlineKeyboardButton("🚀 شروع (/start)", callback_data="cmd_start")]]
    if user_id == ADMIN_ID:
        keyboard.extend([
            [InlineKeyboardButton("📊 آمار (/stats)", callback_data="cmd_stats")],
            [InlineKeyboardButton("💾 بکاپ دیتابیس", callback_data="cmd_backup")],
            [InlineKeyboardButton("🗑️ کلیر دیتابیس", callback_data="cmd_clear")],
            [InlineKeyboardButton("👥 اطلاعات کاربران", callback_data="cmd_users")],
            [InlineKeyboardButton("📢 ارسال اطلاعیه", callback_data="cmd_broadcast")],
            [InlineKeyboardButton("🔌 خاموش/روشن ربات", callback_data="cmd_toggle")]
        ])
    return InlineKeyboardMarkup(keyboard)

# Check channel membership
async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        logger.info(f"🔍 Membership check for user {user_id}: {member.status}")
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"❌ Error checking membership for user {user_id}: {e}")
        return False

# Fetch TRON price in USD
async def get_tron_price():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=tron&vs_currencies=usd", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                logger.info(f"💰 TRON price fetched: {data}")
                return data["tron"]["usd"]
    except Exception as e:
        logger.error(f"❌ Error fetching TRON price: {e}")
        return 0.31663  # Fallback price from provided document

# Convert Toman to TRON with fee consideration
async def toman_to_tron(toman):
    usd_per_toman = 0.000016
    tron_price_usd = await get_tron_price()
    usd_amount = toman * usd_per_toman
    tron_amount = usd_amount / tron_price_usd
    return tron_amount + 1  # Add 1 TRX for transaction fee

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = f"@{update.effective_user.username}" if update.effective_user.username else "Unknown"
    logger.info(f"🚀 Received /start from user {user_id} ({username})")
    
    # Check channel membership
    if not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "⚠️ لطفاً ابتدا در کانال @hadscash عضو شوید و سپس دوباره /start را بزنید!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"❌ User {user_id} not in channel, prompted to join")
        return

    # Initialize user data if new
    user = await get_user(user_id)
    if not user:
        await create_user(user_id, username)
        user = await get_user(user_id)
        logger.info(f"👤 New user initialized: {user_id}")
        
        # Notify admin of new member
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🎉 کاربر جدید:\n👤 ID: {user_id}\n📛 Username: {username}"
            )
            logger.info(f"📢 Admin notified of new user {user_id}")
        except Exception as e:
            logger.error(f"❌ Error notifying admin: {e}")

    # Check for referral
    args = context.args
    if args and args[0].isdigit():
        referrer_id = int(args[0])
        if referrer_id != user_id:
            referrer = await get_user(referrer_id)
            if referrer:
                new_balance = referrer["balance"] + REFERRAL_BONUS
                new_referrals = referrer["referrals"] + 1
                await update_user(referrer_id, balance=new_balance, referrals=new_referrals)
                
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 یک نفر با لینک دعوت شما عضو شد! {REFERRAL_BONUS:,} تومان به موجودی شما اضافه شد. 💰"
                )
                logger.info(f"🎁 Referral bonus added for {referrer_id} by {user_id}")

    # Welcome message
    await update.message.reply_text(
        "🎮 به ربات حدس کَش خوش آمدید! ✨\n\n"
        "🎲 با حدس عدد درست (۱ تا ۱۰۰۰) می‌توانید درآمد کسب کنید! 💰\n\n"
        "🆓 هر هفته یک فرصت رایگان دارید!\n"
        "👥 با دعوت دوستان موجودی خود را افزایش دهید!\n"
        "💳 با افزایش موجودی می‌توانید حدس‌های بیشتری بزنید!",
        reply_markup=get_main_menu()
    )
    logger.info(f"👋 Welcome message sent to user {user_id}")

# Stats command (admin only)
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized stats attempt by {user_id}")
        return
    
    try:
        async with db_pool.acquire() as conn:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            active_users = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at >= $1",
                datetime.now() - timedelta(hours=24)
            )
            total_revenue = await conn.fetchval(
                "SELECT SUM(amount) FROM deposits WHERE status = 'approved'"
            ) or 0
            
        await update.message.reply_text(
            f"📊 آمار ربات:\n\n"
            f"👥 تعداد کل کاربران: {total_users}\n"
            f"🕒 کاربران فعال (۲۴ ساعت اخیر): {active_users}\n"
            f"💰 درآمد کل ربات: {total_revenue:,} تومان",
            reply_markup=get_main_menu()
        )
        logger.info(f"📊 Stats sent to admin {user_id}")
    except Exception as e:
        logger.error(f"❌ Error fetching stats: {e}")
        await update.message.reply_text("❌ خطا در دریافت آمار!")

# Backup database (admin only)
async def backup_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized backup attempt by {user_id}")
        return
    
    try:
        async with db_pool.acquire() as conn:
            users = await conn.fetch("SELECT * FROM users")
            deposits = await conn.fetch("SELECT * FROM deposits")
        
        backup_data = {"users": [dict(row) for row in users], "deposits": [dict(row) for row in deposits]}
        import json
        with open("backup.json", "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        
        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=open("backup.json", "rb"),
            filename="backup.json",
            caption="💾 بکاپ دیتابیس"
        )
        logger.info(f"💾 Backup sent to admin {user_id}")
    except Exception as e:
        logger.error(f"❌ Error creating backup: {e}")
        await update.message.reply_text("❌ خطا در ایجاد بکاپ!")

# Clear database (admin only)
async def clear_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized clear attempt by {user_id}")
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM users")
            await conn.execute("DELETE FROM deposits")
        await update.message.reply_text("🗑️ دیتابیس با موفقیت پاک شد!", reply_markup=get_main_menu())
        logger.info(f"🗑️ Database cleared by admin {user_id}")
    except Exception as e:
        logger.error(f"❌ Error clearing database: {e}")
        await update.message.reply_text("❌ خطا در پاک کردن دیتابیس!")

# Show all users (admin only)
async def show_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized users attempt by {user_id}")
        return
    
    try:
        async with db_pool.acquire() as conn:
            users = await conn.fetch("SELECT * FROM users")
        
        message = "👥 اطلاعات کاربران:\n\n"
        for user in users:
            user_info = (
                f"🆔 ID: {user['user_id']}\n"
                f"📛 نام کاربری: {user['username']}\n"
                f"💰 موجودی: {user['balance']:,} تومان\n"
                f"🎯 شانس باقی‌مانده: {user['guesses_left']}\n"
                f"👥 تعداد دعوت‌ها: {user['referrals']}\n"
                f"💵 کل درآمد: {user['total_earned']:,} تومان\n"
                f"🕒 زمان ورود: {user['created_at']}\n"
                f"{'-'*20}\n"
            )
            if len(message) + len(user_info) > 4000:  # Telegram message limit
                await update.message.reply_text(message, reply_markup=get_main_menu())
                message = "👥 ادامه اطلاعات کاربران:\n\n"
            message += user_info
        
        if message != "👥 اطلاعات کاربران:\n\n":
            await update.message.reply_text(message, reply_markup=get_main_menu())
        else:
            await update.message.reply_text("❌ هیچ کاربری یافت نشد!", reply_markup=get_main_menu())
        logger.info(f"👥 Users list sent to admin {user_id}")
    except Exception as e:
        logger.error(f"❌ Error fetching users: {e}")
        await update.message.reply_text("❌ خطا در دریافت اطلاعات کاربران!")

# Broadcast message (admin only)
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized broadcast attempt by {user_id}")
        return
    
    context.user_data["state"] = "broadcast"
    await update.message.reply_text(
        "📢 پیام خود را برای ارسال به همه کاربران وارد کنید:",
        reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True)
    )
    logger.info(f"📢 Admin {user_id} prompted for broadcast message")

# Handle broadcast message
async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or context.user_data.get("state") != "broadcast":
        return
    
    text = update.message.text
    keyboard = [[InlineKeyboardButton("✅ بفرست", callback_data="broadcast_send"), 
                InlineKeyboardButton("❌ لغو", callback_data="broadcast_cancel")]]
    await update.message.reply_text(
        f"📢 پیام شما:\n\n{text}\n\nبرای همه ارسال شود؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["broadcast_message"] = text
    logger.info(f"📢 Broadcast message preview sent to admin {user_id}")

# Toggle bot status (admin only)
async def toggle_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized toggle attempt by {user_id}")
        return
    
    keyboard = [[InlineKeyboardButton("✅ روشن", callback_data="toggle_on"),
                InlineKeyboardButton("❌ خاموش", callback_data="toggle_off")]]
    await update.message.reply_text(
        "🔌 می‌خواهید ربات را خاموش یا روشن کنید؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    logger.info(f"🔌 Toggle bot prompt sent to admin {user_id}")

# Handle callback queries
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    await query.answer()

    if data == "cmd_start":
        await start(update, context)
    elif data == "cmd_stats" and user_id == ADMIN_ID:
        await stats(update, context)
    elif data == "cmd_backup" and user_id == ADMIN_ID:
        await backup_db(update, context)
    elif data == "cmd_clear" and user_id == ADMIN_ID:
        await clear_db(update, context)
    elif data == "cmd_users" and user_id == ADMIN_ID:
        await show_all_users(update, context)
    elif data == "cmd_broadcast" and user_id == ADMIN_ID:
        await broadcast(update, context)
    elif data == "cmd_toggle" and user_id == ADMIN_ID:
        await toggle_bot(update, context)
    elif data == "broadcast_send" and user_id == ADMIN_ID:
        message = context.user_data.get("broadcast_message")
        if message:
            try:
                async with db_pool.acquire() as conn:
                    users = await conn.fetch("SELECT user_id FROM users")
                for user in users:
                    try:
                        await context.bot.send_message(
                            chat_id=user["user_id"],
                            text=message,
                            reply_markup=get_main_menu()
                        )
                    except Exception as e:
                        logger.error(f"❌ Error sending broadcast to {user['user_id']}: {e}")
                await query.message.reply_text("✅ پیام به همه کاربران ارسال شد!", reply_markup=get_main_menu())
                logger.info(f"📢 Broadcast sent by admin {user_id}")
            except Exception as e:
                logger.error(f"❌ Error during broadcast: {e}")
                await query.message.reply_text("❌ خطا در ارسال اطلاعیه!")
            context.user_data["state"] = None
            context.user_data["broadcast_message"] = None
    elif data == "broadcast_cancel" and user_id == ADMIN_ID:
        context.user_data["state"] = None
        context.user_data["broadcast_message"] = None
        await query.message.reply_text("❌ ارسال اطلاعیه لغو شد.", reply_markup=get_main_menu())
        logger.info(f"📢 Broadcast cancelled by admin {user_id}")
    elif data == "toggle_on" and user_id == ADMIN_ID:
        global BOT_ENABLED
        BOT_ENABLED = True
        await query.message.reply_text("✅ ربات روشن شد!", reply_markup=get_main_menu())
        logger.info(f"🔌 Bot enabled by admin {user_id}")
    elif data == "toggle_off" and user_id == ADMIN_ID:
        global BOT_ENABLED
        BOT_ENABLED = False
        await query.message.reply_text("❌ ربات خاموش شد!", reply_markup=get_main_menu())
        logger.info(f"🔌 Bot disabled by admin {user_id}")
    elif data.startswith("approve_") and user_id == ADMIN_ID:
        deposit_id = int(data.split("_")[1])
        async with db_pool.acquire() as conn:
            deposit = await conn.fetchrow("SELECT * FROM deposits WHERE id = $1", deposit_id)
            if deposit and deposit["status"] == "pending":
                await conn.execute(
                    "UPDATE deposits SET status = 'approved' WHERE id = $1", deposit_id
                )
                user = await get_user(deposit["user_id"])
                new_balance = user["balance"] + deposit["amount"]
                await update_user(deposit["user_id"], balance=new_balance)
                await context.bot.send_message(
                    chat_id=deposit["user_id"],
                    text=f"✅ پرداخت شما تأیید شد!\n💰 موجودی جدید: {new_balance:,} تومان",
                    reply_markup=get_main_menu()
                )
                await query.message.reply_text("✅ پرداخت تأیید شد!", reply_markup=get_main_menu())
                logger.info(f"✅ Deposit {deposit_id} approved for user {deposit['user_id']}")
    elif data.startswith("reject_") and user_id == ADMIN_ID:
        deposit_id = int(data.split("_")[1])
        async with db_pool.acquire() as conn:
            deposit = await conn.fetchrow("SELECT * FROM deposits WHERE id = $1", deposit_id)
            if deposit and deposit["status"] == "pending":
                await conn.execute(
                    "UPDATE deposits SET status = 'rejected' WHERE id = $1", deposit_id
                )
                await context.bot.send_message(
                    chat_id=deposit["user_id"],
                    text="❌ پرداخت شما رد شد. لطفاً با پشتیبانی تماس بگیرید.",
                    reply_markup=get_main_menu()
                )
                await query.message.reply_text("❌ پرداخت رد شد!", reply_markup=get_main_menu())
                logger.info(f"❌ Deposit {deposit_id} rejected for user {deposit['user_id']}")

# Handle text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = context.user_data.get("state")
    
    # Check if bot is disabled for non-admins
    if not BOT_ENABLED and user_id != ADMIN_ID:
        await update.message.reply_text("❌ ربات موقتاً غیرفعال است.", reply_markup=get_main_menu())
        logger.info(f"❌ Non-admin {user_id} attempted to use disabled bot")
        return

    # Check channel membership for non-admins
    if user_id != ADMIN_ID and not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "⚠️ لطفاً ابتدا در کانال @hadscash عضو شوید!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"❌ User {user_id} not in channel")
        return

    logger.info(f"📩 Message received from {user_id}: '{text}' in state: {state}")

    if text == "🎮 شروع بازی":
        await start_game(update, context)
        return
    elif text == "👤 پروفایل":
        await show_profile(update, context)
        return
    elif text == "📩 دعوت دوستان":
        await invite_friends(update, context)
        return
    elif text == "💰 موجودی":
        await update.message.reply_text("💰 مدیریت موجودی:", reply_markup=get_balance_menu())
        return
    elif text == "💸 نمایش موجودی":
        await show_balance(update, context)
        return
    elif text == "💳 افزایش موجودی":
        await increase_balance_prompt(update, context)
        return
    elif text == "🔙 بازگشت به منو":
        await update.message.reply_text("🔙 بازگشت به منوی اصلی:", reply_markup=get_main_menu())
        context.user_data["state"] = None
        return
    elif text == "ℹ️ راهنما":
        await show_help(update, context)
        return
    elif text == "☰ منو":
        await update.message.reply_text("☰ منوی اضافی:", reply_markup=get_hamburger_menu(user_id))
        return

    # Handle state-based messages
    if state == "guessing":
        await handle_guess(update, context)
    elif state == "increase_balance":
        await handle_balance_increase(update, context)
    elif state == "broadcast" and user_id == ADMIN_ID:
        await handle_broadcast(update, context)

# Start game handler
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    
    if not user:
        await update.message.reply_text("⚠️ ابتدا با دستور /start شروع کنید!")
        return

    # Check if user has free guess this week
    now = datetime.now()
    last_guess = user.get("last_free_guess", now - timedelta(days=8))
    
    if (now - last_guess).days >= 7:
        await update_user(user_id, guesses_left=1, last_free_guess=now)
        user["guesses_left"] = 1
        logger.info(f"🆓 Free guess reset for {user_id}")

    # Check if user can guess
    if user["guesses_left"] == 0 and user["balance"] < MIN_BALANCE_FOR_GUESS:
        await update.message.reply_text(
            "❌ شانس شما تمام شده است! 💔\n\n"
            "برای ادامه بازی:\n"
            "👥 دوستان خود را دعوت کنید\n"
            "💳 موجودی خود را افزایش دهید\n"
            "⏳ تا هفته بعد صبر کنید\n\n"
            "🆓 هر هفته یک فرصت رایگان دارید!",
            reply_markup=get_main_menu()
        )
        logger.info(f"🎲 User {user_id} has no guesses or balance")
        return

    await update.message.reply_text(
        "🎲 یک عدد بین ۱ تا ۱۰۰۰ حدس بزنید:\n\n"
        "💡 نکته: عدد باید بین ۱ تا ۱۰۰۰ باشد",
        reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True)
    )
    context.user_data["state"] = "guessing"
    logger.info(f"🎮 User {user_id} started guessing")

# Handle user guesses
async def handle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    user = await get_user(user_id)
    
    if not user:
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات کاربر!")
        return

    try:
        guess = int(text)
        if not 1 <= guess <= 1000:
            await update.message.reply_text("⚠️ لطفاً یک عدد بین ۱ تا ۱۰۰۰ وارد کنید! 🔢")
            logger.info(f"❌ Invalid guess by {user_id}: {guess}")
            return
            
        # Use free guess or deduct balance
        if user["guesses_left"] > 0:
            await update_user(user_id, guesses_left=user["guesses_left"] - 1)
            logger.info(f"🆓 Used free guess for {user_id}")
        else:
            new_balance = user["balance"] - MIN_BALANCE_FOR_GUESS
            await update_user(user_id, balance=new_balance)
            logger.info(f"💸 Deducted {MIN_BALANCE_FOR_GUESS} from {user_id}'s balance")

        # Check if guess is correct
        winning_number = user["winning_number"]
        if guess == winning_number:
            new_balance = user["balance"] + PRIZE_AMOUNT
            new_total_earned = user["total_earned"] + PRIZE_AMOUNT
            await update_user(user_id, balance=new_balance, total_earned=new_total_earned, winning_number=random.randint(1, 1000))
            
            await update.message.reply_text(
                f"🎉 تبریک می‌گم! شما برنده شدید! 🏆\n\n"
                f"💰 جایزه: {PRIZE_AMOUNT:,} تومان\n"
                f"💸 موجودی جدید شما: {new_balance:,} تومان",
                reply_markup=get_main_menu()
            )
            
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🏆 برنده جدید!\n\n"
                         f"👤 ID: {user_id}\n"
                         f"📛 Username: {user.get('username')}\n"
                         f"💰 جایزه: {PRIZE_AMOUNT:,} تومان\n"
                         f"👥 تعداد دعوت‌ها: {user.get('referrals', 0)}\n"
                         f"💵 کل درآمد: {new_total_earned:,} تومان"
                )
            except Exception as e:
                logger.error(f"❌ Error notifying admin of winner: {e}")
                
            logger.info(f"🎉 User {user_id} won {PRIZE_AMOUNT} with guess {guess}")
        else:
            await update_user(user_id, winning_number=random.randint(1, 1000))
            await update.message.reply_text(
                f"❌ اشتباه حدس زدی!\n\n"
                f"💔 شانس شما تمام شد.\n"
                f"برای ادامه:\n"
                f"👥 دوستان خود را دعوت کنید\n"
                f"💳 موجودی خود را افزایش دهید\n"
                f"⏳ تا هفته بعد صبر کنید",
                reply_markup=get_main_menu()
            )
            logger.info(f"❌ Wrong guess by {user_id}: {guess}")
            
        context.user_data["state"] = None
        
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد معتبر وارد کنید! 🔢")
        logger.info(f"❌ Non-numeric guess by {user_id}: {text}")

# Show user profile
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    
    if user:
        await update.message.reply_text(
            f"👤 پروفایل شما:\n\n"
            f"🆔 ID: {user_id}\n"
            f"📛 نام کاربری: {user.get('username', 'Unknown')}\n"
            f"💰 موجودی: {user.get('balance', 0):,} تومان\n"
            f"🎯 شانس باقی‌مانده: {user.get('guesses_left', 0)}\n"
            f"👥 تعداد دعوت‌ها: {user.get('referrals', 0)}\n"
            f"💵 کل درآمد: {user.get('total_earned', 0):,} تومان",
            reply_markup=get_main_menu()
        )
        logger.info(f"📊 Profile shown for {user_id}")
    else:
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات پروفایل!")

# Invite friends
async def invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referral_link = f"https://t.me/HadsCashBot?start={user_id}"
    
    await update.message.reply_text(
        f"📩 دعوت از دوستان\n\n"
        f"👥 دوستان خود را دعوت کنید و به ازای هر نفر {REFERRAL_BONUS:,} تومان دریافت کنید! 💰\n\n"
        f"🔗 لینک دعوت شما:\n{referral_link}\n\n"
        f"📢 ربات حدس کَش:\n"
        f"🎲 با حدس عدد درست درآمد کسب کنید!\n"
        f"🆓 هر هفته یک فرصت رایگان!",
        reply_markup=get_main_menu()
    )
    logger.info(f"📤 Invite link sent to {user_id}")

# Show balance
async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    
    if user:
        await update.message.reply_text(
            f"💸 موجودی شما: {user.get('balance', 0):,} تومان 💰",
            reply_markup=get_balance_menu()
        )
        logger.info(f"💰 Balance shown for {user_id}: {user.get('balance', 0)}")
    else:
        await update.message.reply_text("⚠️ خطا در دریافت موجودی!")

# Prompt for balance increase
async def increase_balance_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 مبلغ مورد نظر برای افزایش موجودی را وارد کنید:\n\n"
        "💡 مثال: 50000\n"
        "💰 حداقل مبلغ: 20,000 تومان",
        reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True)
    )
    context.user_data["state"] = "increase_balance"
    logger.info(f"💳 User {update.effective_user.id} prompted to increase balance")

# Handle balance increase request
async def handle_balance_increase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    user = await get_user(user_id)
    
    try:
        amount = int(text)
        if amount < 20000:
            await update.message.reply_text("⚠️ حداقل مبلغ ۲۰,۰۰۰ تومان است!")
            logger.info(f"❌ Low amount by {user_id}: {amount}")
            return
            
        tron_amount = await toman_to_tron(amount)
        
        # Save deposit request
        async with db_pool.acquire() as conn:
            deposit_id = await conn.fetchval(
                "INSERT INTO deposits (user_id, amount, tron_amount) VALUES ($1, $2, $3) RETURNING id",
                user_id, amount, tron_amount
            )
        
        await update.message.reply_text(
            f"💳 درخواست افزایش موجودی\n\n"
            f"💰 مبلغ: {amount:,} تومان\n"
            f"🔢 مقدار TRX مورد نیاز: {tron_amount:.2f}\n\n"
            f"🏦 آدرس TRON:\n`{TRON_ADDRESS}`\n\n"
            f"📸 لطفاً پس از واریز، اسکرین شات پرداخت را ارسال کنید.\n"
            f"✅ ادمین پس از تأیید، موجودی شما را افزایش می‌دهد.",
            reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        
        # Notify admin
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📥 درخواست افزایش موجودی\n\n"
                     f"👤 کاربر: {user.get('username', 'Unknown')}\n"
                     f"🆔 ID: {user_id}\n"
                     f"💰 مبلغ: {amount:,} تومان\n"
                     f"🔢 TRX: {tron_amount:.2f}\n\n"
                     f"📸 منتظر اسکرین شات پرداخت..."
            )
        except Exception as e:
            logger.error(f"❌ Error notifying admin of deposit request: {e}")
        
        context.user_data["state"] = "waiting_payment_screenshot"
        context.user_data["amount"] = amount
        context.user_data["tron_amount"] = tron_amount
        context.user_data["deposit_id"] = deposit_id
        
        logger.info(f"💳 Deposit request by {user_id}: {amount} Toman ({tron_amount} TRX)")
        
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد معتبر وارد کنید! 🔢")
        logger.info(f"❌ Non-numeric balance input by {user_id}: {text}")

# Handle photo messages (payment screenshots)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("state")
    
    if state == "waiting_payment_screenshot":
        photo = update.message.photo[-1]  # Get highest resolution photo
        user = await get_user(user_id)
        deposit_id = context.user_data.get("deposit_id")
        
        # Forward screenshot to admin with approve/reject buttons
        try:
            keyboard = [
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{deposit_id}"),
                 InlineKeyboardButton("❌ رد", callback_data=f"reject_{deposit_id}")]
            ]
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo.file_id,
                caption=f"📸 اسکرین شات پرداخت\n\n"
                       f"👤 کاربر: {user.get('username', 'Unknown')}\n"
                       f"🆔 ID: {user_id}\n"
                       f"💰 مبلغ: {context.user_data.get('amount', 0):,} تومان\n"
                       f"🔢 TRX: {context.user_data.get('tron_amount', 0):.2f}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"❌ Error forwarding screenshot to admin: {e}")
            await update.message.reply_text("❌ خطا در ارسال اسکرین شات. لطفاً دوباره تلاش کنید.")
            return
        
        await update.message.reply_text(
            "✅ اسکرین شات پرداخت دریافت شد!\n\n"
            "⏳ لطفاً منتظر تأیید ادمین باشید.\n"
            "✅ پس از تأیید، موجودی شما افزایش می‌یابد.",
            reply_markup=get_main_menu()
        )
        
        context.user_data["state"] = None
        context.user_data["amount"] = None
        context.user_data["tron_amount"] = None
        context.user_data["deposit_id"] = None
        logger.info(f"📸 Payment screenshot received from {user_id}")
    else:
        await update.message.reply_text("⚠️ لطفاً از منوی اصلی استفاده کنید.", reply_markup=get_main_menu())

# Show help
async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ راهنمای ربات حدس کَش\n\n"
        "🎮 نحوه بازی:\n"
        "• عددی بین ۱ تا ۱۰۰۰ حدس بزنید\n"
        "• اگر درست حدس بزنید، برنده جایزه می‌شوید\n\n"
        "🆓 فرصت رایگان:\n"
        "• هر هفته یک فرصت رایگان دارید\n"
        "• پس از آن باید موجودی داشته باشید\n\n"
        "💰 افزایش موجودی:\n"
        "• دعوت دوستان (هر نفر ۵,۰۰۰ تومان)\n"
        "• واریز ترون (حداقل ۲۰,۰۰۰ تومان)\n\n"
        "👥 دعوت دوستان:\n"
        "• به ازای هر دعوت: 5,000 تومان\n"
        "• دوستان شما هم یک فرصت رایگان می‌گیرند\n\n"
        "❓ سوالات متداول:\n"
        "• هر کاربر هفته‌ای یک بار بصورت رایگان می‌تواند بازی کند\n"
        "• حداقل موجودی برای بازی: ۲۰,۰۰۰ تومان\n"
        "• جایزه برنده: ۱,۰۰۰,۰۰۰ تومان",
        reply_markup=get_main_menu()
    )

# Webhook handler
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        logger.info(f"🌐 Webhook received update")
        update = Update.de_json(data, application.bot)
        if update:
            await application.update_queue.put(update)
            logger.info("✅ Update added to queue")
        else:
            logger.warning("⚠️ Invalid update received")
        return {"ok": True}
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return {"ok": False}

# Startup and shutdown
@app.on_event("startup")
async def on_startup():
    try:
        await init_db()
        await application.bot.set_webhook(url=WEBHOOK_URL, max_connections=40)
        logger.info(f"✅ Webhook set: {WEBHOOK_URL}")
        
        await application.initialize()
        await application.start()
        logger.info("✅ Application started successfully")
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        if application.running:
            await application.stop()
        await application.shutdown()
        if db_pool:
            await db_pool.close()
            logger.info("✅ Database pool closed")
        logger.info("✅ Application stopped successfully")
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("stats", stats))
application.add_handler(CallbackQueryHandler(button_callback))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
