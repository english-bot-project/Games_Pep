# -*- coding: utf-8 -*-
"""
Ko'p funksiyali Telegram bot (python-telegram-bot v20+)

Imkoniyatlar:
  * 4 til: O'zbek (lotin), Ўзбек (кирилл), Русский, English
  * 6 ta o'yin: son topish, dorboz, X-O (bot bilan), viktorina, matematika, tosh-qaychi-qog'oz
  * Profil (har bir o'yin bali alohida + jami), ballar bazada saqlanadi
  * QR kod yaratish
  * Takrorlanmas parol generatori (10 / 15 / 20 belgi)
  * Eslatmalar ("30 minutdan keyin eslat")

Ishga tushirish:
  pip install "python-telegram-bot[job-queue]" "qrcode[pil]"
  BOT_TOKEN ni pastga qo'ying yoki muhit o'zgaruvchisi sifatida bering, so'ng:  python bot.py
"""
import hashlib
import html
import io
import logging
import os
import random
import re
import secrets
import sqlite3
import string
import time

import qrcode
from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup as Markup
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ====================== SOZLAMALAR ======================
TOKEN = os.getenv("BOT_TOKEN", "8921535690:AAHQeuikVdZDlHeoBZo297jssgDxnXXoM6w")  # <-- BotFather bergan token
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")

MAX_TRIES = 8
GUESS_PTS = {1: 10, 2: 9, 3: 8, 4: 7, 5: 5, 6: 4, 7: 2, 8: 1}  # nechanchi urinishda topsa necha ball
HANG_LIVES = 6
QUIZ_N = 10

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

# ====================== BAZA ======================
db = sqlite3.connect(DB_PATH)
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, lang TEXT DEFAULT 'uz');
CREATE TABLE IF NOT EXISTS scores(user_id INTEGER, game TEXT, points INTEGER DEFAULT 0,
                                  PRIMARY KEY(user_id, game));
CREATE TABLE IF NOT EXISTS passwords(hash TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER,
                                     lang TEXT, due REAL, text TEXT, done INTEGER DEFAULT 0);
