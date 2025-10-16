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

# Bot configuration
TOKEN = "8272958952:AAEixe1Zn3Ba8cZeUMSw8WFxxrVFuk9QOpI"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = "https://hadscash.onrender.com/webhook"
ADMIN_ID = 5542927340
CHANNEL_ID = "@hadscash"
TRON_ADDRESS = "TJ4xrwKJzKjk6FgKfuuqwah3Az5Ur22kJb"
WINNING_NUMBER = 341
MIN_BALANCE_FOR_GUESS = 20000  # 20,000 Toman
REFERRAL_BONUS = 5000  # 5,000 Toman

# Database configuration
DATABASE_URL = "postgresql://neondb_owner:npg_sAQj9gCK3wly@ep-winter-cherry-aezv1w77-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
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
                    created_at TIMESTAMP DEFAULT NOW()
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

async def create_user(user_id: int, username: str):
    """Create new user in database"""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
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

# Check channel membership
async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        logger.debug(f"🔍 Membership check for user {user_id}: {member.status}")
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"❌ Error checking membership for user {user_id}: {e}")
        return False

# Fetch TRON price in USD
async def get_tron_price():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=tron&vs_currencies=usd") as resp:
                data = await resp.json()
                logger.debug(f"💰 TRON price fetched: {data}")
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

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    logger.debug(f"🚀 Received /start from user {user_id} ({username})")
    
    # Initialize user data if new
    user = await get_user(user_id)
    if not user:
        await create_user(user_id, username)
        user = await get_user(user_id)
        logger.debug(f"👤 New user initialized: {user_id}")
    
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
                logger.debug(f"🎁 Referral bonus added for {referrer_id} by {user_id}")

    # Check channel membership
    if not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "⚠️ لطفاً ابتدا در کانال @hadscash عضو شوید و سپس دوباره /start را بزنید!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.debug(f"❌ User {user_id} not in channel, prompted to join")
        return

    # Notify admin of new member
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🎉 کاربر جدید:\n👤 ID: {user_id}\n📛 Username: {username}"
    )
    logger.debug(f"📢 Admin notified of new user {user_id}")

    # Welcome message
    await update.message.reply_text(
        "🎮 به ربات حدس کَش خوش آمدید! ✨\n\n"
        "🎲 با حدس عدد درست (۱ تا ۱۰۰۰) می‌توانید درآمد کسب کنید! 💰\n\n"
        "🆓 هر هفته یک فرصت رایگان دارید!\n"
        "👥 با دعوت دوستان موجودی خود را افزایش دهید!",
        reply_markup=get_main_menu()
    )
    logger.debug(f"👋 Welcome message sent to user {user_id}")

# Admin command to set winning number
async def set_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.debug(f"🚫 Unauthorized set_number attempt by {user_id}")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ لطفاً عدد را وارد کنید:\n/set_number <عدد>")
        logger.debug(f"❌ No number provided for set_number by {user_id}")
        return
    
    global WINNING_NUMBER
    WINNING_NUMBER = int(context.args[0])
    await update.message.reply_text(f"✅ عدد برنده به {WINNING_NUMBER} تغییر کرد. 🎯")
    logger.debug(f"🎯 Winning number set to {WINNING_NUMBER} by admin {user_id}")

# Handle text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = context.user_data.get("state")
    
    logger.debug(f"📩 Message received from {user_id}: '{text}' in state: {state}")

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
        
    elif text == "🔙 بازگشت به منو":
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
        logger.debug(f"🆓 Free guess reset for {user_id}")

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
        logger.debug(f"🎲 User {user_id} has no guesses or balance")
        return

    await update.message.reply_text(
        "🎲 یک عدد بین ۱ تا ۱۰۰۰ حدس بزنید:\n\n"
        "💡 نکته: عدد باید بین ۱ تا ۱۰۰۰ باشد",
        reply_markup=ReplyKeyboardMarkup([["🔙 بازگشت به منو"]], resize_keyboard=True)
    )
    context.user_data["state"] = "guessing"
    logger.debug(f"🎮 User {user_id} started guessing")

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
            logger.debug(f"❌ Invalid guess by {user_id}: {guess}")
            return
            
        # Use free guess or deduct balance
        if user["guesses_left"] > 0:
            await update_user(user_id, guesses_left=user["guesses_left"] - 1)
            logger.debug(f"🆓 Used free guess for {user_id}")
        else:
            new_balance = user["balance"] - MIN_BALANCE_FOR_GUESS
            await update_user(user_id, balance=new_balance)
            logger.debug(f"💸 Deducted {MIN_BALANCE_FOR_GUESS} from {user_id}'s balance")

        # Check if guess is correct
        if guess == WINNING_NUMBER:
            prize = 100000  # 100,000 Toman prize
            new_balance = user["balance"] + prize
            new_total_earned = user["total_earned"] + prize
            await update_user(user_id, balance=new_balance, total_earned=new_total_earned)
            
            await update.message.reply_text(
                f"🎉 تبریک می‌گم! شما برنده شدید! 🏆\n\n"
                f"💰 جایزه: {prize:,} تومان\n"
                f"🎯 عدد برنده: {WINNING_NUMBER}\n\n"
                f"💸 موجودی جدید شما: {new_balance:,} تومان",
                reply_markup=get_main_menu()
            )
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🏆 برنده جدید!\n\n"
                     f"👤 ID: {user_id}\n"
                     f"📛 Username: {user.get('username')}\n"
                     f"💰 جایزه: {prize:,} تومان\n"
                     f"👥 تعداد دعوت‌ها: {user.get('referrals', 0)}\n"
                     f"💵 کل درآمد: {new_total_earned:,} تومان"
            )
            logger.debug(f"🎉 User {user_id} won {prize} with guess {guess}")
            
        else:
            await update.message.reply_text(
                f"❌ اشتباه بود! عدد برنده {WINNING_NUMBER} بود.\n\n"
                f"💔 شانس شما تمام شد.\n"
                f"برای ادامه:\n"
                f"👥 دوستان خود را دعوت کنید\n"
                f"💳 موجودی خود را افزایش دهید\n"
                f"⏳ تا هفته بعد صبر کنید",
                reply_markup=get_main_menu()
            )
            logger.debug(f"❌ Wrong guess by {user_id}: {guess} (correct: {WINNING_NUMBER})")
            
        context.user_data["state"] = None
        
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد معتبر وارد کنید! 🔢")
        logger.debug(f"❌ Non-numeric guess by {user_id}: {text}")

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
        logger.debug(f"📊 Profile shown for {user_id}")
    else:
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات پروفایل!")

