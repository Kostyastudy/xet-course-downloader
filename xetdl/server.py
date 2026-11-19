# -*- coding: utf-8 -*-
"""本地 HTTP 服务：网页控制台 + 给浏览器扩展调用的 API。

只用标准库。默认只监听 127.0.0.1。
"""
import io
import json
import mimetypes
import os
import posixpath
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import config
from .estimate import estimator
from .jobs import JobManager

_manager = JobManager()
_STATE = {'course': None, 'course_at': 0, 'course_seq': 0}
# 取数桥的中间进度（只有内存态，供控制台显示「已解析 n/m」）
_BRIDGE = {'done': 0, 'total': 0, 'step': '', 'at': 0}


def _load_cache():
    """服务重启后从磁盘恢复上一次的课程目录。

    没有这一条，每次开控制台都要重跑一次全量采集（192 节要好几分钟）。
    缓存坏了就当没有，绝不影响启动。
    """
    try:
        if not os.path.isfile(config.CACHE_PATH):
            return
        with io.open(config.CACHE_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f) or {}
        if d.get('lessons'):
            _STATE['course'] = d
            try:
                _STATE['course_at'] = os.path.getmtime(config.CACHE_PATH)
            except OSError:
                _STATE['course_at'] = time.time()
            _STATE['course_seq'] = 1
    except Exception:
        pass