"""
)
db.commit()


def get_lang(uid):
    row = db.execute("SELECT lang FROM users WHERE user_id=?", (uid,)).fetchone()
    return row[0] if row else "uz"


def set_lang(uid, lang):
    db.execute(
        "INSERT INTO users(user_id, lang) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang",
        (uid, lang),
    )
    db.commit()


def add_points(uid, game, pts):
    db.execute(
        "INSERT INTO scores(user_id, game, points) VALUES(?,?,?) "
        "ON CONFLICT(user_id, game) DO UPDATE SET points = points + excluded.points",
        (uid, game, pts),
    )
    db.commit()


def get_scores(uid):
    return dict(db.execute("SELECT game, points FROM scores WHERE user_id=?", (uid,)).fetchall())


# ====================== LOTIN -> KIRILL (o'zbekcha) ======================
_CYR = {
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ", "i": "и", "j": "ж",
    "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "қ", "r": "р", "s": "с",
    "t": "т", "u": "у", "v": "в", "x": "х", "y": "й", "z": "з",
    "o'": "ў", "g'": "ғ", "sh": "ш", "ch": "ч", "yo": "ё", "yu": "ю", "ya": "я", "ye": "е", "'": "ъ",
}
_PAT = re.compile(
    r"(\{[^}]*\}|QR|Telegram|MCMXC)|(o'|g'|sh|ch|yo(?!')|yu|ya|ye|[a-z]|')", re.IGNORECASE
)


def lat2cyr(s):
    def sub(m):
        if m.group(1):
            return m.group(1)
        tok = m.group(2)
        low = tok.lower()
        res = _CYR.get(low, tok)
        if low == "e":
            prev = m.string[m.start() - 1] if m.start() > 0 else " "
            if not prev.isalpha() and prev != "'":
                res = "э"
        return res.upper() if tok[0].isupper() else res

    return _PAT.sub(sub, s)


# ====================== MATNLAR (uz, ru, en) ======================
LANG_IDX = {"uz": 0, "uc": 0, "ru": 1, "en": 2}

T = {
    "pick_lang": ("🌐 Tilni tanlang / Выберите язык / Choose a language:",) * 3,
    "lang_ok": ("✅ Til o'rnatildi.", "✅ Язык установлен.", "✅ Language set."),
    "menu": (
        "🏠 Asosiy menyu. Kerakli bo'limni tanlang:",
        "🏠 Главное меню. Выберите раздел:",
        "🏠 Main menu. Choose a section:",
    ),
    "btn_games": ("🎮 O'yinlar", "🎮 Игры", "🎮 Games"),
    "btn_profile": ("👤 Profil", "👤 Профиль", "👤 Profile"),
    "btn_qr": ("🔳 QR kod", "🔳 QR-код", "🔳 QR code"),
    "btn_pass": ("🔑 Parol yaratish", "🔑 Генератор паролей", "🔑 Password generator"),
    "btn_remind": ("⏰ Eslatma", "⏰ Напоминание", "⏰ Reminder"),
    "btn_lang": ("🌐 Til", "🌐 Язык", "🌐 Language"),
    "btn_menu": ("⬅️ Menyu", "⬅️ Меню", "⬅️ Menu"),
    "btn_again": ("🔄 Yana o'ynash", "🔄 Играть ещё", "🔄 Play again"),
    "games_title": ("🎮 O'yinni tanlang:", "🎮 Выберите игру:", "🎮 Choose a game:"),
    "g_guess": ("🔢 Son topish", "🔢 Угадай число", "🔢 Guess the number"),
    "g_hang": ("🔤 Dorboz (so'z topish)", "🔤 Виселица", "🔤 Hangman"),
    "g_ttt": ("❌⭕ X va O (bot bilan)", "❌⭕ Крестики-нолики", "❌⭕ Tic-tac-toe"),
    "g_quiz": ("🧠 Viktorina", "🧠 Викторина", "🧠 Quiz"),
    "g_math": ("➗ Matematika", "➗ Математика", "➗ Math"),
    "g_rps": ("✊ Tosh-qaychi-qog'oz", "✊ Камень-ножницы-бумага", "✊ Rock-paper-scissors"),
    "pts": ("ball", "балл.", "pts"),
    # --- son topish
    "guess_start": (
        "🔢 Men 1 dan 100 gacha son o'yladim. Topish uchun {n} ta urinish bor.\nSonni yozing:",
        "🔢 Я загадал число от 1 до 100. У вас {n} попыток.\nНапишите число:",
        "🔢 I'm thinking of a number from 1 to 100. You have {n} tries.\nType your guess:",
    ),
    "guess_bad": (
        "❗ 1 dan 100 gacha butun son yozing.",
        "❗ Напишите целое число от 1 до 100.",
        "❗ Type a whole number from 1 to 100.",
    ),
    "guess_low": (
        "📈 Men o'ylagan son kattaroq. Qolgan urinish: {left}",
        "📈 Загаданное число больше. Осталось попыток: {left}",
        "📈 My number is higher. Tries left: {left}",
    ),
    "guess_high": (
        "📉 Men o'ylagan son kichikroq. Qolgan urinish: {left}",
        "📉 Загаданное число меньше. Осталось попыток: {left}",
        "📉 My number is lower. Tries left: {left}",
    ),
    "guess_win": (
        "🎉 Topdingiz! Son: {num}. Urinish: {tries}.\n🏅 Ball: +{pts}",
        "🎉 Угадали! Число: {num}. Попыток: {tries}.\n🏅 Баллы: +{pts}",
        "🎉 You got it! Number: {num}. Tries: {tries}.\n🏅 Points: +{pts}",
    ),
    "guess_lose": (
        "😔 Urinishlar tugadi. Men o'ylagan son: {num}",
        "😔 Попытки закончились. Я загадал: {num}",
        "😔 Out of tries. My number was: {num}",
    ),
    # --- dorboz
    "hang_start": (
        "🔤 So'zni toping! Harf yoki butun so'z yozing.\n\n{word}\n❤️ Jon: {lives}",
        "🔤 Угадайте слово! Пишите букву или слово целиком.\n\n{word}\n❤️ Жизни: {lives}",
        "🔤 Guess the word! Type a letter or the whole word.\n\n{word}\n❤️ Lives: {lives}",
    ),
    "hang_state": (
        "{word}\n❤️ Jon: {lives}\n❌ Xato: {wrong}",
        "{word}\n❤️ Жизни: {lives}\n❌ Ошибки: {wrong}",
        "{word}\n❤️ Lives: {lives}\n❌ Wrong: {wrong}",
    ),
    "hang_win": (
        "🎉 Topdingiz! So'z: {w}\n🏅 Ball: +{pts}",
        "🎉 Угадали! Слово: {w}\n🏅 Баллы: +{pts}",
        "🎉 You won! The word: {w}\n🏅 Points: +{pts}",
    ),
    "hang_lose": (
        "💀 Yutqazdingiz. So'z: {w}",
        "💀 Вы проиграли. Слово: {w}",
        "💀 You lost. The word: {w}",
    ),
    "hang_repeat": (
        "ℹ️ Bu harf allaqachon aytilgan.",
        "ℹ️ Эта буква уже была.",
        "ℹ️ You already tried this letter.",
    ),
    "hang_bad": ("❗ Faqat harf yozing.", "❗ Пишите только буквы.", "❗ Letters only, please."),
    # --- X-O
    "ttt_start": (
        "❌⭕ Siz — ❌, bot — ⭕. Yurishingizni qiling:",
        "❌⭕ Вы — ❌, бот — ⭕. Ваш ход:",
        "❌⭕ You are ❌, the bot is ⭕. Your move:",
    ),
    "ttt_win": ("🎉 Siz yutdingiz! +5 ball", "🎉 Вы победили! +5 баллов", "🎉 You won! +5 points"),
    "ttt_lose": ("🤖 Bot yutdi. -5 ball", "🤖 Победил бот. -5 баллов", "🤖 The bot won. -5 points"),
    "ttt_draw": ("🤝 Durang. 0 ball", "🤝 Ничья. 0 баллов", "🤝 Draw. 0 points"),
    "ttt_busy": ("Bu katak band!", "Эта клетка занята!", "That cell is taken!"),
    # --- viktorina
    "quiz_q": (
        "🧠 Savol {i}/{n}\n\n{q}",
        "🧠 Вопрос {i}/{n}\n\n{q}",
        "🧠 Question {i}/{n}\n\n{q}",
    ),
    "quiz_right": ("✅ To'g'ri!", "✅ Верно!", "✅ Correct!"),
    "quiz_wrong": (
        "❌ Noto'g'ri. To'g'ri javob: {a}",
        "❌ Неверно. Правильный ответ: {a}",
        "❌ Wrong. Correct answer: {a}",
    ),
    "quiz_end": (
        "🏁 Viktorina tugadi!\n✅ To'g'ri javoblar: {c}/{n}\n🏅 Ball: +{pts}",
        "🏁 Викторина окончена!\n✅ Правильных ответов: {c}/{n}\n🏅 Баллы: +{pts}",
        "🏁 Quiz finished!\n✅ Correct answers: {c}/{n}\n🏅 Points: +{pts}",
    ),
    # --- matematika
    "math_start": (
        "➗ Misolni tez yeching! Qanchalik tez bo'lsa, shuncha ko'p ball.\nXato javob o'yinni tugatadi.",
        "➗ Решайте примеры быстро! Чем быстрее, тем больше баллов.\nОшибка завершает игру.",
        "➗ Solve fast! The quicker you answer, the more points.\nA wrong answer ends the game.",
    ),
    "math_right": (
        "✅ To'g'ri! {t} soniya → +{pts} ball",
        "✅ Верно! {t} сек. → +{pts} баллов",
        "✅ Correct! {t} sec → +{pts} points",
    ),
    "math_wrong": (
        "❌ Noto'g'ri. To'g'ri javob: {a}.\nO'yin tugadi. Shu o'yindagi ball: {s}",
        "❌ Неверно. Правильный ответ: {a}.\nИгра окончена. Баллы за игру: {s}",
        "❌ Wrong. Correct answer: {a}.\nGame over. Points this game: {s}",
    ),
    "math_num": ("❗ Son yozing.", "❗ Напишите число.", "❗ Type a number."),
    # --- tosh-qaychi-qog'oz
    "rps_start": (
        "✊ Tanlang! (cheksiz o'ynash mumkin)",
        "✊ Выбирайте! (играть можно бесконечно)",
        "✊ Pick one! (play as long as you like)",
    ),
    "rps_res": (
        "Raund {r}\nSiz: {me}   Bot: {bot}\n{res}\n\nShu o'yindagi ball: {s}",
        "Раунд {r}\nВы: {me}   Бот: {bot}\n{res}\n\nБаллы за игру: {s}",
        "Round {r}\nYou: {me}   Bot: {bot}\n{res}\n\nPoints this game: {s}",
    ),
    "rps_win": ("🎉 Siz yutdingiz! +1", "🎉 Вы выиграли! +1", "🎉 You win! +1"),
    "rps_lose": ("😅 Bot yutdi", "😅 Выиграл бот", "😅 The bot wins"),
    "rps_draw": ("🤝 Durang", "🤝 Ничья", "🤝 Draw"),
    # --- profil
    "profile": (
        "👤 Profil\n\nIsm: {name}\n\n{lines}\n\n🏆 Jami ball: {total}",
        "👤 Профиль\n\nИмя: {name}\n\n{lines}\n\n🏆 Всего баллов: {total}",
        "👤 Profile\n\nName: {name}\n\n{lines}\n\n🏆 Total points: {total}",
    ),
    # --- QR
    "qr_ask": (
        "🔳 Matn, xabar yoki havola yuboring — men uni QR kodga aylantiraman.\nChiqish uchun «Menyu» tugmasini bosing.",
        "🔳 Отправьте текст, сообщение или ссылку — я превращу это в QR-код.\nДля выхода нажмите «Меню».",
        "🔳 Send a text, message or link and I'll turn it into a QR code.\nPress “Menu” to leave.",
    ),
    "qr_photo": (
        "🖼 Rasmning o'zini QR kodga sig'dirib bo'lmaydi. Rasmni biror joyga (Google Drive, telegra.ph va h.k.) yuklab, havolasini yuboring. Rasmga izoh yozsangiz, izohdan QR yarataman.",
        "🖼 Саму картинку в QR-код не поместить. Загрузите её куда-нибудь (Google Drive, telegra.ph и т.п.) и отправьте ссылку. Если у фото есть подпись, я сделаю QR из подписи.",
        "🖼 A picture itself can't fit into a QR code. Upload it somewhere (Google Drive, telegra.ph, etc.) and send me the link. If the photo has a caption, I'll use the caption.",
    ),
    "qr_long": (
        "❗ Matn juda uzun, QR kodga sig'maydi.",
        "❗ Текст слишком длинный для QR-кода.",
        "❗ The text is too long for a QR code.",
    ),
    "qr_done": ("✅ QR kod tayyor! Yana yuborishingiz mumkin.", "✅ QR-код готов! Можно отправить ещё.", "✅ Your QR code is ready! Send more if you like."),
    # --- parol
    "pass_ask": ("🔑 Parol uzunligini tanlang:", "🔑 Выберите длину пароля:", "🔑 Choose the password length:"),
    "pass_done": (
        "🔑 Parolingiz ({n} belgi):\n\n{p}\n\nNusxalash uchun parol ustiga bosing. Bu parol hech kimga qayta berilmaydi.",
        "🔑 Ваш пароль ({n} симв.):\n\n{p}\n\nНажмите на пароль, чтобы скопировать. Такой пароль больше никому не выдаётся.",
        "🔑 Your password ({n} chars):\n\n{p}\n\nTap the password to copy it. It will never be issued to anyone else.",
    ),
    # --- eslatma
    "remind_ask": (
        "⏰ Vaqt va matnni yozing. Masalan:\n• 1 minutdan keyin tanaffus qilishimni eslat\n• 30 minutdan keyin eslat\n• 1 soatdan keyin dori ichishni eslat",
        "⏰ Напишите время и текст. Например:\n• через 1 минуту напомни сделать перерыв\n• через 30 минут напомни\n• через 1 час напомни выпить лекарство",
        "⏰ Write the time and the text. For example:\n• remind me to take a break in 1 minute\n• remind me in 30 minutes\n• remind me to take medicine in 1 hour",
    ),
    "remind_set": (
        "✅ Eslatma o'rnatildi!\n⏳ Kutish vaqti: {when}\n📝 {text}",
        "✅ Напоминание установлено!\n⏳ Через: {when}\n📝 {text}",
        "✅ Reminder set!\n⏳ In: {when}\n📝 {text}",
    ),
    "remind_fire": ("⏰ Eslatma: {text}", "⏰ Напоминание: {text}", "⏰ Reminder: {text}"),
    "remind_default": ("Vaqt keldi!", "Время пришло!", "Time's up!"),
    "remind_bad": (
        "❗ Vaqtni tushunmadim. Masalan: «5 minutdan keyin eslat».",
        "❗ Не понял время. Например: «через 5 минут напомни».",
        "❗ I didn't get the time. Example: “remind me in 5 minutes”.",
    ),
    "remind_far": (
        "❗ Eng ko'pi bilan 30 kunga eslatma qo'yish mumkin.",
        "❗ Максимум — напоминание на 30 дней.",
        "❗ The maximum is 30 days.",
    ),
    "u_s": ("soniya", "сек.", "sec"),
    "u_m": ("daqiqa", "мин.", "min"),
    "u_h": ("soat", "ч.", "h"),
    "use_menu": ("Menyudan tanlang 👇", "Выберите в меню 👇", "Pick from the menu 👇"),
}


def t(lang, key, **kw):
    entry = T[key]
    s = lat2cyr(entry[0]) if lang == "uc" else entry[LANG_IDX[lang]]
    return s.format(**kw) if kw else s.replace("{{", "{").replace("}}", "}")


# ====================== SO'ZLAR (dorboz) ======================
_UZ_WORDS = [
    "olma", "maktab", "kitob", "daryo", "bahor", "qalam", "kompyuter", "telefon", "samolyot",
    "mashina", "bozor", "shahar", "quyosh", "yulduz", "televizor", "dasturchi",
]
WORDS = {
    "uz": _UZ_WORDS,
    "uc": [lat2cyr(w) for w in _UZ_WORDS],
    "ru": [
        "яблоко", "школа", "книга", "река", "весна", "ручка", "компьютер", "телефон", "самолет",
        "машина", "рынок", "город", "солнце", "звезда", "телевизор", "программист",
    ],
    "en": [
        "apple", "school", "book", "river", "spring", "pencil", "computer", "phone", "airplane",
        "machine", "market", "city", "sun", "star", "television", "programmer",
    ],
}

# ====================== VIKTORINA SAVOLLARI ======================
# (uz, ru, en, [variantlar], to'g'ri javob indeksi)
QUIZ = [
    ("Amir Temur qaysi yilda tug'ilgan?", "В каком году родился Амир Темур?", "In what year was Amir Temur born?",
     ["1326", "1336", "1346", "1356"], 1),
    ("Mirzo Ulug'bek rasadxonasi qaysi shaharda joylashgan?", "В каком городе находится обсерватория Улугбека?",
     "In which city is Ulugh Beg's observatory located?",
     [("Buxoro", "Бухара", "Bukhara"), ("Samarqand", "Самарканд", "Samarkand"), ("Xiva", "Хива", "Khiva"),
      ("Toshkent", "Ташкент", "Tashkent")], 1),
    ("π soni 3 ta kasr xonasigacha yaxlitlansa nimaga teng?", "Чему равно π, округлённое до 3 знаков после запятой?",
     "What is π rounded to 3 decimal places?", ["3.141", "3.142", "3.143", "3.140"], 1),
    ("Suv normal bosimda necha kelvinda qaynaydi?", "При скольких кельвинах кипит вода при нормальном давлении?",
     "At how many kelvins does water boil at normal pressure?", ["273", "323", "373", "473"], 2),
    ("Sog'lom odamda nechta xromosoma bor?", "Сколько хромосом у здорового человека?",
     "How many chromosomes does a healthy human have?", ["44", "46", "48", "42"], 1),
    ("Yorug'lik vakuumda taxminan qanday tezlikda tarqaladi (km/s)?",
     "С какой скоростью примерно распространяется свет в вакууме (км/с)?",
     "Approximately how fast does light travel in a vacuum (km/s)?", ["30 000", "150 000", "300 000", "3 000 000"], 2),
    ("Mendeleev jadvalida 26-element belgisi qaysi?", "Какой символ у элемента №26 в таблице Менделеева?",
     "What is the symbol of element number 26?", ["Fe", "Co", "Ni", "Mn"], 0),
    ("Yuriy Gagarin koinotga qaysi yili uchgan?", "В каком году Юрий Гагарин полетел в космос?",
     "In which year did Yuri Gagarin fly into space?", ["1957", "1961", "1965", "1969"], 1),
    ("Ketma-ketlikni davom ettiring: 1, 1, 2, 3, 5, 8, ?", "Продолжите ряд: 1, 1, 2, 3, 5, 8, ?",
     "Continue the sequence: 1, 1, 2, 3, 5, 8, ?", ["11", "12", "13", "15"], 2),
    ("2 ning 10-darajasi nechaga teng?", "Чему равно 2 в степени 10?", "What is 2 to the power of 10?",
     ["512", "1000", "1024", "2048"], 2),
    ("Ikkinchi jahon urushi qaysi yilda boshlangan?", "В каком году началась Вторая мировая война?",
     "In which year did World War II begin?", ["1936", "1939", "1941", "1914"], 1),
    ("Alisher Navoiy qaysi yilda tug'ilgan?", "В каком году родился Алишер Навои?",
     "In which year was Alisher Navoi born?", ["1401", "1441", "1461", "1501"], 1),
    ("Avogadro doimiysi taxminan nimaga teng?", "Чему примерно равна постоянная Авогадро?",
     "What is Avogadro's constant approximately?", ["6.02×10²³", "3.01×10²³", "6.02×10²¹", "9.81×10²³"], 0),
    ("100 gacha nechta tub son bor?", "Сколько простых чисел до 100?",
     "How many prime numbers are there below 100?", ["21", "23", "25", "27"], 2),
    ("Katta yoshli odamda nechta suyak bor?", "Сколько костей у взрослого человека?",
     "How many bones does an adult human have?", ["196", "206", "216", "306"], 1),
    ("O'zbekiston qaysi yilda mustaqillikka erishgan?", "В каком году Узбекистан обрёл независимость?",
     "In which year did Uzbekistan gain independence?", ["1989", "1990", "1991", "1992"], 2),
    ("Yer yoshi taxminan necha milliard yil?", "Каков примерный возраст Земли (млрд лет)?",
     "What is the approximate age of the Earth (billion years)?", ["2.5", "4.5", "6.5", "10"], 1),
    ("MCMXC rim raqami nechaga teng?", "Чему равно римское число MCMXC?", "What is the Roman numeral MCMXC?",
     ["1890", "1980", "1990", "2090"], 2),
    ("Quyosh nuri Yerga taxminan necha daqiqada yetib keladi?", "За сколько минут примерно свет Солнца доходит до Земли?",
     "About how many minutes does sunlight take to reach Earth?", ["3", "8", "15", "30"], 1),
    ("Ikkilik 1010 soni o'nlik sanoq tizimida nechaga teng?", "Чему равно двоичное число 1010 в десятичной системе?",
     "What is the binary number 1010 in decimal?", ["8", "10", "12", "14"], 1),
]


def _opt_text(o, lang):
    if isinstance(o, str):
        return o
    return lat2cyr(o[0]) if lang == "uc" else o[LANG_IDX[lang]]


# ====================== YORDAMCHI FUNKSIYALAR ======================
GAME_KEYS = ["guess", "hang", "ttt", "quiz", "math", "rps"]
STATE_KEYS = ("state", "guess", "hang", "ttt", "quiz", "math", "rps")


def reset(ctx):
    for k in STATE_KEYS:
        ctx.user_data.pop(k, None)


def back_row(lang):
    return [Btn(t(lang, "btn_menu"), callback_data="menu")]


def end_markup(lang, game):
    return Markup([[Btn(t(lang, "btn_again"), callback_data=f"g:{game}")], back_row(lang)])


def menu_markup(lang):
    return Markup(
        [
            [Btn(t(lang, "btn_games"), callback_data="games"), Btn(t(lang, "btn_profile"), callback_data="profile")],
            [Btn(t(lang, "btn_qr"), callback_data="qr"), Btn(t(lang, "btn_pass"), callback_data="pass")],
            [Btn(t(lang, "btn_remind"), callback_data="remind"), Btn(t(lang, "btn_lang"), callback_data="chlang")],
        ]
    )


def lang_markup():
    return Markup(
        [
            [Btn("🇺🇿 O'zbekcha", callback_data="lang:uz"), Btn("🇺🇿 Ўзбекча", callback_data="lang:uc")],
            [Btn("🇷🇺 Русский", callback_data="lang:ru"), Btn("🇬🇧 English", callback_data="lang:en")],
        ]
    )


async def render(update, ctx, text, markup=None, parse_mode=None):
    """Tugma bosilgan bo'lsa xabarni tahrirlaydi, aks holda yangi xabar yuboradi."""
    q = update.callback_query
    if q:
        try:
            await q.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        except Exception:
            pass
    await ctx.bot.send_message(update.effective_chat.id, text, reply_markup=markup, parse_mode=parse_mode)


