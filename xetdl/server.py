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
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import config
from .jobs import JobManager

_manager = JobManager()
_STATE = {'course': None, 'course_at': 0}


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
                'ffmpeg': bool(shutil.which('ffmpeg')),
                'ffprobe': bool(shutil.which('ffprobe')),
                'default_out': config.DEFAULT_OUT,
                'legal': config.LEGAL_NOTICE,
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
            return self._json({'out': config.DEFAULT_OUT,
                              'threads': config.DEFAULT_THREADS,
                              'jobs': config.DEFAULT_JOBS})
        if p == '/api/courses':
            return self._json(_STATE.get('course') or {})
        if p == '/bridge.js':
            return self._bridge()
        if p == '/api/store':
            # 店铺商品清单（匿名可读，但需要 category_goods_id）
            return self._json({'error': 'use the browser bridge for store listing'})
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if p == '/api/courses':
            # 浏览器扩展 / 控制台桥把采集到的课程计划送进来
            body = self._body()
            course = body.get('course') or body
            if not course or not course.get('lessons'):
                return self._json({'error': 'empty course'}, 400)
            _STATE['course'] = course
            _STATE['course_at'] = time.time()
            return self._json({'ok': True, 'lessons': len(course.get('lessons') or []),
                              'title': course.get('title')})
        if p == '/api/jobs':
            body = self._body()
            course = body.get('course')
            if not course:
                return self._json({'error': 'missing course'}, 400)
            out_dir = body.get('out_dir') or config.DEFAULT_OUT
            os.makedirs(out_dir, exist_ok=True)
            job = _manager.submit(course, out_dir,
                                  body.get('threads'), body.get('jobs'))
            _manager.prune()
            return self._json(job.snapshot())
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


class ThreadedServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port=None, open_browser=True):
    port = port or config.DEFAULT_PORT
    httpd = ThreadedServer((config.HOST_BIND, port), Handler)
    url = 'http://%s:%d/' % (config.HOST_BIND, port)
    print('xet-course-downloader %s' % config.VERSION)
    print('控制台: %s' % url)
    if open_browser:
        threading.Timer(1.0, lambda: _open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        httpd.server_close()


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
