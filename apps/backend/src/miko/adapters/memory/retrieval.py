"""Persistent Miko material projection for Tulpa's read-only local retrieval."""
from contextlib import closing
import json
import hashlib
import re
import time
from pathlib import Path
import sqlite3

from miko.adapters.memory.tulpa_upstream.retrieval import Plan, scope_sql, text_matches
from miko.adapters.memory.tulpa_upstream.store import tokens, tokenizer
from miko.storage.search import keywords, terms, STOP


# Category vocabulary, never answers or extra facts. Custom labels remain searchable as-is.
CATEGORY_WORDS = (
    ('音乐 歌曲', '音乐 听歌 歌 歌手 乐队 耳机 歌单 专辑'),
    ('阅读 书籍 文学', '阅读 读书 看书 本书 书 小说 文学 故事'),
    ('工作 职业', '工作 职业 上班 职位 主业 赚钱 接稿 接单 公司 打卡 通勤'),
    ('乐器', '乐器 弹琴 练琴 琴 演奏'),
    ('运动', '运动 锻炼 健身 体能'),
    ('食物 饮食 美食', '食物 饮食 美食 口味 吃饭 吃'),
    ('居住 地点 住处', '居住 住处 家住 搬家 城市 住'),
    ('穿着 服装', '穿着 衣服 衣 穿 外套 衣柜 搭配'),
    ('作息 睡眠', '作息 睡眠 睡觉 起床 熬夜'),
    ('教育 学历 学业', '教育 学历 本科 硕士 研究生 毕业 学校'),
)
CHAT_WORDS = set('喜欢 平时 一般 现在 还在 有点 就是 来着 知道 记得 真的 感觉 晚安 先撤 哪类 哪种 哪个 什么样 怎么样 可以 今天 昨天 今晚 昨晚 晚上 上午 早上 中午 下午 一直 那边 这边 看看 我想 已经 还是 开始 会儿 这会儿 应该 干嘛 干啥 忙啥 自己'.split())


def category_words(category):
    return ' '.join(related for labels, related in CATEGORY_WORDS
                    if any(label in category for label in labels.split()))


def query_terms(query):
    return [w for w in keywords(query) if w not in CHAT_WORDS and w.strip('哈呵嘿嘻')]


def query_grams(query, *, supplementary=True):
    # Keep adjacent meaningful characters even when jieba splits 按/弦, while
    # conversational stopwords break spans instead of creating crossing bigrams.
    text=''.join(' ' if word.lower() in STOP | CHAT_WORDS or not word.strip('哈呵嘿嘻') else word
                 for word in tokenizer.cut(query))
    # Whole query words already have their own channel. Repeating them as bigrams
    # over-rewards partial matches (e.g. a shared prefix of a longer query word).
    excluded=STOP | CHAT_WORDS | (set(query_terms(query)) if supplementary else set())
    return sorted(w for w in character_terms(text) if len(w)>=2 and w not in excluded)


def character_terms(text):
    # Keep the existing Chinese bigrams/whole English words, plus mixed-script
    # boundaries such as T恤 that the original Chinese/ASCII split loses.
    return terms(text) | {word[i:i+2] for word in re.findall(r'\w+',str(text).casefold()[:2000])
                         if not word.isascii() for i in range(len(word)-1)}


def query_categories(query):
    parts = set(tokens(query).split())
    return [labels.split()[0] for labels, related in CATEGORY_WORDS
            if parts.intersection(related.split())]


def query_words(query):
    base = query_terms(query)
    # Single-character topical words (歌/书/琴) are lost by the original >=2-char tokenizer.
    expanded = query_categories(query)
    return list(dict.fromkeys([*base, *expanded, *query_grams(query)]))


def fields(row):
    source = row['source']
    category = source.get('category', '')
    title = source.get('topic') or source.get('title', '')
    body = source.get('content', source.get('text', ''))
    if row['group'] == 'social_memory':
        body = '\n'.join(filter(None, [source.get('automatic'), source.get('manual_corrections')]))
    # A repeated card title should not make a short tag count twice.
    if title == body: title = ''
    return body, title, category, ' '.join(filter(None, [source.get('keywords'), category_words(category)]))


INDEX_COLUMNS = ('tokens','title','category','hints','body_grams','title_grams','category_grams','hint_grams')


def index_fields(row, expressions=()):
    values=list(fields(row))
    values[3]=' '.join(filter(None,[values[3],*expressions]))
    return [tokens(value) for value in values]+[' '.join(sorted(character_terms(value))) for value in values]


