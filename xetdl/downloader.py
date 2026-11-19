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
    """带重试的 GET，返回 bytes。

    必须分块读：大 m3u8（实测 320 KB）一次性 r.read() 会抛
    http.client.IncompleteRead，导致这节直接判失败、一片都不下。
    """
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    raise RuntimeError('HTTP %s' % r.status)
                buf = []
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    buf.append(chunk)
                data = b''.join(buf)
                if not data:
                    raise RuntimeError('empty body')
                return data
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


def valid_seg_file(path):
    """加密分片只按体积判合法。

    密文首字节不是 0x47，长度也不保证是 188 的整数倍
    （小鹅通用「同一文件 + Range 参数」切片，按 AES 块 16 字节对齐）。
    """
    try:
        sz = os.path.getsize(path)
        return sz >= 1000 and sz % 16 == 0
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


def _unique_names(tasks):
    """给小鹅通「同一文件 + Range 参数」的分片补唯一序号名。

    有的 m3u8 里 2000+ 个分片全叫 v.f146750_0.ts，只有 start/end 不同。
    若按原文件名落盘，第一个下完其余全被当成「已存在」跳过 —— 只会得到 1 片。
    """
    names = [n for _, n, _ in tasks]
    if len(set(names)) == len(names):
        return tasks
    out = []
    for i, (_, n, u) in enumerate(tasks):
        stem, ext = os.path.splitext(n)
        if not ext:
            ext = '.ts'
        out.append((i, '%s_%05d%s' % (stem, i, ext), u))
    return out


def _pull_segments(seg_dir, tasks, threads, progress, encrypted=False):
    """并发下载分片（已存在且合法的跳过）。返回 (done, bytes, failed_list)。

    encrypted=True 时跳过 TS 同步字节校验 —— AES-128 密文不是 0x47 开头，
    但体积仍然是 188 的整数倍，所以只按体积判合法性。
    """
    q = queue.Queue()
    for t in tasks:
        q.put(t)
    lock = threading.Lock()
    st = {'done': 0, 'bytes': 0}
    failed = []
    local = threading.local()

    def ok_file(p):
        return valid_ts(p) if not encrypted else valid_seg_file(p)

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
            if os.path.exists(dest) and ok_file(dest):
                with lock:
                    st['done'] += 1
                    st['bytes'] += os.path.getsize(dest)
                continue
            tmp = dest + '.part'
            ok = False
            why = ''
            for attempt in range(6):
                try:
                    data = http_get(url, tries=1, timeout=120)
                    if len(data) < 1000:
                        raise RuntimeError('too small %d' % len(data))
                    if encrypted:
                        if len(data) % 16 != 0:
                            raise RuntimeError('密文体积不是 16 的整数倍 (%d)' % len(data))
                    elif not valid_ts_bytes(data):
                        raise RuntimeError('不是合法 TS 分片 (%d bytes)' % len(data))
                    with open(tmp, 'wb') as f:
                        f.write(data)
                    os.replace(tmp, dest)
                    with lock:
                        st['done'] += 1
                        st['bytes'] += len(data)
                    ok = True
                    break
                except Exception as e:
                    why = repr(e)
                    time.sleep(min(0.8 * (attempt + 1), 6))
            if not ok:
                with lock:
                    failed.append((name, why))
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


def _is_encrypted(text):
    return any(l.strip().upper().startswith('#EXT-X-KEY') and
               'METHOD=NONE' not in l.upper() for l in text.split('\n'))


def _rewrite_playlist(text, tasks, seg_dir, key_line):
    """生成本地 m3u8：分片路径换成本地文件名，KEY 行原样保留（指向远端取密钥）。"""
    out = ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-TARGETDURATION:10',
           '#EXT-X-MEDIA-SEQUENCE:0']
    if key_line:
        out.append(key_line)
    durs = []
    cur = 0.0
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('#EXTINF'):
            try:
                cur = float(line.split(':', 1)[1].split(',')[0])
            except Exception:
                cur = 0.0
        elif line.startswith('#EXT-X-ENDLIST'):
            continue
        elif line and not line.startswith('#'):
            durs.append(cur)
    for i, (_, name, _) in enumerate(tasks):
        d = durs[i] if i < len(durs) else 0.0
        out.append('#EXTINF:%.6f,' % d)
        out.append(name)
    out.append('#EXT-X-ENDLIST')
    return '\n'.join(out) + '\n'


