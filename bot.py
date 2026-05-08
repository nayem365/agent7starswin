import asyncio
import logging
import os
import re
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ServerSelectionTimeoutError

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Env vars ─────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]

# ─── Validation patterns ───────────────────────────────────────────────────────
NAME_RE = re.compile(
    r"^[A-Za-zÀ-ÿА-Яа-я'\-\.]+(?:\s+[A-Za-zÀ-ÿА-Яа-я'\-\.]+){1,3}$"
)
GAMING_ID_RE = re.compile(r"^\d{9,11}$")

# ─── FSM States ───────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    agreement      = State()
    location       = State()
    phone          = State()
    name           = State()
    currency       = State()
    photo1         = State()
    photo2         = State()
    experience     = State()
    street         = State()
    topup          = State()
    gaming_id      = State()


class AdminReject(StatesGroup):
    waiting_reason = State()


# ─── MongoDB ──────────────────────────────────────────────────────────────────
db_client: AsyncIOMotorClient = None
users_col = None


async def init_db():
    global db_client, users_col
    logger.info("Connecting to MongoDB…")
    db_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    # Force connection test
    await db_client.admin.command("ping")
    db = db_client.get_default_database("mobicash")
    users_col = db["users"]
    logger.info("MongoDB connected successfully.")


# ─── Helpers ──────────────────────────────────────────────────────────────────
async def notify_admins(bot: Bot, text: str, **kwargs):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


async def reverse_geocode(lat: float, lon: float) -> str:
    """Return ISO 4217 currency code for the given coordinates, fallback EUR."""
    # Mapping of country codes → currency
    COUNTRY_CURRENCY = {
        "US": "USD", "GB": "GBP", "EU": "EUR", "DE": "EUR", "FR": "EUR",
        "IT": "EUR", "ES": "EUR", "RU": "RUB", "UA": "UAH", "CN": "CNY",
        "JP": "JPY", "KR": "KRW", "IN": "INR", "BR": "BRL", "MX": "MXN",
        "CA": "CAD", "AU": "AUD", "NG": "NGN", "ZA": "ZAR", "KE": "KES",
        "GH": "GHS", "EG": "EGP", "MA": "MAD", "TZ": "TZS", "ET": "ETB",
        "CI": "XOF", "SN": "XOF", "CM": "XAF", "CD": "CDF", "UG": "UGX",
        "TH": "THB", "VN": "VND", "PH": "PHP", "ID": "IDR", "MY": "MYR",
        "PK": "PKR", "BD": "BDT", "TR": "TRY", "SA": "SAR", "AE": "AED",
        "QA": "QAR", "KW": "KWD", "OM": "OMR", "BH": "BHD", "IQ": "IQD",
        "IR": "IRR", "IL": "ILS", "PL": "PLN", "CZ": "CZK", "HU": "HUF",
        "RO": "RON", "SE": "SEK", "NO": "NOK", "DK": "DKK", "CH": "CHF",
        "SG": "SGD", "HK": "HKD", "NZ": "NZD", "AR": "ARS", "CL": "CLP",
        "CO": "COP", "PE": "PEN", "VE": "VES",
    }
    url = (
        f"https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": "MobicashBot/1.0"},
                timeout=aiohttp.ClientTimeout(total=6),
            ) as resp:
                data = await resp.json()
                cc = data.get("address", {}).get("country_code", "").upper()
                return COUNTRY_CURRENCY.get(cc, "EUR")
    except Exception as e:
        logger.warning(f"Geocoding failed: {e}")
        return "EUR"


def status_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📋 Request Status")]],
        resize_keyboard=True,
    )


# ─── Router ───────────────────────────────────────────────────────────────────
router = Router()


# ──────────────────────────────────────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    agreement_text = (
        "👋 Welcome to <b>Mobicash Agent Registration</b>!\n\n"
        "By continuing you agree to our <b>Terms & Conditions</b>:\n"
        "• You will act as an authorised Mobicash agent.\n"
        "• All information provided must be accurate and truthful.\n"
        "• Fraudulent registrations will result in permanent ban.\n\n"
        "Please read and accept the agreement to proceed."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ I Agree", callback_data="agree")]]
    )
    await message.answer(agreement_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.agreement)


# ──────────────────────────────────────────────────────────────────────────────
# Agreement callback
# ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "agree", Reg.agreement)
async def cb_agree(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_reply_markup()
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Share Location", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await call.message.answer(
        "📍 Please share your current location so we can determine your region.",
        reply_markup=kb,
    )
    await state.set_state(Reg.location)


