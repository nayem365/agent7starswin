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

# ─── Country → Currency map ────────────────────────────────────────────────────
COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "IT": "EUR",
    "ES": "EUR", "PT": "EUR", "NL": "EUR", "BE": "EUR", "AT": "EUR",
    "GR": "EUR", "FI": "EUR", "IE": "EUR", "LU": "EUR", "SK": "EUR",
    "SI": "EUR", "EE": "EUR", "LV": "EUR", "LT": "EUR", "CY": "EUR",
    "MT": "EUR", "RU": "RUB", "UA": "UAH", "CN": "CNY", "JP": "JPY",
    "KR": "KRW", "IN": "INR", "BR": "BRL", "MX": "MXN", "CA": "CAD",
    "AU": "AUD", "NG": "NGN", "ZA": "ZAR", "KE": "KES", "GH": "GHS",
    "EG": "EGP", "MA": "MAD", "TZ": "TZS", "ET": "ETB", "CI": "XOF",
    "SN": "XOF", "CM": "XAF", "CD": "CDF", "UG": "UGX", "TH": "THB",
    "VN": "VND", "PH": "PHP", "ID": "IDR", "MY": "MYR", "PK": "PKR",
    "BD": "BDT", "TR": "TRY", "SA": "SAR", "AE": "AED", "QA": "QAR",
    "KW": "KWD", "OM": "OMR", "BH": "BHD", "IQ": "IQD", "IR": "IRR",
    "IL": "ILS", "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON",
    "SE": "SEK", "NO": "NOK", "DK": "DKK", "CH": "CHF", "SG": "SGD",
    "HK": "HKD", "NZ": "NZD", "AR": "ARS", "CL": "CLP", "CO": "COP",
    "PE": "PEN", "VE": "VES", "TN": "TND", "DZ": "DZD", "LY": "LYD",
    "SD": "SDG", "MZ": "MZN", "ZW": "ZWL", "ZM": "ZMW", "RW": "RWF",
    "MG": "MGA", "BJ": "XOF", "BF": "XOF", "ML": "XOF", "NE": "XOF",
    "TG": "XOF", "GA": "XAF", "CG": "XAF", "CF": "XAF", "TD": "XAF",
    "GQ": "XAF", "SO": "SOS", "ER": "ERN", "DJ": "DJF", "MU": "MUR",
    "SC": "SCR", "CV": "CVE", "ST": "STN", "KM": "KMF",
}


# ─── FSM States ───────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    agreement  = State()
    location   = State()
    phone      = State()
    name       = State()
    currency   = State()
    photo1     = State()
    photo2     = State()
    experience = State()
    street     = State()
    topup      = State()
    gaming_id  = State()


class AdminReject(StatesGroup):
    waiting_reason = State()


# ─── MongoDB ──────────────────────────────────────────────────────────────────
db_client: AsyncIOMotorClient = None
users_col = None


async def init_db():
    global db_client, users_col
    logger.info("Connecting to MongoDB…")
    db_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    await db_client.admin.command("ping")
    db = db_client.get_default_database("mobicash")
    users_col = db["users"]
    # Ensure index on user_id
    await users_col.create_index("user_id", unique=True)
    logger.info("MongoDB connected and indexes ensured.")


# ─── Helpers ──────────────────────────────────────────────────────────────────
async def notify_admins(bot: Bot, text: str, **kwargs):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


async def reverse_geocode(lat: float, lon: float) -> str:
    """Return ISO 4217 currency code for the given coordinates, fallback USD."""
    url = (
        f"https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json&accept-language=en"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": "MobicashBot/1.0 (contact@mobicash.io)"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    return "USD"
                data = await resp.json()
                cc = data.get("address", {}).get("country_code", "").upper()
                return COUNTRY_CURRENCY.get(cc, "USD")
    except Exception as e:
        logger.warning(f"Geocoding failed: {e}")
        return "USD"


def status_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📋 Request Status")]],
        resize_keyboard=True,
    )


def yes_no_keyboard(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Yes", callback_data=yes_cb),
            InlineKeyboardButton(text="❌ No",  callback_data=no_cb),
        ]]
    )


