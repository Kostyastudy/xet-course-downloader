# -*- coding: utf-8 -*-
"""xetdl 单元测试。

    cd xet-course-downloader
    python -m unittest discover -s tests -v
"""
import io
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from xetdl import archiver, config, courseware as cw_mod, downloader, model, xetapi  # noqa: E402
from xetdl import server as server_mod  # noqa: E402


# ---------------------------------------------------------------- 模型


class TestModel(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(model.safe_name('高等数学:第1讲/上'), '高等数学第1讲上')
        self.assertEqual(model.safe_name('   '), '未命名')
        self.assertEqual(model.safe_name('a*b?c"d<e>f|g'), 'abcdefg')

    def test_lesson_roundtrip(self):
        l = model.Lesson(rid='l_1', title='测试', rtype=4, start_at='2026.03.01',
                          playlist_url='http://x/a.m3u8')
        d = l.to_dict()
        l2 = model.Lesson.from_dict(d)
        self.assertEqual(l2.rid, 'l_1')
        self.assertEqual(l2.playlist_url, 'http://x/a.m3u8')
        self.assertTrue(l2.is_video)
        self.assertFalse(l2.is_text)

    def test_course_sort(self):
        c = model.Course('测试课')
        c.lessons = [
            model.Lesson('a', '资料', rtype=1),
            model.Lesson('b', '录播', rtype=3),
            model.Lesson('c', '直播2', rtype=4, start_at='2026.03.02'),
            model.Lesson('d', '直播1', rtype=4, start_at='2026.03.01'),
        ]
        c.sort()
        self.assertEqual([l.rid for l in c.lessons], ['d', 'c', 'b', 'a'])
        self.assertEqual([l.index for l in c.lessons], [1, 2, 3, 4])

    def test_plan_dump_load(self):
        c = model.Course('测试课', product_id='p_1')
        c.lessons = [model.Lesson('a', '第一讲', rtype=4, start_at='2026.03.01')]
        c.courseware = [model.Courseware('w', '资料', [model.CoursewareFile('讲义.pdf', 'http://x/a.pdf')])]
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, 'plan.json')
            model.dump_plan(c, p)
            c2 = model.load_plan(p)
            self.assertEqual(c2.title, '测试课')
            self.assertEqual(len(c2.lessons), 1)
            self.assertEqual(c2.courseware[0].files[0].title, '讲义.pdf')
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 接口封装


class TestXetApi(unittest.TestCase):
    def test_parse_column_url(self):
        u = 'https://appisa6gqvx4105.h5.xet.pomoho.com/p/course/column/p_6a1aab3ce4b0694c5bd45cd?type=3'
        r = xetapi.parse_course_url(u)
        self.assertEqual(r['kind'], 'column')
        self.assertEqual(r['product_id'], 'p_6a1aab3ce4b0694c5bd45cd')
        self.assertEqual(r['host'], 'https://appisa6gqvx4105.h5.xet.pomoho.com')

    def test_parse_big_column_url(self):
        u = ('https://appisa6gqvx4105.h5.xet.pomoho.com/p/course/big_column/'
             'p_6a1aab3ce4b0694c5bd45cd?l_program=xe_know_pc')
        r = xetapi.parse_course_url(u)
        self.assertEqual(r['kind'], 'column')
        self.assertEqual(r['product_id'], 'p_6a1aab3ce4b0694c5bd45cd')

    def test_parse_query_product_id(self):
        u = 'https://x.h5.xet.pomoho.com/p/course/index?product_id=p_abc123&app_id=appxyz'
        r = xetapi.parse_course_url(u)
        self.assertEqual(r['product_id'], 'p_abc123')
        self.assertEqual(r['app_id'], 'appxyz')

    def test_parse_empty(self):
        self.assertEqual(xetapi.parse_course_url('')['kind'], 'unknown')

    def test_form_body(self):
        body = xetapi.form_body({'column_id': 'p_1', 'page_index': 1})
        self.assertIn('bizData%5Bcolumn_id%5D=p_1', body)
        self.assertIn('bizData%5Bpage_index%5D=1', body)

    def test_column_items_with_fake_fetcher(self):
        calls = []

        def fetcher(path, params, method):
            calls.append((path, params, method))
            return {'code': 0, 'data': {'list': [
                {'resource_id': 'l_1', 'resource_title': '第一讲', 'resource_type': 4,
                 'start_at': '2026.03.01'},
            ]}}

        items = xetapi.column_items(fetcher, 'p_1')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['resource_title'], '第一讲')
        self.assertEqual(calls[0][0], config.EP_COLUMN_ITEMS)
        self.assertEqual(calls[0][1]['page_size'], 100)
        self.assertEqual(calls[0][2], 'POST')

    def test_lookback_picks_default_line(self):
        def fetcher(path, params, method):
            return {'data': [
                {'default': False, 'line_sharpness': [{'url': 'http://low/a.m3u8'}]},
                {'default': True, 'line_sharpness': [{'url': 'http://hi/a.m3u8'}]},
            ]}
        self.assertEqual(xetapi.lookback_url(fetcher, 'l_1'), 'http://hi/a.m3u8')

    def test_vod_url(self):
        def fetcher(path, params, method):
            return {'data': {'cloud_data': {'zj': {'encrypt_url': 'http://v/a.m3u8'}}}}

        self.assertEqual(xetapi.vod_url(fetcher, 'v_1'), 'http://v/a.m3u8')

    def test_courseware_list(self):
        def fetcher(path, params, method):
            return {'data': [{'title': '讲义', 'type': 'pdf', 'url': 'http://x/a.pdf'}]}
        files = xetapi.courseware_list(fetcher, 'i_1')
        self.assertEqual(files[0]['url'], 'http://x/a.pdf')


