"""Select expression feedback independently of person-fact retrieval."""
import logging
from urllib.parse import urlsplit

import httpx


async def select_feedback(provider, query, rows):
    fixed = [r for r in rows if r['scope'] != 'example']
    examples = [r for r in rows if r['scope'] == 'example']
    selected = []
    if examples and provider and query.strip():
        url = urlsplit(provider.api_base)
        endpoint = url.scheme + '://' + url.netloc + '/api/v1/services/rerank/text-rerank/text-rerank'
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(endpoint,
                    headers={'Authorization': 'Bearer ' + provider.api_key.get_secret_value()},
                    json={'model': 'qwen3.7-text-rerank', 'input': {
                        'query': query,
                        'documents': [r['context'] or r['reply'] for r in examples]},
                        'parameters': {'top_n': min(3, len(examples))}})
                response.raise_for_status()
                logging.getLogger(__name__).debug('Feedback scores: %s', response.json()['output']['results'])
                selected = [examples[r['index']] for r in response.json()['output']['results']
                            if r['relevance_score'] >= .60]
        except (httpx.HTTPError, KeyError, ValueError):
            logging.getLogger(__name__).warning('Feedback reranking unavailable; using explicitly scoped feedback')
    result, seen = [], set()
    for row in fixed + selected:
        key = ''.join(row['feedback'].split())
        if key in seen:
            continue
        seen.add(key)
        result.append({k: row[k] for k in ('reply', 'feedback', 'scope')})
    return result[:8]
