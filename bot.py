import logging
import random
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    "translate": "📝 Угадай перевод слова",
    "pinyin": "🔊 Выбери правильный пиньинь"
}

def extract_sentence(text: str, word: str) -> str:
    """Извлекает предложение, содержащее слово, или возвращает первые 100 символов текста."""
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

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список доступных игр для выбора."""
    user_id = update.effective_user.id
    words = get_all_known_words(user_id)
    if len(words) < 4:
        await update.message.reply_text("❌ Недостаточно изученных слов (нужно минимум 4) для игр. Изучите ещё несколько слов через /learn и /review.")
        return
    
    keyboard = []
    for game_id, game_name in GAMES.items():
        keyboard.append([InlineKeyboardButton(game_name, callback_data=f"game_choose_{game_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎮 *Выберите игру:*\n\nИгры помогут вам быстрее запомнить слова.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def game_choose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает выбор игры из меню."""
    query = update.callback_query
    await query.answer()
    data = query.data
    game_id = data.replace("game_choose_", "")
    
    if game_id == "translate":
        await start_translate_game(update, context)
    else:
        await query.edit_message_text("🚧 Эта игра ещё в разработке. Попробуйте другую.")

async def start_translate_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает игру: выбрать правильный перевод слова."""
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        message = update.callback_query.message
    else:
        user_id = update.effective_user.id
        message = update.message
    
    words = get_all_known_words(user_id)
    words = [w for w in words if w["translation"]]
    if len(words) < 4:
        await message.reply_text("❌ Недостаточно слов с переводом для игры (нужно минимум 4).")
        return
    
    target = random.choice(words)
    other_words = [w for w in words if w["word"] != target["word"]]
    if len(other_words) < 3:
        await message.reply_text("❌ Недостаточно вариантов для ответа.")
        return
    
    options = random.sample(other_words, 3)
    variants = [target["translation"]] + [opt["translation"] for opt in options]
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
    """Проверяет ответ в игре 'Угадай перевод'."""
    query = update.callback_query
    await query.answer()
    data = query.data
    selected_index = int(data.split("_")[-1])
    
    game_state = context.user_data.get("translate_game")
    if not game_state:
        await query.edit_message_text("❌ Игра устарела. Начните новую командой /game.")
        return
    
    correct = game_state["correct"]
    word = game_state["word"]
    selected = game_state["variants"][selected_index]
    
    if selected == correct:
        await query.edit_message_text(
            f"✅ *Правильно!*\n\n`{word}` — {correct}\n\nМожете сыграть ещё раз через /game.",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            f"❌ *Неправильно.*\n\n`{word}` — {correct}\n\nПопробуйте ещё раз через /game.",
            parse_mode="Markdown"
        )
    context.user_data.pop("translate_game", None)

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
        parse_mode="Markdown"
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
    await update.message.reply_text("Отправьте китайский текст (одним сообщением):")
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
            print(f"⚠️ Слово '{w}' не найдено в словарях, пропущено")

    known_count, queue_count = get_stats(user_id)
    await update.message.reply_text(
        f"✅ *Обработано!*\n\n"
        f"📝 Новых слов: `{new_found}`\n"
        f"📚 Всего в очереди: `{queue_count}`\n"
        f"🎓 Изучено слов: `{known_count}`\n\n"
        f"👉 Для изучения используй `/learn`",
        parse_mode="Markdown"
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
        await query.message.reply_text("Все новые слова изучены! Отлично!")

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

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, сбросить всё", callback_data="confirm_reset"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")
        ]
    ])
    await update.message.reply_text(
        "⚠️ *ВНИМАНИЕ!* Эта команда удалит *ВСЕ* ваши изученные слова и очистит очередь.\n"
        "Уровень HSK будет сброшен на 1.\n\n"
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
        await query.edit_message_text("✅ Все ваши данные сброшены. Можно начинать заново командой /start.")
    else:
        await query.edit_message_text("❌ Сброс отменён.")

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
    await update.message.reply_text("Действие отменено.")
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
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern="^(confirm_reset|cancel_reset)$"))

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
