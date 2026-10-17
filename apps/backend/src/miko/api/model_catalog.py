"""Candidate labels for provider model lists; connection tests verify usability."""
import re
from urllib.parse import urlsplit, urlunsplit


def model_kind(row):
    name = row['id'].lower().rsplit('/', 1)[-1]
    metadata = [str(row.get('type', '')).lower(), str(row.get('task', '')).lower()]
    capabilities = row.get('capabilities', [])
    if isinstance(capabilities, dict):
        metadata.extend(str(k).lower() for k, v in capabilities.items() if v)
    elif isinstance(capabilities, list):
        metadata.extend(str(v).lower() for v in capabilities)
    if any('rerank' in value or value == 'text-ranking' for value in metadata):
        return 'rerank'
    if any('embed' in value or value == 'feature-extraction' for value in metadata):
        return 'embedding'
    if any(value in ('llm', 'chat', 'text-generation') for value in metadata):
        return 'chat'
    if 'rerank' in name or row['id'].lower().startswith('cross-encoder/'):
        return 'rerank'
    if ('embed' in name or re.match(r'^(voyage-|bge-|gte-|e5-|multilingual-e5-|text2vec|m3e-)', name)
            or name in ('all-minilm-l6-v2', 'all-mpnet-base-v2')):
        return 'embedding'
    if re.search(r'(^|[-_.])(whisper|tts|dall-e|stable-diffusion|flux)([-_.]|$)', name):
        return 'other'
    return 'chat'


def model_list_base(api_base):
    parts = urlsplit(api_base.rstrip('/'))
    path = parts.path
    if '/services/rerank/' in path:
        path = '/compatible-mode/v1'
    elif path.endswith(('/rerank', '/reranking')):
        path = path.rsplit('/', 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def rerank_endpoint(api_base):
    parts = urlsplit(api_base.rstrip('/'))
    if '/services/rerank/' in parts.path or parts.path.endswith(('/rerank', '/reranking')):
        return api_base.rstrip('/')
    if parts.path.endswith('/compatible-mode/v1'):
        path = '/api/v1/services/rerank/text-rerank/text-rerank'
    else:
        path = parts.path + '/rerank'
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))