# ──────────────────────────────────────────────────────────────────────────────
# Location
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Reg.location, F.location)
async def got_location(message: Message, state: FSMContext):
    lat = message.location.latitude
    lon = message.location.longitude
    await state.update_data(lat=lat, lon=lon)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Share Phone Number", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "✅ Location received!\n\n📞 Now please share your phone number.",
        reply_markup=kb,
    )
    await state.set_state(Reg.phone)


@router.message(Reg.location)
async def location_wrong(message: Message):
    await message.answer("Please use the button below to share your location. 👇")


# ──────────────────────────────────────────────────────────────────────────────
# Phone
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Reg.phone, F.contact)
async def got_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await message.answer(
        "✅ Phone number received!\n\n"
        "👤 Please enter your <b>full name</b>.\n\n"
        "<i>Rules: 2–4 words, letters only (English/Russian/French), "
        "hyphens/apostrophes/periods allowed, max 40 chars, not ALL CAPS.</i>\n\n"
        "Examples: <code>John Doe</code>, <code>Jean-Pierre Dupont</code>, "
        "<code>Иван Петров</code>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.name)


@router.message(Reg.phone)
async def phone_wrong(message: Message):
    await message.answer("Please use the button below to share your phone number. 👇")


# ──────────────────────────────────────────────────────────────────────────────
# Name
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Reg.name, F.text)
async def got_name(message: Message, state: FSMContext):
    name = message.text.strip()
    # Validations
    if len(name) > 40:
        await message.answer("❌ Name is too long (max 40 characters). Please try again.")
        return
    if name == name.upper() and any(c.isalpha() for c in name):
        await message.answer("❌ Please don't use ALL CAPS. Try again.")
        return
    if not NAME_RE.match(name):
        await message.answer(
            "❌ Invalid name format. Use 2–4 words with letters only "
            "(hyphens, apostrophes, periods allowed).\n\nTry again:"
        )
        return

    await state.update_data(full_name=name)

    # Determine local currency via reverse geocoding
    data = await state.get_data()
    local_currency = await reverse_geocode(data["lat"], data["lon"])
    await state.update_data(local_currency=local_currency)

    buttons = [[InlineKeyboardButton(text="🇺🇸 USD (default)", callback_data="currency_USD")]]
    if local_currency != "USD":
        buttons.append(
            [InlineKeyboardButton(text=f"🌍 {local_currency} (local)", callback_data=f"currency_{local_currency}")]
        )

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"✅ Name saved: <b>{name}</b>\n\n"
        "💱 Please select your preferred currency:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.currency)


@router.message(Reg.name)
async def name_not_text(message: Message):
    await message.answer("Please send your full name as text.")


# ──────────────────────────────────────────────────────────────────────────────
# Currency
# ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("currency_"), Reg.currency)
async def cb_currency(call: CallbackQuery, state: FSMContext):
    currency = call.data.split("_", 1)[1]
    await state.update_data(currency=currency)
    await call.answer(f"Selected: {currency}")
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✅ Currency set to <b>{currency}</b>.\n\n"
        "📄 Please send the <b>first photo</b> of your identity document "
        "(Passport / National ID / Driving Licence).",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.photo1)


# ──────────────────────────────────────────────────────────────────────────────
# Photo 1
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Reg.photo1, F.photo)
async def got_photo1(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo1_file_id=file_id)
    await message.answer(
        "✅ First photo received!\n\n"
        "📄 Please send the <b>second photo</b> of your identity document.",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.photo2)


@router.message(Reg.photo1)
async def photo1_wrong(message: Message):
    await message.answer("Please send a photo of your identity document.")


# ──────────────────────────────────────────────────────────────────────────────
# Photo 2
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Reg.photo2, F.photo)
async def got_photo2(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo2_file_id=file_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yes", callback_data="exp_yes"),
                InlineKeyboardButton(text="❌ No", callback_data="exp_no"),
            ]
        ]
    )
    # Send placeholder example image then ask about experience
    await message.answer_photo(
        photo="https://via.placeholder.com/400x250.png?text=MobCash+App+Example",
        caption=(
            "✅ Both photos received!\n\n"
            "Above is an example of the MobCash mobile app interface.\n\n"
            "Do you have experience working with the <b>MobCash mobile app</b>?"
        ),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.experience)


@router.message(Reg.photo2)
async def photo2_wrong(message: Message):
    await message.answer("Please send a photo of your identity document.")


