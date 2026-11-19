# 打包说明（把源码变成 exe）

给两条部署路径里的**路径 E**（内置 ffmpeg 的单文件 exe）用。
路径 P（`python start.py`）不需要看这一页。

## 一条命令

```powershell
cd <仓库根>
python packaging/build.py
```

产物：`dist/XetDownloader.exe`，约 133 MB（实测 133.1 MB，耗时 34–42 秒），
内置 ffmpeg + ffprobe。冷启动实测 2.1–2.5 秒，onedir 不是必需。

要目录版（启动更快、杀软误报率低）：

```powershell
python packaging/build.py --onedir
```

产物：`dist/XetDownloader-onedir.zip`。

## 前置条件

| 需要 | 说明 |
|---|---|
| Python 3.8+ | 打包机器用，最终用户不需要 |
| PyInstaller | `pip install pyinstaller`（本机已有 6.21.0） |
| ffmpeg/ffprobe | `winget install Gyan.FFmpeg`，或任何静态编译版。build.py 会自动从 PATH 或 winget 目录找 |

找不到 ffmpeg 时 build.py 会直接报错并列出候选路径，不会打个半成品出来。

## build.py 干了什么

1. 找 ffmpeg/ffprobe → 拷到 `packaging/bin/`（临时区，已 gitignore）
2. `pyinstaller packaging/xetdl.spec --workpath packaging/_work --distpath dist`
3. 打印产物体积与耗时
4. **自检**：启动 exe → 请求 `/api/health` → 断言 `flavor=="exe"`、`bundled==true`、
   `ffmpeg==true`、端口正确 → 杀掉进程。自检不通过会返回非 0。

## 关键设计（改 spec 前先读）

- **ffmpeg 放 `datas` 不放 `binaries`**：`binaries` 会被 PyInstaller 扫依赖并改路径；
  我们要的是原样两个文件落到 `_MEIPASS/bin/`，由 `config._pick_tool()` 按需选。
- **`upx=False`**：UPX 压 168 MB 的 ffmpeg 既慢又极易触发杀软误报。
- **`console=True`**：故意保留黑窗。报错用户能直接看见；ffmpeg 子进程继承控制台
  天然不闪黑框；黑窗滚下载日志本身就是「实时监控」。
- **spec 里全是相对路径**，build.py 会 `cwd=ROOT` 再调用。移动文件时一起改。

## 运行时行为（打出来之后）

| 维度 | Python 版 | exe 版 |
|---|---|---|
| 默认端口 | 8777 | 8787 |
| 配置目录 | `~/.xetdl/` | `~/.xetdl-exe/` |
| ffmpeg | 系统 PATH | 内置 `_MEIPASS/bin/` |
| 窗口 | 默认浏览器标签页 | Edge `--app=` 独立窗口 |

两版共用同一份源码，靠 `sys.frozen` 分流，可以同时开着互不干扰。
端口被占会自动顺延到 8788/8789…，并把真实端口打印出来。

## 发布

exe **不能进 Git**（GitHub 单文件 >100 MiB 硬拒，GH001）。走 Release 附件：

```powershell
gh release create v1.3.0 'dist\XetDownloader.exe' `
  --repo Kostyastudy/xet-course-downloader `
  --title 'v1.3.0 内置版 exe（含 ffmpeg）' `
  --notes '双击即用，无需安装 Python 与 ffmpeg。Python 版仍然保留，两种装法任选。'
```

Release 附件单文件上限 2 GiB、无总量上限、下载流量免费走 CDN。

## 已知坑

- **SmartScreen**：未签名 exe 首次运行弹「Windows 已保护你的电脑」→「更多信息」→「仍要运行」。
  要消除只能买代码签名证书。
- **杀软误报**：PyInstaller 大 exe 有概率被 Defender/360 报可疑。用 `--onedir` 版误报率低很多。
- **冷启动**：onefile 每次启动要把 335 MB 解到 `%TEMP%`。实测若 >15 秒，改推 onedir。
