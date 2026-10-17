"""QQ context selection: stored ownership, reranking and readable context."""
import json,re,logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx
from miko.adapters.memory.rerank import rank
from miko.storage.search import keywords
from itertools import zip_longest

NAMES={'owner':'橙汁','miko':'Miko'}
GROUPS=('role_material','social_memory','people_memory','dated_experiences','life_entries','life_goals')

def candidates(context):
    result=[];seen=set();core=[]
    for group in GROUPS:
        for row in context.get(group,[]):
            subject=NAMES.get(row.get('subject'),row.get('subject',''))
            if group=='dated_experiences' and row.get('subjects'):
                subject='、'.join(NAMES.get(s,s) for s in row['subjects'])
            if group.startswith('life_'):subject='Miko'
            if group=='role_material':text=row.get('content','')
            elif group=='social_memory':text='\n'.join(filter(None,[row.get('automatic'),row.get('manual_corrections')]))
            elif group=='owner_feedback':text=row.get('reply','')+'\n'+row.get('feedback','')
            else:text=row.get('content',row.get('text',''))
            if not text.strip():continue
            # Preserve each source's author/date; collapse duplicates, not different people's facts.
            day=row.get('day',row.get('as_of','')) or ''
            key=(subject,day,re.sub(r'\s+','',text))
            if key in seen:continue
            seen.add(key)
            if group=='role_material' and row.get('pinned'):
                core.append(f'{subject}：{text}');continue
            label=subject
            if group=='role_material' and row.get('category'):
                label+='（'+row['category'].replace('/',' · ')+'）'
            if group in ('social_memory','people_memory','dated_experiences'):
                label='涉及'+subject+'的记录'
                if row.get('peer'):label+='（'+row['peer']+'）'
            if group=='life_goals':label+='的目标（'+row.get('status','')+'；预计'+row.get('target_date','')+'）'
            if day:label+='，'+day
            result.append({'group':group,'source':row,'text':label+'：'+text})
    return core,result

def topic_queries(query):
    # Split explicit questions; expand only the explicit coordinated subject form.
    clauses=[x.strip() for x in re.split(r'[？?\n]+|[，,](?=(?:那)?你呢)',query) if x.strip()]
    queries=[]
    explicit_pair=False
    for clause in clauses:
        if re.fullmatch(r'(?:那)?你呢', clause) and queries:
            queries.append(re.sub(r'橙汁|他', 'Miko', queries[-1]))
            continue
        match=re.match(r'^(你|Miko)[跟和与]橙汁(.*)$',clause)
        if match:
            explicit_pair=True
            rest=match[2].replace('各自','').replace('分别','')
            queries.extend(['Miko'+rest,'橙汁'+rest])
        else:
            if explicit_pair and clause.startswith('他'):clause='橙汁'+clause[1:]
            queries.append(clause)
    return list(dict.fromkeys(queries))[-4:]


def subjects_for(text, previous=()):
    owner='橙汁' in text
    miko=bool(re.search(r'Miko|你(?!们)',text,re.I))
    if '你们' in text and (owner or any('橙汁' in t for t in previous[-2:])):
        return {'miko','owner'}
    if owner:
        return {'owner','miko'} if miko else {'owner'}
    if miko:return {'miko'}
    if re.search(r'他|她',text):
        for prior in reversed(previous[-2:]):
            if '橙汁' in prior:return {'owner'} if '他' in text else {'miko'}
    return {'miko'}


def scoped_row(row,subjects):
    if row['group']=='dated_experiences' and row['source'].get('subjects'):
        return bool(set(row['source']['subjects']) & subjects)
    subject=row['source'].get('subject')
    if row['group'].startswith('life_'):subject='miko'
    # Use stored ownership; do not infer ownership from a matching word in content.
    return subject in subjects


def near_duplicate(a,b):
    # Suppress repeated presentation only; never overwrite stored memories.
    sa,sb=a['source'],b['source']
    scope=lambda x:(tuple(sorted(x.get('subjects') or [x.get('subject','miko' if 'goal_id' in x else '')])),x.get('peer',''),x.get('day',x.get('as_of','')) or '')
    if scope(sa)!=scope(sb):return False
    normalize=lambda x:re.sub(r'[^\w]','',x)
    body=lambda r:r['source'].get('content',r['source'].get('text',r['text']))
    x,y=normalize(body(a)),normalize(body(b))
    if x==y:return True
    # Different dates/numbers or negation may change the fact; keep both.
    if re.findall(r'\d+',x)!=re.findall(r'\d+',y):return False
    if [w for w in ('不','没','未','否') if w in x]!=[w for w in ('不','没','未','否') if w in y]:return False
    if min(len(x),len(y))>=2 and max(len(x),len(y))<=40 and (x in y or y in x):
        from miko.adapters.memory.retrieval import category_words
        labels=lambda r:set(keywords(r['source'].get('category','')+' '+r['source'].get('topic','')+' '+category_words(r['source'].get('category',''))))
        if labels(a)&labels(b):return True
    grams=lambda t:{t[i:i+3] for i in range(len(t)-2)}
    gx,gy=grams(x),grams(y)
    return bool(gx and gy) and len(gx&gy)/len(gx|gy)>=.85


