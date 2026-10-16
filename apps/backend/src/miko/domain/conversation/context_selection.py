"""QQ context selection: stored ownership, reranking and readable context."""
import json,re
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from miko.adapters.memory.rerank import rank

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
    return subject in subjects if subject in ('miko','owner') else False


def near_duplicate(a,b):
    # Suppress repeated presentation only; never overwrite stored memories.
    sa,sb=a['source'],b['source']
    scope=lambda x:(tuple(sorted(x.get('subjects') or [x.get('subject','miko' if 'goal_id' in x else '')])),x.get('peer',''),x.get('day',x.get('as_of','')))
    if scope(sa)!=scope(sb):return False
    normalize=lambda x:re.sub(r'[^\w]','',x)
    x,y=normalize(a['text']),normalize(b['text'])
    if x==y:return True
    # Different dates/numbers or negation may change the fact; keep both.
    if re.findall(r'\d+',x)!=re.findall(r'\d+',y):return False
    if [w for w in ('不','没','未','否') if w in x]!=[w for w in ('不','没','未','否') if w in y]:return False
    grams=lambda t:{t[i:i+3] for i in range(len(t)-2)}
    gx,gy=grams(x),grams(y)
    return bool(gx and gy) and len(gx&gy)/len(gx|gy)>=.85


def scoring_query(topic):
    # Ownership has already been resolved; do not reward repeated speaker names.
    return re.sub(r'^(?:那)?(?:橙汁|Miko|你|他|她)[，,、 ]*', '', topic, flags=re.I) or topic


def scoring_document(row):
    source=row['source']
    if row['group']!='role_material':return row['text']
    content=source.get('content','')
    name=NAMES.get(source.get('subject'))
    if name:content=re.sub(re.escape(name), '', content, flags=re.I)
    category=source.get('category','')
    parts=category.split('/')
    if len(parts)>1 and parts[0]=='喜好与厌恶':
        category=parts[-1]+'；喜好'
    else:category=category.replace('/',' · ')
    return category+'：'+content


def scoring_documents(rows):
    # Short profile tags need their category peers to convey semantic meaning.
    groups={}
    for row in rows:
        source=row['source']
        if row['group']=='role_material' and source.get('category') and len(source.get('content',''))<=80:
            key=(source.get('subject'),source['category'])
            groups.setdefault(key,[]).append(source['content'])
    result=[]
    for row in rows:
        source=row['source'];document=scoring_document(row)
        if row['group']=='role_material' and len(source.get('content',''))<=80:
            peers=groups.get((source.get('subject'),source.get('category')),[])
            if len(peers)>1:
                document=document.split('：',1)[0]+'：'+'；'.join(dict.fromkeys(peers))
        result.append(document)
    return result


async def select(provider,query,context,threshold=.70,limit=5):
    core,rows=candidates(context)
    if not rows:return core,[],[]
    topics=list(dict.fromkeys([query]+topic_queries(query)));ranks=[]
    previous=context.get('_previous_contact_texts',[])
    scopes=[subjects_for(topic,previous) for topic in topics]
    all_subjects=set().union(*scopes)
    pinned_core={f"{NAMES.get(r.get('subject'), r.get('subject', ''))}：{r.get('content', '')}" for r in context.get('role_material', []) if r.get('pinned')}
    core=[line for line in core if line in pinned_core or not line.startswith('橙汁：') or 'owner' in all_subjects]
    context['_subjects']=all_subjects
    if provider is None:
        selected = [{**row, 'candidate_index': i, 'score': None} for i,row in enumerate(rows)
                    if scoped_row(row, all_subjects)][:limit]
        return core, selected, selected
    async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
        for topic,subjects in zip(topics,scopes):
            indexes=[i for i,row in enumerate(rows) if scoped_row(row,subjects)]
            if not indexes:continue
            ranking = await rank(client,provider,scoring_query(topic),scoring_documents([rows[i] for i in indexes]),len(indexes))
            ranks.append([{**rows[indexes[x['index']]],'candidate_index':indexes[x['index']],'score':x['relevance_score'],'query':topic,'scoring_query':scoring_query(topic)} for x in ranking])
    def eligible(row):
        literal=row['source'].get('content','').strip()
        cutoff=.60 if row['group']=='role_material' and row['source'].get('kind')=='fact' else threshold
        return row['score']>=cutoff or (row['group']=='role_material' and 2<=len(literal)<=30 and literal in query)
    selected=[]
    def add(row):
        if not eligible(row):return False
        if any(row['candidate_index']==x['candidate_index'] or near_duplicate(row,x) for x in selected):return False
        selected.append(row);return True
    # Reserve an eligible hit for each explicit topic before filling the shared budget.
    for ranking in ranks:
        for row in ranking:
            if add(row):break
    merged={}
    for ranking in ranks:
        for row in ranking:
            old=merged.get(row['candidate_index'])
            if old is None or old['score']<row['score']:merged[row['candidate_index']]=row
    ranked=sorted(merged.values(),key=lambda x:x['score'],reverse=True)
    for row in ranked:
        if len(selected)>=limit:break
        add(row)
    return core,selected[:limit],ranked

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
    target=request.get('reply_to')
    pending = pending_contacts(records)
    if pending:
        blocks.append('本次回应：\n'+'\n'.join(
            f"[{r.get('message_id', '')}] {r['author']['name']}：{r['text']}" for r in pending))
    elif target:blocks.append('本次回应：'+target['author']['name']+'的消息 '+str(target['message_id']))
    if request['available_faces']:
        blocks.append('可选 QQ 表情：'+'；'.join(r['face_id']+' '+r['name'] for r in request['available_faces']))
    if request.get('available_stickers'):blocks.append('可选表情包：'+json.dumps(request['available_stickers'],ensure_ascii=False))
    # Existing approved output contract stays structured; conversational input does not.
    blocks.append('输出格式：'+json.dumps(request['output_format'],ensure_ascii=False))
    return '\n\n'.join(blocks)


async def compose_qq_context(chat, records, key):
    contacts=[chat.canonical_query(r['text']) for r in records if r['author']['kind']=='contact']
    pending = pending_contacts(records)
    query='\n'.join(r['text'] for r in pending) if pending else records[-1]['text']
    query = chat.canonical_query(query)
    context=deepcopy(await chat.memory.context(query,key) if chat.memory else chat.profiles.context(query,key))
    context['role_material']=chat.profiles.list(key=key,active=True,kind='overview')+chat.profiles.list(key=key,active=True,kind='fact')+[r for r in context.get('role_material',[]) if r.get('kind')=='recent']
    if chat.life:
        trajectory=chat.life.records()
        days=sorted({r['day'] for r in trajectory if r['day']<=context['current_time'][:10]},reverse=True)[:2]
        context.setdefault('life_entries',[]).extend(r for r in trajectory if r['day'] in days)
    context['_previous_contact_texts']=contacts[:-len(pending)] if pending else contacts
    core,selected,ranked=await select(chat.memory.reranker,query,context)
    result = render(context,core,selected)
    if context.get('owner_feedback'):
        from miko.settings import REPO_ROOT
        result += '\n\n' + (REPO_ROOT / 'config/defaults/feedback-context.txt').read_text(encoding='utf-8').strip()
    return result
