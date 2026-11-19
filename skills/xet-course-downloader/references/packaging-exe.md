# 打包成免安装 Windows exe（内置 ffmpeg）

实测环境：Windows / Python 3.13 / PyInstaller 6.21.0。
产物：**`XetDownloader.exe` 133.1 MB，打包 34–42 秒，冷启动 2.1 秒**。

## 为什么选这个方案

| 方案 | 结论 |
|---|---|
| PyInstaller onefile + 内置 ffmpeg | **采用**。133 MB，启动 2.1 秒，用户零安装 |
| onedir | 支持（`--onedir`），但没必要：onefile 启动已足够快 |
| pywebview 做原生窗口 | 放弃。拖入 pythonnet / .NET CLR / bottle / proxy_tools 依赖链 |
| 重写 tkinter 界面 | 放弃。要复刻整套前端逻辑，不划算 |

窗口外壳用 `msedge.exe --app=<url>`：无地址栏、独立窗口，Win10/11 自带，
不需要额外 UI 框架。

## 双轨并存：同一份源码，两条部署路径

不改代码分支、不克隆代码，靠 `sys.frozen` 分流：

```python
FLAVOR = 'exe' if getattr(sys, 'frozen', False) else 'py'
FROZEN = (FLAVOR == 'exe')

_DEFAULT_PORT_BY_FLAVOR = {'py': 8777, 'exe': 8787}
_SETTINGS_DIRNAME = '.xetdl' if FLAVOR == 'py' else '.xetdl-exe'
```

于是两版可以**同时运行**：端口不同、配置目录不同、缓存不同，互不覆盖。
Python 版行为逐字节不变（所有 exe 专属分支都以 `FROZEN` 为开关）。

资源根也要分流，否则删掉 `dist/` 后 Python 版找不到自己的 web 目录：

```python
def _res_dir():
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        return meipass                      # PyInstaller 解包目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
```

工具查找顺序：**内置 bin/ → PATH → 裸名字**。exe 版命中内置，Python 版自然落到 PATH。

```python
def _pick_tool(name):
    exe = name + ('.exe' if sys.platform.startswith('win') else '')
    bundled = os.path.join(BIN_DIR, exe)
    if os.path.isfile(bundled):
        return bundled
    return shutil.which(name) or name
```

## spec 要点（三个必踩的坑）

```python
a = Analysis(
    ['../start.py'],                    # ← 相对 spec 所在目录，不是 cwd
    datas=[
        ('../xetdl/web', 'xetdl/web'),  # 静态资源
        ('../extension', 'extension'),  # 扩展（导出用）
        ('bin/ffmpeg.exe',  'bin'),     # ← 不带 ../，bin 在 packaging/ 内
        ('bin/ffprobe.exe', 'bin'),
    ],
    ...
)
```

1. **相对路径基准是 spec 所在目录**。写成 `start.py` 会报
   `script '...\packaging\start.py' not found`。
2. **ffmpeg 放 `datas` 不放 `binaries`** —— `binaries` 会被扫依赖并改路径，
   破坏静态 ffmpeg。
3. **`upx=False`** —— 压 168 MB 的 ffmpeg 又慢又极易触发杀软误报。

## 打包脚本要做的事

`build.py` 不只是调 PyInstaller，还要：

1. 找 ffmpeg/ffprobe（PATH → winget 包目录），校验体积 > 100 MB
   （静态完整版一般 160 MB+，太小说明拿到的是残缺版）
2. 拷到 `packaging/bin/`
3. 调 PyInstaller
4. 打印体积与耗时
5. **自检**：起来 → 查 `/api/health` → 断言 `flavor=exe`、`bundled=True`、
   ffmpeg 路径在 `_MEI` 内 → 杀掉

```powershell
python packaging/build.py            # onefile
python packaging/build.py --onedir   # 目录版
```

自检输出长这样（全部 6 项必须 OK）：

```
[OK] flavor == exe
[OK] bundled == True
[OK] ffmpeg 可用
[OK] ffprobe 可用
[OK] 端口 8799
[OK] ffmpeg 路径在 _MEI 解包目录内
```

## 分发：走 GitHub Release，不进 Git

Git 对单文件 **> 100 MiB 硬拒绝**（GH001），**> 50 MiB 警告**。
133 MB 的 exe 不可能进仓库。Release 附件单文件上限 2 GiB、无总量上限、流量免费走 CDN。

```powershell
gh release create v1.3.0 'dist\XetDownloader.exe' `
  --title 'v1.3.0 内置版 exe（含 ffmpeg，双击即用）' `
  --notes-file notes.md
```

`.gitignore` 要加：`dist/`、`packaging/_work/`、`packaging/bin/`、`*.exe`

## 自检怎么做才算数

**「ffmpeg 真内置」必须用干净 PATH 证明**：

```powershell
$inner = 'set PATH=C:\Windows\system32&& "' + $exe + '" --no-browser --port 8801'
Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $inner) -PassThru -WindowStyle Hidden
```

然后确认两件事同时成立：

1. 同环境下 `where ffmpeg` 返回**非 0**（系统里确实没有）
2. exe 的 `/api/health` 仍报 `ffmpeg=true`，且 `ffmpeg_path` 在 `_MEI...\bin\` 下

只做第 2 条不算证明 —— 可能来自 PATH。

## 首次运行的用户体验

未签名 exe 会触发 SmartScreen：「Windows 已保护你的电脑」→
「更多信息」→「**仍要运行**」。这是必经步骤（代码签名证书要花钱），
要在 README 里写清楚，否则用户会以为是病毒。

保留控制台窗口（`console=True`）的理由：报错可见、ffmpeg 子进程继承控制台
不闪黑框、滚动的日志本身就是实时监控。