def scoring_query(topic):
    # Ownership has already been resolved; do not reward repeated speaker names.
    topic=re.sub(r'橙汁|Miko','',topic,flags=re.I)
    return re.sub(r'^(?:那)?(?:你|他|她)[，,、 ]*', '', topic) or topic


def scoring_document(row):
    # Rerank actual facts, not the synonym hints used to broaden recall.
    from miko.adapters.memory.retrieval import fields
    body,title,category,_ = fields(row)
    label=' · '.join(filter(None,[category.split('/')[-1],title]))
    day=row['source'].get('day',row['source'].get('as_of','')) or ''
    return ' '.join(filter(None,[day,label+'：'+body if label else body]))


def scoring_documents(rows):
    return [scoring_document(row) for row in rows]


def retrieval_query(query, previous=()):
    # Nearby speech resolves short follow-ups; full new topics use their own words.
    if previous and re.search(r'^(?:那(?:个|件|次|你呢|他呢|她呢)|这|它|他|她|后来|然后)|(?:多久|多长时间|多少年|怎么解决|怎么样了)|你呢[？?]?$',query):
        return query+'\n'+'\n'.join(previous[-2:])
    return query


def source_key(row):
    source = row['source']; group = row['group']
    if group == 'role_material': return 'profile:' + source.get('id', '')
    if group == 'people_memory': return 'item:' + source.get('id', '')
    if group == 'dated_experiences': return source.get('id', '')
    if group == 'life_goals': return 'life-goal:' + source.get('id', '')
    if group == 'life_entries': return source.get('_retrieval_id', source.get('id', ''))
    return 'social:' + str(source.get('id', ''))


def topic_scopes(query, context):
    previous = context.get('_previous_contact_texts', [])
    topics = topic_queries(query)
    names = context.get('_contact_names', {})
    scopes = [subjects_for(topic, previous) | {person for person,name in names.items()
              if person in topic or name and ('@'+name in topic or len(name)>1 and name in topic)} for topic in topics]
    if context.get('_self_subject') == 'owner':
        scopes = [scope | {'owner'} if re.search(r'我(?!们)', topic) or not re.search(r'Miko|你|橙汁',topic,re.I) else scope for topic, scope in zip(topics, scopes)]
    speaker = context.get('_group_speaker')
    if speaker:
        for i,topic in enumerate(topics):
            if (re.search(r'我的|按我|照我|适合我|我(?:平时|一般|喜欢|爱听|口味)',topic)
                    or re.search(r'我(?!们)',topic) and not re.search(r'你|Miko|橙汁',topic,re.I)
                    or topic.startswith('我') and re.search(r'你还记得|你记得',topic)):
                person=next((person for text,person in speaker.get('by_text',{}).items() if topic in text),speaker['id'])
                scopes[i]={person}
    return topics, scopes


def time_matches(row, topic, context):
    day = row['source'].get('day', row['source'].get('as_of'))
    if not day: return True
    now = context.get('current_time', '')[:10]
    if not now: return True
    if row['group'] == 'life_entries' and day > now: return False
    if any(w in topic for w in ('昨天','昨晚')):
        return day == (datetime.fromisoformat(now) - timedelta(days=1)).date().isoformat()
    if any(w in topic for w in ('今天','今晚','今早')): return day == now
    explicit=re.search(r'\d{4}-\d{2}-\d{2}',topic)
    if explicit:return day==explicit[0]
    if '最近' in topic:return (datetime.fromisoformat(now)-timedelta(days=7)).date().isoformat()<=day<=now
    return True


def fuse_rankings(*orders):
    """Reciprocal-rank fusion: each retrieval source contributes one ranking."""
    scores={}
    for order in orders:
        for position,name in enumerate(dict.fromkeys(order),1):
            scores[name]=scores.get(name,0)+1/(60+position)
    return sorted(scores,key=scores.get,reverse=True)


