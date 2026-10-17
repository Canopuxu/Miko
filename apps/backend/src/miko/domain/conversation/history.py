"""Run read-only history calls inside Miko's OpenAI-compatible bridge."""
import asyncio
import json


class HistoryCompletion:
    def __init__(self, chat, turn, payload, request):
        self.chat, self.turn, self.payload, self.request = chat, turn, payload, request
        self.tools = turn.history_tools
        self.usage = {}

    async def lines(self, remote):
        while True:
            text, calls, output, finished = [], {}, [], None
            async for line in remote.aiter_lines():
                if self.turn.cancelled:
                    return
                if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                    continue
                chunk = json.loads(line[5:].strip())
                for key, value in (chunk.get('usage') or {}).items():
                    if isinstance(value, int):
                        self.usage[key] = self.usage.get(key, 0) + value
                for choice in chunk.get('choices', []):
                    if choice.get('index', 0) != 0:
                        continue
                    delta = choice.get('delta') or {}
                    if delta.get('content'):
                        text.append(delta['content'])
                    for part in delta.get('tool_calls') or []:
                        target = calls.setdefault(part['index'], {'id': '', 'type': 'function',
                            'function': {'name': '', 'arguments': ''}})
                        if part.get('id'):
                            target['id'] = part['id']
                        for key in ('name', 'arguments'):
                            target['function'][key] += part.get('function', {}).get(key) or ''
                    if choice.get('finish_reason'):
                        finished = choice['finish_reason']
                output.append(chunk)
            await remote.aclose()
            if self.turn.cancelled:
                return
            if not finished:
                raise ValueError('incomplete_history_completion')
            if not calls:
                # Tool-round narration is never sent as a QQ bubble or saved as
                # a character reply. Only the final response reaches the SDK/UI.
                visible = [chunk for chunk in output if chunk.get('choices')]
                for index, chunk in enumerate(visible):
                    chunk.pop('usage', None)
                    if self.usage and index == len(visible) - 1:
                        chunk['usage'] = self.usage
                    yield 'data: ' + json.dumps(chunk, ensure_ascii=False)
                    yield ''
                yield 'data: [DONE]'
                yield ''
                return
            if finished != 'tool_calls':
                raise ValueError('incomplete_history_tool_call')
            if not self.payload.get('tools'):
                raise ValueError('unexpected_history_tool_call')
            ordered = [calls[i] for i in sorted(calls)]
            self.payload['messages'].append({'role': 'assistant', 'content': ''.join(text) or None,
                                             'tool_calls': ordered})
            for call in ordered:
                if self.turn.cancelled:
                    return
                try:
                    args = json.loads(call['function']['arguments'])
                    value = await asyncio.to_thread(self.tools.execute, call['function']['name'], args)
                except (ValueError, TypeError):
                    self.tools.calls += 1
                    value = {'error': 'invalid_arguments'}
                self.payload['messages'].append({'role': 'tool', 'tool_call_id': call['id'],
                    'content': json.dumps(value, ensure_ascii=False)})
            if self.tools.calls >= self.tools.max_calls:
                self.payload.pop('tools', None)
                self.payload.pop('tool_choice', None)
            remote = await self.request(self.payload)
            self.turn.remote = remote
