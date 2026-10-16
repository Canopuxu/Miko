"""Miko image selection and model connection for the upstream sticker library."""
import asyncio
import base64
import json
import threading

from miko.adapters.qq.images import download, pixels, MAX_BYTES
from miko.settings import REPO_ROOT


async def message_image(qq, key, mid, index):
    if not qq.client:
        raise ValueError('请先连接 QQ，再读取聊天图片')
    event = qq.store.get_event(key, mid)
    if not event or event['recalled'] or not qq.store.friend(key):
        raise ValueError('找不到这张聊天图片，或原消息已撤回')
    value = await qq.client.message(*key, mid, known_own=event['origin'] in ('miko', 'manual'))
    sources = [s.get('data', {}) for s in value.get('message', []) if s.get('type') in ('image', 'mface')]
    if not 0 <= index < len(sources):
        raise ValueError('原消息没有这张图片')
    source = sources[index]
    file = source.get('file', '')
    if file.startswith('base64://'):
        if len(file) > ((MAX_BYTES + 2) // 3) * 4 + 9:
            raise ValueError('图片超过 6 MiB 上限')
        raw = base64.b64decode(file[9:], validate=True)
    else:
        cancel = threading.Event()
        try:
            raw = await asyncio.to_thread(download, source.get('url', ''), cancel)
        finally:
            cancel.set()
    return raw


async def describe(qq, raw):
    _, frames = await asyncio.to_thread(pixels, raw)
    path = REPO_ROOT / 'config/defaults/qq-sticker-note.txt'
    prompt = path.read_text(encoding='utf-8')
    provider = qq.chat.qq_provider or qq.chat.provider
    try:
        value = await qq.chat.complete_memory(prompt, json.dumps(dict(json_output_format={
            'description': 'string', 'tags': ['string'], 'emotion': 'string', 'usage': 'string',
            'avoid': 'string', 'uncertainty': 'string'})), provider=provider, json_output=True, image_inputs=frames)
        result = json.loads(value['text'])
        if not isinstance(result, dict) or not isinstance(result.get('description'), str):
            raise ValueError('表情含义没有生成成功，请直接填写或重试')
        uncertainty = str(result.get('uncertainty') or '').strip()
        result['uncertainty'] = '' if uncertainty.lower() in ('无', '无。', '没有', 'none', 'null', '不适用') else uncertainty
        return result
    except ValueError as exc:
        if 'HTTP 400' in str(exc) or 'HTTP 415' in str(exc) or 'HTTP 422' in str(exc):
            raise ValueError('当前聊天模型未接受这张表情图片，可以直接填写含义，或在设置中换用支持图片的模型') from None
        raise
