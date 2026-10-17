"""Selected MIT Tulpa retrieval functions; function bodies are unchanged.

Source: chatlocal/retrieval.py and chatlocal/reply_history.py, 7b603d3.
Miko supplies a persistent messages/FTS projection of its own material.
"""
import json
import re
from dataclasses import dataclass, field

from .store import tokens

@dataclass
class Plan:
    mode: str = 'search'
    keywords: list = field(default_factory=list)
    start: int | None = None
    end: int | None = None
    platforms: list = field(default_factory=lambda: ['qq', 'wechat'])
    conversations: list = field(default_factory=list)
    snapshot_max_id: int | None = None

def scope_sql(plan, alias='m'):
    clauses = [f"{alias}.platform IN ({','.join('?' for _ in plan.platforms)})"]
    values = list(plan.platforms)
    if plan.snapshot_max_id is not None:
        clauses.append(f'{alias}.id<=?');values.append(plan.snapshot_max_id)
    if plan.start is not None:
        clauses.append(f'{alias}.timestamp>=?'); values.append(plan.start)
    if plan.end is not None:
        clauses.append(f'{alias}.timestamp<?'); values.append(plan.end)
    if plan.conversations:
        parts = []
        for selection in plan.conversations:
            platform, conversation_id = json.loads(selection)
            parts.append(f'({alias}.platform=? AND {alias}.conversation_id=?)')
            values += [platform, conversation_id]
        clauses.append('(' + ' OR '.join(parts) + ')')
    return ' AND '.join(clauses), values

def words(text):
    return list(dict.fromkeys(w for w in tokens(text).split()
                             if len(w) >= 2 and re.search(r'\w', w)))[:24]

def text_matches(db, where, params, text, extra='', args=(), limit=40):
    """Use the archive's existing FTS index for both turns and owner snippets."""
    terms = words(text)
    if not terms:
        return []
    match = ' OR '.join('"' + w.replace('"', '""') + '"' for w in terms)
    return [dict(r) for r in db.execute(f'''SELECT m.* FROM message_fts JOIN messages m ON m.id=message_fts.rowid
      WHERE message_fts MATCH ? AND {where} {extra} ORDER BY bm25(message_fts) LIMIT ?''',
      [match, *params, *args, limit])]
