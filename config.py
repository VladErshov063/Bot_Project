import os

TOKEN = os.environ.get("TOKEN")
HSK_FILES = {
    1: "hsk_data/hsk1_.json",
    2: "hsk_data/hsk2_.json",
    3: "hsk_data/hsk3_.json",
    4: "hsk_data/hsk4_.json",
    5: "hsk_data/hsk5_.json",
    6: "hsk_data/hsk6_.json",
}

def _load_stop_words(file_path: str = "hsk_data/stopwords.txt") -> set:
    stop_words = set()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word:
                    stop_words.add(word)
    except FileNotFoundError:
        stop_words = {
            "的", "了", "在", "我", "你", "他", "她", "它", "我们", "你们", "他们",
            "这", "那", "是", "有", "和", "也", "都", "不", "就", "但", "所以",
            "因为", "然后", "而且", "或者", "如果", "虽然", "但是", "可是"
        }
    return stop_words

STOP_WORDS = _load_stop_words()
