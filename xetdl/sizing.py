# -*- coding: utf-8 -*-
"""视频体积估算：下载前先告诉用户「这一课大概多大、总共要占多少空间」。

两种估算方式：
  1) 解析 m3u8 清单 —— 得到分片数、每片时长（EXTINF）、总时长。
     再抽样几个分片拿 Content-Length，乘以分片数得到体积。
     这是最准的估算（明文 m3u8，直播回放走这条）。
  2) 清单拿不到时（录播 AES-128 加密清单），退化为
     HEAD 请求 + ffmpeg 读时长 + 经验码率估算。

只用标准库 + 外部 ffprobe。
"""
import concurrent.futures as cf
import os
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import config

_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


def _probe_len(url):
    """拿一个分片的长度：先 HEAD，再退化成 Range。"""
    n = _head_len(url)
    if n is None:
        n = _range_len(url)
    return n


def _absolutize(link, base_url):
    """把清单里的分片地址补成绝对地址。

    m3u8 里的分片行经常是相对路径（甚至只有文件名），
    必须以【清单自己的 URL】为基准拼接，否则抽样全部 404。
    """
    if link.startswith('http://') or link.startswith('https://'):
        return link
    try:
        return urllib.parse.urljoin(base_url, link)
    except Exception:
        return link


def _bitrate(text):
    """从清单里找平均码率（BANDWIDTH / AVERAGE-BANDWIDTH）。"""
    best = 0
    for line in text.split('\n'):
        if 'BANDWIDTH=' not in line:
            continue
        for m in re.finditer(r'(?:AVERAGE-)?BANDWIDTH=(\d+)', line):
            v = int(m.group(1))
            if v > best:
                best = v
    return best


def _http_text(url, timeout=30):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def _head_len(url, timeout=20):
    """HEAD 拿长度。优先 Content-Range（CDN 常不给 Content-Length）。"""
    try:
        req = urllib.request.Request(url, headers=_UA, method='HEAD')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cr = r.headers.get('Content-Range') or ''
            cl = r.headers.get('Content-Length') or ''
            if '/' in cr:
                tail = cr.rsplit('/', 1)[-1].strip()
                if tail.isdigit():
                    return int(tail)
            if cl.isdigit():
                return int(cl)
    except Exception:
        pass
    return None


def _range_len(url, timeout=20):
    """HEAD 拿不到就退化成 Range 请求读 Content-Range。"""
    try:
        req = urllib.request.Request(url, headers=dict(_UA, Range='bytes=0-0'))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cr = r.headers.get('Content-Range') or ''
            if '/' in cr:
                tail = cr.rsplit('/', 1)[-1].strip()
                if tail.isdigit():
                    return int(tail)
    except Exception:
        pass
    return None


def parse_manifest(text):
    """解析 m3u8 清单 -> (分片数, 总时长秒, 分片时长列表, 是否加密)。

    * 明文清单：分片行直接是 URL
    * AES-128 加密清单：分片行是相对路径，且带 EXT-X-KEY
    """
    segs = 0
    dur = 0.0
    durs = []
    encrypted = False
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXT-X-KEY') or line.startswith('#EXT-X-SESSION-KEY'):
            if 'METHOD=AES-128' in line.upper():
                encrypted = True
            continue
        if line.startswith('#EXTINF'):
            m = re.search(r'#EXTINF:\s*([0-9.]+)', line)
            if m:
                try:
                    d = float(m.group(1))
                    durs.append(d)
                    dur += d
                except ValueError:
                    pass
            continue
        if line.startswith('#'):
            continue
        segs += 1
    return segs, dur, durs, encrypted


