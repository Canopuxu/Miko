"""Message segments and semantic bubbles adapted from MIT Tulpa MCP chat/turns.

Original CQ-looking strings remain literal text. Assets use local opaque IDs.
"""
import json
import re


FACES = []  # Native face sending is currently disabled; keep incoming name lookup.
FACE_NAMES = {'14': '微笑', '76': '赞', '182': '笑哭'}


def update_face_catalog(catalog):
    """Refresh incoming face names without enabling outgoing candidates."""
    faces = {}
    for pack in catalog['packs']:
        for face in pack['emojis']:
            face_id = str(face['q_sid'])
            if not face_id.isascii() or not face_id.isdigit():
                continue  # Unicode emoji are sent as ordinary text, not OneBot face IDs.
            name = face['q_des'].lstrip('/').strip()
            if name:
                faces[face_id] = name
    if not faces:
        raise ValueError('QQ 表情目录为空')
    FACE_NAMES.clear()
    FACE_NAMES.update(faces)


def parse_message(source):
    if isinstance(source, str):
        source = [{'type': 'text', 'data': {'text': source}}]
    parts, segments = [], []
    for item in source[:100] if isinstance(source, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get('data'), dict):
            continue
        kind, data = item.get('type'), item['data']
        if kind == 'text':
            text = str(data.get('text', ''))[:12000]
            parts.append(text)
            segments.append({'type': 'text', 'text': text})
        elif kind == 'reply':
            mid = str(data.get('id', ''))
            if re.fullmatch(r'-?\d{1,20}', mid):
                segments.append({'type': 'reply', 'message_id': int(mid)})
                parts.append('[引用]')
        elif kind == 'face':
            face = str(data.get('id', ''))
            name = FACE_NAMES.get(face, 'QQ表情 ' + face)
            segments.append({'type': 'face', 'face_id': face, 'name': name})
            parts.append('[' + name + ']')
        elif kind in ('image', 'mface'):
            # Adapted from Tulpa image_ref: preserve protocol references, not local paths.
            ref = {}
            if isinstance(data.get('url'), str) and len(data['url']) <= 4096:
                ref['url'] = data['url']
            if isinstance(data.get('file'), str) and re.fullmatch(r'[A-Za-z0-9_{}.-]{1,180}', data['file']) and '..' not in data['file']:
                ref['file'] = data['file']
            summary = str(data.get('summary', '')).strip().strip('[]')[:120] if kind == 'mface' else ''
            segments.append({'type': 'image', 'ref': ref, 'content_status': 'not_read',
                             **({'name': summary} if summary else {})})
            parts.append('[表情包' + ('：' + summary if summary else '') + ']' if kind == 'mface' else '[图片]')
        elif kind == 'at':
            qq = str(data.get('qq', ''))[:30]
            segments.append({'type': 'at', 'qq': qq})
            parts.append('@' + qq)
        else:
            label = {'record': '语音', 'video': '视频', 'file': '文件', 'forward': '合并转发', 'share': '分享卡片'}.get(kind, '非文本消息')
            segments.append({'type': str(kind), 'name': label, 'content_status': 'not_read'})
            parts.append('[' + label + ']')
    return {'text': ''.join(parts)[:12000], 'segments': segments}


def model_message(payload):
    """Keep media references out of model text; image bytes are attached separately."""
    return [{k: v for k, v in item.items() if k != 'ref'} for item in payload.get('segments', [])]


def parse_bubbles(raw, records, stickers, max_chars=100, *, group=False):
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {'bubbles'}:
        raise ValueError('回复需要 bubbles JSON 对象')
    bubbles = value['bubbles']
    if not isinstance(bubbles, list) or not 0 <= len(bubbles) <= 3:
        raise ValueError('一次回复最多 3 个语义气泡，无需回复时使用空数组')
    quotes = {r['message_id'] for r in records if 'message_id' in r}
    faces = {face['face_id'] for face in FACES}
    assets = {s['sticker_id'] for s in stickers}
    total = 0
    current_ids = set()
    current_authors = set()
    for record in reversed(records):
        if record.get('author', {}).get('kind') in ('miko', 'owner'):
            break
        if record.get('author', {}).get('kind') == 'contact':
            current_ids.add(record.get('message_id'))
            current_authors.add(record['author'].get('id'))
    ordinary_reply = not group or len(current_authors) <= 1
    for i, bubble in enumerate(bubbles):
        if not isinstance(bubble, dict):
            raise ValueError('气泡格式无效')
        kind = bubble.get('kind')
        field = {'text': 'text', 'face': 'face_id', 'sticker': 'sticker_id'}.get(kind)
        if not field or set(bubble) - {'kind', field, 'quote_message_id', 'introduces_miko'}:
            raise ValueError('气泡类型或字段无效')
        if 'introduces_miko' in bubble and (kind != 'text' or type(bubble['introduces_miko']) is not bool):
            raise ValueError('介绍标记只能用于文字气泡，并需要布尔值')
        content = bubble.get(field)
        if not isinstance(content, str) or not content.strip():
            raise ValueError('气泡内容为空')
        if kind == 'text':
            bubble['text'] = content.strip()
            total += len(bubble['text'])
        elif content not in (faces if kind == 'face' else assets):
            raise ValueError('表情不在本轮候选中')
        if 'quote_message_id' in bubble:
            mid = bubble['quote_message_id']
            if ordinary_reply and type(mid) is int and mid in current_ids:
                bubble.pop('quote_message_id')
                continue
            if i or type(mid) is not int or mid not in quotes:
                raise ValueError('引用需要本轮记录中的消息编号，且只用于首个气泡')
    if total > max_chars:
        raise ValueError(f'本轮文字总长度超过 {max_chars} 字，未发送')
    latest = next((r for r in reversed(records) if r.get('author', {}).get('kind') == 'contact'), None)
    if latest and ordinary_reply:
        # Replying to the latest message already identifies the target in ordinary chat.
        for bubble in bubbles:
            if bubble.get('quote_message_id') == latest.get('message_id'):
                bubble.pop('quote_message_id', None)
    return bubbles


def bubble_delay(previous):
    # Tulpa ChatTurns pacing, preserved for asynchronous dispatch.
    return min(4., 1.2 + len(previous.get('text', '')) * .035)


OUTPUT_SCHEMA = {'bubbles': [
    {'kind': 'text', 'text': 'string', 'quote_message_id': 'optional integer', 'introduces_miko': 'optional boolean'},
    {'kind': 'face', 'face_id': 'candidate string'},
    {'kind': 'sticker', 'sticker_id': 'candidate string'},
]}
