import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ConversationHandler, ContextTypes
)
from config import TOKEN
from db import (
    init_db, get_user_level, set_user_level, add_known_word, add_to_queue,
    get_next_new_word, get_words_for_review, update_review, get_stats
)
from hsk_loader import load_hsk_dicts
from tokenizer import segment_chinese
from telegram.request import HTTPXRequest

HSK_DICT = load_hsk_dicts()

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TEXT_INPUT = 1

# ---------- обработчики ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    level = get_user_level(user_id)
    await update.message.reply_text(
        f"Привет! Я помогу тебе учить китайские слова.\n"
        f"Твой текущий уровень HSK: {level}.\n"
        f"Изменить уровень можно командой /level <1-6>\n"
        f"Отправь мне китайский текст командой /add_text, и я найду новые слова."
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
        info = HSK_DICT.get(w)
        if info:
            pinyin, translation, word_level = info
            if word_level > user_level:
                add_to_queue(user_id, w, pinyin, translation, word_level, context=text)
                new_found += 1
        else:
            add_to_queue(user_id, w, "", "", 0, context=text)
            new_found += 1

    known_count, queue_count = get_stats(user_id)
    await update.message.reply_text(
        f"Обработано. Найдено новых слов: {new_found}.\n"
        f"Всего в очереди на изучение: {queue_count}.\n"
        f"Изучено слов: {known_count}.\n"
        f"Для изучения используй /learn"
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
        f"*Контекст:* {word_data['context'][:100]}..."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Знаю", callback_data=f"learn_know_{word_data['word']}")],
        [InlineKeyboardButton("❌ Не знаю", callback_data="learn_skip")]
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
    else:
        await query.edit_message_text("Слово пропущено. Оно останется в очереди.")

    next_word = get_next_new_word(user_id)
    if next_word:
        context.user_data["current_learn_word"] = next_word["word"]
        card_text = (
            f"📖 *Новое слово*\n\n"
            f"`{next_word['word']}`\n"
            f"*Пиньинь:* {next_word['pinyin']}\n"
            f"*Перевод:* {next_word['translation']}\n"
            f"*HSK уровень:* {next_word['hsk_level']}\n"
            f"*Контекст:* {next_word['context'][:100]}..."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Знаю", callback_data=f"learn_know_{next_word['word']}")],
            [InlineKeyboardButton("❌ Не знаю", callback_data="learn_skip")]
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
        f"*Контекст:* {w['context'][:100] if w['context'] else '—'}"
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

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

def main():
    init_db()
    proxy_url = os.environ.get("PROXY_URL")
    if proxy_url:
        request = HTTPXRequest(
            proxy=proxy_url,
            connect_timeout=30,
            read_timeout=30,
            write_timeout=30
        )
    else:
        request = HTTPXRequest(
            connect_timeout=30,
            read_timeout=30,
            write_timeout=30
        )
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
    app.add_handler(CommandHandler("cancel", cancel))

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
