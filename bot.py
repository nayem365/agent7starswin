import asyncio
import logging
import os
import re
import signal
from datetime import datetime, timezone

import asyncpg
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
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Env vars ──────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]

# ─── Validation patterns ───────────────────────────────────────────────────────
NAME_RE      = re.compile(r"^[A-Za-zÀ-ÿА-Яа-я'\-\.]+(?:\s+[A-Za-zÀ-ÿА-Яа-я'\-\.]+){1,3}$")
GAMING_ID_RE = re.compile(r"^\d{9,11}$")

# ─── Country → Currency map ────────────────────────────────────────────────────
COUNTRY_CURRENCY: dict[str, str] = {
    "US":"USD","GB":"GBP","DE":"EUR","FR":"EUR","IT":"EUR","ES":"EUR","PT":"EUR",
    "NL":"EUR","BE":"EUR","AT":"EUR","GR":"EUR","FI":"EUR","IE":"EUR","LU":"EUR",
    "SK":"EUR","SI":"EUR","EE":"EUR","LV":"EUR","LT":"EUR","CY":"EUR","MT":"EUR",
    "RU":"RUB","UA":"UAH","CN":"CNY","JP":"JPY","KR":"KRW","IN":"INR","BR":"BRL",
    "MX":"MXN","CA":"CAD","AU":"AUD","NG":"NGN","ZA":"ZAR","KE":"KES","GH":"GHS",
    "EG":"EGP","MA":"MAD","TZ":"TZS","ET":"ETB","CI":"XOF","SN":"XOF","CM":"XAF",
    "CD":"CDF","UG":"UGX","TH":"THB","VN":"VND","PH":"PHP","ID":"IDR","MY":"MYR",
    "PK":"PKR","BD":"BDT","TR":"TRY","SA":"SAR","AE":"AED","QA":"QAR","KW":"KWD",
    "OM":"OMR","BH":"BHD","IQ":"IQD","IR":"IRR","IL":"ILS","PL":"PLN","CZ":"CZK",
    "HU":"HUF","RO":"RON","SE":"SEK","NO":"NOK","DK":"DKK","CH":"CHF","SG":"SGD",
    "HK":"HKD","NZ":"NZD","AR":"ARS","CL":"CLP","CO":"COP","PE":"PEN","VE":"VES",
    "TN":"TND","DZ":"DZD","LY":"LYD","SD":"SDG","MZ":"MZN","ZW":"ZWL","ZM":"ZMW",
    "RW":"RWF","MG":"MGA","BJ":"XOF","BF":"XOF","ML":"XOF","NE":"XOF","TG":"XOF",
    "GA":"XAF","CG":"XAF","CF":"XAF","TD":"XAF","GQ":"XAF","SO":"SOS","ER":"ERN",
    "DJ":"DJF","MU":"MUR","SC":"SCR","CV":"CVE","ST":"STN","KM":"KMF",
}

# ─── Country code → currency (for geo lookup) ─────────────────────────────────
COUNTRY_CODE_CURRENCY: dict[str, str] = COUNTRY_CURRENCY

# ─── Supported registration countries ─────────────────────────────────────────
SUPPORTED_COUNTRIES = {
    "BD": "🇧🇩 Bangladesh",
    "IN": "🇮🇳 India",
    "PK": "🇵🇰 Pakistan",
    "TR": "🇹🇷 Turkey",
    "TH": "🇹🇭 Thailand",
    "PH": "🇵🇭 Philippines",
}

# Country → commission info
COUNTRY_COMMISSION = {
    "BD": {
        "prepay": 100,
        "deposit_pct": 5,
        "wd_pct": 2,
        "currency_display": "BDT",
    },
    "IN": {
        "prepay": 100,
        "deposit_pct": 5,
        "wd_pct": 2,
        "currency_display": "INR",
    },
    "PK": {
        "prepay": 50,
        "deposit_pct": 6,
        "wd_pct": 2,
        "currency_display": "PKR",
    },
    "TR": {
        "prepay": 50,
        "deposit_pct": 7,
        "wd_pct": 2,
        "currency_display": "TRY",
    },
    "TH": {
        "prepay": 50,
        "deposit_pct": 3,
        "wd_pct": 1,
        "currency_display": "THB",
    },
    "PH": {
        "prepay": 50,
        "deposit_pct": 3,
        "wd_pct": 1,
        "currency_display": "PHP",
    },
}

def get_commission_text(country_code: str) -> str:
    info = COUNTRY_COMMISSION.get(country_code)
    if not info:
        return (
            "💵 <b>FOR Mobicash Agent — $50 Prepayment only</b>\n"
            "📊 <b>7% Deposit Commission | 2% Withdrawal Commission</b>"
        )
    return (
        f"💵 <b>FOR Mobicash Agent — ${info['prepay']} Prepayment only</b>\n"
        f"📊 <b>{info['deposit_pct']}% Deposit Commission | {info['wd_pct']}% Withdrawal Commission</b>"
    )

def get_currency_for_country(country_code: str) -> str:
    info = COUNTRY_COMMISSION.get(country_code)
    if info:
        return info["currency_display"]
    return COUNTRY_CURRENCY.get(country_code, "USD")

# ─── Supported languages ──────────────────────────────────────────────────────
LANGS = {
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
    "bn": "🇧🇩 বাংলা",
    "hi": "🇮🇳 हिन्दी",
    "ur": "🇵🇰 اردو",
    "ar": "🇸🇦 عربي",
}

