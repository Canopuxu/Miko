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
    async def test_local_relevance_ownership_and_duplicate_budget(self):
        context={'role_material': [profile('noise','红丝绒蛋糕'), profile('wrong','电吉他','owner'),
            profile('right','电吉他'),profile('duplicate','电吉他')],
            'people_memory':[dict(id='old',subject='miko',content='路由器断网后重置解决',category='网络',updated_at=1),
                             dict(id='new',subject='miko',content='今天路由器打折',category='网络',updated_at=999)]}
        with patch('miko.domain.conversation.context_selection.rank',side_effect=AssertionError('local must not call model')):
            _,rows,_=await select(None,'你还在练电吉他吗',copy.deepcopy(context))
            self.assertEqual([r['source']['id'] for r in rows],['right'])
            _,rows,_=await select(None,'路由器断网怎么解决的',copy.deepcopy(context))
            self.assertEqual(rows[0]['source']['id'],'old')
            self.assertFalse((await select(None,'哈哈哈哈',copy.deepcopy(context)))[1])

    async def test_followup_and_new_topic_do_not_share_old_topic(self):
        self.assertIn('路由器',retrieval_query('后来怎么解决的',['路由器断网了？']))
        self.assertNotIn('蛋糕',retrieval_query('对了，你还打网球吗',['你喜欢什么蛋糕']))
        context={'role_material':[profile('cake','红丝绒蛋糕'),profile('sport','网球')],
                 '_previous_contact_texts':['你喜欢什么蛋糕']}
        _,rows,_=await select(None,'对了，你还打网球吗',context)
        self.assertEqual([r['source']['id'] for r in rows],['sport'])

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

    def test_no_implicit_reranker_on_chat_only_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'config').mkdir()
            (root/'config/provider.local.json').write_text('{"api_base":"https://example.test/v1","api_key":"test","model":"chat"}')
            self.assertIsNone(Settings(root,7870).load_rerank())


if __name__=='__main__':unittest.main()
