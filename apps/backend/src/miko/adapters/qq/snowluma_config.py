"""Read existing SnowLuma configuration; never start or modify that application.

Schema checked against the 1.14.19/1.14.20 runtime's onebot/src/config.ts:
snapshot ignores global defaults; overlays merge complete adapters by name.
Private tokens stay in this process. Public previews contain only node metadata.
"""
import hashlib
import json


class SetupError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise SetupError(code, message)


def read_json(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            fail('unsupported_format', '配置文件过大，未读取。请使用高级设置手动连接。')
        value = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(value, dict):
            fail('unsupported_format', '配置格式不受支持，应为 JSON 对象。')
        return value, hashlib.sha256(raw).hexdigest()
    except FileNotFoundError:
        fail('file_missing', '配置文件不存在，请重新选择 SnowLuma 文件夹并检测。')
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, SetupError):
            raise
        fail('invalid_json', '配置 JSON 损坏或编码无效。原文件未修改，可在 SnowLuma 修复或使用手动连接。')
    except OSError:
        fail('file_unreadable', '无法读取配置文件，请检查目录权限。')


def node_from(raw, kind, name):
    """Canonicalize only supported local server fields, without guessing a port."""
    result = dict(id=kind + ':' + hashlib.sha256(name.encode()).hexdigest()[:24], name=name, enabled=raw.get('enabled', True),
                  usable=False, url='', reason='', token_present=False)
    token = raw.get('accessToken', '')
    try:
        if type(result['enabled']) is not bool or not isinstance(token, str) or len(token) > 4096:
            raise ValueError()
        if token != token.strip() or any(ord(c) < 32 for c in token):
            raise ValueError()
        result['token_present'] = bool(token)
        host, port, path = raw.get('host', '0.0.0.0'), raw.get('port'), raw.get('path', '/') or '/'
        if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535 or not isinstance(path, str):
            raise ValueError()
        host = host.strip().lower()
        # A wildcard bind is reachable through loopback; never use a LAN address.
        host = {'0.0.0.0': '127.0.0.1', '::': '::1', '[::]': '::1', '[::1]': '::1'}.get(host, host)
        if host not in ('127.0.0.1', 'localhost', '::1'):
            result['reason'] = 'Tulpa 仅支持本机接口，请在 SnowLuma 检查节点绑定地址。'
            return result, token
        if (not path.startswith('/') or path.startswith('//') or any(c in path for c in '?#\\')
                or any(ord(c) <= 32 for c in path) or any(p in ('.', '..') for p in path.split('/'))):
            raise ValueError()
        scheme = 'http' if kind == 'http' else 'ws'
        result['url'] = f'{scheme}://{"[::1]" if host == "::1" else host}:{port}{path}'
        if token and (token in name or token in path):
            result['name'] = '节点名称已隐藏'
            raise ValueError()
        if not result['enabled']:
            result['reason'] = '此节点已禁用，请在 SnowLuma 中自行启用后重新检测。'
        elif kind == 'ws' and str(raw.get('role', 'Universal')).lower() not in ('event', 'universal'):
            result['reason'] = '此 WS 节点只提供 API，不推送事件；请选择 Event 或 Universal 节点。'
        else:
            result['usable'] = True
    except (ValueError, TypeError):
        result.update(url='', reason='节点字段格式不受支持，请检查 host、port、path 和 Token，或手动配置。')
    return result, token


def adapters(sources):
    nodes, private = {'http': [], 'ws': []}, {}
    names = set()
    for field, kind in (('httpServers', 'http'), ('wsServers', 'ws')):
        merged, counter = {}, 0
        for source in sources:
            if 'mode' in source and source['mode'] not in ('snapshot', 'overlay'):
                fail('unsupported_format', '不支持这个账号配置的 mode，请使用手动连接。')
            if 'networks' in source and not isinstance(source['networks'], dict):
                fail('unsupported_format', 'networks 字段格式不受支持。')
            for group in (source.get('networks', {}), source):
                items = group.get(field, [])
                if not isinstance(items, list) or len(items) > 100:
                    fail('unsupported_format', '节点列表格式不受支持或节点过多。')
                for raw in items:
                    if not isinstance(raw, dict):
                        fail('unsupported_format', '节点应为 JSON 对象，请检查 SnowLuma 配置。')
                    name = raw.get('name', '')
                    if not isinstance(name, str) or len(name) > 160 or any(ord(c) < 32 for c in name):
                        fail('unsupported_format', '节点名称格式不受支持。')
                    name = name.strip()
                    if not name:
                        counter += 1
                        while f'{kind}-{counter}' in merged:
                            counter += 1
                        name = f'{kind}-{counter}'
                    merged[name] = raw
        for name, raw in merged.items():
            if name in names:
                fail('ambiguous_nodes', 'HTTP 与 WS 节点名称重复，请先在 SnowLuma 核对，不会盲试节点。')
            names.add(name)
            public, token = node_from(raw, kind, name)
            nodes[kind].append(public)
            private[public['id']] = dict(url=public['url'], token=token)
            if kind == 'http' and raw.get('enableWebSocket') is True:
                # SnowLuma's HTTP server can explicitly expose an Event/Universal
                # websocket on the same host/port/path. This is not a port guess.
                shared, token = node_from(raw, 'ws', name + ' · 同端口事件')
                shared['id'] = 'http-ws:' + hashlib.sha256(name.encode()).hexdigest()[:24]
                nodes['ws'].append(shared)
                private[shared['id']] = dict(url=shared['url'], token=token)
    return nodes, private

