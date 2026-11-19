# 下载引擎：为什么这么写（每条都有实测依据）

## 核心结论：不要用 ffmpeg 直拉远端加密 HLS

实测（2026-11-19，122 节课的真实环境）：让 ffmpeg `-i <远端加密m3u8>` 直拉，
速度只有 **0.8 倍速** —— 因为它单线程串行解密，等于实时播放，一节课 45 分钟就要 45 分钟。

**正确路径**：

```python
# 1. 取 m3u8 文本，解析出分片列表
tasks = [(i, basename(url), abs_url) for i, url in enumerate(segments)]

# 2. 并发拉【密文】分片 —— 远端不解密，走满带宽
done, nbytes, failed = _pull_segments(seg_dir, tasks, threads, progress, encrypted=True)

# 3. 写本地 m3u8：分片指本地文件，KEY 行补成绝对 URL
write_local_playlist(...)

# 4. ffmpeg 读本地 m3u8 + 远端密钥，一次性解密拼接
ffmpeg -allowed_extensions ALL \
       -protocol_whitelist file,http,https,tcp,tls,crypto,httpproxy \
       -i local.m3u8 -c copy -bsf:a aac_adtstoasc -movflags +faststart out.tmp.mp4

# 5. ffprobe 校验；失败则删分片重下（最多 3 轮）
```

## `-protocol_whitelist` 必须包含 crypto

拉录播时少了这项会直接失败。完整值：

```
file,http,https,tcp,tls,crypto,httpproxy
```

## KEY 行的相对 URI 必须补成绝对地址

**这是个真实 bug，实测踩过**：本地 m3u8 放在 `.segs/<rid>/` 里，如果 KEY 行是
`URI="k.bin"`，ffmpeg 会去 `.segs/<rid>/k.bin` 找密钥 —— 找不到，整段合并失败，
重试 3 轮后报「合并后校验未通过」。

```python
def _absolutize_key(key_line, base):
    m = re.search(r'URI\s*=\s*"([^"]*)"', key_line)
    uri = m.group(1)
    if uri.startswith(('http://', 'https://', 'data:', 'file:')):
        return key_line
    return key_line[:m.start(1)] + urllib.parse.urljoin(base, uri) + key_line[m.end(1):]
```

有些平台给绝对 URL，有些给相对的。统一兜住，代价为零。

## 加密分片的合法性判断：只看体积

明文 TS 分片以 `0x47`（同步字节）开头，但 **AES-128 密文不是**。
密文分片体积是 **16 的整数倍**（不是 188 —— 加密后填充会变）。

```python
if encrypted:
    if len(data) % 16 != 0: raise RuntimeError('密文体积不是 16 的整数倍')
else:
    if not valid_ts_bytes(data): raise RuntimeError('不是合法 TS 分片')
```

用错判据的后果：所有分片都被判成损坏，无限重下。

## 同名分片必须补序号

加密 m3u8 里常见**所有分片同一个文件名**，靠 URL 的 `Range` 参数区分：

```
v.f146750_0.ts?start=0&end=655359
v.f146750_0.ts?start=655360&end=1310719
```

如果直接 `basename()` 当本地文件名，全部写成同一个文件。
必须补 `_NNNNN` 序号：

```python
def _unique_names(tasks):
    seen = {}
    out = []
    for i, name, url in tasks:
        if name in seen:
            seen[name] += 1
            stem, dot, ext = name.rpartition('.')
            name = '%s_%05d.%s' % (stem, seen[name], ext) if dot else '%s_%05d' % (name, seen[name])
        else:
            seen[name] = 0
        out.append((i, name, url))
    return out
```

## 大 m3u8 必须分块读

一次性 `r.read()` 在 **320 KB** 的 m3u8 上必抛 `IncompleteRead`。改 65536 字节循环读：

```python
buf = []
while True:
    chunk = r.read(65536)
    if not chunk: break
    buf.append(chunk)
data = b''.join(buf)
```

## 取消必须能真打断

原来只在每 100 个分片上报一次进度、也不检查取消标志。后果：

- 一节课 12 片，进度条全程显示 `0/12`，用户以为卡死
- 点取消要等整节课下完才生效，一节课 45 分钟等于取消无效

修法：分片循环里**每片前**查一次取消；进度用 `% 100` 或 **0.5 秒时间兜底**双条件。

```python
def worker():
    while True:
        if cancelled and cancelled():
            st['stop'] = True
            return
        ...
        if progress and (st['done'] % 100 == 0 or now - last['t'] > 0.5):
            last['t'] = now
            progress({...})
```

## 文件名与序号规则

- `safe_name()` 去掉 Windows 非法字符 `\ / : * ? " < > |`，长度截断，保留中文
- 课时目录 `<NN>_<标题>`；多专栏时多套一层 `<NN>_<专栏名>/`
- **已有同名目录要复用，忽略序号前缀**。原因：只补下某几节时，子集计划的
  `group_index` 会从 01 重排，导致「04_行业解析」被重新编号成「01_行业解析」，
  课时落到新目录里、看起来像丢文件。
- 幂等：已存在 `视频.mp4` 且 > 1 MB 直接跳过（`[HAVE]`）

## 合并期间的中间状态

`视频.mp4.tmp.mp4` 显示 **48 字节或 0 字节是正常的** —— ffmpeg 先写临时文件，
成功后才 `os.replace` 到正式名。不要因此判断失败，也不要手动删。

课件回填：文件名里的日期 / 第N讲 / 第N章 / N课 / 结尾序号 / 括号序号，
按可靠性从高到低依次匹配课时。实测能覆盖
`20260301讲义.pdf`、`第2讲课件.zip`、`高数第3章PPT.pptx`、`Python高阶课程（5）.pdf` 这类命名。

## 归档优先级

同盘优先硬链接（秒级、零额外空间），失败再 `shutil.copy2`。
课件分发到对应课时目录时也走这条路，避免同一份课件占两份空间。
