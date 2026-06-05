import sqlite3
import os
from datetime import datetime, timedelta

def get_db_path():
    if os.path.exists("/app/data"):
        return "/app/data/user_data.db"
    return "user_data.db"

DB_PATH = get_db_path()

def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            hsk_level INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS words_queue (
            user_id INTEGER,
            word TEXT NOT NULL,
            pinyin TEXT,
            translation TEXT,
            hsk_level INTEGER,
            context TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, word)
        )
    """)
    conn.commit()
    conn.close()

def get_user_level(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT hsk_level FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 1

def set_user_level(user_id: int, level: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, hsk_level) VALUES (?, ?)", (user_id, level))
    conn.commit()
    conn.close()

def add_known_word(user_id: int, word: str, pinyin: str, translation: str, hsk_level: int, context: str = ""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM words_queue WHERE user_id = ? AND word = ?", (user_id, word))
    now = datetime.now()
    next_review = (now + timedelta(days=1)).date()
    c.execute("""
        INSERT OR REPLACE INTO known_words
        (user_id, word, pinyin, translation, hsk_level, context, last_reviewed, difficulty, forgetting_curve, next_review, review_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, word, pinyin, translation, hsk_level, context, now, 3, 0.85, next_review, 0))
    conn.commit()
    conn.close()

def add_to_queue(user_id: int, word: str, pinyin: str, translation: str, hsk_level: int, context: str = "") -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM known_words WHERE user_id = ? AND word = ?", (user_id, word))
    if c.fetchone():
        conn.close()
        return False
    c.execute("SELECT 1 FROM words_queue WHERE user_id = ? AND word = ?", (user_id, word))
    if c.fetchone():
        conn.close()
        return False
    c.execute("""
        INSERT INTO words_queue (user_id, word, pinyin, translation, hsk_level, context)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, word, pinyin, translation, hsk_level, context))
    conn.commit()
    conn.close()
    return True

def get_next_new_word(user_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT word, pinyin, translation, hsk_level, context
        FROM words_queue
        WHERE user_id = ?
        ORDER BY added_at ASC
        LIMIT 1
    """, (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"word": row[0], "pinyin": row[1], "translation": row[2], "hsk_level": row[3], "context": row[4]}
    return None

def get_words_for_review(user_id: int) -> list[dict]:
    today = datetime.now().date()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT word, pinyin, translation, hsk_level, context, difficulty, forgetting_curve, last_reviewed, review_count
        FROM known_words
        WHERE user_id = ? AND next_review <= ?
        ORDER BY next_review ASC
        LIMIT 10
    """, (user_id, today))
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        result.append({
            "word": row[0], "pinyin": row[1], "translation": row[2], "hsk_level": row[3],
            "context": row[4], "difficulty": row[5], "forgetting_curve": row[6],
            "last_reviewed": row[7], "review_count": row[8]
        })
    return result

def update_review(user_id: int, word: str, success: bool):
    from scheduler import calculate_next_review
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT difficulty, forgetting_curve, review_count FROM known_words WHERE user_id = ? AND word = ?", (user_id, word))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    difficulty, forgetting_curve, review_count = row
    next_review, new_review_count = calculate_next_review(success, difficulty, forgetting_curve, review_count)
    c.execute("""
        UPDATE known_words
        SET last_reviewed = ?, next_review = ?, review_count = ?
        WHERE user_id = ? AND word = ?
    """, (datetime.now(), next_review, new_review_count, user_id, word))
    conn.commit()
    conn.close()

def get_stats(user_id: int) -> tuple[int, int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM known_words WHERE user_id = ?", (user_id,))
    known_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM words_queue WHERE user_id = ?", (user_id,))
    queue_count = c.fetchone()[0]
    conn.close()
    return known_count, queue_count

def is_word_known(user_id: int, word: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM known_words WHERE user_id = ? AND word = ?", (user_id, word))
    found = c.fetchone() is not None
    conn.close()
    return found

def get_all_known_words(user_id: int) -> list[dict]:
    """Возвращает список всех изученных слов с переводом."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT word, translation
        FROM known_words
        WHERE user_id = ? AND translation != ''
        ORDER BY word
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"word": row[0], "translation": row[1]} for row in rows]

def bump_queue_word(user_id: int, word: str):
    """Перемещает слово в конец очереди (обновляет added_at)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE words_queue SET added_at = ? WHERE user_id = ? AND word = ?",
              (datetime.now(), user_id, word))
    conn.commit()
    conn.close()
