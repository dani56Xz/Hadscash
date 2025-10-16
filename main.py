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
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    
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
        logger.info("Database initialized")

async def get_user(user_id: int):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow(
            'SELECT * FROM users WHERE user_id = $1', user_id
        )
        return dict(user) if user else None

async def update_user(user_id: int, **kwargs):
    async with db_pool.acquire() as conn:
        set_clause = ', '.join([f"{key} = ${i+2}" for i, key in enumerate(kwargs.keys())])
        values = [user_id] + list(kwargs.values())
        await conn.execute(
            f'INSERT INTO users (user_id, {", ".join(kwargs.keys())}) '
            f'VALUES ($1, {", ".join(["$" + str(i+2) for i in range(len(kwargs))])}) '
            f'ON CONFLICT (user_id) DO UPDATE SET {set_clause}',
            *values
        )

# Main menu keyboard
def get_main_menu():
    keyboard = [
        [KeyboardButton("🎮 شروع بازی"), KeyboardButton("👤 پروفایل")],
        [KeyboardButton("📩 دعوت دوستان"), KeyboardButton("💰 موجودی")],
        [KeyboardButton("ℹ️ راهنما")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Balance menu keyboard
def get_balance_menu():
    keyboard = [
        [KeyboardButton("💸 نمایش موجودی"), KeyboardButton("💳 افزایش موجودی")],
        [KeyboardButton("🔙 بازگشت به منو")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Check channel membership
async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        logger.debug(f"Membership check for user {user_id}: {member.status}")
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Error checking membership for user {user_id}: {e}")
        return False

# Fetch TRON price in USD
async def get_tron_price():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=tron&vs_currencies=usd") as resp:
                data = await resp.json()
                logger.debug(f"TRON price fetched: {data}")
                return data["tron"]["usd"]
    except Exception as e:
        logger.error(f"Error fetching TRON price: {e}")
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
    logger.debug(f"Received /start from user {user_id} ({username})")
    
    # Initialize user data if new
    user = await get_user(user_id)
    if not user:
        await update_user(
            user_id=user_id,
            username=username,
            balance=0,
            guesses_left=1,
            last_free_guess=datetime.now(),
            referrals=0,
            total_earned=0
        )
        logger.debug(f"New user initialized: {user_id}")
    
    # Check for referral
    args = context.args
    if args and args[0].isdigit():
        referrer_id = int(args[0])
        if referrer_id != user_id:
            referrer = await get_user(referrer_id)
            if referrer and await check_membership(context.bot, user_id):
                await update_user(
                    user_id=referrer_id,
                    balance=referrer["balance"] + REFERRAL_BONUS,
                    referrals=referrer["referrals"] + 1
                )
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 یک نفر با لینک دعوت شما عضو شد! {REFERRAL_BONUS:,} تومان به موجودی شما اضافه شد. 💰"
                )
                logger.debug(f"Referral bonus added for {referrer_id} by {user_id}")

    # Check channel membership
    if not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "❌ لطفاً ابتدا در کانال @hadscash عضو شوید و سپس دوباره /start را بزنید! 👥",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.debug(f"User {user_id} not in channel, prompted to join")
        return

    # Notify admin of new member
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🎉 کاربر جدید:\n🆔 ID: {user_id}\n👤 Username: {username}"
    )
    logger.debug(f"Admin notified of new user {user_id}")

    # Welcome message
    await update.message.reply_text(
        "🎮 به ربات حدس کَش خوش آمدید! ✨\n\n"
        "🎲 با حدس عدد درست (۱ تا ۱۰۰۰) می‌توانید درآمد کسب کنید! 💰\n"
        "💫 هر کاربر هفته‌ای یک فرصت رایگان دارد!\n"
        "📱 برای شروع از منوی زیر انتخاب کنید:",
        reply_markup=get_main_menu()
    )
    logger.debug(f"Welcome message sent to user {user_id}")

# Admin command to set winning number
async def set_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.debug(f"Unauthorized set_number attempt by {user_id}")
        return
    if not context.args:
        await update.message.reply_text("❌ لطفاً عدد را وارد کنید:\n/set_number <عدد>")
        logger.debug(f"No number provided for set_number by {user_id}")
        return
    global WINNING_NUMBER
    WINNING_NUMBER = int(context.args[0])
    await update.message.reply_text(f"✅ عدد برنده به {WINNING_NUMBER} تغییر کرد. 🎯")
    logger.debug(f"Winning number set to {WINNING_NUMBER} by admin {user_id}")

# Handle text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = context.user_data.get("state")
    logger.debug(f"Message received from {user_id}: {text} (state: {state})")

    if text == "🎮 شروع بازی":
        user = await get_user(user_id)
        if not user:
            await update.message.reply_text("❌ ابتدا با دستور /start شروع کنید!")
            return

        now = datetime.now()
        last_guess = user.get("last_free_guess", now - timedelta(days=8))
        
        # Reset free guess if 7 days passed
        if (now - last_guess).days >= 7:
            await update_user(user_id=user_id, guesses_left=1, last_free_guess=now)
            user["guesses_left"] = 1
            logger.debug(f"Free guess reset for {user_id}")

        # Check if user can guess
        if user["guesses_left"] == 0 and user["balance"] < MIN_BALANCE_FOR_GUESS:
            await update.message.reply_text(
                "❌ شانس شما تمام شده است! 🎲\n\n"
                "برای ادامه بازی:\n"
                "📩 ۱. دوستان خود را دعوت کنید\n"
                "💳 ۲. موجودی خود را افزایش دهید\n"
                "⏳ ۳. تا هفته بعد صبر کنید\n\n"
                "💫 هر کاربر هفته‌ای یک فرصت رایگان دارد!",
                reply_markup=get_main_menu()
            )
            logger.debug(f"User {user_id} has no guesses or balance")
            return

        await update.message.reply_text(
            "🎲 یک عدد بین ۱ تا ۱۰۰۰ حدس بزنید:\n\n"
            "💡 نکته: عدد باید بین ۱ تا ۱۰۰۰ باشد",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت به منو")]], resize_keyboard=True)
        )
        context.user_data["state"] = "guessing"
        logger.debug(f"User {user_id} started guessing")
        return

    elif text == "👤 پروفایل":
        user = await get_user(user_id)
        if user:
            await update.message.reply_text(
                f"👤 پروفایل شما:\n\n"
                f"🆔 ID: {user_id}\n"
                f"👤 نام کاربری: {user.get('username', 'Unknown')}\n"
                f"💰 موجودی: {user.get('balance', 0):,} تومان\n"
                f"📊 تعداد دعوت‌ها: {user.get('referrals', 0)}\n"
                f"💸 کل درآمد: {user.get('total_earned', 0):,} تومان\n"
                f"🎯 فرصت‌های رایگان: {user.get('guesses_left', 0)}",
                reply_markup=get_main_menu()
            )
            logger.debug(f"Profile shown for {user_id}")
        return

    elif text == "📩 دعوت دوستان":
        referral_link = f"https://t.me/HadsCashBot?start={user_id}"
        await update.message.reply_text(
            f"📩 دعوت از دوستان:\n\n"
            f"💫 دوستان خود را دعوت کنید و به ازای هر نفر {REFERRAL_BONUS:,} تومان دریافت کنید! 💰\n\n"
            f"🔗 لینک دعوت شما:\n{referral_link}\n\n"
            f"📢 ربات حدس کَش:\n🎲 با حدس عدد درست درآمد کسب کنید!",
            reply_markup=get_main_menu()
        )
        logger.debug(f"Invite link sent to {user_id}")
        return

    elif text == "💰 موجودی":
        await update.message.reply_text(
            "💰 مدیریت موجودی:\n\n"
            "💸 نمایش موجودی فعلی\n"
            "💳 افزایش موجودی با پرداخت\n"
            "🔙 بازگشت به منوی اصلی",
            reply_markup=get_balance_menu()
        )
        logger.debug(f"Balance menu shown for {user_id}")
        return

    elif text == "💸 نمایش موجودی":
        user = await get_user(user_id)
        balance = user.get("balance", 0) if user else 0
        await update.message.reply_text(
            f"💸 موجودی شما: {balance:,} تومان 💰\n\n"
            f"💡 برای افزایش موجودی از گزینه '💳 افزایش موجودی' استفاده کنید.",
            reply_markup=get_balance_menu()
        )
        logger.debug(f"Balance shown for {user_id}: {balance}")
        return

    elif text == "💳 افزایش موجودی":
        await update.message.reply_text(
            "💳 افزایش موجودی:\n\n"
            "💰 مبلغ مورد نظر برای افزایش موجودی را وارد کنید:\n\n"
            "📝 مثال: 50000\n"
            "💡 حداقل مبلغ: 20,000 تومان",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت به منو")]], resize_keyboard=True)
        )
        context.user_data["state"] = "increase_balance"
        logger.debug(f"User {user_id} prompted to increase balance")
        return

    elif text == "🔙 بازگشت به منو":
        context.user_data["state"] = None
        await update.message.reply_text(
            "🏠 منوی اصلی:",
            reply_markup=get_main_menu()
        )
        logger.debug(f"User {user_id} returned to main menu")
        return

    elif text == "ℹ️ راهنما":
        await update.message.reply_text(
            "📖 راهنمای ربات حدس کَش:\n\n"
            "🎮 شروع بازی: حدس عدد بین ۱ تا ۱۰۰۰\n"
            "💰 جایزه برنده: ۱۰۰,۰۰۰ تومان\n"
            "💫 هر کاربر هفته‌ای یک فرصت رایگان\n"
            "📩 دعوت دوستان: ۵,۰۰۰ تومان به ازای هر نفر\n"
            "💳 افزایش موجودی: از طریق درگاه پرداخت\n\n"
            "📞 پشتیبانی: @HadsCashSupport",
            reply_markup=get_main_menu()
        )
        return

    # Handle states
    if state == "guessing":
        try:
            guess = int(text)
            if not 1 <= guess <= 1000:
                await update.message.reply_text("❌ لطفاً یک عدد بین ۱ تا ۱۰۰۰ وارد کنید! 🔢")
                logger.debug(f"Invalid guess by {user_id}: {guess}")
                return
            
            user = await get_user(user_id)
            if not user:
                await update.message.reply_text("❌ خطا در دریافت اطلاعات کاربر!")
                return

            # Use free guess or deduct from balance
            if user["guesses_left"] > 0:
                await update_user(user_id=user_id, guesses_left=user["guesses_left"] - 1)
                logger.debug(f"Used free guess for {user_id}")
            else:
                if user["balance"] < MIN_BALANCE_FOR_GUESS:
                    await update.message.reply_text(
                        "❌ موجودی شما کافی نیست! 💸\n"
                        "لطفاً موجودی خود را افزایش دهید.",
                        reply_markup=get_main_menu()
                    )
                    context.user_data["state"] = None
                    return
                await update_user(user_id=user_id, balance=user["balance"] - MIN_BALANCE_FOR_GUESS)
                logger.debug(f"Deducted {MIN_BALANCE_FOR_GUESS} from {user_id}'s balance")

            # Check if guess is correct
            if guess == WINNING_NUMBER:
                prize = 100000  # 100,000 Toman prize
                new_balance = user["balance"] + prize
                new_total_earned = user["total_earned"] + prize
                
                await update_user(
                    user_id=user_id,
                    balance=new_balance,
                    total_earned=new_total_earned
                )
                
                await update.message.reply_text(
                    f"🎉 تبریک! شما برنده شدید! 🏆\n\n"
                    f"🎯 عدد برنده: {WINNING_NUMBER}\n"
                    f"💰 جایزه شما: {prize:,} تومان\n"
                    f"💸 موجودی جدید: {new_balance:,} تومان\n\n"
                    f"🎮 برای بازی مجدد از منوی اصلی استفاده کنید!",
                    reply_markup=get_main_menu()
                )
                
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🏆 برنده جدید!\n\n"
                         f"🆔 ID: {user_id}\n"
                         f"👤 Username: {user.get('username')}\n"
                         f"💰 جایزه: {prize:,} تومان\n"
                         f"📊 تعداد دعوت‌ها: {user.get('referrals', 0)}\n"
                         f"💸 کل درآمد: {new_total_earned:,} تومان"
                )
                logger.debug(f"User {user_id} won {prize} with guess {guess}")
            else:
                await update.message.reply_text(
                    f"❌ اشتباه بود! عدد برنده {WINNING_NUMBER} بود.\n\n"
                    f"💫 شانس خود را هفته آینده دوباره امتحان کنید!\n"
                    f"📩 یا دوستان خود را دعوت کنید و موجودی دریافت کنید.",
                    reply_markup=get_main_menu()
                )
                logger.debug(f"Wrong guess by {user_id}: {guess}")
            
            context.user_data["state"] = None
            
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید! 🔢")
            logger.debug(f"Non-numeric guess by {user_id}: {text}")
        return

    elif state == "increase_balance":
        try:
            amount = int(text)
            if amount < 20000:
                await update.message.reply_text("❌ حداقل مبلغ ۲۰,۰۰۰ تومان است! 💸")
                return
            
            tron_amount = await toman_to_tron(amount)
            
            await update.message.reply_text(
                f"💳 درخواست افزایش موجودی:\n\n"
                f"💰 مبلغ: {amount:,} تومان\n"
                f"🔢 مقدار TRX مورد نیاز: {tron_amount:.2f}\n\n"
                f"🏦 آدرس TRON:\n`{TRON_ADDRESS}`\n\n"
                f"📋 دستورالعمل:\n"
                f"۱. مبلغ {tron_amount:.2f} TRX به آدرس بالا واریز کنید\n"
                f"۲. اسکرین‌شات پرداخت را ارسال کنید\n"
                f"۳. پس از تأیید ادمین، موجودی شما اضافه می‌شود\n\n"
                f"💡 توجه: هزینه شبکه (۱ TRX) محاسبه شده است",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت به منو")]], resize_keyboard=True)
            )
            
            context.user_data["deposit_amount"] = amount
            context.user_data["tron_amount"] = tron_amount
            context.user_data["state"] = "waiting_payment_proof"
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📥 درخواست افزایش موجودی:\n\n"
                     f"🆔 ID: {user_id}\n"
                     f"👤 Username: {update.effective_user.username or 'Unknown'}\n"
                     f"💰 مبلغ: {amount:,} تومان\n"
                     f"🔢 مقدار TRX: {tron_amount:.2f}"
            )
            logger.debug(f"Deposit request by {user_id}: {amount} Toman ({tron_amount} TRX)")
            
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید! 🔢")
            logger.debug(f"Non-numeric balance input by {user_id}: {text}")
        return

    elif state == "waiting_payment_proof":
        # User should send screenshot/photos
        if update.message.photo or update.message.document:
            amount = context.user_data.get("deposit_amount", 0)
            tron_amount = context.user_data.get("tron_amount", 0)
            
            # Forward to admin
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📸 اسکرین‌شات پرداخت دریافت شد:\n\n"
                     f"🆔 ID: {user_id}\n"
                     f"👤 Username: {update.effective_user.username or 'Unknown'}\n"
                     f"💰 مبلغ: {amount:,} تومان\n"
                     f"🔢 مقدار TRX: {tron_amount:.2f}"
            )
            
            # Forward the media to admin
            if update.message.photo:
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=update.message.photo[-1].file_id,
                    caption=f"اسکرین‌شات پرداخت کاربر {user_id}"
                )
            elif update.message.document:
                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=update.message.document.file_id,
                    caption=f"اسکرین‌شات پرداخت کاربر {user_id}"
                )
            
            await update.message.reply_text(
                "✅ اسکرین‌شات پرداخت دریافت شد! 📸\n\n"
                "⏳ پس از تأیید ادمین، موجودی شما اضافه خواهد شد.\n"
                "📞 در صورت نیاز به پیگیری با پشتیبانی تماس بگیرید.",
                reply_markup=get_main_menu()
            )
            
            context.user_data["state"] = None
            logger.debug(f"Payment proof received from {user_id}")
        else:
            await update.message.reply_text(
                "❌ لطفاً اسکرین‌شات پرداخت را ارسال کنید! 📸\n\n"
                "💡 می‌توانید عکس یا فایل اسکرین‌شات را ارسال کنید.",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت به منو")]], resize_keyboard=True)
            )
        return

