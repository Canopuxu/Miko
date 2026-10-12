"""Message segments and semantic bubbles adapted from MIT Tulpa MCP chat/turns.

Original CQ-looking strings remain literal text. Assets use local opaque IDs.
"""
import json
import re


FACES = [{'face_id': '14', 'name': '微笑'}, {'face_id': '76', 'name': '赞'},
         {'face_id': '182', 'name': '笑哭'}]
FACE_NAMES = {item['face_id']: item['name'] for item in FACES}


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
            segments.append({'type': 'image', 'ref': ref, 'content_status': 'not_read'})
            parts.append('[图片]')
        elif kind == 'at':
            qq = str(data.get('qq', ''))[:30]
            segments.append({'type': 'at', 'qq': qq})
            parts.append('@' + qq)
        else:
            label = {'record': '语音', 'video': '视频', 'file': '文件', 'forward': '合并转发'}.get(kind, '非文本消息')
            segments.append({'type': str(kind), 'name': label, 'content_status': 'not_read'})
            parts.append('[' + label + ']')
    return {'text': ''.join(parts)[:12000], 'segments': segments}


def model_message(payload):
    """Keep remote media URLs outside model input until image reading is configured."""
    return [{k: v for k, v in item.items() if k != 'ref'} for item in payload.get('segments', [])]


def parse_bubbles(raw, records, stickers, max_chars=80):
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {'bubbles'}:
        raise ValueError('回复需要 bubbles JSON 对象')
    bubbles = value['bubbles']
    if not isinstance(bubbles, list) or not 1 <= len(bubbles) <= 3:
        raise ValueError('一次回复需要 1–3 个语义气泡')
    quotes = {r['message_id'] for r in records if 'message_id' in r}
    faces = set(FACE_NAMES)
    assets = {s['sticker_id'] for s in stickers}
    total = 0
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
            if i or type(mid) is not int or mid not in quotes:
                raise ValueError('引用需要本轮记录中的消息编号，且只用于首个气泡')
    if total > max_chars:
        raise ValueError('本轮文字总长度超过 80 字，未发送')
    return bubbles


def bubble_delay(previous):
    # Tulpa ChatTurns pacing, preserved for asynchronous dispatch.
    return min(4., 1.2 + len(previous.get('text', '')) * .035)


OUTPUT_SCHEMA = {'bubbles': [
    {'kind': 'text', 'text': 'string', 'quote_message_id': 'optional integer', 'introduces_miko': 'optional boolean'},
    {'kind': 'face', 'face_id': 'candidate string'},
    {'kind': 'sticker', 'sticker_id': 'candidate string'},
]}
