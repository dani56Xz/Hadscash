import os
import logging
import aiohttp
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)

# Bot configuration
TOKEN = "8272958952:AAEixe1Zn3Ba8cZeUMSw8WFxxrVFuk9QOpI"
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = f"https://api.telegram.org/bot8272958952:AAEixe1Zn3Ba8cZeUMSw8WFxxrVFuk9QOpI/setWebhook?url=https://hadscash.onrender.com/webhook/hadscash-secret&max_connections=40{WEBHOOK_PATH}"
ADMIN_ID = 5542927340
CHANNEL_ID = "@hadscash"
TRON_ADDRESS = "TJ4xrwKJzKjk6FgKfuuqwah3Az5Ur22kJb"
WINNING_NUMBER = 341
MIN_BALANCE_FOR_GUESS = 20000  # 20,000 Toman
REFERRAL_BONUS = 5000  # 5,000 Toman

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# FastAPI app
app = FastAPI()

# Telegram bot application
application = Application.builder().token(TOKEN).build()

# In-memory user data (replace with a database in production)
users = {}  # Format: {user_id: {"username": str, "balance": int, "guesses_left": int, "last_free_guess": datetime, "referrals": int, "total_earned": int}}

# Main menu keyboard
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("شروع بازی", callback_data="start_game")],
        [InlineKeyboardButton("پروفایل", callback_data="profile")],
        [InlineKeyboardButton("دعوت دوستان", callback_data="invite")],
        [InlineKeyboardButton("موجودی", callback_data="balance")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Balance menu keyboard
def get_balance_menu():
    keyboard = [
        [InlineKeyboardButton("نمایش موجودی", callback_data="show_balance")],
        [InlineKeyboardButton("افزایش موجودی", callback_data="increase_balance")],
        [InlineKeyboardButton("بازگشت به منو", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Check channel membership
async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# Fetch TRON price in USD
async def get_tron_price():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=tron&vs_currencies=usd") as resp:
            data = await resp.json()
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
    username = update.effective_user.username or "Unknown"
    
    # Initialize user data if new
    if user_id not in users:
        users[user_id] = {
            "username": username,
            "balance": 0,
            "guesses_left": 1,  # Free guess for new users
            "last_free_guess": datetime.now(),
            "referrals": 0,
            "total_earned": 0
        }
    
    # Check for referral
    args = context.args
    if args and args[0].isdigit():
        referrer_id = int(args[0])
        if referrer_id != user_id and referrer_id in users:
            if await check_membership(context.bot, user_id):
                users[referrer_id]["balance"] += REFERRAL_BONUS
                users[referrer_id]["referrals"] += 1
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 یک نفر با لینک دعوت شما عضو شد! {REFERRAL_BONUS:,} تومان به موجودی شما اضافه شد."
                )

    # Check channel membership
    if not await check_membership(context.bot, user_id):
        keyboard = [[InlineKeyboardButton("عضویت در کانال", url="https://t.me/hadscash")]]
        await update.message.reply_text(
            "لطفاً ابتدا در کانال @hadscash عضو شوید و سپس دوباره /start را بزنید!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Notify admin of new member
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🎉 کاربر جدید:\nID: {user_id}\nUsername: {username}"
    )

    # Welcome message
    await update.message.reply_text(
        "🎮 به ربات حدس کَش خوش آمدید!\nبا حدس عدد درست (۱ تا ۱۰۰۰) می‌توانید درآمد کسب کنید!",
        reply_markup=get_main_menu()
    )

# Admin command to set winning number
async def set_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("لطفاً عدد را وارد کنید: /set_number <عدد>")
        return
    global WINNING_NUMBER
    WINNING_NUMBER = int(context.args[0])
    await update.message.reply_text(f"عدد برنده به {WINNING_NUMBER} تغییر کرد.")

# Handle button clicks
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "main_menu":
        await query.message.edit_text("منوی اصلی:", reply_markup=get_main_menu())
        return

    if data == "start_game":
        user = users.get(user_id, {})
        now = datetime.now()
        last_guess = user.get("last_free_guess", now - timedelta(days=8))
        if (now - last_guess).days >= 7:
            users[user_id]["guesses_left"] = 1
            users[user_id]["last_free_guess"] = now
        if user.get("guesses_left", 0) == 0 and user.get("balance", 0) < MIN_BALANCE_FOR_GUESS:
            await query.message.edit_text(
                "❌ شانس شما تمام شده است! برای ادامه:\n1️⃣ دوستان خود را دعوت کنید\n2️⃣ موجودی خود را افزایش دهید\n3️⃣ تا هفته بعد صبر کنید",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت به منو", callback_data="main_menu")]])
            )
            return
        await query.message.edit_text(
            "🎲 یک عدد بین ۱ تا ۱۰۰۰ حدس بزنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت به منو", callback_data="main_menu")]])
        )
        context.user_data["state"] = "guessing"
        return

    if data == "profile":
        user = users.get(user_id, {})
        await query.message.edit_text(
            f"👤 پروفایل شما:\nID: {user_id}\nUsername: {user.get('username', 'Unknown')}\nموجودی: {user.get('balance', 0):,} تومان\nتعداد دعوت‌ها: {user.get('referrals', 0)}\nکل درآمد: {user.get('total_earned', 0):,} تومان",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت به منو", callback_data="main_menu")]])
        )
        return

    if data == "invite":
        referral_link = f"https://t.me/HadsCashBot?start={user_id}"
        await query.message.edit_text(
            f"📩 دوستان خود را دعوت کنید و به ازای هر نفر {REFERRAL_BONUS:,} تومان دریافت کنید!\nلینک دعوت شما:\n{referral_link}\n\n📢 ربات حدس کَش: با حدس عدد درست درآمد کسب کنید!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت به منو", callback_data="main_menu")]])
        )
        return

    if data == "balance":
        await query.message.edit_text("💰 مدیریت موجودی:", reply_markup=get_balance_menu())
        return

    if data == "show_balance":
        balance = users.get(user_id, {}).get("balance", 0)
        await query.message.edit_text(
            f"💸 موجودی شما: {balance:,} تومان",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت به منو", callback_data="main_menu")]])
        )
        return

    if data == "increase_balance":
        await query.message.edit_text(
            "💳 مبلغ مورد نظر برای افزایش موجودی را وارد کنید (مثال: 50000)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت به منو", callback_data="main_menu")]])
        )
        context.user_data["state"] = "increase_balance"
        return