# ─── Translation strings ───────────────────────────────────────────────────────
T: dict[str, dict[str, str]] = {
    "agree_btn": {
        "en": "✅ I Agree & Continue",
        "ru": "✅ Согласен и продолжить",
        "bn": "✅ আমি সম্মত ও চালিয়ে যান",
        "hi": "✅ मैं सहमत हूँ और जारी रखें",
        "ur": "✅ میں متفق ہوں اور جاری رکھیں",
        "ar": "✅ أوافق وأكمل",
    },
    "step_phone": {
        "en": "📞 <b>Step 2 of 9</b> — Phone Number\n\nPlease share your phone number using the button below.",
        "ru": "📞 <b>Шаг 2 из 9</b> — Номер телефона\n\nПоделитесь своим номером телефона.",
        "bn": "📞 <b>ধাপ ২ এর ৯</b> — ফোন নম্বর\n\nনিচের বোতাম দিয়ে আপনার ফোন নম্বর শেয়ার করুন।",
        "hi": "📞 <b>चरण 2 का 9</b> — फ़ोन नंबर\n\nकृपया नीचे बटन का उपयोग करके अपना फ़ोन नंबर साझा करें।",
        "ur": "📞 <b>مرحلہ 2 از 9</b> — فون نمبر\n\nنیچے بٹن سے اپنا فون نمبر شیئر کریں۔",
        "ar": "📞 <b>الخطوة 2 من 9</b> — رقم الهاتف\n\nيرجى مشاركة رقم هاتفك.",
    },
    "share_phone_btn": {
        "en": "📞 Share Phone Number",
        "ru": "📞 Поделиться номером телефона",
        "bn": "📞 ফোন নম্বর শেয়ার করুন",
        "hi": "📞 फ़ोन नंबर साझा करें",
        "ur": "📞 فون نمبر شیئر کریں",
        "ar": "📞 مشاركة رقم الهاتف",
    },
    "step_name": {
        "en": (
            "✅ Phone number received!\n\n"
            "👤 <b>Step 3 of 9</b> — Full Name\n\n"
            "Please enter your <b>full name in English letters</b>.\n\n"
            "<i>Rules: 2–4 words, English letters only, hyphens, apostrophes and periods allowed, max 40 chars, not ALL CAPS.</i>\n\n"
            "Example: <code>John Doe</code>, <code>Jean-Pierre Dupont</code>"
        ),
        "ru": (
            "✅ Номер телефона получен!\n\n"
            "👤 <b>Шаг 3 из 9</b> — Полное имя\n\n"
            "Введите ваше <b>полное имя на английском</b> (2–4 слова, не CAPS)."
        ),
        "bn": (
            "✅ ফোন নম্বর পাওয়া গেছে!\n\n"
            "👤 <b>ধাপ ৩ এর ৯</b> — পুরো নাম\n\n"
            "আপনার <b>পূর্ণ নাম ইংরেজি অক্ষরে</b> লিখুন (২–৪ শব্দ, সর্বোচ্চ ৪০ অক্ষর)।"
        ),
        "hi": (
            "✅ फ़ोन नंबर प्राप्त हुआ!\n\n"
            "👤 <b>चरण 3 का 9</b> — पूरा नाम\n\n"
            "कृपया अपना <b>पूरा नाम अंग्रेज़ी अक्षरों में</b> दर्ज करें (2–4 शब्द, अधिकतम 40 अक्षर)।"
        ),
        "ur": (
            "✅ فون نمبر موصول ہوا!\n\n"
            "👤 <b>مرحلہ 3 از 9</b> — پورا نام\n\n"
            "براہ کرم اپنا <b>پورا نام انگریزی حروف میں</b> درج کریں (2–4 الفاظ، زیادہ سے زیادہ 40 حروف)۔"
        ),
        "ar": (
            "✅ تم استلام رقم الهاتف!\n\n"
            "👤 <b>الخطوة 3 من 9</b> — الاسم الكامل\n\n"
            "يرجى إدخال <b>اسمك الكامل بالأحرف الإنجليزية</b> (2–4 كلمات، 40 حرفاً كحد أقصى)."
        ),
    },
    "step_currency": {
        "en": "💱 <b>Step 4 of 9</b> — Currency\n\nPlease select your preferred currency:",
        "ru": "💱 <b>Шаг 4 из 9</b> — Валюта\n\nВыберите предпочтительную валюту:",
        "bn": "💱 <b>ধাপ ৪ এর ৯</b> — মুদ্রা\n\nআপনার পছন্দের মুদ্রা নির্বাচন করুন:",
        "hi": "💱 <b>चरण 4 का 9</b> — मुद्रा\n\nकृपया अपनी पसंदीदा मुद्रा चुनें:",
        "ur": "💱 <b>مرحلہ 4 از 9</b> — کرنسی\n\nاپنی پسندیدہ کرنسی منتخب کریں:",
        "ar": "💱 <b>الخطوة 4 من 9</b> — العملة\n\nيرجى اختيار عملتك المفضلة:",
    },
    "step_photo1": {
        "en": (
            "📄 <b>Step 5 of 9</b> — Identity Document\n\n"
            "Please <b>Send First Photo</b> of your identity document "
            "(Passport / National ID / Driving Licence).\n\n"
            "<i>Make sure the photo is clear and all text is readable.</i>"
        ),
        "ru": (
            "📄 <b>Шаг 5 из 9</b> — Документ удостоверяющий личность\n\n"
            "Пожалуйста, <b>отправьте первое фото</b> вашего документа.\n\n"
            "<i>Убедитесь, что фото чёткое и весь текст читаем.</i>"
        ),
        "bn": (
            "📄 <b>ধাপ ৫ এর ৯</b> — পরিচয় নথি\n\n"
            "আপনার পরিচয় নথির <b>প্রথম ছবি পাঠান</b>।\n\n"
            "<i>ছবিটি স্পষ্ট হতে হবে।</i>"
        ),
        "hi": (
            "📄 <b>चरण 5 का 9</b> — पहचान दस्तावेज़\n\n"
            "कृपया अपने पहचान दस्तावेज़ की <b>पहली फ़ोटो भेजें</b>।\n\n"
            "<i>सुनिश्चित करें कि फ़ोटो स्पष्ट हो।</i>"
        ),
        "ur": (
            "📄 <b>مرحلہ 5 از 9</b> — شناختی دستاویز\n\n"
            "براہ کرم اپنی شناختی دستاویز کی <b>پہلی تصویر بھیجیں</b>۔\n\n"
            "<i>یقینی بنائیں کہ تصویر واضح ہو۔</i>"
        ),
        "ar": (
            "📄 <b>الخطوة 5 من 9</b> — وثيقة الهوية\n\n"
            "يرجى <b>إرسال الصورة الأولى</b> لوثيقة هويتك.\n\n"
            "<i>تأكد من وضوح الصورة.</i>"
        ),
    },
    "step_photo2": {
        "en": (
            "✅ First photo received!\n\n"
            "📄 <b>Step 6 of 9</b> — Identity Document (Second Photo)\n\n"
            "Please <b>Send the Back Side / Second Page</b> of your identity document.\n\n"
            "<i>Make sure the photo is clear and all text is readable.</i>"
        ),
        "ru": (
            "✅ Первое фото получено!\n\n"
            "📄 <b>Шаг 6 из 9</b> — Документ (Второе фото)\n\n"
            "Пожалуйста, <b>отправьте обратную сторону / вторую страницу</b>."
        ),
        "bn": (
            "✅ প্রথম ছবি পাওয়া গেছে!\n\n"
            "📄 <b>ধাপ ৬ এর ৯</b> — পরিচয় নথি (দ্বিতীয় ছবি)\n\n"
            "অনুগ্রহ করে <b>পেছনের দিক / দ্বিতীয় পাতার ছবি পাঠান</b>।"
        ),
        "hi": (
            "✅ पहली फ़ोटो प्राप्त हुई!\n\n"
            "📄 <b>चरण 6 का 9</b> — पहचान दस्तावेज़ (दूसरी फ़ोटो)\n\n"
            "कृपया <b>पिछली तरफ / दूसरे पृष्ठ की फ़ोटो भेजें</b>।"
        ),
        "ur": (
            "✅ پہلی تصویر موصول ہوئی!\n\n"
            "📄 <b>مرحلہ 6 از 9</b> — شناختی دستاویز (دوسری تصویر)\n\n"
            "براہ کرم <b>پچھلی طرف / دوسرے صفحے کی تصویر بھیجیں</b>۔"
        ),
        "ar": (
            "✅ تم استلام الصورة الأولى!\n\n"
            "📄 <b>الخطوة 6 من 9</b> — وثيقة الهوية (الصورة الثانية)\n\n"
            "يرجى <b>إرسال الجهة الخلفية / الصفحة الثانية</b>."
        ),
    },
    "step_experience": {
        "en": (
            "✅ Both identity photos received!\n\n"
            "📱 <b>Step 7 of 9</b> — Mobicash Agent Experience\n\n"
            "Do you have prior experience working with the <b>MobCash agent or any other company</b>?"
        ),
        "ru": (
            "✅ Оба фото документов получены!\n\n"
            "📱 <b>Шаг 7 из 9</b> — Опыт работы агентом\n\n"
            "Есть ли у вас опыт работы <b>агентом MobCash или в другой компании</b>?"
        ),
        "bn": (
            "✅ উভয় পরিচয় ছবি পাওয়া গেছে!\n\n"
            "📱 <b>ধাপ ৭ এর ৯</b> — Mobicash এজেন্ট অভিজ্ঞতা\n\n"
            "আপনার কি <b>MobCash এজেন্ট বা অন্য কোনো কোম্পানিতে</b> কাজ করার অভিজ্ঞতা আছে?"
        ),
        "hi": (
            "✅ दोनों पहचान फ़ोटो प्राप्त हुईं!\n\n"
            "📱 <b>चरण 7 का 9</b> — Mobicash एजेंट अनुभव\n\n"
            "क्या आपको <b>MobCash एजेंट या किसी अन्य कंपनी</b> के साथ काम करने का अनुभव है?"
        ),
        "ur": (
            "✅ دونوں شناختی تصاویر موصول ہوئیں!\n\n"
            "📱 <b>مرحلہ 7 از 9</b> — Mobicash ایجنٹ کا تجربہ\n\n"
            "کیا آپ کو <b>MobCash ایجنٹ یا کسی اور کمپنی</b> کے ساتھ کام کرنے کا تجربہ ہے؟"
        ),
        "ar": (
            "✅ تم استلام صورتي الهوية!\n\n"
            "📱 <b>الخطوة 7 من 9</b> — تجربة وكيل Mobicash\n\n"
            "هل لديك خبرة سابقة في العمل مع <b>وكيل MobCash أو أي شركة أخرى</b>؟"
        ),
    },
    "yes_btn": {
        "en": "✅ Yes", "ru": "✅ Да", "bn": "✅ হ্যাঁ",
        "hi": "✅ हाँ", "ur": "✅ ہاں", "ar": "✅ نعم",
    },
    "no_btn": {
        "en": "❌ No", "ru": "❌ Нет", "bn": "❌ না",
        "hi": "❌ नहीं", "ur": "❌ نہیں", "ar": "❌ لا",
    },
    "step_withdraw_address": {
        "en": (
            "🏠 <b>Step 8 of 9</b> — Withdrawal Address\n\n"
            "Please enter your <b>withdrawal address</b> (minimum 8 characters).\n"
            "This will be used as your payout address.\n\n"
            "Then attach a <b>photo of your area/location</b> together with the text."
        ),
        "ru": (
            "🏠 <b>Шаг 8 из 9</b> — Адрес для вывода\n\n"
            "Введите ваш <b>адрес для вывода</b> (минимум 8 символов).\n"
            "Это будет ваш платёжный адрес.\n\n"
            "Также прикрепите <b>фото вашего района/местонахождения</b>."
        ),
        "bn": (
            "🏠 <b>ধাপ ৮ এর ৯</b> — উত্তোলন ঠিকানা\n\n"
            "আপনার <b>উত্তোলন ঠিকানা</b> লিখুন (ন্যূনতম ৮ অক্ষর)।\n"
            "এটি আপনার পেআউট ঠিকানা হিসেবে ব্যবহার করা হবে।\n\n"
            "সাথে <b>আপনার এলাকার একটি ছবি</b> যুক্ত করুন।"
        ),
        "hi": (
            "🏠 <b>चरण 8 का 9</b> — निकासी पता\n\n"
            "कृपया अपना <b>निकासी पता</b> दर्ज करें (न्यूनतम 8 अक्षर)।\n"
            "यह आपके भुगतान पते के रूप में उपयोग किया जाएगा।\n\n"
            "साथ में <b>अपने क्षेत्र की एक फ़ोटो</b> भी संलग्न करें।"
        ),
        "ur": (
            "🏠 <b>مرحلہ 8 از 9</b> — ادائیگی کا پتہ\n\n"
            "براہ کرم اپنا <b>ادائیگی کا پتہ</b> درج کریں (کم از کم 8 حروف)۔\n"
            "یہ آپ کے ادائیگی پتے کے طور پر استعمال ہوگا۔\n\n"
            "ساتھ میں <b>اپنے علاقے کی ایک تصویر</b> بھی منسلک کریں۔"
        ),
        "ar": (
            "🏠 <b>الخطوة 8 من 9</b> — عنوان السحب\n\n"
            "يرجى إدخال <b>عنوان السحب</b> الخاص بك (8 أحرف كحد أدنى).\n"
            "سيُستخدم هذا كعنوان الدفع الخاص بك.\n\n"
            "أرفق أيضاً <b>صورة لمنطقتك/موقعك</b>."
        ),
    },
    "step_topup": {
        "en": (
            "✅ Withdrawal address and photo received!\n\n"
            "💳 <b>Step 9 of 9</b> — Top-up Method\n\n"
            "Please select your preferred <b>top-up method</b>:"
        ),
        "ru": (
            "✅ Адрес и фото получены!\n\n"
            "💳 <b>Шаг 9 из 9</b> — Способ пополнения\n\n"
            "Выберите предпочтительный <b>способ пополнения</b>:"
        ),
        "bn": (
            "✅ উত্তোলন ঠিকানা ও ছবি পাওয়া গেছে!\n\n"
            "💳 <b>ধাপ ৯ এর ৯</b> — টপ-আপ পদ্ধতি\n\n"
            "আপনার পছন্দের <b>টপ-আপ পদ্ধতি</b> নির্বাচন করুন:"
        ),
        "hi": (
            "✅ निकासी पता और फ़ोटो प्राप्त हुए!\n\n"
            "💳 <b>चरण 9 का 9</b> — टॉप-अप विधि\n\n"
            "कृपया अपनी पसंदीदा <b>टॉप-अप विधि</b> चुनें:"
        ),
        "ur": (
            "✅ ادائیگی کا پتہ اور تصویر موصول ہوئی!\n\n"
            "💳 <b>مرحلہ 9 از 9</b> — ٹاپ-اپ طریقہ\n\n"
            "اپنا پسندیدہ <b>ٹاپ-اپ طریقہ</b> منتخب کریں:"
        ),
        "ar": (
            "✅ تم استلام عنوان السحب والصورة!\n\n"
            "💳 <b>الخطوة 9 من 9</b> — طريقة الشحن\n\n"
            "يرجى اختيار <b>طريقة الشحن</b> المفضلة لديك:"
        ),
    },
    "preview_title": {
        "en": "📋 <b>Registration Preview</b>\n\n⚠️ <i>If all data is accurate and valid, move forward.</i>\n\nPlease review your information before submitting:",
        "ru": "📋 <b>Предварительный просмотр</b>\n\n⚠️ <i>Если все данные верны, продолжайте.</i>\n\nПроверьте информацию перед отправкой:",
        "bn": "📋 <b>নিবন্ধন পূর্বরূপ</b>\n\n⚠️ <i>সকল তথ্য সঠিক হলে এগিয়ে যান।</i>\n\nজমা দেওয়ার আগে পর্যালোচনা করুন:",
        "hi": "📋 <b>पंजीकरण पूर्वावलोकन</b>\n\n⚠️ <i>यदि सभी डेटा सटीक और वैध है, तो आगे बढ़ें।</i>\n\nजमा करने से पहले समीक्षा करें:",
        "ur": "📋 <b>رجسٹریشن پیش نظارہ</b>\n\n⚠️ <i>اگر تمام ڈیٹا درست ہے تو آگے بڑھیں۔</i>\n\nجمع کرانے سے پہلے جائزہ لیں:",
        "ar": "📋 <b>معاينة التسجيل</b>\n\n⚠️ <i>إذا كانت جميع البيانات دقيقة وصحيحة، تقدم.</i>\n\nيرجى المراجعة قبل الإرسال:",
    },
    "send_btn": {
        "en": "✅ Send Application",
        "ru": "✅ Отправить заявку",
        "bn": "✅ আবেদন পাঠান",
        "hi": "✅ आवेदन भेजें",
        "ur": "✅ درخواست بھیجیں",
        "ar": "✅ إرسال الطلب",
    },
    "restart_btn": {
        "en": "🔄 Restart",
        "ru": "🔄 Начать заново",
        "bn": "🔄 পুনরায় শুরু",
        "hi": "🔄 पुनः प्रारंभ",
        "ur": "🔄 دوبارہ شروع",
        "ar": "🔄 إعادة البدء",
    },
    "status_btn": {
        "en": "📋 Request Status",
        "ru": "📋 Статус заявки",
        "bn": "📋 অনুরোধের অবস্থা",
        "hi": "📋 अनुरोध की स्थिति",
        "ur": "📋 درخواست کی حیثیت",
        "ar": "📋 حالة الطلب",
    },
    "submission_ok": {
        "en": (
            "🎉 <b>Application Submitted!</b>\n\n"
            "Your application is <b>pending admin review</b>.\n"
            "You'll be notified once a decision is made.\n\n"
            "Use the button below to check your status at any time."
        ),
        "ru": (
            "🎉 <b>Заявка отправлена!</b>\n\n"
            "Ваша заявка находится на <b>рассмотрении</b>.\n"
            "Вы получите уведомление после принятия решения."
        ),
        "bn": (
            "🎉 <b>আবেদন জমা হয়েছে!</b>\n\n"
            "আপনার আবেদন <b>পর্যালোচনার অপেক্ষায়</b>।\n"
            "সিদ্ধান্ত হলে আপনাকে জানানো হবে।"
        ),
        "hi": (
            "🎉 <b>आवेदन जमा हुआ!</b>\n\n"
            "आपका आवेदन <b>समीक्षा के लिए लंबित</b> है।\n"
            "निर्णय होने पर आपको सूचित किया जाएगा।"
        ),
        "ur": (
            "🎉 <b>درخواست جمع ہو گئی!</b>\n\n"
            "آپ کی درخواست <b>جائزے کے لیے زیر التوا</b> ہے۔\n"
            "فیصلہ ہونے پر آپ کو مطلع کیا جائے گا۔"
        ),
        "ar": (
            "🎉 <b>تم تقديم الطلب!</b>\n\n"
            "طلبك <b>قيد مراجعة المسؤول</b>.\n"
            "سيتم إخطارك بمجرد اتخاذ القرار."
        ),
    },
    "under_review_msg": {
        "en": (
            "🔍 <b>Application Under Review</b>\n\n"
            "We have received your application and are currently reviewing it.\n"
            "We will inform you within <b>48 hours</b>.\n\n"
            "Thank you for your patience! ⏳"
        ),
        "ru": (
            "🔍 <b>Заявка на рассмотрении</b>\n\n"
            "Мы получили вашу заявку и рассматриваем её.\n"
            "Мы сообщим вам в течение <b>48 часов</b>.\n\n"
            "Спасибо за терпение! ⏳"
        ),
        "bn": (
            "🔍 <b>আবেদন পর্যালোচনাধীন</b>\n\n"
            "আমরা আপনার আবেদন পেয়েছি এবং পর্যালোচনা করছি।\n"
            "<b>৪৮ ঘণ্টার</b> মধ্যে আপনাকে জানানো হবে।\n\n"
            "ধৈর্যের জন্য ধন্যবাদ! ⏳"
        ),
        "hi": (
            "🔍 <b>आवेदन समीक्षाधीन</b>\n\n"
            "हमें आपका आवेदन मिल गया है और हम इसकी समीक्षा कर रहे हैं।\n"
            "<b>48 घंटों</b> के भीतर आपको सूचित किया जाएगा।\n\n"
            "आपके धैर्य के लिए धन्यवाद! ⏳"
        ),
        "ur": (
            "🔍 <b>درخواست زیر جائزہ</b>\n\n"
            "ہمیں آپ کی درخواست مل گئی ہے اور ہم اس کا جائزہ لے رہے ہیں۔\n"
            "<b>48 گھنٹوں</b> کے اندر آپ کو مطلع کیا جائے گا۔\n\n"
            "آپ کے صبر کا شکریہ! ⏳"
        ),
        "ar": (
            "🔍 <b>الطلب قيد المراجعة</b>\n\n"
            "لقد استلمنا طلبك ونقوم بمراجعته حالياً.\n"
            "سنخبرك خلال <b>48 ساعة</b>.\n\n"
            "شكراً لصبرك! ⏳"
        ),
    },
    "deposit_request_msg": {
        "en": (
            "💰 <b>Deposit Required!</b>\n\n"
            "Kindly make a deposit in your player account and let us know after completing the prepayment.\n\n"
            "Once your deposit is confirmed, your agent account will be activated! 🚀"
        ),
        "ru": (
            "💰 <b>Требуется депозит!</b>\n\n"
            "Пожалуйста, пополните счёт игрока и сообщите нам после оплаты.\n\n"
            "После подтверждения депозита ваш агентский аккаунт будет активирован! 🚀"
        ),
        "bn": (
            "💰 <b>ডিপোজিট প্রয়োজন!</b>\n\n"
            "আপনার প্লেয়ার অ্যাকাউন্টে ডিপোজিট করুন এবং পেমেন্ট করার পরে আমাদের জানান।\n\n"
            "আপনার ডিপোজিট নিশ্চিত হলে আপনার এজেন্ট অ্যাকাউন্ট সক্রিয় হবে! 🚀"
        ),
        "hi": (
            "💰 <b>जमा राशि आवश्यक!</b>\n\n"
            "कृपया अपने प्लेयर अकाउंट में जमा करें और प्रीपेमेंट पूरा करने के बाद हमें सूचित करें।\n\n"
            "आपकी जमा राशि की पुष्टि होने पर आपका एजेंट अकाउंट सक्रिय हो जाएगा! 🚀"
        ),
        "ur": (
            "💰 <b>ڈپازٹ درکار ہے!</b>\n\n"
            "براہ کرم اپنے پلیئر اکاؤنٹ میں ڈپازٹ کریں اور ادائیگی مکمل کرنے کے بعد ہمیں بتائیں۔\n\n"
            "آپ کا ڈپازٹ تصدیق ہونے پر آپ کا ایجنٹ اکاؤنٹ فعال ہو جائے گا! 🚀"
        ),
        "ar": (
            "💰 <b>مطلوب إيداع!</b>\n\n"
            "يرجى إيداع مبلغ في حساب اللاعب وإخبارنا بعد إتمام الدفع المسبق.\n\n"
            "بمجرد تأكيد إيداعك، سيتم تفعيل حساب وكيلك! 🚀"
        ),
    },
    "final_approved_msg": {
        "en": (
            "🏆 <b>Congratulations! You are now officially our Mobicash Agent!</b> 🎊\n\n"
            "Earn together with <b>7starswin</b> — win together!\n\n"
            "If you need any kind of help, contact in <b>Reddy</b> with your <b>Reddy Group</b> with your manager.\n\n"
            "Welcome to the team! 🚀"
        ),
        "ru": (
            "🏆 <b>Поздравляем! Вы официально наш агент Mobicash!</b> 🎊\n\n"
            "Зарабатывайте вместе с <b>7starswin</b> — побеждайте вместе!\n\n"
            "По всем вопросам обращайтесь в <b>Reddy</b> в вашей <b>Reddy Group</b> к вашему менеджеру.\n\n"
            "Добро пожаловать в команду! 🚀"
        ),
        "bn": (
            "🏆 <b>অভিনন্দন! আপনি এখন আনুষ্ঠানিকভাবে আমাদের Mobicash এজেন্ট!</b> 🎊\n\n"
            "<b>7starswin</b>-এর সাথে একসাথে উপার্জন করুন — একসাথে জিতুন!\n\n"
            "যেকোনো সাহায্যের জন্য <b>Reddy</b>-তে আপনার <b>Reddy Group</b>-এ আপনার ম্যানেজারের সাথে যোগাযোগ করুন।\n\n"
            "দলে স্বাগতম! 🚀"
        ),
        "hi": (
            "🏆 <b>बधाई! आप अब आधिकारिक रूप से हमारे Mobicash एजेंट हैं!</b> 🎊\n\n"
            "<b>7starswin</b> के साथ मिलकर कमाएं — मिलकर जीतें!\n\n"
            "किसी भी सहायता के लिए अपने <b>Reddy Group</b> में अपने मैनेजर से <b>Reddy</b> में संपर्क करें।\n\n"
            "टीम में आपका स्वागत है! 🚀"
        ),
        "ur": (
            "🏆 <b>مبارک ہو! آپ اب باضابطہ طور پر ہمارے Mobicash ایجنٹ ہیں!</b> 🎊\n\n"
            "<b>7starswin</b> کے ساتھ مل کر کمائیں — مل کر جیتیں!\n\n"
            "کسی بھی مدد کے لیے اپنے <b>Reddy Group</b> میں اپنے مینیجر سے <b>Reddy</b> میں رابطہ کریں۔\n\n"
            "ٹیم میں خوش آمدید! 🚀"
        ),
        "ar": (
            "🏆 <b>تهانينا! أنت الآن وكيل Mobicash الرسمي لدينا!</b> 🎊\n\n"
            "اكسب مع <b>7starswin</b> — انتصروا معاً!\n\n"
            "لأي مساعدة، تواصل في <b>Reddy</b> مع مجموعة <b>Reddy Group</b> الخاصة بك مع مديرك.\n\n"
            "مرحباً بك في الفريق! 🚀"
        ),
    },
}


