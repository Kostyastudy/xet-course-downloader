# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 生成「内置 ffmpeg 的单文件 exe」。

运行方式（不要直接跑这个文件）：
    python packaging/build.py

路径约定：PyInstaller 解析 spec 里的相对路径时，基准是 **spec 文件所在目录**
（也就是 packaging/），不是 cwd。所以源码与资源都用 `../` 前缀。
datas 的第二个元素是「解包后的目标路径」，相对 _MEIPASS 根，不带 `..`。

ffmpeg/ffprobe 放 datas 而不是 binaries：
  binaries 会被 PyInstaller 扫依赖、改路径；我们要的是「原样两个文件」
  落到 _MEIPASS/bin/，交给 config._pick_tool() 按需选用。
"""

a = Analysis(
    ['../start.py'],
    pathex=['..'],
    binaries=[],
    datas=[
        ('../xetdl/web', 'xetdl/web'),        # 静态界面（_static 从这里读）
        ('../extension', 'extension'),        # 扩展源码（/bridge.js 与导出用）
        ('bin/ffmpeg.exe', 'bin'),
        ('bin/ffprobe.exe', 'bin'),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # 砍体积：这些模块整个产品都没用到
    excludes=[
        'tkinter', 'unittest', 'pydoc', 'test', 'distutils', 'lib2to3',
        'pdb', 'doctest', 'cProfile', 'pstats', 'pydoc_data',
        'sqlite3', 'bz2', 'lzma', 'curses', 'readline',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='XetDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # upx 压 168MB 的 ffmpeg 既慢又极易触发杀软误报 —— 明确关掉
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
