"""Chinese bigram / ASCII matching adapted from MIT Tulpa StickerLibrary.

FTS queries use Tulpa reply_history's quoted OR terms and bm25 ranking.
"""
import re


def terms(text, limit=2000):
    out = set()
    for word in re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+', str(text).casefold()[:limit]):
        out.update([word] if word.isascii() or len(word) < 2 else [word[i:i+2] for i in range(len(word)-1)])
    return out


def fts_query(text):
    return ' OR '.join('"' + word.replace('"', '""') + '"' for word in sorted(terms(text))[:48])
