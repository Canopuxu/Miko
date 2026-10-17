import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from miko.domain.conversation.context_selection import retrieval_query
from retrieval_support import select
from miko.settings import Settings, RerankConfig


def profile(id, text, subject='miko', category='兴趣'):
    return dict(id=id, content=text, subject=subject, category=category, kind='fact')


class RetrievalFoundationTests(unittest.IsolatedAsyncioTestCase):
    async def test_matched_life_entry_brings_its_goal_status_for_web_and_qq(self):
        from miko.domain.conversation.context_selection import selected_context, render
        context={'current_time':'2026-10-17T19:00:00+08:00',
            'life_entries':[dict(id='entry',day='2026-10-16',text='调试网页布局',goal_id='site')],
            'life_goals':[dict(id='site',text='做个人主页',status='completed',target_date='2026-10-16'),
                          dict(id='other',text='学吉他',status='active',target_date='2026-11-01')]}
        core,rows,_=await select(None,'布局弄好了吗',context,limit=1)
        picked=selected_context(context,rows)
        self.assertEqual([r['id'] for r in picked['life_entries']],['entry'])
        self.assertEqual([r['id'] for r in picked['life_goals']],['site'])
        self.assertEqual(picked['life_goals'][0]['status'],'completed')
        self.assertIn('做个人主页',render(context,core,rows))
        self.assertIn('completed',render(context,core,rows))

    async def test_local_relevance_ownership_and_duplicate_budget(self):
        context={'role_material': [profile('noise','红丝绒蛋糕'), profile('wrong','电吉他','owner'),
            profile('right','电吉他'),profile('duplicate','电吉他')],
            'people_memory':[dict(id='old',subject='miko',content='路由器断网后重置解决',category='网络',updated_at=1),
                             dict(id='new',subject='miko',content='今天路由器打折',category='网络',updated_at=999)]}
        with patch('miko.domain.conversation.context_selection.rank',side_effect=AssertionError('local must not call model')):
            _,rows,_=await select(None,'你还在练电吉他吗',copy.deepcopy(context))
            self.assertEqual([r['source']['id'] for r in rows],['right'])
            _,rows,_=await select(None,'路由器断网怎么解决的',copy.deepcopy(context))
            self.assertEqual([r['source']['id'] for r in rows],['old'])
            self.assertFalse((await select(None,'哈哈哈哈',copy.deepcopy(context)))[1])

    async def test_followup_and_new_topic_do_not_share_old_topic(self):
        self.assertIn('路由器',retrieval_query('后来怎么解决的',['路由器断网了？']))
        self.assertNotIn('蛋糕',retrieval_query('对了，你还打网球吗',['你喜欢什么蛋糕']))
        context={'role_material':[profile('cake','红丝绒蛋糕'),profile('sport','网球')],
                 '_previous_contact_texts':['你喜欢什么蛋糕']}
        _,rows,_=await select(None,'对了，你还打网球吗',context)
        self.assertEqual([r['source']['id'] for r in rows],['sport'])

    async def test_natural_profile_topics_do_not_pull_generic_words_or_old_episodes(self):
        context={'role_material':[profile('music','ヨルシカ',category='喜好与厌恶/音乐与作品'),
                    profile('book','文学',category='兴趣与能力/阅读'),
                    profile('job','自由画师',category='兴趣与能力/学习与工作'),
                    profile('cake','红丝绒蛋糕',category='喜好与厌恶/喜欢的食物')],
                 'people_memory':[dict(id='noise',subject='miko',category='工具',topic='电脑',content='大量工作在电脑完成')],
                 'life_entries':[dict(day='2026-10-14',text='戴耳机听歌，吃了蛋糕')],
                 '_previous_contact_texts':['你还练电吉他吗']}
        for query,expected in [('你耳机里平时放谁啊','music'),('睡前想找本书看看，你一般看啥','book'),
                               ('你不用每天去公司打卡吗','job'),('我想买块蛋糕，你喜欢哪种','cake')]:
            _,rows,_=await select(None,query,copy.deepcopy(context))
            self.assertEqual([r['source']['id'] for r in rows],[expected])
        self.assertFalse((await select(None,'哈哈哈哈',copy.deepcopy(context)))[1])
        self.assertFalse((await select(None,'晚安啦',copy.deepcopy(context)))[1])
        self.assertFalse((await select(None,'那我先撤啦',copy.deepcopy(context)))[1])
        from miko.adapters.memory.retrieval import query_categories
        self.assertFalse(query_categories('这事你记住了吗，别吃亏'))

    async def test_user_defined_topic_and_semantic_scope_stay_separate_per_question(self):
        context={'role_material':[profile('camera','富士相机',category='摄影器材'),
                                 profile('film','胶片模拟',category='摄影器材'),
                                 profile('game','三角洲','owner','游戏')],
                 'retrieval':{'hits':[{'name':'profile:film','score':.8}],
                              'by_topic':{'橙汁玩三角洲吗':[{'name':'profile:game','score':.8}]}}}
        _,rows,_=await select(None,'你的富士相机还用吗？橙汁玩三角洲吗',context)
        self.assertEqual({r['source']['id'] for r in rows},{'camera','game','film'})

    async def test_topics_dates_and_failed_reranking_keep_base(self):
        context={'current_time':'2026-10-17T12:00:00+08:00',
                 'role_material':[profile('miko','陈绮贞',category='音乐'),profile('owner','许嵩','owner','音乐')],
                 'life_entries':[dict(day='2026-10-16',text='吃了番茄鸡蛋饭'),
                                 dict(day='2026-10-17',text='吃了番茄鸡蛋面'),
                                 dict(day='2026-10-18',text='吃了番茄鸡蛋饺子')]}
        _,rows,_=await select(None,'你喜欢陈绮贞？橙汁喜欢许嵩吗',copy.deepcopy(context),limit=2)
        self.assertEqual({r['source']['id'] for r in rows},{'miko','owner'})
        base=(await select(None,'今天吃番茄鸡蛋了吗',copy.deepcopy(context)))[1]
        self.assertEqual([r['source']['day'] for r in base],['2026-10-17'])
        config=RerankConfig(api_base='https://example.test/rerank',api_key='test',model='test',format='standard')
        with patch('miko.domain.conversation.context_selection.rank',side_effect=httpx.ConnectError('offline')):
            fallback=(await select(config,'今天吃番茄鸡蛋了吗',copy.deepcopy(context)))[1]
        self.assertEqual(fallback,base)

    async def test_friend_self_reference_and_group_isolation(self):
        context={'role_material':[profile('miko','硕士')],
            'people_memory':[dict(id='alice',subject='qq:1',content='211硕士',category='学历',peer='qq:a:group-g'),
                             dict(id='bob',subject='qq:2',content='本科',category='学历',peer='qq:a:group-g')],
            '_group_speaker':{'id':'qq:1','text':'我的学历还记得吗'},
            '_contact_names':{'qq:1':'甲','qq:2':'乙'}}
        _,rows,_=await select(None,'我的学历还记得吗',context)
        self.assertEqual([r['source']['id'] for r in rows],['alice'])
        _,rows,_=await select(None,'我学历怎么样，你还记得吗',context)
        self.assertEqual([r['source']['id'] for r in rows],['alice'])
        context['_group_speaker']['by_text']={'我的学历还记得吗':'qq:1','我的学历呢':'qq:2'}
        _,rows,_=await select(None,'我的学历还记得吗\n我的学历呢',context)
        self.assertEqual({r['source']['id'] for r in rows},{'alice','bob'})
        from miko.domain.conversation.context_selection import topic_scopes
        _,scopes=topic_scopes('你最近忙什么',{'_contact_names':{'qq:1':'忙'}})
        self.assertEqual(scopes,[{'miko'}])

    async def test_rerank_is_an_additional_ranking_and_empty_or_failed_result_keeps_hybrid(self):
        config=RerankConfig(api_base='https://example.test/rerank',api_key='test',model='test',format='standard')
        context={'role_material':[profile('literature','文学',category='阅读'),profile('fantasy','奇幻故事',category='阅读')]}
        with patch('miko.domain.conversation.context_selection.rank',return_value=[{'index':1,'relevance_score':.02},{'index':0,'relevance_score':.001}]):
            _,rows,_=await select(config,'睡前找本书，你平时看啥',context)
        self.assertEqual({r['source']['id'] for r in rows},{'literature','fantasy'})
        semantic={'role_material':[profile('job','绘本插画师',category='职业')],
                  'retrieval':{'hits':[{'name':'profile:job','score':.8}]}}
        base=(await select(None,'那些稿子是你的主业吗',copy.deepcopy(semantic)))[1]
        self.assertTrue(base)
        for response in ([],httpx.ConnectError('offline')):
            options={'side_effect':response} if isinstance(response,Exception) else {'return_value':response}
            with patch('miko.domain.conversation.context_selection.rank',**options):
                rows=(await select(config,'那些稿子是你的主业吗',copy.deepcopy(semantic)))[1]
            self.assertEqual(rows,base)

    async def test_semantic_recall_can_add_a_fact_from_a_different_category(self):
        context={'role_material':[profile('singer','陈绮贞',category='音乐')],
                 'people_memory':[dict(id='memo',subject='miko',content='喜欢听ヨルシカ',category='个人情况')],
                 'retrieval':{'hits':[{'name':'item:memo','score':.9}]}}
        _,rows,_=await select(None,'你耳机里一般放谁',context)
        self.assertEqual({r['source']['id'] for r in rows},{'singer','memo'})

    async def test_semantic_topic_evidence_reduces_unrelated_short_fact(self):
        context={'people_memory':[dict(id='guitar',subject='miko',content='会练琴',category='兴趣爱好',topic='乐器')],
                 'retrieval':{'hits':[{'name':'item:guitar','score':.47}],
                              'category_hits':{'对了你养过仓鼠吗':[{'category':'宠物','score':.7,'members':[]},{'category':'兴趣爱好/乐器','score':.30,'members':['item:guitar']}]}}}
        self.assertFalse((await select(None,'对了你养过仓鼠吗',context))[1])
        # A useful specific topic can support a short fact with a weaker individual vector.
        context['retrieval']['category_hits']={'你还玩乐器吗':[{'category':'兴趣爱好/乐器','score':.7,'members':['item:guitar']}]}
        self.assertEqual([r['source']['id'] for r in (await select(None,'你还玩乐器吗',context))[1]],['guitar'])

    async def test_local_character_recall_and_soft_category_recover_useful_facts(self):
        context={'people_memory':[dict(id='practice',subject='miko',category='学习记录',topic='练习',
                                      content='刚学吉他时按弦会痛，后来习惯了'),
                                  dict(id='camera',subject='miko',category='摄影器材',topic='器材',
                                      content='一直用富士相机拍照')],
                 'role_material':[profile('job','自由画师',category='工作')]}
        _,rows,_=await select(None,'你之前按弦会手疼吗',copy.deepcopy(context))
        self.assertIn('practice',[r['source']['id'] for r in rows])
        _,rows,_=await select(None,'你还在公司用富士相机吗',copy.deepcopy(context))
        self.assertIn('camera',[r['source']['id'] for r in rows])

    def test_no_implicit_reranker_on_chat_only_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'config').mkdir()
            (root/'config/provider.local.json').write_text('{"api_base":"https://example.test/v1","api_key":"test","model":"chat"}')
            self.assertIsNone(Settings(root,7870).load_rerank())


if __name__=='__main__':unittest.main()
