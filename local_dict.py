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
                    if line.startswith("#"):
                        continue
                    match = re.match(r"^(\S+)\s+\S+\s*\[(.*?)\]\s*/(.*)/", line)
                    if match:
                        word = match.group(1)
                        pinyin = match.group(2)
                        translation = match.group(3).split("/")[0]
                        self.entries[word] = (self._format_pinyin(pinyin), translation)
            logger.info(f"Загружено {len(self.entries)} слов из локального словаря")
        except Exception as e:
            logger.error(f"Ошибка загрузки словаря: {e}")

    def _format_pinyin(self, pinyin: str) -> str:
        """Конвертирует пиньинь с цифрами в символы с тонами."""
        tone_map = {
            'a1': 'ā', 'a2': 'á', 'a3': 'ǎ', 'a4': 'à',
            'e1': 'ē', 'e2': 'é', 'e3': 'ě', 'e4': 'è',
            'i1': 'ī', 'i2': 'í', 'i3': 'ǐ', 'i4': 'ì',
            'o1': 'ō', 'o2': 'ó', 'o3': 'ǒ', 'o4': 'ò',
            'u1': 'ū', 'u2': 'ú', 'u3': 'ǔ', 'u4': 'ù',
            'v1': 'ǖ', 'v2': 'ǘ', 'v3': 'ǚ', 'v4': 'ǜ',
        }
        result = pinyin
        for key, val in tone_map.items():
            result = result.replace(key, val)
        result = result.replace('v', 'ü')
        return result

    def lookup(self, word: str):
        """Возвращает (пиньинь, перевод) или (None, None)."""
        return self.entries.get(word, (None, None))