# Admin command to add balance
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("❌ فرمت دستور:\n/add_balance <user_id> <amount>")
        return
    
    try:
        target_user_id = int(context.args[0])
        amount = int(context.args[1])
        
        user = await get_user(target_user_id)
        if not user:
            await update.message.reply_text("❌ کاربر یافت نشد!")
            return
        
        new_balance = user["balance"] + amount
        await update_user(user_id=target_user_id, balance=new_balance)
        
        await update.message.reply_text(
            f"✅ موجودی کاربر {target_user_id} به {new_balance:,} تومان افزایش یافت."
        )
        
        # Notify user
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"💰 موجودی شما به مبلغ {amount:,} تومان افزایش یافت!\n\n"
                 f💸 موجودی جدید: {new_balance:,} تومان\n"
                 f"🎮 اکنون می‌توانید بازی کنید!",
            reply_markup=get_main_menu()
        )
        
        logger.debug(f"Balance added for {target_user_id}: {amount}")
        
    except (ValueError, IndexError):
        await update.message.reply_text("❌ خطا در پارامترها!")

# Webhook handler
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        logger.debug(f"Webhook received: {data}")
        update = Update.de_json(data, application.bot)
        if update:
            await application.update_queue.put(update)
            logger.debug("Update added to queue")
        else:
            logger.warning("Invalid update received")
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"ok": False}

# Startup and shutdown
@app.on_event("startup")
async def on_startup():
    try:
        await init_db()
        await application.bot.set_webhook(url=WEBHOOK_URL, max_connections=40)
        logger.info(f"Webhook set: {WEBHOOK_URL}")
        await application.initialize()
        await application.start()
        logger.info("Application started")
    except Exception as e:
        logger.error(f"Startup error: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await application.stop()
        await application.shutdown()
        if db_pool:
            await db_pool.close()
        logger.info("Application stopped")
    except Exception as e:
        logger.error(f"Shutdown error: {e}")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("set_number", set_number))
application.add_handler(CommandHandler("add_balance", add_balance))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_message))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
