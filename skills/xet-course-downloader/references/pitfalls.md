# 踩坑清单（全部为实测，附数字）

按「会不会让你卡住半天」排序。每一条都是真踩过的，不是设想。

## 一、会让你彻底走错方向的

### 1. 用 Python 直连平台接口，永远拿不到播放地址

非浏览器客户端得到 `HTTP 200 + 空响应体`；`get_lookback_list` 直接 `code:11302`。
无论怎么伪装 UA 都没用 —— 它看的是登录态与风控指纹。

**唯一出路**：在已登录页面里同源 `fetch`，`credentials: 'include'`。
不要在这上面反复尝试（我试过 UA 伪装、Cookie 注入，都不行）。

### 2. 大课包用 `goods.info.get` 找子专栏，找不到

`goods.info.get` 的返回里**根本没有子专栏列表**。一个含 9 个子专栏的大课包，
用它会绕很久还是拿不全。正解是 `topic.items.get`。

### 3. `goods.info.get` 的参数名是 `resource_id`

传 `product_id` 得到 `code:10 参数错误`。而其他接口确实用 `column_id`/`product_id`，
所以很容易写错还以为是别的问题。

### 4. ffmpeg 直拉远端加密 HLS 只有 0.8 倍速

等于实时播放，45 分钟的课要 45 分钟。必须改成「并发拉密文 + 本地解密拼接」。
这条不改，整批 76 GB 要下几天。

## 二、会让你下出坏文件的

### 5. AES-128 分片体积判据错误

密文是 **16 的整数倍**，不是 188，也不以 `0x47` 开头。
用 TS 判据去校验密文 → 全部判为损坏 → 无限重下，永远下不完。

### 6. 加密 m3u8 里所有分片同名

平台用同一文件名 + `Range` 参数区分分片。直接 `basename()` 会把所有分片写进
同一个文件，最后得到一个体积不对、ffprobe 报错的 mp4。必须补 `_NNNNN` 序号。

### 7. 大 m3u8 一次性 read 抛 IncompleteRead

实测在 **320 KB** 的 m3u8 上必现。改 65536 分块循环读。

### 8. KEY 行相对 URI 在本地 m3u8 里取不到密钥

见 `download-engine.md`。症状是「分片全下完了，但合并 3 轮都失败」，
很容易误判成网络问题。

### 9. `-protocol_whitelist` 缺 crypto

拉录播直接失败。完整值：`file,http,https,tcp,tls,crypto,httpproxy`。

### 10. 误判 `.tmp.mp4` 为失败

合并期间 `视频.mp4.tmp.mp4` 是 **48 字节或 0 字节**属正常（ffmpeg 先写临时文件）。
不要手动干预，也不要因此重试。

## 三、只在 Windows 上出现的

### 11. `SO_REUSEADDR` 让两个实例绑同一端口

Linux 语义是「跳过 TIME_WAIT」，Windows 语义是「**允许抢占别人已绑的端口**」。
开着它时：

- 端口顺延逻辑永远不触发
- 双轨并存时两个服务抢同一端口，请求随机落到其中一边
- 用户看到的是「配置莫名串了」

```python
allow_reuse_address = not sys.platform.startswith('win')
```

修完后实测：第二个实例得到 `WinError 10048`，顺延从 8850 正确落到 8851。

### 12. `_bind(0)` 必须回读真实端口

`port=0` 是「让系统挑」，返回请求的 `0` 会让客户端连 `:0` 直接
`WinError 10049`。必须 `return httpd.server_address[1]`。

### 13. PowerShell 5.1 读 `.ps1` 按 ANSI 解码

非 ASCII 字符（中文注释、全角标点）会乱码甚至语法报错。`.ps1` 必须纯 ASCII。

### 14. 用 `Invoke-WebRequest` 在非交互 PowerShell 里报错

报「Windows PowerShell 处于非交互模式」。改用 `System.Net.Http.HttpClient`。

### 15. `-ArgumentList` 拼接含 `=` 的命令会报参数错误

`Start-Process -ArgumentList '/c','set PATH=...' + $exe` 报
「找不到接受实际参数+的位置形式参数」。必须先拼成**单个字符串**再传数组：
`@('/c', $inner)`。

## 四、PyInstaller 特有的

### 16. spec 相对路径基准是 spec 所在目录，不是 cwd

首次打包报 `script '...\packaging\start.py' not found`。
要用 `../start.py`、`../xetdl/web`、`../extension`。
但 `bin/ffmpeg.exe` **不带** `../`（bin 在 packaging/ 内）。

### 17. ffmpeg 要放 `datas` 不放 `binaries`

`binaries` 会被 PyInstaller 扫依赖并改路径，破坏静态 ffmpeg。
`datas` 是原样落到 `_MEIPASS/bin/`，才是要的。

### 18. `upx=True` 会毁掉打包

压 168 MB 的 ffmpeg 既慢（分钟级）又极易触发杀软误报。关掉。

### 19. onefile 冷启动没想象中慢

担心 133 MB 单文件启动慢，实测**冷启动 2.1–2.5 秒**（远低于 15 秒门槛）。
不必为了启动速度改 onedir。

### 20. 窗口化后 ffmpeg 子进程会闪黑框

打包成 windowed 时给 `subprocess` 传 `creationflags=0x08000000` +
`STARTUPINFO(dwFlags |= STARTF_USESHOWWINDOW)`。
但对非 frozen 要返回 `{}`，否则破坏 Python 版行为。

## 五、验证方法上的陷阱

### 21. 用「真占端口」测顺延，在 Windows 上测不出来

因为 `SO_REUSEADDR` 让第二个 socket 也能绑上（见 #11）。
改用替身注入：让前 N 次构造抛 `OSError`，看是否顺延。

### 22. 证明「ffmpeg 真内置」必须清空 PATH

只在正常环境看到 `ffmpeg:true` 不能证明 —— 可能来自 PATH。
必须在 `set PATH=C:\Windows\system32` 下验证，并同时确认
`where ffmpeg` 返回非 0（系统里确实没有）。实测：
干净 PATH 下 exe 仍报 `ffmpeg=true`，路径在 `_MEI123122\bin\ffmpeg.exe`。

### 23. 复用旧输出目录会得出假失败

测「取消」时若复用上一次已下完的目录，会把旧 mp4 误判成「取消后仍产出了 mp4」。
每次测试用带时间戳的全新目录。

### 24. `taskkill /IM python.exe` 会杀掉测试脚本自己

验收脚本本身可能就是 `python.exe`。只杀自己 `Popen` 出来的子进程句柄。

### 25. `Select-Object -First N` 会提前关管道杀掉进程

PowerShell 里 `python ... | Select-Object -First 5` 会让 Python 收到
管道关闭信号而中断。改用 `Out-File` 或 `-Last N`。