class TestXetApiV11(unittest.TestCase):
    """v1.1.0 新增/修正的接口封装，全部基于 2026-11-17 的真实实测。"""

    def test_goods_info_uses_resource_id(self):
        """实测：goods.info.get 的参数名是 resource_id。
        传 product_id 会得到 code:10「参数错误」。"""
        calls = []

        def fetcher(path, params, method):
            calls.append((path, params, method))
            return {'code': 0, 'data': {'resource_id': 'p_1', 'resource_type': 8}}

        j = xetapi.goods_info(fetcher, 'p_1')
        self.assertEqual(j['code'], 0)
        self.assertEqual(calls[0][0], config.EP_GOODS_INFO)
        self.assertEqual(calls[0][1]['resource_id'], 'p_1')
        self.assertNotIn('product_id', calls[0][1])

    def test_resource_available(self):
        def fetcher(path, params, method):
            return {'code': 0, 'data': {'other_list': [{'resource_id': 'p_2'}]}}

        j = xetapi.resource_available(fetcher, 'p_1')
        self.assertEqual(j['data']['other_list'][0]['resource_id'], 'p_2')

    def test_store_goods_category_id_is_string(self):
        """实测：category_goods_id 必须是字符串，用数字会报
        buz_data.category_goods_id.string。"""
        seen = {}

        def fetcher(path, params, method, json_body=False):
            seen['body'] = params
            seen['json'] = json_body
            return {'code': 0, 'data': {'list': [{'spu_id': 'p_1', 'title': '课'}]}}

        j = xetapi.store_goods(fetcher, 'cg_69df550d6b94d_bDhMNoi',
                               app_id='appisa6gqvx4105', micro_page_id='4760792')
        self.assertEqual(j['code'], 0)
        self.assertTrue(seen['json'], '店铺清单必须走 JSON body')
        cg = seen['body']['buz_data']['category_goods_id']
        self.assertIsInstance(cg, str)
        self.assertEqual(cg, 'cg_69df550d6b94d_bDhMNoi')
        self.assertEqual(seen['body']['buz_data']['micro_page_id'], '4760792')

    def test_extract_columns_finds_nested_columns(self):
        """通用结构提取：任意层级里的 p_ id 都要找出来。"""
        data = {
            'resource_id': 'p_big',
            'items': [
                {'product_id': 'p_c1', 'product_name': '专栏一', 'resource_count': 12},
                {'nested': {'column_id': 'p_c2', 'title': '专栏二'}},
                {'product_id': 'p_big'},          # 自身要排除
                {'product_id': 'p_c1'},            # 重复
            ],
        }
        cols = xetapi.extract_columns(data, exclude='p_big')
        ids = sorted(c['product_id'] for c in cols)
        self.assertEqual(ids, ['p_c1', 'p_c2'])
        names = {c['product_id']: c['name'] for c in cols}
        self.assertEqual(names['p_c1'], '专栏一')

    def test_extract_columns_excludes_big_package(self):
        """标记为「大课包/BSC」的节点不是子专栏。"""
        data = {'children': [
            {'product_id': 'p_big', 'resource_type_name': '大课包'},
            {'product_id': 'p_real', 'resource_type_name': '专栏'},
        ]}
        cols = xetapi.extract_columns(data)
        self.assertEqual([c['product_id'] for c in cols], ['p_real'])

    def test_column_items_paged(self):
        """满页就继续翻，不满页就停。"""
        pages = {
            1: [{'resource_id': 'l_%d' % i} for i in range(100)],
            2: [{'resource_id': 'm_%d' % i} for i in range(100)],
            3: [{'resource_id': 'z_1'}, {'resource_id': 'z_2'}],
        }

        def fetcher(path, params, method):
            idx = int(params['page_index'])
            return {'code': 0, 'data': {'list': pages.get(idx, [])}}

        out = xetapi.column_items_paged(fetcher, 'p_1', page_size=100)
        self.assertEqual(len(out), 202)
        self.assertEqual(out[0]['resource_id'], 'l_0')
        self.assertEqual(out[-1]['resource_id'], 'z_2')

    def test_column_items_paged_stops_on_error(self):
        def fetcher(path, params, method):
            return {'code': 10, 'msg': '参数错误', 'data': {}}

        self.assertEqual(xetapi.column_items_paged(fetcher, 'p_1'), [])

    def test_lookback_returns_none_when_auth_required(self):
        """实测：未登录访问 get_lookback_list 返回 code:11302（风控）。"""
        def fetcher(path, params, method):
            return {'code': config.LOOKBACK_AUTH_REQUIRED_CODE, 'data': []}

        self.assertIsNone(xetapi.lookback_url(fetcher, 'l_1'))

    def test_api_call_supports_both_fetcher_signatures(self):
        def f3(path, params, method):
            return {'code': 0, 'three': True}

        def f4(path, params, method, json_body=False):
            return {'code': 0, 'four': True, 'json': json_body}

        self.assertTrue(xetapi.api_call(f3, '/x')['three'])
        r = xetapi.api_call(f4, '/x', json_body=True)
        self.assertTrue(r['four'])
        self.assertTrue(r['json'])