async def show_menu(update, ctx, lang):
    reset(ctx)
    await render(update, ctx, t(lang, "menu"), menu_markup(lang))


# ====================== /start, /menu ======================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reset(ctx)
    await update.message.reply_text(t("uz", "pick_lang"), reply_markup=lang_markup())


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_menu(update, ctx, get_lang(update.effective_user.id))


# ====================== SON TOPISH ======================
async def start_guess(update, ctx, lang):
    reset(ctx)
    ctx.user_data["state"] = "guess"
    ctx.user_data["guess"] = {"num": random.randint(1, 100), "tries": 0}
    await render(update, ctx, t(lang, "guess_start", n=MAX_TRIES), Markup([back_row(lang)]))


async def text_guess(update, ctx, lang, text):
    uid = update.effective_user.id
    g = ctx.user_data["guess"]
    s = text.strip()
    if not re.fullmatch(r"\d{1,3}", s) or not 1 <= int(s) <= 100:
        await update.message.reply_text(t(lang, "guess_bad"))
        return
    n = int(s)
    g["tries"] += 1
    left = MAX_TRIES - g["tries"]
    if n == g["num"]:
        pts = GUESS_PTS[g["tries"]]
        add_points(uid, "guess", pts)
        msg = t(lang, "guess_win", num=g["num"], tries=g["tries"], pts=pts)
        reset(ctx)
        await update.message.reply_text(msg, reply_markup=end_markup(lang, "guess"))
    elif left <= 0:
        msg = t(lang, "guess_lose", num=g["num"])
        reset(ctx)
        await update.message.reply_text(msg, reply_markup=end_markup(lang, "guess"))
    else:
        key = "guess_low" if n < g["num"] else "guess_high"
        await update.message.reply_text(t(lang, key, left=left), reply_markup=Markup([back_row(lang)]))