async def get_user_doc(user_id: int) -> dict | None:
    return await users_col.find_one({"user_id": user_id})


# ─── Router ───────────────────────────────────────────────────────────────────
router = Router()


# ══════════════════════════════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    # If already registered, show status instead of restarting
    doc = await get_user_doc(message.from_user.id)
    if doc and doc.get("status") in ("approved", "pending"):
        status = doc["status"]
        if status == "approved":
            await message.answer(
                "✅ You are already a registered Mobicash agent!\n"
                "Use the button below to check your status.",
                reply_markup=status_keyboard(),
            )
        else:
            await message.answer(
                "⏳ You already have a <b>pending</b> registration.\n"
                "Use the button below to check your status.\n\n"
                "To restart your registration, send /restart.",
                reply_markup=status_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        return

    agreement_text = (
        "👋 Welcome to <b>Mobicash Agent Registration</b>!\n\n"
        "<b>Terms &amp; Conditions:</b>\n"
        "• You will act as an authorised Mobicash agent.\n"
        "• All information provided must be accurate and truthful.\n"
        "• Fraudulent registrations will result in a permanent ban.\n"
        "• Your data is stored securely and used only for verification.\n\n"
        "Please read and accept the agreement to proceed."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ I Agree & Continue", callback_data="agree")]]
    )
    await message.answer(agreement_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.agreement)


# ══════════════════════════════════════════════════════════════════════════════
# /restart — allow rejected users to re-apply
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("restart"))
async def cmd_restart(message: Message, state: FSMContext):
    await state.clear()
    doc = await get_user_doc(message.from_user.id)
    if doc and doc.get("status") == "approved":
        await message.answer("✅ You are already an approved agent. No need to restart.")
        return
    # Delete existing record so they can re-register fresh
    if doc:
        await users_col.delete_one({"user_id": message.from_user.id})
    await cmd_start(message, state)


# ══════════════════════════════════════════════════════════════════════════════
# /cancel — cancel ongoing registration
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Nothing to cancel.")
        return
    await state.clear()
    await message.answer(
        "❌ Registration cancelled. Send /start to begin again.",
        reply_markup=ReplyKeyboardRemove(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Agreement callback
# ══════════════════════════════════════════════════════════════════════════════
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
        "📍 <b>Step 1 of 9</b> — Location\n\n"
        "Please share your current location so we can determine your region and currency.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.location)


# ══════════════════════════════════════════════════════════════════════════════
# Location
# ══════════════════════════════════════════════════════════════════════════════
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
        "✅ Location received!\n\n"
        "📞 <b>Step 2 of 9</b> — Phone Number\n\n"
        "Please share your phone number using the button below.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.phone)


@router.message(Reg.location)
async def location_wrong(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Share Location", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "⚠️ Please use the <b>Share Location</b> button below to share your location.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Phone
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.phone, F.contact)
async def got_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    # Ensure phone belongs to this user (not someone else's contact)
    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        await message.answer("⚠️ Please share <b>your own</b> phone number.", parse_mode=ParseMode.HTML)
        return
    await state.update_data(phone=phone)
    await message.answer(
        "✅ Phone number received!\n\n"
        "👤 <b>Step 3 of 9</b> — Full Name\n\n"
        "Please enter your <b>full name</b>.\n\n"
        "<i>Rules: 2–4 words, letters only (English / Cyrillic / French), "
        "hyphens, apostrophes and periods allowed, max 40 chars, not ALL CAPS.</i>\n\n"
        "Examples: <code>John Doe</code>, <code>Jean-Pierre Dupont</code>, "
        "<code>Иван Петров</code>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.name)


@router.message(Reg.phone)
async def phone_wrong(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Share Phone Number", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "⚠️ Please use the <b>Share Phone Number</b> button below.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Name
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.name, F.text)
async def got_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) > 40:
        await message.answer("❌ Name is too long (max 40 characters). Please try again:")
        return
    if name == name.upper() and any(c.isalpha() for c in name):
        await message.answer("❌ Please don't use ALL CAPS. Try again:")
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
            [InlineKeyboardButton(
                text=f"🌍 {local_currency} (local)",
                callback_data=f"currency_{local_currency}",
            )]
        )

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"✅ Name saved: <b>{name}</b>\n\n"
        "💱 <b>Step 4 of 9</b> — Currency\n\n"
        "Please select your preferred currency:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.currency)


@router.message(Reg.name)
async def name_not_text(message: Message):
    await message.answer("⚠️ Please send your full name as text.")


# ══════════════════════════════════════════════════════════════════════════════
# Currency
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("currency_"), Reg.currency)
async def cb_currency(call: CallbackQuery, state: FSMContext):
    currency = call.data.split("_", 1)[1]
    await state.update_data(currency=currency)
    await call.answer(f"✅ Selected: {currency}")
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✅ Currency set to <b>{currency}</b>.\n\n"
        "📄 <b>Step 5 of 9</b> — Identity Document (Photo 1)\n\n"
        "Please send the <b>front photo</b> of your identity document "
        "(Passport / National ID / Driving Licence).\n\n"
        "<i>Make sure the photo is clear and all text is readable.</i>",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.photo1)


