"""Sample the shape of a day before selecting its supporting character facts."""
import hashlib
import random
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from miko.storage.search import terms


DAYTIME = (
    '白天大部分时间被本职事务占用，空闲较少',
    '主要事务持续到较晚，剩余精力不多',
    '主要事务占去半天，另外半天可自行安排',
    '需要处理一些日常事务，时间比较零碎',
    '在家处理手头的事情，节奏缓慢',
    '生活杂事占去不少时间，忙忙停停',
    '今天比较空闲，可以按自己的状态度过',
)
LEISURE = (
    '剩余时间零碎，适合随意放松',
    '有一段完整的空闲，想做点轻松的事',
    '有空外出走动，节奏不赶',
    '有与人相处的兴致，愿意多聊一会儿',
    '精力偏低，想按自己的节奏休息',
    '有兴致继续一件自己在意的事',
    '有兴致尝试或继续自己喜欢的事情',
)


def day_rhythm(day):
    # Shuffle a week's broad circumstances rather than drawing the same errand
    # several days in a row. A same-day retry keeps the same circumstances.
    week = str(day.toordinal() - day.weekday())
    seed = int(hashlib.sha256(('miko-life:' + week).encode()).hexdigest(), 16)
    rng = random.Random(seed)
    daytime, leisure = list(DAYTIME), list(LEISURE)
    rng.shuffle(daytime)
    rng.shuffle(leisure)
    return {'白天的时间去向': daytime[day.weekday()], '空下来之后': leisure[day.weekday()]}


def conversation_background(profiles, events, day, query):
    today = day.isoformat()
    recent = (day - timedelta(days=7)).isoformat()
    soon = (day + timedelta(days=2)).isoformat()
    rows = [r for r in events if 'miko' in r.get('subjects', [r.get('subject')])]
    current = sorted((r for r in rows if r['day'] == today), key=lambda r: r.get('updated_at', 0), reverse=True)[:2]
    upcoming = sorted((r for r in rows if today < r['day'] <= soon
                       and len(query & terms(r['text'])) >= 2),
                      key=lambda r: (r['day'], -r.get('updated_at', 0)))[:1] if not current else []
    # A nearby plan also supplies its topic: yesterday's change to that plan
    # should be available alongside it, without another retrieval/model call.
    relevant = query | terms(' '.join(r['text'] for r in current + upcoming))
    counterparts = {person for r in current + upcoming for person in r.get('subjects', []) if person != 'miko'}
    eligible = [r for r in rows if recent <= r['day'] < today and
                (not counterparts or not (set(r.get('subjects', [])) - {'miko'}) or
                 counterparts & set(r.get('subjects', [])))]
    def relevance(row):
        # The compact extraction may omit a category such as “游戏”; its
        # existing source messages can supply that keyword for local matching.
        titles = re.findall(r'《([^》]+)》', row['text'])
        sources = row.get('sources', [])
        nearby = {j for i, source in enumerate(sources) if any(title in source['content'] for title in titles)
                  for j in range(max(0, i - 1), min(len(sources), i + 2))}
        text = row['text'] + ' ' + ' '.join(sources[i]['content'] for i in sorted(nearby))
        return len(relevant & terms(text, limit=None))
    past = sorted((r for r in eligible if relevance(r) >= 2),
                  key=lambda r: (relevance(r), r['day'], r.get('updated_at', 0)), reverse=True)[:1]
    groups = {'当天': current, '临近': upcoming, '近期对话': past}
    if not any(groups.values()):
        return {}
    with profiles.chat.connect() as db:
        names = {r['id']: r['display_name'] for r in db.execute('SELECT id,display_name FROM people')}
    def background(row):
        item = {'日期': row['day'],
                '涉及的人': [names.get(person, person) for person in row.get('subjects', [row.get('subject')])],
                '内容': row['text']}
        # A summary date can be the date an invitation was said, not a booked
        # outing. Preserve the wording and speakers instead of promoting it to
        # a confirmed plan. Sources are shared by an extraction batch, so only
        # include the matching exchange and its immediate neighbors.
        sources = row.get('sources', [])
        words = terms(row['text'])
        anchors = [i for i, source in enumerate(sources) if words & terms(source['content'])]
        indices = sorted({j for i in anchors for j in range(max(0, i - 2), min(len(sources), i + 3))})[-12:]
        quotes = []
        for i in indices:
            source = sources[i]
            speaker = source.get('sender_id', source.get('author', ''))
            quote = {'发言人': names.get(speaker, speaker), '原话': source['content']}
            if source.get('timestamp'):
                quote['发言时间'] = datetime.fromtimestamp(source['timestamp'] / 1000, ZoneInfo('Asia/Shanghai')).isoformat()
            quotes.append(quote)
        if quotes:
            item['相关原话'] = quotes
        return item
    return {key: [background(r) for r in values] for key, values in groups.items() if values}


