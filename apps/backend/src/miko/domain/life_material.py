"""Sample the shape of a day before selecting its supporting character facts."""
import hashlib
import random
import re
from datetime import timedelta

from miko.storage.search import terms


DAYTIME = (
    '白天主要处理手上的本职事务，同一件事情来回做，中间吃饭和休息',
    '本职事务一直做到晚饭后，吃饭和家务穿插着处理',
    '半天处理本职事务，另外半天处理买东西、取东西、家务等事情',
    '主要事务不多，外出办事和来回路上占去大半天',
    '在家慢慢处理手上的事情，没有明显的结果，时间就这么过去了',
    '主要处理洗衣、打扫、补充日用品等生活琐事，忙忙停停花去大半天',
    '今天没有安排本职事务，主要在家休息，起居和吃饭把时间分成零碎的几段',
)
LEISURE = (
    '看连续剧或电影，吃饭、洗澡、刷手机，时间零零碎碎地过去',
    '在家打游戏、看视频，后来收拾了一会儿房间',
    '在外面逛街吃饭，买日用品，路上走走停停',
    '和熟悉的人闲聊、吃饭，聊着聊着过去了一晚上',
    '听歌，翻了几页小说，之后又拿起手机随便看',
    '做一会儿自己有兴致的活动，做完之后吃饭洗澡、休息',
    '做一会儿自己有兴致的活动，做完之后吃饭洗澡、休息',
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
    return {key: [{'日期': r['day'], '涉及的人': [names.get(person, '好友') for person in r.get('subjects', [r.get('subject')])],
                   '内容': r['text']} for r in values] for key, values in groups.items() if values}


def generation_material(profiles, records, goals, day, rhythm=None, memory_events=()):
    shape = rhythm if rhythm is not None else day_rhythm(day)
    owner_names = {'橙汁', profiles.chat.person_name('owner')}
    rows = [r for r in profiles.list(subject='miko', active=True)
            if r['kind'] in ('overview', 'fact') and not r['account'] and not r['friend']
            and not (r['kind'] == 'overview' and any(name in r['content'] for name in owner_names))]
    work = [r for r in rows if any(word in r['category'] for word in ('工作', '职业', '学业'))]
    core = [r for r in rows if r['kind'] == 'overview' or r['category'].startswith('生活习惯') or r in work]
    work_terms = set().union(*(terms(r['content']) for r in work)) if work else set()
    available = [g for g in goals.values() if g['status'] == 'active'
                 and g.get('start_date', day.isoformat()) <= day.isoformat()]
    ranked = sorted(available, key=lambda g: len(work_terms & terms(g['text'])), reverse=True)
    selected = ranked[:1] if ranked and work_terms & terms(ranked[0]['text']) else []
    others = [g for g in available if g not in selected]
    if '有兴致' in shape['空下来之后'] and others:
        seed = int(hashlib.sha256(('miko-interest:' + day.isoformat()).encode()).hexdigest(), 16)
        selected.append(random.Random(seed).choice(others))

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

    prior = [r for r in records if r['day'] < day.isoformat()]
    selected_ids = {g['id'] for g in selected}
    progress, used = [], set()
    for row in prior:
        goal_id = row.get('goal_id')
        if goal_id in selected_ids and goal_id not in used:
            progress.append({'day': row['day'], 'text': row['text'], 'goal_id': goal_id})
            used.add(goal_id)
    # The recent prose is background, not a source of obsolete goal IDs/statuses.
    # Generated stories about the real user are not evidence about that person.
    recent_entries = [{'day': r['day'], 'text': r['text']} for r in prior
                      if not any(name in r['text'] for name in owner_names)][:8]
    ended = [{'text': g['text'], 'status': g['status'],
              **({'completed_date': g['completed_date']} if g.get('completed_date') else {})}
             for g in goals.values() if g['status'] != 'active'][-5:]
    result = {'日期': day.isoformat(), 'role_material': role_material, '真实用户资料': owner_material,
            '本次抽样的日子轮廓': shape, '生活目标': selected, '相关事项的最新进展': progress,
            '既有角色生活记录': recent_entries,
            '已经告一段落的事项': ended}
    background = conversation_background(profiles, memory_events, day, query)
    if background:
        # Keep recalled context ahead of the current day, not as the final
        # instruction that can take over the day's otherwise ordinary shape.
        result = {'对话提炼记录': background, **result}
    return result