def t(key: str, lang: str) -> str:
    return T.get(key, {}).get(lang) or T.get(key, {}).get("en", key)


# ─── FSM States ────────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    language         = State()
    country          = State()
    agreement        = State()
    phone            = State()
    name             = State()
    currency         = State()
    photo1           = State()
    photo2           = State()
    experience       = State()
    withdraw_address = State()   # text + photo in one message
    topup            = State()
    preview          = State()


class AdminReject(StatesGroup):
    waiting_reason = State()


class AdminReply(StatesGroup):
    waiting_message = State()


class AdminBroadcast(StatesGroup):
    waiting_message = State()


# ─── PostgreSQL ────────────────────────────────────────────────────────────────
db_pool: asyncpg.Pool = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                    SERIAL PRIMARY KEY,
    user_id               BIGINT UNIQUE NOT NULL,
    username              TEXT,
    first_name            TEXT,
    last_name             TEXT,
    language              TEXT DEFAULT 'en',
    country_code          TEXT,
    full_name             TEXT,
    phone                 TEXT,
    currency              TEXT,
    local_currency        TEXT,
    photo1_file_id        TEXT,
    photo2_file_id        TEXT,
    has_experience        BOOLEAN,
    withdraw_address      TEXT,
    withdraw_photo_file_id TEXT,
    topup_method          TEXT,
    gaming_id             TEXT,
    status                TEXT DEFAULT 'pending',
    rejection_reason      TEXT,
    registered_at         TIMESTAMPTZ,
    updated_at            TIMESTAMPTZ,
    review_at             TIMESTAMPTZ,
    review_by             BIGINT,
    deposit_at            TIMESTAMPTZ,
    deposit_by            BIGINT,
    final_approved_at     TIMESTAMPTZ,
    final_approved_by     BIGINT,
    rejected_at           TIMESTAMPTZ,
    rejected_by           BIGINT
);
"""

# Status flow: pending -> review -> deposit -> approved -> (active agent)
# Admin panels: pending_request | deposit_request | final_request | active_agent


async def init_db():
    global db_pool
    logger.info("Connecting to PostgreSQL…")
    dsn = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    db_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, ssl="require")
    async with db_pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)
    logger.info("PostgreSQL connected and table ensured.")


async def get_user(user_id: int) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
    return dict(row) if row else None


async def upsert_user(user_id: int, **fields):
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc)
    cols   = ", ".join(fields.keys())
    vals   = ", ".join(f"${i+2}" for i in range(len(fields)))
    update = ", ".join(f"{k}=EXCLUDED.{k}" for k in fields)
    sql = (
        f"INSERT INTO users (user_id, {cols}) VALUES ($1, {vals}) "
        f"ON CONFLICT (user_id) DO UPDATE SET {update}"
    )
    async with db_pool.acquire() as conn:
        await conn.execute(sql, user_id, *fields.values())


# ─── Helpers ──────────────────────────────────────────────────────────────────
def status_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("status_btn", lang))]],
        resize_keyboard=True,
    )


TOPUP_LABELS = {
    "USDT":         "🪙 USDT",
    "BINANCE":      "🏦 Binance",
    "MONEY_GO":     "💸 Money Go",
    "OTHER_CRYPTO": "🔄 Others Crypto",
}

STATUS_EMOJI = {
    "pending":  "⏳",
    "review":   "🔍",
    "deposit":  "💰",
    "approved": "✅",
    "rejected": "❌",
}


def build_terms_text(country_code: str, user_name: str, lang: str) -> str:
    commission = get_commission_text(country_code)
    country_name = SUPPORTED_COUNTRIES.get(country_code, country_code)

    base_terms = {
        "en": (
            f"👋 Welcome <b>{user_name}</b> to <b>Mobicash Agent Registration</b>!\n"
            f"📍 Country: <b>{country_name}</b>\n\n"
            f"{commission}\n\n"
            "<b>Terms &amp; Conditions:</b>\n"
            "• You will act as an authorised Mobicash agent.\n"
            "• All information provided must be accurate and truthful.\n"
            "• Fraudulent registrations will result in a permanent ban.\n"
            "• Your data is stored securely and used only for verification.\n\n"
            "Please read and accept the agreement to proceed."
        ),
        "ru": (
            f"👋 Добро пожаловать <b>{user_name}</b> в <b>Регистрацию агента Mobicash</b>!\n"
            f"📍 Страна: <b>{country_name}</b>\n\n"
            f"{commission}\n\n"
            "<b>Условия и положения:</b>\n"
            "• Вы будете действовать как авторизованный агент Mobicash.\n"
            "• Все предоставленные данные должны быть точными.\n"
            "• Мошеннические регистрации ведут к постоянной блокировке.\n"
            "• Ваши данные хранятся безопасно и используются только для верификации.\n\n"
            "Прочитайте и примите соглашение для продолжения."
        ),
        "bn": (
            f"👋 <b>{user_name}</b> Mobicash এজেন্ট নিবন্ধনে স্বাগতম!\n"
            f"📍 দেশ: <b>{country_name}</b>\n\n"
            f"{commission}\n\n"
            "<b>শর্তাবলী:</b>\n"
            "• আপনি একজন অনুমোদিত Mobicash এজেন্ট হিসেবে কাজ করবেন।\n"
            "• সকল তথ্য সঠিক ও সত্য হতে হবে।\n"
            "• প্রতারণামূলক নিবন্ধন স্থায়ী নিষেধাজ্ঞায় পরিণত হবে।\n"
            "• আপনার ডেটা নিরাপদে সংরক্ষিত।\n\n"
            "চালিয়ে যেতে চুক্তিটি পড়ুন এবং গ্রহণ করুন।"
        ),
        "hi": (
            f"👋 <b>{user_name}</b> Mobicash एजेंट पंजीकरण में आपका स्वागत है!\n"
            f"📍 देश: <b>{country_name}</b>\n\n"
            f"{commission}\n\n"
            "<b>नियम और शर्तें:</b>\n"
            "• आप एक अधिकृत Mobicash एजेंट के रूप में कार्य करेंगे।\n"
            "• सभी जानकारी सटीक और सत्य होनी चाहिए।\n"
            "• धोखाधड़ी पंजीकरण पर स्थायी प्रतिबंध लगेगा।\n"
            "• आपका डेटा सुरक्षित रूप से संग्रहीत है।\n\n"
            "आगे बढ़ने के लिए कृपया अनुबंध पढ़ें और स्वीकार करें।"
        ),
        "ur": (
            f"👋 <b>{user_name}</b> Mobicash ایجنٹ رجسٹریشن میں خوش آمدید!\n"
            f"📍 ملک: <b>{country_name}</b>\n\n"
            f"{commission}\n\n"
            "<b>شرائط و ضوابط:</b>\n"
            "• آپ ایک مجاز Mobicash ایجنٹ کے طور پر کام کریں گے۔\n"
            "• فراہم کردہ تمام معلومات درست اور سچ ہونی چاہیے۔\n"
            "• دھوکہ دہی کی رجسٹریشن مستقل پابندی کا باعث بنے گی۔\n"
            "• آپ کا ڈیٹا محفوظ طریقے سے ذخیرہ کیا جاتا ہے۔\n\n"
            "آگے بڑھنے کے لیے معاہدہ پڑھیں اور قبول کریں۔"
        ),
        "ar": (
            f"👋 مرحباً <b>{user_name}</b> في تسجيل وكيل Mobicash!\n"
            f"📍 الدولة: <b>{country_name}</b>\n\n"
            f"{commission}\n\n"
            "<b>الشروط والأحكام:</b>\n"
            "• ستعمل كوكيل معتمد لـ Mobicash.\n"
            "• يجب أن تكون جميع المعلومات المقدمة دقيقة وصحيحة.\n"
            "• التسجيلات الاحتيالية ستؤدي إلى حظر دائم.\n"
            "• يتم تخزين بياناتك بأمان.\n\n"
            "يرجى قراءة الاتفاقية والموافقة عليها للمتابعة."
        ),
    }
    return base_terms.get(lang) or base_terms["en"]


# ─── Router ────────────────────────────────────────────────────────────────────
router = Router()


# ══════════════════════════════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    doc = await get_user(message.from_user.id)
    if doc and doc.get("status") in ("approved", "pending", "review", "deposit"):
        lang = doc.get("language", "en")
        status = doc["status"]
        msgs = {
            "approved": "🏆 You are already an active Mobicash Agent! Use the button below to check your status.",
            "deposit":  "💰 Your application is awaiting deposit confirmation. Use the button below.",
            "review":   "🔍 Your application is under review. We will inform you within 48 hours.",
            "pending":  "⏳ You already have a pending registration. Use the button below.\n\nTo restart, send /restart.",
        }
        await message.answer(
            msgs.get(status, "Your application is being processed."),
            reply_markup=status_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
        return

    # Language selection
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        ],
        [
            InlineKeyboardButton(text="🇧🇩 বাংলা",   callback_data="lang_bn"),
            InlineKeyboardButton(text="🇮🇳 हिन्दी",  callback_data="lang_hi"),
        ],
        [
            InlineKeyboardButton(text="🇵🇰 اردو",   callback_data="lang_ur"),
            InlineKeyboardButton(text="🇸🇦 عربي",   callback_data="lang_ar"),
        ],
    ])
    await message.answer(
        "🌍 <b>Please select your language / ভাষা নির্বাচন করুন / भाषा चुनें / زبان منتخب کریں</b>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.language)


@router.callback_query(F.data.startswith("lang_"), Reg.language)
async def cb_language(call: CallbackQuery, state: FSMContext):
    lang = call.data.split("_", 1)[1]
    await state.update_data(lang=lang)
    await call.answer()
    await call.message.edit_reply_markup()

    # Country selection
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇧🇩 Bangladesh", callback_data="country_BD"),
            InlineKeyboardButton(text="🇮🇳 India",      callback_data="country_IN"),
        ],
        [
            InlineKeyboardButton(text="🇵🇰 Pakistan",   callback_data="country_PK"),
            InlineKeyboardButton(text="🇹🇷 Turkey",     callback_data="country_TR"),
        ],
        [
            InlineKeyboardButton(text="🇹🇭 Thailand",   callback_data="country_TH"),
            InlineKeyboardButton(text="🇵🇭 Philippines",callback_data="country_PH"),
        ],
    ])

    user_name = call.from_user.first_name or call.from_user.username or "User"
    select_text = {
        "en": f"👋 Welcome <b>{user_name}</b>!\n\n🌏 Please select your country:",
        "ru": f"👋 Добро пожаловать <b>{user_name}</b>!\n\n🌏 Выберите вашу страну:",
        "bn": f"👋 স্বাগতম <b>{user_name}</b>!\n\n🌏 আপনার দেশ নির্বাচন করুন:",
        "hi": f"👋 स्वागत है <b>{user_name}</b>!\n\n🌏 अपना देश चुनें:",
        "ur": f"👋 خوش آمدید <b>{user_name}</b>!\n\n🌏 اپنا ملک منتخب کریں:",
        "ar": f"👋 مرحباً <b>{user_name}</b>!\n\n🌏 يرجى اختيار دولتك:",
    }
    await call.message.answer(
        select_text.get(lang, select_text["en"]),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.country)


@router.callback_query(F.data.startswith("country_"), Reg.country)
async def cb_country(call: CallbackQuery, state: FSMContext):
    country_code = call.data.split("_", 1)[1]
    data = await state.get_data()
    lang = data.get("lang", "en")
    await state.update_data(country_code=country_code)
    await call.answer()
    await call.message.edit_reply_markup()

    user_name = call.from_user.first_name or call.from_user.username or "User"
    terms_text = build_terms_text(country_code, user_name, lang)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("agree_btn", lang), callback_data="agree")]]
    )
    await call.message.answer(terms_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.agreement)


# ══════════════════════════════════════════════════════════════════════════════
# /restart  /cancel
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("restart"))
async def cmd_restart(message: Message, state: FSMContext):
    await state.clear()
    doc = await get_user(message.from_user.id)
    if doc and doc.get("status") in ("approved", "deposit"):
        await message.answer("✅ You are already an active/approved agent. No need to restart.")
        return
    if doc:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM users WHERE user_id=$1", message.from_user.id)
    await cmd_start(message, state)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Nothing to cancel.")
        return
    await state.clear()
    await message.answer("❌ Registration cancelled. Send /start to begin again.", reply_markup=ReplyKeyboardRemove())


# ══════════════════════════════════════════════════════════════════════════════
# Agreement
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "agree", Reg.agreement)
async def cb_agree(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    await call.answer()
    await call.message.edit_reply_markup()

    step_text = {
        "en": "📞 <b>Step 2 of 9</b> — Phone Number\n\nPlease share your phone number using the button below.",
        "ru": "📞 <b>Шаг 2 из 9</b> — Номер телефона\n\nПоделитесь своим номером телефона.",
        "bn": "📞 <b>ধাপ ২ এর ৯</b> — ফোন নম্বর\n\nনিচের বোতাম দিয়ে আপনার ফোন নম্বর শেয়ার করুন।",
        "hi": "📞 <b>चरण 2 का 9</b> — फ़ोन नंबर\n\nकृपया नीचे बटन का उपयोग करके अपना फ़ोन नंबर साझा करें।",
        "ur": "📞 <b>مرحلہ 2 از 9</b> — فون نمبر\n\nنیچے بٹن سے اپنا فون نمبر شیئر کریں۔",
        "ar": "📞 <b>الخطوة 2 من 9</b> — رقم الهاتف\n\nيرجى مشاركة رقم هاتفك.",
    }
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("share_phone_btn", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await call.message.answer(
        step_text.get(lang, step_text["en"]),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.phone)


# ══════════════════════════════════════════════════════════════════════════════
# Phone
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.phone, F.contact)
async def got_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        await message.answer("⚠️ Please share <b>your own</b> phone number.", parse_mode=ParseMode.HTML)
        return
    await state.update_data(phone=message.contact.phone_number)
    await message.answer(t("step_name", lang), reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML)
    await state.set_state(Reg.name)


@router.message(Reg.phone)
async def phone_wrong(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("share_phone_btn", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("⚠️ Please use the <b>Share Phone Number</b> button below.", reply_markup=kb, parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Name
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.name, F.text)
async def got_name(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    country_code = data.get("country_code", "BD")
    name = message.text.strip()
    if len(name) > 40:
        await message.answer("❌ Name is too long (max 40 characters). Please try again:")
        return
    if name == name.upper() and any(c.isalpha() for c in name):
        await message.answer("❌ Please don't use ALL CAPS. Try again:")
        return
    if not NAME_RE.match(name):
        await message.answer("❌ Invalid name format. Use 2–4 words with English letters only.\n\nTry again:")
        return

    await state.update_data(full_name=name)

    # Currency based on country
    local_currency = get_currency_for_country(country_code)
    await state.update_data(local_currency=local_currency)

    # Build currency buttons — country currency + USD
    buttons = []
    if local_currency != "USD":
        buttons.append([
            InlineKeyboardButton(text=f"🌍 {local_currency}", callback_data=f"currency_{local_currency}"),
            InlineKeyboardButton(text="🇺🇸 USD",              callback_data="currency_USD"),
        ])
    else:
        buttons.append([InlineKeyboardButton(text="🇺🇸 USD", callback_data="currency_USD")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"✅ Name saved: <b>{name}</b>\n\n" + t("step_currency", lang),
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
    data = await state.get_data()
    lang = data.get("lang", "en")
    currency = call.data.split("_", 1)[1]
    await state.update_data(currency=currency)
    await call.answer(f"✅ {currency}")
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✅ Currency set to <b>{currency}</b>.\n\n" + t("step_photo1", lang),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.photo1)


# ══════════════════════════════════════════════════════════════════════════════
# Photo 1
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.photo1, F.photo)
async def got_photo1(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    await state.update_data(photo1_file_id=message.photo[-1].file_id)
    await message.answer(t("step_photo2", lang), parse_mode=ParseMode.HTML)
    await state.set_state(Reg.photo2)


@router.message(Reg.photo1, F.document)
async def photo1_as_document(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    await state.update_data(photo1_file_id=message.document.file_id)
    await message.answer(t("step_photo2", lang), parse_mode=ParseMode.HTML)
    await state.set_state(Reg.photo2)


@router.message(Reg.photo1)
async def photo1_wrong(message: Message):
    await message.answer("⚠️ Please send a <b>photo</b> of your identity document.", parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Photo 2
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.photo2, F.photo)
async def got_photo2(message: Message, state: FSMContext):
    await state.update_data(photo2_file_id=message.photo[-1].file_id)
    await _ask_experience(message, state)


@router.message(Reg.photo2, F.document)
async def photo2_as_document(message: Message, state: FSMContext):
    await state.update_data(photo2_file_id=message.document.file_id)
    await _ask_experience(message, state)


@router.message(Reg.photo2)
async def photo2_wrong(message: Message):
    await message.answer("⚠️ Please send a <b>photo</b> of the back side / second page.", parse_mode=ParseMode.HTML)


async def _ask_experience(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("yes_btn", lang), callback_data="exp_yes"),
        InlineKeyboardButton(text=t("no_btn",  lang), callback_data="exp_no"),
    ]])
    await message.answer(t("step_experience", lang), reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.experience)


# ══════════════════════════════════════════════════════════════════════════════
# Experience
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.in_({"exp_yes", "exp_no"}), Reg.experience)
async def cb_experience(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    experience = call.data == "exp_yes"
    await state.update_data(has_experience=experience)
    await call.answer()
    await call.message.edit_reply_markup()
    label = t("yes_btn", lang) if experience else t("no_btn", lang)
    await call.message.answer(
        f"✅ Experience: <b>{label}</b>\n\n" + t("step_withdraw_address", lang),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.withdraw_address)


# ══════════════════════════════════════════════════════════════════════════════
# Withdraw Address + Photo (in ONE message with caption + photo)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.withdraw_address, F.photo)
async def got_withdraw_photo_with_caption(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")

    caption = (message.caption or "").strip()
    existing_address = data.get("withdraw_address")

    if not existing_address:
        if len(caption) < 8:
            await message.answer(
                "❌ Please include your <b>withdrawal address</b> (min 8 chars) as the photo caption.\n\n"
                "Send the photo again with your address typed as caption.",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.update_data(withdraw_address=caption)

    await state.update_data(withdraw_photo_file_id=message.photo[-1].file_id)
    await _ask_topup(message, state, lang)


@router.message(Reg.withdraw_address, F.document)
async def got_withdraw_doc_with_caption(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")

    caption = (message.caption or "").strip()
    existing_address = data.get("withdraw_address")

    if not existing_address:
        if len(caption) < 8:
            await message.answer(
                "❌ Please include your <b>withdrawal address</b> (min 8 chars) as the caption.\n\n"
                "Send the photo again with your address typed as caption.",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.update_data(withdraw_address=caption)

    await state.update_data(withdraw_photo_file_id=message.document.file_id)
    await _ask_topup(message, state, lang)


@router.message(Reg.withdraw_address, F.text)
async def got_withdraw_text_only(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    address = message.text.strip()
    if len(address) < 8:
        await message.answer("❌ Address must be at least 8 characters. Try again:")
        return
    if len(address) > 200:
        await message.answer("❌ Address too long (max 200 characters). Try again:")
        return
    await state.update_data(withdraw_address=address)
    await message.answer(
        "✅ Address saved! Now please send a <b>photo of your area/location</b>.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Reg.withdraw_address)
async def withdraw_wrong(message: Message):
    await message.answer(
        "⚠️ Please send your <b>withdrawal address</b> as text, or send a <b>photo</b> with the address as caption.",
        parse_mode=ParseMode.HTML,
    )


async def _ask_topup(message: Message, state: FSMContext, lang: str):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🪙 USDT",          callback_data="topup_USDT"),
                InlineKeyboardButton(text="🏦 Binance",        callback_data="topup_BINANCE"),
            ],
            [
                InlineKeyboardButton(text="💸 Money Go",       callback_data="topup_MONEY_GO"),
                InlineKeyboardButton(text="🔄 Others Crypto",  callback_data="topup_OTHER_CRYPTO"),
            ],
        ]
    )
    await message.answer(t("step_topup", lang), reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.topup)


# ══════════════════════════════════════════════════════════════════════════════
# Top-up
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("topup_"), Reg.topup)
async def cb_topup(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    method = call.data.split("_", 1)[1]
    await state.update_data(topup_method=method)
    await call.answer()
    await call.message.edit_reply_markup()
    await _show_preview(call.message, state, lang)


# ══════════════════════════════════════════════════════════════════════════════
# Preview — shows text + ID photos visible
# ══════════════════════════════════════════════════════════════════════════════
async def _show_preview(message: Message, state: FSMContext, lang: str):
    data = await state.get_data()

    exp_label = t("yes_btn", lang) if data.get("has_experience") else t("no_btn", lang)
    country_name = SUPPORTED_COUNTRIES.get(data.get("country_code", ""), "—")
    topup_label  = TOPUP_LABELS.get(data.get("topup_method", ""), data.get("topup_method", "—"))

    preview_text = (
        t("preview_title", lang) + "\n\n"
        f"👤 <b>Full Name:</b> {data.get('full_name', '—')}\n"
        f"📞 <b>Phone:</b> {data.get('phone', '—')}\n"
        f"🌏 <b>Country:</b> {country_name}\n"
        f"💱 <b>Currency:</b> {data.get('currency', '—')}\n"
        f"📱 <b>Experience:</b> {exp_label}\n"
        f"🏠 <b>Withdraw Address:</b> {data.get('withdraw_address', '—')}\n"
        f"💳 <b>Top-up Method:</b> {topup_label}\n\n"
        f"📄 <b>Documents:</b> 2 photos uploaded ✅"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("send_btn",    lang), callback_data="preview_send"),
        InlineKeyboardButton(text=t("restart_btn", lang), callback_data="preview_restart"),
    ]])

    # Send ID photo 1
    photo1 = data.get("photo1_file_id")
    photo2 = data.get("photo2_file_id")
    withdraw_photo = data.get("withdraw_photo_file_id")

    if photo1 and photo2:
        try:
            media = [
                InputMediaPhoto(media=photo1, caption="📄 Identity Document — Front"),
                InputMediaPhoto(media=photo2, caption="📄 Identity Document — Back"),
            ]
            if withdraw_photo:
                media.append(InputMediaPhoto(media=withdraw_photo, caption="🏠 Area / Location Photo"))
            await message.answer_media_group(media)
        except Exception as e:
            logger.warning(f"Could not send preview media group: {e}")

    await message.answer(preview_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.preview)


# ══════════════════════════════════════════════════════════════════════════════
# Preview callbacks
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "preview_restart", Reg.preview)
async def cb_preview_restart(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_reply_markup()
    await state.clear()
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE user_id=$1", call.from_user.id)
    await call.message.answer("🔄 Restarting registration… Send /start to begin.")


@router.callback_query(F.data == "preview_send", Reg.preview)
async def cb_preview_send(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await call.message.edit_reply_markup()
    data = await state.get_data()
    lang = data.get("lang", "en")
    user = call.from_user
    now  = datetime.now(timezone.utc)

    await upsert_user(
        user.id,
        username               = user.username,
        first_name             = user.first_name,
        last_name              = user.last_name,
        language               = lang,
        country_code           = data.get("country_code"),
        full_name              = data["full_name"],
        phone                  = data["phone"],
        currency               = data["currency"],
        local_currency         = data.get("local_currency", "USD"),
        photo1_file_id         = data["photo1_file_id"],
        photo2_file_id         = data["photo2_file_id"],
        has_experience         = data["has_experience"],
        withdraw_address       = data["withdraw_address"],
        withdraw_photo_file_id = data.get("withdraw_photo_file_id"),
        topup_method           = data["topup_method"],
        status                 = "pending",
        registered_at          = now,
    )
    logger.info(f"New registration: user_id={user.id}")

    await call.message.answer(
        t("submission_ok", lang),
        reply_markup=status_keyboard(lang),
        parse_mode=ParseMode.HTML,
    )
    await state.clear()

    # ── Notify admins ─────────────────────────────────────────────────────────
    await _send_admin_application(bot, user.id, data, lang)


async def _send_admin_application(bot: Bot, target_user_id: int, data: dict, lang: str):
    """Send full application with photos to all admins."""
    country_name = SUPPORTED_COUNTRIES.get(data.get("country_code", ""), "—")
    commission   = get_commission_text(data.get("country_code", ""))
    topup_label  = TOPUP_LABELS.get(data.get("topup_method", ""), data.get("topup_method", "—"))
    exp_text     = "Yes ✅" if data.get("has_experience") else "No ❌"

    admin_text = (
        f"🔔 <b>New Agent Registration</b>\n\n"
        f"👤 Name: <b>{data['full_name']}</b>\n"
        f"🆔 User ID: <code>{target_user_id}</code>\n"
        f"📛 Username: @{data.get('username') or 'N/A'}\n"
        f"📞 Phone: {data['phone']}\n"
        f"🌏 Country: {country_name}\n"
        f"💱 Currency: {data['currency']}\n"
        f"📱 Experience: {exp_text}\n"
        f"🏠 Withdraw Address: {data.get('withdraw_address', '—')}\n"
        f"💳 Top-up: {topup_label}\n"
        f"🌐 Language: {LANGS.get(lang, lang)}\n\n"
        f"{commission}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔍 Mark Under Review", callback_data=f"adm_review_{target_user_id}"),
        InlineKeyboardButton(text="❌ Reject",            callback_data=f"adm_reject_{target_user_id}"),
    ]])

    for admin_id in ADMIN_IDS:
        try:
            # Send photos first as media group
            photo1 = data.get("photo1_file_id")
            photo2 = data.get("photo2_file_id")
            withdraw_photo = data.get("withdraw_photo_file_id")

            if photo1 and photo2:
                media = [
                    InputMediaPhoto(media=photo1, caption="📄 ID Front"),
                    InputMediaPhoto(media=photo2, caption="📄 ID Back"),
                ]
                if withdraw_photo:
                    media.append(InputMediaPhoto(media=withdraw_photo, caption="🏠 Area Photo"))
                await bot.send_media_group(admin_id, media)

            await bot.send_message(admin_id, admin_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Admin notify failed {admin_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: Mark Under Review (Step 1)
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("adm_review_"))
async def cb_admin_review(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status='review', review_at=$1, review_by=$2 "
            "WHERE user_id=$3 AND status='pending'",
            datetime.now(timezone.utc), call.from_user.id, target_id,
        )
    if result == "UPDATE 0":
        await call.answer("User not found or already processed.", show_alert=True)
        return
    await call.answer("✅ Marked as Under Review!")
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💰 Deposit Request",  callback_data=f"adm_deposit_{target_id}"),
        InlineKeyboardButton(text="❌ Reject",           callback_data=f"adm_reject_{target_id}"),
    ]]))
    await call.message.reply(
        f"🔍 User <code>{target_id}</code> is now <b>Under Review</b>.",
        parse_mode=ParseMode.HTML,
    )
    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"
    try:
        await bot.send_message(
            target_id,
            t("under_review_msg", lang),
            reply_markup=status_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: Deposit Request (Step 2)
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("adm_deposit_"))
async def cb_admin_deposit(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status='deposit', deposit_at=$1, deposit_by=$2 "
            "WHERE user_id=$3 AND status='review'",
            datetime.now(timezone.utc), call.from_user.id, target_id,
        )
    if result == "UPDATE 0":
        await call.answer("User not found or not in review state.", show_alert=True)
        return
    await call.answer("✅ Deposit request sent!")
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Final Approve",    callback_data=f"adm_approve_{target_id}"),
        InlineKeyboardButton(text="❌ Reject",           callback_data=f"adm_reject_{target_id}"),
        InlineKeyboardButton(text="💬 Reply to User",   callback_data=f"adm_reply_{target_id}"),
    ]]))
    await call.message.reply(
        f"💰 User <code>{target_id}</code> — <b>Deposit Request</b> sent.",
        parse_mode=ParseMode.HTML,
    )
    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"

    # Get prepay amount from country
    country_code = doc.get("country_code", "BD") if doc else "BD"
    info = COUNTRY_COMMISSION.get(country_code, {})
    prepay = info.get("prepay", 50)

    try:
        await bot.send_message(
            target_id,
            t("deposit_request_msg", lang).replace(
                "Kindly make a deposit in your player account",
                f"Kindly make a <b>${prepay} deposit</b> in your player account"
            ),
            reply_markup=status_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: Final Approve (Step 3)
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("adm_approve_"))
async def cb_admin_approve(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status='approved', final_approved_at=$1, final_approved_by=$2 "
            "WHERE user_id=$3 AND status='deposit'",
            datetime.now(timezone.utc), call.from_user.id, target_id,
        )
    if result == "UPDATE 0":
        await call.answer("User not found or not in deposit state.", show_alert=True)
        return
    await call.answer("🏆 Final Approved!")
    await call.message.edit_reply_markup()
    await call.message.reply(
        f"✅ User <code>{target_id}</code> is now a <b>fully approved Active Agent</b>.",
        parse_mode=ParseMode.HTML,
    )
    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"
    try:
        await bot.send_message(
            target_id,
            t("final_approved_msg", lang),
            reply_markup=status_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: Reject
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("adm_reject_"))
async def cb_admin_reject(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    doc = await get_user(target_id)
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
# Admin: Reply to User
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("adm_reply_"))
async def cb_admin_reply(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    await call.answer()
    await state.set_state(AdminReply.waiting_message)
    await state.update_data(reply_target_id=target_id)
    await call.message.reply(
        f"✍️ Send your message (text, photo, or video) to user <code>{target_id}</code>:\n"
        "Send /cancel_reply to abort.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("cancel_reply"), AdminReply.waiting_message)
async def cmd_cancel_reply(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Reply cancelled.")


@router.message(AdminReply.waiting_message)
async def got_admin_reply(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data["reply_target_id"]
    await state.clear()

    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"

    try:
        await bot.send_message(
            target_id,
            "📨 <b>Message from Mobicash Admin:</b>",
            parse_mode=ParseMode.HTML,
        )
        await message.copy_to(target_id)
        await message.answer(f"✅ Message sent to user <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.answer(f"❌ Failed to send: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Request Status button (user-facing)
# ══════════════════════════════════════════════════════════════════════════════
STATUS_BTN_TEXTS = {t("status_btn", lang) for lang in LANGS}


@router.message(F.text.in_(STATUS_BTN_TEXTS))
async def request_status(message: Message):
    doc = await get_user(message.from_user.id)
    if not doc:
        await message.answer("You have not registered yet. Use /start to begin.")
        return
    lang   = doc.get("language", "en")
    status = doc.get("status", "pending")
    country_name = SUPPORTED_COUNTRIES.get(doc.get("country_code", ""), "—")

    if status == "pending":
        reg_time = doc.get("registered_at")
        time_str = reg_time.strftime("%Y-%m-%d %H:%M UTC") if reg_time else "Unknown"
        await message.answer(
            f"⏳ <b>Status: Pending Review</b>\n\n"
            f"📍 Country: {country_name}\n"
            f"📅 Submitted: <b>{time_str}</b>\n\n"
            "Your application is in queue. We will review it soon.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "review":
        review_time = doc.get("review_at")
        time_str = review_time.strftime("%Y-%m-%d %H:%M UTC") if review_time else "Unknown"
        await message.answer(
            f"🔍 <b>Status: Under Review</b> (Step 1 of 3)\n\n"
            f"📍 Country: {country_name}\n"
            f"📅 Review started: <b>{time_str}</b>\n\n"
            "We are reviewing your application. You will be informed within 48 hours.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "deposit":
        deposit_time = doc.get("deposit_at")
        time_str = deposit_time.strftime("%Y-%m-%d %H:%M UTC") if deposit_time else "Unknown"
        country_code = doc.get("country_code", "BD")
        info = COUNTRY_COMMISSION.get(country_code, {})
        prepay = info.get("prepay", 50)
        await message.answer(
            f"💰 <b>Status: Deposit Required</b> (Step 2 of 3)\n\n"
            f"📍 Country: {country_name}\n"
            f"📅 Since: <b>{time_str}</b>\n\n"
            f"Please make your <b>${prepay} prepayment</b> and notify admin.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "approved":
        approved_time = doc.get("final_approved_at")
        time_str = approved_time.strftime("%Y-%m-%d %H:%M UTC") if approved_time else "Unknown"
        await message.answer(
            f"🏆 <b>Status: Active Agent</b> (Step 3 of 3 — Complete!)\n\n"
            f"📍 Country: {country_name}\n"
            f"📅 Approved: <b>{time_str}</b>\n\n"
            "You are a fully certified Mobicash Agent! 🎊",
            parse_mode=ParseMode.HTML,
        )
    elif status == "rejected":
        reason = doc.get("rejection_reason") or "No reason provided."
        await message.answer(
            f"❌ <b>Status: Rejected</b>\n\n"
            f"📍 Country: {country_name}\n"
            f"Reason: {reason}\n\n"
            "Send /restart to re-apply.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(f"Status: <b>{status}</b>", parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Admin: rejection reason handler
# ══════════════════════════════════════════════════════════════════════════════
@router.message(AdminReject.waiting_reason)
async def got_rejection_reason(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data["reject_target_id"]
    reason_text = message.text or message.caption or "[Media rejection]"

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET status='rejected', rejection_reason=$1, rejected_at=$2, rejected_by=$3 WHERE user_id=$4",
            reason_text, datetime.now(timezone.utc), message.from_user.id, target_id,
        )
    await state.clear()
    await message.answer(f"✅ User <code>{target_id}</code> rejected.", parse_mode=ParseMode.HTML)
    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"
    try:
        await bot.send_message(
            target_id,
            "❌ <b>Your Mobicash agent application has been rejected.</b>\n\nReason from admin:",
            reply_markup=status_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
        await message.copy_to(target_id)
    except Exception as e:
        logger.warning(f"Could not send rejection to user {target_id}: {e}")


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
            f"💬 <b>User Reply</b>\n"
            f"👤 {user.full_name} | ID: <code>{user.id}</code> | @{user.username or 'N/A'}"
        )
        doc = await get_user(user.id)
        if doc:
            header += f"\n📊 Status: <b>{doc.get('status', 'unknown')}</b>"
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, header, parse_mode=ParseMode.HTML)
                await message.forward(admin_id)
            except Exception as e:
                logger.warning(f"Could not forward to admin {admin_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /viewdocs
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
    doc = await get_user(target_id)
    if not doc:
        await message.answer("User not found.")
        return

    country_name = SUPPORTED_COUNTRIES.get(doc.get("country_code", ""), "—")
    commission   = get_commission_text(doc.get("country_code", ""))

    await message.answer(
        f"📂 <b>Documents for User {target_id}</b>\n"
        f"👤 {doc.get('full_name', 'N/A')} | @{doc.get('username') or 'N/A'}\n"
        f"🌏 Country: {country_name}\n"
        f"📞 Phone: {doc.get('phone', 'N/A')}\n"
        f"🏠 Withdraw Address: {doc.get('withdraw_address', 'N/A')}\n"
        f"💳 Top-up: {TOPUP_LABELS.get(doc.get('topup_method', ''), doc.get('topup_method', 'N/A'))}\n"
        f"📊 Status: <b>{doc.get('status', 'N/A')}</b>\n\n"
        f"{commission}",
        parse_mode=ParseMode.HTML,
    )

    photo1  = doc.get("photo1_file_id")
    photo2  = doc.get("photo2_file_id")
    wphoto  = doc.get("withdraw_photo_file_id")

    if photo1 and photo2:
        media = [
            InputMediaPhoto(media=photo1, caption="📄 Identity Document — Front"),
            InputMediaPhoto(media=photo2, caption="📄 Identity Document — Back"),
        ]
        if wphoto:
            media.append(InputMediaPhoto(media=wphoto, caption="🏠 Area / Location Photo"))
        try:
            await bot.send_media_group(message.chat.id, media)
        except Exception as e:
            await message.answer(f"Could not send photos: {e}")
    else:
        await message.answer("⚠️ No photos on file.")


# ══════════════════════════════════════════════════════════════════════════════
# Admin Panel — 4 sections
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("panel"))
async def cmd_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with db_pool.acquire() as conn:
        pending  = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='pending'")
        review   = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='review'")
        deposit  = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='deposit'")
        approved = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='approved'")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"⏳ Pending Requests ({pending})",  callback_data="panel_pending"),
            InlineKeyboardButton(text=f"🔍 Under Review ({review})",       callback_data="panel_review"),
        ],
        [
            InlineKeyboardButton(text=f"💰 Deposit Requests ({deposit})", callback_data="panel_deposit"),
            InlineKeyboardButton(text=f"🏆 Active Agents ({approved})",    callback_data="panel_approved"),
        ],
    ])
    await message.answer(
        "🛠️ <b>Admin Panel</b>\n\n"
        f"⏳ Pending: <b>{pending}</b>\n"
        f"🔍 Under Review: <b>{review}</b>\n"
        f"💰 Deposit: <b>{deposit}</b>\n"
        f"🏆 Active Agents: <b>{approved}</b>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("panel_"))
async def cb_panel(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    section = call.data.split("_", 1)[1]
    status_map = {
        "pending":  "pending",
        "review":   "review",
        "deposit":  "deposit",
        "approved": "approved",
    }
    section_titles = {
        "pending":  "⏳ Pending Requests",
        "review":   "🔍 Under Review",
        "deposit":  "💰 Deposit Requests",
        "approved": "🏆 Active Agents",
    }
    db_status = status_map.get(section)
    if not db_status:
        await call.answer()
        return

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username, full_name, country_code, registered_at, status "
            "FROM users WHERE status=$1 ORDER BY registered_at ASC LIMIT 30",
            db_status,
        )

    await call.answer()
    if not rows:
        await call.message.answer(f"{section_titles[section]}: <b>None found.</b>", parse_mode=ParseMode.HTML)
        return

    # Build action buttons for each user
    inline_rows = []
    lines = []
    for r in rows:
        country = SUPPORTED_COUNTRIES.get(r["country_code"] or "", "—")
        date = r["registered_at"].strftime("%m-%d %H:%M") if r["registered_at"] else "?"
        lines.append(
            f"• <code>{r['user_id']}</code> | @{r['username'] or 'N/A'} | "
            f"{r['full_name'] or '—'} | {country} | {date}"
        )
        if section == "pending":
            inline_rows.append([
                InlineKeyboardButton(text=f"🔍 Review #{r['user_id']}",   callback_data=f"adm_review_{r['user_id']}"),
                InlineKeyboardButton(text=f"❌ Reject #{r['user_id']}",   callback_data=f"adm_reject_{r['user_id']}"),
            ])
        elif section == "review":
            inline_rows.append([
                InlineKeyboardButton(text=f"💰 Deposit #{r['user_id']}", callback_data=f"adm_deposit_{r['user_id']}"),
                InlineKeyboardButton(text=f"❌ Reject #{r['user_id']}",  callback_data=f"adm_reject_{r['user_id']}"),
            ])
        elif section == "deposit":
            inline_rows.append([
                InlineKeyboardButton(text=f"✅ Approve #{r['user_id']}", callback_data=f"adm_approve_{r['user_id']}"),
                InlineKeyboardButton(text=f"❌ Reject #{r['user_id']}",  callback_data=f"adm_reject_{r['user_id']}"),
                InlineKeyboardButton(text=f"💬 Reply #{r['user_id']}",   callback_data=f"adm_reply_{r['user_id']}"),
            ])

    kb = InlineKeyboardMarkup(inline_keyboard=inline_rows) if inline_rows else None
    await call.message.answer(
        f"<b>{section_titles[section]} ({len(lines)}):</b>\n\n" + "\n".join(lines),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Admin commands
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("listpending"))
async def cmd_listpending(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username, full_name, country_code, registered_at FROM users "
            "WHERE status='pending' ORDER BY registered_at ASC"
        )
    if not rows:
        await message.answer("No pending registrations. 🎉")
        return
    lines = []
    for r in rows:
        date = r["registered_at"].strftime("%m-%d %H:%M") if r["registered_at"] else "?"
        country = SUPPORTED_COUNTRIES.get(r["country_code"] or "", "—")
        lines.append(f"• <code>{r['user_id']}</code> | @{r['username'] or 'N/A'} | {r['full_name'] or ''} | {country} | {date}")
    await message.answer(f"<b>Pending ({len(lines)}):</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with db_pool.acquire() as conn:
        total    = await conn.fetchval("SELECT COUNT(*) FROM users")
        pending  = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='pending'")
        review   = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='review'")
        deposit  = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='deposit'")
        approved = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='approved'")
        rejected = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='rejected'")
    await message.answer(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total: <b>{total}</b>\n"
        f"⏳ Pending: <b>{pending}</b>\n"
        f"🔍 Under Review: <b>{review}</b>\n"
        f"💰 Deposit: <b>{deposit}</b>\n"
        f"✅ Active Agents: <b>{approved}</b>\n"
        f"❌ Rejected: <b>{rejected}</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("approve"))
async def cmd_approve(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /approve <user_id>  (user must be in 'deposit' status)")
        return
    target_id = int(parts[1])
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status='approved', final_approved_at=$1, final_approved_by=$2 "
            "WHERE user_id=$3 AND status='deposit'",
            datetime.now(timezone.utc), message.from_user.id, target_id,
        )
    if result == "UPDATE 0":
        await message.answer("User not found or not in deposit state.")
        return
    await message.answer(f"✅ User <code>{target_id}</code> approved as Active Agent.", parse_mode=ParseMode.HTML)
    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"
    try:
        await bot.send_message(target_id, t("final_approved_msg", lang), reply_markup=status_keyboard(lang), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


@router.message(Command("reject"))
async def cmd_reject(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /reject <user_id>")
        return
    target_id = int(parts[1])
    doc = await get_user(target_id)
    if not doc:
        await message.answer("User not found.")
        return
    if doc.get("status") == "approved":
        await message.answer("User is already approved and active.")
        return
    await state.set_state(AdminReject.waiting_reason)
    await state.update_data(reject_target_id=target_id)
    await message.answer(
        f"Please send the <b>rejection reason</b> for user <code>{target_id}</code>.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("listall"))
async def cmd_listall(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    where = "WHERE status=$1" if len(parts) == 2 else ""
    params = [parts[1].lower()] if len(parts) == 2 else []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT user_id, username, full_name, country_code, status FROM users {where} ORDER BY registered_at DESC LIMIT 50",
            *params,
        )
    if not rows:
        await message.answer("No users found.")
        return
    emoji_map = {"pending": "⏳", "review": "🔍", "deposit": "💰", "approved": "✅", "rejected": "❌"}
    lines = [
        f"{emoji_map.get(r['status'], '❓')} <code>{r['user_id']}</code> | @{r['username'] or 'N/A'} | "
        f"{r['full_name'] or ''} | {SUPPORTED_COUNTRIES.get(r['country_code'] or '', '—')} | {r['status']}"
        for r in rows
    ]
    await message.answer(f"<b>Users (last 50):</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /broadcast
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminBroadcast.waiting_message)
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='approved'")
    await message.answer(
        f"📢 Send message to broadcast to <b>{count} active agents</b>. Send /cancel_broadcast to abort.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("cancel_broadcast"), AdminBroadcast.waiting_message)
async def cmd_cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Broadcast cancelled.")


@router.message(AdminBroadcast.waiting_message)
async def got_broadcast_message(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE status='approved'")
    sent = 0
    failed = 0
    for row in rows:
        try:
            await message.copy_to(row["user_id"])
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"📢 Broadcast complete.\n✅ Sent: {sent} | ❌ Failed: {failed}")


# ══════════════════════════════════════════════════════════════════════════════
# /help
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("help"))
async def cmd_help(message: Message):
    is_admin = message.from_user.id in ADMIN_IDS
    user_help = (
        "<b>Available commands:</b>\n\n"
        "/start — Start registration\n"
        "/restart — Re-apply (if rejected)\n"
        "/cancel — Cancel ongoing registration\n"
        "/help — Show this help message\n\n"
        "📋 Use the <b>Request Status</b> button to check your application."
    )
    admin_help = (
        "\n\n<b>Admin commands:</b>\n\n"
        "/panel — 📊 Admin panel with 4 sections\n"
        "/listpending — Pending applications\n"
        "/listall [status] — All users\n"
        "/stats — Statistics\n"
        "/approve &lt;user_id&gt; — Final approve\n"
        "/reject &lt;user_id&gt; — Reject with reason\n"
        "/viewdocs &lt;user_id&gt; — View all photos\n"
        "/broadcast — Send message to all active agents\n\n"
        "<b>Status flow:</b> pending → review → deposit → approved\n"
    )
    await message.answer(user_help + (admin_help if is_admin else ""), parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Fallback
# ══════════════════════════════════════════════════════════════════════════════
@router.message()
async def fallback(message: Message, state: FSMContext):
    current = await state.get_state()
    if current:
        await message.answer(
            "⚠️ Unexpected input. Please follow the instructions above.\nSend /cancel to abort."
        )
    else:
        await message.answer("👋 Send /start to begin registration or /help for commands.")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    try:
        await init_db()
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise SystemExit(1)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("Shutdown signal received…")
        stop_event.set()

    loop.add_signal_handler(signal.SIGTERM, _handle_signal)
    loop.add_signal_handler(signal.SIGINT,  _handle_signal)

    logger.info("Deleting webhook…")
    for attempt in range(5):
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            break
        except Exception as e:
            logger.warning(f"delete_webhook attempt {attempt+1} failed: {e}")
            await asyncio.sleep(2)
    await asyncio.sleep(3)

    logger.info("Starting polling…")

    async def _poll():
        while not stop_event.is_set():
            try:
                await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if stop_event.is_set():
                    break
                logger.error(f"Polling crashed: {e}. Restarting in 5s…")
                await asyncio.sleep(5)

    poll_task = asyncio.create_task(_poll())
    await stop_event.wait()

    logger.info("Stopping polling…")
    await dp.stop_polling()
    poll_task.cancel()
    try:
        await asyncio.wait_for(poll_task, timeout=10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    await bot.session.close()
    if db_pool:
        await db_pool.close()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