def generation_material(profiles, records, goals, day, rhythm=None, memory_events=()):
    shape = rhythm if rhythm is not None else day_rhythm(day)
    identity = profiles.chat.identity()
    owner_names = {name for name, subject in identity.aliases.items() if subject == 'owner'}
    rows = [r for r in profiles.list(subject='miko', active=True)
            if r['kind'] in ('overview', 'fact') and not r['account'] and not r['friend']
            and not (r['kind'] == 'overview' and any(name in r['content'] for name in owner_names))]
    work = [r for r in rows if any(word in r['category'] for word in ('工作', '职业', '学业'))]
    core = [r for r in rows if r['kind'] == 'overview' or r['category'].startswith('生活习惯') or r in work]
    work_terms = set().union(*(terms(r['content']) for r in work)) if work else set()
    available = [dict(g) for g in goals.values() if g['status'] != 'completed'
                 and g.get('start_date', day.isoformat()) <= day.isoformat()]
    prior = sorted((r for r in records if r['day'] < day.isoformat()), key=lambda r: r['day'], reverse=True)
    active = [g for g in available if g['status'] == 'active']
    # Work can already occupy the day. A spare-time project is offered only
    # when the sampled circumstances leave room for it; selection is not progress.
    ranked = sorted(active, key=lambda g: len(work_terms & terms(g['text'])), reverse=True)
    selected = ranked[:1] if ranked and work_terms & terms(ranked[0]['text']) else []
    has_room = (shape.get('空下来之后') in (LEISURE[1], LEISURE[5], LEISURE[6])
                and shape.get('白天的时间去向') not in DAYTIME[:2])
    if has_room:
        remaining = [g for g in active if g not in selected]
        # Give projects left aside for longer a chance, rather than always
        # offering the first one or forcing every project into today's plan.
        remaining.sort(key=lambda g: next((r['day'] for r in prior if r.get('goal_id') == g['id']), g['start_date']))
        selected += remaining[:1]

    # A candidate is an option, not a scheduled action. Keep one current
    # project available even on a busy day so recent prose cannot mention a
    # live project while its identity and state are missing from the input.
    if not selected and active:
        selected = [min(active, key=lambda g: next(
            (r['day'] for r in prior if r.get('goal_id') == g['id']), g['start_date']))]

    query = terms(' '.join(shape.values()) + ' ' + ' '.join(g['text'] for g in selected))
    related = [r for r in rows if r not in core and r['category'].startswith(('兴趣与能力', '喜好与厌恶'))
               and query & terms(r['content'])]
    material_rows = core + related[:5]
    role_material = [{name: r[name] for name in ('subject', 'kind', 'category', 'content')}
                     for r in material_rows]
    identity = [r for r in profiles.list(subject='owner', kind='overview', active=True)
                if not r['account'] and not r['friend'] and any(name in r['content'] for name in owner_names)]
    recent = [r for r in profiles.list(kind='recent', active=True)
              if r['subject'] == 'owner' and r.get('as_of') and r['as_of'] <= day.isoformat()
              and not r['account'] and not r['friend']][:3]
    owner_material = [{name: r[name] for name in ('subject', 'kind', 'category', 'content')}
                      for r in identity]
    owner_material += [{name: r[name] for name in ('subject', 'kind', 'category', 'content', 'as_of')}
                      for r in recent]

    selected_ids = {g['id'] for g in selected}
    progress, counts = [], {}
    for row in prior:
        goal_id = row.get('goal_id')
        if goal_id in selected_ids and counts.get(goal_id, 0) < 3:
            progress.append({key: row[key] for key in ('day', 'text', 'goal_id', 'result') if key in row})
            counts[goal_id] = counts.get(goal_id, 0) + 1
    for goal in selected:
        latest = next((row for row in prior if row.get('goal_id') == goal['id']), None)
        goal['current_state'] = (latest.get('result') or latest['text']) if latest else ''
        goal['done_when'] = goal.get('done_when') or goal['text']
    # The recent prose is background, not a source of obsolete goal IDs/statuses.
    # Generated stories about the real user are not evidence about that person.
    recent_entries = [{'day': r['day'], 'text': r['text']} for r in prior
                      if not any(name in r['text'] for name in owner_names)][:8]
    ended = [{'text': g['text'], 'status': g['status'],
              **({'completed_date': g['completed_date']} if g.get('completed_date') else {})}
             for g in goals.values() if g['status'] != 'active'][-5:]
    result = {'既有角色生活记录': recent_entries, '已经告一段落的事项': ended,
            '日期': day.isoformat(), 'role_material': role_material, '真实用户资料': owner_material,
            '本次抽样的日子轮廓': shape,
            '未完成目标总数': len(available),
            '其他已建立事项': [g['text'] for g in available if g not in selected],
            '相关事项的最新进展': progress, '生活目标': selected}
    background = conversation_background(profiles, memory_events, day, query)
    if background:
        # Keep recalled context ahead of the current day, not as the final
        # instruction that can take over the day's otherwise ordinary shape.
        result = {'对话提炼记录': background, **result}
    return result
