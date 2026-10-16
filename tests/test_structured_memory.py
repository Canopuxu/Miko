import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from miko.adapters.memory.service import Memory
from miko.domain.conversation.chat import ChatService
from miko.settings import Settings


class StructuredTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.local=Path(self.tmp.name)
        (self.local/'config').mkdir()
        (self.local/'config/memory.local.json').write_text('{"engine":"memobase"}',encoding='utf-8')
        (self.local/'config/provider.local.json').write_text(json.dumps({
            'api_base':'http://127.0.0.1:1/v1','api_key':'test','model':'test'}),encoding='utf-8')
        self.chat=ChatService(Settings(self.local,7870))
        self.memory=Memory(self.chat)
        self.social=self.memory.social
        self.social.loop=asyncio.get_running_loop()
        self.calls=[]

        async def complete(system,message,model=None,json_output=False):
            self.calls.append(message)
            if json_output:
                text=json.dumps({'facts':[
                    {'subject':'owner','topic':'兴趣','sub_topic':'乐器','memo':'贝斯' if '改练贝斯' in message else '吉他'},
                    {'subject':'miko','topic':'兴趣','sub_topic':'乐器','memo':'键盘'}],
                    'events':[{'subjects':['owner','miko'],'date':'2026-10-14','text':'一起练了半小时乐器'}]},ensure_ascii=False)
            else:
                import ast
                text='\n'.join(str(c['memo_id'])+'. UPDATE::'+c['new_info'] for c in ast.literal_eval(message.strip()))
            return {'text':text,'usage':{}}
        self.chat.complete_memory=complete

    async def asyncTearDown(self):
        await self.memory.close()
        self.tmp.cleanup()

    def add_turn(self,text):
        self.chat.store.begin_turn('example','turn-'+str(len(self.calls))+text,text)
        self.chat.store.finish('turn-'+str(len(self.calls))+text,'我在练键盘','completed',None)
        self.social.store.sync()
        return self.social.listing()['scopes'][0]['id']

    async def test_update_recall_manual_restore_and_source_recall(self):
        sid=self.add_turn('我在练吉他')
        result=await asyncio.to_thread(self.social.consolidate,sid,None,True)
        self.assertEqual(result['status'],'updated')
        owner=self.memory.structured.items(subject='owner')[0]
        miko=self.memory.structured.items(subject='miko')[0]
        self.assertEqual((owner['content'],miko['content']),('吉他','键盘'))
        self.assertTrue(self.memory.structured.days(subject='owner'))
        shared=self.memory.structured.days(subject='owner')[0]
        self.assertEqual(shared['id'],self.memory.structured.days(subject='miko')[0]['id'])
        from miko.domain.conversation.context_selection import candidates,scoped_row
        _,rows=candidates({'dated_experiences':[shared]})
        self.assertTrue(scoped_row(rows[0],{'miko'}))
        self.assertTrue(scoped_row(rows[0],{'owner'}))
        self.assertIn('橙汁',rows[0]['text'])
        self.add_turn('我暂停吉他，现在改练贝斯')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        row=self.memory.structured.items(subject='owner')[0]
        self.assertEqual(row['id'],owner['id'])
        self.assertEqual(row['content'],'贝斯')
        self.assertEqual((await self.memory.context('贝斯'))['people_memory'][0]['content'],'贝斯')
        edited=self.memory.structured.edit(row['id'],'主要练贝斯，吉他偶尔练',row['revision'])
        self.add_turn('我暂停吉他，现在改练贝斯')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        fresh=self.memory.structured.detail(row['id'])
        self.assertEqual(fresh['content'],edited['content'])
        old=next(h for h in fresh['history'] if json.loads(h['snapshot'])['automatic']=='吉他')
        restored=self.memory.structured.restore(row['id'],old['id'],fresh['revision'])
        self.assertEqual(restored['content'],'吉他')
        self.memory.structured.edit(row['id'],'',restored['revision'])
        with self.social.store.connect() as db:
            source=db.execute('SELECT message_id FROM memory_sources WHERE item_id=? LIMIT 1',(row['id'],)).fetchone()[0]
            db.execute('DELETE FROM messages WHERE id=?',(source,))
        self.assertFalse(self.memory.structured.items('贝斯',subject='owner',active=True))

    async def test_delete_memory_and_shared_event_removes_retrieval_keeps_chat(self):
        import httpx
        from fastapi import FastAPI
        from miko.api.memory import memory_router
        from test_memory import Embeddings
        sid = self.add_turn('我在练吉他')
        await asyncio.to_thread(self.social.consolidate, sid, None, True)
        item = self.memory.structured.items(subject='owner')[0]
        event = self.memory.structured.days(subject='owner')[0]
        original_messages = self.chat.store.messages('example')
        (self.local/'config/embedding.local.json').write_text('{"model":"test"}', encoding='utf-8')
        self.memory.configure_index()
        self.memory.index._embedding_pool._cache['embedding'] = Embeddings()
        await self.memory.sync()
        app = FastAPI()
        app.include_router(memory_router(self.memory))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            self.assertEqual((await client.delete('/api/memory/items/'+item['id'])).status_code, 200)
            self.assertEqual((await client.get('/api/memory/items/'+item['id'])).status_code, 404)
            self.assertEqual((await client.delete('/api/memory/days/'+event['id'])).status_code, 200)
            self.assertEqual((await client.delete('/api/memory/days/'+event['id'])).status_code, 404)
        self.assertFalse(self.memory.structured.items(subject='owner'))
        self.assertFalse(self.memory.structured.days(subject='owner'))
        self.assertFalse(self.memory.structured.days(subject='miko'))
        files = self.memory.index.database.recall_file_repo.list_recall_files()
        self.assertNotIn('item:'+item['id'], [r.name for r in files.values()])
        self.assertNotIn(event['id'], [r.name for r in files.values()])
        context = await self.memory.context('吉他')
        self.assertNotIn(item['id'], [r['id'] for r in context['people_memory']])
        self.assertEqual(self.chat.store.messages('example'), original_messages)
        with self.social.store.connect() as db:
            for deleted_id in (item['id'], event['id']):
                for table, column in [('memory_fts','item_id'), ('memory_sources','item_id'),
                                      ('memory_audit','item_id'), ('memory_event_people','event_id')]:
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE {column}=?', (deleted_id,)).fetchone()[0], 0)

    async def test_edit_content_category_and_name_updates_recall_and_restores_version(self):
        import httpx
        from fastapi import FastAPI
        from miko.api.memory import memory_router
        from test_memory import Embeddings
        sid = self.add_turn('我在练吉他')
        await asyncio.to_thread(self.social.consolidate, sid, None, True)
        row = self.memory.structured.items(subject='owner')[0]
        (self.local/'config/embedding.local.json').write_text('{"model":"test"}', encoding='utf-8')
        self.memory.configure_index()
        self.memory.index._embedding_pool._cache['embedding'] = Embeddings()
        app = FastAPI()
        app.include_router(memory_router(self.memory))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            response = await client.patch('/api/memory/items/'+row['id'], json={
                'text':'尤克里里', 'category':'音乐', 'topic':'练习', 'revision':row['revision']})
            self.assertEqual(response.status_code, 200, response.text)
            edited = response.json()
            self.assertEqual((edited['category'], edited['topic'], edited['content']), ('音乐','练习','尤克里里'))
            self.assertEqual((await client.patch('/api/memory/items/'+row['id'], json={
                'text':'旧编辑', 'revision':row['revision']})).status_code, 409)
        self.assertEqual(self.memory.structured.items('尤克里里', category='音乐')[0]['id'], row['id'])
        self.assertFalse(self.memory.structured.items('吉他', subject='owner'))
        files = self.memory.index.database.recall_file_repo.list_recall_files()
        indexed = next(r for r in files.values() if r.name=='item:'+row['id'])
        self.assertIn('音乐', indexed.content)
        self.assertIn('尤克里里', indexed.content)
        history = next(h for h in edited['history'] if h['kind']=='before_manual')
        restored = self.memory.structured.restore(row['id'], history['id'], edited['revision'])
        self.assertEqual((restored['category'],restored['topic'],restored['content']),
                         (row['category'],row['topic'],row['content']))
        self.add_turn('我暂停吉他，现在改练贝斯')
        await asyncio.to_thread(self.social.consolidate, sid, None, True)
        self.assertEqual(self.memory.structured.detail(row['id'])['content'], row['content'])

    async def test_batch_invalidated_by_manual_edit_and_feedback_withdrawal(self):
        sid=self.add_turn('我在练吉他')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        owner=self.memory.structured.items(subject='owner')[0]
        self.add_turn('我暂停吉他，现在改练贝斯')
        captured=self.chat.complete_memory
        touched=False
        async def concurrent_edit(system,message,model=None,json_output=False):
            nonlocal touched
            if not touched:
                touched=True
                self.memory.structured.edit(owner['id'],'用户确认仍练吉他',owner['revision'])
            return await captured(system,message,model,json_output)
        self.chat.complete_memory=concurrent_edit
        result=await asyncio.to_thread(self.social.consolidate,sid,None,True)
        self.assertEqual(result['status'],'superseded')
        self.assertEqual(self.memory.structured.detail(owner['id'])['content'],'用户确认仍练吉他')
        reply=self.chat.store.messages('example')[-1]
        note=self.memory.structured.annotate(reply['id'],'语气稍微轻松一些','global',0)
        self.assertTrue((await self.memory.context('你好'))['owner_feedback'])
        self.memory.structured.annotate(reply['id'],note['note'],'global',note['revision'],False)
        self.assertFalse((await self.memory.context('你好'))['owner_feedback'])
        reviewed=self.memory.structured.review()['replies']
        self.assertTrue(reviewed)
        self.assertEqual(reviewed[0]['conversation_id'],'example')
        self.assertEqual(reviewed[0]['source'],'web')
        self.assertIn('贝斯',reviewed[0]['context'][-1]['text'])
        self.assertLessEqual(len(reviewed[0]['context']),2)

    async def test_changed_subtopic_updates_named_item_and_category_history(self):
        import ast
        original=self.chat.complete_memory
        async def classify(system,message,model=None,json_output=False):
            answer=await original(system,message,model,json_output)
            if json_output:
                value=json.loads(answer['text'])
                fact=value['facts'][0]
                fact['sub_topic']='乐器' if '改练贝斯' in message else '吉他'
                fact['memo']='吉他暂时停练，现在主要练贝斯' if '改练贝斯' in message else '正在练吉他'
                answer['text']=json.dumps(value,ensure_ascii=False)
            return answer
        self.chat.complete_memory=classify
        sid=self.add_turn('我在练吉他')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        self.add_turn('我暂停吉他，现在改练贝斯')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        rows=self.memory.structured.items(subject='owner',category='兴趣',active=True)
        self.assertEqual(len(rows),2)
        self.assertTrue(all('停练' in r['content'] for r in rows))
        revisions={r['id']:r['revision'] for r in rows}
        self.memory.structured.organize('owner','','兴趣',[{'sub_topic':'乐器','memo':'主要练贝斯，吉他暂时停练'}],revisions)
        active=self.memory.structured.items(subject='owner',category='兴趣',active=True)
        self.assertEqual(len(active),1)
        self.assertTrue(self.memory.structured.detail(rows[0]['id'])['history'])


class ReviewAndFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_dates_review_includes_multiple_days_and_excludes_deleted(self):
        import httpx
        from datetime import datetime
        from fastapi import FastAPI
        from miko.api.memory import memory_router
        from miko.storage.chat import ChatStore
        from miko.adapters.memory.tulpa import Projection
        from miko.adapters.memory.structured import StructuredMemory
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=ChatStore(root/'chat.db')
            for key,day in [('old','2026-10-14'),('new','2026-10-15')]:
                source.begin_turn('conversation',key,'你好')
                source.finish(key,'你好呀','completed',None)
                with source.connect() as db:
                    stamp=int(datetime.fromisoformat(day+'T12:00:00').astimezone().timestamp()*1000)
                    db.execute('UPDATE messages SET created_at=? WHERE id=?',(stamp,key))
            store=StructuredMemory(Projection(root/'memory.db',source,True))
            app=FastAPI();app.include_router(memory_router(SimpleNamespace(structured=store)))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                all_days=await client.get('/api/memory/review?day=all')
                self.assertEqual(all_days.status_code,200)
                self.assertEqual([r['id'] for r in all_days.json()['replies']],['new','old'])
                one_day=await client.get('/api/memory/review?day=2026-10-14')
                self.assertEqual([r['id'] for r in one_day.json()['replies']],['old'])
                store.delete_review('old')
                remaining=await client.get('/api/memory/review?day=all')
                self.assertEqual([r['id'] for r in remaining.json()['replies']],['new'])

    async def test_delete_review_keeps_chat_and_removes_feedback(self):
        from miko.storage.chat import ChatStore
        from miko.adapters.memory.tulpa import Projection
        from miko.adapters.memory.structured import StructuredMemory
        from miko.domain.conversation.context_selection import candidates, render
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=ChatStore(root/'chat.db')
            source.begin_turn('web-example','reply-one','早上好')
            source.finish('reply-one','早上好呀','completed',None)
            store=StructuredMemory(Projection(root/'memory.db',source,True))
            store.annotate('reply-one','语气自然一些','global',0)
            feedback=store.feedback('听歌','qq:1:2')
            self.assertEqual(len(feedback),1)
            context={'current_time':'2026-10-14','role_material':[], 'owner_feedback':feedback}
            self.assertEqual(candidates(context),([],[]))
            self.assertIn('语气自然一些',render(context,[],[]))
            # Current-chat feedback cannot cross chats; withdrawn feedback is absent.
            note=store.annotate('reply-one','语气自然一些','friend',1)
            self.assertEqual(store.feedback('听歌','qq:1:2'),[])
            self.assertTrue(store.feedback('听歌','web-example'))
            store.annotate('reply-one','语气自然一些','global',note['revision'],False)
            self.assertEqual(store.feedback('听歌','web-example'),[])
            store.delete_review('reply-one')
            self.assertEqual(store.review()['replies'],[])
            self.assertEqual(len(source.messages('web-example')),2)
            reopened=StructuredMemory(Projection(root/'memory.db',source,True))
            self.assertEqual(reopened.review()['replies'],[])
            with self.assertRaises(ValueError):reopened.annotate('reply-one','新批注','global',0)

if __name__=='__main__':
    unittest.main()
