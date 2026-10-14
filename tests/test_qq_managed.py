from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from miko.adapters.qq.managed import resolve_connection
from miko.settings import write_json


class ManagedQqTests(unittest.TestCase):
    def test_discovers_account_nodes_and_credentials_without_copying(self):
        with TemporaryDirectory() as folder:
            local=Path(folder)
            bridge=local/'runtime/qq-bridge'
            (bridge/'config').mkdir(parents=True)
            write_json(bridge/'package.json',{'name':'@snowluma/runtime','version':'1.14.20'})
            write_json(bridge/'config/onebot_12345.json',{'mode':'snapshot','networks':{
                'httpServers':[{'name':'http','enabled':True,'host':'127.0.0.1','port':3000,'accessToken':'private-http'}],
                'wsServers':[{'name':'events','enabled':True,'host':'127.0.0.1','port':3001,'role':'Event','accessToken':'private-ws'}]}})
            with patch('miko.adapters.qq.managed.bridge_folders',return_value=[bridge]):
                result=resolve_connection(local,'12345',{})
            self.assertEqual(result.account,'12345')
            self.assertEqual(result.http_token.get_secret_value(),'private-http')
            self.assertEqual(result.ws_token.get_secret_value(),'private-ws')
            self.assertEqual(result.http_url,'http://127.0.0.1:3000')
            self.assertNotIn('private-',(local/'config/qq-bridge.local.json').read_text())
            with self.assertRaises(ValueError):
                resolve_connection(local,'different-account',{})

    def test_missing_component_does_not_create_a_fake_connection(self):
        with TemporaryDirectory() as folder:
            with patch('miko.adapters.qq.managed.bridge_folders',return_value=[]):
                with self.assertRaisesRegex(ValueError,'尚未找到'):
                    resolve_connection(Path(folder),'12345',{})


if __name__ == '__main__': unittest.main()
