# -*- coding: utf-8 -*-
"""课件/资料下载。

课件附件一般挂在课程的「图文资料」课时下，接口返回的 URL 通常是免登录的 CDN 直链，
所以课件下载不依赖浏览器的登录态。
"""
import io
import os
import re
import urllib.parse

from .downloader import http_get


def ware_filename(item):
    """优先用 download_name（URL 里的显示名），其次用接口 title。"""
    u = item.get('url') or ''
    q = urllib.parse.urlparse(u)
    dn = urllib.parse.parse_qs(q.query).get('download_name')
    if dn:
        n = urllib.parse.unquote(dn[0]).strip()
        if n:
            return _safe(n)
    t = (item.get('title') or '').strip()
    if t:
        return _safe(t)
    return _safe(q.path.rsplit('/', 1)[-1])


def _safe(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '', name or '').strip().strip('.')
    return name or '未命名'


def download_courseware(courseware, dest_dir, threads=6, progress=None, log=None):
    """下载一个资料课时的全部附件，返回 (成功数, 失败列表)。"""
    log = log or (lambda *a, **k: None)
    os.makedirs(dest_dir, exist_ok=True)
    todo = []
    for it in courseware.files:
        if not it.url:
            continue
        name = ware_filename({'url': it.url, 'title': it.title})
        dest = os.path.join(dest_dir, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            continue
        todo.append((it.url, dest, name))

    if not todo:
        return 0, []

    import threading
    import queue
    q = queue.Queue()
    for t in todo:
        q.put(t)
    lock = threading.Lock()
    st = {'ok': 0}
    failed = []

    def worker():
        while True:
            try:
                url, dest, name = q.get_nowait()
            except queue.Empty:
                return
            try:
                data = http_get(url, tries=4, timeout=180)
                with open(dest + '.part', 'wb') as f:
                    f.write(data)
                os.replace(dest + '.part', dest)
                with lock:
                    st['ok'] += 1
                    cur = st['ok']
                log('    ↓ %-52s %.1f KB' % (name[:52], len(data) / 1024.0))
                if progress:
                    progress({'state': 'courseware', 'done': cur, 'total': len(todo)})
            except Exception as e:
                with lock:
                    failed.append((name, str(e)))
                log('    ! %s 下载失败: %s' % (name[:52], e))

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return st['ok'], failed
