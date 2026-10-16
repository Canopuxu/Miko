import json
import unittest
from unittest.mock import patch

import httpx

from miko.adapters.memory.rerank import rank
from miko.adapters.memory.feedback import select_feedback
from miko.domain.conversation.context_selection import select
from miko.settings import RerankConfig


class RerankConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashscope_payload_and_custom_model(self):
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'output':{'results':[{'index':1,'relevance_score':.8}]}})
        config=RerankConfig(api_base='https://ranks.example/custom',api_key='test',model='chosen-ranker')
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            rows=await rank(client,config,'问题',['甲','乙'],1)
        self.assertEqual(rows,[{'index':1,'relevance_score':.8}])
        self.assertEqual(str(requests[0].url),'https://ranks.example/custom')
        self.assertEqual(json.loads(requests[0].content),{'model':'chosen-ranker','input':{'query':'问题','documents':['甲','乙']},'parameters':{'top_n':1}})

    async def test_disabled_reranking_keeps_scoped_feedback_and_uses_local_candidates(self):
        rows=[{'scope':'example','feedback':'相似场景','reply':'回复','context':'问题'},
              {'scope':'global','feedback':'通用反馈','reply':'回复','context':''}]
        result=await select_feedback(None,'问题',rows)
        self.assertEqual([r['feedback'] for r in result],['通用反馈'])
        context={'role_material':[{'subject':'miko','kind':'fact','category':'喜好','content':'文学'}]}
        _,selected,_=await select(None,'你喜欢看书吗',context)
        self.assertEqual(len(selected),1)

    async def test_profiles_and_feedback_use_the_same_selected_service(self):
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'results':[{'index':0,'relevance_score':.95}]})
        original=httpx.AsyncClient
        def client(**kwargs):
            return original(**kwargs,transport=httpx.MockTransport(respond))
        config=RerankConfig(api_base='https://ranks.example/v1/rerank',api_key='test',model='selected-model',format='standard')
        with patch('httpx.AsyncClient',side_effect=client):
            feedback=await select_feedback(config,'晚上吃什么',[
                {'scope':'example','feedback':'简短一些','reply':'原回复','context':'晚上吃什么'}])
            _,selected,_=await select(config,'你喜欢什么',{'role_material':[
                {'subject':'miko','kind':'fact','category':'喜好','content':'文学'}]})
        self.assertEqual(len(feedback),1)
        self.assertEqual(len(selected),1)
        self.assertGreaterEqual(len(requests),2)
        self.assertTrue(all(str(r.url)=='https://ranks.example/v1/rerank' for r in requests))
        self.assertTrue(all(json.loads(r.content)['model']=='selected-model' for r in requests))
