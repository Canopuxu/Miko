"""Local message lookup adapted from MIT Tulpa's ChatTools and Store.context.

Uses the existing Miko tables; no second message store or model calls.
"""
from datetime import datetime, time, timedelta
import json
from zoneinfo import ZoneInfo


ROWS = '''SELECT m.*,p.kind author_kind,p.display_name author_name,
    coalesce(f.name,c.title) conversation_title,f.account,f.friend,
    coalesce(e.recalled,0) recalled,e.payload
    FROM messages m JOIN people p ON p.id=m.author_id
    JOIN conversations c ON c.id=m.conversation_id
    LEFT JOIN qq_friends f ON f.conversation_id=m.conversation_id
    LEFT JOIN qq_events e ON e.account=f.account AND e.friend=f.friend
        AND e.message_id=CAST(substr(m.id,length(m.conversation_id)+2) AS INTEGER)'''


def public_message(row):
    payload = json.loads(row['payload'] or '{}')
    name = row['author_name']
    if row['author_kind'] == 'contact':
        name = payload.get('sender_name') or name
    return dict(id=row['id'], conversation_id=row['conversation_id'],
                conversation_title=row['conversation_title'], source=row['source'],
                author=dict(id=row['author_id'], kind=row['author_kind'], name=name),
                text='这条消息已撤回' if row['recalled'] else row['text'],
                created_at=row['created_at'], recalled=bool(row['recalled']),
                account=row['account'], friend=row['friend'])


class History:
    def __init__(self, store):
        self.store = store

    def search(self, source, query='', day=None, conversation_id='', offset=0, limit=30, snapshot=None):
        # Tulpa's literal keyword matching, scoped before pagination.
        where, values = ['m.source=?', 'coalesce(e.recalled,0)=0', "m.status!='running'"], [source]
        if conversation_id:
            where.append('m.conversation_id=?'); values.append(conversation_id)
        for term in list(dict.fromkeys(query.lower().split()))[:12]:
            where.append('instr(lower(m.text),?)>0'); values.append(term)
        if day:
            start = datetime.combine(day, time.min, ZoneInfo('Asia/Shanghai'))
            where.append('m.created_at>=? AND m.created_at<?')
            values.extend([int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)])
        with self.store.connect() as db:
            db.execute('BEGIN')
            if snapshot is None:
                snapshot = db.execute('SELECT coalesce(max(sequence),0) FROM messages').fetchone()[0]
            where.append('m.sequence<=?'); values.append(snapshot)
            filtered = ' WHERE ' + ' AND '.join(where)
            total = db.execute('SELECT count(*) FROM (' + ROWS + filtered + ')', values).fetchone()[0]
            # A date-only lookup starts at the beginning of that day.
            order = 'ASC' if day and not query.strip() else 'DESC'
            rows = db.execute(ROWS + filtered + f' ORDER BY m.created_at {order},m.sequence {order} LIMIT ? OFFSET ?',
                              [*values, limit, offset]).fetchall()
        return dict(messages=[public_message(row) for row in rows], total=total,
                    has_more=offset + len(rows) < total, snapshot=snapshot)

    def context(self, source, conversation_id, message_id, before=8, after=8):
        # As in Tulpa Store.context, native IDs never determine chronological order.
        with self.store.connect() as db:
            db.execute('BEGIN')
            scope, values = 'm.source=? AND m.conversation_id=?', [source, conversation_id]
            row = db.execute(ROWS + ' WHERE ' + scope + ' AND m.id=?', [*values, message_id]).fetchone()
            if not row:
                raise ValueError('原消息不存在或不属于这段对话')
            cursor = [row['created_at'], row['sequence']]
            earlier = db.execute(ROWS + ' WHERE ' + scope + ''' AND
                (m.created_at,m.sequence)<(?,?) ORDER BY m.created_at DESC,m.sequence DESC LIMIT ?''',
                                 [*values, *cursor, before + 1]).fetchall()
            later = db.execute(ROWS + ' WHERE ' + scope + ''' AND
                (m.created_at,m.sequence)>(?,?) ORDER BY m.created_at,m.sequence LIMIT ?''',
                               [*values, *cursor, after + 1]).fetchall()
        messages = [*reversed(earlier[:before]), row, *later[:after]]
        return dict(messages=[public_message(item) for item in messages],
                    has_before=len(earlier) > before, has_after=len(later) > after)
