# -*- coding: utf-8 -*-
"""一键启动器：启动本地下载控制台。

    python start.py                 # 启动控制台（默认 127.0.0.1:8777）
    python start.py --port 9000     # 换端口
    python start.py --no-browser    # 不自动开浏览器
    python start.py --run plan.json --out D:\\课程   # 无界面直接下载（服务器/批量场景）
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from xetdl import config  # noqa: E402

MIN_PY = (3, 6)


def _check_env():
    if sys.version_info < MIN_PY:
        print('需要 Python %d.%d 及以上，当前是 %d.%d' % (
            MIN_PY[0], MIN_PY[1], sys.version_info[0], sys.version_info[1]))
        return False
    if not shutil.which('ffmpeg'):
        print('[提醒] 没找到 ffmpeg。视频合并与校验需要它：')
        print('       Windows: https://www.gyan.dev/ffmpeg/builds/  下载后把 bin 目录加进 PATH')
        print('       macOS:   brew install ffmpeg')
        print('       Linux:   sudo apt install ffmpeg')
        print('       （只下载课件、不下视频的话可以先不管）')
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='xet-course-downloader',
        description='小鹅通课程下载器：本地控制台 + 浏览器取数桥')
    ap.add_argument('--port', type=int, default=config.DEFAULT_PORT,
                    help='本地服务端口（默认 %d）' % config.DEFAULT_PORT)
    ap.add_argument('--no-browser', action='store_true', help='启动后不自动打开浏览器')
    ap.add_argument('--run', metavar='PLAN', help='用计划 JSON 直接下载（无界面模式）')
    ap.add_argument('--out', metavar='DIR', help='配合 --run 使用的输出目录')
    ap.add_argument('--verify', metavar='DIR', help='用 ffprobe 校验目录里已归档的 mp4')
    ap.add_argument('--jobs', type=int, default=config.DEFAULT_JOBS, help='同时下载几讲')
    ap.add_argument('--threads', type=int, default=config.DEFAULT_THREADS, help='每讲并发分片数')
    args = ap.parse_args(argv)

    if not _check_env():
        return 2

    if args.verify:
        from xetdl import cli
        return cli.verify_dir(args.verify)

    if args.run:
        from xetdl import cli
        return cli.run_plan(args.run, args.out, args.jobs, args.threads)

    from xetdl import server
    print('')
    print('=' * 62)
    print('  小鹅通课程下载器  v%s' % config.VERSION)
    print('=' * 62)
    print('  %s' % config.LEGAL_NOTICE.replace('\n', '\n  '))
    print('-' * 62)
    server.serve(args.port, open_browser=not args.no_browser)
    return 0


if __name__ == '__main__':
    sys.exit(main())
