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
BOT_TOKEN  = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]          # Heroku Postgres URL
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]

# ─── Validation patterns ────────────────────────────────────────────────────────
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

# ─── Multilingual strings ───────────────────────────────────────────────────────
LANGS = {
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
    "bn": "🇧🇩 বাংলা",
    "hi": "🇮🇳 हिन्दी",
    "ur": "🇵🇰 اردو",
    "ar": "🇸🇦 عربي",
}

T: dict[str, dict[str, str]] = {
    # ── agreement ──────────────────────────────────────────────────────────────
    "welcome": {
        "en": (
            "👋 Welcome to <b>Mobicash Agent Registration</b>!\n\n"
            "<b>Terms &amp; Conditions:</b>\n"
            "• You will act as an authorised Mobicash agent.\n"
            "• All information provided must be accurate and truthful.\n"
            "• Fraudulent registrations will result in a permanent ban.\n"
            "• Your data is stored securely and used only for verification.\n\n"
            "Please read and accept the agreement to proceed."
        ),
        "ru": (
            "👋 Добро пожаловать в <b>Регистрацию агента Mobicash</b>!\n\n"
            "<b>Условия и положения:</b>\n"
            "• Вы будете действовать как авторизованный агент Mobicash.\n"
            "• Все предоставленные данные должны быть точными.\n"
            "• Мошеннические регистрации ведут к постоянной блокировке.\n"
            "• Ваши данные хранятся безопасно и используются только для верификации.\n\n"
            "Прочитайте и примите соглашение для продолжения."
        ),
        "bn": (
            "👋 <b>Mobicash এজেন্ট নিবন্ধন</b>-এ স্বাগতম!\n\n"
            "<b>শর্তাবলী:</b>\n"
            "• আপনি একজন অনুমোদিত Mobicash এজেন্ট হিসেবে কাজ করবেন।\n"
            "• সকল তথ্য সঠিক ও সত্য হতে হবে।\n"
            "• প্রতারণামূলক নিবন্ধন স্থায়ী নিষেধাজ্ঞায় পরিণত হবে।\n"
            "• আপনার ডেটা নিরাপদে সংরক্ষিত এবং শুধুমাত্র যাচাইয়ের জন্য ব্যবহৃত।\n\n"
            "চালিয়ে যেতে চুক্তিটি পড়ুন এবং গ্রহণ করুন।"
        ),
        "hi": (
            "👋 <b>Mobicash एजेंट पंजीकरण</b> में आपका स्वागत है!\n\n"
            "<b>नियम और शर्तें:</b>\n"
            "• आप एक अधिकृत Mobicash एजेंट के रूप में कार्य करेंगे।\n"
            "• सभी प्रदान की गई जानकारी सटीक और सत्य होनी चाहिए।\n"
            "• धोखाधड़ी पंजीकरण पर स्थायी प्रतिबंध लगेगा।\n"
            "• आपका डेटा सुरक्षित रूप से संग्रहीत और केवल सत्यापन के लिए उपयोग किया जाता है।\n\n"
            "आगे बढ़ने के लिए कृपया अनुबंध पढ़ें और स्वीकार करें।"
        ),
        "ur": (
            "👋 <b>Mobicash ایجنٹ رجسٹریشن</b> میں خوش آمدید!\n\n"
            "<b>شرائط و ضوابط:</b>\n"
            "• آپ ایک مجاز Mobicash ایجنٹ کے طور پر کام کریں گے۔\n"
            "• فراہم کردہ تمام معلومات درست اور سچ ہونی چاہیے۔\n"
            "• دھوکہ دہی کی رجسٹریشن مستقل پابندی کا باعث بنے گی۔\n"
            "• آپ کا ڈیٹا محفوظ طریقے سے ذخیرہ کیا جاتا ہے۔\n\n"
            "آگے بڑھنے کے لیے معاہدہ پڑھیں اور قبول کریں۔"
        ),
        "ar": (
            "👋 مرحباً بك في <b>تسجيل وكيل Mobicash</b>!\n\n"
            "<b>الشروط والأحكام:</b>\n"
            "• ستعمل كوكيل معتمد لـ Mobicash.\n"
            "• يجب أن تكون جميع المعلومات المقدمة دقيقة وصحيحة.\n"
            "• التسجيلات الاحتيالية ستؤدي إلى حظر دائم.\n"
            "• يتم تخزين بياناتك بأمان وتُستخدم فقط للتحقق.\n\n"
            "يرجى قراءة الاتفاقية والموافقة عليها للمتابعة."
        ),
    },
    "agree_btn": {
        "en":"✅ I Agree & Continue","ru":"✅ Согласен и продолжить",
        "bn":"✅ আমি সম্মত ও চালিয়ে যান","hi":"✅ मैं सहमत हूँ और जारी रखें",
        "ur":"✅ میں متفق ہوں اور جاری رکھیں","ar":"✅ أوافق وأكمل",
    },
    "step_location": {
        "en":"📍 <b>Step 1 of 10</b> — Location\n\nPlease share your current location so we can determine your region and currency.",
        "ru":"📍 <b>Шаг 1 из 10</b> — Местоположение\n\nПожалуйста, поделитесь своим местоположением.",
        "bn":"📍 <b>ধাপ ১ এর ১০</b> — অবস্থান\n\nআপনার বর্তমান অবস্থান শেয়ার করুন।",
        "hi":"📍 <b>चरण 1 का 10</b> — स्थान\n\nकृपया अपना वर्तमान स्थान साझा करें।",
        "ur":"📍 <b>مرحلہ 1 از 10</b> — مقام\n\nبراہ کرم اپنا موجودہ مقام شیئر کریں۔",
        "ar":"📍 <b>الخطوة 1 من 10</b> — الموقع\n\nيرجى مشاركة موقعك الحالي.",
    },
    "share_location_btn": {
        "en":"📍 Share Location","ru":"📍 Поделиться местоположением",
        "bn":"📍 অবস্থান শেয়ার করুন","hi":"📍 स्थान साझा करें",
        "ur":"📍 مقام شیئر کریں","ar":"📍 مشاركة الموقع",
    },
    "location_ok": {
        "en":"✅ Location received!","ru":"✅ Местоположение получено!",
        "bn":"✅ অবস্থান পাওয়া গেছে!","hi":"✅ स्थान प्राप्त हुआ!",
        "ur":"✅ مقام موصول ہوا!","ar":"✅ تم استلام الموقع!",
    },
    "step_phone": {
        "en":"📞 <b>Step 2 of 10</b> — Phone Number\n\nPlease share your phone number using the button below.",
        "ru":"📞 <b>Шаг 2 из 10</b> — Номер телефона\n\nПоделитесь своим номером телефона.",
        "bn":"📞 <b>ধাপ ২ এর ১০</b> — ফোন নম্বর\n\nনিচের বোতাম দিয়ে আপনার ফোন নম্বর শেয়ার করুন।",
        "hi":"📞 <b>चरण 2 का 10</b> — फ़ोन नंबर\n\nकृपया नीचे बटन का उपयोग करके अपना फ़ोन नंबर साझा करें।",
        "ur":"📞 <b>مرحلہ 2 از 10</b> — فون نمبر\n\nنیچے بٹن سے اپنا فون نمبر شیئر کریں۔",
        "ar":"📞 <b>الخطوة 2 من 10</b> — رقم الهاتف\n\nيرجى مشاركة رقم هاتفك.",
    },
    "share_phone_btn": {
        "en":"📞 Share Phone Number","ru":"📞 Поделиться номером телефона",
        "bn":"📞 ফোন নম্বর শেয়ার করুন","hi":"📞 फ़ोन नंबर साझा करें",
        "ur":"📞 فون نمبر شیئر کریں","ar":"📞 مشاركة رقم الهاتف",
    },
    "step_name": {
        "en":(
            "✅ Phone number received!\n\n"
            "👤 <b>Step 3 of 10</b> — Full Name\n\n"
            "Please enter your <b>full name</b>.\n\n"
            "<i>Rules: 2–4 words, letters only (English / Cyrillic / French), "
            "hyphens, apostrophes and periods allowed, max 40 chars, not ALL CAPS.</i>\n\n"
            "Examples: <code>John Doe</code>, <code>Jean-Pierre Dupont</code>"
        ),
        "ru":(
            "✅ Номер телефона получен!\n\n"
            "👤 <b>Шаг 3 из 10</b> — Полное имя\n\n"
            "Введите ваше <b>полное имя</b> (2–4 слова, не CAPS)."
        ),
        "bn":(
            "✅ ফোন নম্বর পাওয়া গেছে!\n\n"
            "👤 <b>ধাপ ৩ এর ১০</b> — পুরো নাম\n\n"
            "আপনার <b>পূর্ণ নাম</b> লিখুন (২–৪ শব্দ, সর্বোচ্চ ৪০ অক্ষর)।"
        ),
        "hi":(
            "✅ फ़ोन नंबर प्राप्त हुआ!\n\n"
            "👤 <b>चरण 3 का 10</b> — पूरा नाम\n\n"
            "कृपया अपना <b>पूरा नाम</b> दर्ज करें (2–4 शब्द, अधिकतम 40 अक्षर)।"
        ),
        "ur":(
            "✅ فون نمبر موصول ہوا!\n\n"
            "👤 <b>مرحلہ 3 از 10</b> — پورا نام\n\n"
            "براہ کرم اپنا <b>پورا نام</b> درج کریں (2–4 الفاظ، زیادہ سے زیادہ 40 حروف)۔"
        ),
        "ar":(
            "✅ تم استلام رقم الهاتف!\n\n"
            "👤 <b>الخطوة 3 من 10</b> — الاسم الكامل\n\n"
            "يرجى إدخال <b>اسمك الكامل</b> (2–4 كلمات، 40 حرفاً كحد أقصى)."
        ),
    },
    "step_currency": {
        "en":"💱 <b>Step 4 of 10</b> — Currency\n\nPlease select your preferred currency:",
        "ru":"💱 <b>Шаг 4 из 10</b> — Валюта\n\nВыберите предпочтительную валюту:",
        "bn":"💱 <b>ধাপ ৪ এর ১০</b> — মুদ্রা\n\nআপনার পছন্দের মুদ্রা নির্বাচন করুন:",
        "hi":"💱 <b>चरण 4 का 10</b> — मुद्रा\n\nकृपया अपनी पसंदीदा मुद्रा चुनें:",
        "ur":"💱 <b>مرحلہ 4 از 10</b> — کرنسی\n\nاپنی پسندیدہ کرنسی منتخب کریں:",
        "ar":"💱 <b>الخطوة 4 من 10</b> — العملة\n\nيرجى اختيار عملتك المفضلة:",
    },
    "step_photo1": {
        "en":(
            "📄 <b>Step 5 of 10</b> — Identity Document\n\n"
            "Please <b>Send First Photo</b> of your identity document "
            "(Passport / National ID / Driving Licence).\n\n"
            "<i>Make sure the photo is clear and all text is readable.</i>"
        ),
        "ru":(
            "📄 <b>Шаг 5 из 10</b> — Документ удостоверяющий личность\n\n"
            "Пожалуйста, <b>отправьте первое фото</b> вашего документа.\n\n"
            "<i>Убедитесь, что фото чёткое и весь текст читаем.</i>"
        ),
        "bn":(
            "📄 <b>ধাপ ৫ এর ১০</b> — পরিচয় নথি\n\n"
            "আপনার পরিচয় নথির <b>প্রথম ছবি পাঠান</b> "
            "(পাসপোর্ট / জাতীয় পরিচয়পত্র / ড্রাইভিং লাইসেন্স)।\n\n"
            "<i>ছবিটি স্পষ্ট হতে হবে।</i>"
        ),
        "hi":(
            "📄 <b>चरण 5 का 10</b> — पहचान दस्तावेज़\n\n"
            "कृपया अपने पहचान दस्तावेज़ की <b>पहली फ़ोटो भेजें</b>।\n\n"
            "<i>सुनिश्चित करें कि फ़ोटो स्पष्ट हो।</i>"
        ),
        "ur":(
            "📄 <b>مرحلہ 5 از 10</b> — شناختی دستاویز\n\n"
            "براہ کرم اپنی شناختی دستاویز کی <b>پہلی تصویر بھیجیں</b>۔\n\n"
            "<i>یقینی بنائیں کہ تصویر واضح ہو۔</i>"
        ),
        "ar":(
            "📄 <b>الخطوة 5 من 10</b> — وثيقة الهوية\n\n"
            "يرجى <b>إرسال الصورة الأولى</b> لوثيقة هويتك.\n\n"
            "<i>تأكد من وضوح الصورة.</i>"
        ),
    },
    "step_photo2": {
        "en":(
            "✅ First photo received!\n\n"
            "📄 <b>Step 6 of 10</b> — Identity Document (Second Photo)\n\n"
            "Please <b>Send Another Photo</b> of your identity document "
            "(back side or second page of passport).\n\n"
            "<i>Make sure the photo is clear and all text is readable.</i>"
        ),
        "ru":(
            "✅ Первое фото получено!\n\n"
            "📄 <b>Шаг 6 из 10</b> — Документ (Второе фото)\n\n"
            "Пожалуйста, <b>отправьте ещё одно фото</b> (обратная сторона или вторая страница)."
        ),
        "bn":(
            "✅ প্রথম ছবি পাওয়া গেছে!\n\n"
            "📄 <b>ধাপ ৬ এর ১০</b> — পরিচয় নথি (দ্বিতীয় ছবি)\n\n"
            "অনুগ্রহ করে <b>আরেকটি ছবি পাঠান</b> (পেছনের দিক বা দ্বিতীয় পাতা)।"
        ),
        "hi":(
            "✅ पहली फ़ोटो प्राप्त हुई!\n\n"
            "📄 <b>चरण 6 का 10</b> — पहचान दस्तावेज़ (दूसरी फ़ोटो)\n\n"
            "कृपया <b>एक और फ़ोटो भेजें</b> (पिछली तरफ या दूसरा पृष्ठ)।"
        ),
        "ur":(
            "✅ پہلی تصویر موصول ہوئی!\n\n"
            "📄 <b>مرحلہ 6 از 10</b> — شناختی دستاویز (دوسری تصویر)\n\n"
            "براہ کرم <b>ایک اور تصویر بھیجیں</b> (پچھلی طرف یا دوسرا صفحہ)۔"
        ),
        "ar":(
            "✅ تم استلام الصورة الأولى!\n\n"
            "📄 <b>الخطوة 6 من 10</b> — وثيقة الهوية (الصورة الثانية)\n\n"
            "يرجى <b>إرسال صورة أخرى</b> (الجهة الخلفية أو الصفحة الثانية)."
        ),
    },
    "step_experience": {
        "en":(
            "✅ Both identity photos received!\n\n"
            "📱 <b>Step 7 of 10</b> — App Experience\n\n"
            "Do you have prior experience working with the <b>MobCash mobile app</b>?"
        ),
        "ru":(
            "✅ Оба фото документов получены!\n\n"
            "📱 <b>Шаг 7 из 10</b> — Опыт работы с приложением\n\n"
            "Есть ли у вас опыт работы с мобильным приложением <b>MobCash</b>?"
        ),
        "bn":(
            "✅ উভয় পরিচয় ছবি পাওয়া গেছে!\n\n"
            "📱 <b>ধাপ ৭ এর ১০</b> — অ্যাপ অভিজ্ঞতা\n\n"
            "আপনার কি <b>MobCash মোবাইল অ্যাপ</b> নিয়ে আগের অভিজ্ঞতা আছে?"
        ),
        "hi":(
            "✅ दोनों पहचान फ़ोटो प्राप्त हुईं!\n\n"
            "📱 <b>चरण 7 का 10</b> — ऐप अनुभव\n\n"
            "क्या आपको <b>MobCash मोबाइल ऐप</b> के साथ काम करने का अनुभव है?"
        ),
        "ur":(
            "✅ دونوں شناختی تصاویر موصول ہوئیں!\n\n"
            "📱 <b>مرحلہ 7 از 10</b> — ایپ کا تجربہ\n\n"
            "کیا آپ کو <b>MobCash موبائل ایپ</b> کے ساتھ کام کرنے کا تجربہ ہے؟"
        ),
        "ar":(
            "✅ تم استلام صورتي الهوية!\n\n"
            "📱 <b>الخطوة 7 من 10</b> — تجربة التطبيق\n\n"
            "هل لديك خبرة سابقة في العمل مع <b>تطبيق MobCash</b>؟"
        ),
    },
    "yes_btn": {
        "en":"✅ Yes","ru":"✅ Да","bn":"✅ হ্যাঁ","hi":"✅ हाँ","ur":"✅ ہاں","ar":"✅ نعم",
    },
    "no_btn": {
        "en":"❌ No","ru":"❌ Нет","bn":"❌ না","hi":"❌ नहीं","ur":"❌ نہیں","ar":"❌ لا",
    },
    "step_street": {
        "en":(
            "🏠 <b>Step 8 of 10</b> — Street Name & Photo\n\n"
            "Please enter your <b>street name</b> (min 2 chars), then send a <b>photo of your street/area</b>."
        ),
        "ru":(
            "🏠 <b>Шаг 8 из 10</b> — Улица и фото\n\n"
            "Введите название вашей <b>улицы</b> (мин 2 символа), затем отправьте <b>фото улицы/района</b>."
        ),
        "bn":(
            "🏠 <b>ধাপ ৮ এর ১০</b> — রাস্তার নাম ও ছবি\n\n"
            "আপনার <b>রাস্তার নাম</b> লিখুন (ন্যূনতম ২ অক্ষর), তারপর <b>রাস্তার/এলাকার একটি ছবি</b> পাঠান।"
        ),
        "hi":(
            "🏠 <b>चरण 8 का 10</b> — सड़क का नाम और फ़ोटो\n\n"
            "कृपया अपनी <b>सड़क का नाम</b> दर्ज करें (न्यूनतम 2 अक्षर), फिर <b>सड़क/क्षेत्र की फ़ोटो</b> भेजें।"
        ),
        "ur":(
            "🏠 <b>مرحلہ 8 از 10</b> — گلی کا نام اور تصویر\n\n"
            "براہ کرم اپنی <b>گلی کا نام</b> درج کریں (کم از کم 2 حروف)، پھر <b>گلی/علاقے کی تصویر</b> بھیجیں۔"
        ),
        "ar":(
            "🏠 <b>الخطوة 8 من 10</b> — اسم الشارع والصورة\n\n"
            "يرجى إدخال <b>اسم شارعك</b> (2 أحرف كحد أدنى)، ثم أرسل <b>صورة للشارع/المنطقة</b>."
        ),
    },
    "street_name_ok_now_photo": {
        "en":"✅ Street name saved! Now please send a <b>photo of your street or area</b>.",
        "ru":"✅ Название улицы сохранено! Теперь отправьте <b>фото вашей улицы или района</b>.",
        "bn":"✅ রাস্তার নাম সংরক্ষিত! এখন <b>আপনার রাস্তা বা এলাকার ছবি</b> পাঠান।",
        "hi":"✅ सड़क का नाम सहेजा गया! अब <b>अपनी सड़क या क्षेत्र की फ़ोटो</b> भेजें।",
        "ur":"✅ گلی کا نام محفوظ ہو گیا! اب <b>اپنی گلی یا علاقے کی تصویر</b> بھیجیں۔",
        "ar":"✅ تم حفظ اسم الشارع! الآن أرسل <b>صورة لشارعك أو منطقتك</b>.",
    },
    "step_topup": {
        "en":(
            "✅ Street photo received!\n\n"
            "💳 <b>Step 9 of 10</b> — Top-up Method\n\n"
            "Please select your preferred <b>top-up method</b>:"
        ),
        "ru":(
            "✅ Фото улицы получено!\n\n"
            "💳 <b>Шаг 9 из 10</b> — Способ пополнения\n\n"
            "Выберите предпочтительный <b>способ пополнения</b>:"
        ),
        "bn":(
            "✅ রাস্তার ছবি পাওয়া গেছে!\n\n"
            "💳 <b>ধাপ ৯ এর ১০</b> — টপ-আপ পদ্ধতি\n\n"
            "আপনার পছন্দের <b>টপ-আপ পদ্ধতি</b> নির্বাচন করুন:"
        ),
        "hi":(
            "✅ सड़क की फ़ोटो प्राप्त हुई!\n\n"
            "💳 <b>चरण 9 का 10</b> — टॉप-अप विधि\n\n"
            "कृपया अपनी पसंदीदा <b>टॉप-अप विधि</b> चुनें:"
        ),
        "ur":(
            "✅ گلی کی تصویر موصول ہوئی!\n\n"
            "💳 <b>مرحلہ 9 از 10</b> — ٹاپ-اپ طریقہ\n\n"
            "اپنا پسندیدہ <b>ٹاپ-اپ طریقہ</b> منتخب کریں:"
        ),
        "ar":(
            "✅ تم استلام صورة الشارع!\n\n"
            "💳 <b>الخطوة 9 من 10</b> — طريقة الشحن\n\n"
            "يرجى اختيار <b>طريقة الشحن</b> المفضلة لديك:"
        ),
    },
    "step_gaming_id": {
        "en":"✅ Top-up method selected!\n\n🎮 Please enter your <b>7starswin Gaming ID</b> (numeric only, 9–11 digits):",
        "ru":"✅ Способ пополнения выбран!\n\n🎮 Введите ваш <b>игровой ID 7starswin</b> (только цифры, 9–11 цифр):",
        "bn":"✅ টপ-আপ পদ্ধতি নির্বাচিত!\n\n🎮 আপনার <b>7starswin গেমিং আইডি</b> লিখুন (শুধুমাত্র সংখ্যা, ৯–১১ সংখ্যা):",
        "hi":"✅ टॉप-अप विधि चुनी गई!\n\n🎮 अपना <b>7starswin गेमिंग ID</b> दर्ज करें (केवल संख्याएं, 9–11 अंक):",
        "ur":"✅ ٹاپ-اپ طریقہ منتخب!\n\n🎮 اپنا <b>7starswin گیمنگ آئی ڈی</b> درج کریں (صرف نمبر، 9–11 ہندسے):",
        "ar":"✅ تم اختيار طريقة الشحن!\n\n🎮 يرجى إدخال <b>معرف 7starswin</b> (أرقام فقط، 9–11 رقماً):",
    },
    "step_about": {
        "en":(
            "✅ Gaming ID saved!\n\n"
            "📝 <b>Step 10 of 10</b> — About You as an Agent\n\n"
            "Please write a few sentences about yourself as an agent:\n"
            "• Your experience in finance / mobile money\n"
            "• Why you want to become a Mobicash agent\n"
            "• Any other relevant information\n\n"
            "<i>(Minimum 20 characters)</i>"
        ),
        "ru":(
            "✅ Игровой ID сохранён!\n\n"
            "📝 <b>Шаг 10 из 10</b> — О вас как агенте\n\n"
            "Расскажите немного о себе как агенте:\n"
            "• Ваш опыт в финансах / мобильных деньгах\n"
            "• Почему вы хотите стать агентом Mobicash\n"
            "• Любая другая актуальная информация\n\n"
            "<i>(Минимум 20 символов)</i>"
        ),
        "bn":(
            "✅ গেমিং আইডি সংরক্ষিত!\n\n"
            "📝 <b>ধাপ ১০ এর ১০</b> — এজেন্ট হিসেবে আপনার সম্পর্কে\n\n"
            "এজেন্ট হিসেবে নিজের সম্পর্কে কিছু লিখুন:\n"
            "• অর্থ / মোবাইল মানিতে আপনার অভিজ্ঞতা\n"
            "• কেন আপনি Mobicash এজেন্ট হতে চান\n"
            "• অন্য কোনো প্রাসঙ্গিক তথ্য\n\n"
            "<i>(ন্যূনতম ২০ অক্ষর)</i>"
        ),
        "hi":(
            "✅ गेमिंग ID सहेजी गई!\n\n"
            "📝 <b>चरण 10 का 10</b> — एजेंट के रूप में आपके बारे में\n\n"
            "कृपया एजेंट के रूप में अपने बारे में कुछ लिखें:\n"
            "• वित्त / मोबाइल मनी में आपका अनुभव\n"
            "• आप Mobicash एजेंट क्यों बनना चाहते हैं\n"
            "• कोई अन्य प्रासंगिक जानकारी\n\n"
            "<i>(न्यूनतम 20 अक्षर)</i>"
        ),
        "ur":(
            "✅ گیمنگ آئی ڈی محفوظ ہو گئی!\n\n"
            "📝 <b>مرحلہ 10 از 10</b> — ایجنٹ کے طور پر آپ کے بارے میں\n\n"
            "براہ کرم ایجنٹ کے طور پر اپنے بارے میں کچھ لکھیں:\n"
            "• مالیات / موبائل منی میں آپ کا تجربہ\n"
            "• آپ Mobicash ایجنٹ کیوں بننا چاہتے ہیں\n"
            "• کوئی اور متعلقہ معلومات\n\n"
            "<i>(کم از کم 20 حروف)</i>"
        ),
        "ar":(
            "✅ تم حفظ معرف اللعبة!\n\n"
            "📝 <b>الخطوة 10 من 10</b> — عنك كوكيل\n\n"
            "يرجى كتابة بعض الجمل عن نفسك كوكيل:\n"
            "• خبرتك في المال / المال المحمول\n"
            "• لماذا تريد أن تصبح وكيل Mobicash\n"
            "• أي معلومات أخرى ذات صلة\n\n"
            "<i>(20 حرفاً كحد أدنى)</i>"
        ),
    },
    "preview_title": {
        "en":"📋 <b>Registration Preview</b>\n\nPlease review your information before submitting:",
        "ru":"📋 <b>Предварительный просмотр регистрации</b>\n\nПроверьте вашу информацию перед отправкой:",
        "bn":"📋 <b>নিবন্ধন পূর্বরূপ</b>\n\nজমা দেওয়ার আগে আপনার তথ্য পর্যালোচনা করুন:",
        "hi":"📋 <b>पंजीकरण पूर्वावलोकन</b>\n\nजमा करने से पहले अपनी जानकारी की समीक्षा करें:",
        "ur":"📋 <b>رجسٹریشن پیش نظارہ</b>\n\nجمع کرانے سے پہلے اپنی معلومات کا جائزہ لیں:",
        "ar":"📋 <b>معاينة التسجيل</b>\n\nيرجى مراجعة معلوماتك قبل الإرسال:",
    },
    "send_btn": {
        "en":"✅ Send Application","ru":"✅ Отправить заявку",
        "bn":"✅ আবেদন পাঠান","hi":"✅ आवेदन भेजें",
        "ur":"✅ درخواست بھیجیں","ar":"✅ إرسال الطلب",
    },
    "restart_btn": {
        "en":"🔄 Restart","ru":"🔄 Начать заново",
        "bn":"🔄 পুনরায় শুরু","hi":"🔄 पुनः प्रारंभ",
        "ur":"🔄 دوبارہ شروع","ar":"🔄 إعادة البدء",
    },
    "status_btn": {
        "en":"📋 Request Status","ru":"📋 Статус заявки",
        "bn":"📋 অনুরোধের অবস্থা","hi":"📋 अनुरोध की स्थिति",
        "ur":"📋 درخواست کی حیثیت","ar":"📋 حالة الطلب",
    },
    "submission_ok": {
        "en":(
            "🎉 <b>Application Submitted!</b>\n\n"
            "Your application is <b>pending admin review</b>.\n"
            "You'll be notified once a decision is made.\n\n"
            "Use the button below to check your status at any time."
        ),
        "ru":(
            "🎉 <b>Заявка отправлена!</b>\n\n"
            "Ваша заявка находится на <b>рассмотрении у администратора</b>.\n"
            "Вы получите уведомление после принятия решения."
        ),
        "bn":(
            "🎉 <b>আবেদন জমা হয়েছে!</b>\n\n"
            "আপনার আবেদন <b>অ্যাডমিন পর্যালোচনার অপেক্ষায়</b>।\n"
            "সিদ্ধান্ত হলে আপনাকে জানানো হবে।"
        ),
        "hi":(
            "🎉 <b>आवेदन जमा हुआ!</b>\n\n"
            "आपका आवेदन <b>व्यवस्थापक समीक्षा के लिए लंबित</b> है।\n"
            "निर्णय होने पर आपको सूचित किया जाएगा।"
        ),
        "ur":(
            "🎉 <b>درخواست جمع ہو گئی!</b>\n\n"
            "آپ کی درخواست <b>ایڈمن کے جائزے کے لیے زیر التوا</b> ہے۔\n"
            "فیصلہ ہونے پر آپ کو مطلع کیا جائے گا۔"
        ),
        "ar":(
            "🎉 <b>تم تقديم الطلب!</b>\n\n"
            "طلبك <b>قيد مراجعة المسؤول</b>.\n"
            "سيتم إخطارك بمجرد اتخاذ القرار."
        ),
    },
    "approved_msg": {
        "en":"🎉 <b>Congratulations!</b> Your Mobicash agent application has been <b>approved</b>! Welcome to the team! 🚀",
        "ru":"🎉 <b>Поздравляем!</b> Ваша заявка на агента Mobicash <b>одобрена</b>! Добро пожаловать в команду! 🚀",
        "bn":"🎉 <b>অভিনন্দন!</b> আপনার Mobicash এজেন্ট আবেদন <b>অনুমোদিত</b>! দলে স্বাগতম! 🚀",
        "hi":"🎉 <b>बधाई!</b> आपका Mobicash एजेंट आवेदन <b>अनुमोदित</b> हो गया! टीम में आपका स्वागत है! 🚀",
        "ur":"🎉 <b>مبارک ہو!</b> آپ کی Mobicash ایجنٹ درخواست <b>منظور</b> ہو گئی! ٹیم میں خوش آمدید! 🚀",
        "ar":"🎉 <b>تهانينا!</b> تمت <b>الموافقة</b> على طلب وكيل Mobicash الخاص بك! مرحباً بك في الفريق! 🚀",
    },
    "ongoing_msg": {
        "en":"✅ Your application has been <b>accepted</b> and is now <b>ongoing / active</b>. You are now an active Mobicash agent! 🚀",
        "ru":"✅ Ваша заявка <b>принята</b> и теперь находится в статусе <b>активна</b>. Вы теперь активный агент Mobicash! 🚀",
        "bn":"✅ আপনার আবেদন <b>গৃহীত</b> হয়েছে এবং এখন <b>চলমান / সক্রিয়</b>। আপনি এখন একজন সক্রিয় Mobicash এজেন্ট! 🚀",
        "hi":"✅ आपका आवेदन <b>स्वीकार</b> कर लिया गया है और अब <b>चल रहा / सक्रिय</b> है। आप अब एक सक्रिय Mobicash एजेंट हैं! 🚀",
        "ur":"✅ آپ کی درخواست <b>قبول</b> ہو گئی ہے اور اب <b>جاری / فعال</b> ہے۔ آپ اب ایک فعال Mobicash ایجنٹ ہیں! 🚀",
        "ar":"✅ تم <b>قبول</b> طلبك وهو الآن <b>جارٍ / نشط</b>. أنت الآن وكيل Mobicash نشط! 🚀",
    },
    "final_approved_msg": {
        "en":"🏆 <b>Final Approval!</b> Your Mobicash agent registration has been <b>fully approved</b>. You are a fully certified Mobicash agent! 🎊",
        "ru":"🏆 <b>Финальное одобрение!</b> Ваша регистрация агента Mobicash <b>полностью одобрена</b>. Вы полностью сертифицированный агент Mobicash! 🎊",
        "bn":"🏆 <b>চূড়ান্ত অনুমোদন!</b> আপনার Mobicash এজেন্ট নিবন্ধন <b>সম্পূর্ণ অনুমোদিত</b>। আপনি একজন সম্পূর্ণ সার্টিফাইড Mobicash এজেন্ট! 🎊",
        "hi":"🏆 <b>अंतिम अनुमोदन!</b> आपका Mobicash एजेंट पंजीकरण <b>पूरी तरह से अनुमोदित</b> है। आप एक पूर्ण प्रमाणित Mobicash एजेंट हैं! 🎊",
        "ur":"🏆 <b>حتمی منظوری!</b> آپ کی Mobicash ایجنٹ رجسٹریشن <b>مکمل طور پر منظور</b> ہو گئی ہے۔ آپ ایک مکمل تصدیق شدہ Mobicash ایجنٹ ہیں! 🎊",
        "ar":"🏆 <b>الموافقة النهائية!</b> تم <b>الاعتماد الكامل</b> لتسجيلك كوكيل Mobicash. أنت الآن وكيل Mobicash معتمد بالكامل! 🎊",
    },
}