# ---------------------------------------------------------------- 下载器


class TestDownloader(unittest.TestCase):
    def test_valid_ts_bytes(self):
        self.assertTrue(downloader.valid_ts_bytes((b'\x47' + b'\x00' * 187) * 3))
        self.assertFalse(downloader.valid_ts_bytes(b'\x00' * 188 * 3))
        self.assertFalse(downloader.valid_ts_bytes(b'\x47' * 100))

    def test_parse_m3u8_absolute(self):
        txt = ('#EXTM3U\n#EXT-X-VERSION:3\n'
               'https://cdn.example.com/a/1.ts?sign=x\n'
               'https://cdn.example.com/a/2.ts?sign=x\n')
        base, segs = downloader.parse_m3u8(txt)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0][0], '1.ts')
        self.assertTrue(segs[0][1].startswith('https://'))

    def test_parse_m3u8_relative(self):
        base, segs = downloader.parse_m3u8('#EXTM3U\n1.ts\n2.ts\n')
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[1][0], '2.ts')

    def test_probe_duration_on_garbage(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, 'x.mp4')
            with open(p, 'wb') as f:
                f.write(b'not a video')
            ok, dur, errs = downloader.probe_duration(p, retries=1)
            self.assertFalse(ok)
            self.assertEqual(dur, 0.0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 课件


class TestCourseware(unittest.TestCase):
    def test_ware_filename_prefers_download_name(self):
        item = {'url': 'http://cdn/x/abc?download_name=%E6%95%99%E4%B9%89.pdf',
                'title': '别的名字'}
        self.assertEqual(cw_mod.ware_filename(item), '教义.pdf')

    def test_ware_filename_fallback_title(self):
        item = {'url': 'http://cdn/x/abc', 'title': '第1讲.pdf'}
        self.assertEqual(cw_mod.ware_filename(item), '第1讲.pdf')

    def test_ware_filename_fallback_path(self):
        item = {'url': 'http://cdn/x/abc.zip', 'title': ''}
        self.assertEqual(cw_mod.ware_filename(item), 'abc.zip')

    def test_ware_filename_sanitizes(self):
        item = {'url': 'http://cdn/x/a', 'title': 'a/b:c*d?.pdf'}
        self.assertEqual(cw_mod.ware_filename(item), 'abcd.pdf')


# ---------------------------------------------------------------- 归档


class TestArchiver(unittest.TestCase):
    def test_cn2int(self):
        self.assertEqual(archiver.cn2int('三'), 3)
        self.assertEqual(archiver.cn2int('十'), 10)
        self.assertEqual(archiver.cn2int('十五'), 15)
        self.assertEqual(archiver.cn2int('二十'), 20)
        self.assertEqual(archiver.cn2int('42'), 42)
        self.assertIsNone(archiver.cn2int('abc'))

    def test_lesson_number(self):
        self.assertEqual(archiver.lesson_number('高等数学-15'), 15)
        self.assertEqual(archiver.lesson_number('Python高阶课程（5）'), 5)
        self.assertIsNone(archiver.lesson_number('开篇致辞'))

    def test_ware_to_lesson_by_date(self):
        lessons = [model.Lesson('l1', '第一讲', rtype=4, start_at='2026.03.01')]
        got = archiver.ware_to_lesson('20260301讲义.pdf', lessons)
        self.assertEqual(got.rid if got else None, 'l1')

    def test_ware_to_lesson_by_nth_lecture(self):
        lessons = [model.Lesson('l1', '第一讲', rtype=4), model.Lesson('l2', 'C语言-2', rtype=4)]
        got = archiver.ware_to_lesson('第2讲课件.zip', lessons)
        self.assertEqual(got.rid if got else None, 'l2')

    def test_ware_to_lesson_by_nn(self):
        lessons = [model.Lesson('l1', 'Python-1', rtype=4), model.Lesson('l2', 'Python-2', rtype=4)]
        got = archiver.ware_to_lesson('Python 2 课讲义.pdf', lessons)
        self.assertEqual(got.rid if got else None, 'l2')

    def test_ware_to_lesson_no_match(self):
        lessons = [model.Lesson('l1', '开篇致辞', rtype=4)]
        self.assertIsNone(archiver.ware_to_lesson('随手记.pdf', lessons))

    def test_link_or_copy_hardlink(self):
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, 'a.txt')
            dst = os.path.join(d, 'b.txt')
            with io.open(src, 'w', encoding='utf-8') as f:
                f.write('hello')
            r = archiver.link_or_copy(src, dst)
            self.assertIn(r, ('hardlink', 'copy'))
            self.assertTrue(os.path.exists(dst))
            with io.open(dst, 'r', encoding='utf-8') as f:
                self.assertEqual(f.read(), 'hello')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_write_index_and_readme(self):
        d = tempfile.mkdtemp()
        try:
            c = model.Course('测试课', product_id='p_1')
            c.lessons = [model.Lesson('l1', '第一讲', rtype=4, start_at='2026.03.01')]
            c.sort()
            root = os.path.join(d, '测试课')
            os.makedirs(os.path.join(root, '01_第一讲'), exist_ok=True)
            with open(os.path.join(root, '01_第一讲', '视频.mp4'), 'wb') as f:
                f.write(b'x')
            p = archiver.write_index(c, root, {'l1': ('done', '')})
            with io.open(p, 'r', encoding='utf-8') as f:
                text = f.read()
            self.assertIn('第一讲', text)
            self.assertIn('00_课程总览.md', os.listdir(root))
            self.assertIn('商用', text)          # 合规提示必须内嵌
            r = archiver.write_readme(c, root)
            with io.open(r, 'r', encoding='utf-8') as f:
                self.assertIn('商用', f.read())
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 本地服务


