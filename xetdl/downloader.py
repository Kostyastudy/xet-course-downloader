# -*- coding: utf-8 -*-
"""下载器：分片并发下载 → concat 合并 → ffprobe 自检 → 损坏自动重下。

只用标准库 urllib + 外部 ffmpeg/ffprobe。
"""
import io
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import config

_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
_netlock = threading.Lock()


def log_cb(*a, **k):
    pass


def http_get(url, tries=6, timeout=120):
    """带重试的 GET，返回 bytes。"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    raise RuntimeError('HTTP %s' % r.status)
                return r.read()
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, e)
            time.sleep(min(1.0 * (i + 1), 8))
    raise RuntimeError(last or 'download failed')


def http_get_text(url, tries=6, timeout=90):
    return http_get(url, tries, timeout).decode('utf-8', 'replace')


def valid_ts_bytes(data):
    """TS 分片合法性：188 字节对齐 + 0x47 同步字节。"""
    if len(data) < 188 * 3 or len(data) % 188 != 0:
        return False
    return data[0] == 0x47


def valid_ts(path):
    try:
        sz = os.path.getsize(path)
        if sz < 188 * 3 or sz % 188 != 0:
            return False
        with open(path, 'rb') as f:
            head = f.read(188)
        return bool(head) and head[0] == 0x47
    except Exception:
        return False


def parse_m3u8(text):
    """返回 (base_url, [(name, url), ...])。分片行可能是绝对地址，也可能是相对路径。"""
    base = ''
    segs = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        url = line if line.startswith('http') else base + line
        name = line.split('?')[0].split('/')[-1]
        segs.append((name, url))
    return base, segs


def probe_duration(path, retries=2):
    """ffprobe 读时长与流信息。返回 (ok, duration, errors)。"""
    for _ in range(retries):
        try:
            r = subprocess.run([config.FFPROBE, '-v', 'error', '-show_entries',
                                'format=duration,size:stream=codec_type',
                                '-of', 'json', path],
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=180)
            import json as _json
            j = _json.loads(r.stdout or '{}')
            fmt = j.get('format') or {}
            dur = float(fmt.get('duration') or 0)
            streams = j.get('streams') or []
            errs = [l for l in (r.stderr or '').split('\n')
                    if l.strip() and re.search(
                        r'Invalid|Error splitting|missing picture|corrupt|truncat|no frame', l)]
            has_v = any(s.get('codec_type') == 'video' for s in streams)
            return (dur > 0 and has_v), dur, errs
        except Exception:
            time.sleep(1)
    return False, 0.0, []


def merge_verify(seg_dir, tasks, mp4):
    """concat 合并分片 -> ffprobe 自检。返回 (ok, bad_lines)。"""
    concat = os.path.join(seg_dir, 'concat.txt')
    with io.open(concat, 'w', encoding='utf-8', newline='\n') as f:
        for _, name, _ in tasks:
            f.write("file '%s'\n" % os.path.join(seg_dir, name).replace('\\', '/'))
    tmp = mp4 + '.tmp.mp4'
    base = [config.FFMPEG, '-y', '-f', 'concat', '-safe', '0', '-i', concat]
    attempts = [
        base + ['-c', 'copy', '-bsf:a', 'aac_adtstoasc', '-movflags', '+faststart', tmp],
        base + ['-c', 'copy', '-bsf:a', 'aac_adtstoasc', tmp],
        base + ['-c', 'copy', tmp],
    ]
    for cmd in attempts:
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and os.path.exists(tmp):
            break
    if not os.path.exists(tmp):
        return False, -1
    ok, dur, errs = probe_duration(tmp)
    if not ok or len(errs) > 20:
        return False, len(errs)
    os.replace(tmp, mp4)
    return True, len(errs)


def _pull_segments(seg_dir, tasks, threads, progress):
    """并发下载分片（已存在且合法的跳过）。返回 (done, bytes, failed_list)。"""
    q = queue.Queue()
    for t in tasks:
        q.put(t)
    lock = threading.Lock()
    st = {'done': 0, 'bytes': 0}
    failed = []
    local = threading.local()

    def sess():
        if not hasattr(local, 's'):
            local.s = urllib.request
        return local.s

    def worker():
        while True:
            try:
                _, name, url = q.get_nowait()
            except queue.Empty:
                return
            dest = os.path.join(seg_dir, name)
            if os.path.exists(dest) and valid_ts(dest):
                with lock:
                    st['done'] += 1
                    st['bytes'] += os.path.getsize(dest)
                continue
            tmp = dest + '.part'
            ok = False
            for attempt in range(6):
                try:
                    data = http_get(url, tries=1, timeout=120)
                    if len(data) < 1000:
                        raise RuntimeError('too small %d' % len(data))
                    if not valid_ts_bytes(data):
                        raise RuntimeError('不是合法 TS 分片 (%d bytes)' % len(data))
                    with open(tmp, 'wb') as f:
                        f.write(data)
                    os.replace(tmp, dest)
                    with lock:
                        st['done'] += 1
                        st['bytes'] += len(data)
                    ok = True
                    break
                except Exception:
                    time.sleep(min(0.8 * (attempt + 1), 6))
            if not ok:
                with lock:
                    failed.append(name)
            if progress and st['done'] % 100 == 0:
                progress(dict(st))

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    if progress:
        progress(dict(st))
    return st['done'], st['bytes'], failed


def download_replay(lesson, out_dir, threads=None, progress=None, log=None):
    """下载直播回放（明文 m3u8，分片自带签名）。

    progress(dict) 用于回报 {done,total,bytes,state,...}
    返回 (ok, message)
    """
    log = log or log_cb
    threads = threads or config.DEFAULT_THREADS
    url = lesson.playlist_url
    if not url:
        return False, '缺少播放列表地址'
    seg_dir = os.path.join(out_dir, '.segs', lesson.rid)
    os.makedirs(seg_dir, exist_ok=True)
    mp4 = os.path.join(out_dir, '视频.mp4')

    try:
        txt = http_get_text(url)
    except Exception as e:
        return False, '取播放列表失败: %s' % e
    base = url.split('?')[0].rsplit('/', 1)[0] + '/'
    segs = [l.strip() for l in txt.split('\n') if l.strip() and not l.startswith('#')]
    if not segs:
        return False, '播放列表为空'
    tasks = [(i, s.split('?')[0].split('/')[-1], (s if s.startswith('http') else base + s))
             for i, s in enumerate(segs)]
    total = len(tasks)

    for rnd in range(1, 4):
        def prog(st, rnd=rnd):
            if progress:
                progress({'state': 'downloading', 'done': st['done'], 'total': total,
                          'bytes': st['bytes'], 'round': rnd})
        done, nbytes, failed = _pull_segments(seg_dir, tasks, threads, prog)
        if failed:
            return False, '%d 个分片下载失败' % len(failed)
        if progress:
            progress({'state': 'merging', 'done': done, 'total': total, 'bytes': nbytes})
        ok, bad = merge_verify(seg_dir, tasks, mp4)
        if ok:
            shutil.rmtree(seg_dir, ignore_errors=True)
            shutil.rmtree(os.path.join(out_dir, '.segs'), ignore_errors=True)
            log('  [OK] %s -> %s' % (lesson.title, os.path.basename(out_dir)))
            return True, 'ok'
        log('  [CORRUPT] %s 第%d轮 ffprobe 报错 %d 行，重下分片' % (lesson.title, rnd, bad))
        if rnd >= 3:
            return False, '合并后校验未通过（ffprobe 报错 %d 行）' % bad
        shutil.rmtree(seg_dir, ignore_errors=True)
        os.makedirs(seg_dir, exist_ok=True)
    return False, '重试 3 轮仍失败'


def download_vod(lesson, out_dir, progress=None, log=None):
    """下载录播（AES-128 m3u8）。用 ffmpeg 直拉，签名现取现用。"""
    log = log or log_cb
    url = lesson.vod_url
    if not url:
        return False, '缺少录播地址'
    os.makedirs(out_dir, exist_ok=True)
    mp4 = os.path.join(out_dir, '视频.mp4')
    tmp = mp4 + '.tmp.mp4'
    if progress:
        progress({'state': 'downloading', 'done': 0, 'total': 0, 'bytes': 0})
    cmd = [config.FFMPEG, '-y', '-allowed_extensions', 'ALL',
           '-protocol_whitelist', 'file,http,https,tcp,tls,crypto,httpproxy',
           '-i', url, '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
           '-movflags', '+faststart', tmp]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        return False, 'ffmpeg 失败: %s' % (r.stderr or b'')[-300:].decode('utf-8', 'replace')
    ok, dur, errs = probe_duration(tmp)
    if not ok:
        if os.path.getsize(tmp) > 10 * 1024 * 1024:
            os.replace(tmp, mp4)
            log('  [WARN] %s ffprobe 读不出时长，按体积收下' % lesson.title)
            return True, 'ok(未校验)'
        return False, 'ffprobe 校验不通过'
    os.replace(tmp, mp4)
    log('  [OK] %s -> %s (%.1f 分钟)' % (lesson.title, os.path.basename(out_dir), dur / 60))
    return True, 'ok'
