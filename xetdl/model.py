# -*- coding: utf-8 -*-
"""数据模型：课程 / 课时 / 课件文件 / 下载计划。

浏览器扩展与本地服务之间用 JSON 交换数据，结构定义在这里，两端共用。
"""
import io
import json
import os
import re


def safe_name(name, fallback='未命名'):
    """去掉 Windows 文件名非法字符。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '', name or '').strip().strip('.')
    return name or fallback


def slug(name):
    return re.sub(r'[\\/:*?"<>|\s]+', '', name or '')


TYPE_LIVE_DEFAULT = 4


class Lesson(object):
    """一个课时。rtype: 4=直播回放 3=录播 1=图文资料。

    group / group_name / group_index 只有在大课包（一门课里有多个子专栏）时才有值，
    用来把 187 节课分到各自的专栏目录下——否则全平铺在一层根本没法看。
    """

    def __init__(self, rid, title, rtype=TYPE_LIVE_DEFAULT, start_at='', product_id='',
                 playlist_url='', vod_url='', index=0,
                 group='', group_name='', group_index=0):
        self.rid = rid
        self.title = title or rid
        self.rtype = rtype
        self.start_at = start_at
        self.product_id = product_id
        self.playlist_url = playlist_url      # 直播回放：明文 m3u8（分片自带签名）
        self.vod_url = vod_url                # 录播：AES-128 m3u8（签名约 30 分钟过期）
        self.index = index
        self.group = group
        self.group_name = group_name
        self.group_index = group_index

    @property
    def kind(self):
        return {4: '直播回放', 3: '录播', 1: '图文资料', 2: '图文'}.get(self.rtype, '其他')

    @property
    def folder(self):
        n = self.group_index or self.index
        return '%02d_%s' % (n, safe_name(self.title))

    @property
    def is_video(self):
        return self.rtype in (3, 4)

    @property
    def is_text(self):
        return self.rtype not in (3, 4)

    def to_dict(self):
        return {
            'rid': self.rid, 'title': self.title, 'rtype': self.rtype,
            'start_at': self.start_at, 'product_id': self.product_id,
            'playlist_url': self.playlist_url, 'vod_url': self.vod_url,
            'index': self.index,
            'group': self.group, 'group_name': self.group_name,
            'group_index': self.group_index,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(rid=d.get('rid') or '', title=d.get('title') or '',
                   rtype=int(d.get('rtype') or 4), start_at=d.get('start_at') or '',
                   product_id=d.get('product_id') or '',
                   playlist_url=d.get('playlist_url') or '',
                   vod_url=d.get('vod_url') or '',
                   index=int(d.get('index') or 0),
                   group=d.get('group') or '',
                   group_name=d.get('group_name') or '',
                   group_index=int(d.get('group_index') or 0))


class CoursewareFile(object):
    def __init__(self, title, url, mime=''):
        self.title = title or ''
        self.url = url or ''
        self.mime = mime

    def to_dict(self):
        return {'title': self.title, 'url': self.url, 'mime': self.mime}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get('title') or '', d.get('url') or '', d.get('mime') or '')


class Courseware(object):
    """一个资料课时及其附件。"""

    def __init__(self, rid, title, files=None):
        self.rid = rid
        self.title = title or rid
        self.files = files or []

    def to_dict(self):
        return {'rid': self.rid, 'title': self.title,
                'files': [f.to_dict() for f in self.files]}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get('rid') or '', d.get('title') or '',
                   [CoursewareFile.from_dict(x) for x in (d.get('files') or [])])


def normalize_columns(raw, lessons=None):
    """把 columns 归一到 [{product_id, name}]。

    兼容三种历史写法：
      * ["p_xxx", "p_yyy"]                       —— v1.1.x 扩展只发 id
      * [{"product_id": "p_x", "name": "高数"}]  —— 新写法
      * 缺失                                        —— 由课时里的 product_id 兜底
    没有名字的专栏用 id 当名字（界面按 id 分组仍然可用）。
    """
    out = []
    seen = {}
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                pid = it.get('product_id') or it.get('rid') or it.get('id') or ''
                name = it.get('name') or it.get('title') or ''
            else:
                pid, name = (it if isinstance(it, str) else ''), ''
            if not pid or pid in seen:
                continue
            seen[pid] = 1
            out.append({'product_id': pid, 'name': name or pid})
    if not out and lessons:
        for l in lessons:
            pid = getattr(l, 'product_id', '') or ''
            if pid and pid not in seen:
                seen[pid] = 1
                out.append({'product_id': pid, 'name': pid})
    return out


class Course(object):
    def __init__(self, title, product_id='', source_url='', host='', app_id=''):
        self.title = title or '未命名课程'
        self.product_id = product_id
        self.source_url = source_url
        self.host = host
        self.app_id = app_id
        self.lessons = []
        self.courseware = []
        self.columns = []      # [{product_id, name}]

    @property
    def folder(self):
        return safe_name(self.title)

    def column_names(self):
        """{product_id: 专栏名}，给归档与界面分组用。"""
        out = {}
        for c in self.columns or []:
            pid = c.get('product_id') or ''
            if pid and c.get('name'):
                out[pid] = c['name']
        return out

    def to_dict(self):
        return {
            'title': self.title, 'product_id': self.product_id,
            'source_url': self.source_url, 'host': self.host, 'app_id': self.app_id,
            'columns': [dict(c) for c in (self.columns or [])],
            'lessons': [l.to_dict() for l in self.lessons],
            'courseware': [c.to_dict() for c in self.courseware],
        }

    @classmethod
    def from_dict(cls, d):
        c = cls(d.get('title') or '', d.get('product_id') or '',
                d.get('source_url') or '', d.get('host') or '', d.get('app_id') or '')
        c.lessons = [Lesson.from_dict(x) for x in (d.get('lessons') or [])]
        c.courseware = [Courseware.from_dict(x) for x in (d.get('courseware') or [])]
        c.columns = normalize_columns(d.get('columns'), c.lessons)
        return c

    def sort(self, group=True):
        """视频按时开播时间升序，资料课时排最后；重新编号。

        group=True 且存在多个专栏时（大课包），把课时按专栏分组：
        每个专栏内单独从 01 编号，归档成 <课程>/<NN_专栏名>/<NN_课时>/。
        只有单个专栏时保持扁平结构，和旧版本一致。
        """
        names = self.column_names()

        def date_key(l):
            m = re.match(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', l.start_at or '')
            return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (9999, 99, 99)

        def kind_rank(l):
            return {4: 0, 3: 1}.get(l.rtype, 2)

        def key(l):
            return (date_key(l), l.index)

        groups = []
        if group:
            for l in self.lessons:
                pid = l.product_id or ''
                if pid and pid not in groups:
                    groups.append(pid)

        if len(groups) < 2:
            # —— 单专栏 / 没有专栏信息：保持老结构，全平铺 ——
            self._clear_groups()
            vid = sorted([l for l in self.lessons if l.rtype == 4], key=key)
            vod = sorted([l for l in self.lessons if l.rtype == 3], key=key)
            txt = sorted([l for l in self.lessons if l.rtype not in (3, 4)], key=key)
            self.lessons = vid + vod + txt
            for i, l in enumerate(self.lessons):
                l.index = i + 1
            return self

        # —— 多专栏：按专栏分组，组内按日期+类型重排 ——
        self.columns = [{'product_id': p, 'name': names.get(p) or p} for p in groups]
        order = {p: i + 1 for i, p in enumerate(groups)}
        self.lessons.sort(key=lambda l: (
            order.get(l.product_id, len(groups) + 1), kind_rank(l)) + date_key(l))
        counters = {}
        for i, l in enumerate(self.lessons):
            pid = l.product_id or groups[0]
            l.index = i + 1
            l.group = pid
            l.group_name = names.get(pid) or pid
            counters[pid] = counters.get(pid, 0) + 1
            l.group_index = counters[pid]
        return self

    def _clear_groups(self):
        for l in self.lessons:
            l.group = ''
            l.group_name = ''
            l.group_index = 0


def dump_plan(course, path):
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(course.to_dict(), f, ensure_ascii=False, indent=1)


def load_plan(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        return Course.from_dict(json.load(f))
