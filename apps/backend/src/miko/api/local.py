from urllib.parse import urlparse

from fastapi import HTTPException, Request


def local_request(request: Request):
    base = str(request.base_url).rstrip('/')
    if (urlparse(base).hostname not in ('127.0.0.1', 'localhost', '::1', 'testserver')
        or request.headers.get('origin') not in (None, base)
        or request.headers.get('sec-fetch-site') == 'cross-site'):
        raise HTTPException(403, '请从 Miko 本机页面访问')