# ══════════════════════════════════════════════════════════════════════════════
# Photo 1
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.photo1, F.photo)
async def got_photo1(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo1_file_id=file_id)
    await message.answer(
        "✅ Front photo received!\n\n"
        "📄 <b>Step 6 of 9</b> — Identity Document (Photo 2)\n\n"
        "Please send the <b>back photo</b> of your identity document "
        "(or the second page of your passport).\n\n"
        "<i>Make sure the photo is clear and all text is readable.</i>",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.photo2)


@router.message(Reg.photo1, F.document)
async def photo1_as_document(message: Message, state: FSMContext):
    """Accept documents sent as files (user may not compress)."""
    file_id = message.document.file_id
    await state.update_data(photo1_file_id=file_id)
    await message.answer(
        "✅ Front document received!\n\n"
        "📄 <b>Step 6 of 9</b> — Identity Document (Photo 2)\n\n"
        "Please send the <b>back photo</b> of your identity document.",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.photo2)


@router.message(Reg.photo1)
async def photo1_wrong(message: Message):
    await message.answer("⚠️ Please send a <b>photo</b> of the front of your identity document.", parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Photo 2
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.photo2, F.photo)
async def got_photo2(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo2_file_id=file_id)
    await _ask_experience(message, state)


@router.message(Reg.photo2, F.document)
async def photo2_as_document(message: Message, state: FSMContext):
    file_id = message.document.file_id
    await state.update_data(photo2_file_id=file_id)
    await _ask_experience(message, state)


@router.message(Reg.photo2)
async def photo2_wrong(message: Message):
    await message.answer("⚠️ Please send a <b>photo</b> of the back of your identity document.", parse_mode=ParseMode.HTML)


async def _ask_experience(message: Message, state: FSMContext):
    """Ask about MobCash app experience after both photos are received."""
    kb = yes_no_keyboard("exp_yes", "exp_no")
    await message.answer(
        "✅ Both identity photos received!\n\n"
        "📱 <b>Step 7 of 9</b> — App Experience\n\n"
        "Do you have prior experience working with the <b>MobCash mobile app</b>?",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.experience)


# ══════════════════════════════════════════════════════════════════════════════
# Experience
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.in_({"exp_yes", "exp_no"}), Reg.experience)
async def cb_experience(call: CallbackQuery, state: FSMContext):
    experience = call.data == "exp_yes"
    await state.update_data(has_experience=experience)
    await call.answer()
    await call.message.edit_reply_markup()
    label = "Yes ✅" if experience else "No ❌"
    await call.message.answer(
        f"✅ Experience: <b>{label}</b>\n\n"
        "🏠 <b>Step 8 of 9</b> — Street Name\n\n"
        "Please enter your <b>street name</b> (name only, not full address, min 2 chars):",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.street)