class TestServer(unittest.TestCase):
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
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read().decode('utf-8')
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw

    def test_health(self):
        code, j = self.req('/api/health')
        self.assertEqual(code, 200)
        self.assertTrue(j['ok'])
        self.assertIn('商用', j['legal'])
        self.assertEqual(j['version'], config.VERSION)

    def test_defaults(self):
        code, j = self.req('/api/defaults')
        self.assertEqual(code, 200)
        self.assertIn('out', j)
        self.assertGreater(j['threads'], 0)

    def test_index_page(self):
        code, body = self.req('/')
        self.assertEqual(code, 200)
        self.assertIn('小鹅通课程下载器', body)

    def test_bridge_js(self):
        code, body = self.req('/bridge.js')
        self.assertEqual(code, 200)
        self.assertIn('column.items.get', body)

    def test_post_course_and_read_back(self):
        course = {
            'title': '测试课', 'product_id': 'p_1',
            'lessons': [{'rid': 'l1', 'title': '第一讲', 'rtype': 4,
                         'start_at': '2026.03.01', 'playlist_url': ''}],
            'courseware': [],
        }
        code, j = self.req('/api/courses', 'POST', {'course': course})
        self.assertEqual(code, 200)
        self.assertTrue(j['ok'])
        code, j = self.req('/api/courses')
        self.assertEqual(j['title'], '测试课')
        self.assertEqual(len(j['lessons']), 1)

    def test_post_course_rejects_empty(self):
        try:
            self.req('/api/courses', 'POST', {'course': {'title': 'x', 'lessons': []}})
            self.fail('应当返回 400')
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_job_lifecycle(self):
        out = tempfile.mkdtemp()
        try:
            course = {'title': '空任务课', 'lessons': []}
            code, j = self.req('/api/jobs', 'POST', {'course': course, 'out_dir': out})
            self.assertEqual(code, 200)
            jid = j['id']
            for _ in range(60):
                code, snap = self.req('/api/jobs/' + jid)
                if snap['state'] in ('done', 'failed', 'cancelled'):
                    break
                time.sleep(0.2)
            self.assertEqual(snap['state'], 'done')
            code, j = self.req('/api/jobs')
            self.assertTrue(any(x['id'] == jid for x in j['jobs']))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_unknown_path_404(self):
        try:
            self.req('/api/nope')
            self.fail('应当 404')
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