def download_replay(lesson, out_dir, threads=None, progress=None, log=None):
    """下载直播回放（m3u8，可能 AES-128 加密）。

    明文：并发拉分片 → concat 合并 → ffprobe 自检。
    加密：并发拉【密文】分片（远端不重复解密，走满带宽），
          再让 ffmpeg 读本地 m3u8 + 远端密钥一次性解密拼接。

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
    tasks = _unique_names(tasks)
    total = len(tasks)

    encrypted = _is_encrypted(txt)
    key_line = ''
    if encrypted:
        for line in txt.split('\n'):
            if line.strip().upper().startswith('#EXT-X-KEY'):
                key_line = line.strip()
                break
        log('  [ENC] %s 为 AES-128 加密，走「并发拉密文 + ffmpeg 解密拼接」' % lesson.title)

    for rnd in range(1, 4):
        def prog(st, rnd=rnd):
            if progress:
                progress({'state': 'downloading', 'done': st['done'], 'total': total,
                          'bytes': st['bytes'], 'round': rnd})
        done, nbytes, failed = _pull_segments(seg_dir, tasks, threads, prog, encrypted)
        if failed:
            log('  [PART] %s 本轮 %d/%d 片失败，进入第%d轮补拉（原因示例：%s）'
                % (lesson.title, len(failed), total, rnd + 1,
                   failed[0][1] if isinstance(failed[0], tuple) else failed[0]))
            for name, why in [(f if isinstance(f, tuple) else (f, '')) for f in failed[:5]]:
                log('         - %s %s' % (name, why))
            if rnd >= 3:
                return False, '%d 个分片下载失败（重试 3 轮）' % len(failed)
            time.sleep(3)
            continue
        if progress:
            progress({'state': 'merging', 'done': done, 'total': total, 'bytes': nbytes})

        if encrypted:
            local_pl = os.path.join(seg_dir, 'local.m3u8')
            with io.open(local_pl, 'w', encoding='utf-8', newline='\n') as f:
                f.write(_rewrite_playlist(txt, tasks, seg_dir, key_line))
            ok, bad = _merge_local_hls(local_pl, mp4, seg_dir)
        else:
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


def _merge_local_hls(local_pl, mp4, seg_dir):
    """ffmpeg 读本地 m3u8（分片在本地，密钥从远端取）解密并拼接。"""
    tmp = mp4 + '.tmp.mp4'
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    base = [config.FFMPEG, '-y', '-allowed_extensions', 'ALL',
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto,httpproxy',
            '-i', local_pl, '-c', 'copy']
    attempts = [
        base + ['-bsf:a', 'aac_adtstoasc', '-movflags', '+faststart', tmp],
        base + ['-bsf:a', 'aac_adtstoasc', tmp],
        base + [tmp],
    ]
    last_err = ''
    for cmd in attempts:
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and os.path.exists(tmp):
            ok, dur, errs = probe_duration(tmp)
            if ok and len(errs) <= 20:
                os.replace(tmp, mp4)
                return True, len(errs)
        last_err = (r.stderr or b'').decode('utf-8', 'replace')[-300:]
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False, -1 if last_err else 0


class _PlaylistShim(object):
    """把录播的 vod_url 伪装成 playlist_url，复用 download_replay。"""

    def __init__(self, lesson, url):
        self._l = lesson
        self.playlist_url = url
        self.rid = lesson.rid
        self.title = lesson.title

    def __getattr__(self, k):
        return getattr(self._l, k)


def download_vod(lesson, out_dir, threads=None, progress=None, log=None):
    """下载录播（m3u8，可能是 AES-128 加密）。

    实测（2026-11-19）：让 ffmpeg 直接拉远端加密 HLS 只有约 0.8 倍速
    （≈实时播放，因为 ffmpeg 单线程串行解密）。录播和直播回放本质都是
    HLS，所以这里直接复用 download_replay 的「并发拉密文 + 本地 m3u8
    + ffmpeg 一次解密拼接」方案，速度能跑满带宽。
    """
    log = log or log_cb
    url = lesson.vod_url
    if not url:
        return False, '缺少录播地址'
    # 复用回放的下载器：把 vod_url 当作 playlist_url
    shim = _PlaylistShim(lesson, url)
    return download_replay(shim, out_dir, threads=threads,
                           progress=progress, log=log)