# ====================== DORBOZ ======================
def _mask(word, guessed):
    return " ".join(c if c in guessed else "_" for c in word)


def _hang_text(lang, h):
    return t(lang, "hang_state", word=_mask(h["word"], h["guessed"]), lives=h["lives"],
             wrong=" ".join(h["wrong"]) or "—")


async def start_hang(update, ctx, lang):
    reset(ctx)
    word = random.choice(WORDS[lang])
    ctx.user_data["state"] = "hang"
    ctx.user_data["hang"] = {"word": word, "guessed": set(), "wrong": [], "lives": HANG_LIVES}
    await render(update, ctx, t(lang, "hang_start", word=_mask(word, set()), lives=HANG_LIVES),
                 Markup([back_row(lang)]))


async def text_hang(update, ctx, lang, text):
    uid = update.effective_user.id
    h = ctx.user_data["hang"]
    s = text.strip().lower()
    if not s.isalpha():
        await update.message.reply_text(t(lang, "hang_bad"))
        return
    if len(s) == 1:
        if s in h["guessed"] or s in h["wrong"]:
            await update.message.reply_text(t(lang, "hang_repeat"))
            return
        if s in h["word"]:
            h["guessed"].add(s)
        else:
            h["wrong"].append(s)
            h["lives"] -= 1
    else:
        if s == h["word"]:
            h["guessed"] = set(h["word"])
        else:
            h["wrong"].append(s)
            h["lives"] -= 1
    if all(c in h["guessed"] for c in h["word"]):
        pts = 3 + h["lives"]
        add_points(uid, "hang", pts)
        msg = t(lang, "hang_win", w=h["word"], pts=pts)
        reset(ctx)
        await update.message.reply_text(msg, reply_markup=end_markup(lang, "hang"))
    elif h["lives"] <= 0:
        msg = t(lang, "hang_lose", w=h["word"])
        reset(ctx)
        await update.message.reply_text(msg, reply_markup=end_markup(lang, "hang"))
    else:
        await update.message.reply_text(_hang_text(lang, h), reply_markup=Markup([back_row(lang)]))


