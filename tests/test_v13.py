# -*- coding: utf-8 -*-
"""v1.3.0 双轨部署测试：风味分流、内置工具探测、端口顺延、课程缓存、扩展导出。

这些测试的核心目的：证明「exe 版是新增的、Python 版行为没被改坏」，
以及两版的配置/缓存不会互相覆盖。
"""
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from xetdl import config  # noqa: E402
from xetdl import server as server_mod  # noqa: E402


def _reload_config():
    """按当前 sys.frozen / sys._MEIPASS 状态重新导入 config。"""
    return importlib.reload(config)


class TestFlavor(unittest.TestCase):
    """FLAVOR / 端口 / 配置目录的分流 —— 双轨隔离的回归测试。"""

    def setUp(self):
        self._frozen = getattr(sys, 'frozen', None)
        self._meipass = getattr(sys, '_MEIPASS', None)

    def tearDown(self):
        for name, val in (('frozen', self._frozen), ('_MEIPASS', self._meipass)):
            if val is None:
                if hasattr(sys, name):
                    delattr(sys, name)
            else:
                setattr(sys, name, val)
        _reload_config()

    def test_py_flavor_defaults(self):
        """源码运行：端口 8777、配置目录 .xetdl —— 与改造前逐字节一致。"""
        if hasattr(sys, 'frozen'):
            delattr(sys, 'frozen')
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        c = _reload_config()
        self.assertEqual(c.FLAVOR, 'py')
        self.assertFalse(c.FROZEN)
        self.assertEqual(c._DEFAULT_PORT_BY_FLAVOR['py'], 8777)
        # 用户可能已经写过 settings.json 改过端口，所以只断言「默认值表」
        self.assertEqual(os.path.basename(c.SETTINGS_DIR), '.xetdl')
        self.assertEqual(os.path.basename(os.path.dirname(c.SETTINGS_PATH)), '.xetdl')

    def test_exe_flavor_defaults(self):
        """伪造 frozen：端口 8787、配置目录 .xetdl-exe —— 两版互不覆盖。"""
        sys.frozen = True
        c = _reload_config()
        self.assertEqual(c.FLAVOR, 'exe')
        self.assertTrue(c.FROZEN)
        self.assertEqual(c._DEFAULT_PORT_BY_FLAVOR['exe'], 8787)
        self.assertEqual(os.path.basename(c.SETTINGS_DIR), '.xetdl-exe')
        self.assertEqual(os.path.basename(os.path.dirname(c.SETTINGS_PATH)), '.xetdl-exe')

    def test_two_flavors_never_share_state_paths(self):
        """两版的 settings / cache 路径必须完全不同（会互相覆盖的根因）。"""
        if hasattr(sys, 'frozen'):
            delattr(sys, 'frozen')
        py = _reload_config()
        py_settings, py_cache = py.SETTINGS_PATH, py.CACHE_PATH

        sys.frozen = True
        ex = _reload_config()
        ex_settings, ex_cache = ex.SETTINGS_PATH, ex.CACHE_PATH

        self.assertNotEqual(py_settings, ex_settings)
        self.assertNotEqual(py_cache, ex_cache)
        self.assertNotEqual(os.path.dirname(py_settings), os.path.dirname(ex_settings))


