"""Select expression feedback independently of person-fact retrieval."""
import logging

import httpx
from miko.adapters.memory.rerank import rank
from miko.storage.search import keywords


async def select_feedback(provider, query, rows):
    fixed = [r for r in rows if r['scope'] != 'example']
    examples = [r for r in rows if r['scope'] == 'example']
    wanted = set(keywords(query))
    selected = [r for r in examples if wanted & set(keywords(r['context'] or r['reply']))][:3]
    if examples and provider and query.strip():
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                ranked = await rank(client, provider, query,
                    [r['context'] or r['reply'] for r in examples], min(3, len(examples)))
                selected = [examples[r['index']] for r in ranked
                            if r['relevance_score'] >= .5]
        except (httpx.HTTPError, KeyError, ValueError):
            logging.getLogger(__name__).warning('Feedback reranking unavailable; keeping local matching and scoped feedback')
    result, seen = [], set()
    for row in fixed + selected:
        key = ''.join(row['feedback'].split())
        if key in seen:
            continue
        seen.add(key)
        result.append({k: row[k] for k in ('reply', 'feedback', 'scope')})
    return result[:8]