# ====================== X VA O ======================
WINS = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def ttt_winner(b):
    for a, c, d in WINS:
        if b[a] != " " and b[a] == b[c] == b[d]:
            return b[a]
    return None


def ttt_bot_move(b):
    empty = [i for i, v in enumerate(b) if v == " "]
    for mark in ("O", "X"):  # avval yutish, keyin to'sish
        for i in empty:
            b[i] = mark
            w = ttt_winner(b)
            b[i] = " "
            if w == mark:
                return i
    if 4 in empty and random.random() < 0.7:
        return 4
    return random.choice(empty)


def ttt_markup(lang, b, done=False):
    sym = {"X": "❌", "O": "⭕", " ": "▫️"}
    rows = []
    for r in range(3):
        rows.append([
            Btn(sym[b[r * 3 + c]], callback_data="noop" if done else f"ttt:{r * 3 + c}") for c in range(3)
        ])
    if done:
        rows.append([Btn(t(lang, "btn_again"), callback_data="g:ttt")])
    rows.append(back_row(lang))
    return Markup(rows)


async def start_ttt(update, ctx, lang):
    reset(ctx)
    ctx.user_data["ttt"] = [" "] * 9
    await render(update, ctx, t(lang, "ttt_start"), ttt_markup(lang, ctx.user_data["ttt"]))