# ──────────────────────────────────────────────────────────────────────────────
# Experience
# ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.in_({"exp_yes", "exp_no"}), Reg.experience)
async def cb_experience(call: CallbackQuery, state: FSMContext):
    experience = call.data == "exp_yes"
    await state.update_data(has_experience=experience)
    await call.answer()
    await call.message.edit_reply_markup()
    label = "Yes ✅" if experience else "No ❌"
    await call.message.answer(
        f"✅ Experience: <b>{label}</b>\n\n"
        "🏠 Please enter your <b>street name</b> (name only, not full address, min 2 chars):",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.street)


# ──────────────────────────────────────────────────────────────────────────────
# Street
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Reg.street, F.text)
async def got_street(message: Message, state: FSMContext):
    street = message.text.strip()
    if len(street) < 2:
        await message.answer("❌ Street name must be at least 2 characters. Try again:")
        return
    await state.update_data(street=street)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🪙 USDT", callback_data="topup_USDT"),
                InlineKeyboardButton(text="🔄 Other Crypto", callback_data="topup_OTHER"),
            ]
        ]
    )
    await message.answer(
        f"✅ Street saved: <b>{street}</b>\n\n"
        "💳 Please select your preferred <b>top-up method</b>:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.topup)


@router.message(Reg.street)
async def street_not_text(message: Message):
    await message.answer("Please send the street name as text.")


# ──────────────────────────────────────────────────────────────────────────────
# Top-up method
# ──────────────────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("topup_"), Reg.topup)
async def cb_topup(call: CallbackQuery, state: FSMContext):
    method = call.data.split("_", 1)[1]
    label = "🪙 USDT" if method == "USDT" else "🔄 Other Crypto"
    await state.update_data(topup_method=method)
    await call.answer()
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✅ Top-up method: <b>{label}</b>\n\n"
        "🎮 Please enter your <b>7starswin gaming ID</b> "
        "(numeric, 9–11 digits):",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.gaming_id)