def source_fingerprint(row):
    source=row['source']
    scope={k:source.get(k) for k in ('subject','subjects','peer','account','friend','day','as_of','status','start_date','target_date','completed_date')}
    return hashlib.sha256(json.dumps([row['group'],scope,fields(row)],ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def semantic_text(row):
    body, title, category, hints = fields(row)
    label = ' · '.join(filter(None, [category.split('/')[-1], title]))
    source=row['source']
    if row['group']=='life_goals':
        label='目标（'+source.get('status','')+'，预计'+source.get('target_date','')+'）'
    day=source.get('day',source.get('as_of','')) or ''
    text='；'.join(filter(None, [day,label + '：' + body if label else body,
                               '检索类别：' + hints if hints else '']))
    # memU memory retrieval embeds content lines, not the file description vector.
    return re.sub(r'\s+',' ',text)


class LocalRecall:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, source_id TEXT UNIQUE NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'miko', conversation_id TEXT NOT NULL,
                    content TEXT NOT NULL, timestamp INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS retrieval_expansions (
                    source_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL, prompt_hash TEXT NOT NULL,
                    expressions TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'pending',
                    updated_at INTEGER NOT NULL DEFAULT 0, retry_at INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '');
            ''')
            columns = [r['name'] for r in db.execute('PRAGMA table_info(message_fts)')]
            if columns != list(INDEX_COLUMNS):
                # Only derived search data changes; user material is never migrated/replaced.
                db.execute('DROP TABLE IF EXISTS message_fts')
                db.execute('CREATE VIRTUAL TABLE message_fts USING fts5('+','.join(INDEX_COLUMNS)+')')
                for row in db.execute('SELECT id,payload FROM messages').fetchall():
                    db.execute('INSERT INTO message_fts(rowid,'+','.join(INDEX_COLUMNS)+') VALUES(?,?,?,?,?,?,?,?,?)',
                               (row['id'],*index_fields(json.loads(row['payload']))))

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def sync(self, documents, expansion_version=None):
        """Only changed material is reindexed; this database is disposable derived data."""
        with closing(self.connect()) as db, db:
            existing = {r['source_id']: r for r in db.execute('SELECT * FROM messages')}
            expansions={r['source_id']:r for r in db.execute('SELECT * FROM retrieval_expansions')}
            for name, old in existing.items():
                if name not in documents:
                    db.execute('DELETE FROM message_fts WHERE rowid=?', (old['id'],))
                    db.execute('DELETE FROM messages WHERE id=?', (old['id'],))
                    db.execute('DELETE FROM retrieval_expansions WHERE source_id=?',(name,))
            for name, doc in documents.items():
                payload = json.dumps(doc['row'], ensure_ascii=False, sort_keys=True)
                content = json.dumps(fields(doc['row']), ensure_ascii=False)
                old = existing.get(name)
                cached=expansions.get(name)
                changed=False
                if doc['row']['source'].get('pinned'):
                    if cached:
                        db.execute('DELETE FROM retrieval_expansions WHERE source_id=?',(name,));changed=True
                    cached=None
                elif expansion_version is not None:
                    digest=source_fingerprint(doc['row'])
                    if not cached or cached['source_hash']!=digest or cached['prompt_hash']!=expansion_version:
                        db.execute('''INSERT INTO retrieval_expansions(source_id,source_hash,prompt_hash) VALUES(?,?,?)
                            ON CONFLICT(source_id) DO UPDATE SET source_hash=excluded.source_hash,
                            prompt_hash=excluded.prompt_hash,expressions='[]',state='pending',retry_at=0,error='' ''',
                            (name,digest,expansion_version))
                        cached=None;changed=True
                elif cached and cached['source_hash']!=source_fingerprint(doc['row']):
                    db.execute("UPDATE retrieval_expansions SET source_hash=?,expressions='[]',state='pending',retry_at=0,error='' WHERE source_id=?",
                               (source_fingerprint(doc['row']),name))
                    cached=None;changed=True
                if old and old['payload'] == payload and old['content'] == content and not changed:
                    continue
                db.execute('''INSERT INTO messages(source_id,conversation_id,content,timestamp,payload)
                    VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                    conversation_id=excluded.conversation_id,content=excluded.content,
                    timestamp=excluded.timestamp,payload=excluded.payload''',
                    (name, doc['peer'], content, doc.get('timestamp', 0), payload))
                mid = db.execute('SELECT id FROM messages WHERE source_id=?', (name,)).fetchone()[0]
                if not old or old['content'] != content or changed:
                    db.execute('DELETE FROM message_fts WHERE rowid=?', (mid,))
                    db.execute('INSERT INTO message_fts(rowid,'+','.join(INDEX_COLUMNS)+') VALUES(?,?,?,?,?,?,?,?,?)',
                               (mid,*index_fields(doc['row'],json.loads(cached['expressions']) if cached and cached['state']=='ready' else [])))

    def expansion_status(self):
        with closing(self.connect()) as db:
            counts={r['state']:r['n'] for r in db.execute('SELECT state,count(*) n FROM retrieval_expansions GROUP BY state')}
        return {'total':sum(counts.values()),'ready':counts.get('ready',0),
                'pending':counts.get('pending',0)+counts.get('failed',0),'failed':counts.get('failed',0)}

    def expansion_items(self, limit=300):
        with closing(self.connect()) as db:
            return [dict(r,expressions=json.loads(r['expressions']),row=json.loads(r['payload'])) for r in db.execute('''
                SELECT e.*,m.payload FROM retrieval_expansions e JOIN messages m USING(source_id)
                ORDER BY e.updated_at DESC,e.source_id LIMIT ?''',(limit,))]

    def expansion_pending(self, limit=8):
        with closing(self.connect()) as db:
            return [dict(r,row=json.loads(r['payload'])) for r in db.execute('''SELECT e.*,m.payload
                FROM retrieval_expansions e JOIN messages m USING(source_id)
                WHERE e.state!='ready' AND e.retry_at<=?
                ORDER BY json_extract(m.payload,'$.group') IN ('role_material','people_memory') DESC,
                m.timestamp DESC,m.id LIMIT ?''',(int(time.time()),limit))]

    def apply_expansions(self, batch, expressions):
        applied=0
        with closing(self.connect()) as db,db:
            for item in batch:
                row=db.execute('SELECT e.*,m.id,m.payload FROM retrieval_expansions e JOIN messages m USING(source_id) WHERE e.source_id=?',
                               (item['source_id'],)).fetchone()
                if not row or row['source_hash']!=item['source_hash'] or row['prompt_hash']!=item['prompt_hash']:
                    continue
                values=expressions[item['source_id']]
                db.execute("UPDATE retrieval_expansions SET expressions=?,state='ready',updated_at=?,retry_at=0,error='' WHERE source_id=?",
                           (json.dumps(values,ensure_ascii=False),int(time.time()),item['source_id']))
                db.execute('DELETE FROM message_fts WHERE rowid=?',(row['id'],))
                db.execute('INSERT INTO message_fts(rowid,'+','.join(INDEX_COLUMNS)+') VALUES(?,?,?,?,?,?,?,?,?)',
                           (row['id'],*index_fields(json.loads(row['payload']),values)))
                applied+=1
        return applied

    def expansion_failed(self, batch, error):
        with closing(self.connect()) as db,db:
            db.executemany("UPDATE retrieval_expansions SET state='failed',error=?,retry_at=? WHERE source_id=? AND source_hash=? AND prompt_hash=?",
                [(error,int(time.time())+300,r['source_id'],r['source_hash'],r['prompt_hash']) for r in batch])

    def retry_expansions(self, rebuild=False):
        with closing(self.connect()) as db,db:
            if rebuild:
                db.execute("UPDATE retrieval_expansions SET expressions='[]',state='pending',retry_at=0,error=''")
                for row in db.execute('SELECT m.id,m.payload FROM messages m JOIN retrieval_expansions e USING(source_id)').fetchall():
                    db.execute('DELETE FROM message_fts WHERE rowid=?',(row['id'],))
                    db.execute('INSERT INTO message_fts(rowid,'+','.join(INDEX_COLUMNS)+') VALUES(?,?,?,?,?,?,?,?,?)',
                               (row['id'],*index_fields(json.loads(row['payload']))))
            else:
                db.execute("UPDATE retrieval_expansions SET state='pending',retry_at=0,error='' WHERE state='failed'")

    def material(self, peers):
        plan = Plan(platforms=['miko'], conversations=[json.dumps(['miko', peer]) for peer in peers])
        where, params = scope_sql(plan)
        with closing(self.connect()) as db:
            return {r['source_id']: json.loads(r['payload']) for r in db.execute(
                'SELECT m.* FROM messages m WHERE ' + where, params)}

    def search(self, query, allowed, limit=40, *, explain=False):
        words = query_words(query)
        if not allowed or not words:
            return []
        where = 'm.source_id IN (' + ','.join('?' for _ in allowed) + ')'
        with closing(self.connect()) as db:
            quote = lambda word:'"' + word.replace('"','""') + '"'
            # Topic expansion targets category metadata, never incidental words in an episode.
            clauses = ['{tokens title category hints} : '+quote(word) for word in query_terms(query)]
            clauses += ['category : ' + quote(word) for word in query_categories(query)]
            matches = text_matches(db, where, list(allowed), ' '.join(words), limit=limit,
                                   weights=(5,4,2,1,0,0,0,0), match=' OR '.join(clauses)) if clauses else []
            grams=query_grams(query)
            gram_matches=text_matches(db,where,list(allowed),' '.join(grams),limit=limit,
                weights=(0,0,0,0,5,4,2,1),
                match=' OR '.join('{body_grams title_grams category_grams hint_grams} : '+quote(word) for word in grams)) if grams else []
            expansions={r['source_id']:' '.join(json.loads(r['expressions'])) for r in db.execute(
                "SELECT source_id,expressions FROM retrieval_expansions WHERE state='ready' AND source_id IN ("+
                ','.join('?' for _ in allowed)+')',list(allowed))}
        primary = set(query_terms(query))
        full_grams=set(query_grams(query,supplementary=False))
        independent = {w for w in primary if not any(w!=other and w in other for other in primary)}
        topical = set(query_categories(query))
        dated = bool(re.search(r'今天|今晚|昨天|昨晚|最近|\d{4}-\d{2}-\d{2}', query))
        fused={}
        for channel, channel_matches in (('word',matches),('gram',gram_matches)):
            ordered=[]
            for row in channel_matches:
                material=allowed[row['source_id']]
                values=fields(material)
                field_terms=[set(tokens(value).split()) for value in values]
                field_grams=[character_terms(value) for value in values]
                exact=any(len(value)>=2 and value in query for value in values[:2])
                matched=independent & set.union(*field_terms[:2])
                body_grams=set.union(*field_grams[:2])
                gram_body=set(grams)&body_grams
                coverage=len(matched)/max(1,len(independent))
                gram_coverage=len(full_grams&body_grams)/max(1,len(full_grams)) if gram_body else 0
                category=bool(topical & field_terms[2]) or any(
                    len(label)>=2 and label in query for label in values[2].split('/'))
                expansion=expansions.get(row['source_id'],'')
                expansion_words=independent & set(tokens(expansion).split())
                expansion_grams=character_terms(expansion)
                expansion_coverage=max(len(expansion_words)/max(1,len(independent)),
                    len(full_grams&expansion_grams)/max(1,len(full_grams)) if set(grams)&expansion_grams else 0)
                # Coverage and category are ranking evidence, not early minimum-match
                # requirements or category fences. Both channels search the entire scope.
                # Generated wording is a recall hint. An isolated overlap in a
                # longer question must not become the sole reason to inject a fact.
                expansion_support=expansion_coverage if expansion_coverage>=.5 else 0
                support=coverage+gram_coverage+int(exact)+2*int(category)+.8*expansion_support
                preferred=material['group'] in (('life_entries','dated_experiences') if dated else ('role_material','people_memory'))
                ordered.append({'name':row['source_id'],'score':-row['relevance']*(1+coverage+gram_coverage+int(exact)),
                    'bm25':-row['relevance'],'coverage':coverage,'gram_coverage':gram_coverage,
                    'phrase_match':exact,'category_match':category,'support':support*(1 if preferred else .3),
                    'expansion_coverage':expansion_coverage,'expansion_terms':sorted(expansion_words),
                    'channel':channel,
                    'fields':{key:sorted((primary & words) if channel=='word' else (set(grams)&parts))
                              for key,words,parts in zip(('body','title','category','hints'),field_terms,field_grams)}})
            ordered.sort(key=lambda r:r['score'],reverse=True)
            for position,hit in enumerate(ordered,1):
                result=fused.setdefault(hit['name'],dict(hit,score=0,channels=[]))
                result['score']+=1/(60+position)
                result['channels'].append(hit)
        for result in fused.values():
            result['score']*=result['support']
        ranked=sorted(fused.values(),key=lambda r:r['score'],reverse=True)
        # After recall, retain results with useful evidence relative to this query's
        # best candidate. This prunes incidental partial hits without a fixed term count.
        cutoff=ranked[0]['score']*.4 if ranked else 0
        results=[r for r in ranked if r['score']>0 and r['score']>=cutoff][:limit]
        return results if explain else [r['name'] for r in results]

    def recent(self, allowed, limit=64):
        if not allowed:
            return []
        with closing(self.connect()) as db:
            # Tulpa's per-bucket round-robin; buckets here are Miko material types.
            return [r['source_id'] for r in db.execute('''WITH candidates AS (
                SELECT *,row_number() OVER(PARTITION BY json_extract(payload,'$.group')
                    ORDER BY timestamp DESC,id DESC) AS slot FROM messages WHERE source_id IN (''' +
                ','.join('?' for _ in allowed) + ')) SELECT source_id FROM candidates ORDER BY slot,timestamp DESC,id DESC LIMIT ?', [*allowed, limit])]