async def press_ttt(update, ctx, lang, i):
    q = update.callback_query
    uid = update.effective_user.id
    b = ctx.user_data.get("ttt")
    if b is None:
        await q.answer()
        return
    if b[i] != " ":
        await q.answer(t(lang, "ttt_busy"))
        return
    await q.answer()
    b[i] = "X"
    res = None
    if ttt_winner(b) == "X":
        res = "win"
    elif " " not in b:
        res = "draw"
    else:
        b[ttt_bot_move(b)] = "O"
        if ttt_winner(b) == "O":
            res = "lose"
        elif " " not in b:
            res = "draw"
    if res:
        add_points(uid, "ttt", {"win": 5, "lose": -5, "draw": 0}[res])
        ctx.user_data.pop("ttt", None)
        await render(update, ctx, t(lang, "ttt_" + res), ttt_markup(lang, b, done=True))
    else:
        await render(update, ctx, t(lang, "ttt_start"), ttt_markup(lang, b))


# ====================== VIKTORINA ======================
def _quiz_view(lang, quiz, prefix=""):
    item = quiz["items"][quiz["i"]]
    text = t(lang, "quiz_q", i=quiz["i"] + 1, n=len(quiz["items"]), q=item["q"])
    if prefix:
        text = prefix + "\n\n" + text
    rows = [[Btn(o, callback_data=f"qz:{k}")] for k, o in enumerate(item["opts"])]
    rows.append(back_row(lang))
    return text, Markup(rows)


async def start_quiz(update, ctx, lang):
    reset(ctx)
    items = []
    for qd in random.sample(QUIZ, QUIZ_N):
        q_text = lat2cyr(qd[0]) if lang == "uc" else qd[LANG_IDX[lang]]
        opts = [(_opt_text(o, lang), k == qd[4]) for k, o in enumerate(qd[3])]
        random.shuffle(opts)
        items.append({"q": q_text, "opts": [o[0] for o in opts], "ans": [o[1] for o in opts].index(True)})
    ctx.user_data["quiz"] = {"items": items, "i": 0, "correct": 0}
    text, markup = _quiz_view(lang, ctx.user_data["quiz"])
    await render(update, ctx, text, markup)


async def press_quiz(update, ctx, lang, k):
    uid = update.effective_user.id
    quiz = ctx.user_data.get("quiz")
    if quiz is None:
        return
    item = quiz["items"][quiz["i"]]
    if k == item["ans"]:
        quiz["correct"] += 1
        verdict = t(lang, "quiz_right")
    else:
        verdict = t(lang, "quiz_wrong", a=item["opts"][item["ans"]])
    quiz["i"] += 1
    if quiz["i"] >= len(quiz["items"]):
        pts = quiz["correct"]
        add_points(uid, "quiz", pts)
        text = verdict + "\n\n" + t(lang, "quiz_end", c=quiz["correct"], n=len(quiz["items"]), pts=pts)
        ctx.user_data.pop("quiz", None)
        await render(update, ctx, text, end_markup(lang, "quiz"))
    else:
        text, markup = _quiz_view(lang, quiz, prefix=verdict)
        await render(update, ctx, text, markup)


# ====================== MATEMATIKA ======================
def new_math():
    op = random.choice(["+", "-", "×", "÷"])
    if op == "+":
        a, b = random.randint(10, 99), random.randint(10, 99)
        ans = a + b
    elif op == "-":
        a = random.randint(20, 99)
        b = random.randint(10, a)
        ans = a - b
    elif op == "×":
        a, b = random.randint(2, 12), random.randint(2, 12)
        ans = a * b
    else:
        b = random.randint(2, 12)
        ans = random.randint(2, 12)
        a = b * ans
    return f"{a} {op} {b} = ?", ans


def math_pts(sec):
    for limit, pts in ((3, 8), (5, 6), (7, 4), (10, 2)):
        if sec <= limit:
            return pts
    return 1


