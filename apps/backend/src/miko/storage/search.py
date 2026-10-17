"""Tulpa query words and legacy Chinese bigram indexing for existing UI searches."""
import re
from miko.adapters.memory.tulpa_upstream.retrieval import words

STOP = set('之前 关于 怎么 怎样 什么 哪些 有没有 最近 过去 一周 一下 事情 讨论 记录 聊天 帮我 查找 找到 请问 我 你 他 她 的 了 是 在 和 与 里 中 过 吗 呢 地 得 做 提到 曾经 一件 一个 时候 我们 他们 内容 微信 qq wechat'.split())


def keywords(text):
    return [word for word in words(text) if word not in STOP]


def terms(text, limit=2000):
    out = set()
    for word in re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+', str(text).casefold()[:limit]):
        out.update([word] if word.isascii() or len(word) < 2 else [word[i:i+2] for i in range(len(word)-1)])
    return out


def fts_query(text):
    return ' OR '.join('"' + word.replace('"', '""') + '"' for word in sorted(terms(text))[:48])
