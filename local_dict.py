import re
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class ChineseDictionary:
    """Локальный словарь на основе CC-CEDICT с разрешением перекрёстных ссылок и очисткой перевода."""

    def __init__(self, dict_path: str = "hsk_data/cedict_1_0_ts_utf-8_mdbg.txt"):
        self.dict_path = Path(dict_path)
        self.entries = {}
        self._load()

    def _load(self):
        if not self.dict_path.exists():
            logger.warning(f"Файл словаря не найден: {self.dict_path}")
            return

        raw_entries = {}
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
                        pinyin_raw = match.group(3)
                        raw_trans = match.group(4).split("/")[0]
                        pinyin = self._format_pinyin(pinyin_raw)
                        raw_entries[simplified] = (pinyin, raw_trans)
                        if simplified != traditional:
                            raw_entries[traditional] = (pinyin, raw_trans)
            logger.info(f"Загружено {len(raw_entries)} записей из словаря")
        except Exception as e:
            logger.error(f"Ошибка загрузки словаря: {e}")
            return

        self.entries = {}
        for word, (pinyin, raw_trans) in raw_entries.items():
            trans = self._resolve_reference(raw_trans, raw_entries)
            trans = self._clean_translation(trans)
            self.entries[word] = (pinyin, trans)
        logger.info(f"Разрешены ссылки и очищены переводы, итого записей: {len(self.entries)}")

    def _resolve_reference(self, translation: str, raw_entries: dict) -> str:
        """Итеративно раскрывает ссылки вида 'see X' (без рекурсии)."""
        visited = set()
        current = translation
        while True:
            if current in visited:
                break
            visited.add(current)

            match = re.match(r"^see\s+(\S+?)(?:\||\s|$)", current)
            if not match:
                break

            target = match.group(1)

            if '|' in target:
                target = target.split('|')[1]

            target_clean = re.sub(r'[0-9]', '', target)
            target_clean = re.sub(r'[a-zA-Z\s]+$', '', target_clean)

            if target_clean in raw_entries:
                _, current = raw_entries[target_clean]
                continue
            else:
                current = current.replace("see ", "→ ")
                break

        return current[:500]

    def _clean_translation(self, text: str) -> str:
        """Удаляет из перевода китайские иероглифы и пиньинь, оставляя только английский текст."""
        pattern = r'[一-龥]+(?:\|[一-龥]+)?[a-zA-Z0-9\sāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]+'
        cleaned = re.sub(pattern, '', text)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        cleaned = cleaned.strip(' .,;:!?')
        return cleaned

    def _format_pinyin(self, pinyin: str) -> str:
        """Конвертирует пиньинь с цифрами в тоновые символы."""
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
            for vowel in ['a', 'e', 'o', 'u', 'i']:
                if vowel in base:
                    pos = base.rfind(vowel)
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
