import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ConversationHandler, ContextTypes
)
from config import TOKEN
from db import (
    init_db, get_user_level, set_user_level, add_known_word, add_to_queue,
    get_next_new_word, get_words_for_review, update_review, get_stats,
    is_word_known, get_all_known_words, bump_queue_word, reset_user_data
)
from hsk_loader import load_hsk_dicts
from tokenizer import segment_chinese
from telegram.request import HTTPXRequest
from local_dict import ChineseDictionary

HSK_DICT = load_hsk_dicts()
LOCAL_DICT = ChineseDictionary()

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TEXT_INPUT = 1

GAMES = {
    "translate": "📝 Угадай перевод",
    "scramble": "🔄 Перемешанные иероглифы",
    "pinyin": "🔊 Выбери иероглиф по пиньиню",
    "chain": "⛓️ Цепочка слов",
    "tones": "🎵 Выбери правильный пиньинь"
}

def extract_sentence(text: str, word: str) -> str:
    delimiters = ['。', '！', '？', '；', '!', '?', ';']
    sentences = []
    current = ''
    for ch in text:
        current += ch
        if ch in delimiters:
            sentences.append(current.strip())
            current = ''
    if current.strip():
        sentences.append(current.strip())
    for sent in sentences:
        if word in sent:
            return sent[:150]
    return text[:100]