async def select(provider,query,context,limit=5,*,local):
    from miko.adapters.memory.retrieval import query_words
    core,rows=candidates(context)
    topics,scopes=topic_scopes(query,context)
    context['_subjects']=set().union(*scopes)
    selected=[];rankings=[]
    hits=context.get('retrieval',{}).get('hits',[])
    async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
        for topic,subjects in zip(topics,scopes):
            allowed={source_key(row):dict(row,candidate_index=i) for i,row in enumerate(rows)
                     if scoped_row(row,subjects) and time_matches(row,topic,context)}
            search=retrieval_query(topic,context.get('_previous_contact_texts',[]))
            words=query_words(scoring_query(search))
            if not allowed:continue
            evidence=local.search(scoring_query(search),allowed,explain=True) if words else []
            lexical=[hit['name'] for hit in evidence]
            # A date/recency request can retrieve dated experiences directly.
            if not lexical and not words and re.search(r'今天|今晚|今早|昨天|昨晚|最近|\d{4}-\d{2}-\d{2}',topic):
                dated=[name for name,row in allowed.items() if row['source'].get('day')]
                lexical=local.recent(dated,limit=40)
            topic_hits=context.get('retrieval',{}).get('by_topic',{}).get(topic,hits)
            categories=context.get('retrieval',{}).get('category_hits',{}).get(topic,[])
            category_scores={name:hit['score'] for hit in categories for name in hit['members']}
            # Topic confidence is relative to this query's best topic. A low absolute
            # category score must not suppress every fact for an indirect question.
            best_category=max((hit['score'] for hit in categories),default=0)
            unseen_category=min((hit['score'] for hit in categories),default=0)
            semantic_scores=[dict(hit,score=hit['score']+category_scores.get(hit['name'],unseen_category)-best_category
                                  if category_scores and allowed[hit['name']]['source'].get('category') else hit['score'])
                             for hit in topic_hits if hit['name'] in allowed] if words else []
            semantic=[hit['name'] for hit in sorted(semantic_scores,key=lambda h:h['score'],reverse=True) if hit['score']>=.45]
            # A category is a topic hint; expanding several broad categories adds unrelated
            # facts. Use the best topic only when lexical recall found no concrete material.
            category_members=[name for hit in categories[:1] if hit['score']>=.45
                              for name in hit['members'] if name in allowed] if words and not lexical else []
            semantic=fuse_rankings(semantic,category_members)
            names=fuse_rankings(lexical,semantic)
            trace={'local':evidence,'semantic_scores':semantic_scores,'semantic':semantic,'category_fallback':category_members,'base_order':list(names)}
            if provider:
                pool=names[:64]
                if not pool:continue
                try:
                    category=context.get('retrieval',{}).get('topic_categories',{}).get(topic)
                    rank_query=(category+'：' if category else '')+scoring_query(search)
                    ranked=await rank(client,provider,rank_query,scoring_documents([allowed[name] for name in pool]),len(pool))
                    ranked=sorted(ranked,key=lambda r:r['relevance_score'],reverse=True)
                    reranked=[pool[r['index']] for r in ranked]
                    names=fuse_rankings(lexical,semantic,reranked)
                    trace['rerank']=[{'name':pool[r['index']],'score':r['relevance_score']} for r in ranked]
                except (httpx.HTTPError,KeyError,ValueError,TypeError) as exc:
                    context.setdefault('retrieval',{})['rerank_error']='相关性筛选暂不可用，已保留本地与语义排序'
                    logging.getLogger(__name__).warning('Reranking failed; keeping fused retrieval: %s',type(exc).__name__)
            trace['order']=list(names)
            context.setdefault('retrieval',{}).setdefault('rankings',{})[topic]=trace
            rankings.append([allowed[name] for name in names])
    remaining=20000
    # A round-robin gives each topic space before another topic fills the budget.
    for batch in zip_longest(*rankings):
        for row in batch:
            if row is None or any(row['candidate_index']==x['candidate_index'] or near_duplicate(row,x) for x in selected):continue
            if len(row['text'])>remaining:continue
            selected.append(row);remaining-=len(row['text'])
            if len(selected)==limit:break
        if len(selected)==limit:break
    linked = {row['source']['goal_id'] for row in selected
              if row['group']=='life_entries' and row['source'].get('goal_id')}
    present = {row['source']['id'] for row in selected if row['group']=='life_goals'}
    for row in rows:
        if row['group']!='life_goals' or row['source']['id'] not in linked - present:
            continue
        if len(row['text'])>remaining:continue
        selected.append(row);remaining-=len(row['text'])
        present.add(row['source']['id'])
    return core,selected,[row for ranking in rankings for row in ranking]


def selected_context(context, selected):
    result=dict(context)
    for group in GROUPS:
        result[group]=[row['source'] for row in selected if row['group']==group]
    result['role_material']=[r for r in context.get('role_material',[]) if r.get('pinned')]+result['role_material']
    return result


