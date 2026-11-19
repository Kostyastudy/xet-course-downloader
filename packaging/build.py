# -*- coding: utf-8 -*-
"""把产品打包成内置 ffmpeg 的 Windows 单文件 exe。

    python packaging/build.py            # 出 onefile（默认）
    python packaging/build.py --onedir   # 出目录版（误报率低、启动快）

做的事：
  1. 找 ffmpeg/ffprobe（PATH → winget 目录），拷到 packaging/bin/
  2. 调 PyInstaller 按 packaging/xetdl.spec 打包
  3. 打印产物体积与耗时
  4. 自检：起 exe → 查 /api/health → 断言 flavor=exe 且 ffmpeg 内置 → 杀掉

只用标准库，不需要 pip 装东西。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # 仓库根
BIN = os.path.join(HERE, 'bin')
WORK = os.path.join(HERE, '_work')
DIST = os.path.join(ROOT, 'dist')

MIN_TOOL_BYTES = 100 * 1024 * 1024           # 静态 ffmpeg 一般 160MB+，低于这个可疑

WINGET_HINTS = [
    r'C:\Users\%s\AppData\Local\Microsoft\WinGet\Packages'
    % os.environ.get('USERNAME', ''),
]


def find_tool(name):
    """PATH → winget 包目录里的 bin/ → 放弃。返回绝对路径或 None。"""
    p = shutil.which(name)
    if p:
        return os.path.abspath(p)
    exe = name + '.exe'
    for hint in WINGET_HINTS:
        if not os.path.isdir(hint):
            continue
        for dirpath, _dirnames, filenames in os.walk(hint):
            if exe in filenames:
                return os.path.abspath(os.path.join(dirpath, exe))
    return None


def stage_bins():
    """把 ffmpeg/ffprobe 拷到 packaging/bin/，返回两个路径。"""
    os.makedirs(BIN, exist_ok=True)
    out = {}
    for name in ('ffmpeg', 'ffprobe'):
        src = find_tool(name)
        if not src:
            print('[ERROR] 找不到 %s。' % name)
            print('        请先安装（winget install Gyan.FFmpeg 或 yt-dlp.FFmpeg），')
            print('        或手动把 %s.exe 放进 %s' % (name, BIN))
            return None
        size = os.path.getsize(src)
        if size < MIN_TOOL_BYTES:
            print('[ERROR] %s 只有 %.1f MB，不是静态完整版？' % (src, size / 1048576))
            return None
        dst = os.path.join(BIN, name + '.exe')
        if not (os.path.isfile(dst) and os.path.getsize(dst) == size):
            print('  拷贝 %s (%.1f MB) -> %s' % (src, size / 1048576, dst))
            shutil.copy2(src, dst)
        else:
            print('  已就位 %s (%.1f MB)' % (dst, size / 1048576))
        out[name] = dst
    return out


def wait_health(port, timeout=40.0):
    """等 exe 起来并回 /api/health。"""
    url = 'http://127.0.0.1:%d/api/health' % port
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            last = e
            time.sleep(0.5)
    raise RuntimeError('健康检查超时（%s）：%s' % (url, last))


def selftest(exe_path, port=8799):
    """起 exe → 断言 flavor/bundled/ffmpeg → 杀掉。返回 True/False。"""
    print('\n--- 自检：启动 exe 并检查内置 ffmpeg ---')
    proc = subprocess.Popen(
        [exe_path, '--port', str(port), '--no-browser'],
        cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    ok = False
    try:
        h = wait_health(port)
        print('  /api/health = %s' % json.dumps(h, ensure_ascii=False))
        checks = [
            ('flavor == exe', h.get('flavor') == 'exe'),
            ('bundled == True', h.get('bundled') is True),
            ('ffmpeg 可用', h.get('ffmpeg') is True),
            ('ffprobe 可用', h.get('ffprobe') is True),
            ('端口 %d' % port, h.get('port') == port),
            ('ffmpeg 路径在 _MEI 解包目录内',
             '_MEI' in (h.get('ffmpeg_path') or '') or 'bin' in (h.get('ffmpeg_path') or '')),
        ]
        for label, passed in checks:
            print('  [%s] %s' % ('OK' if passed else 'FAIL', label))
        ok = all(p for _, p in checks)
    except Exception as e:
        print('  [FAIL] %s' % e)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    print('--- 自检 %s ---' % ('通过' if ok else '未通过'))
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description='打包 XetDownloader.exe')
    ap.add_argument('--onedir', action='store_true',
                    help='出目录版（不再单文件）。误报率低、冷启动快')
    ap.add_argument('--no-selftest', action='store_true', help='跳过打包后自检')
    args = ap.parse_args(argv)

    pyinstaller = shutil.which('pyinstaller')
    if not pyinstaller:
        # 常见位置兜底（Scripts 目录有时不在 PATH 里）
        cand = os.path.join(os.path.dirname(sys.executable), 'Scripts', 'pyinstaller.exe')
        if os.path.isfile(cand):
            pyinstaller = cand
    if not pyinstaller:
        print('[ERROR] 找不到 pyinstaller。先装：pip install pyinstaller')
        return 2
    print('pyinstaller: %s' % pyinstaller)

    bins = stage_bins()
    if not bins:
        return 2

    spec = os.path.join(HERE, 'xetdl.spec')
    if not os.path.isfile(spec):
        print('[ERROR] 找不到 spec：%s' % spec)
        return 2

    print('\n--- 打包中（第一次要 2~5 分钟，335MB ffmpeg 要压缩） ---')
    t0 = time.time()
    cmd = [pyinstaller, spec, '--noconfirm', '--clean',
           '--workpath', WORK, '--distpath', DIST]
    if args.onedir:
        cmd += ['--onedir']
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print('[ERROR] pyinstaller 失败，退出码 %d' % r.returncode)
        return r.returncode
    print('打包耗时 %.1f 秒' % (time.time() - t0))

    if args.onedir:
        # onedir 的产物是目录，打成 zip 便于分发
        name = 'XetDownloader'
        d = os.path.join(DIST, name)
        if not os.path.isdir(d):
            print('[ERROR] 没找到目录产物 %s' % d)
            return 2
        zip_base = os.path.join(DIST, 'XetDownloader-onedir')
        archive = shutil.make_archive(zip_base, 'zip', root_dir=DIST, base_dir=name)
        print('产物：%s (%.1f MB)' % (archive, os.path.getsize(archive) / 1048576))
        exe_path = os.path.join(d, name + '.exe')
    else:
        exe_path = os.path.join(DIST, 'XetDownloader.exe')
        if not os.path.isfile(exe_path):
            print('[ERROR] 没找到产物 %s' % exe_path)
            return 2
        print('产物：%s (%.1f MB)' % (exe_path, os.path.getsize(exe_path) / 1048576))

    if args.no_selftest:
        print('（已跳过自检）')
        return 0
    return 0 if selftest(exe_path) else 1


if __name__ == '__main__':
    sys.exit(main())
