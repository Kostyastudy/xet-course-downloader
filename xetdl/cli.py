# -*- coding: utf-8 -*-
"""无界面命令行模式：拿一份「下载计划 JSON」直接跑。

给这两种场景用：
  * 服务器 / 无人值守批量下载
  * 不想开浏览器控制台时

计划 JSON 的结构与本地服务 /api/courses 收到的完全一致，可以直接在扩展弹窗里
「同步到下载器」后从磁盘上找这份文件（下载目录下的 `下载计划.json`）。
"""
import io
import json
import os
import sys
import time

from . import config, model
from .jobs import JobManager


def run_plan(plan_path, out_dir=None, jobs=None, threads=None):
    """按计划文件下载。返回进程退出码。"""
    if not os.path.exists(plan_path):
        print('找不到计划文件：%s' % plan_path)
        return 2
    with io.open(plan_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    course_dict = data.get('course') or data
    if not course_dict.get('lessons'):
        print('计划里没有课时，无法下载。')
        return 2

    out_dir = out_dir or config.DEFAULT_OUT
    os.makedirs(out_dir, exist_ok=True)

    mgr = JobManager()
    job = mgr.submit(course_dict, out_dir, threads, jobs)

    print('')
    print('课程：%s' % job.course.title)
    print('课时：%d 个' % len(job.course.lessons))
    print('输出：%s' % out_dir)
    print('-' * 62)

    last = 0
    while True:
        snap = job.snapshot()
        if snap['done'] + snap['failed'] != last:
            last = snap['done'] + snap['failed']
            sys.stdout.write('\r  进度 %d/%d  完成 %d  失败 %d   ' % (
                snap['done'] + snap['failed'], snap['total'], snap['done'], snap['failed']))
            sys.stdout.flush()
        if snap['state'] in ('done', 'failed', 'cancelled'):
            break
        time.sleep(1)
    print('')
    print('-' * 62)
    print('任务结束：%s' % job.state)
    if job.error:
        print('错误：%s' % job.error)
    root = os.path.join(out_dir, model.safe_name(job.course.title))
    print('课程目录：%s' % root)
    print('')
    print(config.LEGAL_NOTICE)
    return 0 if job.state == 'done' else 1


def verify_dir(path):
    """用 ffprobe 校验目录下所有 mp4。返回退出码。"""
    import subprocess
    if not os.path.isdir(path):
        print('目录不存在：%s' % path)
        return 2
    bad = 0
    total = 0
    total_sec = 0.0
    for base, _dirs, files in os.walk(path):
        for fn in sorted(files):
            if not fn.lower().endswith('.mp4'):
                continue
            total += 1
            p = os.path.join(base, fn)
            r = subprocess.run([config.FFPROBE, '-v', 'error', '-show_entries',
                                'format=duration', '-of', 'json', p],
                               capture_output=True, text=True,
                               encoding='utf-8', errors='replace',
                               **config.popen_kwargs())
            try:
                dur = float((json.loads(r.stdout or '{}').get('format') or {}).get('duration') or 0)
            except Exception:
                dur = 0.0
            if dur <= 0:
                bad += 1
                print('  [BAD] %s' % p)
            else:
                total_sec += dur
    print('共 %d 个 mp4，异常 %d 个，总时长 %.1f 分钟' % (total, bad, total_sec / 60.0))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'verify':
        sys.exit(verify_dir(sys.argv[2]))
    if len(sys.argv) >= 2:
        out = None
        if '--out' in sys.argv:
            out = sys.argv[sys.argv.index('--out') + 1]
        sys.exit(run_plan(sys.argv[1], out))
    print('用法：python -m xetdl.cli <plan.json> [--out DIR]  |  python -m xetdl.cli verify <dir>')
    sys.exit(2)