# ──────────────────────────────────────────────────────────────────────────────
# Gaming ID
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Reg.gaming_id, F.text)
async def got_gaming_id(message: Message, state: FSMContext, bot: Bot):
    gid = message.text.strip()
    if not GAMING_ID_RE.match(gid):
        await message.answer(
            "❌ Invalid gaming ID. It must be <b>9–11 digits</b>. Please try again:",
            parse_mode=ParseMode.HTML,
        )
        return

    await state.update_data(gaming_id=gid)
    data = await state.get_data()

    user = message.from_user
    doc = {
        "user_id": user.id,
        "username": user.username,
        "full_name": data["full_name"],
        "phone": data["phone"],
        "lat": data["lat"],
        "lon": data["lon"],
        "currency": data["currency"],
        "photo1_file_id": data["photo1_file_id"],
        "photo2_file_id": data["photo2_file_id"],
        "has_experience": data["has_experience"],
        "street": data["street"],
        "topup_method": data["topup_method"],
        "gaming_id": gid,
        "status": "pending",
        "rejection_reason": None,
        "registered_at": datetime.now(timezone.utc),
    }
    await users_col.update_one(
        {"user_id": user.id}, {"$set": doc}, upsert=True
    )
    logger.info(f"New registration: user_id={user.id}, username={user.username}")

    await message.answer(
        "✅ <b>Registration complete!</b>\n\n"
        "Your application is <b>pending admin approval</b>.\n"
        "Use the button below to check your status at any time.",
        reply_markup=status_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await state.clear()

    # Notify admins
    admin_text = (
        f"🔔 <b>New agent registration!</b>\n\n"
        f"👤 Name: {data['full_name']}\n"
        f"🆔 User ID: <code>{user.id}</code>\n"
        f"📛 Username: @{user.username or 'N/A'}\n"
        f"📞 Phone: {data['phone']}\n"
        f"💱 Currency: {data['currency']}\n"
        f"🗺 Location: {data['lat']:.4f}, {data['lon']:.4f}\n"
        f"🏠 Street: {data['street']}\n"
        f"💳 Top-up: {data['topup_method']}\n"
        f"🎮 Gaming ID: {gid}\n"
        f"📱 Experience: {'Yes' if data['has_experience'] else 'No'}\n\n"
        f"Use /approve {user.id} or /reject {user.id}"
    )
    await notify_admins(bot, admin_text, parse_mode=ParseMode.HTML)


@router.message(Reg.gaming_id)
async def gaming_id_not_text(message: Message):
    await message.answer("Please send your gaming ID as a number (9–11 digits).")


# ──────────────────────────────────────────────────────────────────────────────
# Request Status button
# ──────────────────────────────────────────────────────────────────────────────
@router.message(F.text == "📋 Request Status")
async def request_status(message: Message):
    doc = await users_col.find_one({"user_id": message.from_user.id})
    if not doc:
        await message.answer("You have not registered yet. Use /start to begin.")
        return
    status = doc.get("status", "pending")
    if status == "pending":
        await message.answer("⏳ Your registration is under review.")
    elif status == "approved":
        await message.answer("✅ Approved! You are now a registered Mobicash agent.")
    elif status == "rejected":
        reason = doc.get("rejection_reason") or "No reason provided."
        await message.answer(f"❌ Rejected. Reason: {reason}")
    else:
        await message.answer(f"Status: {status}")


# ──────────────────────────────────────────────────────────────────────────────
# Forward user replies to admins
# ──────────────────────────────────────────────────────────────────────────────
@router.message(F.reply_to_message)
async def forward_reply_to_admins(message: Message, bot: Bot):
    # Only forward if the original message is from the bot itself
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.id == bot.id:
            user = message.from_user
            header = (
                f"💬 <b>User reply</b>\n"
                f"👤 {user.full_name} | ID: <code>{user.id}</code> | "
                f"@{user.username or 'N/A'}"
            )
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, header, parse_mode=ParseMode.HTML)
                    await message.forward(admin_id)
                except Exception as e:
                    logger.warning(f"Could not forward to admin {admin_id}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Admin: /listpending
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Command("listpending"))
async def cmd_listpending(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    cursor = users_col.find({"status": "pending"})
    lines = []
    async for doc in cursor:
        uid = doc["user_id"]
        uname = doc.get("username") or "N/A"
        name = doc.get("full_name", "")
        lines.append(f"• <code>{uid}</code> | @{uname} | {name}")
    if not lines:
        await message.answer("No pending registrations.")
    else:
        await message.answer(
            "<b>Pending registrations:</b>\n\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Admin: /approve <user_id>
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Command("approve"))
async def cmd_approve(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /approve <user_id>")
        return
    target_id = int(parts[1])
    result = await users_col.update_one(
        {"user_id": target_id, "status": "pending"},
        {"$set": {"status": "approved", "approved_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        await message.answer("User not found or not in pending state.")
        return
    await message.answer(f"✅ User {target_id} approved.")
    try:
        await bot.send_message(
            target_id,
            "🎉 <b>Congratulations!</b> Your Mobicash agent application has been "
            "<b>approved</b>! You are now a registered Mobicash agent.",
            reply_markup=status_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Admin: /reject <user_id>
# ──────────────────────────────────────────────────────────────────────────────
@router.message(Command("reject"))
async def cmd_reject(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /reject <user_id>")
        return
    target_id = int(parts[1])
    doc = await users_col.find_one({"user_id": target_id})
    if not doc:
        await message.answer("User not found.")
        return
    await state.set_state(AdminReject.waiting_reason)
    await state.update_data(reject_target_id=target_id)
    await message.answer(
        f"Please send the rejection reason for user <code>{target_id}</code>.\n"
        "You can send text, a photo, or a video.",
        parse_mode=ParseMode.HTML,
    )


@router.message(AdminReject.waiting_reason)
async def got_rejection_reason(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data["reject_target_id"]

    # Determine reason text for DB
    if message.text:
        reason_text = message.text
    elif message.caption:
        reason_text = message.caption
    elif message.photo:
        reason_text = "[Photo rejection reason]"
    elif message.video:
        reason_text = "[Video rejection reason]"
    else:
        reason_text = "[Media rejection reason]"

    await users_col.update_one(
        {"user_id": target_id},
        {
            "$set": {
                "status": "rejected",
                "rejection_reason": reason_text,
                "rejected_at": datetime.now(timezone.utc),
            }
        },
    )
    await state.clear()
    await message.answer(f"✅ User {target_id} has been rejected.")

    # Forward rejection reason to the user
    try:
        await bot.send_message(
            target_id,
            "❌ <b>Your Mobicash agent application has been rejected.</b>\n\n"
            "Reason from admin:",
            reply_markup=status_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        await message.copy_to(target_id)
    except Exception as e:
        logger.warning(f"Could not send rejection to user {target_id}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
async def main():
    # Test MongoDB before doing anything
    try:
        await init_db()
    except (ServerSelectionTimeoutError, Exception) as e:
        logger.error(f"MongoDB connection failed: {e}")
        raise SystemExit(1)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Deleting webhook and clearing pending updates…")
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(1)

    logger.info("Starting polling…")
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        except Exception as e:
            logger.error(f"Polling crashed: {e}. Restarting in 5 s…")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
