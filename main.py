import os
import logging
import aiohttp
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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
DATABASE_URL = "postgresql://neondb_owner:npg_sAQj9gCK3wly@ep-winter-cherry-aezv1w77-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# Database setup
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True)
    username = Column(String)
    balance = Column(Float, default=0)
    guesses_left = Column(Integer, default=1)
    last_free_guess = Column(DateTime, default=datetime.now)
    referrals = Column(Integer, default=0)
    total_earned = Column(Float, default=0)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# FastAPI app
app = FastAPI()

# Telegram bot application
application = Application.builder().token(TOKEN).build()

# Main menu keyboard
def get_main_menu():
    keyboard = [
        ["🎮 شروع بازی"],
        ["👤 پروفایل", "📩 دعوت دوستان"],
        ["💰 موجودی"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# Balance menu keyboard
def get_balance_menu():
    keyboard = [
        ["💸 نمایش موجودی"],
        ["💳 افزایش موجودی"],
        ["🔙 بازگشت به منو"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# Check channel membership
async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        logger.debug(f"🎯 بررسی عضویت کاربر {user_id}: {member.status}")
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"❌ خطا در بررسی عضویت کاربر {user_id}: {e}")
        return False

# Fetch TRON price in USD
async def get_tron_price():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=tron&vs_currencies=usd") as resp:
            data = await resp.json()
            logger.debug(f"💹 قیمت TRON دریافت شد: {data}")
            return data["tron"]["usd"]

# Convert Toman to TRON with fee consideration (assuming 1 TRX fee)
async def toman_to_tron(toman):
    usd_per_toman = 0.000016  # Approximate USD/Toman rate (update as needed)
    tron_price_usd = await get_tron_price()
    usd_amount = toman * usd_per_toman
    tron_amount = usd_amount / tron_price_usd
    return tron_amount + 1  # Add 1 TRX for transaction fee

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "بدون نام 😎"
    logger.debug(f"🚀 دریافت /start از کاربر {user_id} ({username})")
    
    # Initialize user data if new
    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    if not user:
        user = User(
            user_id=user_id,
            username=username,
            balance=0,
            guesses_left=1,
            last_free_guess=datetime.now(),
            referrals=0,
            total_earned=0
        )
        session.add(user)
        session.commit()
        logger.debug(f"🌟 کاربر جدید ایجاد شد: {user_id}")
    
    # Check for referral
    args = context.args
    if args and args[0].isdigit():
        referrer_id = int(args[0])
        if referrer_id != user_id:
            referrer = session.query(User).filter_by(user_id=referrer_id).first()
            if referrer and await check_membership(context.bot, user_id):
                referrer.balance += REFERRAL_BONUS
                referrer.referrals += 1
                session.commit()
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 یه نفر با لینک دعوتت عضو شد! {REFERRAL_BONUS:,} 💸 به موجودیت اضافه شد!"
                )
                logger.debug(f"🎁 پاداش دعوت برای {referrer_id} توسط {user_id}")

    # Check channel membership
    if not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "🚫 لطفاً اول توی کانال @hadscash عضو شو و بعد دوباره /start بزن! 😊",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        logger.debug(f"⛔ کاربر {user_id} عضو کانال نیست، درخواست عضویت")
        session.close()
        return

    # Notify admin of new member
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🌟 کاربر جدید:\n🆔 ID: {user_id}\n👤 Username: {username}"
    )
    logger.debug(f"📩 ادمین از کاربر جدید {user_id} مطلع شد")

    # Welcome message
    await update.message.reply_text(
        "🎉 به ربات *حدس کَش* خوش اومدی! 😎\nبا حدس عدد درست (۱ تا ۱۰۰۰) می‌تونی پول دربیاری! 💰",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    logger.debug(f"✅ پیام خوش‌آمد به کاربر {user_id} ارسال شد")
    session.close()

# Admin command to set winning number
async def set_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        logger.debug(f"🚫 تلاش غیرمجاز برای set_number توسط {user_id}")
        return
    if not context.args:
        await update.message.reply_text("❌ لطفاً عدد رو وارد کن: /set_number <عدد> 🔢")
        logger.debug(f"⛔ عدد برای set_number توسط {user_id} وارد نشده")
        return
    global WINNING_NUMBER
    WINNING_NUMBER = int(context.args[0])
    await update.message.reply_text(f"🎯 عدد برنده به {WINNING_NUMBER} تغییر کرد! ✅")
    logger.debug(f"🔢 عدد برنده به {WINNING_NUMBER} توسط ادمین {user_id} تنظیم شد")

# Handle button clicks
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    logger.debug(f"🖱️ دکمه کلیک شده توسط {user_id}: {data}")

    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()

    if data == "main_menu":
        await query.message.reply_text("🏠 منوی اصلی:", reply_markup=get_main_menu())
        logger.debug(f"🏠 منوی اصلی برای {user_id} نمایش داده شد")
        session.close()
        return

    if data == "start_game":
        now = datetime.now()
        last_guess = user.last_free_guess or now - timedelta(days=8)
        if (now - last_guess).days >= 7:
            user.guesses_left = 1
            user.last_free_guess = now
            session.commit()
            logger.debug(f"🎟️ شانس رایگان برای {user_id} ریست شد")
        if user.guesses_left == 0 and user.balance < MIN_BALANCE_FOR_GUESS:
            await query.message.reply_text(
                "❌ اوه! شانس یا موجودیت کافی نیست! 😕\nبرای ادامه می‌تونی:\n1️⃣ دوستاتو دعوت کن 📩\n2️⃣ موجودیتو افزایش بده 💳\n3️⃣ تا هفته بعد صبر کن ⏳",
                reply_markup=get_main_menu()
            )
            logger.debug(f"⛔ کاربر {user_id} شانس یا موجودی کافی نداره")
            session.close()
            return
        await query.message.reply_text(
            "🎲 یه عدد بین ۱ تا ۱۰۰۰ حدس بزن! 🔢",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["state"] = "guessing"
        logger.debug(f"🎮 کاربر {user_id} شروع به حدس زدن کرد")
        session.close()
        return

    if data == "profile":
        await query.message.reply_text(
            f"👤 *پروفایلت*:\n🆔 *ID*: {user_id}\n📛 *نام*: {user.username}\n💸 *موجودی*: {user.balance:,} تومان\n👥 *دعوت‌ها*: {user.referrals}\n🏆 *کل درآمد*: {user.total_earned:,} تومان",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
        logger.debug(f"📋 پروفایل برای {user_id} نمایش داده شد")
        session.close()
        return

    if data == "invite":
        referral_link = f"https://t.me/HadsCashBot?start={user_id}"
        await query.message.reply_text(
            f"📩 دوستاتو دعوت کن و برای هر نفر {REFERRAL_BONUS:,} تومان بگیر! 💰\nلینکت:\n{referral_link}\n\n🎉 *حدس کَش*: با حدس عدد درست پول دربیار! 😎",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
        logger.debug(f"🔗 لینک دعوت به {user_id} ارسال شد")
        session.close()
        return

    if data == "balance":
        await query.message.reply_text("💰 مدیریت موجودی:", reply_markup=get_balance_menu())
        logger.debug(f"📊 منوی موجودی برای {user_id} نمایش داده شد")
        session.close()
        return

    if data == "show_balance":
        await query.message.reply_text(
            f"💸 موجودیت: {user.balance:,} تومان 😎",
            reply_markup=get_main_menu()
        )
        logger.debug(f"💰 موجودی برای {user_id}: {user.balance}")
        session.close()
        return

    if data == "increase_balance":
        await query.message.reply_text(
            "💳 مبلغی که می‌خوای به موجودیت اضافه کنی رو وارد کن (مثال: 50000) 🔢",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["state"] = "increase_balance_amount"
        logger.debug(f"💳 کاربر {user_id} برای افزایش موجودی درخواست داد")
        session.close()
        return

# Handle user messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("state")
    logger.debug(f"📩 پیام دریافتی از {user_id} در حالت {state}: {update.message.text}")

    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()

    if state == "guessing":
        try:
            guess = int(update.message.text)
            if not 1 <= guess <= 1000:
                await update.message.reply_text("❌ لطفاً یه عدد بین ۱ تا ۱۰۰۰ وارد کن! 🔢")
                logger.debug(f"⛔ حدس نامعتبر توسط {user_id}: {guess}")
                session.close()
                return
            if user.guesses_left > 0:
                user.guesses_left -= 1
                logger.debug(f"🎟️ شانس رایگان برای {user_id} استفاده شد")
            else:
                if user.balance < MIN_BALANCE_FOR_GUESS:
                    await update.message.reply_text(
                        "❌ موجودیت کافی نیست! 😕\nبرای ادامه می‌تونی:\n1️⃣ دوستاتو دعوت کن 📩\n2️⃣ موجودیتو افزایش بده 💳\n3️⃣ تا هفته بعد صبر کن ⏳",
                        reply_markup=get_main_menu()
                    )
                    logger.debug(f"⛔ کاربر {user_id} موجودی کافی نداره")
                    session.close()
                    return
                user.balance -= MIN_BALANCE_FOR_GUESS
                logger.debug(f"💸 کسر {MIN_BALANCE_FOR_GUESS} از موجودی {user_id}")

            if guess == WINNING_NUMBER:
                prize = 100000  # Example prize, adjust as needed
                user.balance += prize
                user.total_earned += prize
                await update.message.reply_text(
                    f"🎉 *تبریک*! برنده {prize:,} تومان شدی! 🤑",
                    reply_markup=get_main_menu(),
                    parse_mode="Markdown"
                )
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🏆 *برنده جدید*!\n🆔 ID: {user_id}\n👤 Username: {user.username}\n🎁 جایزه: {prize:,} تومان\n👥 تعداد دعوت‌ها: {user.referrals}\n🏆 کل درآمد: {user.total_earned:,} تومان",
                    parse_mode="Markdown"
                )
                logger.debug(f"🎉 کاربر {user_id} با حدس {guess} برنده {prize} شد")
            else:
                await update.message.reply_text(
                    "❌ اشتباه بود! 😕 شانس یا موجودیت تموم شد. برای ادامه:\n1️⃣ دوستاتو دعوت کن 📩\n2️⃣ موجودیتو افزایش بده 💳\n3️⃣ تا هفته بعد صبر کن ⏳",
                    reply_markup=get_main_menu()
                )
                logger.debug(f"⛔ حدس اشتباه توسط {user_id}: {guess}")
            session.commit()
            context.user_data["state"] = None
        except ValueError:
            await update.message.reply_text("❌ لطفاً یه عدد معتبر وارد کن! 🔢")
            logger.debug(f"⛔ حدس غیرعددی توسط {user_id}: {update.message.text}")
        session.close()
        return

    if state == "increase_balance_amount":
        try:
            amount = int(update.message.text)
            if amount <= 0:
                await update.message.reply_text("❌ مبلغ باید معتبر باشه! مثلاً 50000 🔢")
                logger.debug(f"⛔ مبلغ نامعتبر توسط {user_id}: {update.message.text}")
                session.close()
                return
            tron_amount = await toman_to_tron(amount)
            context.user_data["deposit_amount"] = amount
            await update.message.reply_text(
                f"💳 برای افزایش موجودی {amount:,} تومان، لطفاً {tron_amount:.2f} TRX به این آدرس واریز کن:\n`{TRON_ADDRESS}`\n\n📸 بعد از واریز، اسکرین‌شات پرداخت رو اینجا بفرست! 😊",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
            context.user_data["state"] = "awaiting_screenshot"
            logger.debug(f"💸 درخواست واریز {amount} تومان ({tron_amount} TRX) توسط {user_id}")
            session.close()
        except ValueError:
            await update.message.reply_text("❌ لطفاً یه عدد معتبر وارد کن! 🔢")
            logger.debug(f"⛔ ورودی غیرعددی برای مبلغ توسط {user_id}: {update.message.text}")
            session.close()
        return

    if state == "awaiting_screenshot":
        if update.message.photo:
            amount = context.user_data.get("deposit_amount", 0)
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=update.message.photo[-1].file_id,
                caption=f"📸 اسکرین‌شات پرداخت از:\n🆔 ID: {user_id}\n👤 Username: {user.username}\n💳 مبلغ: {amount:,} تومان"
            )
            await update.message.reply_text(
                "✅ اسکرین‌شات دریافت شد! ادمین بررسی می‌کنه و موجودیت به‌زودی افزایش پیدا می‌کنه! 😊",
                reply_markup=get_main_menu()
            )
            logger.debug(f"📸 اسکرین‌شات پرداخت از {user_id} برای {amount} تومان به ادمین ارسال شد")
            context.user_data["state"] = None
            context.user_data["deposit_amount"] = 0
        else:
            await update.message.reply_text("❌ لطفاً اسکرین‌شات پرداخت رو بفرست! 📸")
            logger.debug(f"⛔ پیام غیرعکس در حالت انتظار اسکرین‌شات از {user_id}")
        session.close()
        return

    # Handle main menu selections
    if update.message.text == "🎮 شروع بازی":
        now = datetime.now()
        last_guess = user.last_free_guess or now - timedelta(days=8)
        if (now - last_guess).days >= 7:
            user.guesses_left = 1
            user.last_free_guess = now
            session.commit()
            logger.debug(f"🎟️ شانس رایگان برای {user_id} ریست شد")
        if user.guesses_left == 0 and user.balance < MIN_BALANCE_FOR_GUESS:
            await update.message.reply_text(
                "❌ اوه! شانس یا موجودیت کافی نیست! 😕\nبرای ادامه می‌تونی:\n1️⃣ دوستاتو دعوت کن 📩\n2️⃣ موجودیتو افزایش بده 💳\n3️⃣ تا هفته بعد صبر کن ⏳",
                reply_markup=get_main_menu()
            )
            logger.debug(f"⛔ کاربر {user_id} شانس یا موجودی کافی نداره")
            session.close()
            return
        await update.message.reply_text(
            "🎲 یه عدد بین ۱ تا ۱۰۰۰ حدس بزن! 🔢",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["state"] = "guessing"
        logger.debug(f"🎮 کاربر {user_id} شروع به حدس زدن کرد")
        session.close()
        return

    if update.message.text == "👤 پروفایل":
        await update.message.reply_text(
            f"👤 *پروفایلت*:\n🆔 *ID*: {user_id}\n📛 *نام*: {user.username}\n💸 *موجودی*: {user.balance:,} تومان\n👥 *دعوت‌ها*: {user.referrals}\n🏆 *کل درآمد*: {user.total_earned:,} تومان",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
        logger.debug(f"📋 پروفایل برای {user_id} نمایش داده شد")
        session.close()
        return

    if update.message.text == "📩 دعوت دوستان":
        referral_link = f"https://t.me/HadsCashBot?start={user_id}"
        await update.message.reply_text(
            f"📩 دوستاتو دعوت کن و برای هر نفر {REFERRAL_BONUS:,} تومان بگیر! 💰\nلینکت:\n{referral_link}\n\n🎉 *حدس کَش*: با حدس عدد درست پول دربیار! 😎",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
        logger.debug(f"🔗 لینک دعوت به {user_id} ارسال شد")
        session.close()
        return

    if update.message.text == "💰 موجودی":
        await update.message.reply_text("💰 مدیریت موجودی:", reply_markup=get_balance_menu())
        logger.debug(f"📊 منوی موجودی برای {user_id} نمایش داده شد")
        session.close()
        return

    if update.message.text == "💸 نمایش موجودی":
        await update.message.reply_text(
            f"💸 موجودیت: {user.balance:,} تومان 😎",
            reply_markup=get_main_menu()
        )
        logger.debug(f"💰 موجودی برای {user_id}: {user.balance}")
        session.close()
        return

    if update.message.text == "💳 افزایش موجودی":
        await update.message.reply_text(
            "💳 مبلغی که می‌خوای به موجودیت اضافه کنی رو وارد کن (مثال: 50000) 🔢",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["state"] = "increase_balance_amount"
        logger.debug(f"💳 کاربر {user_id} برای افزایش موجودی درخواست داد")
        session.close()
        return

    if update.message.text == "🔙 بازگشت به منو":
        await update.message.reply_text("🏠 منوی اصلی:", reply_markup=get_main_menu())
        logger.debug(f"🏠 منوی اصلی برای {user_id} نمایش داده شد")
        session.close()
        return

# Webhook handler
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        logger.debug(f"🌐 وب‌هوک دریافت شد: {data}")
        update = Update.de_json(data, application.bot)
        if update:
            await application.update_queue.put(update)
            logger.debug("✅ آپدیت به صف اضافه شد")
        else:
            logger.warning("⚠️ آپدیت نامعتبر دریافت شد")
        return {"ok": True}
    except Exception as e:
        logger.error(f"❌ خطای وب‌هوک: {e}")
        return {"ok": False}

# Startup and shutdown
@app.on_event("startup")
async def on_startup():
    try:
        await application.bot.set_webhook(url=WEBHOOK_URL, max_connections=40)
        logger.info(f"🌐 وب‌هوک تنظیم شد: {WEBHOOK_URL}")
        await application.initialize()
        await application.start()
        logger.info("🚀 اپلیکیشن شروع شد")
    except Exception as e:
        logger.error(f"❌ خطا در استارت‌آپ: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await application.stop()
        await application.shutdown()
        logger.info("🛑 اپلیکیشن متوقف شد")
    except Exception as e:
        logger.error(f"❌ خطا در خاموشی: {e}")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("set_number", set_number))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(MessageHandler(filters.PHOTO, handle_message))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
