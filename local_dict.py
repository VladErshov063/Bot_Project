import re
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class ChineseDictionary:
    """Локальный словарь на основе CC-CEDICT."""

    def __init__(self, dict_path: str = "hsk_data/cedict_1_0_ts_utf-8_mdbg.txt"):
        self.dict_path = Path(dict_path)
        self.entries = {}
        self._load()

    def _load(self):
        if not self.dict_path.exists():
            logger.warning(f"Файл словаря не найден: {self.dict_path}")
            return
        try:
            with open(self.dict_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    match = re.match(r"^(\S+)\s+(\S+)\s*\[(.*?)\]\s*/(.*)", line)
                    if match:
                        simplified = match.group(1)
                        traditional = match.group(2)
                        pinyin = match.group(3)
                        translation = match.group(4).split("/")[0]
                        self.entries[simplified] = (self._format_pinyin(pinyin), translation)
                        if simplified != traditional:
                            self.entries[traditional] = (self._format_pinyin(pinyin), translation)
            logger.info(f"Загружено {len(self.entries)} слов из локального словаря")
        except Exception as e:
            logger.error(f"Ошибка загрузки словаря: {e}")

    def _format_pinyin(self, pinyin: str) -> str:
        """Конвертирует пиньинь с цифрами (например, 'tian1' или 'zuó tian1') в тоновые символы."""
        tone_map = {
            'a1': 'ā', 'a2': 'á', 'a3': 'ǎ', 'a4': 'à',
            'e1': 'ē', 'e2': 'é', 'e3': 'ě', 'e4': 'è',
            'i1': 'ī', 'i2': 'í', 'i3': 'ǐ', 'i4': 'ì',
            'o1': 'ō', 'o2': 'ó', 'o3': 'ǒ', 'o4': 'ò',
            'u1': 'ū', 'u2': 'ú', 'u3': 'ǔ', 'u4': 'ù',
            'v1': 'ǖ', 'v2': 'ǘ', 'v3': 'ǚ', 'v4': 'ǜ',
        }
        
        def convert_syllable(syl: str) -> str:
            if not syl:
                return syl
            last_char = syl[-1]
            if last_char not in "1234":
                return syl
            tone = last_char
            base = syl[:-1]
            if 'a' in base:
                pos = base.rfind('a')
            elif 'e' in base:
                pos = base.rfind('e')
            elif 'o' in base:
                pos = base.rfind('o')
            elif 'u' in base:
                pos = base.rfind('u')
            elif 'i' in base:
                pos = base.rfind('i')
            else:
                pos = -1
            if pos != -1:
                vowel = base[pos]
                key = vowel + tone
                if key in tone_map:
                    return base[:pos] + tone_map[key] + base[pos+1:]
            return base
        
        syllables = pinyin.split()
        converted = [convert_syllable(s) for s in syllables]
        return ' '.join(converted)

    def lookup(self, word: str):
        """Возвращает (пиньинь, перевод) или (None, None)."""
        return self.entries.get(word, (None, None))
