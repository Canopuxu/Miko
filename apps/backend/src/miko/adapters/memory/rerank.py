"""Shared transport for the two supported text reranking response formats."""
from miko.settings import RerankConfig, legacy_rerank
from urllib.parse import urlsplit


def infer_format(api_base):
    return 'dashscope' if '/services/rerank/' in urlsplit(api_base).path else 'standard'


async def rank(client, config, query, documents, top_n):
    if not isinstance(config, RerankConfig):
        config = legacy_rerank(config)
    if config.format == 'dashscope':
        payload = {'model': config.model, 'input': {'query': query, 'documents': documents},
                   'parameters': {'top_n': top_n}}
    else:
        payload = {'model': config.model, 'query': query, 'documents': documents, 'top_n': top_n}
    response = await client.post(config.api_base,
        headers={'Authorization': 'Bearer ' + config.api_key.get_secret_value()}, json=payload)
    response.raise_for_status()
    value = response.json()
    rows = value['output']['results'] if config.format == 'dashscope' else value['results']
    for row in rows:
        if type(row['index']) is not int or not 0 <= row['index'] < len(documents):
            raise ValueError('Invalid rerank result index')
        row['relevance_score'] = float(row['relevance_score'])
    return rows
