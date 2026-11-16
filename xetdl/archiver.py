# -*- coding: utf-8 -*-
"""目录归档：把下载结果整理成 <课程名>/<序号_课时>/ 的结构，并生成总览索引。"""
import io
import os
import re

from . import config
from .model import safe_name

_CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7,
       '八': 8, '九': 9, '十': 10}


def cn2int(s):
    if s.isdigit():
        return int(s)
    if s in _CN:
        return _CN[s]
    if s.startswith('十') and len(s) == 2:
        return 10 + _CN.get(s[1], 0)
    if len(s) == 2 and s[1] == '十':
        return _CN[s[0]] * 10
    if len(s) == 3 and s[1] == '十':
        return _CN[s[0]] * 10 + _CN.get(s[2], 0)
    return None


def parse_start(s):
    m = re.match(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', (s or '').strip())
    if not m:
        return (9999, 99, 99)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def lesson_number(title):
    """从课时标题抽序号：'高等数学-15'->15，'Python高阶课程（5）'->5。"""
    m = re.search(r'[-—－]\s*(\d+)\s*$', title or '')
    if m:
        return int(m.group(1))
    m = re.search(r'[（(]\s*(\d+)\s*[)）]\s*$', title or '')
    if m:
        return int(m.group(1))
    return None


def ware_to_lesson(filename, lessons):
    """按文件名里的日期 / 第N讲 / 第N章 / N课 / NN，把课件对上具体课时。

    真实文件名长这样：'20260301讲义.pdf'、'第2讲课件.zip'、'Python 2 课讲义.pdf'、
    '高数第3章PPT.pptx'、'Python高阶课程（5）.pdf'。规则按可靠性从高到低依次尝试。
    """
    if not lessons:
        return None
    stem = filename.rsplit('.', 1)[0]

    def by_number(n):
        if not n:
            return None
        for l in lessons:
            if lesson_number(l.title) == n:
                return l
        return None

    # 1) 文件名开头就是日期 20260301
    m = re.match(r'(\d{4})(\d{2})(\d{2})', stem)
    if m:
        date = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        for l in lessons:
            if parse_start(l.start_at) == date:
                return l

    # 2) 文件名里含 MM-DD，且课时的开播日期与之相符
    m = re.search(r'(?<!\d)(\d{1,2})[.\-/月](\d{1,2})(?!\d)', stem)
    if m:
        for year in (2026, 2025):
            date = (year, int(m.group(1)), int(m.group(2)))
            for l in lessons:
                if parse_start(l.start_at) == date:
                    return l

    # 3) 第 N 讲
    m = re.search(r'第\s*([0-9一二三四五六七八九十]+)\s*讲', stem)
    if m:
        got = by_number(cn2int(m.group(1)))
        if got:
            return got

    # 4) 第 N 章
    m = re.search(r'第\s*([0-9一二三四五六七八九十]+)\s*章', stem)
    if m:
        got = by_number(cn2int(m.group(1)))
        if got:
            return got

    # 5) N 课 / N讲（'Python 2 课讲义'、'第2课'）
    m = re.search(r'(?<!\d)(\d{1,3})\s*[课讲]', stem)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 99:
            got = by_number(n)
            if got:
                return got

    # 6) 结尾的裸序号（'Python 2'、'02'）
    m = re.search(r'(?<!\d)(\d{1,3})\s*$', stem)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 99:
            got = by_number(n)
            if got:
                return got

    # 7) 括号里的序号（'Python高阶课程（5）'）
    m = re.search(r'[（(]\s*(\d{1,3})\s*[)）]', stem)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 99:
            got = by_number(n)
            if got:
                return got

    return None


def course_root(out_dir, course):
    return os.path.join(out_dir, safe_name(course.title))


def lesson_dir(root, lesson):
    return os.path.join(root, lesson.folder)


def link_or_copy(src, dst):
    """同盘优先硬链接（秒级、零额外空间），失败再拷贝。"""
    if os.path.exists(dst):
        try:
            if os.path.getsize(dst) == os.path.getsize(src):
                return 'skip'
        except OSError:
            pass
    try:
        os.remove(dst)
    except OSError:
        pass
    try:
        os.link(src, dst)
        return 'hardlink'
    except Exception:
        import shutil
        shutil.copy2(src, dst)
        return 'copy'


def write_index(course, root, status=None):
    """生成 00_课程总览.md。status: {rid: (state, note)}"""
    status = status or {}
    lines = ['# %s · 课程总览' % course.title, '']
    if course.source_url:
        lines += ['来源：<%s>' % course.source_url, '']
    if course.product_id:
        lines += ['产品 ID：`%s`' % course.product_id, '']
    lines += ['| # | 课时 | 类型 | 开播时间 | 视频 | 课件 | 状态 |',
              '|---|------|------|----------|------|------|------|']
    for l in course.lessons:
        ldir = lesson_dir(root, l)
        has_v = '✅' if os.path.exists(os.path.join(ldir, '视频.mp4')) else '—'
        wdir = os.path.join(ldir, '课件')
        nw = len(os.listdir(wdir)) if os.path.isdir(wdir) else 0
        st, note = status.get(l.rid, ('', ''))
        mark = {'done': '✅', 'failed': '⚠️', 'skipped': '—'}.get(st, '…')
        lines.append('| %02d | %s | %s | %s | %s | %s | %s %s |' % (
            l.index, l.title, l.kind, l.start_at or '—', has_v,
            ('%d 个' % nw) if nw else '—', mark, note or ''))
    lines += ['', '## 使用须知', '', config.LEGAL_NOTICE, '']
    with io.open(os.path.join(root, '00_课程总览.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))
    return os.path.join(root, '00_课程总览.md')


def write_readme(course, root):
    p = os.path.join(root, '使用须知.md')
    with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# %s · 使用须知\n\n%s\n\n'
                '本目录内容由 `xet-course-downloader` 下载整理，仅供个人离线学习。\n'
                '请勿传播、转载或用于商业用途。\n' % (course.title, config.LEGAL_NOTICE))
    return p