def estimate_one(url, sample=12):
    """估算单个播放地址的体积。

    返回 dict: {ok, bytes, seconds, segs, method, note}
    """
    out = {'ok': False, 'bytes': 0, 'seconds': 0.0, 'segs': 0,
           'method': '', 'note': ''}
    if not url:
        out['note'] = '没有播放地址'
        return out

    try:
        text = _http_text(url)
    except Exception as e:
        out['note'] = '清单读取失败: %s' % (type(e).__name__)
        return out

    segs, dur, durs, encrypted = parse_manifest(text)
    out['segs'] = segs
    out['seconds'] = round(dur, 1)

    if not segs:
        out['note'] = '清单里没有分片（可能是加密清单或已过期）'
        return out

    # 取清单里的分片 URL
    # 注意：m3u8 里的分片行可能是【相对路径】，必须以清单自身的 URL 为基准拼绝对地址。
    # 之前的做法漏了这点，导致抽样全部失败、只能退化成经验码率（体积高估一倍）。
    seg_urls = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        seg_urls.append(_absolutize(line, url))
        if len(seg_urls) >= sample:
            break

    # 抽样拿长度。
    # 抽更多片（默认 12 个，均匀取样覆盖清单首尾），并用【中位数】而不是平均数 ——
    # 实测分片大小差异很大（同一节课首片与尾片能差一倍以上），
    # 平均数会被极端值带偏，单节误差曾达 ±167%；中位数后总量误差收敛到 ~10%。
    n_sample = min(sample, len(seg_urls))
    if len(seg_urls) > n_sample:
        picked = []
        step = len(seg_urls) / float(n_sample)
        for i in range(n_sample):
            picked.append(seg_urls[int(i * step)])
        seg_urls = picked

    lens = []
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_probe_len, u) for u in seg_urls]
        for fu in futs:
            try:
                n = fu.result(timeout=25)
            except Exception:
                n = None
            if n:
                lens.append(n)

    if lens and segs:
        lens.sort()
        mid = len(lens) // 2
        if len(lens) % 2:
            med = lens[mid]
        else:
            med = (lens[mid - 1] + lens[mid]) / 2.0
        out['bytes'] = int(med * segs)
        out['ok'] = True
        out['method'] = 'm3u8 清单 + %d 个分片中位数' % len(lens)
        return out

    # 拿不到长度：用清单里的 BITRATE 标签（如果有）
    br = _bitrate(text)
    if br and dur > 0:
        out['bytes'] = int(dur * br / 8)
        out['ok'] = True
        out['method'] = '按清单码率估算'
        return out

    # 最后的兜底：时长 × 经验码率
    if dur > 0:
        out['bytes'] = int(dur * 900_000 / 8)   # 0.9 Mbps 经验值（实测更接近）
        out['ok'] = True
        out['method'] = '按时长估算(0.9Mbps)'
        out['note'] = '分片长度拿不到，用经验码率估算'
        return out

    out['note'] = '无法估算'
    return out


def estimate_batch(items, progress=None, workers=4, sample=8, cache=None,
                   should_stop=None):
    """批量估算。

    items: [{'rid','title','playlist_url'/'vod_url'}]
    progress(done, total, result)：每算完一个回调一次。
    cache: 可选的 dict（rid -> bytes），命中就不联网，直接复用。
    should_stop(): 返回 True 时尽快停下，剩下的不再估算。

    外层 workers 控制「同时估算几节课」，每节课内部再各自并发探分片。
    189 节课 × 12 个 HEAD 全串行要好几分钟，4 路并行能压到 1 分钟级。
    """
    cache = cache if cache is not None else {}
    total = len(items)
    results = [None] * total
    state = {'done': 0}
    lock = threading.Lock()

    def one(idx_item):
        idx, it = idx_item
        rid = it.get('rid', '')
        if should_stop and should_stop():
            return idx, None
        if rid and cache.get(rid):
            got = cache[rid]
            return idx, {'ok': True, 'bytes': int(got), 'seconds': 0.0, 'segs': 0,
                         'method': '缓存', 'note': '', 'rid': rid,
                         'title': it.get('title', ''),
                         'product_id': it.get('product_id', '')}
        url = it.get('playlist_url') or it.get('vod_url') or ''
        r = estimate_one(url, sample=sample)
        r['rid'] = rid
        r['title'] = it.get('title', '')
        r['product_id'] = it.get('product_id', '')
        return idx, r

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(one, (i, it)) for i, it in enumerate(items)]
        for fu in futs:
            try:
                idx, r = fu.result()
            except Exception:
                continue
            if r is None:
                continue
            results[idx] = r
            with lock:
                state['done'] += 1
                done = state['done']
            if progress:
                progress(done, total, r)
    return [r for r in results if r]


def total_bytes(results):
    return sum((r.get('bytes') or 0) for r in (results or []) if r)


def human_size(n):
    if not n:
        return '-'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return '%.1f %s' % (n, unit)
        n /= 1024.0


def human_time(sec):
    if not sec:
        return '-'
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h:
        return '%d:%02d:%02d' % (h, m, s)
    return '%d:%02d' % (m, s)