def t(key: str, lang: str) -> str:
    """Return translated string; fallback to English."""
    return T.get(key, {}).get(lang) or T.get(key, {}).get("en", key)


# ─── FSM States ────────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    language   = State()
    agreement  = State()
    location   = State()
    phone      = State()
    name       = State()
    currency   = State()
    photo1     = State()
    photo2     = State()
    experience = State()
    street     = State()
    street_photo = State()
    topup      = State()
    gaming_id  = State()
    about      = State()
    preview    = State()


class AdminReject(StatesGroup):
    waiting_reason = State()


class AdminBroadcast(StatesGroup):
    waiting_message = State()


# ─── PostgreSQL ────────────────────────────────────────────────────────────────
db_pool: asyncpg.Pool = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                SERIAL PRIMARY KEY,
    user_id           BIGINT UNIQUE NOT NULL,
    username          TEXT,
    first_name        TEXT,
    last_name         TEXT,
    language          TEXT DEFAULT 'en',
    full_name         TEXT,
    phone             TEXT,
    lat               DOUBLE PRECISION,
    lon               DOUBLE PRECISION,
    currency          TEXT,
    local_currency    TEXT,
    photo1_file_id    TEXT,
    photo2_file_id    TEXT,
    has_experience    BOOLEAN,
    street            TEXT,
    street_photo_file_id TEXT,
    topup_method      TEXT,
    gaming_id         TEXT,
    about_agent       TEXT,
    status            TEXT DEFAULT 'pending',
    rejection_reason  TEXT,
    registered_at     TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ,
    approved_at       TIMESTAMPTZ,
    approved_by       BIGINT,
    ongoing_at        TIMESTAMPTZ,
    ongoing_by        BIGINT,
    final_approved_at TIMESTAMPTZ,
    final_approved_by BIGINT,
    rejected_at       TIMESTAMPTZ,
    rejected_by       BIGINT
);
"""


async def init_db():
    global db_pool
    logger.info("Connecting to PostgreSQL…")
    # Heroku DATABASE_URL starts with postgres:// but asyncpg needs postgresql://
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
async def notify_admins(bot: Bot, text: str, **kwargs):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


async def reverse_geocode(lat: float, lon: float) -> str:
    url = (
        f"https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json&accept-language=en"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": "MobicashBot/1.0"},
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


def status_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("status_btn", lang))]],
        resize_keyboard=True,
    )


TOPUP_LABELS = {
    "USDT": "🪙 USDT",
    "BTC":  "₿ Bitcoin",
    "ETH":  "Ξ Ethereum",
    "OTHER":"🔄 Other Crypto",
}


# ─── Router ────────────────────────────────────────────────────────────────────
router = Router()


# ══════════════════════════════════════════════════════════════════════════════
# /start  — language selection
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    doc = await get_user(message.from_user.id)
    if doc and doc.get("status") in ("approved", "pending", "ongoing"):
        lang = doc.get("language", "en")
        status = doc["status"]
        if status == "approved":
            await message.answer(
                "✅ You are already a registered Mobicash agent!\nUse the button below to check your status.",
                reply_markup=status_keyboard(lang),
            )
        elif status == "ongoing":
            await message.answer(
                "🔄 Your application is currently <b>ongoing/active</b>.\nUse the button below to check your status.\n\nTo restart, send /restart.",
                reply_markup=status_keyboard(lang),
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(
                "⏳ You already have a <b>pending</b> registration.\nUse the button below to check your status.\n\nTo restart, send /restart.",
                reply_markup=status_keyboard(lang),
                parse_mode=ParseMode.HTML,
            )
        return

    # Language selection keyboard
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇬🇧 English",  callback_data="lang_en"),
            InlineKeyboardButton(text="🇷🇺 Русский",  callback_data="lang_ru"),
        ],
        [
            InlineKeyboardButton(text="🇧🇩 বাংলা",    callback_data="lang_bn"),
            InlineKeyboardButton(text="🇮🇳 हिन्दी",   callback_data="lang_hi"),
        ],
        [
            InlineKeyboardButton(text="🇵🇰 اردو",    callback_data="lang_ur"),
            InlineKeyboardButton(text="🇸🇦 عربي",    callback_data="lang_ar"),
        ],
    ])
    await message.answer(
        "🌍 <b>Please select your language / Пожалуйста, выберите язык / ভাষা নির্বাচন করুন / भाषा चुनें / زبان منتخب کریں / اختر لغتك</b>",
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

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("agree_btn", lang), callback_data="agree")]]
    )
    await call.message.answer(t("welcome", lang), reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.agreement)


# ══════════════════════════════════════════════════════════════════════════════
# /restart
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("restart"))
async def cmd_restart(message: Message, state: FSMContext):
    await state.clear()
    doc = await get_user(message.from_user.id)
    if doc and doc.get("status") in ("approved", "ongoing"):
        await message.answer("✅ You are already an active/approved agent. No need to restart.")
        return
    if doc:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM users WHERE user_id=$1", message.from_user.id)
    await cmd_start(message, state)


# ══════════════════════════════════════════════════════════════════════════════
# /cancel
# ══════════════════════════════════════════════════════════════════════════════
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
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("share_location_btn", lang), request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await call.message.answer(t("step_location", lang), reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.location)


# ══════════════════════════════════════════════════════════════════════════════
# Location
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.location, F.location)
async def got_location(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    await state.update_data(lat=message.location.latitude, lon=message.location.longitude)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("share_phone_btn", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        t("location_ok", lang) + "\n\n" + t("step_phone", lang),
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.phone)


@router.message(Reg.location)
async def location_wrong(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t("share_location_btn", lang), request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "⚠️ Please use the <b>Share Location</b> button below.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


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
    name = message.text.strip()
    if len(name) > 40:
        await message.answer("❌ Name is too long (max 40 characters). Please try again:")
        return
    if name == name.upper() and any(c.isalpha() for c in name):
        await message.answer("❌ Please don't use ALL CAPS. Try again:")
        return
    if not NAME_RE.match(name):
        await message.answer("❌ Invalid name format. Use 2–4 words with letters only.\n\nTry again:")
        return

    await state.update_data(full_name=name)
    local_currency = await reverse_geocode(data["lat"], data["lon"])
    await state.update_data(local_currency=local_currency)

    buttons = [[InlineKeyboardButton(text="🇺🇸 USD (default)", callback_data="currency_USD")]]
    if local_currency != "USD":
        buttons.append([InlineKeyboardButton(
            text=f"🌍 {local_currency} (local)",
            callback_data=f"currency_{local_currency}",
        )])
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
async def photo1_wrong(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    await message.answer(
        "⚠️ Please <b>Send First Photo</b> of your identity document.",
        parse_mode=ParseMode.HTML,
    )


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
async def photo2_wrong(message: Message, state: FSMContext):
    await message.answer(
        "⚠️ Please <b>Send Another Photo</b> of your identity document.",
        parse_mode=ParseMode.HTML,
    )


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
        f"✅ Experience: <b>{label}</b>\n\n" + t("step_street", lang),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.street)


# ══════════════════════════════════════════════════════════════════════════════
# Street (text first, then photo)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.street, F.text)
async def got_street(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    street = message.text.strip()
    if len(street) < 2:
        await message.answer("❌ Street name must be at least 2 characters. Try again:")
        return
    if len(street) > 80:
        await message.answer("❌ Street name too long (max 80 characters). Try again:")
        return
    await state.update_data(street=street)
    await message.answer(t("street_name_ok_now_photo", lang), parse_mode=ParseMode.HTML)
    await state.set_state(Reg.street_photo)


@router.message(Reg.street)
async def street_not_text(message: Message):
    await message.answer("⚠️ Please send the street name as text.")


# ── Street Photo ──────────────────────────────────────────────────────────────
@router.message(Reg.street_photo, F.photo)
async def got_street_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    await state.update_data(street_photo_file_id=message.photo[-1].file_id)
    await _ask_topup(message, state, lang)


@router.message(Reg.street_photo, F.document)
async def got_street_photo_doc(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    await state.update_data(street_photo_file_id=message.document.file_id)
    await _ask_topup(message, state, lang)


@router.message(Reg.street_photo)
async def street_photo_wrong(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    await message.answer(
        "⚠️ Please send a <b>photo of your street/area</b>.",
        parse_mode=ParseMode.HTML,
    )


async def _ask_topup(message: Message, state: FSMContext, lang: str):
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
    await message.answer(t("step_topup", lang), reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Reg.topup)


# ══════════════════════════════════════════════════════════════════════════════
# Top-up method
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("topup_"), Reg.topup)
async def cb_topup(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    method = call.data.split("_", 1)[1]
    await state.update_data(topup_method=method)
    await call.answer()
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"✅ Top-up: <b>{TOPUP_LABELS.get(method, method)}</b>\n\n" + t("step_gaming_id", lang),
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Reg.gaming_id)


# ══════════════════════════════════════════════════════════════════════════════
# Gaming ID
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.gaming_id, F.text)
async def got_gaming_id(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    gid = message.text.strip()
    if not GAMING_ID_RE.match(gid):
        await message.answer(
            "❌ Invalid gaming ID. It must be <b>9–11 digits</b>. Please try again:",
            parse_mode=ParseMode.HTML,
        )
        return
    # Check duplicate
    async with db_pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT user_id FROM users WHERE gaming_id=$1 AND user_id!=$2",
            gid, message.from_user.id,
        )
    if existing:
        await message.answer("❌ This gaming ID is already registered. Contact support.")
        return
    await state.update_data(gaming_id=gid)
    await message.answer(t("step_about", lang), parse_mode=ParseMode.HTML)
    await state.set_state(Reg.about)


@router.message(Reg.gaming_id)
async def gaming_id_not_text(message: Message):
    await message.answer("⚠️ Please send your gaming ID as a number (9–11 digits).")


# ══════════════════════════════════════════════════════════════════════════════
# About agent (Step 10)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Reg.about, F.text)
async def got_about(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")
    about = message.text.strip()
    if len(about) < 20:
        await message.answer("❌ Please write at least 20 characters about yourself as an agent.\n\nTry again:")
        return
    await state.update_data(about_agent=about)
    await _show_preview(message, state)


@router.message(Reg.about)
async def about_not_text(message: Message):
    await message.answer("⚠️ Please send your description as text.")


async def _show_preview(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "en")

    exp_label = t("yes_btn", lang) if data.get("has_experience") else t("no_btn", lang)
    preview = (
        t("preview_title", lang) + "\n\n"
        f"👤 <b>Full Name:</b> {data.get('full_name','—')}\n"
        f"📞 <b>Phone:</b> {data.get('phone','—')}\n"
        f"💱 <b>Currency:</b> {data.get('currency','—')}\n"
        f"📱 <b>Experience:</b> {exp_label}\n"
        f"🏠 <b>Street:</b> {data.get('street','—')}\n"
        f"💳 <b>Top-up:</b> {TOPUP_LABELS.get(data.get('topup_method',''), data.get('topup_method','—'))}\n"
        f"🎮 <b>Gaming ID:</b> {data.get('gaming_id','—')}\n"
        f"🌍 <b>Location:</b> {data.get('lat',0):.4f}, {data.get('lon',0):.4f}\n\n"
        f"📝 <b>About You:</b>\n{data.get('about_agent','—')}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("send_btn",    lang), callback_data="preview_send"),
        InlineKeyboardButton(text=t("restart_btn", lang), callback_data="preview_restart"),
    ]])
    await message.answer(preview, reply_markup=kb, parse_mode=ParseMode.HTML)
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
        username        = user.username,
        first_name      = user.first_name,
        last_name       = user.last_name,
        language        = lang,
        full_name       = data["full_name"],
        phone           = data["phone"],
        lat             = data["lat"],
        lon             = data["lon"],
        currency        = data["currency"],
        local_currency  = data.get("local_currency", "USD"),
        photo1_file_id  = data["photo1_file_id"],
        photo2_file_id  = data["photo2_file_id"],
        has_experience  = data["has_experience"],
        street          = data["street"],
        street_photo_file_id = data.get("street_photo_file_id"),
        topup_method    = data["topup_method"],
        gaming_id       = data["gaming_id"],
        about_agent     = data["about_agent"],
        status          = "pending",
        registered_at   = now,
    )
    logger.info(f"New registration: user_id={user.id}, gaming_id={data['gaming_id']}")

    await call.message.answer(
        t("submission_ok", lang),
        reply_markup=status_keyboard(lang),
        parse_mode=ParseMode.HTML,
    )
    await state.clear()

    # ── Notify admins ─────────────────────────────────────────────────────────
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Mark Ongoing",    callback_data=f"admin_ongoing_{user.id}"),
        InlineKeyboardButton(text="❌ Reject",          callback_data=f"admin_reject_{user.id}"),
    ]])
    admin_text = (
        f"🔔 <b>New Agent Registration</b>\n\n"
        f"👤 Name: {data['full_name']}\n"
        f"🆔 User ID: <code>{user.id}</code>\n"
        f"📛 Username: @{user.username or 'N/A'}\n"
        f"📞 Phone: {data['phone']}\n"
        f"💱 Currency: {data['currency']}\n"
        f"🌍 Location: {data['lat']:.4f}, {data['lon']:.4f}\n"
        f"🏠 Street: {data['street']}\n"
        f"💳 Top-up: {TOPUP_LABELS.get(data['topup_method'], data['topup_method'])}\n"
        f"🎮 Gaming ID: {data['gaming_id']}\n"
        f"📱 Experience: {'Yes ✅' if data['has_experience'] else 'No ❌'}\n"
        f"🌐 Language: {LANGS.get(lang, lang)}\n\n"
        f"📝 <b>About Agent:</b>\n{data['about_agent']}\n\n"
        f"<i>/viewdocs {user.id} to see ID photos + street photo</i>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=admin_kb, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Admin notify failed {admin_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: inline "Mark Ongoing"
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("admin_ongoing_"))
async def cb_admin_ongoing(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status='ongoing', ongoing_at=$1, ongoing_by=$2 "
            "WHERE user_id=$3 AND status='pending'",
            datetime.now(timezone.utc), call.from_user.id, target_id,
        )
    if result == "UPDATE 0":
        await call.answer("User not found or already processed.", show_alert=True)
        return
    await call.answer("✅ Marked as Ongoing!")
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Final Approve", callback_data=f"admin_approve_{target_id}"),
        InlineKeyboardButton(text="❌ Reject",        callback_data=f"admin_reject_{target_id}"),
    ]]))
    await call.message.reply(
        f"🔄 User <code>{target_id}</code> is now <b>ongoing/active</b>.",
        parse_mode=ParseMode.HTML,
    )
    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"
    try:
        await bot.send_message(
            target_id,
            t("ongoing_msg", lang),
            reply_markup=status_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: inline "Final Approve"
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("admin_approve_"))
async def cb_admin_approve(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Not authorised.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status='approved', final_approved_at=$1, final_approved_by=$2 "
            "WHERE user_id=$3 AND status='ongoing'",
            datetime.now(timezone.utc), call.from_user.id, target_id,
        )
    if result == "UPDATE 0":
        await call.answer("User not found or not in ongoing state.", show_alert=True)
        return
    await call.answer("✅ Final Approved!")
    await call.message.edit_reply_markup()
    await call.message.reply(
        f"✅ User <code>{target_id}</code> has been <b>finally approved</b>.",
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
# Admin: inline "Reject"
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("admin_reject_"))
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
# Request Status button
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text.in_({
    t("status_btn", "en"), t("status_btn", "ru"), t("status_btn", "bn"),
    t("status_btn", "hi"), t("status_btn", "ur"), t("status_btn", "ar"),
}))
async def request_status(message: Message):
    doc = await get_user(message.from_user.id)
    if not doc:
        await message.answer("You have not registered yet. Use /start to begin.")
        return
    lang   = doc.get("language", "en")
    status = doc.get("status", "pending")
    if status == "pending":
        reg_time = doc.get("registered_at")
        time_str = reg_time.strftime("%Y-%m-%d %H:%M UTC") if reg_time else "Unknown"
        await message.answer(
            f"⏳ <b>Status: Under Review</b>\n\nSubmitted: <b>{time_str}</b>.\nWe'll notify you once reviewed.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "ongoing":
        ongoing_time = doc.get("ongoing_at")
        time_str = ongoing_time.strftime("%Y-%m-%d %H:%M UTC") if ongoing_time else "Unknown"
        await message.answer(
            f"🔄 <b>Status: Ongoing / Active</b>\n\nYour application is active since <b>{time_str}</b>.\nAwaiting final approval.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "approved":
        approved_time = doc.get("final_approved_at") or doc.get("approved_at")
        time_str = approved_time.strftime("%Y-%m-%d %H:%M UTC") if approved_time else "Unknown"
        await message.answer(
            f"✅ <b>Status: Fully Approved</b>\n\nYou are a registered Mobicash agent since <b>{time_str}</b>.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "rejected":
        reason = doc.get("rejection_reason") or "No reason provided."
        await message.answer(
            f"❌ <b>Status: Rejected</b>\n\nReason: {reason}\n\nSend /restart to re-apply.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(f"Status: <b>{status}</b>", parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /viewdocs — send ALL photos (ID 1, ID 2, Street photo)
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

    await message.answer(
        f"📂 <b>Documents for user {target_id}</b>\n"
        f"👤 {doc.get('full_name','N/A')} | @{doc.get('username') or 'N/A'}\n"
        f"📝 <b>About:</b> {doc.get('about_agent','N/A')}",
        parse_mode=ParseMode.HTML,
    )
    for file_id, caption in [
        (doc.get("photo1_file_id"), "📄 Identity Document — First Photo"),
        (doc.get("photo2_file_id"), "📄 Identity Document — Second Photo"),
        (doc.get("street_photo_file_id"), "🏠 Street / Area Photo"),
    ]:
        if file_id:
            try:
                await bot.send_photo(message.chat.id, file_id, caption=caption)
            except Exception as e:
                await message.answer(f"Could not send '{caption}': {e}")
        else:
            await message.answer(f"⚠️ No file for: {caption}")


# ══════════════════════════════════════════════════════════════════════════════
# Admin: /listpending, /listall, /stats, /approve, /reject
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("listpending"))
async def cmd_listpending(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username, full_name, registered_at FROM users "
            "WHERE status='pending' ORDER BY registered_at ASC"
        )
    if not rows:
        await message.answer("No pending registrations. 🎉")
        return
    lines = []
    for r in rows:
        date = r["registered_at"].strftime("%m-%d %H:%M") if r["registered_at"] else "?"
        lines.append(f"• <code>{r['user_id']}</code> | @{r['username'] or 'N/A'} | {r['full_name'] or ''} | {date}")
    await message.answer(
        f"<b>Pending ({len(lines)}):</b>\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("listongoing"))
async def cmd_listongoing(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username, full_name, ongoing_at FROM users "
            "WHERE status='ongoing' ORDER BY ongoing_at ASC"
        )
    if not rows:
        await message.answer("No ongoing registrations.")
        return
    lines = []
    for r in rows:
        date = r["ongoing_at"].strftime("%m-%d %H:%M") if r["ongoing_at"] else "?"
        lines.append(f"• <code>{r['user_id']}</code> | @{r['username'] or 'N/A'} | {r['full_name'] or ''} | {date}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"✅ Approve #{r['user_id']}", callback_data=f"admin_approve_{r['user_id']}"),
            InlineKeyboardButton(text=f"❌ Reject #{r['user_id']}",  callback_data=f"admin_reject_{r['user_id']}"),
        ]
        for r in rows
    ])
    await message.answer(
        f"<b>Ongoing ({len(lines)}):</b>\n\n" + "\n".join(lines),
        reply_markup=kb,
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
            f"SELECT user_id, username, full_name, status FROM users {where} ORDER BY registered_at DESC LIMIT 50",
            *params,
        )
    if not rows:
        await message.answer("No users found.")
        return
    emoji_map = {"pending":"⏳","approved":"✅","ongoing":"🔄","rejected":"❌"}
    lines = [
        f"{emoji_map.get(r['status'],'❓')} <code>{r['user_id']}</code> | @{r['username'] or 'N/A'} | {r['full_name'] or ''} | {r['status']}"
        for r in rows
    ]
    await message.answer(f"<b>Users (last 50):</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with db_pool.acquire() as conn:
        total    = await conn.fetchval("SELECT COUNT(*) FROM users")
        pending  = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='pending'")
        ongoing  = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='ongoing'")
        approved = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='approved'")
        rejected = await conn.fetchval("SELECT COUNT(*) FROM users WHERE status='rejected'")
    await message.answer(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total: <b>{total}</b>\n"
        f"⏳ Pending: <b>{pending}</b>\n"
        f"🔄 Ongoing: <b>{ongoing}</b>\n"
        f"✅ Approved: <b>{approved}</b>\n"
        f"❌ Rejected: <b>{rejected}</b>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("approve"))
async def cmd_approve(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /approve <user_id>  (user must be in 'ongoing' status)")
        return
    target_id = int(parts[1])
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status='approved', final_approved_at=$1, final_approved_by=$2 "
            "WHERE user_id=$3 AND status='ongoing'",
            datetime.now(timezone.utc), message.from_user.id, target_id,
        )
    if result == "UPDATE 0":
        await message.answer("User not found or not in ongoing state. Use /ongoing <user_id> first.")
        return
    await message.answer(f"✅ User <code>{target_id}</code> finally approved.", parse_mode=ParseMode.HTML)
    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"
    try:
        await bot.send_message(target_id, t("final_approved_msg", lang), reply_markup=status_keyboard(lang), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


@router.message(Command("ongoing"))
async def cmd_ongoing(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /ongoing <user_id>")
        return
    target_id = int(parts[1])
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET status='ongoing', ongoing_at=$1, ongoing_by=$2 "
            "WHERE user_id=$3 AND status='pending'",
            datetime.now(timezone.utc), message.from_user.id, target_id,
        )
    if result == "UPDATE 0":
        await message.answer("User not found or not in pending state.")
        return
    await message.answer(f"🔄 User <code>{target_id}</code> moved to ongoing.", parse_mode=ParseMode.HTML)
    doc = await get_user(target_id)
    lang = doc.get("language", "en") if doc else "en"
    try:
        await bot.send_message(target_id, t("ongoing_msg", lang), reply_markup=status_keyboard(lang), parse_mode=ParseMode.HTML)
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
    if doc.get("status") not in ("pending", "ongoing"):
        await message.answer(f"User status is already: <b>{doc['status']}</b>", parse_mode=ParseMode.HTML)
        return
    await state.set_state(AdminReject.waiting_reason)
    await state.update_data(reject_target_id=target_id)
    await message.answer(
        f"Please send the <b>rejection reason</b> for user <code>{target_id}</code>.",
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

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET status='rejected', rejection_reason=$1, rejected_at=$2, rejected_by=$3 WHERE user_id=$4",
            reason_text, datetime.now(timezone.utc), message.from_user.id, target_id,
        )
    await state.clear()
    await message.answer(f"✅ User <code>{target_id}</code> has been rejected.", parse_mode=ParseMode.HTML)
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
        f"📢 Send message to broadcast to <b>{count} approved agents</b>. Send /cancel_broadcast to abort.",
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
            f"👤 {user.full_name} | ID: <code>{user.id}</code> | @{user.username or 'N/A'}"
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
        "/start — Start registration\n"
        "/restart — Re-apply (if rejected)\n"
        "/cancel — Cancel ongoing registration\n"
        "/help — Show this help message\n\n"
        "📋 Use the <b>Request Status</b> button to check your application."
    )
    admin_help = (
        "\n\n<b>Admin commands:</b>\n\n"
        "/listpending — Pending applications\n"
        "/listongoing — Ongoing applications (with approve/reject buttons)\n"
        "/listall [status] — All users\n"
        "/stats — Statistics\n"
        "/ongoing &lt;user_id&gt; — Move pending → ongoing\n"
        "/approve &lt;user_id&gt; — Final approve (ongoing → approved)\n"
        "/reject &lt;user_id&gt; — Reject with reason\n"
        "/viewdocs &lt;user_id&gt; — View all photos (ID front, ID back, street)\n"
        "/broadcast — Send message to all approved agents\n"
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
