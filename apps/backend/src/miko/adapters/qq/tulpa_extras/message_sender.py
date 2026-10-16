"""Tulpa's target verification connected to Miko's native message IDs."""
from miko.adapters.qq.onebot import SendError


class QQSender:
    def __init__(self, config, transport):
        self.client = transport

    def check_target(self, target):
        account, kind, peer = target['conversation_id'].split(':')
        if target['platform'] != 'qq' or kind != 'group' or not all(p.isascii() and p.isdigit() for p in (account, peer)):
            raise SendError('回应目标不是当前 QQ 群')
        if self.client.login() != account:
            raise SendError('QQ 账号已变化')
        name = self.client.target(account, 'group-' + peer)
        return dict(account=account, kind=kind, peer=peer, name=name)

    def verify_chat_message(self, address, response_target):
        mid = response_target['onebot_message_id']
        if not isinstance(mid, str) or not mid.isascii() or not mid.lstrip('-').isdigit() or not -(2**63) < int(mid) < 2**63 or int(mid) == 0:
            raise SendError('原消息编号无效')
        info = self.client.call('get_msg', dict(message_id=int(mid)))
        sender = info.get('sender', {})
        if (str(info.get('message_id')) != mid or info.get('message_type') != 'group'
            or str(info.get('group_id')) != address['peer'] or str(info.get('self_id')) != address['account']
            or str(sender.get('user_id')) != response_target['sender_id']
            or type(info.get('time')) is not int or info['time'] != response_target['timestamp'] // 1000
            or any(info.get(k) for k in ('recalled', 'is_recalled', 'deleted', 'is_deleted'))):
            raise SendError('无法核对回应原消息，或原消息已撤回')
        return info
