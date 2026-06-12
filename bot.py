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
        [KeyboardButton("/learn"), KeyboardButton("/review"), KeyboardButton("/level")],
        [KeyboardButton("/add_text"), KeyboardButton("/game")],
        [KeyboardButton("/mydict"), KeyboardButton("/stats")],
        [KeyboardButton("/reset"), KeyboardButton("/help")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def generate_tone_variants(pinyin: str, num_variants: int = 3):
    """
    Генерирует варианты пиньиня с изменёнными тонами.
    """
    tone_map = {
        'a': ['ā', 'á', 'ǎ', 'à'],
        'e': ['ē', 'é', 'ě', 'è'],
        'i': ['ī', 'í', 'ǐ', 'ì'],
        'o': ['ō', 'ó', 'ǒ', 'ò'],
        'u': ['ū', 'ú', 'ǔ', 'ù'],
        'ü': ['ǖ', 'ǘ', 'ǚ', 'ǜ']
    }
    syllables = pinyin.split()
    if not syllables:
        return []

    current_tones = []
    for syl in syllables:
        tone = 0
        for base, chars in tone_map.items():
            for idx, ch in enumerate(chars):
                if ch in syl:
                    tone = idx + 1
                    break
            if tone:
                break
        current_tones.append(tone)

    variants = set()
    attempts = 0
    while len(variants) < num_variants and attempts < 50:
        eligible = [i for i, t in enumerate(current_tones) if t != 0]
        if not eligible:
            break
        idx = random.choice(eligible)
        current_tone = current_tones[idx]
        new_tone = random.choice([t for t in [1,2,3,4] if t != current_tone])
        new_syl = syllables[idx]
        for base, chars in tone_map.items():
            if base in new_syl or any(ch in new_syl for ch in chars):
                for old_ch in chars:
                    if old_ch in new_syl:
                        new_syl = new_syl.replace(old_ch, chars[new_tone-1])
                        break
                break
        new_pinyin = syllables.copy()
        new_pinyin[idx] = new_syl
        variant = ' '.join(new_pinyin)
        if variant != pinyin:
            variants.add(variant)
        attempts += 1

    return list(variants)[:num_variants]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    level = await get_user_level(user_id)
    await update.message.reply_text(
        f"🇨🇳 *Привет! Я 机器-教师, твой помощник в изучении китайского!*\n\n"
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
    if context.args and len(context.args) > 0:
        try:
            new_level = int(context.args[0])
            if 1 <= new_level <= 6:
                await set_user_level(update.effective_user.id, new_level)
                await update.message.reply_text(f"✅ Уровень HSK установлен на {new_level}.")
            else:
                await update.message.reply_text("❌ Уровень должен быть от 1 до 6.")
        except ValueError:
            await update.message.reply_text("❌ Используйте число от 1 до 6.\nПример: `/level 3`", parse_mode="Markdown")
        return

    keyboard = []
    for level in range(1, 7):
        keyboard.append([InlineKeyboardButton(f"HSK {level}", callback_data=f"set_level_{level}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎯 *Выберите ваш уровень HSK:*\n\n"
        "Слова выше выбранного уровня будут считаться новыми.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def level_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    level = int(query.data.split("_")[-1])
    user_id = query.from_user.id
    await set_user_level(user_id, level)
    await query.edit_message_text(f"✅ Ваш уровень HSK установлен на {level}.")
    await query.message.reply_text("Используйте главное меню для продолжения.", reply_markup=get_main_keyboard())

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
    user_level = await get_user_level(user_id)
    new_found = 0
    for w in words:
        if await is_word_known(user_id, w):
            continue
        context_sent = extract_sentence(text, w)

        info = HSK_DICT.get(w)
        if info:
            pinyin, translation, word_level = info
            if word_level > user_level:
                if await add_to_queue(user_id, w, pinyin, translation, word_level, context=context_sent):
                    new_found += 1
            continue

        pinyin, translation = LOCAL_DICT.lookup(w)
        if pinyin and translation:
            if await add_to_queue(user_id, w, pinyin, translation, 0, context=context_sent):
                new_found += 1
        else:
            logger.debug(f"Слово '{w}' не найдено в словарях, пропущено")

    known_count, queue_count = await get_stats(user_id)
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

async def cancel_in_add_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершает диалог без дополнительных сообщений, если пришла команда /cancel."""
    await update.message.reply_text("Ввод текста отменён.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    word_data = await get_next_new_word(user_id)
    if not word_data:
        await update.message.reply_text("Нет новых слов в очереди. Отправьте текст через /add_text")
        return

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
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("learn_know_"):
        word = data.replace("learn_know_", "")
        from db import DB_PATH
        import aiosqlite
        async with aiosqlite.connect(DB_PATH) as conn:
            async with conn.execute("SELECT pinyin, translation, hsk_level, context FROM words_queue WHERE user_id = ? AND word = ?", (user_id, word)) as cursor:
                row = await cursor.fetchone()
                if row:
                    pinyin, translation, hsk_level, context_sent = row
                    await add_known_word(user_id, word, pinyin, translation, hsk_level, context_sent)
                    async with conn.execute("SELECT COUNT(*) FROM words_queue WHERE user_id = ?", (user_id,)) as cursor2:
                        queue_count = (await cursor2.fetchone())[0]
                    if queue_count == 0:
                        await query.edit_message_text(
                            f"✅ Слово «{word}» добавлено в изученные! В очереди больше нет слов. Отлично!"
                        )
                        await query.message.reply_text("Выберите действие:", reply_markup=get_main_keyboard())
                        return
                    else:
                        await query.edit_message_text(
                            f"✅ Слово «{word}» добавлено в изученные! Осталось слов в очереди: {queue_count}"
                        )
                else:
                    await query.edit_message_text("Ошибка: слово не найдено в очереди.")
                    return
    elif data.startswith("learn_skip_"):
        word = data.replace("learn_skip_", "")
        await query.edit_message_text("Слово пропущено. Оно перемещено в конец очереди.")
        await bump_queue_word(user_id, word)
    else:
        await query.edit_message_text("Неизвестная команда.")
        return

    next_word = await get_next_new_word(user_id)
    if next_word:
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
    review_words = await get_words_for_review(user_id)
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
        await update_review(user_id, word, success=True)
        await query.edit_message_text(f"✅ {word} – запомнил. Отлично!")
    else:
        await update_review(user_id, word, success=False)
        await query.edit_message_text(f"❌ {word} – повторим позже.")

    context.user_data["review_index"] = idx + 1
    if context.user_data["review_index"] < len(review_list):
        await show_review_card(update, context)
    else:
        await query.message.reply_text("Повторение окончено! Ты молодец!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    known, queue = await get_stats(user_id)
    level = await get_user_level(user_id)
    await update.message.reply_text(
        f"📊 *Твоя статистика*\n"
        f"Уровень HSK: {level}\n"
        f"Изучено слов: {known}\n"
        f"Слов в очереди: {queue}",
        parse_mode="Markdown"
    )

async def mydict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    words = await get_all_known_words(user_id)
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
        await reset_user_data(user_id)
        await query.edit_message_text("✅ Все ваши данные сброшены (включая уровень HSK на 1).")
        await query.message.reply_text("Нажмите /start или используйте меню.", reply_markup=get_main_keyboard())
    else:
        await query.edit_message_text("❌ Сброс отменён.")

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_game_menu(update, context)

async def show_game_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    words = await get_all_known_words(user_id)
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

    words = await get_all_known_words(user_id)
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

    all_words = await get_all_known_words(user_id)
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

    all_words = await get_all_known_words(user_id)
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
    words_with_pinyin = [(w, p, t) for w, p, t in words_with_pinyin if p]
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

    all_words = await get_all_known_words(user_id)
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
        keyboard = [
            [InlineKeyboardButton("🔁 Ещё раз", callback_data="chain_again")],
            [InlineKeyboardButton("🎮 В меню", callback_data="to_game_menu")]
        ]
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
        keyboard = [
            [InlineKeyboardButton("🔁 Ещё раз", callback_data="chain_again")],
            [InlineKeyboardButton("🎮 В меню", callback_data="to_game_menu")]
        ]
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

async def start_tones_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        message = update.callback_query.message
    else:
        user_id = update.effective_user.id
        message = update.message

    all_words = await get_all_known_words(user_id)
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

    tone_chars = set('āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ')
    words_with_pinyin = [(w, p, t) for w, p, t in words_with_pinyin if p and any(c in tone_chars for c in p)]
    if len(words_with_pinyin) < 2:
        await message.reply_text("❌ Недостаточно слов с тоновым пиньинем для игры.")
        return

    target_word = None
    target_pinyin = None
    target_trans = None
    for _ in range(20):
        cand_word, cand_pinyin, cand_trans = random.choice(words_with_pinyin)
        if not cand_pinyin:
            continue
        variants = generate_tone_variants(cand_pinyin, 2)
        if len(variants) >= 1:
            target_word, target_pinyin, target_trans = cand_word, cand_pinyin, cand_trans
            break

    if target_word is None or target_pinyin is None:
        await message.reply_text("❌ Не удалось подобрать слово с вариантами тонов. Попробуйте позже.")
        return

    wrong_variants = generate_tone_variants(target_pinyin, 3)
    if not wrong_variants:
        await message.reply_text("❌ Для этого слова нет вариантов тонов. Выберите другое слово.")
        return

    variants = [target_pinyin] + wrong_variants[:3]
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

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if update and update.effective_message:
        await update.effective_message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

def main():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())
    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0
    )
    app = Application.builder().token(TOKEN).request(request).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_text", add_text_start)],
        states={
            TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_text_process)]
        },
        fallbacks=[CommandHandler("cancel", cancel_in_add_text)],
        allow_reentry=True
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
    app.add_handler(CallbackQueryHandler(level_choice_callback, pattern="^set_level_"))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_error_handler(error_handler)

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
