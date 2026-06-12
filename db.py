import aiosqlite
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def get_db_path():
    if os.path.exists("/app/data"):
        return "/app/data/user_data.db"
    return "user_data.db"

DB_PATH = get_db_path()

async def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                hsk_level INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS known_words (
                user_id INTEGER,
                word TEXT NOT NULL,
                pinyin TEXT,
                translation TEXT,
                hsk_level INTEGER,
                context TEXT,
                last_reviewed TIMESTAMP,
                difficulty INTEGER DEFAULT 3,
                forgetting_curve REAL DEFAULT 0.85,
                next_review DATE,
                review_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, word)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS words_queue (
                user_id INTEGER,
                word TEXT NOT NULL,
                pinyin TEXT,
                translation TEXT,
                hsk_level INTEGER,
                context TEXT,
                added_at TEXT,
                PRIMARY KEY (user_id, word)
            )
        """)
        await db.commit()

async def get_user_level(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT hsk_level FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 1

async def set_user_level(user_id: int, level: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO users (user_id, hsk_level) VALUES (?, ?)", (user_id, level))
        await db.commit()

async def add_known_word(user_id: int, word: str, pinyin: str, translation: str, hsk_level: int, context: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM words_queue WHERE user_id = ? AND word = ?", (user_id, word))
        now = datetime.now()
        next_review = (now + timedelta(days=1)).date()
        await db.execute("""
            INSERT OR REPLACE INTO known_words
            (user_id, word, pinyin, translation, hsk_level, context, last_reviewed, difficulty, forgetting_curve, next_review, review_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, word, pinyin, translation, hsk_level, context, now, 3, 0.85, next_review, 0))
        await db.commit()

async def add_to_queue(user_id: int, word: str, pinyin: str, translation: str, hsk_level: int, context: str = "") -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM known_words WHERE user_id = ? AND word = ?", (user_id, word)) as cursor:
            if await cursor.fetchone():
                return False
        async with db.execute("SELECT 1 FROM words_queue WHERE user_id = ? AND word = ?", (user_id, word)) as cursor:
            if await cursor.fetchone():
                return False
        now_str = datetime.now().isoformat()
        await db.execute("""
            INSERT INTO words_queue (user_id, word, pinyin, translation, hsk_level, context, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, word, pinyin, translation, hsk_level, context, now_str))
        await db.commit()
        return True

async def get_words_for_review(user_id: int) -> list[dict]:
    today = datetime.now().date()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT word, pinyin, translation, hsk_level, context, difficulty, forgetting_curve, last_reviewed, review_count
            FROM known_words
            WHERE user_id = ? AND next_review <= ?
            ORDER BY next_review ASC
            LIMIT 10
        """, (user_id, today)) as cursor:
            rows = await cursor.fetchall()
    result = []
    for row in rows:
        result.append({
            "word": row[0], "pinyin": row[1], "translation": row[2], "hsk_level": row[3],
            "context": row[4], "difficulty": row[5], "forgetting_curve": row[6],
            "last_reviewed": row[7], "review_count": row[8]
        })
    return result

async def update_review(user_id: int, word: str, success: bool):
    from scheduler import calculate_next_review
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT difficulty, forgetting_curve, review_count FROM known_words WHERE user_id = ? AND word = ?", (user_id, word)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return
        difficulty, forgetting_curve, review_count = row
        next_review, new_review_count = calculate_next_review(success, difficulty, forgetting_curve, review_count)
        await db.execute("""
            UPDATE known_words
            SET last_reviewed = ?, next_review = ?, review_count = ?
            WHERE user_id = ? AND word = ?
        """, (datetime.now(), next_review, new_review_count, user_id, word))
        await db.commit()

async def get_stats(user_id: int) -> tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM known_words WHERE user_id = ?", (user_id,)) as cursor:
            known_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM words_queue WHERE user_id = ?", (user_id,)) as cursor:
            queue_count = (await cursor.fetchone())[0]
    return known_count, queue_count

async def is_word_known(user_id: int, word: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM known_words WHERE user_id = ? AND word = ?", (user_id, word)) as cursor:
            return await cursor.fetchone() is not None

async def get_all_known_words(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT word, translation
            FROM known_words
            WHERE user_id = ? AND translation != ''
            ORDER BY word
        """, (user_id,)) as cursor:
            rows = await cursor.fetchall()
    return [{"word": row[0], "translation": row[1]} for row in rows]

async def bump_queue_word(user_id: int, word: str):
    now_str = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE words_queue SET added_at = ? WHERE user_id = ? AND word = ?",
                         (now_str, user_id, word))
        await db.commit()

async def get_next_new_word(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT word, added_at FROM words_queue WHERE user_id = ? ORDER BY added_at ASC", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                return None
            word = rows[0][0]
        async with db.execute("SELECT word, pinyin, translation, hsk_level, context FROM words_queue WHERE user_id = ? AND word = ?", (user_id, word)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"word": row[0], "pinyin": row[1] or "", "translation": row[2] or "", "hsk_level": row[3] or 0, "context": row[4] or ""}
        return None

async def reset_user_data(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM known_words WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM words_queue WHERE user_id = ?", (user_id,))
        await db.execute("UPDATE users SET hsk_level = 1 WHERE user_id = ?", (user_id,))
        await db.commit()
