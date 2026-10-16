"""Sample the shape of a day before selecting its supporting character facts."""
import hashlib
import random

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


def generation_material(profiles, records, goals, day, rhythm=None):
    shape = rhythm if rhythm is not None else day_rhythm(day)
    rows = [r for r in profiles.list(subject='miko', active=True)
            if r['kind'] in ('overview', 'fact') and not r['account'] and not r['friend']]
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
    owner_identity = [r for r in profiles.list(subject='owner', kind='overview', active=True)
                      if not r['account'] and not r['friend']]
    role_material += [{name: r[name] for name in ('subject', 'kind', 'category', 'content')}
                      for r in owner_identity]
    recent = [r for r in profiles.list(kind='recent', active=True)
              if r['subject'] == 'owner' and r.get('as_of') and r['as_of'] <= day.isoformat()
              and not r['account'] and not r['friend']][:3]
    role_material += [{name: r[name] for name in ('subject', 'kind', 'category', 'content', 'as_of')}
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
    recent_entries = [{'day': r['day'], 'text': r['text']} for r in prior[:8]]
    ended = [{'text': g['text'], 'status': g['status'],
              **({'completed_date': g['completed_date']} if g.get('completed_date') else {})}
             for g in goals.values() if g['status'] != 'active'][-5:]
    return {'日期': day.isoformat(), 'role_material': role_material, '本次抽样的日子轮廓': shape,
            '生活目标': selected, '相关事项的最新进展': progress, '生活条目': recent_entries,
            '已经告一段落的事项': ended}
