# -*- coding: utf-8 -*-
"""v1.2.0 服务接口测试：估算、磁盘、配置、专栏分组回写。"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from xetdl import config  # noqa: E402
from xetdl import server as server_mod  # noqa: E402


class TestServerV12(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server_mod.ThreadedServer((config.HOST_BIND, 0), server_mod.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, path, method='GET', body=None):
        url = 'http://%s:%d%s' % (config.HOST_BIND, self.port, path)
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read().decode('utf-8')
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw

    def test_courses_api_groups_by_column(self):
        """推一份两专栏的计划，回读时课时必须带上 group / group_index。"""
        course = {
            'title': '分组测试课', 'product_id': 'p_big',
            'columns': [{'product_id': 'p_a', 'name': '甲专栏'},
                        {'product_id': 'p_b', 'name': '乙专栏'}],
            'lessons': [
                {'rid': 'a1', 'title': '甲-1', 'rtype': 4, 'start_at': '2025.01.01 09:00',
                 'product_id': 'p_a'},
                {'rid': 'a2', 'title': '甲-2', 'rtype': 4, 'start_at': '2025.01.02 09:00',
                 'product_id': 'p_a'},
                {'rid': 'b1', 'title': '乙-1', 'rtype': 4, 'start_at': '2025.02.01 09:00',
                 'product_id': 'p_b'},
            ],
            'courseware': [],
        }
        code, j = self.req('/api/courses', 'POST', {'course': course})
        self.assertEqual(code, 200)
        self.assertTrue(j['ok'])
        self.assertEqual(j['columns'], 2)

        code, got = self.req('/api/courses')
        self.assertEqual(got['title'], '分组测试课')
        by_rid = {l['rid']: l for l in got['lessons']}
        self.assertEqual(by_rid['a1']['group'], 'p_a')
        self.assertEqual(by_rid['a1']['group_name'], '甲专栏')
        self.assertEqual(by_rid['a1']['group_index'], 1)
        self.assertEqual(by_rid['a2']['group_index'], 2)
        self.assertEqual(by_rid['b1']['group'], 'p_b')
        self.assertEqual(by_rid['b1']['group_index'], 1)   # 组内从 1 重新编号

    def test_courses_api_accepts_plain_id_columns(self):
        """老扩展只发 ['p_x']，服务端也要能归一（并接受）。"""
        course = {'title': '旧格式', 'lessons': [
            {'rid': 'l1', 'title': '一讲', 'rtype': 4, 'product_id': 'p_x'}],
            'columns': ['p_x']}
        code, j = self.req('/api/courses', 'POST', {'course': course})
        self.assertEqual(code, 200)
        self.assertTrue(j['ok'])

    def test_disk_api(self):
        code, j = self.req('/api/disk?path=' + urllib.request.quote(os.path.expanduser('~')))
        self.assertEqual(code, 200)
        self.assertTrue(j['ok'])
        self.assertGreater(j['free'], 0)

    def test_settings_api_roundtrip(self):
        old_dir, old_path = config.SETTINGS_DIR, config.SETTINGS_PATH
        tmp = tempfile.mkdtemp()
        config.SETTINGS_DIR = os.path.join(tmp, '.xetdl')
        config.SETTINGS_PATH = os.path.join(config.SETTINGS_DIR, 'settings.json')
        try:
            code, j = self.req('/api/settings', 'POST',
                               {'threads': 7, 'jobs': 3, 'sizing_workers': 5})
            self.assertEqual(code, 200)
            self.assertTrue(j['ok'])
            self.assertEqual(j['settings']['threads'], 7)
            code, got = self.req('/api/settings')
            self.assertEqual(got['settings']['threads'], 7)
            self.assertEqual(got['settings']['sizing_workers'], 5)
        finally:
            config.SETTINGS_DIR, config.SETTINGS_PATH = old_dir, old_path
            shutil.rmtree(tmp, ignore_errors=True)

    def _wait_idle(self, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            code, snap = self.req('/api/estimate')
            if not snap['running']:
                return snap
            time.sleep(0.1)
        return snap

    def test_estimate_api_starts_and_finishes(self):
        """没有播放地址的课时会被跳过，但任务本身要能正常开始+结束。"""
        self._wait_idle()          # 上一个测试可能残留一轮
        code, j = self.req('/api/estimate', 'POST', {
            'lessons': [{'rid': 'x1', 'title': '无地址', 'playlist_url': ''}],
            'workers': 1, 'sample': 2})
        self.assertEqual(code, 200)
        self.assertTrue(j['ok'])
        snap = self._wait_idle()
        self.assertFalse(snap['running'])
        self.assertEqual(snap['total'], 1)

    def test_estimator_rejects_double_start(self):
        """已经在估的时候再 start() 要返回 False，不能把上一轮结果搞乱。

        直接测 Estimator：HTTP 那层太快结束，用它测这个会变得脆弱。
        """
        from xetdl.estimate import Estimator

        class Slow(Estimator):
            def _run(self, lessons, workers, sample):
                try:
                    time.sleep(0.6)
                finally:
                    with self._lock:
                        self.running = False

        e = Slow()
        self.assertTrue(e.start([{'rid': 'a'}]))
        self.assertFalse(e.start([{'rid': 'b'}]))
        time.sleep(1.0)
        self.assertFalse(e.snapshot()['running'])

    def test_estimate_api_rejects_empty(self):
        """既没传 lessons、服务端也没有课程时返回 400。"""
        saved = server_mod._STATE.get('course')
        server_mod._STATE['course'] = None
        try:
            self.req('/api/estimate', 'POST', {'lessons': []})
            self.fail('应当返回 400')
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
        finally:
            server_mod._STATE['course'] = saved

    def test_estimate_cancel(self):
        code, j = self.req('/api/estimate/cancel', 'POST', {})
        self.assertEqual(code, 200)
        self.assertIn('running', j)

    def test_job_snapshot_has_bytes_fields(self):
        out = tempfile.mkdtemp()
        try:
            code, j = self.req('/api/jobs', 'POST', {
                'course': {'title': '体积字段课', 'lessons': []},
                'out_dir': out, 'bytes_planned': 1234567})
            self.assertEqual(code, 200)
            self.assertEqual(j['bytes_planned'], 1234567)
            self.assertIn('bytes', j)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_index_page_has_new_controls(self):
        code, body = self.req('/')
        self.assertEqual(code, 200)
        for tid in ('btnEstimate', 'columns', 'q', 'typeFilter', 'outDir',
                    'btnSaveCfg', 'diskStat'):
            self.assertIn(tid, body, '控制台缺少 %s' % tid)


if __name__ == '__main__':
    unittest.main()
