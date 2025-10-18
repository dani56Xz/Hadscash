import os
import logging
import aiohttp
import asyncpg
import random
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommandScopeChat
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)
from bs4 import BeautifulSoup

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
MIN_WITHDRAWAL = 1000000  # 1,000,000 Toman

# Database configuration
DATABASE_URL = "postgresql://neondb_owner:npg_sAQj9gCK3wly@ep-winter-cherry-aezv1w77-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# Global variables
bot_enabled = True
user_winning_numbers = {}

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
                    referrer_id BIGINT DEFAULT NULL,
                    balance INTEGER DEFAULT 0,
                    guesses_left INTEGER DEFAULT 1,
                    last_free_guess TIMESTAMP DEFAULT NOW(),
                    referrals INTEGER DEFAULT 0,
                    total_earned INTEGER DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    total_deposited INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    last_active TIMESTAMP DEFAULT NOW(),
                    is_active BOOLEAN DEFAULT true
                )
            ''')
            logger.info("✅ Users table created/verified")
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

async def create_user(user_id: int, username: str, referrer_id: int = None):
    """Create new user in database"""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, username, referrer_id, balance, guesses_left) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (user_id) DO NOTHING",
                user_id, username, referrer_id, 0, 1
            )
            logger.info(f"✅ New user created: {user_id} with referrer: {referrer_id}")
            return True
    except Exception as e:
        logger.error(f"❌ Error creating user {user_id}: {e}")
        return False

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

async def update_user_activity(user_id: int):
    """Update user last activity time"""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_active = NOW() WHERE user_id = $1",
                user_id
            )
    except Exception as e:
        logger.error(f"❌ Error updating user activity {user_id}: {e}")

async def get_bot_stats():
    """Get bot statistics"""
    try:
        async with db_pool.acquire() as conn:
            # Total users
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            
            # Active users in last 24 hours
            active_users = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE last_active >= NOW() - INTERVAL '24 hours'"
            )
            
            # Total income (sum of total_deposited)
            total_income = await conn.fetchval("SELECT COALESCE(SUM(total_deposited), 0) FROM users")
            
            # Total referred users
            total_referred = await conn.fetchval("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL")
            
            # New users today
            new_users_today = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE"
            )
            
            # New users this week
            new_users_week = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'"
            )
            
            return {
                "total_users": total_users,
                "active_users": active_users,
                "total_income": total_income,
                "total_referred": total_referred,
                "new_users_today": new_users_today,
                "new_users_week": new_users_week
            }
    except Exception as e:
        logger.error(f"❌ Error getting bot stats: {e}")
        return {
            "total_users": 0, 
            "active_users": 0, 
            "total_income": 0, 
            "total_referred": 0,
            "new_users_today": 0,
            "new_users_week": 0
        }

async def get_all_users():
    """Get all users data"""
    try:
        async with db_pool.acquire() as conn:
            users = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(user) for user in users]
    except Exception as e:
        logger.error(f"❌ Error getting all users: {e}")
        return []

async def backup_database():
    """Create database backup"""
    try:
        async with db_pool.acquire() as conn:
            users_data = await conn.fetch("SELECT * FROM users ORDER BY user_id")
            backup = {
                "timestamp": datetime.now().isoformat(),
                "users": [dict(user) for user in users_data]
            }
            return json.dumps(backup, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ Error creating database backup: {e}")
        return None

async def clear_database():
    """Clear all user data"""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM users")
            return True
    except Exception as e:
        logger.error(f"❌ Error clearing database: {e}")
        return False

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
        ["💸 برداشت", "🔙 بازگشت به منو"]
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

# Fetch TRON price in IRR from arzdigital
async def get_tron_price():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://arzdigital.com/coins/tron/", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')
                price_elem = soup.select_one('.arz-coin-page-coin-price-rial')
                if price_elem:
                    price_str = price_elem.text.strip().replace(',', '')
                    price = int(price_str)
                    logger.info(f"💰 TRON price fetched from arzdigital: {price} IRR")
                    return price
                else:
                    logger.warning("⚠️ Could not find price element on arzdigital")
                    return 96000  # Fallback
    except Exception as e:
        logger.error(f"❌ Error fetching TRON price from arzdigital: {e}")
        return 96000  # Fallback price in IRR

# Convert Toman to TRON with fee consideration
async def toman_to_tron(toman):
    tron_price_irr = await get_tron_price()
    tron_price_toman = tron_price_irr / 10  # 1 Toman = 10 IRR
    tron_amount = toman / tron_price_toman
    return tron_amount + 1  # Add 1 TRX for transaction fee

# Generate random winning number for user
def generate_winning_number(user_id: int):
    winning_number = random.randint(1, 1000)
    user_winning_numbers[user_id] = winning_number
    logger.info(f"🎯 Generated winning number {winning_number} for user {user_id}")
    return winning_number

# Get winning number for user
def get_winning_number(user_id: int):
    if user_id not in user_winning_numbers:
        return generate_winning_number(user_id)
    return user_winning_numbers[user_id]

async def refresh_free_guess(user_id: int):
    user = await get_user(user_id)
    if not user:
        return
    now = datetime.now()
    last_guess = user.get("last_free_guess")
    reset = False
    if last_guess:
        last_guess = last_guess.replace(tzinfo=None) if last_guess.tzinfo else last_guess
        if (now - last_guess).days >= 7:
            reset = True
    else:
        reset = True
    if reset:
        await update_user(user_id, guesses_left=1, last_free_guess=now)
        logger.info(f"🆓 Free guess reset for {user_id}")

async def handle_referral(user_id: int, referrer_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Handle referral bonus for referrer"""
    try:
        referrer = await get_user(referrer_id)
        if referrer:
            # Update referrer's balance and referral count
            new_balance = referrer["balance"] + REFERRAL_BONUS
            new_referrals = referrer["referrals"] + 1
            await update_user(referrer_id, balance=new_balance, referrals=new_referrals)
            
            # Notify referrer
            await context.bot.send_message(
                chat_id=referrer_id,
                text=f"🎉 یک نفر با لینک دعوت شما عضو شد! {REFERRAL_BONUS:,} تومان به موجودی شما اضافه شد. 💰\n\n"
                     f"💰 موجودی جدید: {new_balance:,} تومان\n"
                     f"👥 تعداد کل دعوت‌ها: {new_referrals}"
            )
            logger.info(f"🎁 Referral bonus added for {referrer_id} by {user_id}")
            
            # Set referrer for new user
            await update_user(user_id, referrer_id=referrer_id)
            return True
        return False
    except Exception as e:
        logger.error(f"❌ Error handling referral for {referrer_id}: {e}")
        return False

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    logger.info(f"🚀 Received /start from user {user_id} (@{username})")
    
    # Update user activity
    await update_user_activity(user_id)
    
    # Check if bot is enabled for regular users
    if user_id != ADMIN_ID and not bot_enabled:
        await update.message.reply_text("❌ ربات موقتاً غیرفعال شده است. لطفاً بعدا تلاش کنید.")
        return
    
    # Check channel membership for regular users
    if user_id != ADMIN_ID and not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "⚠️ لطفاً ابتدا در کانال @hadscash عضو شوید و سپس دوباره /start را بزنید!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.info(f"❌ User {user_id} not in channel, prompted to join")
        return
    
    # Initialize user data if new
    user = await get_user(user_id)
    was_new = False
    referrer_id = None
    
    if not user:
        was_new = True
        
        # Check for referral
        args = context.args
        if args and args[0].isdigit():
            potential_referrer_id = int(args[0])
            if potential_referrer_id != user_id:
                referrer = await get_user(potential_referrer_id)
                if referrer:
                    referrer_id = potential_referrer_id
                    logger.info(f"🔗 Referral detected: {user_id} referred by {referrer_id}")
        
        # Create user with referrer
        await create_user(user_id, username, referrer_id)
        user = await get_user(user_id)
        logger.info(f"👤 New user initialized: {user_id}")
        
        # Handle referral bonus if applicable
        if referrer_id:
            await handle_referral(user_id, referrer_id, context)
        
        # Notify admin only for new users
        try:
            referral_text = f" (دعوت شده توسط {referrer_id})" if referrer_id else ""
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🎉 کاربر جدید:\n👤 ID: {user_id}\n📛 @{username}{referral_text}"
            )
            logger.info(f"📢 Admin notified of new user {user_id}")
        except Exception as e:
            logger.error(f"❌ Error notifying admin: {e}")
    
    # Refresh free guess always
    await refresh_free_guess(user_id)
    
    # Give extra guess if referred and new
    if was_new and referrer_id:
        user = await get_user(user_id)
        new_guesses = user['guesses_left'] + 1
        await update_user(user_id, guesses_left=new_guesses)
        logger.info(f"🆓 Extra guess added for referred user {user_id}: {new_guesses}")
    
    # Set menu commands based on user
    scope = BotCommandScopeChat(chat_id=user_id)
    if user_id == ADMIN_ID:
        commands = [
            ("start", "شروع بازی"),
            ("stats", "آمار ربات"),
            ("backup", "پشتیبان گیری"),
            ("clear", "پاکسازی دیتابیس"),
            ("users", "اطلاعات کاربران"),
            ("broadcast", "ارسال اطلاعیه"),
            ("toggle", "خاموش/روشن کردن ربات")
        ]
    else:
        commands = [
            ("start", "شروع بازی")
        ]
    try:
        await context.bot.set_my_commands(commands, scope=scope)
    except Exception as e:
        logger.error(f"❌ Error setting commands: {e}")
    
    # Welcome message
    welcome_text = (
        "🎮 به ربات حدس کَش خوش آمدید! ✨\n\n"
        "🎲 با حدس عدد درست (۱ تا ۱۰۰۰) می‌توانید درآمد کسب کنید! 💰\n\n"
        "🆓 هر هفته یک فرصت رایگان دارید!\n"
        "👥 با دعوت دوستان موجودی خود را افزایش دهید!\n"
        "💳 با افزایش موجودی هم می‌توانید بازی کنید!"
    )
    
    if referrer_id:
        welcome_text += f"\n\n🎁 شما با دعوت یکی از دوستان عضو شدید!"
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu()
    )
    logger.info(f"👋 Welcome message sent to user {user_id}")

# Admin command to show stats
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized stats attempt by {user_id}")
        return
    
    stats_data = await get_bot_stats()
    
    await update.message.reply_text(
        f"📊 آمار کامل ربات:\n\n"
        f"👥 تعداد کل کاربران: {stats_data['total_users']:,}\n"
        f"🟢 کاربران فعال (24h): {stats_data['active_users']:,}\n"
        f"💰 درآمد کل ربات: {stats_data['total_income']:,} تومان\n"
        f"👥 تعداد کاربران دعوت شده: {stats_data['total_referred']:,}\n"
        f"📈 کاربران جدید امروز: {stats_data['new_users_today']:,}\n"
        f"📅 کاربران جدید این هفته: {stats_data['new_users_week']:,}\n"
        f"🔘 وضعیت ربات: {'🟢 روشن' if bot_enabled else '🔴 خاموش'}"
    )

# Admin command to backup database
async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized backup attempt by {user_id}")
        return
    
    await update.message.reply_text("⏳ در حال ایجاد پشتیبان از دیتابیس...")
    
    backup_data = await backup_database()
    if backup_data:
        # Send as file if too large
        if len(backup_data) > 4000:
            await update.message.reply_document(
                document=backup_data.encode('utf-8'),
                filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                caption="✅ پشتیبان دیتابیس"
            )
        else:
            await update.message.reply_text(f"```json\n{backup_data}\n```", parse_mode="Markdown")
        logger.info("✅ Database backup sent to admin")
    else:
        await update.message.reply_text("❌ خطا در ایجاد پشتیبان!")

# Admin command to clear database
async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized clear attempt by {user_id}")
        return
    
    keyboard = [
        [InlineKeyboardButton("✅ بله", callback_data="clear_confirm"),
         InlineKeyboardButton("❌ خیر", callback_data="clear_cancel")]
    ]
    
    await update.message.reply_text(
        "⚠️ آیا مطمئن هستید که می‌خواهید تمام داده‌های کاربران را پاک کنید؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Admin command to show all users
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized users attempt by {user_id}")
        return
    
    await update.message.reply_text("⏳ در حال دریافت اطلاعات کاربران...")
    
    all_users = await get_all_users()
    if not all_users:
        await update.message.reply_text("❌ هیچ کاربری یافت نشد!")
        return
    
    # Send in chunks to avoid message limits
    chunk_size = 20
    for i in range(0, len(all_users), chunk_size):
        chunk = all_users[i:i + chunk_size]
        message = "👥 اطلاعات کاربران:\n\n"
        
        for user in chunk:
            referrer_info = f" (دعوت شده توسط {user['referrer_id']})" if user.get('referrer_id') else ""
            message += (
                f"👤 @{user.get('username', 'بدون یوزرنیم')}{referrer_info}\n"
                f"🆔 ID: {user['user_id']}\n"
                f"💰 موجودی: {user.get('balance', 0):,} تومان\n"
                f"🎯 شانس: {user.get('guesses_left', 0)}\n"
                f"👥 دعوت‌ها: {user.get('referrals', 0)}\n"
                f"💵 درآمد: {user.get('total_earned', 0):,} تومان\n"
                f"💸 هزینه: {user.get('total_spent', 0):,} تومان\n"
                f"🕒 عضویت: {user.get('created_at').strftime('%Y-%m-%d %H:%M')}\n"
                f"🟢 آخرین فعالیت: {user.get('last_active').strftime('%Y-%m-%d %H:%M')}\n"
                f"{'-' * 30}\n"
            )
        
        try:
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"❌ Error sending users chunk: {e}")
            await update.message.reply_text("❌ خطا در ارسال اطلاعات کاربران!")

# Admin command for broadcast
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized broadcast attempt by {user_id}")
        return
    
    context.user_data["broadcast_mode"] = True
    await update.message.reply_text(
        "📢 لطفاً پیام اطلاعیه را ارسال کنید:"
    )

# Admin command to toggle bot
async def toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.info(f"🚫 Unauthorized toggle attempt by {user_id}")
        return
    
    global bot_enabled
    keyboard = [
        [InlineKeyboardButton("✅ روشن", callback_data="toggle_on"),
         InlineKeyboardButton("❌ خاموش", callback_data="toggle_off")]
    ]
    
    await update.message.reply_text(
        f"🔘 وضعیت فعلی ربات: {'🟢 روشن' if bot_enabled else '🔴 خاموش'}\n\n"
        "می‌خواهید ربات را خاموش❌ یا روشن✅ کنید؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Handle callback queries
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()
    
    # Handle payment approval
    if data.startswith("approve_"):
        if user_id != ADMIN_ID:
            await query.edit_message_caption(caption="❌ شما دسترسی لازم را ندارید!")
            return
            
        parts = data.split("_")
        if len(parts) != 3:
            await query.edit_message_caption(caption="❌ داده نامعتبر!")
            return
            
        payment_user_id = int(parts[1])
        amount = int(parts[2])
        
        user = await get_user(payment_user_id)
        if user:
            new_balance = user["balance"] + amount
            new_total_deposited = user.get("total_deposited", 0) + amount
            await update_user(payment_user_id, balance=new_balance, total_deposited=new_total_deposited)
            
            await query.edit_message_caption(
                caption=f"✅ پرداخت کاربر @{user.get('username', 'Unknown')} تأیید شد!\n"
                f"💰 مبلغ: {amount:,} تومان\n"
                f"💸 موجودی جدید: {new_balance:,} تومان"
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=payment_user_id,
                    text=f"✅ پرداخت شما تأیید شد!\n\n💰 مبلغ {amount:,} تومان به موجودی شما اضافه شد.\n💸 موجودی جدید: {new_balance:,} تومان",
                    reply_markup=get_main_menu()
                )
            except Exception as e:
                logger.error(f"❌ Error notifying user of payment approval: {e}")
                
        else:
            await query.edit_message_caption(caption="❌ کاربر یافت نشد!")
    
    # Handle payment rejection
    elif data.startswith("reject_"):
        if user_id != ADMIN_ID:
            await query.edit_message_caption(caption="❌ شما دسترسی لازم را ندارید!")
            return
            
        parts = data.split("_")
        if len(parts) != 2:
            await query.edit_message_caption(caption="❌ داده نامعتبر!")
            return
            
        payment_user_id = int(parts[1])
        
        user = await get_user(payment_user_id)
        await query.edit_message_caption(
            caption=f"❌ پرداخت کاربر @{user.get('username', 'Unknown')} رد شد!"
        )
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=payment_user_id,
                text="❌ پرداخت شما رد شد!\n\n📞 لطفاً با پشتیبانی تماس بگیرید.",
                reply_markup=get_main_menu()
            )
        except Exception as e:
            logger.error(f"❌ Error notifying user of payment rejection: {e}")
    
    # Handle withdrawal approval
    elif data.startswith("withdraw_approve_"):
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ شما دسترسی لازم را ندارید!")
            return
            
        parts = data.split("_")
        if len(parts) != 3:
            await query.edit_message_text("❌ داده نامعتبر!")
            return
            
        withdraw_user_id = int(parts[1])
        amount = int(parts[2])
        
        await query.edit_message_text(
            text=query.message.text + "\n\n✅ واریز انجام شد!"
        )
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=withdraw_user_id,
                text=f"✅ برداشت {amount:,} تومان شما انجام شد! 💸"
            )
        except Exception as e:
            logger.error(f"❌ Error notifying user of withdrawal approval: {e}")
    
    # Handle database clear confirmation
    elif data == "clear_confirm":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ شما دسترسی لازم را ندارید!")
            return
            
        if await clear_database():
            await query.edit_message_text("✅ دیتابیس با موفقیت پاک شد!")
        else:
            await query.edit_message_text("❌ خطا در پاکسازی دیتابیس!")
    
    elif data == "clear_cancel":
        await query.edit_message_text("❌ پاکسازی دیتابیس لغو شد.")
    
    # Handle bot toggle
    elif data == "toggle_on":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ شما دسترسی لازم را ندارید!")
            return
            
        global bot_enabled
        bot_enabled = True
        await query.edit_message_text("✅ ربات روشن شد!")
    
    elif data == "toggle_off":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ شما دسترسی لازم را ندارید!")
            return
            
        bot_enabled = False
        await query.edit_message_text("🔴 ربات خاموش شد!")
    
    # Handle broadcast confirmation
    elif data == "broadcast_confirm":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ شما دسترسی لازم را ندارید!")
            return
            
        broadcast_message = context.user_data.get("broadcast_message")
        if broadcast_message:
            all_users = await get_all_users()
            success_count = 0
            fail_count = 0
            
            for user in all_users:
                try:
                    await context.bot.send_message(
                        chat_id=user["user_id"],
                        text=f"📢 اطلاعیه:\n\n{broadcast_message}"
                    )
                    success_count += 1
                except Exception as e:
                    fail_count += 1
                    logger.error(f"❌ Error sending broadcast to {user['user_id']}: {e}")
            
            await query.edit_message_text(
                f"📊 نتیجه ارسال اطلاعیه:\n\n"
                f"✅ موفق: {success_count}\n"
                f"❌ ناموفق: {fail_count}"
            )
        else:
            await query.edit_message_text("❌ پیام اطلاعیه یافت نشد!")
        
        context.user_data["broadcast_mode"] = False
    
    elif data == "broadcast_cancel":
        await query.edit_message_text("❌ ارسال اطلاعیه لغو شد.")
        context.user_data["broadcast_mode"] = False

# Handle text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = context.user_data.get("state")
    
    logger.info(f"📩 Message received from {user_id}: '{text}' in state: {state}")
    
    # Update user activity
    await update_user_activity(user_id)
    
    # Check if bot is enabled for regular users
    if user_id != ADMIN_ID and not bot_enabled:
        await update.message.reply_text("❌ ربات موقتاً غیرفعال شده است. لطفاً بعدا تلاش کنید.")
        return
    
    # Check channel membership for regular users for all actions
    if user_id != ADMIN_ID and not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "⚠️ لطفاً ابتدا در کانال @hadscash عضو شوید و سپس از ربات استفاده کنید!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Handle back to menu in any state
    if text == "🔙 بازگشت به منو":
        context.user_data["state"] = None
        await update.message.reply_text("🔙 بازگشت به منوی اصلی:", reply_markup=get_main_menu())
        return
    
    # Handle broadcast mode for admin
    if context.user_data.get("broadcast_mode") and user_id == ADMIN_ID:
        context.user_data["broadcast_message"] = text
        keyboard = [
            [InlineKeyboardButton("✅ بله", callback_data="broadcast_confirm"),
             InlineKeyboardButton("❌ خیر", callback_data="broadcast_cancel")]
        ]
        await update.message.reply_text(
            f"📢 آیا می‌خواهید این پیام را برای همه کاربران ارسال کنید؟\n\n{text}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Handle main menu options
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
        
    elif text == "💸 برداشت":
        await withdraw_prompt(update, context)
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
        
    elif state == "withdraw_amount":
        await handle_withdraw_amount(update, context)
        return
        
    elif state == "withdraw_card":
        await handle_withdraw_card(update, context)
        return
        
    elif state == "withdraw_confirm":
        await handle_withdraw_confirm(update, context)
        return
        
    # Default response for unknown messages
    await update.message.reply_text(
        "⚠️ لطفاً از منوی اصلی استفاده کنید:",
        reply_markup=get_main_menu()
    )

# Start game handler
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await refresh_free_guess(user_id)
    user = await get_user(user_id)
    
    if not user:
        await update.message.reply_text("⚠️ ابتدا با دستور /start شروع کنید!")
        return

    # Check if user can guess
    if user.get("guesses_left", 0) == 0 and user.get("balance", 0) < MIN_BALANCE_FOR_GUESS:
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

    # Generate winning number for this user session
    generate_winning_number(user_id)
    
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
            
        # Get winning number for this user
        winning_number = get_winning_number(user_id)
            
        # Use free guess or deduct balance
        if user.get("guesses_left", 0) > 0:
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
            new_total_earned = user.get("total_earned", 0) + PRIZE_AMOUNT
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
                         f"👤 @{user.get('username', 'Unknown')}\n"
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
                f"❌ اشتباه بود!\n\n"
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
    await refresh_free_guess(user_id)
    user = await get_user(user_id)
    
    if user:
        # Calculate next free guess
        now = datetime.now()
        last_guess = user.get("last_free_guess")
        next_free_guess = "امروز"
        
        if last_guess:
            last_guess = last_guess.replace(tzinfo=None) if last_guess.tzinfo else last_guess
            days_passed = (now - last_guess).days
            days_remaining = 7 - days_passed
            if days_remaining > 0:
                next_free_guess = f"{days_remaining} روز دیگر"
            else:
                next_free_guess = "امروز"
        
        # Get referrer info if exists
        referrer_info = ""
        if user.get('referrer_id'):
            referrer = await get_user(user['referrer_id'])
            referrer_username = referrer.get('username', 'Unknown') if referrer else 'Unknown'
            referrer_info = f"\n👥 دعوت شده توسط: @{referrer_username}"
        
        await update.message.reply_text(
            f"👤 پروفایل شما:\n\n"
            f"🆔 ID: {user_id}\n"
            f"📛 نام کاربری: @{user.get('username', 'Unknown')}"
            f"{referrer_info}\n"
            f"💰 موجودی: {user.get('balance', 0):,} تومان\n"
            f"🎯 شانس باقی‌مانده: {user.get('guesses_left', 0)}\n"
            f"👥 تعداد دعوت‌ها: {user.get('referrals', 0)}\n"
            f"💵 کل درآمد: {user.get('total_earned', 0):,} تومان\n"
            f"🆓 فرصت رایگان بعدی: {next_free_guess}",
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
    await refresh_free_guess(user_id)
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

# Prompt for withdrawal
async def withdraw_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    
    if user["balance"] < MIN_WITHDRAWAL:
        await update.message.reply_text(
            "❌ موجودی شما کمتر از ۱,۰۰۰,۰۰۰ تومان است!\n\nبرای برداشت حداقل ۱ میلیون تومان نیاز دارید.",
            reply_markup=get_main_menu()
        )
        return
    
    await update.message.reply_text(
        "💸 وارد کردن مبلغ برداشت:\n\nلطفاً مبلغ مورد نظر برای برداشت را وارد کنید (حداقل ۱,۰۰۰,۰۰۰ تومان):",
        reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True)
    )
    context.user_data["state"] = "withdraw_amount"
    logger.info(f"💸 User {user_id} prompted for withdrawal amount")

# Handle withdrawal amount
async def handle_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    user = await get_user(user_id)
    
    try:
        amount = int(text)
        if amount < MIN_WITHDRAWAL or amount > user["balance"]:
            await update.message.reply_text(
                f"⚠️ مبلغ نامعتبر! حداقل {MIN_WITHDRAWAL:,} تومان و حداکثر {user['balance']:,} تومان."
            )
            return
        
        context.user_data["withdraw_amount"] = amount
        await update.message.reply_text(
            "🏦 وارد کردن شماره کارت:\n\nلطفاً شماره کارت ۱۶ رقمی خود را وارد کنید:",
            reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True)
        )
        context.user_data["state"] = "withdraw_card"
        
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد معتبر وارد کنید! 🔢")

# Handle withdrawal card
async def handle_withdraw_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if not text.isdigit() or len(text) != 16:
        await update.message.reply_text("⚠️ شماره کارت نامعتبر! باید ۱۶ رقم باشد.")
        return
    
    context.user_data["withdraw_card"] = text
    amount = context.user_data["withdraw_amount"]
    keyboard = [
        ["✅ بله", "❌ خیر"]
    ]
    await update.message.reply_text(
        f"آیا از برداشت {amount:,} تومان به شماره کارت {text} مطمئن هستید؟",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    context.user_data["state"] = "withdraw_confirm"

# Handle withdrawal confirmation
async def handle_withdraw_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    user = await get_user(user_id)
    
    if text == "✅ بله":
        amount = context.user_data["withdraw_amount"]
        card = context.user_data["withdraw_card"]
        new_balance = user["balance"] - amount
        await update_user(user_id, balance=new_balance)
        
        keyboard = [
            [InlineKeyboardButton("✅ واریز شد", callback_data=f"withdraw_approve_{user_id}_{amount}")]
        ]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📤 درخواست برداشت:\n\n"
                 f"👤 @{user.get('username', 'Unknown')}\n"
                 f"🆔 ID: {user_id}\n"
                 f"💰 مبلغ: {amount:,} تومان\n"
                 f"🏦 کارت: {card}\n\n"
                 f"💸 موجودی پس از کسر: {new_balance:,} تومان",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        await update.message.reply_text(
            "✅ درخواست برداشت ارسال شد.\n\n⏳ منتظر تأیید ادمین باشید.",
            reply_markup=get_main_menu()
        )
        logger.info(f"📤 Withdrawal request by {user_id}: {amount} to {card}")
        
    elif text == "❌ خیر":
        await update.message.reply_text("❌ برداشت لغو شد.", reply_markup=get_main_menu())
    
    context.user_data["state"] = None

# Handle photo messages (payment screenshots)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("state")
    
    if state == "waiting_payment_screenshot":
        photo = update.message.photo[-1]  # Get highest resolution photo
        user = await get_user(user_id)
        amount = context.user_data.get("amount", 0)
        tron_amount = context.user_data.get("tron_amount", 0)
        
        # Forward screenshot to admin with approve/reject buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{user_id}_{amount}"),
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
        f"• به ازای هر دعوت: 5,000 تومان\n"
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
application.add_handler(CommandHandler("backup", backup))
application.add_handler(CommandHandler("clear", clear))
application.add_handler(CommandHandler("users", users))
application.add_handler(CommandHandler("broadcast", broadcast))
application.add_handler(CommandHandler("toggle", toggle))
application.add_handler(CallbackQueryHandler(handle_callback))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
