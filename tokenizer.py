import jieba
from config import STOP_WORDS

def segment_chinese(text: str) -> list[str]:
    words = jieba.lcut(text)
    return [w for w in words if w not in STOP_WORDS and len(w) >= 2]