def _save_cache():
    try:
        os.makedirs(config.SETTINGS_DIR, exist_ok=True)
        with io.open(config.CACHE_PATH, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(_STATE.get('course') or {}, f, ensure_ascii=False)
    except Exception:
        pass


_load_cache()


def _disk_info(path):
    """查磁盘剩余空间，用来提示「这 30 GB 下得下不下」。"""
    out = {'path': path, 'ok': False, 'free': 0, 'total': 0}
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    if not probe or not os.path.isdir(probe):
        probe = os.path.expanduser('~')
    try:
        u = shutil.disk_usage(probe)
        out.update(ok=True, free=u.free, total=u.total, used=u.used, base=probe)
    except Exception as e:
        out['error'] = str(e)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = 'xetdl/' + config.VERSION
    protocol_version = 'HTTP/1.1'

    # ---------- 基础工具 ----------
    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p in ('/', '/index.html'):
            return self._static('index.html')
        if p.startswith('/static/'):
            return self._static(p[len('/static/'):])
        if p == '/api/health':
            return self._json({
                'ok': True, 'version': config.VERSION,
                'ffmpeg': config.tool_ok(config.FFMPEG),
                'ffprobe': config.tool_ok(config.FFPROBE),
                'ffmpeg_path': config.FFMPEG,
                'ffprobe_path': config.FFPROBE,
                'flavor': config.FLAVOR,          # 'py' / 'exe'
                'bundled': config.FROZEN,         # True = ffmpeg 内置
                'port': self.server.server_address[1],
                'default_out': config.DEFAULT_OUT,
                'legal': config.LEGAL_NOTICE,
                'settings_path': config.SETTINGS_PATH,
            })
        if p == '/api/jobs':
            return self._json({'jobs': [j.snapshot() for j in _manager.list()]})
        if p.startswith('/api/jobs/'):
            jid = p.rsplit('/', 1)[-1]
            job = _manager.get(jid)
            if not job:
                return self._json({'error': 'not found'}, 404)
            return self._json(job.snapshot())
        if p == '/api/defaults':
            s = config.load_settings()
            return self._json({'out': s['default_out'],
                               'threads': s['threads'],
                               'jobs': s['jobs'],
                               'port': s['port'],
                               'host': s['host'],
                               'workers': s['sizing_workers'],
                               'sample': s['sizing_sample'],
                               'settings_path': config.SETTINGS_PATH,
                               'settings_writable': config.settings_file_writable()})
        if p == '/api/settings':
            return self._json({'ok': True, 'settings': config.load_settings(),
                               'path': config.SETTINGS_PATH})
        if p == '/api/estimate':
            return self._json(estimator.snapshot())
        if p == '/api/disk':
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            path = (q.get('path') or [config.DEFAULT_OUT])[0]
            return self._json(_disk_info(path))
        if p == '/api/courses':
            # 返回最近一次收到的计划 + 同步序号/时间。
            # 序号是必需的：否则客户端会把「上一轮留下的旧计划」误当成
            # 「这一轮刚采集到的」，导致抓取器假成功。
            # 课程字段平铺在最外层，与旧的返回结构保持兼容。
            course = dict(_STATE.get('course') or {})
            course['lessons'] = course.get('lessons') or []
            course['seq'] = _STATE.get('course_seq', 0)
            course['at'] = _STATE.get('course_at', 0)
            return self._json(course)
        if p == '/bridge.js':
            return self._bridge()
        if p == '/api/bridge/progress':
            return self._json(dict(_BRIDGE))
        if p == '/api/export-extension':
            return self._export_extension()
        if p == '/api/store':
            # 店铺商品清单（匿名可读，但需要 category_goods_id）
            return self._json({'error': 'use the browser bridge for store listing'})
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if p == '/api/courses':
            # 浏览器扩展 / 控制台桥把采集到的课程计划送进来。
            # 存之前先归一：套上专栏分组（大课包 187 节课要分到 14 个专栏里），
            # 并补齐专栏名字。这样前端拿到的就是可以直接分组展示的结构。
            body = self._body()
            raw = body.get('course') or body
            if not raw or not raw.get('lessons'):
                return self._json({'error': 'empty course'}, 400)
            from .model import Course
            course = Course.from_dict(raw)
            course.sort()
            course_dict = course.to_dict()
            _STATE['course'] = course_dict
            _STATE['course_at'] = time.time()
            _STATE['course_seq'] = _STATE.get('course_seq', 0) + 1
            estimator.reset()      # 换了课程，旧的估算结果作废
            _save_cache()          # 落盘：重启后不必重跑一次全量采集
            return self._json({'ok': True,
                               'lessons': len(course_dict.get('lessons') or []),
                               'columns': len(course_dict.get('columns') or []),
                               'title': course_dict.get('title'),
                               'seq': _STATE['course_seq']})
        if p == '/api/jobs':
            body = self._body()
            course = body.get('course')
            if not course:
                return self._json({'error': 'missing course'}, 400)
            out_dir = body.get('out_dir') or config.DEFAULT_OUT
            os.makedirs(out_dir, exist_ok=True)
            job = _manager.submit(course, out_dir,
                                  body.get('threads'), body.get('jobs'))
            # 把前端传来的预计体积挂到任务上，进度条就能显示「已下 X / 预计 Y」
            try:
                job.bytes_planned = int(body.get('bytes_planned') or 0)
            except (TypeError, ValueError):
                job.bytes_planned = 0
            _manager.prune()
            return self._json(job.snapshot())
        if p == '/api/estimate':
            # 起一轮体积估算
            body = self._body()
            lessons = body.get('lessons') or []
            if not lessons:
                course = _STATE.get('course') or {}
                lessons = [{'rid': l.get('rid'), 'title': l.get('title'),
                            'playlist_url': l.get('playlist_url') or '',
                            'vod_url': l.get('vod_url') or ''}
                           for l in (course.get('lessons') or [])]
            if not lessons:
                return self._json({'error': '没有可估算的课时'}, 400)
            ok = estimator.start(lessons, body.get('workers'), body.get('sample'))
            return self._json({'ok': ok, 'state': estimator.snapshot()})
        if p == '/api/estimate/cancel':
            estimator.cancel()
            return self._json(estimator.snapshot())
        if p == '/api/bridge/progress':
            # 取数桥在页面里把「已解析 n/m」推进来，控制台轮询显示。
            body = self._body()
            _BRIDGE['done'] = int(body.get('done') or 0)
            _BRIDGE['total'] = int(body.get('total') or 0)
            _BRIDGE['step'] = str(body.get('step') or '')[:200]
            _BRIDGE['at'] = time.time()
            return self._json({'ok': True})
        if p == '/api/settings':
            body = self._body()
            saved = config.save_settings(body)
            return self._json({'ok': True, 'settings': saved,
                               'path': config.SETTINGS_PATH,
                               'note': '端口/默认目录等改了要重启下载器才生效'})
        if p.startswith('/api/jobs/') and p.endswith('/cancel'):
            jid = p.split('/')[3]
            job = _manager.get(jid)
            if not job:
                return self._json({'error': 'not found'}, 404)
            job.cancel()
            return self._json(job.snapshot())
        if p == '/api/open':
            body = self._body()
            path = body.get('path') or ''
            if not os.path.exists(path):
                return self._json({'error': 'path not found'}, 404)
            try:
                if sys.platform.startswith('win'):
                    os.startfile(path)      # noqa: S606  (Windows 专用)
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', path])
                else:
                    subprocess.Popen(['xdg-open', path])
                return self._json({'ok': True})
            except Exception as e:
                return self._json({'error': str(e)}, 500)
        return self._json({'error': 'not found'}, 404)

    # ---------- 静态文件 ----------
    def _static(self, rel):
        rel = posixpath.normpath(rel).lstrip('/')
        full = os.path.join(config.WEB_DIR, rel)
        if not os.path.abspath(full).startswith(os.path.abspath(config.WEB_DIR)):
            return self._json({'error': 'forbidden'}, 403)
        if not os.path.isfile(full):
            return self._json({'error': 'not found'}, 404)
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript',):
            ctype += '; charset=utf-8'
        with open(full, 'rb') as f:
            self._send(200, f.read(), ctype)

    # ---------- 取数桥 ----------
    def _bridge(self):
        """下发 extension/content.js —— 扩展与控制台桥共用同一份实现。

        只把 __XETDL_LOCAL__ 占位符换成本服务的真实地址，
        这样用户在 8777 以外的端口启动时也能用。
        """
        src = os.path.join(config.EXT_DIR, 'content.js')
        if not os.path.isfile(src):
            return self._json({'error': 'content.js not found'}, 404)
        try:
            with open(src, 'r', encoding='utf-8') as f:
                code = f.read()
        except Exception as e:
            return self._json({'error': str(e)}, 500)
        local = 'http://%s:%d' % (config.HOST_BIND, self.server.server_address[1])
        code = code.replace('__XETDL_LOCAL__', local)
        self._send(200, code, 'application/javascript; charset=utf-8')

    # ---------- 导出自带扩展 ----------
    def _export_extension(self):
        """把 extension/ 打成 zip 落到下载目录。

        exe 是单文件，用户手上没有 extension 文件夹；这里让他点一下就能
        在本机落出一份「可加载已解压的扩展」目录。
        """
        if not os.path.isdir(config.EXT_DIR):
            return self._json({'error': 'extension 目录不存在'}, 404)
        names = [n for n in sorted(os.listdir(config.EXT_DIR))
                 if os.path.isfile(os.path.join(config.EXT_DIR, n))]
        if not names:
            return self._json({'error': 'extension 目录是空的'}, 404)
        try:
            dest_dir = _pick_export_dir()
            os.makedirs(dest_dir, exist_ok=True)
            zip_path = os.path.join(dest_dir, '小鹅通下载器-扩展.zip')
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
                for n in names:
                    z.write(os.path.join(config.EXT_DIR, n), os.path.join('extension', n))
        except Exception as e:
            return self._json({'error': '%s: %s' % (type(e).__name__, e)}, 500)
        return self._json({'ok': True, 'path': zip_path,
                           'files': names,
                           'howto': '解压后在同名目录加载：edge://extensions → 开发者模式 → '
                                    '加载已解压的扩展程序'})


def _pick_export_dir():
    """优先落到「下载」目录，退而求其次用主目录。"""
    home = os.path.expanduser('~')
    for cand in (os.path.join(home, 'Downloads'), os.path.join(home, '下载'), home):
        if os.path.isdir(cand):
            return cand
    return home


class ThreadedServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    # Windows 的 SO_REUSEADDR 语义是「允许抢占别人已绑的端口」，
    # 不像 Linux 那样仅仅是跳过 TIME_WAIT。开着它会导致：
    #   ① 第二个实例也能绑上同一端口 → 端口顺延逻辑形同虚设；
    #   ② 双轨并存时两个服务抢同一个端口，请求随机落到其中一边，
    #      用户看到的是「配置莫名串了」。Windows 上必须关掉。
    allow_reuse_address = not sys.platform.startswith('win')


def _bind(port, tries=None):
    """绑定端口；被占就往后顺延。

    双轨并存时用户可能同时开着 Python 版（8777）和 exe 版（8787），
    或者旧服务还没退干净 —— 直接崩掉不可接受。
    """
    tries = tries or config.PORT_SCAN_TRIES
    last = None
    for p in range(port, port + max(1, tries)):
        try:
            httpd = ThreadedServer((config.HOST_BIND, p), Handler)
            # 必须回读真实端口：p 可能是 0（交给系统挑），那样 p 本身没有意义
            return httpd, httpd.server_address[1]
        except OSError as e:
            last = e
    raise last if last else OSError('no free port near %d' % port)


def serve(port=None, open_browser=True, app_window=False):
    port = port or config.DEFAULT_PORT
    httpd, port = _bind(port)
    url = 'http://%s:%d/' % (config.HOST_BIND, port)
    print('xet-course-downloader %s  (%s)' % (config.VERSION, config.FLAVOR))
    print('控制台: %s' % url)
    print('配置: %s' % config.SETTINGS_PATH)
    print('ffmpeg: %s' % config.FFMPEG)
    if open_browser:
        def _later():
            if app_window and _open_app(url):
                return
            _open(url)
        threading.Timer(1.0, _later).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        httpd.server_close()


_APP_WINDOW_CANDIDATES = (
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
)


def _open_app(url):
    """用 Edge/Chrome 的 --app 模式开一个无地址栏的独立窗口。

    比默认浏览器标签页更像「一个 App」，也不用 Python 侧做 GUI。
    """
    for exe in _APP_WINDOW_CANDIDATES:
        if os.path.isfile(exe):
            try:
                subprocess.Popen([exe, '--app=' + url, '--window-size=1320,940'],
                                 **config.popen_kwargs())
                return True
            except Exception:
                continue
    return False


def _open(url):
    try:
        if sys.platform.startswith('win'):
            os.startfile(url)      # noqa: S606
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', url])
        else:
            subprocess.Popen(['xdg-open', url])
    except Exception:
        pass


if __name__ == '__main__':
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else None)