# Handle user guesses
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("state")

    if state == "guessing":
        try:
            guess = int(update.message.text)
            if not 1 <= guess <= 1000:
                await update.message.reply_text("لطفاً یک عدد بین ۱ تا ۱۰۰۰ وارد کنید!")
                return
            user = users[user_id]
            if user["guesses_left"] > 0:
                user["guesses_left"] -= 1
            else:
                user["balance"] -= MIN_BALANCE_FOR_GUESS

            if guess == WINNING_NUMBER:
                prize = 100000  # Example prize, adjust as needed
                user["balance"] += prize
                user["total_earned"] += prize
                await update.message.reply_text(
                    f"🎉 تبریک! شما برنده {prize:,} تومان شدید!",
                    reply_markup=get_main_menu()
                )
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🏆 برنده جدید!\nID: {user_id}\nUsername: {user.get('username')}\nجایزه: {prize:,} تومان\nتعداد دعوت‌ها: {user.get('referrals', 0)}\nکل درآمد: {user.get('total_earned', 0):,}"
                )
            else:
                await update.message.reply_text(
                    "❌ اشتباه بود! شانس شما تمام شده است. برای ادامه:\n1️⃣ دوستان خود را دعوت کنید\n2️⃣ موجودی خود را افزایش دهید\n3️⃣ تا هفته بعد صبر کنید",
                    reply_markup=get_main_menu()
                )
            context.user_data["state"] = None
        except ValueError:
            await update.message.reply_text("لطفاً یک عدد معتبر وارد کنید!")
        return

    if state == "increase_balance":
        try:
            amount = int(update.message.text)
            if amount <= 0:
                await update.message.reply_text("لطفاً مبلغ معتبر وارد کنید!")
                return
            tron_amount = await toman_to_tron(amount)
            await update.message.reply_text(
                f"برای افزایش موجودی به مبلغ {amount:,} تومان، لطفاً {tron_amount:.2f} TRX به آدرس زیر واریز کنید:\n{TRON_ADDRESS}\n\nپس از واریز، ادمین تأیید می‌کند و موجودی شما افزایش می‌یابد.",
                reply_markup=get_main_menu()
            )
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📥 درخواست افزایش موجودی:\nID: {user_id}\nUsername: {users[user_id].get('username')}\nمبلغ: {amount:,} تومان\nمقدار TRX: {tron_amount:.2f}"
            )
            context.user_data["state"] = None
        except ValueError:
            await update.message.reply_text("لطفاً یک عدد معتبر وارد کنید!")
        return

# Webhook handler
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

# Startup and shutdown
@app.on_event("startup")
async def on_startup():
    await application.bot.set_webhook(url=WEBHOOK_URL)
    print("✅ Webhook set:", WEBHOOK_URL)
    await application.initialize()
    await application.start()

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("set_number", set_number))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