def render(context,core,selected):
    blocks=['当前时间：'+context['current_time'],'人物背景：\n'+'\n'.join(core)]
    relations=[]
    for row in context.get('friend_relationships',[]):
        name=row.get('name','好友')
        relations.append('Miko 已向'+name+'介绍过自己。' if row.get('miko_introduced')
                         else 'Miko 尚未向'+name+'介绍自己。')
        for key,subject in [('owner_relationship','橙汁'),('miko_relationship','Miko')]:
            if subject=='橙汁' and 'owner' not in context.get('_subjects',set()):continue
            if row.get(key):relations.append(subject+'与'+name+'：'+row[key])
    if relations:blocks.append('当前关系：\n'+'\n'.join(relations))
    if selected:blocks.append('相关资料：\n'+'\n'.join('- '+r['text'] for r in selected))
    if context.get('owner_feedback'):
        blocks.append('回复批注：\n' + '\n'.join(
            '- 原回复：' + r['reply'] + '\n  批注：' + r['feedback']
            for r in context['owner_feedback']))
    return '\n\n'.join(blocks)

def pending_contacts(records):
    start = 0
    for i, row in enumerate(records):
        if row.get('author', {}).get('kind') in ('miko', 'owner'):
            start = i + 1
    return [r for r in records[start:] if r.get('author', {}).get('kind') == 'contact']


def render_dialogue(request):
    records=request['chat_records'];lines=[]
    for row in records:
        name=NAMES.get(row['author']['kind'],row['author']['name'])
        if request.get('group') and row['author']['kind'] == 'contact':
            name += '（' + row['author'].get('id', '') + '）'
        line=f"[{row.get('message_id', '')}] {name}：{row['text']}"
        if row.get('created_at') is not None:
            stamp=datetime.fromtimestamp(row['created_at']/1000,ZoneInfo('Asia/Shanghai'))
            line=stamp.strftime('[%Y-%m-%d %H:%M:%S] ')+line
        # Text already rendered once. Retain non-text segments for quotes/attachments.
        extra=[s for s in row.get('segments',[]) if s.get('type')!='text']
        if extra:line+=' '+json.dumps(extra,ensure_ascii=False)
        if row.get('quoted_message'):line+=' 引用内容：'+json.dumps(row['quoted_message'],ensure_ascii=False)
        lines.append(line)
    blocks=['最近对话：\n'+'\n'.join(lines)]
    if request.get('group'):
        blocks.insert(0, '当前群聊：' + request['group']['name'] + '（' + request['group']['id'] + '）')
    target=request.get('reply_to')
    pending = pending_contacts(records)
    if pending:
        blocks.append('本次回应：\n'+'\n'.join(
            f"[{r.get('message_id', '')}] {r['author']['name']}：{r['text']}" for r in pending))
    elif target:blocks.append('本次回应：'+target['author']['name']+'的消息 '+str(target['message_id']))
    if request['available_faces']:
        blocks.append('可选 QQ 表情：'+'；'.join(r['face_id']+' '+r['name'] for r in request['available_faces']))
    if request.get('available_stickers'):blocks.append('可选表情包：'+json.dumps(request['available_stickers'],ensure_ascii=False))
    if request.get('available_reactions'):
        blocks.append('可选消息表情回应：'+json.dumps(request['available_reactions'],ensure_ascii=False)
                      +'\n可回应的消息编号：'+json.dumps(request['reaction_targets']))
    # Existing approved output contract stays structured; conversational input does not.
    blocks.append('输出格式：'+json.dumps(request['output_format'],ensure_ascii=False))
    if request.get('optional_bubble_formats'):
        blocks.append('可选气泡格式：'+json.dumps(request['optional_bubble_formats'],ensure_ascii=False))
    return '\n\n'.join(blocks)


async def compose_qq_context(chat, records, key, history_tools=None):
    contacts=[chat.canonical_query(r['text']) for r in records if r['author']['kind']=='contact']
    pending = pending_contacts(records)
    query='\n'.join(r['text'] for r in pending) if pending else records[-1]['text']
    query = chat.canonical_query(query)
    previous=contacts[:-len(pending)] if pending else contacts[:-1]
    participants={r['author']['id']:r['author']['name'] for r in records if r['author']['kind']=='contact'}
    speaker={'id':pending[-1]['author']['id'],'text':pending[-1]['text'],
             'by_text':{chat.canonical_query(r['text']):r['author']['id'] for r in pending}} if pending else None
    context=await chat.memory.context(query,key,previous=previous,participants=participants,speaker=speaker,raw=True)
    core,selected,ranked=await select(chat.memory.reranker,query,context,local=chat.memory.local)
    result = render(context,core,selected)
    if history_tools:
        evidence = history_tools.seed(selected_context(context, selected), query)
        if evidence:
            result += '\n\n' + history_tools.contract['title'] + '：\n' + json.dumps(evidence, ensure_ascii=False)
    if context.get('owner_feedback'):
        from miko.settings import REPO_ROOT
        result += '\n\n' + (REPO_ROOT / 'config/defaults/feedback-context.txt').read_text(encoding='utf-8').strip()
    return result