async def start_math(update, ctx, lang):
    reset(ctx)
    q, ans = new_math()
    ctx.user_data["state"] = "math"
    ctx.user_data["math"] = {"ans": ans, "t0": time.time(), "score": 0}
    await render(update, ctx, t(lang, "math_start") + "\n\n" + q, Markup([back_row(lang)]))
    ctx.user_data["math"]["t0"] = time.time()


async def text_math(update, ctx, lang, text):
    uid = update.effective_user.id
    m = ctx.user_data["math"]
    s = text.strip()
    if not re.fullmatch(r"-?\d+", s):
        await update.message.reply_text(t(lang, "math_num"))
        return
    sec = time.time() - m["t0"]
    if int(s) == m["ans"]:
        pts = math_pts(sec)
        m["score"] += pts
        add_points(uid, "math", pts)
        q, ans = new_math()
        m["ans"] = ans
        msg = t(lang, "math_right", t=round(sec, 1), pts=pts) + "\n\n" + q
        await update.message.reply_text(msg, reply_markup=Markup([back_row(lang)]))
        m["t0"] = time.time()
    else:
        msg = t(lang, "math_wrong", a=m["ans"], s=m["score"])
        reset(ctx)
        await update.message.reply_text(msg, reply_markup=end_markup(lang, "math"))


# ====================== TOSH-QAYCHI-QOG'OZ ======================
RPS = {"r": "🪨", "s": "✂️", "p": "📄"}
RPS_BEATS = {"r": "s", "s": "p", "p": "r"}


def rps_markup(lang):
    return Markup([[Btn(v, callback_data=f"rps:{k}") for k, v in RPS.items()], back_row(lang)])


async def start_rps(update, ctx, lang):
    reset(ctx)
    ctx.user_data["rps"] = {"s": 0, "r": 0}
    await render(update, ctx, t(lang, "rps_start"), rps_markup(lang))


async def press_rps(update, ctx, lang, me):
    uid = update.effective_user.id
    st = ctx.user_data.setdefault("rps", {"s": 0, "r": 0})
    bot = random.choice(list(RPS))
    st["r"] += 1
    if me == bot:
        res = t(lang, "rps_draw")
    elif RPS_BEATS[me] == bot:
        res = t(lang, "rps_win")
        st["s"] += 1
        add_points(uid, "rps", 1)
    else:
        res = t(lang, "rps_lose")
    text = t(lang, "rps_res", r=st["r"], me=RPS[me], bot=RPS[bot], res=res, s=st["s"])
    await render(update, ctx, text, rps_markup(lang))


# ====================== PROFIL ======================
async def show_profile(update, ctx, lang):
    reset(ctx)
    uid = update.effective_user.id
    sc = get_scores(uid)
    lines = "\n".join(f"{t(lang, 'g_' + k)}: {sc.get(k, 0)} {t(lang, 'pts')}" for k in GAME_KEYS)
    total = sum(sc.get(k, 0) for k in GAME_KEYS)
    text = t(lang, "profile", name=update.effective_user.full_name, lines=lines, total=total)
    await render(update, ctx, text, Markup([back_row(lang)]))


# ====================== QR KOD ======================
async def send_qr(update, ctx, lang, data):
    if len(data) > 1200:
        await update.message.reply_text(t(lang, "qr_long"))
        return
    try:
        img = qrcode.make(data)
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)
    except Exception:
        await update.message.reply_text(t(lang, "qr_long"))
        return
    await update.message.reply_photo(bio, caption=t(lang, "qr_done"), reply_markup=Markup([back_row(lang)]))


# ====================== PAROL ======================
SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?"


def make_password(n):
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, SYMBOLS]
    allc = "".join(pools)
    rnd = secrets.SystemRandom()
    while True:
        chars = [secrets.choice(p) for p in pools] + [secrets.choice(allc) for _ in range(n - len(pools))]
        rnd.shuffle(chars)
        pwd = "".join(chars)
        h = hashlib.sha256(pwd.encode()).hexdigest()
        try:  # faqat xesh saqlanadi; takror chiqsa qaytadan yaratiladi
            db.execute("INSERT INTO passwords(hash) VALUES(?)", (h,))
            db.commit()
            return pwd
        except sqlite3.IntegrityError:
            continue


def pass_markup(lang):
    return Markup([[Btn(str(n), callback_data=f"pw:{n}") for n in (10, 15, 20)], back_row(lang)])


# ====================== ESLATMA ======================
def _unit_mult(w):
    w = w.lower()
    if w.startswith(("sek", "sec", "сек", "soniya", "сония")) or w in ("s", "с"):
        return 1
    if w.startswith(("min", "daq", "мин", "дақ")) or w in ("m", "м"):
        return 60
    if w.startswith(("soat", "соат", "час", "hour", "hr")) or w in ("h", "ч"):
        return 3600
    return None


_UNIT_RE = re.compile(r"(\d+)\s*([^\W\d_]+)")
_STOP = {
    "keyin", "dan", "eslat", "eslatib", "eslatgin", "eslating", "eslatingchi", "qo'y", "qoʻy", "menga", "bering",
    "кейин", "дан", "эслат", "эслатинг", "қўй", "менга",
    "через", "после", "напомни", "напомнить", "напомните", "мне",
    "remind", "me", "in", "after", "to", "please",
}


def parse_reminder(text):
    """'30 minutdan keyin ... eslat' -> (soniyalar, matn) yoki None."""
    total, spans = 0, []
    for m in _UNIT_RE.finditer(text):
        mult = _unit_mult(m.group(2))
        if mult:
            total += int(m.group(1)) * mult
            spans.append(m.span())
    if not total:
        return None
    rest = text
    for a, b in reversed(spans):
        rest = rest[:a] + " " + rest[b:]
    words = rest.split()
    clean = lambda w: w.lower().strip(".,!?:;-—«»\"")
    while words and clean(words[0]) in _STOP:
        words.pop(0)
    while words and clean(words[-1]) in _STOP:
        words.pop()
    return total, " ".join(words).strip()