def get_main_keyboard():
    buttons = [
        [KeyboardButton("/learn"), KeyboardButton("/review")],
        [KeyboardButton("/add_text"), KeyboardButton("/game")],
        [KeyboardButton("/mydict"), KeyboardButton("/stats")],
        [KeyboardButton("/reset"), KeyboardButton("/help")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

async def show_game_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    words = get_all_known_words(user_id)
    if len(words) < 4:
        await update.effective_chat.send_message(
            "❌ Недостаточно изученных слов (нужно минимум 4) для игр. Изучите ещё несколько слов через /learn и /review."
        )
        return
    keyboard = []
    for game_id, game_name in GAMES.items():
        keyboard.append([InlineKeyboardButton(game_name, callback_data=f"game_choose_{game_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.effective_chat.send_message(
        "🎮 *Выберите игру:*\n\nИгры помогут вам быстрее запомнить слова.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    level = get_user_level(user_id)
    await update.message.reply_text(
        f"🇨🇳 *Привет! Я робот-бобот, твой помощник в изучении китайского.*\n\n"
        f"📌 Твой уровень HSK: `{level}`\n"
        f"🔧 Изменить уровень: `/level 1-6`\n"
        f"📖 Посмотреть все команды: `/help`\n\n"
        f"🎯 *Что я умею:*\n"
        f"• Выделять новые слова из любого текста (`/add_text`)\n"
        f"• Показывать карточки с переводом, пиньинем и контекстом (`/learn`)\n"
        f"• Повторять слова по интервальной системе (`/review`)\n"
        f"• Играть в игры для запоминания (`/game`)\n"
        f"• Вести ваш личный словарик (`/mydict`)\n\n"
        f"💡 *Совет:* Начните с установки своего уровня, затем отправьте текст – и бот покажет новые слова!",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def set_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_level = int(context.args[0])
        if 1 <= new_level <= 6:
            set_user_level(update.effective_user.id, new_level)
            await update.message.reply_text(f"Уровень HSK установлен на {new_level}.")
        else:
            await update.message.reply_text("Уровень должен быть от 1 до 6.")
    except (IndexError, ValueError):
        await update.message.reply_text("Используйте: /level <число от 1 до 6>")

async def add_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправьте китайский текст (одним сообщением):", reply_markup=ReplyKeyboardRemove())
    return TEXT_INPUT

async def add_text_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Текст не может быть пустым. Попробуйте снова.")
        return TEXT_INPUT

    words = segment_chinese(text)
    user_level = get_user_level(user_id)
    new_found = 0
    for w in words:
        if is_word_known(user_id, w):
            continue
        context_sent = extract_sentence(text, w)

        info = HSK_DICT.get(w)
        if info:
            pinyin, translation, word_level = info
            if word_level > user_level:
                if add_to_queue(user_id, w, pinyin, translation, word_level, context=context_sent):
                    new_found += 1
            continue

        pinyin, translation = LOCAL_DICT.lookup(w)
        if pinyin and translation:
            if add_to_queue(user_id, w, pinyin, translation, 0, context=context_sent):
                new_found += 1
        else:
            logger.debug(f"Слово '{w}' не найдено в словарях, пропущено")

    known_count, queue_count = get_stats(user_id)
    await update.message.reply_text(
        f"✅ *Обработано!*\n\n"
        f"📝 Новых слов: `{new_found}`\n"
        f"📚 Всего в очереди: `{queue_count}`\n"
        f"🎓 Изучено слов: `{known_count}`\n\n"
        f"👉 Для изучения используй `/learn`",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    word_data = get_next_new_word(user_id)
    if not word_data:
        await update.message.reply_text("Нет новых слов в очереди. Отправьте текст через /add_text")
        return

    context.user_data["current_learn_word"] = word_data["word"]
    card_text = (
        f"📖 *Новое слово*\n\n"
        f"`{word_data['word']}`\n"
        f"*Пиньинь:* {word_data['pinyin']}\n"
        f"*Перевод:* {word_data['translation']}\n"
        f"*HSK уровень:* {word_data['hsk_level']}\n"
        f"*Контекст:* {word_data['context']}..."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Знаю", callback_data=f"learn_know_{word_data['word']}")],
        [InlineKeyboardButton("❌ Не знаю", callback_data=f"learn_skip_{word_data['word']}")]
    ])
    await update.message.reply_text(card_text, parse_mode="Markdown", reply_markup=keyboard)

async def learn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"learn_callback: тип context = {type(context)}")
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("learn_know_"):
        word = data.replace("learn_know_", "")
        import sqlite3
        from db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        try:
            c = conn.cursor()
            c.execute("SELECT pinyin, translation, hsk_level, context FROM words_queue WHERE user_id = ? AND word = ?", (user_id, word))
            row = c.fetchone()
            if row:
                pinyin, translation, hsk_level, context = row
                add_known_word(user_id, word, pinyin, translation, hsk_level, context)
                await query.edit_message_text(f"✅ Слово «{word}» добавлено в изученные!")
            else:
                await query.edit_message_text("Ошибка: слово не найдено в очереди.")
        finally:
            conn.close()
    elif data.startswith("learn_skip_"):
        word = data.replace("learn_skip_", "")
        await query.edit_message_text("Слово пропущено. Оно перемещено в конец очереди.")
        bump_queue_word(user_id, word)
    else:
        await query.edit_message_text("Неизвестная команда.")
        return

    next_word = get_next_new_word(user_id)
    if next_word:
        if not hasattr(context, 'user_data') or not isinstance(context, ContextTypes.DEFAULT_TYPE):
            logger.error(f"learn_callback: некорректный контекст, тип: {type(context)}")
            return
        context.user_data["current_learn_word"] = next_word["word"]
        card_text = (
            f"📖 *Новое слово*\n\n"
            f"`{next_word['word']}`\n"
            f"*Пиньинь:* {next_word['pinyin']}\n"
            f"*Перевод:* {next_word['translation']}\n"
            f"*HSK уровень:* {next_word['hsk_level']}\n"
            f"*Контекст:* {next_word['context']}..."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Знаю", callback_data=f"learn_know_{next_word['word']}")],
            [InlineKeyboardButton("❌ Не знаю", callback_data=f"learn_skip_{next_word['word']}")]
        ])
        await query.message.reply_text(card_text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await query.message.reply_text("Все новые слова изучены! Отлично!", reply_markup=get_main_keyboard())

async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    review_words = get_words_for_review(user_id)
    if not review_words:
        await update.message.reply_text("Сегодня нет слов для повторения. Отдохни!")
        return
    context.user_data["review_list"] = review_words
    context.user_data["review_index"] = 0
    await show_review_card(update, context)

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idx = context.user_data.get("review_index", 0)
    review_list = context.user_data.get("review_list", [])
    if idx >= len(review_list):
        await update.message.reply_text("Повторение закончено! Молодец.")
        return
    w = review_list[idx]
    card_text = (
        f"🔄 *Повторение* ({idx+1}/{len(review_list)})\n\n"
        f"`{w['word']}`\n"
        f"*Пиньинь:* {w['pinyin']}\n"
        f"*Перевод:* {w['translation']}\n"
        f"*HSK:* {w['hsk_level']}\n"
        f"*Контекст:* {w['context'] if w['context'] else '—'}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Знаю", callback_data="review_know"),
         InlineKeyboardButton("❌ Не знаю", callback_data="review_dont_know")]
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(card_text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(card_text, parse_mode="Markdown", reply_markup=keyboard)

async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action = query.data
    idx = context.user_data.get("review_index", 0)
    review_list = context.user_data.get("review_list", [])
    if idx >= len(review_list):
        await query.edit_message_text("Повторение завершено.")
        return

    word_data = review_list[idx]
    word = word_data["word"]
    if action == "review_know":
        update_review(user_id, word, success=True)
        await query.edit_message_text(f"✅ {word} – запомнил. Отлично!")
    else:
        update_review(user_id, word, success=False)
        await query.edit_message_text(f"❌ {word} – повторим позже.")

    context.user_data["review_index"] = idx + 1
    if context.user_data["review_index"] < len(review_list):
        await show_review_card(update, context)
    else:
        await query.message.reply_text("Повторение окончено! Ты молодец!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    known, queue = get_stats(user_id)
    level = get_user_level(user_id)
    await update.message.reply_text(
        f"📊 *Твоя статистика*\n"
        f"Уровень HSK: {level}\n"
        f"Изучено слов: {known}\n"
        f"Слов в очереди: {queue}",
        parse_mode="Markdown"
    )

async def mydict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    words = get_all_known_words(user_id)
    if not words:
        await update.message.reply_text("📭 У вас пока нет изученных слов. Добавьте слова через /learn.")
        return
    text = "📖 *Ваш словарик:*\n\n"
    for i, w in enumerate(words[:30], 1):
        text += f"{i}. {w['word']} — {w['translation']}\n"
    if len(words) > 30:
        text += f"\n... и ещё {len(words)-30} слов. Список слишком длинный, показана только часть."
    await update.message.reply_text(text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 Доступные команды:\n\n"
        "/start - Начать работу\n"
        "/level <1-6> - Установить свой уровень HSK\n"
        "/add_text - Отправить китайский текст для выделения новых слов\n"
        "/learn - Изучать следующее новое слово из очереди\n"
        "/review - Повторять выученные слова (интервальные повторения)\n"
        "/stats - Показать статистику\n"
        "/mydict - Показать все изученные слова с переводом\n"
        "/game - Выбрать игру для повторения слов\n"
        "/cancel - Отменить текущий диалог\n"
        "/reset - Полностью очистить все изученные слова и очередь\n"
        "/help - Показать это сообщение\n\n"
        "💡 Совет: Установите правильный уровень HSK (/level), чтобы бот правильно определял знакомые и новые слова."
    )
    await update.message.reply_text(text)

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, сбросить всё", callback_data="confirm_reset"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")
        ]
    ])
    await update.message.reply_text(
        "⚠️ *ВНИМАНИЕ!* Эта команда удалит *ВСЕ* ваши изученные слова и очистит очередь.\n\n"
        "Вы уверены?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data == "confirm_reset":
        reset_user_data(user_id)
        await query.edit_message_text("✅ Все ваши данные сброшены.", reply_markup=get_main_keyboard())
    else:
        await query.edit_message_text("❌ Сброс отменён.")

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_game_menu(update, context)

async def game_choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    game_id = query.data.replace("game_choose_", "")
    if game_id == "translate":
        await start_translate_game(update, context)
    elif game_id == "scramble":
        await start_scramble_game(update, context)
    elif game_id == "pinyin":
        await start_pinyin_game(update, context)
    elif game_id == "chain":
        await start_chain_game(update, context)
    elif game_id == "tones":
        await start_tones_game(update, context)
    else:
        await query.edit_message_text("🚧 Эта игра ещё в разработке.")

async def start_translate_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        message = update.callback_query.message
    else:
        user_id = update.effective_user.id
        message = update.message

    words = get_all_known_words(user_id)
    words = [w for w in words if w.get("word") and w.get("translation") and w["translation"].strip()]

    if len(words) < 4:
        await message.reply_text("❌ Недостаточно слов с переводом для игры (нужно минимум 4).")
        return

    target = random.choice(words)
    other_words = [w for w in words if w["word"] != target["word"]]
    if len(other_words) < 3:
        await message.reply_text("❌ Недостаточно вариантов для ответа.")
        return

    options = random.sample(other_words, 3)
    try:
        variants = [target["translation"]] + [opt["translation"] for opt in options]
    except (TypeError, KeyError) as e:
        logger.error(f"Ошибка формирования вариантов в translate: {e}")
        await message.reply_text("❌ Произошла ошибка. Попробуйте позже.")
        return

    random.shuffle(variants)

    context.user_data["translate_game"] = {
        "word": target["word"],
        "correct": target["translation"],
        "variants": variants
    }

    keyboard = [
        [InlineKeyboardButton(variants[0], callback_data="game_translate_0"),
         InlineKeyboardButton(variants[1], callback_data="game_translate_1")],
        [InlineKeyboardButton(variants[2], callback_data="game_translate_2"),
         InlineKeyboardButton(variants[3], callback_data="game_translate_3")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"🎮 *Игра: угадай перевод*\n\nСлово: `{target['word']}`\n\nКакой перевод правильный?"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def translate_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_index = int(query.data.split("_")[-1])
    game_state = context.user_data.get("translate_game")
    if not game_state:
        await query.edit_message_text("❌ Игра устарела. Начните новую командой /game.")
        return
    correct = game_state["correct"]
    word = game_state["word"]
    selected = game_state["variants"][selected_index]
    if selected == correct:
        text = f"✅ *Правильно!*\n\n`{word}` — {correct}"
    else:
        text = f"❌ *Неправильно.*\n\nПравильный ответ: `{word}` — {correct}"
    keyboard = [
        [InlineKeyboardButton("🔁 Ещё раз", callback_data="translate_again")],
        [InlineKeyboardButton("🎮 В меню", callback_data="to_game_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.pop("translate_game", None)

async def translate_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_translate_game(update, context)

async def start_scramble_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        message = update.callback_query.message
    else:
        user_id = update.effective_user.id
        message = update.message

    all_words = get_all_known_words(user_id)
    words = [w for w in all_words if len(w['word']) >= 2]
    if len(words) < 4:
        await message.reply_text("❌ Нужно минимум 4 слова длиной 2+ иероглифа для игры.")
        return

    target = random.choice(words)
    original = target['word']
    shuffled_list = list(original)
    random.shuffle(shuffled_list)
    scrambled = ''.join(shuffled_list)
    if scrambled == original:
        random.shuffle(shuffled_list)
        scrambled = ''.join(shuffled_list)

    other_words = [w for w in words if w['word'] != original]
    options = random.sample(other_words, 3)
    variants = [original] + [w['word'] for w in options]
    random.shuffle(variants)

    context.user_data["scramble_game"] = {
        "original": original,
        "translation": target['translation'],
        "variants": variants
    }

    keyboard = [[InlineKeyboardButton(v, callback_data=f"scramble_ans_{i}")] for i, v in enumerate(variants)]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"🔀 *Какое слово было перемешано?*\n\n{scrambled}"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def scramble_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_index = int(query.data.split("_")[-1])
    game_state = context.user_data.get("scramble_game")
    if not game_state:
        await query.edit_message_text("❌ Игра устарела. Начните новую командой /game.")
        return
    correct = game_state["original"]
    translation = game_state["translation"]
    selected = game_state["variants"][selected_index]
    if selected == correct:
        text = f"✅ *Правильно!*\n\nСлово: `{correct}` — {translation}"
    else:
        text = f"❌ *Неправильно.*\n\nПравильный ответ: `{correct}` — {translation}"
    keyboard = [
        [InlineKeyboardButton("🔁 Ещё раз", callback_data="scramble_again")],
        [InlineKeyboardButton("🎮 В меню", callback_data="to_game_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.pop("scramble_game", None)

async def scramble_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_scramble_game(update, context)

async def start_pinyin_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        message = update.callback_query.message
    else:
        user_id = update.effective_user.id
        message = update.message

    all_words = get_all_known_words(user_id)
    words_with_pinyin = []
    for w in all_words:
        word = w['word']
        info = HSK_DICT.get(word)
        if info:
            pinyin = info[0]
            words_with_pinyin.append((word, pinyin, w['translation']))
        else:
            pinyin, translation = LOCAL_DICT.lookup(word)
            if pinyin:
                words_with_pinyin.append((word, pinyin, translation))
    if len(words_with_pinyin) < 4:
        await message.reply_text("❌ Недостаточно слов с пиньинем для игры (нужно минимум 4).")
        return

    target_word, target_pinyin, target_trans = random.choice(words_with_pinyin)
    other_words = [w for w in words_with_pinyin if w[0] != target_word]
    options = random.sample(other_words, 3)
    variants = [target_word] + [w[0] for w in options]
    random.shuffle(variants)

    context.user_data["pinyin_game"] = {
        "correct": target_word,
        "pinyin": target_pinyin,
        "translation": target_trans,
        "variants": variants
    }

    keyboard = [[InlineKeyboardButton(v, callback_data=f"pinyin_ans_{i}")] for i, v in enumerate(variants)]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"🔊 *Какой иероглиф соответствует пиньиню?*\n\n`{target_pinyin}`"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def pinyin_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_index = int(query.data.split("_")[-1])
    game_state = context.user_data.get("pinyin_game")
    if not game_state:
        await query.edit_message_text("❌ Игра устарела. Начните новую командой /game.")
        return
    correct = game_state["correct"]
    pinyin = game_state["pinyin"]
    translation = game_state["translation"]
    selected = game_state["variants"][selected_index]
    if selected == correct:
        text = f"✅ *Правильно!*\n\n`{pinyin}` → {correct} — {translation}"
    else:
        text = f"❌ *Неправильно.*\n\nПравильный ответ: `{pinyin}` → {correct} — {translation}"
    keyboard = [
        [InlineKeyboardButton("🔁 Ещё раз", callback_data="pinyin_again")],
        [InlineKeyboardButton("🎮 В меню", callback_data="to_game_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.pop("pinyin_game", None)

async def pinyin_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_pinyin_game(update, context)

async def start_chain_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        message = update.callback_query.message
    else:
        user_id = update.effective_user.id
        message = update.message

    all_words = get_all_known_words(user_id)
    if len(all_words) < 4:
        await message.reply_text("❌ Недостаточно изученных слов для игры (нужно минимум 4).")
        return

    index = {}
    for w in all_words:
        first_char = w['word'][0]
        index.setdefault(first_char, []).append(w)

    current_word_obj = random.choice(all_words)
    current_word = current_word_obj['word']
    current_trans = current_word_obj['translation']

    context.user_data["chain_game"] = {
        "current_word": current_word,
        "current_trans": current_trans,
        "index": index,
        "used_words": set([current_word])
    }

    await show_chain_question(update, context, message)

async def show_chain_question(update: Update, context: ContextTypes.DEFAULT_TYPE, message):
    game = context.user_data.get("chain_game")
    if not game:
        await message.reply_text("❌ Игра не активна. Начните заново через /game.")
        return

    current_word = game["current_word"]
    current_trans = game["current_trans"]
    last_char = current_word[-1]
    index = game["index"]
    used = game["used_words"]

    candidates = [w for w in index.get(last_char, []) if w['word'] not in used]
    if not candidates:
        keyboard = [[InlineKeyboardButton("🎮 В меню", callback_data="to_game_menu")]]
        await message.reply_text(
            f"🏁 *Игра окончена!*\n\n"
            f"Последнее слово: `{current_word}` — {current_trans}\n"
            f"Нет слов на `{last_char}`. Вы справились отлично!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data.pop("chain_game", None)
        return

    num_options = min(3, len(candidates))
    selected = random.sample(candidates, num_options)
    correct_word_obj = random.choice(selected)
    correct_word = correct_word_obj['word']
    variants = [correct_word] + [w['word'] for w in selected if w['word'] != correct_word]
    if len(variants) < 4:
        all_other = [w for w in index.get(last_char, []) if w['word'] not in used and w['word'] not in variants]
        if all_other:
            variants.extend(random.sample(all_other, min(4 - len(variants), len(all_other))))
    random.shuffle(variants)

    game["chain_question"] = {
        "correct": correct_word,
        "correct_trans": correct_word_obj['translation'],
        "variants": variants,
        "last_char": last_char
    }

    keyboard = [[InlineKeyboardButton(v, callback_data=f"chain_ans_{i}")] for i, v in enumerate(variants)]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"⛓️ *Цепочка слов*\n\n"
        f"Текущее слово: `{current_word}` — {current_trans}\n"
        f"Последний иероглиф: `{last_char}`\n\n"
        f"Выберите слово, которое начинается на `{last_char}`:"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def chain_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_index = int(query.data.split("_")[-1])
    game = context.user_data.get("chain_game")
    if not game:
        await query.edit_message_text("❌ Игра устарела. Начните новую командой /game.")
        return
    question = game.get("chain_question")
    if not question:
        await query.edit_message_text("❌ Ошибка игры. Начните заново.")
        return

    selected_word = question["variants"][selected_index]
    correct_word = question["correct"]
    if selected_word == correct_word:
        correct_obj = None
        for w in game["index"].get(correct_word[0], []):
            if w['word'] == correct_word:
                correct_obj = w
                break
        if not correct_obj:
            correct_obj = {"word": correct_word, "translation": "?"}
        game["current_word"] = correct_word
        game["current_trans"] = correct_obj['translation']
        game["used_words"].add(correct_word)
        game.pop("chain_question", None)
        await show_chain_question(update, context, query.message)
    else:
        correct_obj = None
        for w in game["index"].get(correct_word[0], []):
            if w['word'] == correct_word:
                correct_obj = w
                break
        if not correct_obj:
            correct_obj = {"word": correct_word, "translation": "?"}
        keyboard = [[InlineKeyboardButton("🎮 В меню", callback_data="to_game_menu")]]
        await query.edit_message_text(
            f"❌ *Неправильно.*\n\n"
            f"Вы выбрали: `{selected_word}`\n"
            f"Правильный ответ: `{correct_word}` — {correct_obj['translation']}\n\n"
            f"Игра окончена.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data.pop("chain_game", None)

async def chain_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_chain_game(update, context)

def generate_tone_variants(pinyin: str, num_variants: int = 3):
    """Генерирует варианты пиньиня с изменёнными тонами."""
    import re
    tone_chars = {'ā', 'á', 'ǎ', 'à', 'ē', 'é', 'ě', 'è', 'ī', 'í', 'ǐ', 'ì', 'ō', 'ó', 'ǒ', 'ò', 'ū', 'ú', 'ǔ', 'ù', 'ǖ', 'ǘ', 'ǚ', 'ǜ'}
    variants = set()
    for _ in range(20):
        new_pinyin = list(pinyin)
        num_changes = random.randint(1, min(2, len([c for c in new_pinyin if c in tone_chars])))
        changed = 0
        while changed < num_changes:
            pos = random.randint(0, len(new_pinyin)-1)
            if new_pinyin[pos] in tone_chars:
                base = new_pinyin[pos][0]
                new_tone = random.choice([1,2,3,4])
                if base == 'a':
                    new_char = ['ā','á','ǎ','à'][new_tone-1]
                elif base == 'e':
                    new_char = ['ē','é','ě','è'][new_tone-1]
                elif base == 'i':
                    new_char = ['ī','í','ǐ','ì'][new_tone-1]
                elif base == 'o':
                    new_char = ['ō','ó','ǒ','ò'][new_tone-1]
                elif base == 'u':
                    new_char = ['ū','ú','ǔ','ù'][new_tone-1]
                elif base == 'ü':
                    new_char = ['ǖ','ǘ','ǚ','ǜ'][new_tone-1]
                else:
                    continue
                new_pinyin[pos] = new_char
                changed += 1
        variant = ''.join(new_pinyin)
        if variant != pinyin:
            variants.add(variant)
        if len(variants) >= num_variants:
            break
    return list(variants)[:num_variants]

async def start_tones_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        message = update.callback_query.message
    else:
        user_id = update.effective_user.id
        message = update.message

    all_words = get_all_known_words(user_id)
    words_with_pinyin = []
    for w in all_words:
        word = w['word']
        info = HSK_DICT.get(word)
        if info and info[0]:
            pinyin = info[0]
            words_with_pinyin.append((word, pinyin, w['translation']))
        else:
            pinyin, translation = LOCAL_DICT.lookup(word)
            if pinyin:
                words_with_pinyin.append((word, pinyin, translation))
    if len(words_with_pinyin) < 4:
        await message.reply_text("❌ Недостаточно слов с пиньинем для игры (нужно минимум 4).")
        return

    target_word, target_pinyin, target_trans = random.choice(words_with_pinyin)
    variants = [target_pinyin]
    wrong = generate_tone_variants(target_pinyin, 3)
    variants.extend(wrong)
    random.shuffle(variants)

    context.user_data["tones_game"] = {
        "word": target_word,
        "correct": target_pinyin,
        "translation": target_trans,
        "variants": variants
    }

    keyboard = [[InlineKeyboardButton(v, callback_data=f"tones_ans_{i}")] for i, v in enumerate(variants)]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"🎵 *Какой пиньинь правильный?*\n\nСлово: `{target_word}`"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def tones_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_index = int(query.data.split("_")[-1])
    game_state = context.user_data.get("tones_game")
    if not game_state:
        await query.edit_message_text("❌ Игра устарела. Начните новую командой /game.")
        return
    correct = game_state["correct"]
    word = game_state["word"]
    translation = game_state["translation"]
    selected = game_state["variants"][selected_index]
    if selected == correct:
        text = f"✅ *Правильно!*\n\n`{word}` → {correct} — {translation}"
    else:
        text = f"❌ *Неправильно.*\n\nПравильный ответ: `{word}` → {correct} — {translation}"
    keyboard = [
        [InlineKeyboardButton("🔁 Ещё раз", callback_data="tones_again")],
        [InlineKeyboardButton("🎮 В меню", callback_data="to_game_menu")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.pop("tones_game", None)

async def tones_again_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_tones_game(update, context)

async def to_game_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_game_menu(update, context)

def main():
    init_db()
    request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30)
    app = Application.builder().token(TOKEN).request(request).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_text", add_text_start)],
        states={TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_text_process)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("level", set_level))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CallbackQueryHandler(learn_callback, pattern="^learn_"))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CallbackQueryHandler(review_callback, pattern="^review_"))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("mydict", mydict))
    app.add_handler(CommandHandler("game", game))
    app.add_handler(CallbackQueryHandler(game_choose_callback, pattern="^game_choose_"))
    app.add_handler(CallbackQueryHandler(translate_game_callback, pattern="^game_translate_"))
    app.add_handler(CallbackQueryHandler(translate_again_callback, pattern="^translate_again$"))
    app.add_handler(CallbackQueryHandler(scramble_game_callback, pattern="^scramble_ans_"))
    app.add_handler(CallbackQueryHandler(scramble_again_callback, pattern="^scramble_again$"))
    app.add_handler(CallbackQueryHandler(pinyin_game_callback, pattern="^pinyin_ans_"))
    app.add_handler(CallbackQueryHandler(pinyin_again_callback, pattern="^pinyin_again$"))
    app.add_handler(CallbackQueryHandler(chain_game_callback, pattern="^chain_ans_"))
    app.add_handler(CallbackQueryHandler(chain_again_callback, pattern="^chain_again$"))
    app.add_handler(CallbackQueryHandler(tones_game_callback, pattern="^tones_ans_"))
    app.add_handler(CallbackQueryHandler(tones_again_callback, pattern="^tones_again$"))
    app.add_handler(CallbackQueryHandler(to_game_menu_callback, pattern="^to_game_menu$"))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern="^(confirm_reset|cancel_reset)$"))

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