class TestResDir(unittest.TestCase):
    """_res_dir()：源码模式指仓库根，打包模式指 _MEIPASS。"""

    def setUp(self):
        self._meipass = getattr(sys, '_MEIPASS', None)
        self._frozen = getattr(sys, 'frozen', None)

    def tearDown(self):
        for name, val in (('_MEIPASS', self._meipass), ('frozen', self._frozen)):
            if val is None:
                if hasattr(sys, name):
                    delattr(sys, name)
            else:
                setattr(sys, name, val)
        _reload_config()

    def test_source_mode_points_to_repo_root(self):
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        c = _reload_config()
        self.assertEqual(os.path.abspath(c.HERE), os.path.abspath(HERE))
        self.assertTrue(os.path.isdir(c.WEB_DIR), 'web 目录必须存在')
        self.assertTrue(os.path.isdir(c.EXT_DIR), 'extension 目录必须存在')

    def test_frozen_mode_uses_meipass(self):
        tmp = tempfile.mkdtemp()
        try:
            sys._MEIPASS = tmp
            sys.frozen = True
            c = _reload_config()
            self.assertEqual(os.path.abspath(c.HERE), os.path.abspath(tmp))
            self.assertEqual(os.path.abspath(c.WEB_DIR),
                             os.path.abspath(os.path.join(tmp, 'xetdl', 'web')))
            self.assertEqual(os.path.abspath(c.BIN_DIR),
                             os.path.abspath(os.path.join(tmp, 'bin')))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestPickTool(unittest.TestCase):
    """内置 bin/ 优先于 PATH —— 这是「真内置」的关键。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_bin = config.BIN_DIR

    def tearDown(self):
        config.BIN_DIR = self._old_bin
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bundled_wins_over_path(self):
        config.BIN_DIR = self.tmp
        name = 'ffmpeg.exe' if sys.platform.startswith('win') else 'ffmpeg'
        bundled = os.path.join(self.tmp, name)
        with open(bundled, 'wb') as f:
            f.write(b'x')
        picked = config._pick_tool('ffmpeg')
        self.assertEqual(os.path.abspath(picked), os.path.abspath(bundled))

    def test_falls_back_to_path_or_bare_name(self):
        config.BIN_DIR = self.tmp          # 空的，没有内置
        picked = config._pick_tool('ffmpeg')
        # 要么是 PATH 里的绝对路径，要么是裸名字（交给系统再找一次）
        self.assertTrue(picked)
        if os.path.isabs(picked):
            self.assertTrue(os.path.isfile(picked))


class TestToolOk(unittest.TestCase):
    """tool_ok：绝对是 / 裸名字 / 不存在 三种输入都要判得准。"""

    def test_existing_absolute_path(self):
        f = os.path.join(tempfile.mkdtemp(), 'a.txt')
        try:
            with open(f, 'w') as fh:
                fh.write('x')
            self.assertTrue(config.tool_ok(f))
        finally:
            shutil.rmtree(os.path.dirname(f), ignore_errors=True)

    def test_missing_absolute_path(self):
        self.assertFalse(config.tool_ok('/no/such/tool/xyzzy-12345'))

    def test_bare_unknown_name(self):
        self.assertFalse(config.tool_ok('definitely-not-a-real-tool-12345'))

    def test_empty(self):
        self.assertFalse(config.tool_ok(''))
        self.assertFalse(config.tool_ok(None))


class TestPopenKwargs(unittest.TestCase):
    """非 frozen 必须返回 {}，否则 Python 版行为会被改动。"""

    def setUp(self):
        self._frozen = getattr(sys, 'frozen', None)

    def tearDown(self):
        if self._frozen is None:
            if hasattr(sys, 'frozen'):
                delattr(sys, 'frozen')
        else:
            sys.frozen = self._frozen
        _reload_config()

    def test_not_frozen_returns_empty(self):
        if hasattr(sys, 'frozen'):
            delattr(sys, 'frozen')
        c = _reload_config()
        self.assertEqual(c.popen_kwargs(), {})

    def test_frozen_returns_creationflags_on_windows(self):
        sys.frozen = True
        c = _reload_config()
        kw = c.popen_kwargs()
        if sys.platform.startswith('win'):
            self.assertEqual(kw.get('creationflags'), 0x08000000)
            self.assertIn('startupinfo', kw)
        else:
            self.assertEqual(kw, {})


class TestBindPort(unittest.TestCase):
    """端口被占要顺延，不能崩 —— 双轨并存时两版可能撞端口。

    注意：Windows 上 SO_REUSEADDR 允许两个 socket 绑同一端口，所以不能靠
    「真的占住一个端口」来测。改用替身：让前 N 次构造抛 OSError，看是否顺延。
    """

    def setUp(self):
        self._real = server_mod.ThreadedServer
        self.calls = []

    def tearDown(self):
        server_mod.ThreadedServer = self._real

    def test_returns_real_port_not_requested_zero(self):
        """port=0 时必须回读系统挑的真实端口，否则客户端连不上。"""
        httpd, got = server_mod._bind(0, tries=1)
        try:
            self.assertNotEqual(got, 0)
            self.assertEqual(got, httpd.server_address[1])
            self.assertGreater(got, 0)
        finally:
            httpd.server_close()

    def test_scans_past_busy_ports(self):
        real = self._real

        def fake(addr, handler):
            self.calls.append(addr[1])
            if len(self.calls) <= 3:
                raise OSError('busy (simulated)')
            return real(addr, handler)

        server_mod.ThreadedServer = fake
        httpd, got = server_mod._bind(8800, tries=8)
        try:
            self.assertEqual(len(self.calls), 4, '应在第 4 次绑定成功')
            self.assertEqual(self.calls[:4], [8800, 8801, 8802, 8803])
            self.assertEqual(got, 8803)
        finally:
            httpd.server_close()

    def test_raises_when_all_ports_busy(self):
        def always(addr, handler):
            raise OSError('busy (simulated)')

        server_mod.ThreadedServer = always
        with self.assertRaises(OSError):
            server_mod._bind(8810, tries=3)

    def test_windows_disables_reuse_address(self):
        """Windows 上必须关掉 SO_REUSEADDR。

        否则第二个实例也能绑上同一端口 —— 端口顺延永远不触发，
        双轨并存时两个服务抢同一端口，用户会看到「配置莫名串了」。
        这是实测出来的真实故障（d4_port.py），必须锁住。
        """
        if not sys.platform.startswith('win'):
            self.skipTest('仅 Windows 需要')
        self.assertFalse(server_mod.ThreadedServer.allow_reuse_address)

    def test_second_instance_on_same_port_is_refused(self):
        """同一端口起第二个服务必须抛 OSError，从而触发顺延。"""
        if not sys.platform.startswith('win'):
            self.skipTest('仅 Windows 需要')
        httpd, port = server_mod._bind(0, tries=1)
        try:
            with self.assertRaises(OSError):
                server_mod.ThreadedServer((server_mod.config.HOST_BIND, port),
                                          server_mod.Handler)
        finally:
            httpd.server_close()


class TestCourseCache(unittest.TestCase):
    """课程目录落盘缓存：重启后不必重跑一次全量采集。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_dir = config.SETTINGS_DIR
        self._old_path = config.SETTINGS_PATH
        self._old_cache = config.CACHE_PATH
        config.SETTINGS_DIR = self.tmp
        config.SETTINGS_PATH = os.path.join(self.tmp, 'settings.json')
        config.CACHE_PATH = os.path.join(self.tmp, 'course.json')
        self._old_state = dict(server_mod._STATE)

    def tearDown(self):
        config.SETTINGS_DIR = self._old_dir
        config.SETTINGS_PATH = self._old_path
        config.CACHE_PATH = self._old_cache
        server_mod._STATE.clear()
        server_mod._STATE.update(self._old_state)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_then_load_roundtrip(self):
        server_mod._STATE['course'] = {
            'title': '缓存课', 'lessons': [{'rid': 'r1', 'title': '第一课'}]
        }
        server_mod._save_cache()
        self.assertTrue(os.path.isfile(config.CACHE_PATH))

        server_mod._STATE['course'] = None
        server_mod._load_cache()
        self.assertIsNotNone(server_mod._STATE['course'])
        self.assertEqual(len(server_mod._STATE['course']['lessons']), 1)
        self.assertEqual(server_mod._STATE['course_seq'], 1)

    def test_broken_cache_does_not_raise(self):
        with io.open(config.CACHE_PATH, 'w', encoding='utf-8') as f:
            f.write('{ this is not json')
        server_mod._STATE['course'] = None
        server_mod._load_cache()          # 不抛异常即通过
        self.assertIsNone(server_mod._STATE['course'])

    def test_missing_cache_is_fine(self):
        server_mod._STATE['course'] = None
        server_mod._load_cache()
        self.assertIsNone(server_mod._STATE['course'])