# ══════════════════════════════════════════════════════════════════════════════
# Street
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.street, F.text)
async def got_street(message: Message, state: FSMContext):
    street = message.text.strip()
    if len(street) < 2:
        await message.answer("❌ Street name must be at least 2 characters. Try again:")
        return
    if len(street) > 80:
        await message.answer("❌ Street name too long (max 80 characters). Try again:")
        return
    await state.update_data(street=street)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🪙 USDT",         callback_data="topup_USDT"),
                InlineKeyboardButton(text="₿ Bitcoin",       callback_data="topup_BTC"),
            ],
            [
                InlineKeyboardButton(text="Ξ Ethereum",      callback_data="topup_ETH"),
                InlineKeyboardButton(text="🔄 Other Crypto", callback_data="topup_OTHER"),
            ],
        ]
    )
    await message.answer(
        f"✅ Street saved: <b>{street}</b>\n\n"
        "💳 <b>Step 9 of 9</b> — Top-up Method\n\n"
        "Please select your preferred <b>top-up method</b>:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.topup)


@router.message(Reg.street)
async def street_not_text(message: Message):
    await message.answer("⚠️ Please send the street name as text.")


# ══════════════════════════════════════════════════════════════════════════════
# Top-up method
# ══════════════════════════════════════════════════════════════════════════════
TOPUP_LABELS = {
    "USDT":  "🪙 USDT",
    "BTC":   "₿ Bitcoin",
    "ETH":   "Ξ Ethereum",
    "OTHER": "🔄 Other Crypto",
}


@router.callback_query(F.data.startswith("topup_"), Reg.topup)
async def cb_topup(call: CallbackQuery, state: FSMContext):
    method = call.data.split("_", 1)[1]
    label = TOPUP_LABELS.get(method, method)
    await state.update_data(topup_method=method)
    await call.answer()
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✅ Top-up method: <b>{label}</b>\n\n"
        "🎮 Almost done! Please enter your <b>7starswin Gaming ID</b> "
        "(numeric only, 9–11 digits):",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.gaming_id)


