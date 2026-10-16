"""QQ image download and encoding adapted from Tulpa (MIT), mcp_chat_media.py."""
import asyncio
import base64
import http.client
import io
import ipaddress
import json
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit
import httpx
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 6 * 1024 * 1024

def remote_target(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if (parts.scheme not in ('http', 'https') or parts.username or parts.password or
        parts.port not in (None, 80, 443) or parts.fragment or
        not any(host == d or host.endswith('.'+d) for d in ('qq.com', 'qq.com.cn', 'qpic.cn', 'gtimg.com', 'gtimg.cn'))):
        raise ValueError('图片来源不是允许的 QQ 图片服务。')
    return parts


def public_addresses(host, port):
    records=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    # TUN proxies commonly synthesize RFC2544 addresses for public domains.
    # Resolve the already-allowlisted hostname via authenticated public DNS;
    # do not exempt private addresses from the download boundary.
    fake=(ipaddress.ip_network('198.18.0.0/15'), ipaddress.ip_network('2001:2::/48'))
    if records and all(any(ipaddress.ip_address(r[4][0]) in network for network in fake) for r in records):
        try:
            with httpx.Client(timeout=4,trust_env=False,follow_redirects=False) as client:
                with client.stream('GET','https://dns.alidns.com/resolve',params={'name':host,'type':'A'}) as response:
                    response.raise_for_status();raw=bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>65536:raise ValueError('DNS response too large')
            answers=json.loads(raw).get('Answer',[])
            records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(a['data'],port)) for a in answers if a.get('type')==1]
        except (httpx.HTTPError,ValueError,TypeError,KeyError):
            raise ValueError('代理使用了虚拟 DNS，但无法取得 QQ 图片的公网地址；请检查网络。') from None
    if not records or any(not ipaddress.ip_address(r[4][0]).is_global for r in records):
        raise ValueError('图片地址指向非公网，已拒绝。')
    return records


def download(url, cancel):
    """DNS pinned per hop, no proxy, no local networks even after redirects."""
    deadline = time.monotonic()+25
    try:
        for _ in range(5):
            parts = remote_target(url)
            port = parts.port or (443 if parts.scheme == 'https' else 80)
            records = public_addresses(parts.hostname, port)
            if cancel.is_set() or time.monotonic() >= deadline:
                raise ValueError('图片读取已取消或超时。')
            family, socktype, proto, _, address = records[0]
            sock = socket.socket(family, socktype, proto)
            conn = http.client.HTTPConnection(parts.hostname, port, timeout=5)
            try:
                sock.settimeout(min(5, max(.1, deadline-time.monotonic())))
                sock.connect(address)
                if parts.scheme == 'https':
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
                conn.sock = sock
                path = parts.path or '/'
                if parts.query:path += '?'+parts.query
                conn.request('GET', path, headers={'User-Agent':'Tulpa/0.5', 'Accept':'image/*', 'Accept-Encoding':'identity'})
                response = conn.getresponse()
                if response.status in (301,302,303,307,308):
                    url = urljoin(url, response.getheader('Location') or '')
                    continue
                if response.status != 200:raise ValueError('QQ 图片暂不可用或地址已过期。')
                if int(response.getheader('Content-Length') or 0) > MAX_BYTES:
                    raise ValueError('图片超过单张 6 MiB 上限。')
                raw = bytearray()
                while True:
                    if cancel.is_set() or time.monotonic() >= deadline:
                        raise ValueError('图片读取已取消或超时。')
                    chunk = response.read(min(65536, MAX_BYTES+1-len(raw)))
                    if not chunk:break
                    raw.extend(chunk)
                    if len(raw)>MAX_BYTES:raise ValueError('图片超过单张 6 MiB 上限。')
                return bytes(raw)
            finally:
                conn.close();sock.close()
        raise ValueError('图片重定向次数过多。')
    except (OSError, http.client.HTTPException):
        raise ValueError('QQ 图片下载失败；可稍后重试，消息接收不受影响。') from None


def pixels(raw):
    if not raw or len(raw)>MAX_BYTES:raise ValueError('图片为空或超过单张 6 MiB 上限。')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('PNG','JPEG','GIF','WEBP'):raise ValueError('暂不支持这种图片格式。')
            total = getattr(im, 'n_frames', 1)
            if total>300 or im.width*im.height*total>80000000 or im.width*im.height>12000000:
                raise ValueError('图片尺寸或动画帧数过大，未处理。')
            size, fmt = list(im.size), im.format
            im.verify()
        frames, indices = [], sorted({0, total//2, total-1})
        with Image.open(io.BytesIO(raw)) as im:
            for index in indices:
                im.seek(index)
                frame = im.convert('RGBA');frame.thumbnail((1400,1400))
                background = Image.new('RGB', frame.size, 'white');background.paste(frame, mask=frame.getchannel('A'))
                out = io.BytesIO();background.save(out, 'JPEG', quality=82)
                frames.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode('ascii')}})
        return dict(format=fmt, dimensions=size, total_frames=total, frame_indices=indices), frames
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, EOFError):
        raise ValueError('图片损坏或未完整下载；不会影响持续聊天和普通工具。') from None


async def attach_images(client, key, records):
    """Attach at most four recent images; URLs remain outside model text."""
    remaining = 4
    cancel = threading.Event()
    try:
        for record in reversed(records[-12:]):
            images = [s for s in record.get('segments', []) if s.get('type') == 'image']
            if not images or not remaining:
                continue
            try:
                value = await client.message(*key, record['message_id'],
                    known_own=record.get('author', {}).get('kind') in ('owner', 'miko'))
                sources = [s.get('data', {}) for s in value.get('message', [])
                           if s.get('type') in ('image', 'mface')]
                for index, segment in enumerate(images):
                    if not remaining:
                        break
                    remaining -= 1
                    try:
                        source = sources[index]
                        file = source.get('file', '')
                        if file.startswith('base64://'):
                            encoded = file[len('base64://'):]
                            if len(encoded) > ((MAX_BYTES + 2) // 3) * 4:
                                raise ValueError('图片超过单张 6 MiB 上限。')
                            raw = base64.b64decode(encoded, validate=True)
                        else:
                            raw = await asyncio.to_thread(download, source.get('url', ''), cancel)
                        info, frames = await asyncio.to_thread(pixels, raw)
                        record.setdefault('_image_inputs', []).extend([
                            {'type': 'text', 'text': f"消息 {record['message_id']} · 图片 {index + 1} · 采样帧 {info['frame_indices']}"},
                            *frames])
                        segment['content_status'] = 'attached'
                    except (ValueError, IndexError):
                        segment['content_status'] = 'unavailable'
            except ValueError:
                for segment in images:
                    segment['content_status'] = 'unavailable'
    finally:
        cancel.set()
    return records
