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
    """一个课时。rtype: 4=直播回放 3=录播 1=图文资料。"""

    def __init__(self, rid, title, rtype=TYPE_LIVE_DEFAULT, start_at='', product_id='',
                 playlist_url='', vod_url='', index=0):
        self.rid = rid
        self.title = title or rid
        self.rtype = rtype
        self.start_at = start_at
        self.product_id = product_id
        self.playlist_url = playlist_url      # 直播回放：明文 m3u8（分片自带签名）
        self.vod_url = vod_url                # 录播：AES-128 m3u8（签名约 30 分钟过期）
        self.index = index

    @property
    def kind(self):
        return {4: '直播回放', 3: '录播', 1: '图文资料', 2: '图文'}.get(self.rtype, '其他')

    @property
    def folder(self):
        return '%02d_%s' % (self.index, safe_name(self.title))

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
        }

    @classmethod
    def from_dict(cls, d):
        return cls(rid=d.get('rid') or '', title=d.get('title') or '',
                   rtype=int(d.get('rtype') or 4), start_at=d.get('start_at') or '',
                   product_id=d.get('product_id') or '',
                   playlist_url=d.get('playlist_url') or '',
                   vod_url=d.get('vod_url') or '',
                   index=int(d.get('index') or 0))


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


class Course(object):
    def __init__(self, title, product_id='', source_url='', host='', app_id=''):
        self.title = title or '未命名课程'
        self.product_id = product_id
        self.source_url = source_url
        self.host = host
        self.app_id = app_id
        self.lessons = []
        self.courseware = []

    @property
    def folder(self):
        return safe_name(self.title)

    def to_dict(self):
        return {
            'title': self.title, 'product_id': self.product_id,
            'source_url': self.source_url, 'host': self.host, 'app_id': self.app_id,
            'lessons': [l.to_dict() for l in self.lessons],
            'courseware': [c.to_dict() for c in self.courseware],
        }

    @classmethod
    def from_dict(cls, d):
        c = cls(d.get('title') or '', d.get('product_id') or '',
                d.get('source_url') or '', d.get('host') or '', d.get('app_id') or '')
        c.lessons = [Lesson.from_dict(x) for x in (d.get('lessons') or [])]
        c.courseware = [Courseware.from_dict(x) for x in (d.get('courseware') or [])]
        return c

    def sort(self):
        """视频按时开播时间升序，资料课时排最后；重新编号。"""
        def key(l):
            m = re.match(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', l.start_at or '')
            t = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (9999, 99, 99)
            return (t, l.index)
        vid = sorted([l for l in self.lessons if l.rtype == 4], key=key)
        vod = sorted([l for l in self.lessons if l.rtype == 3], key=key)
        txt = sorted([l for l in self.lessons if l.rtype not in (3, 4)], key=key)
        self.lessons = vid + vod + txt
        for i, l in enumerate(self.lessons):
            l.index = i + 1
        return self


def dump_plan(course, path):
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(course.to_dict(), f, ensure_ascii=False, indent=1)


def load_plan(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        return Course.from_dict(json.load(f))