# ══════════════════════════════════════════════════════════════════════════════
# Gaming ID  →  final submission
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.gaming_id, F.text)
async def got_gaming_id(message: Message, state: FSMContext, bot: Bot):
    gid = message.text.strip()
    if not GAMING_ID_RE.match(gid):
        await message.answer(
            "❌ Invalid gaming ID. It must be <b>9–11 digits</b> (numbers only). Please try again:",
            parse_mode=ParseMode.HTML,
        )
        return

    # Check if gaming ID already registered by another user
    existing = await users_col.find_one({"gaming_id": gid, "user_id": {"$ne": message.from_user.id}})
    if existing:
        await message.answer(
            "❌ This gaming ID is already registered. "
            "Please check and try again, or contact support."
        )
        return

    await state.update_data(gaming_id=gid)
    data = await state.get_data()
    user = message.from_user

    doc = {
        "user_id":        user.id,
        "username":       user.username,
        "first_name":     user.first_name,
        "last_name":      user.last_name,
        "full_name":      data["full_name"],
        "phone":          data["phone"],
        "lat":            data["lat"],
        "lon":            data["lon"],
        "currency":       data["currency"],
        "local_currency": data.get("local_currency", "USD"),
        "photo1_file_id": data["photo1_file_id"],
        "photo2_file_id": data["photo2_file_id"],
        "has_experience": data["has_experience"],
        "street":         data["street"],
        "topup_method":   data["topup_method"],
        "gaming_id":      gid,
        "status":         "pending",
        "rejection_reason": None,
        "registered_at":  datetime.now(timezone.utc),
        "updated_at":     datetime.now(timezone.utc),
    }

    await users_col.update_one(
        {"user_id": user.id},
        {"$set": doc},
        upsert=True,
    )
    logger.info(f"New registration: user_id={user.id}, username={user.username}, gaming_id={gid}")

    # Confirmation to user
    summary = (
        "🎉 <b>Registration Complete!</b>\n\n"
        f"👤 Name: <b>{data['full_name']}</b>\n"
        f"📞 Phone: <b>{data['phone']}</b>\n"
        f"💱 Currency: <b>{data['currency']}</b>\n"
        f"🎮 Gaming ID: <b>{gid}</b>\n"
        f"💳 Top-up: <b>{TOPUP_LABELS.get(data['topup_method'], data['topup_method'])}</b>\n\n"
        "Your application is <b>pending admin review</b>.\n"
        "You'll be notified once a decision is made.\n\n"
        "Use the button below to check your status at any time."
    )
    await message.answer(summary, reply_markup=status_keyboard(), parse_mode=ParseMode.HTML)
    await state.clear()

    # Notify admins with full details + approve/reject buttons
    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"admin_approve_{user.id}"),
                InlineKeyboardButton(text="❌ Reject",  callback_data=f"admin_reject_{user.id}"),
            ]
        ]
    )
    admin_text = (
        f"🔔 <b>New Agent Registration</b>\n\n"
        f"👤 Name: {data['full_name']}\n"
        f"🆔 User ID: <code>{user.id}</code>\n"
        f"📛 Username: @{user.username or 'N/A'}\n"
        f"📞 Phone: {data['phone']}\n"
        f"💱 Currency: {data['currency']}\n"
        f"🗺 Location: {data['lat']:.4f}, {data['lon']:.4f}\n"
        f"🏠 Street: {data['street']}\n"
        f"💳 Top-up: {TOPUP_LABELS.get(data['topup_method'], data['topup_method'])}\n"
        f"🎮 Gaming ID: {gid}\n"
        f"📱 Experience: {'Yes ✅' if data['has_experience'] else 'No ❌'}\n\n"
        f"<i>Commands: /approve {user.id} | /reject {user.id} | /viewdocs {user.id}</i>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, admin_text,
                reply_markup=admin_kb,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


@router.message(Reg.gaming_id)
async def gaming_id_not_text(message: Message):
    await message.answer("⚠️ Please send your gaming ID as a number (9–11 digits).")


# ══════════════════════════════════════════════════════════════════════════════
# Admin inline approve/reject callbacks
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("admin_approve_"))
async def cb_admin_approve(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    result = await users_col.update_one(
        {"user_id": target_id, "status": "pending"},
        {"$set": {"status": "approved", "approved_at": datetime.now(timezone.utc),
                  "approved_by": call.from_user.id}},
    )
    if result.matched_count == 0:
        await call.answer("User not found or already processed.", show_alert=True)
        return
    await call.answer("✅ Approved!")
    await call.message.edit_reply_markup()
    await call.message.reply(f"✅ User <code>{target_id}</code> has been <b>approved</b>.", parse_mode=ParseMode.HTML)
    try:
        await bot.send_message(
            target_id,
            "🎉 <b>Congratulations!</b> Your Mobicash agent application has been "
            "<b>approved</b>! You are now a registered Mobicash agent.\n\n"
            "Welcome to the team! 🚀",
            reply_markup=status_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


@router.callback_query(F.data.startswith("admin_reject_"))
async def cb_admin_reject(call: CallbackQuery, state: FSMContext, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    doc = await get_user_doc(target_id)
    if not doc:
        await call.answer("User not found.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_reply_markup()
    await state.set_state(AdminReject.waiting_reason)
    await state.update_data(reject_target_id=target_id)
    await call.message.reply(
        f"Please send the <b>rejection reason</b> for user <code>{target_id}</code>.\n"
        "You can send text, a photo, or a video.",
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Request Status button
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "📋 Request Status")
async def request_status(message: Message):
    doc = await get_user_doc(message.from_user.id)
    if not doc:
        await message.answer("You have not registered yet. Use /start to begin.")
        return
    status = doc.get("status", "pending")
    if status == "pending":
        reg_time = doc.get("registered_at")
        time_str = reg_time.strftime("%Y-%m-%d %H:%M UTC") if reg_time else "Unknown"
        await message.answer(
            f"⏳ <b>Status: Under Review</b>\n\n"
            f"Your registration was submitted on <b>{time_str}</b>.\n"
            f"We'll notify you once a decision is made.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "approved":
        approved_time = doc.get("approved_at")
        time_str = approved_time.strftime("%Y-%m-%d %H:%M UTC") if approved_time else "Unknown"
        await message.answer(
            f"✅ <b>Status: Approved</b>\n\n"
            f"You are a registered Mobicash agent since <b>{time_str}</b>.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "rejected":
        reason = doc.get("rejection_reason") or "No reason provided."
        await message.answer(
            f"❌ <b>Status: Rejected</b>\n\n"
            f"Reason: {reason}\n\n"
            f"You may re-apply by sending /restart.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(f"Status: <b>{status}</b>", parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /viewdocs <user_id>  — send ID photos to admin
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("viewdocs"))
async def cmd_viewdocs(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /viewdocs <user_id>")
        return
    target_id = int(parts[1])
    doc = await get_user_doc(target_id)
    if not doc:
        await message.answer("User not found.")
        return

    header = (
        f"📂 <b>Documents for user {target_id}</b>\n"
        f"👤 {doc.get('full_name', 'N/A')} | @{doc.get('username') or 'N/A'}"
    )
    await message.answer(header, parse_mode=ParseMode.HTML)

    p1 = doc.get("photo1_file_id")
    p2 = doc.get("photo2_file_id")

    if p1:
        try:
            await bot.send_photo(message.chat.id, p1, caption="📄 Document — Front")
        except Exception as e:
            await message.answer(f"Could not send photo 1: {e}")
    if p2:
        try:
            await bot.send_photo(message.chat.id, p2, caption="📄 Document — Back")
        except Exception as e:
            await message.answer(f"Could not send photo 2: {e}")

    if not p1 and not p2:
        await message.answer("No documents found for this user.")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /listpending
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("listpending"))
async def cmd_listpending(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    cursor = users_col.find({"status": "pending"}).sort("registered_at", 1)
    lines = []
    async for doc in cursor:
        uid   = doc["user_id"]
        uname = doc.get("username") or "N/A"
        name  = doc.get("full_name", "")
        reg   = doc.get("registered_at")
        date  = reg.strftime("%m-%d %H:%M") if reg else "?"
        lines.append(f"• <code>{uid}</code> | @{uname} | {name} | {date}")
    if not lines:
        await message.answer("No pending registrations. 🎉")
    else:
        await message.answer(
            f"<b>Pending registrations ({len(lines)}):</b>\n\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /listall [status]
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("listall"))
async def cmd_listall(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    query = {}
    if len(parts) == 2:
        query = {"status": parts[1].lower()}
    cursor = users_col.find(query).sort("registered_at", -1).limit(50)
    lines = []
    async for doc in cursor:
        uid    = doc["user_id"]
        uname  = doc.get("username") or "N/A"
        name   = doc.get("full_name", "")
        status = doc.get("status", "?")
        emoji  = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(status, "❓")
        lines.append(f"{emoji} <code>{uid}</code> | @{uname} | {name}")
    if not lines:
        await message.answer("No users found.")
    else:
        await message.answer(
            f"<b>Users (last 50):</b>\n\n" + "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /stats
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    total    = await users_col.count_documents({})
    pending  = await users_col.count_documents({"status": "pending"})
    approved = await users_col.count_documents({"status": "approved"})
    rejected = await users_col.count_documents({"status": "rejected"})
    await message.answer(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total registrations: <b>{total}</b>\n"
        f"⏳ Pending: <b>{pending}</b>\n"
        f"✅ Approved: <b>{approved}</b>\n"
        f"❌ Rejected: <b>{rejected}</b>",
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /approve <user_id>
# ══════════════════════════════════════════════════════════════════════════════
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
        {"$set": {
            "status":      "approved",
            "approved_at": datetime.now(timezone.utc),
            "approved_by": message.from_user.id,
        }},
    )
    if result.matched_count == 0:
        await message.answer("User not found or not in pending state.")
        return
    await message.answer(f"✅ User <code>{target_id}</code> approved.", parse_mode=ParseMode.HTML)
    try:
        await bot.send_message(
            target_id,
            "🎉 <b>Congratulations!</b> Your Mobicash agent application has been "
            "<b>approved</b>! Welcome to the team! 🚀",
            reply_markup=status_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /reject <user_id>
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("reject"))
async def cmd_reject(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /reject <user_id>")
        return
    target_id = int(parts[1])
    doc = await get_user_doc(target_id)
    if not doc:
        await message.answer("User not found.")
        return
    if doc.get("status") != "pending":
        await message.answer(f"User status is already: <b>{doc['status']}</b>", parse_mode=ParseMode.HTML)
        return
    await state.set_state(AdminReject.waiting_reason)
    await state.update_data(reject_target_id=target_id)
    await message.answer(
        f"Please send the <b>rejection reason</b> for user <code>{target_id}</code>.\n"
        "You can send text, a photo, or a video.",
        parse_mode=ParseMode.HTML,
    )


@router.message(AdminReject.waiting_reason)
async def got_rejection_reason(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data["reject_target_id"]

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
        {"$set": {
            "status":           "rejected",
            "rejection_reason": reason_text,
            "rejected_at":      datetime.now(timezone.utc),
            "rejected_by":      message.from_user.id,
        }},
    )
    await state.clear()
    await message.answer(f"✅ User <code>{target_id}</code> has been rejected.", parse_mode=ParseMode.HTML)

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


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /broadcast  — send a message to all approved agents
# ══════════════════════════════════════════════════════════════════════════════
class AdminBroadcast(StatesGroup):
    waiting_message = State()


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminBroadcast.waiting_message)
    count = await users_col.count_documents({"status": "approved"})
    await message.answer(
        f"📢 Send the message to broadcast to all <b>{count} approved agents</b>.\n"
        "You can send text, photo, or video. Send /cancel_broadcast to abort.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("cancel_broadcast"), AdminBroadcast.waiting_message)
async def cmd_cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Broadcast cancelled.")


@router.message(AdminBroadcast.waiting_message)
async def got_broadcast_message(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    cursor = users_col.find({"status": "approved"}, {"user_id": 1})
    sent = 0
    failed = 0
    async for doc in cursor:
        try:
            await message.copy_to(doc["user_id"])
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"📢 Broadcast complete.\n✅ Sent: {sent} | ❌ Failed: {failed}",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Forward user replies to admins
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.reply_to_message)
async def forward_reply_to_admins(message: Message, bot: Bot):
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot.id
    ):
        user = message.from_user
        header = (
            f"💬 <b>User message</b>\n"
            f"👤 {user.full_name} | ID: <code>{user.id}</code> | "
            f"@{user.username or 'N/A'}"
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, header, parse_mode=ParseMode.HTML)
                await message.forward(admin_id)
            except Exception as e:
                logger.warning(f"Could not forward to admin {admin_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# /help
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("help"))
async def cmd_help(message: Message):
    is_admin = message.from_user.id in ADMIN_IDS
    user_help = (
        "<b>Available commands:</b>\n\n"
        "/start — Start or restart registration\n"
        "/restart — Re-apply (if rejected)\n"
        "/cancel — Cancel ongoing registration\n"
        "/help — Show this help message\n\n"
        "📋 Use the <b>Request Status</b> button to check your application."
    )
    admin_help = (
        "\n\n<b>Admin commands:</b>\n\n"
        "/listpending — List all pending applications\n"
        "/listall [status] — List all users (optional filter: pending/approved/rejected)\n"
        "/stats — Show registration statistics\n"
        "/approve &lt;user_id&gt; — Approve an application\n"
        "/reject &lt;user_id&gt; — Reject an application (prompts for reason)\n"
        "/viewdocs &lt;user_id&gt; — View user identity documents\n"
        "/broadcast — Send message to all approved agents\n"
    )
    text = user_help + (admin_help if is_admin else "")
    await message.answer(text, parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Fallback for unrecognised messages during registration
# ══════════════════════════════════════════════════════════════════════════════
@router.message()
async def fallback(message: Message, state: FSMContext):
    current = await state.get_state()
    if current:
        await message.answer(
            "⚠️ Unexpected input. Please follow the instructions above.\n"
            "Send /cancel to abort the current registration."
        )
    else:
        await message.answer(
            "👋 Send /start to begin registration or /help for available commands."
        )


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
async def main():
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
