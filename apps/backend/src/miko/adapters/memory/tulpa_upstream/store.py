import jieba

tokenizer = jieba.Tokenizer()

def configure_cache(path):
    path.mkdir(parents=True, exist_ok=True)
    tokenizer.tmp_dir = str(path)

def tokens(text):
    return " ".join(t.lower() for t in tokenizer.cut_for_search(text) if t.strip())