def fmt_duration(sec, lang):
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    parts = []
    if h:
        parts.append(f"{h} {t(lang, 'u_h')}")
    if m:
        parts.append(f"{m} {t(lang, 'u_m')}")
    if s:
        parts.append(f"{s} {t(lang, 'u_s')}")
    return " ".join(parts)


async def fire_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.job.data
    try:
        await ctx.bot.send_message(d["chat_id"], t(d["lang"], "remind_fire", text=d["text"]))
    finally:
        db.execute("UPDATE reminders SET done=1 WHERE id=?", (d["id"],))
        db.commit()


def schedule_reminder(job_queue, rid, chat_id, lang, delay, text):
    job_queue.run_once(
        fire_reminder, when=max(1, delay),
        data={"id": rid, "chat_id": chat_id, "lang": lang, "text": text}, name=f"rem{rid}",
    )


async def text_remind(update, ctx, lang, text):
    parsed = parse_reminder(text)
    if not parsed:
        await update.message.reply_text(t(lang, "remind_bad"))
        return
    secs, body = parsed
    if secs > 30 * 86400:
        await update.message.reply_text(t(lang, "remind_far"))
        return
    body = body or t(lang, "remind_default")
    chat_id = update.effective_chat.id
    cur = db.execute(
        "INSERT INTO reminders(chat_id, lang, due, text) VALUES(?,?,?,?)",
        (chat_id, lang, time.time() + secs, body),
    )
    db.commit()
    schedule_reminder(ctx.job_queue, cur.lastrowid, chat_id, lang, secs, body)
    await update.message.reply_text(
        t(lang, "remind_set", when=fmt_duration(secs, lang), text=body),
        reply_markup=Markup([back_row(lang)]),
    )


async def post_init(app):
    """Bot qayta ishga tushganda tugamagan eslatmalarni tiklaydi."""
    rows = db.execute("SELECT id, chat_id, lang, due, text FROM reminders WHERE done=0").fetchall()
    for rid, chat_id, lang, due, text in rows:
        schedule_reminder(app.job_queue, rid, chat_id, lang, int(due - time.time()), text)
    log.info("Tiklangan eslatmalar: %d", len(rows))


# ====================== TUGMALAR ======================
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id
    lang = get_lang(uid)

    if not data.startswith("ttt:"):
        await q.answer()

    if data == "noop":
        return
    if data.startswith("lang:"):
        lang = data.split(":")[1]
        set_lang(uid, lang)
        await show_menu(update, ctx, lang)
    elif data == "chlang":
        reset(ctx)
        await render(update, ctx, t("uz", "pick_lang"), lang_markup())
    elif data == "menu":
        await show_menu(update, ctx, lang)
    elif data == "games":
        reset(ctx)
        rows = [[Btn(t(lang, "g_" + k), callback_data=f"g:{k}")] for k in GAME_KEYS]
        rows.append(back_row(lang))
        await render(update, ctx, t(lang, "games_title"), Markup(rows))
    elif data.startswith("g:"):
        starters = {
            "guess": start_guess, "hang": start_hang, "ttt": start_ttt,
            "quiz": start_quiz, "math": start_math, "rps": start_rps,
        }
        await starters[data.split(":")[1]](update, ctx, lang)
    elif data == "profile":
        await show_profile(update, ctx, lang)
    elif data == "qr":
        reset(ctx)
        ctx.user_data["state"] = "qr"
        await render(update, ctx, t(lang, "qr_ask"), Markup([back_row(lang)]))
    elif data == "pass":
        reset(ctx)
        await render(update, ctx, t(lang, "pass_ask"), pass_markup(lang))
    elif data.startswith("pw:"):
        n = int(data.split(":")[1])
        if n in (10, 15, 20):
            pwd = make_password(n)
            text = t(lang, "pass_done", n=n, p=f"<code>{html.escape(pwd)}</code>")
            await render(update, ctx, text, pass_markup(lang), parse_mode=ParseMode.HTML)
    elif data == "remind":
        reset(ctx)
        ctx.user_data["state"] = "remind"
        await render(update, ctx, t(lang, "remind_ask"), Markup([back_row(lang)]))
    elif data.startswith("ttt:"):
        await press_ttt(update, ctx, lang, int(data.split(":")[1]))
    elif data.startswith("qz:"):
        await press_quiz(update, ctx, lang, int(data.split(":")[1]))
    elif data.startswith("rps:"):
        await press_rps(update, ctx, lang, data.split(":")[1])


# ====================== MATN VA RASM ======================
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    text = update.message.text or ""
    state = ctx.user_data.get("state")
    if state == "guess":
        await text_guess(update, ctx, lang, text)
    elif state == "hang":
        await text_hang(update, ctx, lang, text)
    elif state == "math":
        await text_math(update, ctx, lang, text)
    elif state == "qr":
        await send_qr(update, ctx, lang, text)
    elif state == "remind":
        await text_remind(update, ctx, lang, text)
    else:
        await update.message.reply_text(t(lang, "use_menu"), reply_markup=menu_markup(lang))


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    if ctx.user_data.get("state") == "qr":
        caption = update.message.caption
        if caption:
            await send_qr(update, ctx, lang, caption)
        else:
            await update.message.reply_text(t(lang, "qr_photo"), reply_markup=Markup([back_row(lang)]))
    else:
        await update.message.reply_text(t(lang, "use_menu"), reply_markup=menu_markup(lang))


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Xatolik:", exc_info=ctx.error)


# ====================== ISHGA TUSHIRISH ======================
def main():
    if TOKEN.startswith("PASTE_"):
        raise SystemExit("bot.py ichida TOKEN ni BotFather bergan token bilan almashtiring.")
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    log.info("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
