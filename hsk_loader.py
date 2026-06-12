import json
from config import HSK_FILES

def load_hsk_dicts() -> dict[str, tuple[str, str, int]]:
    hsk_dict = {}

    for level, path in HSK_FILES.items():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for key, words in data.items():
                    for w in words:
                        word = w["word"]
                        pinyin = w.get("pinyin", "")
                        translation = w.get("translation", "")
                        hsk_dict[word] = (pinyin, translation, level)
        except FileNotFoundError:
            print(f"Предупреждение: файл {path} не найден, уровень {level} пропущен")
        except Exception as e:
            print(f"Ошибка при загрузке {path}: {e}")

    return hsk_dict