# Invite friends
async def invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referral_link = f"https://t.me/HadsCashBot?start={user_id}"
    
    await update.message.reply_text(
        f"📩 دعوت از دوستان\n\n"
        f"👥 دوستان خود را دعوت کنید و به ازای هر نفر {REFERRAL_BONUS:,} تومان دریافت کنید! 💰\n\n"
        f"🔗 لینک دعوت شما:\n`{referral_link}`\n\n"
        f"📢 ربات حدس کَش:\n"
        f"🎲 با حدس عدد درست درآمد کسب کنید!\n"
        f"🆓 هر هفته یک فرصت رایگان!",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    logger.debug(f"📤 Invite link sent to {user_id}")

# Show balance
async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    
    if user:
        await update.message.reply_text(
            f"💸 موجودی شما: {user.get('balance', 0):,} تومان 💰",
            reply_markup=get_balance_menu()
        )
        logger.debug(f"💰 Balance shown for {user_id}: {user.get('balance', 0)}")
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
    logger.debug(f"💳 User {update.effective_user.id} prompted to increase balance")

# Handle balance increase request
async def handle_balance_increase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    user = await get_user(user_id)
    
    try:
        amount = int(text)
        if amount < 20000:
            await update.message.reply_text("⚠️ حداقل مبلغ ۲۰,۰۰۰ تومان است!")
            logger.debug(f"❌ Low amount by {user_id}: {amount}")
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
        
        # Notify admin
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📥 درخواست افزایش موجودی\n\n"
                 f"👤 کاربر: {user.get('username', 'Unknown')}\n"
                 f"🆔 ID: {user_id}\n"
                 f"💰 مبلغ: {amount:,} تومان\n"
                 f"🔢 TRX: {tron_amount:.2f}\n\n"
                 f"📸 منتظر اسکرین شات پرداخت..."
        )
        
        context.user_data["state"] = "waiting_payment_screenshot"
        context.user_data["amount"] = amount
        context.user_data["tron_amount"] = tron_amount
        
        logger.debug(f"💳 Deposit request by {user_id}: {amount} Toman ({tron_amount} TRX)")
        
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد معتبر وارد کنید! 🔢")
        logger.debug(f"❌ Non-numeric balance input by {user_id}: {text}")

# Handle photo messages (payment screenshots)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("state")
    
    if state == "waiting_payment_screenshot":
        photo = update.message.photo[-1]  # Get highest resolution photo
        user = await get_user(user_id)
        
        # Forward screenshot to admin
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo.file_id,
            caption=f"📸 اسکرین شات پرداخت\n\n"
                   f"👤 کاربر: {user.get('username', 'Unknown')}\n"
                   f"🆔 ID: {user_id}\n"
                   f"💰 مبلغ: {context.user_data.get('amount', 0):,} تومان\n"
                   f"🔢 TRX: {context.user_data.get('tron_amount', 0):.2f}"
        )
        
        await update.message.reply_text(
            "✅ اسکرین شات پرداخت دریافت شد!\n\n"
            "⏳ لطفاً منتظر تأیید ادمین باشید.\n"
            "✅ پس از تأیید، موجودی شما افزایش می‌یابد.",
            reply_markup=get_main_menu()
        )
        
        context.user_data["state"] = None
        logger.debug(f"📸 Payment screenshot received from {user_id}")
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
        "• واریز تومان و تبدیل به TRX\n\n"
        "👥 دعوت دوستان:\n"
        f"• به ازای هر دعوت: {REFERRAL_BONUS:,} تومان\n"
        "• دوستان شما هم یک فرصت رایگان می‌گیرند\n\n"
        "❓ سوالات متداول:\n"
        "• هر کاربر هفته‌ای یک بار می‌تواند بازی کند\n"
        "• حداقل موجودی برای بازی: ۲۰,۰۰۰ تومان\n"
        "• جایزه برنده: ۱۰۰,۰۰۰ تومان",
        reply_markup=get_main_menu()
    )

# Webhook handler
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        logger.debug(f"🌐 Webhook received: {data}")
        update = Update.de_json(data, application.bot)
        if update:
            await application.update_queue.put(update)
            logger.debug("✅ Update added to queue")
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
        await application.stop()
        await application.shutdown()
        if db_pool:
            await db_pool.close()
        logger.info("✅ Application stopped successfully")
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("set_number", set_number))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