class TestServerV13(unittest.TestCase):
    """新接口的 HTTP 层验证。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.port = server_mod._bind(0, tries=1)
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()
    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        url = 'http://%s:%d%s' % (config.HOST_BIND, self.port, path)
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, json.loads(r.read().decode('utf-8'))

    def test_health_has_flavor_fields(self):
        code, h = self._get('/api/health')
        self.assertEqual(code, 200)
        for k in ('flavor', 'bundled', 'port', 'ffmpeg', 'ffprobe',
                  'ffmpeg_path', 'ffprobe_path'):
            self.assertIn(k, h, '健康检查缺少字段 %s' % k)
        self.assertIn(h['flavor'], ('py', 'exe'))
        self.assertEqual(h['port'], self.port)
        self.assertIsInstance(h['bundled'], bool)

    def test_export_extension_makes_zip(self):
        code, r = self._get('/api/export-extension')
        self.assertEqual(code, 200)
        self.assertTrue(r.get('ok'), r)
        zp = r['path']
        try:
            self.assertTrue(os.path.isfile(zp))
            with zipfile.ZipFile(zp) as z:
                names = z.namelist()
            joined = '\n'.join(names)
            self.assertIn('manifest.json', joined)
            self.assertIn('content.js', joined)
        finally:
            try:
                os.remove(zp)
            except OSError:
                pass

    def test_bridge_snippet_uses_real_port(self):
        url = 'http://%s:%d/bridge.js' % (config.HOST_BIND, self.port)
        with urllib.request.urlopen(url, timeout=20) as r:
            self.assertEqual(r.status, 200)
            code = r.read().decode('utf-8')
        self.assertIn('http://127.0.0.1:%d' % self.port, code)
        self.assertNotIn('__XETDL_LOCAL__', code, '占位符必须已被替换')

    def test_bridge_progress_endpoint(self):
        code, r = self._get('/api/bridge/progress')
        self.assertEqual(code, 200)
        for k in ('done', 'total', 'step', 'at'):
            self.assertIn(k, r)


class TestCancelAndProgress(unittest.TestCase):
    """取消要能真打断；进度不能全程停在 0。

    这两条都是实测抓出来的：12 片的课时进度条一直显示 0/12（因为上报
    条件是 done % 100 == 0），点取消也要等整节课下完才生效。
    """

    def test_cancel_flag_is_passed_down(self):
        import inspect
        from xetdl import downloader as dl
        self.assertIn('cancelled', inspect.signature(dl.download_replay).parameters)
        self.assertIn('cancelled', inspect.signature(dl.download_vod).parameters)
        self.assertIn('cancelled', inspect.signature(dl._pull_segments).parameters)

    def test_jobs_wires_cancelled_into_downloader(self):
        import inspect
        from xetdl import jobs
        src = inspect.getsource(jobs._download_videos)
        self.assertIn('cancelled=', src, 'jobs 必须把 job.cancelled 传下去')

    def test_pull_segments_stops_when_cancelled(self):
        """已经取消时，一个分片都不该去拉。"""
        from xetdl import downloader as dl
        calls = []
        orig = dl.http_get

        def fake(url, tries=6, timeout=120):
            calls.append(url)
            return b'\x47' + b'\x00' * 2000

        dl.http_get = fake
        try:
            tmp = tempfile.mkdtemp()
            try:
                tasks = [(i, 'seg_%03d.ts' % i, 'http://x/%d.ts' % i)
                         for i in range(10)]
                done, nbytes, failed = dl._pull_segments(
                    tmp, tasks, 4, None, False, lambda: True)
                self.assertEqual(calls, [], '取消后不该再发请求')
                self.assertEqual(done, 0)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            dl.http_get = orig

    def test_pull_segments_reports_progress_for_small_lessons(self):
        """小课时（<100 片）也要在上报进度，否则前端一直显示 0。

        这里卡住 HTTP 让它失败重试，从而在少量分片时产生多次上报机会；
        只要 progress 被调用过且 done 有意义即可。
        """
        from xetdl import downloader as dl
        seen = []

        def fake(url, tries=6, timeout=120):
            raise RuntimeError('boom')

        orig = dl.http_get
        orig_sleep = dl.time.sleep
        dl.http_get = fake
        dl.time.sleep = lambda s: None          # 别真等退避
        tmp = tempfile.mkdtemp()
        try:
            tasks = [(0, 'a.ts', 'http://x/a.ts')]
            dl._pull_segments(tmp, tasks, 1, lambda st: seen.append(dict(st)),
                              False, None)
            self.assertTrue(seen, 'progress 必须被调用')
            self.assertIn('done', seen[-1])
        finally:
            dl.http_get = orig
            dl.time.sleep = orig_sleep
            shutil.rmtree(tmp, ignore_errors=True)


class TestKeyUri(unittest.TestCase):
    """KEY 行的相对 URI 必须补成绝对地址。

    ffmpeg 读的是 .segs 里的本地 m3u8，相对的 k.bin 会被解析成
    「.segs/k.bin」而取不到密钥，整段合并失败（实测踩过）。
    """

    def test_relative_key_becomes_absolute(self):
        from xetdl import downloader as dl
        out = dl._absolutize_key(
            '#EXT-X-KEY:METHOD=AES-128,URI="k.bin",IV=0x00',
            'http://127.0.0.1:8901/pl.m3u8')
        self.assertIn('URI="http://127.0.0.1:8901/k.bin"', out)

    def test_absolute_key_untouched(self):
        from xetdl import downloader as dl
        key = '#EXT-X-KEY:METHOD=AES-128,URI="https://cdn/k.bin"'
        self.assertEqual(dl._absolutize_key(key, 'http://h/pl.m3u8'), key)

    def test_no_key_line(self):
        from xetdl import downloader as dl
        self.assertEqual(dl._absolutize_key('', 'http://h/pl.m3u8'), '')


if __name__ == '__main__':
    unittest.main()
