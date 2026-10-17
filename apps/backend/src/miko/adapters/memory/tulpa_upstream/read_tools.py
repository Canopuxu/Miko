"""Selected MIT Tulpa ChatTools read methods; Miko supplies storage and admission.
Source: chatlocal/agent_tools.py. Non-chat tools and investigator notes omitted.
"""
from dataclasses import replace
from .retrieval import scope_sql
from .store import tokens
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

def date_bound(value, end=False):
    stamp = datetime.fromisoformat(str(value)).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
    if end and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
        stamp += timedelta(days=1)
    return int(stamp.timestamp() * 1000)

class ReadTools:

    def _validate(self, name, args):
        schema = next((t['parameters'] for t in self.schemas if t['name'] == name), None)
        if schema is None or not isinstance(args, dict):
            raise ValueError('未知工具或参数格式不正确。')
        if set(args) - set(schema['properties']) or set(schema['required']) - set(args):
            raise ValueError('工具参数缺失或包含未允许的字段。')

        def validate(key, value, spec):
            if spec['type'] == 'integer' and type(value) is not int:
                raise ValueError(f'{key} 必须是整数。')
            if spec['type'] == 'integer' and (value < spec.get('minimum', value) or value > spec.get('maximum', value)):
                raise ValueError(f"{key} 允许范围为 {spec.get('minimum', 0)}–{spec.get('maximum', '不限')}；请修改此参数后重试。")
            if spec['type'] == 'boolean' and type(value) is not bool:
                raise ValueError(f'{key} 必须是布尔值。')
            if spec['type'] == 'string' and (not isinstance(value, str) or len(value) > spec.get('maxLength', 600)):
                raise ValueError(f"{key} 必须是不超过 {spec.get('maxLength', 600)} 字的字符串。")
            if spec['type'] == 'array':
                if not isinstance(value, list) or not spec.get('minItems', 0) <= len(value) <= spec.get('maxItems', 30):
                    raise ValueError(f'{key} 数组长度超出允许范围。')
                for i, item in enumerate(value):
                    validate(f'{key}[{i}]', item, spec['items'])
            if spec['type'] == 'object':
                if not isinstance(value, dict) or set(value) - set(spec['properties']) or set(spec.get('required', [])) - set(value):
                    raise ValueError(f'{key} 对象字段不符合要求。')
                for k, v in value.items():
                    validate(k, v, spec['properties'][k])
            if 'enum' in spec and value not in spec['enum']:
                raise ValueError(f'{key} 不在允许范围内。')
        for key, value in args.items():
            validate(key, value, schema['properties'][key])
            if key in schema['required'] and isinstance(value, str) and (not value.strip()) and (not (name == 'search_messages' and key == 'query' and (args.get('has_media') is True or args.get('has_voice') is True or args.get('sender_id')))):
                raise ValueError(f'{key} 不能为空。')
        if args.get('snapshot_max_id', 0) < 0:
            raise ValueError('快照 ID 不能为负数。')
        if name == 'search_messages' and args.get('sender_id') and (not args.get('platform')):
            raise ValueError('按账号搜索必须同时指定platform，不能跨平台合并同号账号。')

    def _scope(self, args):
        plan = replace(self.plan)
        if args.get('platform'):
            if args['platform'] not in plan.platforms:
                raise ValueError('不能查询用户未选择的平台。')
            plan.platforms = [args['platform']]
        where, values = scope_sql(plan)
        if args.get('conversation_id'):
            where += ' AND m.conversation_id=?'
            values.append(args['conversation_id'])
        if 'has_media' in args:
            where += ' AND m.media_json' + ('!=' if args['has_media'] else '=') + "'[]'"
        if 'has_voice' in args:
            where += ' AND ' + ('' if args['has_voice'] else 'NOT ') + 'EXISTS(SELECT 1 FROM voice_sources v WHERE v.message_id=m.id)'
        for key, op in [('start', '>='), ('end', '<')]:
            if args.get(key):
                value = date_bound(args[key], key == 'end')
                where += f' AND m.timestamp{op}?'
                values.append(value)
        return (where, values)

    def _search_messages(self, args):
        query = args['query'].strip()
        if not query and (not args.get('has_media')) and (not args.get('has_voice')) and (not args.get('sender_id')):
            raise ValueError('关键词不能为空')
        terms = list(dict.fromkeys(query.lower().split()))[:12]
        expression = ' OR '.join(('"' + tokens(k).replace('"', '""') + '"' for k in terms if tokens(k).strip()))
        if query and (not expression):
            raise ValueError('无有效关键词')
        where, values = self._scope(args)
        if args.get('sender_id'):
            where += ' AND m.sender_id=?'
            values.append(args['sender_id'])
        if expression:
            where += ' AND (m.id IN (SELECT rowid FROM message_fts WHERE message_fts MATCH ?) OR ' + ' OR '.join(('instr(lower(m.content),?)>0' for _ in terms)) + ')'
            values += [expression] + terms
        if args.get('author') == 'self':
            where += ' AND m.is_self=1'
        elif args.get('author') == 'others':
            where += ' AND (m.is_self=0 OR m.is_self IS NULL)'
        with self.store.connect() as db:
            total = db.execute(f'SELECT count(*) FROM messages m WHERE {where}', values).fetchone()[0]
            rows = db.execute(f'WITH candidates AS (\n                SELECT m.*,row_number() OVER(PARTITION BY platform,conversation_id ORDER BY timestamp DESC,id DESC) slot\n                FROM messages m WHERE {where})\n                SELECT * FROM candidates ORDER BY slot,platform,timestamp DESC,id DESC LIMIT ? OFFSET ?', values + [args.get('limit', 12), args.get('offset', 0)]).fetchall()
        result = self._page(rows, total, args.get('offset', 0))
        return self._search_context(result, args)

    def _search_context(self, result, args):
        """Send bounded original dialogue with hits, not just UI-only context.

        All hits are admitted first. Context has a separate *smaller* allowance
        within the existing turn budget, leaving room for deliberate followups.
        """
        hits = list(result['messages'])
        result['hit_ids'] = [m['id'] for m in hits]
        selected = {m['id']: m for m in hits}
        windows = []
        start, end = (self.plan.start, self.plan.end)
        if args.get('start'):
            bound = date_bound(args['start'])
            start = max(start, bound) if start is not None else bound
        if args.get('end'):
            bound = date_bound(args['end'], True)
            end = min(end, bound) if end is not None else bound
        ceiling = self.used_chars + min(6000, max(0, self.max_chars - self.used_chars) // 2)
        ordered = sorted(hits, key=lambda m: (m['conversation_type'] != 'direct', len(m['content']) > 200))
        for hit in ordered:
            radius = 5 if hit['conversation_type'] == 'direct' else 2
            snapshot = args.get('snapshot_max_id', self.plan.snapshot_max_id)
            if self.plan.snapshot_max_id is not None and snapshot is not None:
                snapshot = min(snapshot, self.plan.snapshot_max_id)
            nearby = self.store.context(hit['id'], radius=radius, start=start, end=end, max_id=snapshot)
            admitted = self._admit(nearby, char_limit=ceiling)
            selected.update(((m['id'], m) for m in admitted))
            sent = {m['id'] for m in admitted}
            self.contexts[hit['id']] = nearby
            if len(hit['content']) <= 200:
                self.context_requirements.setdefault(hit['id'], set()).update((m['id'] for m in nearby))
            windows.append(dict(anchor_id=hit['id'], message_ids=[m['id'] for m in nearby if m['id'] in sent], radius=radius, budget_limited=len(admitted) < len(nearby)))
        result.update(messages=sorted(selected.values(), key=lambda m: (m['platform'], m['conversation_id'], self.originals[m['id']]['timestamp'], m['id'])), context_windows=windows, context_budget_limited=any((w['budget_limited'] for w in windows)), remaining_messages=self.max_messages - len(self.messages), remaining_chars=self.max_chars - self.used_chars, needs_context_ids=sorted(self.unresolved_context_ids()))
        return result

    def unresolved_context_ids(self):
        delivered = self.messages.keys()
        return {mid for mid, needed in self.context_requirements.items() if not needed <= delivered}

    def _get_context(self, args):
        radius = args.get('radius', 3)
        if not 0 <= radius <= 8:
            raise ValueError('上下文范围不合法')
        if args['message_id'] == getattr(self, 'reply_current_anchor', None) or args['message_id'] in getattr(self, 'reply_live_rows', {}):
            rows = self.reply_current_rows
            selected = self._admit(rows)
            self.contexts[args['message_id']] = rows
            return dict(messages=selected, budget_limited=len(selected) < len(rows))
        where, values = self._scope({})
        with self.store.connect() as db:
            found = db.execute(f'SELECT id FROM messages m WHERE {where} AND m.id=?', values + [args['message_id']]).fetchone()
        if found is None:
            raise ValueError('消息不在范围内')
        rows = self.store.context(args['message_id'], radius=radius, start=self.plan.start, end=self.plan.end, max_id=self.plan.snapshot_max_id)
        selected = self._admit(rows)
        self.contexts[args['message_id']] = rows
        return dict(messages=selected, budget_limited=len(selected) < len(rows))

    def _read_conversation(self, args):
        where, values = self._scope(args)
        order = args.get('order', 'oldest')
        direction = 'DESC' if order == 'newest' else 'ASC'
        with self.store.connect() as db:
            snapshot = args.get('snapshot_max_id')
            if snapshot is None:
                snapshot = db.execute(f'SELECT coalesce(max(id),0) FROM messages m WHERE {where}', values).fetchone()[0]
            where += ' AND m.id<=?'
            values.append(snapshot)
            total = db.execute(f'SELECT count(*) FROM messages m WHERE {where}', values).fetchone()[0]
            rows = db.execute(f'SELECT * FROM messages m WHERE {where} ORDER BY timestamp {direction},id {direction} LIMIT ? OFFSET ?', values + [args.get('limit', 12), args.get('offset', 0)]).fetchall()
        result = self._page(rows, total, args.get('offset', 0))
        result.update(order=order, snapshot_max_id=snapshot)
        return result


class ContextStore:
    def context(self, mid, radius=3, start=None, end=None, max_id=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
            if not row:
                return []
            clauses, args = (['platform=?', 'conversation_id=?'], [row['platform'], row['conversation_id']])
            if max_id is not None:
                clauses.append('id<=?')
                args.append(max_id)
            if start is not None:
                clauses.append('timestamp>=?')
                args.append(start)
            if end is not None:
                clauses.append('timestamp<?')
                args.append(end)
            where = ' AND '.join(clauses)
            before = db.execute(f'SELECT * FROM messages WHERE {where} AND\n                (timestamp,id)<(?,?) ORDER BY timestamp DESC,id DESC LIMIT ?', args + [row['timestamp'], mid, radius]).fetchall()
            after = db.execute(f'SELECT * FROM messages WHERE {where} AND\n                (timestamp,id)>(?,?) ORDER BY timestamp,id LIMIT ?', args + [row['timestamp'], mid, radius]).fetchall()
            return [dict(r) for r in reversed(before)] + [dict(row)] + [dict(r) for r in after]
