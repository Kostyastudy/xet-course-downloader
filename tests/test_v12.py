# -*- coding: utf-8 -*-
"""v1.2.0 新增能力的回归测试：体积估算、专栏分组、配置持久化。"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from xetdl import archiver, config, model, sizing  # noqa: E402


class TestSizing(unittest.TestCase):
    def test_parse_manifest_counts_segments(self):
        txt = ('#EXTM3U\n#EXT-X-TARGETDURATION:10\n'
               '#EXTINF:9.0,\nseg0.ts\n#EXTINF:9.0,\nseg1.ts\n'
               '#EXTINF:3.5,\nseg2.ts\n#EXT-X-ENDLIST\n')
        segs, dur, durs, enc = sizing.parse_manifest(txt)
        self.assertEqual(segs, 3)
        self.assertAlmostEqual(dur, 21.5, places=3)
        self.assertEqual(len(durs), 3)
        self.assertFalse(enc)

    def test_parse_manifest_detects_aes(self):
        txt = ('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n'
               '#EXTINF:10,\na.ts\n')
        segs, dur, durs, enc = sizing.parse_manifest(txt)
        self.assertTrue(enc)
        self.assertEqual(segs, 1)

    def test_parse_manifest_empty(self):
        self.assertEqual(sizing.parse_manifest(''), (0, 0.0, [], False))

    def test_absolutize_relative_path(self):
        base = 'https://cdn.example.com/a/b/playlist.f3.m3u8?sign=x'
        got = sizing._absolutize('seg0.ts', base)
        self.assertEqual(got, 'https://cdn.example.com/a/b/seg0.ts')

    def test_absolutize_keeps_absolute(self):
        u = 'https://other.example.com/x/y.ts'
        self.assertEqual(sizing._absolutize(u, 'https://a/b/c.m3u8'), u)

    def test_human_size(self):
        self.assertEqual(sizing.human_size(0), '-')
        self.assertEqual(sizing.human_size(512), '512.0 B')
        self.assertEqual(sizing.human_size(1536), '1.5 KB')
        self.assertEqual(sizing.human_size(5 * 1024 ** 3), '5.0 GB')

    def test_human_time(self):
        self.assertEqual(sizing.human_time(0), '-')
        self.assertEqual(sizing.human_time(65), '1:05')
        self.assertEqual(sizing.human_time(3725), '1:02:05')

    def test_estimate_one_without_url(self):
        r = sizing.estimate_one('')
        self.assertFalse(r['ok'])
        self.assertEqual(r['bytes'], 0)
        self.assertIn('播放地址', r['note'])

    def test_estimate_batch_honours_cache(self):
        items = [{'rid': 'r1', 'title': 't1', 'playlist_url': ''},
                 {'rid': 'r2', 'title': 't2', 'playlist_url': ''}]
        got = []

        def prog(done, total, r):
            got.append((done, r.get('rid')))

        res = sizing.estimate_batch(items, progress=prog, workers=2,
                                    cache={'r2': 123456})
        by_rid = {r['rid']: r for r in res}
        self.assertEqual(by_rid['r2']['bytes'], 123456)
        self.assertEqual(by_rid['r2']['method'], '缓存')
        self.assertTrue(by_rid['r1']['bytes'] == 0)   # 没地址，估不出来
        self.assertEqual(len(got), 2)

    def test_estimate_batch_should_stop(self):
        items = [{'rid': 'r%d' % i, 'title': 't', 'playlist_url': ''}
                 for i in range(10)]
        res = sizing.estimate_batch(items, workers=1, cache={},
                                    should_stop=lambda: True)
        self.assertEqual(res, [])

    def test_total_bytes(self):
        self.assertEqual(sizing.total_bytes([]), 0)
        self.assertEqual(sizing.total_bytes([{'bytes': 10}, {'bytes': 5}]), 15)
        self.assertEqual(sizing.total_bytes([None, {'bytes': 7}]), 7)


class TestColumnGrouping(unittest.TestCase):
    def _course(self, n_cols=2):
        d = {'title': '大课包', 'product_id': 'p_big',
             'columns': [{'product_id': 'p_a', 'name': '甲专栏'},
                         {'product_id': 'p_b', 'name': '乙专栏'}][:n_cols],
             'lessons': []}
        for i in range(3):
            d['lessons'].append({'rid': 'a%d' % i, 'title': '甲-%d' % (i + 1),
                                 'rtype': 4, 'start_at': '2025.0%d.01 09:00' % (i + 1),
                                 'product_id': 'p_a'})
        if n_cols > 1:
            for i in range(2):
                d['lessons'].append({'rid': 'b%d' % i, 'title': '乙-%d' % (i + 1),
                                     'rtype': 4, 'start_at': '2025.0%d.02 09:00' % (i + 1),
                                     'product_id': 'p_b'})
        return model.Course.from_dict(d)

    def test_multi_column_gets_groups(self):
        c = self._course(2)
        c.sort()
        self.assertEqual(len(c.columns), 2)
        a = [l for l in c.lessons if l.group == 'p_a']
        b = [l for l in c.lessons if l.group == 'p_b']
        self.assertEqual(len(a), 3)
        self.assertEqual(len(b), 2)
        # 组内独立编号，从 1 开始
        self.assertEqual([l.group_index for l in a], [1, 2, 3])
        self.assertEqual([l.group_index for l in b], [1, 2])
        # 组内按日期升序
        self.assertEqual([l.title for l in a], ['甲-1', '甲-2', '甲-3'])

    def test_single_column_stays_flat(self):
        c = self._course(1)
        c.sort()
        self.assertTrue(all(not l.group for l in c.lessons))
        self.assertEqual([l.index for l in c.lessons], [1, 2, 3])

    def test_no_columns_stays_flat(self):
        d = {'title': 'x', 'lessons': [
            {'rid': 'r%d' % i, 'title': 't%d' % i, 'rtype': 4,
             'start_at': '2025.01.0%d 09:00' % (i + 1)} for i in range(3)]}
        c = model.Course.from_dict(d)
        c.sort()
        self.assertTrue(all(not l.group for l in c.lessons))
        self.assertEqual([l.index for l in c.lessons], [1, 2, 3])

    def test_group_dir_only_when_grouped(self):
        c = self._course(2)
        c.sort()
        root = os.path.join('X:', 'root')
        a = [l for l in c.lessons if l.group == 'p_a'][0]
        p = archiver.lesson_dir(root, a, c)
        self.assertIn('01_甲专栏', p)

    def test_normalize_columns_from_plain_ids(self):
        """老版本扩展只发 ['p_xxx']，也要能归一成带名字的结构。"""
        cols = model.normalize_columns(['p_a', 'p_b'])
        self.assertEqual([c['product_id'] for c in cols], ['p_a', 'p_b'])
        self.assertEqual(cols[0]['name'], 'p_a')

    def test_normalize_columns_falls_back_to_lessons(self):
        lessons = [model.Lesson('r1', 't', product_id='p_z')]
        cols = model.normalize_columns(None, lessons)
        self.assertEqual(cols, [{'product_id': 'p_z', 'name': 'p_z'}])

    def test_courseware_and_lesson_roundtrip_keeps_group(self):
        c = self._course(2)
        c.sort()
        d = c.to_dict()
        c2 = model.Course.from_dict(d)
        self.assertEqual(len(c2.columns), 2)
        self.assertTrue(any(l.group == 'p_b' for l in c2.lessons))
        self.assertTrue(all(l.group_index >= 1 for l in c2.lessons))

    def test_course_groups_helper(self):
        c = self._course(2)
        c.sort()
        gs = archiver.course_groups(c)
        self.assertEqual(len(gs), 2)
        self.assertEqual(gs[0]['name'], '甲专栏')
        self.assertEqual(len(gs[1]['lessons']), 2)

    def test_write_index_multi_column_has_sections(self):
        c = self._course(2)
        c.sort()
        tmp = tempfile.mkdtemp()
        try:
            p = archiver.write_index(c, tmp)
            with io.open(p, encoding='utf-8') as f:
                txt = f.read()
            self.assertIn('甲专栏', txt)
            self.assertIn('乙专栏', txt)
            self.assertIn('共 2 个专栏', txt)
            self.assertIn(config.LEGAL_NOTICE.split('\n')[0], txt)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_find_existing_mp4_checks_flat_layout(self):
        """分组改造前下载的视频在平铺目录里，不能被当成本地没有而重下。"""
        c = self._course(2)
        c.sort()
        tmp = tempfile.mkdtemp()
        try:
            lesson = [l for l in c.lessons if l.group == 'p_a'][0]
            flat = archiver.lesson_dir(tmp, lesson)      # 不传 course = 旧结构
            os.makedirs(flat, exist_ok=True)
            mp4 = os.path.join(flat, '视频.mp4')
            with open(mp4, 'wb') as f:
                f.write(b'\0' * (2 * 1024 * 1024))
            path, sz = archiver.find_existing_mp4(tmp, c, lesson)
            self.assertEqual(path, mp4)
            self.assertGreater(sz, 1024 * 1024)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestSettings(unittest.TestCase):
    def setUp(self):
        self._old = (config.SETTINGS_DIR, config.SETTINGS_PATH)
        self.tmp = tempfile.mkdtemp()
        config.SETTINGS_DIR = os.path.join(self.tmp, '.xetdl')
        config.SETTINGS_PATH = os.path.join(config.SETTINGS_DIR, 'settings.json')

    def tearDown(self):
        config.SETTINGS_DIR, config.SETTINGS_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_without_file(self):
        s = config.load_settings()
        self.assertEqual(s['threads'], 10)
        self.assertEqual(s['jobs'], 2)

    def test_save_then_load(self):
        config.save_settings({'threads': 16, 'jobs': 4, 'default_out': 'D:/课'})
        s = config.load_settings()
        self.assertEqual(s['threads'], 16)
        self.assertEqual(s['jobs'], 4)
        self.assertEqual(s['default_out'], 'D:/课')

    def test_partial_patch_keeps_others(self):
        config.save_settings({'threads': 16})
        config.save_settings({'jobs': 5})
        s = config.load_settings()
        self.assertEqual(s['threads'], 16)
        self.assertEqual(s['jobs'], 5)

    def test_bad_values_are_ignored(self):
        config.save_settings({'threads': 'abc', 'jobs': 3})
        s = config.load_settings()
        self.assertEqual(s['threads'], 10)     # 非法值被丢弃，保留默认
        self.assertEqual(s['jobs'], 3)

    def test_corrupt_file_falls_back(self):
        os.makedirs(config.SETTINGS_DIR, exist_ok=True)
        with io.open(config.SETTINGS_PATH, 'w', encoding='utf-8') as f:
            f.write('{ not json ]')
        s = config.load_settings()
        self.assertEqual(s['threads'], 10)

    def test_unknown_keys_dropped(self):
        config.save_settings({'evil_key': 'x'})
        s = config.load_settings()
        self.assertNotIn('evil_key', s)


if __name__ == '__main__':
    unittest.main()