# ---------------------------------------------------------------- 交付物一致性


class TestAssets(unittest.TestCase):
    def test_manifest(self):
        p = os.path.join(ROOT, 'extension', 'manifest.json')
        with io.open(p, 'r', encoding='utf-8') as f:
            m = json.load(f)
        self.assertEqual(m['manifest_version'], 3)
        self.assertTrue(m['name'])
        for cs in m.get('content_scripts', []):
            for js in cs['js']:
                self.assertTrue(os.path.exists(os.path.join(ROOT, 'extension', js)),
                                '缺少内容脚本 %s' % js)
        self.assertTrue(os.path.exists(os.path.join(ROOT, 'extension', m['action']['default_popup'])))
        self.assertTrue(os.path.exists(os.path.join(ROOT, 'extension', 'popup.js')))

    def test_js_contract_matches_config(self):
        """扩展与控制台桥共用 extension/content.js，接口必须和 config 一致。"""
        p = os.path.join(ROOT, 'extension', 'content.js')
        with io.open(p, 'r', encoding='utf-8') as f:
            text = f.read()
        for ep in (config.EP_COLUMN_ITEMS, config.EP_COURSEWARE,
                   config.EP_VOD_LINES, config.EP_LOOKBACK,
                   config.EP_GOODS_INFO, config.EP_RESOURCE_AVAILABLE,
                   config.EP_STORE_GOODS):
            self.assertIn(ep, text, 'content.js 缺少接口 %s' % ep)

    def test_bridge_is_served_from_content_js(self):
        """v1.1.0：不再有独立的 xetdl/web/bridge.js，
        本地服务直接下发 extension/content.js（保证两份实现不跑偏）。"""
        old = os.path.join(ROOT, 'xetdl', 'web', 'bridge.js')
        self.assertFalse(os.path.exists(old),
                         '旧的 xetdl/web/bridge.js 应删掉，避免两份实现不一致')
        with io.open(os.path.join(ROOT, 'extension', 'content.js'),
                     'r', encoding='utf-8') as f:
            text = f.read()
        self.assertIn('__XETDL_LOCAL__', text,
                      'content.js 里应保留 __XETDL_LOCAL__ 占位符')

    def test_goods_param_is_resource_id(self):
        """实测：goods.info.get 的参数名是 resource_id，不是 product_id。"""
        with io.open(os.path.join(ROOT, 'extension', 'content.js'),
                     'r', encoding='utf-8') as f:
            text = f.read()
        self.assertIn("'resource_id'", text,
                      'content.js 应先用 resource_id 试 goods.info.get')
        self.assertIn('参数错误', text,
                      'content.js 应记录 code:10「参数错误」这类失败原因')

    def test_bridge_port_matches_config(self):
        """服务端下发时会把占位符换成本地地址。"""
        with io.open(os.path.join(ROOT, 'xetdl', 'server.py'),
                     'r', encoding='utf-8') as f:
            srv = f.read()
        self.assertIn('__XETDL_LOCAL__', srv,
                      'server.py 应把占位符替换成真实地址')
        self.assertIn(str(config.DEFAULT_PORT), srv)

    def test_courses_api_carries_sync_seq(self):
        """/api/courses 必须带 seq，否则客户端会把上一轮残留的旧计划
        误当成这一轮刚采集到的（抓取器假成功）。"""
        with io.open(os.path.join(ROOT, 'xetdl', 'server.py'),
                     'r', encoding='utf-8') as f:
            srv = f.read()
        self.assertIn("course_seq", srv,
                      'server.py 应维护 course_seq 同步序号')
        self.assertIn("'seq'", srv,
                      '/api/courses 的响应里应带 seq 字段')
        # 序号必须在 POST 时自增
        self.assertIn("course_seq', 0) + 1", srv,
                      '每次同步都应让 course_seq 自增')

    def test_docs_exist(self):
        for f in ('README.md', 'LICENSE', '使用须知.md', 'start.py', '一键启动.bat'):
            self.assertTrue(os.path.exists(os.path.join(ROOT, f)), '缺少 %s' % f)

    def test_legal_notice_everywhere(self):
        self.assertIn('商用', config.LEGAL_NOTICE)
        self.assertIn('商用', config.LEGAL_NOTICE)
        with io.open(os.path.join(ROOT, 'README.md'), 'r', encoding='utf-8') as f:
            self.assertIn('商用', f.read())
        with io.open(os.path.join(ROOT, '使用须知.md'), 'r', encoding='utf-8') as f:
            self.assertIn('商用', f.read())

    def test_no_pip_dependency(self):
        """只允许标准库：源码里不能出现第三方 import。"""
        local = {'xetdl', 'config', 'model', 'xetapi', 'downloader', 'courseware',
                 'archiver', 'jobs', 'server', 'cli'}
        allowed = set(local) | set(sys.builtin_module_names)
        allowed |= set(getattr(sys, 'stdlib_module_names', ()))
        import ast
        for base, _dirs, files in os.walk(os.path.join(ROOT, 'xetdl')):
            for fn in files:
                if not fn.endswith('.py'):
                    continue
                p = os.path.join(base, fn)
                with io.open(p, 'r', encoding='utf-8') as f:
                    tree = ast.parse(f.read(), p)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for a in node.names:
                            self.assertIn(a.name.split('.')[0], allowed,
                                          '%s 引入了第三方库 %s' % (fn, a.name))
                    elif isinstance(node, ast.ImportFrom):
                        if node.level:      # 相对导入 = 本包内
                            continue
                        self.assertIn((node.module or '').split('.')[0], allowed,
                                      '%s 引入了第三方库 %s' % (fn, node.module))


class TestCli(unittest.TestCase):
    def test_run_plan_missing_file(self):
        from xetdl import cli
        self.assertEqual(cli.run_plan('nope-does-not-exist.json'), 2)

    def test_verify_empty_dir(self):
        from xetdl import cli
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(cli.verify_dir(d), 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
