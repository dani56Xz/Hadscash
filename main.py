import os
import logging
import aiohttp
import asyncpg
import random
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)

# Bot configuration
TOKEN = "8272958952:AAEixe1Zn3Ba8cZeUMSw8WFxxrVFuk9QOpI"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = "https://hadscash.onrender.com/webhook"
ADMIN_ID = 5542927340
CHANNEL_ID = "@hadscash"
TRON_ADDRESS = "TJ4xrwKJzKjk6FgKfuuqwah3Az5Ur22kJb"
MIN_BALANCE_FOR_GUESS = 20000  # 20,000 Toman
REFERRAL_BONUS = 5000  # 5,000 Toman
PRIZE_AMOUNT = 1000000  # 1,000,000 Toman

# Database configuration
DATABASE_URL = "postgresql://neondb_owner:npg_sAQj9gCK3wly@ep-winter-cherry-aezv1w77-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# Bot state - تعریف متغیر global در اینجا
BOT_ACTIVE = True

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
                    total_spent INTEGER DEFAULT 0,
                    last_active TIMESTAMP DEFAULT NOW(),
                    created_at TIMESTAMP DEFAULT NOW(),
                    is_active BOOLEAN DEFAULT TRUE
                )
            ''')
            
            # Create bot_stats table if not exists
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_stats (
                    id SERIAL PRIMARY KEY,
                    total_income INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Insert initial stats if not exists
            await conn.execute('''
                INSERT INTO bot_stats (id, total_income) 
                VALUES (1, 0) 
                ON CONFLICT (id) DO NOTHING
            ''')
            
            logger.info("✅ Database tables created/verified")
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
                "INSERT INTO users (user_id, username, last_active) VALUES ($1, $2, NOW()) ON CONFLICT (user_id) DO NOTHING",
                user_id, username
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

async def update_bot_stats(income: int = 0):
    """Update bot statistics"""
    try:
        async with db_pool.acquire() as conn:
            if income > 0:
                await conn.execute(
                    "UPDATE bot_stats SET total_income = total_income + $1, updated_at = NOW() WHERE id = 1",
                    income
                )
            logger.debug("✅ Bot stats updated")
    except Exception as e:
        logger.error(f"❌ Error updating bot stats: {e}")

async def get_bot_stats():
    """Get bot statistics"""
    try:
        async with db_pool.acquire() as conn:
            stats = await conn.fetchrow("SELECT * FROM bot_stats WHERE id = 1")
            return dict(stats) if stats else None
    except Exception as e:
        logger.error(f"❌ Error getting bot stats: {e}")
        return None

async def get_all_users():
    """Get all users from database"""
    try:
        async with db_pool.acquire() as conn:
            users = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(user) for user in users]
    except Exception as e:
        logger.error(f"❌ Error getting all users: {e}")
        return []

async def get_active_users_count(hours: int = 24):
    """Get count of active users in last N hours"""
    try:
        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE last_active >= NOW() - INTERVAL '$1 hours'",
                hours
            )
            return count
    except Exception as e:
        logger.error(f"❌ Error getting active users count: {e}")
        return 0

# Main menu keyboard
def get_main_menu():
    keyboard = [
        ["🎮 شروع بازی", "👤 پروفایل"],
        ["📩 دعوت دوستان", "💰 موجودی"],
        ["ℹ️ راهنما"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Balance menu keyboard
def get_balance_menu():
    keyboard = [
        ["💸 نمایش موجودی", "💳 افزایش موجودی"],
        ["🔙 بازگشت به منو"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Admin menu keyboard
def get_admin_menu():
    keyboard = [
        ["📊 آمار ربات", "💾 پشتیبان گیری"],
        ["🧹 پاکسازی دیتابیس", "👥 اطلاعات کاربران"],
        ["📢 ارسال اطلاعیه", "🔌 مدیریت بات"],
        ["🔙 بازگشت به منو"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

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
        return 0.1  # Fallback price

# Convert Toman to TRON with fee consideration
async def toman_to_tron(toman):
    usd_per_toman = 0.000016
    tron_price_usd = await get_tron_price()
    usd_amount = toman * usd_per_toman
    tron_amount = usd_amount / tron_price_usd
    return tron_amount + 1  # Add 1 TRX for transaction fee

# Generate random winning number
def generate_winning_number():
    return random.randint(1, 1000)

# تابع برای بررسی وضعیت بات
def is_bot_active():
    return BOT_ACTIVE

# تابع برای تغییر وضعیت بات
def set_bot_active(status: bool):
    global BOT_ACTIVE
    BOT_ACTIVE = status

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    logger.info(f"🚀 Received /start from user {user_id} (@{username})")
    
    # Update last active time
    await update_user(user_id, last_active=datetime.now())
    
    # Check if bot is active for regular users
    if user_id != ADMIN_ID and not is_bot_active():
        await update.message.reply_text("⏸️ ربات در حال حاضر غیرفعال است. لطفاً稍后 تلاش کنید.")
        return
    
    # Initialize user data if new
    user = await get_user(user_id)
    if not user:
        await create_user(user_id, username)
        user = await get_user(user_id)
        logger.info(f"👤 New user initialized: {user_id}")
        
        # Notify admin of new member (only for first time)
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🎉 کاربر جدید:\n👤 ID: {user_id}\n📛 @{username}"
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
            if referrer and await check_membership(context.bot, user_id):
                new_balance = referrer["balance"] + REFERRAL_BONUS
                new_referrals = referrer["referrals"] + 1
                await update_user(referrer_id, balance=new_balance, referrals=new_referrals)
                
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 یک نفر با لینک دعوت شما عضو شد! {REFERRAL_BONUS:,} تومان به موجودی شما اضافه شد. 💰"
                )
                logger.info(f"🎁 Referral bonus added for {referrer_id} by {user_id}")

    # Check channel membership for regular users
    if user_id != ADMIN_ID and not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "⚠️ لطفاً ابتدا در کانال @hadscash عضو شوید و سپس دوباره /start را بزنید!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"❌ User {user_id} not in channel, prompted to join")
        return

    # Welcome message
    welcome_text = (
        "🎮 به ربات حدس کَش خوش آمدید! ✨\n\n"
        "🎲 با حدس عدد درست (۱ تا ۱۰۰۰) می‌توانید درآمد کسب کنید! 💰\n\n"
        "🆓 هر هفته یک فرصت رایگان دارید!\n"
        "👥 با دعوت دوستان موجودی خود را افزایش دهید!\n"
        "💳 با افزایش موجودی هم می‌توانید بازی کنید!"
    )
    
    if user_id == ADMIN_ID:
        await update.message.reply_text(welcome_text, reply_markup=get_admin_menu())
    else:
        await update.message.reply_text(welcome_text, reply_markup=get_main_menu())
    
    logger.info(f"👋 Welcome message sent to user {user_id}")

# Admin command to show stats
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized stats attempt by {user_id}")
        return
    
    try:
        # Get statistics
        total_users = len(await get_all_users())
        active_users = await get_active_users_count(24)
        bot_stats = await get_bot_stats()
        total_income = bot_stats["total_income"] if bot_stats else 0
        
        stats_text = (
            f"📊 آمار کامل ربات:\n\n"
            f"👥 تعداد کل کاربران: {total_users:,}\n"
            f"🟢 کاربران فعال (24h): {active_users:,}\n"
            f"💰 درآمد کل ربات: {total_income:,} تومان\n"
            f"🔌 وضعیت بات: {'فعال' if is_bot_active() else 'غیرفعال'}"
        )
        
        await update.message.reply_text(stats_text, reply_markup=get_admin_menu())
        logger.info(f"📊 Stats shown to admin {user_id}")
    except Exception as e:
        logger.error(f"❌ Error showing stats: {e}")
        await update.message.reply_text("❌ خطا در دریافت آمار")

# Admin command to backup database
async def backup_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    try:
        users = await get_all_users()
        backup_data = {
            "backup_time": datetime.now().isoformat(),
            "total_users": len(users),
            "users": users
        }
        
        # Create backup file
        backup_filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(backup_filename, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        
        # Send backup file
        with open(backup_filename, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=backup_filename,
                caption="✅ پشتیبان گیری از دیتابیس انجام شد"
            )
        
        # Clean up
        os.remove(backup_filename)
        logger.info(f"💾 Database backup created by admin {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error creating backup: {e}")
        await update.message.reply_text("❌ خطا در پشتیبان گیری")

# Admin command to clear database
async def clear_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    try:
        async with db_pool.acquire() as conn:
            # Reset all user data but keep user records
            await conn.execute('''
                UPDATE users SET 
                balance = 0,
                guesses_left = 1,
                referrals = 0,
                total_earned = 0,
                total_spent = 0,
                last_free_guess = NOW(),
                is_active = TRUE
            ''')
            
            # Reset bot stats
            await conn.execute("UPDATE bot_stats SET total_income = 0, updated_at = NOW() WHERE id = 1")
        
        await update.message.reply_text("✅ دیتابیس با موفقیت پاکسازی شد", reply_markup=get_admin_menu())
        logger.info(f"🧹 Database cleared by admin {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error clearing database: {e}")
        await update.message.reply_text("❌ خطا در پاکسازی دیتابیس")

# Admin command to get all users info
async def get_users_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    try:
        users = await get_all_users()
        if not users:
            await update.message.reply_text("❌ هیچ کاربری یافت نشد")
            return
        
        # Send in chunks to avoid message length limits
        chunk_size = 10
        for i in range(0, len(users), chunk_size):
            chunk = users[i:i + chunk_size]
            message = "👥 اطلاعات کاربران:\n\n"
            
            for user in chunk:
                message += (
                    f"👤 کاربر: @{user.get('username', 'بدون یوزرنیم')}\n"
                    f"🆔 ID: {user['user_id']}\n"
                    f"💰 موجودی: {user.get('balance', 0):,} تومان\n"
                    f"🎯 شانس باقی‌مانده: {user.get('guesses_left', 0)}\n"
                    f"👥 دعوت‌ها: {user.get('referrals', 0)}\n"
                    f"💵 درآمد کل: {user.get('total_earned', 0):,} تومان\n"
                    f"💸 هزینه کل: {user.get('total_spent', 0):,} تومان\n"
                    f"🕒 آخرین فعالیت: {user.get('last_active', 'نامشخص')}\n"
                    f"📅 تاریخ عضویت: {user.get('created_at', 'نامشخص')}\n"
                    f"────────────────────\n"
                )
            
            await update.message.reply_text(message)
        
        logger.info(f"📋 Users info sent to admin {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error getting users info: {e}")
        await update.message.reply_text("❌ خطا در دریافت اطلاعات کاربران")

# Admin command to broadcast message
async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    context.user_data["broadcasting"] = True
    await update.message.reply_text(
        "📢 لطفاً پیام اطلاعیه را ارسال کنید:",
        reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
    )

# Admin command to manage bot state
async def manage_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    keyboard = [
        ["✅ روشن کردن بات", "❌ خاموش کردن بات"],
        ["🔙 بازگشت به منو"]
    ]
    
    await update.message.reply_text(
        "🔌 مدیریت وضعیت بات:\n\n"
        "می‌خواهید ربات را خاموش ❌ یا روشن ✅ کنید؟",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

# Handle callback queries for payment approval
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = int(data.split('_')[1])
    action = data.split('_')[0]
    
    if action == "approve":
        # Approve payment
        amount = context.user_data.get(f"pending_{user_id}", {}).get("amount", 0)
        if amount > 0:
            user = await get_user(user_id)
            new_balance = user["balance"] + amount
            await update_user(user_id, balance=new_balance)
            
            # Update bot stats
            await update_bot_stats(amount)
            
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ پرداخت شما تأیید شد! {amount:,} تومان به موجودی شما اضافه شد. 💰"
            )
            
            await query.edit_message_caption(
                f"✅ پرداخت تأیید شد!\n👤 کاربر: @{user.get('username', 'Unknown')}\n💰 مبلغ: {amount:,} تومان"
            )
            
            logger.info(f"✅ Payment approved for user {user_id}: {amount} Toman")
            
    elif action == "reject":
        # Reject payment
        user = await get_user(user_id)
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ پرداخت شما رد شد. لطفاً با پشتیبانی تماس بگیرید."
        )
        
        await query.edit_message_caption(
            f"❌ پرداخت رد شد!\n👤 کاربر: @{user.get('username', 'Unknown')}"
        )
        
        logger.info(f"❌ Payment rejected for user {user_id}")
    
    # Clean up
    if f"pending_{user_id}" in context.user_data:
        del context.user_data[f"pending_{user_id}"]

# Handle text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Update last active time
    await update_user(user_id, last_active=datetime.now())
    
    # Check if bot is active for regular users
    if user_id != ADMIN_ID and not is_bot_active():
        await update.message.reply_text("⏸️ ربات در حال حاضر غیرفعال است. لطفاً稍后 تلاش کنید.")
        return
    
    # Check channel membership for regular users
    if user_id != ADMIN_ID and not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "⚠️ لطفاً ابتدا در کانال @hadscash عضو شوید و سپس از منوی اصلی استفاده کنید!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    state = context.user_data.get("state")
    logger.info(f"📩 Message received from {user_id}: '{text}' in state: {state}")

    # Handle admin menu options
    if user_id == ADMIN_ID:
        if text == "📊 آمار ربات":
            await stats(update, context)
            return
        elif text == "💾 پشتیبان گیری":
            await backup_database(update, context)
            return
        elif text == "🧹 پاکسازی دیتابیس":
            await clear_database(update, context)
            return
        elif text == "👥 اطلاعات کاربران":
            await get_users_info(update, context)
            return
        elif text == "📢 ارسال اطلاعیه":
            await broadcast_message(update, context)
            return
        elif text == "🔌 مدیریت بات":
            await manage_bot(update, context)
            return
        elif text == "✅ روشن کردن بات":
            set_bot_active(True)
            await update.message.reply_text("✅ ربات روشن شد!", reply_markup=get_admin_menu())
            return
        elif text == "❌ خاموش کردن بات":
            set_bot_active(False)
            await update.message.reply_text("❌ ربات خاموش شد!", reply_markup=get_admin_menu())
            return
        elif text == "❌ لغو" and context.user_data.get("broadcasting"):
            context.user_data["broadcasting"] = False
            await update.message.reply_text("✅ ارسال اطلاعیه لغو شد", reply_markup=get_admin_menu())
            return

    # Handle broadcasting state
    if context.user_data.get("broadcasting") and user_id == ADMIN_ID:
        users = await get_all_users()
        success_count = 0
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user["user_id"],
                    text=f"📢 اطلاعیه:\n\n{text}"
                )
                success_count += 1
            except Exception as e:
                logger.error(f"❌ Error sending broadcast to {user['user_id']}: {e}")
        
        context.user_data["broadcasting"] = False
        await update.message.reply_text(
            f"✅ اطلاعیه به {success_count} کاربر ارسال شد",
            reply_markup=get_admin_menu()
        )
        return

    # Handle main menu options for all users
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
        if user_id == ADMIN_ID:
            await update.message.reply_text("🔙 بازگشت به منوی اصلی:", reply_markup=get_admin_menu())
        else:
            await update.message.reply_text("🔙 بازگشت به منوی اصلی:", reply_markup=get_main_menu())
        return
        
    elif text == "ℹ️ راهنما":
        await show_help(update, context)
        return

    # Handle state-based messages
    if state == "guessing":
        await handle_guess(update, context)
        return
        
    elif state == "increase_balance":
        await handle_balance_increase(update, context)
        return

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
        
        # Generate winning number for this guess
        winning_number = generate_winning_number()
            
        # Use free guess or deduct balance
        if user["guesses_left"] > 0:
            await update_user(user_id, guesses_left=user["guesses_left"] - 1)
            logger.info(f"🆓 Used free guess for {user_id}")
        else:
            new_balance = user["balance"] - MIN_BALANCE_FOR_GUESS
            new_total_spent = user.get("total_spent", 0) + MIN_BALANCE_FOR_GUESS
            await update_user(user_id, balance=new_balance, total_spent=new_total_spent)
            logger.info(f"💸 Deducted {MIN_BALANCE_FOR_GUESS} from {user_id}'s balance")

        # Check if guess is correct
        if guess == winning_number:
            new_balance = user["balance"] + PRIZE_AMOUNT
            new_total_earned = user["total_earned"] + PRIZE_AMOUNT
            await update_user(user_id, balance=new_balance, total_earned=new_total_earned)
            
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
                         f"👤 کاربر: @{user.get('username', 'Unknown')}\n"
                         f"🆔 ID: {user_id}\n"
                         f"💰 جایزه: {PRIZE_AMOUNT:,} تومان\n"
                         f"👥 تعداد دعوت‌ها: {user.get('referrals', 0)}\n"
                         f"💵 کل درآمد: {new_total_earned:,} تومان"
                )
            except Exception as e:
                logger.error(f"❌ Error notifying admin of winner: {e}")
                
            logger.info(f"🎉 User {user_id} won {PRIZE_AMOUNT} with guess {guess}")
            
        else:
            await update.message.reply_text(
                f"❌ اشتباه حدس زدید!\n\n"
                f"💔 شانس شما تمام شد.\n"
                f"برای ادامه:\n"
                f"👥 دوستان خود را دعوت کنید\n"
                f"💳 موجودی خود را افزایش دهید\n"
                f"⏳ تا هفته بعد صبر کنید",
                reply_markup=get_main_menu()
            )
            logger.info(f"❌ Wrong guess by {user_id}: {guess} (correct: {winning_number})")
            
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
            f"📛 نام کاربری: @{user.get('username', 'Unknown')}\n"
            f"💰 موجودی: {user.get('balance', 0):,} تومان\n"
            f"🎯 شانس باقی‌مانده: {user.get('guesses_left', 0)}\n"
            f"👥 تعداد دعوت‌ها: {user.get('referrals', 0)}\n"
            f"💵 کل درآمد: {user.get('total_earned', 0):,} تومان\n"
            f"💸 کل هزینه: {user.get('total_spent', 0):,} تومان",
            reply_markup=get_main_menu()
        )
        logger.info(f"📊 Profile shown for {user_id}")
    else:
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات پروفایل!")

# Invite friends
async def invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referral_link = f"https://t.me/HadsCashBot?start={user_id}"
    
    keyboard = [[InlineKeyboardButton("🔗 لینک دعوت", url=referral_link)]]
    
    await update.message.reply_text(
        f"📩 دعوت از دوستان\n\n"
        f"👥 دوستان خود را دعوت کنید و به ازای هر نفر {REFERRAL_BONUS:,} تومان دریافت کنید! 💰\n\n"
        f"🔗 لینک دعوت شما:\n{referral_link}\n\n"
        f"📢 ربات حدس کَش:\n"
        f"🎲 با حدس عدد درست درآمد کسب کنید!\n"
        f"🆓 هر هفته یک فرصت رایگان!",
        reply_markup=InlineKeyboardMarkup(keyboard)
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
        
        context.user_data["state"] = "waiting_payment_screenshot"
        context.user_data["amount"] = amount
        context.user_data["tron_amount"] = tron_amount
        
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
        amount = context.user_data.get("amount", 0)
        tron_amount = context.user_data.get("tron_amount", 0)
        
        # Store pending payment info
        context.user_data[f"pending_{user_id}"] = {
            "amount": amount,
            "tron_amount": tron_amount,
            "username": user.get("username", "Unknown")
        }
        
        # Forward screenshot to admin with approve/reject buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton("❌ رد", callback_data=f"reject_{user_id}")
            ]
        ]
        
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo.file_id,
                caption=f"📸 اسکرین شات پرداخت\n\n"
                       f"👤 کاربر: @{user.get('username', 'Unknown')}\n"
                       f"🆔 ID: {user_id}\n"
                       f"💰 مبلغ: {amount:,} تومان\n"
                       f"🔢 TRX: {tron_amount:.2f}",
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
        "• واریز ترون (حداقل ۲۰،۰۰۰ تومان)\n\n"
        "👥 دعوت دوستان:\n"
        "• به ازای هر دعوت: 5,000 تومان\n"
        "• دوستان شما هم یک فرصت رایگان می‌گیرند\n\n"
        "❓ سوالات متداول:\n"
        "• هر کاربر هفته‌ای یک بار بصورت رایگان می‌تواند بازی کند\n"
        "• حداقل موجودی برای بازی: ۲۰,۰۰۰ تومان\n"
        "• جایزه برنده: ۱۰۰۰,۰۰۰ تومان",
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
        
        # Initialize application without starting polling
        await application.initialize()
        
        # Start the application without updater for webhook mode
        await application.start()
        logger.info("✅ Application started successfully")
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        # Stop application if it's running
        if application.running:
            await application.stop()
        
        # Shutdown application
        await application.shutdown()
        
        # Close database pool
        if db_pool:
            await db_pool.close()
            logger.info("✅ Database pool closed")
            
        logger.info("✅ Application stopped successfully")
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("stats", stats))
application.add_handler(CallbackQueryHandler(handle_callback_query))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
