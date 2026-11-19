---
name: xet-course-downloader
description: |
  从「已付费购买」的网课平台（小鹅通 / xet.pomoho / h5.xet 系列店铺）把课程视频与课件
  完整下载到本地，并归档成可离线学习的目录结构。也包含把这类下载器打包成免安装
  Windows exe（内置 ffmpeg）的成熟路径。
  Use this skill whenever the user mentions 小鹅通 / 下载已买的课 / 导出自己的课程 /
  把网课存到本地 / 课程视频下载器 / 备份我买的专栏 / "download my purchased course" /
  "crawl this course platform" / 或者要写一个「在已登录网页里取播放地址再本地并发下载」的工具。
  也用于排查这类下载器跑不通的问题（目录读不到 / 播放地址 11302 / 合并后损坏 / 只有 0.8 倍速）。
  不要用于：没有购买权限的站点抓取、绕过付费墙、批量搬运他人内容（本技能假定用户已付费且只下自有内容）、
  以及纯视频转文字（用 course-to-notes）。
descriptions:
  zh-Hans: "把已付费购买的网课（小鹅通等）完整下载归档，并可打包成免安装 Windows exe。含实测接口契约与踩坑清单。"
displayNames:
  zh-Hans: "网课下载与归档"
---

# 已购网课 → 本地归档（含打包成 exe）

本技能沉淀的是一条**已经跑通 122 节课 / 76 GB / 0 异常**的实现路径。核心价值在
「哪些做法验证有效、哪些坑一定会踩」，照着走可以省掉大量试错。

## 适用前提（硬约束，不满足就停）

1. **用户已付费购买**该课程。本技能只下用户自有的内容。
2. **认证必须用户本人完成**。不得代填手机号 / 验证码 / 密码，不得读取、
   存储或外传任何 Cookie / Token —— 靠的是浏览器页面自身的登录态。
3. 下载所得仅用于个人离线学习，不商用、不传播。产物里必须留合规声明。

不满足就明确拒绝，不要「想办法绕过」。

## 架构：三段解耦（不要合并成一段）

```
① 本地控制台 (Python 标准库 http.server)      ← 无凭证、无 cookie
        ↕ HTTP 127.0.0.1
② 浏览器取数桥 (扩展 / 控制台片段)             ← 在已登录页面里同源 fetch
        ↕ 平台接口
③ 下载引擎 (并发分片 + ffmpeg 解密合并 + ffprobe 校验)
```

**为什么必须解耦**：平台对非浏览器客户端返回 `HTTP 200 + 空响应体`，播放地址接口
直接给 `code:11302`（风控）。只有**在已登录页面里发同源 fetch** 拿得到地址。
把认证塞进 Python 侧是最常见的走不通的路。

关键设计：取数桥是**同一份 JS 文件**，既能当扩展内容脚本加载，也能被本地服务
在 `/bridge.js` 下发后 eval —— 避免两份实现漂移。

## 标准执行流程

### 步骤 1 · 定位课程

从课程页 URL 解析出 `app_id`（host 形如 `https://<app_id>.h5.xet.pomoho.com`）与
`product_id`（`p_xxx`）。让用户直接粘课程页链接，不要让他手找 ID。

### 步骤 2 · 读目录（免登录，先验证链路通）

`POST /xe.course.business_go.column.items.get/2.0.0`

这一步**不需要登录**。先跑通它，能立刻区分「网络/域名问题」和「登录态问题」。
拿到课时清单（`resource_type`: 4=直播回放 3=录播 1=资料）。

### 步骤 3 · 大课包展开子专栏

如果是大课包（一个 `p_` 下含多个专栏），用
`POST /xe.course.business.topic.items.get/2.0.0`（`bizData[column_id]=p_xxx`）。

**这是唯一能拿全子专栏的接口。** `goods.info.get` 不返回子专栏——这是最容易
走弯路的地方，别在它身上耗时间。

### 步骤 4 · 在已登录页面取播放地址

- 直播回放：`GET /_alive/v3/get_lookback_list?alive_id=l_xxx` → 明文 m3u8，分片自带签名
- 录播：`POST /xe.course.business_go.video.mutli_line/1.0.0` → **AES-128**，签名约 30 分钟过期

**必须现取现用**：录播地址 30 分钟失效，不能「先全量取完再慢慢下」。
按「取一批 → 下完 → 再取下一批」推进，或每节下载前单独取。

并发取地址的实测上限是 **5**（再高没验证过，可能触发 11302）。串行取 192 节要 4 分钟以上，
用户会以为卡死。

### 步骤 5 · 下载（本技能最关键的一段）

**不要**让 ffmpeg 直接拉远端加密 HLS —— 实测只有 **0.8 倍速**（单线程串行解密，
约等于实时播放）。正确做法：

1. 并发拉**密文**分片（远端不解密，走满带宽）
2. 写一份本地 m3u8：分片路径换成本地文件名，KEY 行补成**绝对 URL**
3. 让 ffmpeg 读本地 m3u8 + 远端密钥，**一次性**解密拼接
4. ffprobe 校验，失败重下分片（3 轮）

细节与参数见 `references/download-engine.md`。

### 步骤 6 · 归档 + 校验

按 `<课程>/<NN_专栏>/<NN_课时>/视频.mp4` 落盘，课件按文件名回填到对应课时。
生成 `00_课程总览.md` 与 `使用须知.md`（含合规声明）。
最后用 ffprobe 全量复核产物的时长与可解码性。

## 排查对照表（症状 → 真因）

| 症状 | 真因 | 处理 |
|---|---|---|
| 播放地址返回 `11302` | 不在已登录页面里取 | 改从页面同源 fetch，别用 Python 直连 |
| 接口返回 `200` 但 body 空 | 平台挡非浏览器客户端 | 同上，并带 `credentials: 'include'` |
| 目录只拿到 1 层，缺子专栏 | 用了 `goods.info.get` | 换 `topic.items.get` |
| `goods.info.get` 返回 `code:10 参数错误` | 参数名写成了 `product_id` | 改 `resource_id` |
| 下载只有 0.8 倍速 | ffmpeg 直拉远端加密 HLS | 改并发拉密文 + 本地解密拼接 |
| 合并后文件损坏 / 0 字节 | 分片不完整或 m3u8 名字冲突 | 见 `references/download-engine.md` |
| 大 m3u8 读取抛 `IncompleteRead` | 一次性 `read()` | 改 65536 分块循环读 |
| 两实例抢同一端口、配置莫名串了 | Windows `SO_REUSEADDR` 允许抢占 | Windows 上关掉 `allow_reuse_address` |

完整踩坑清单（含每条的实测数字）见 `references/pitfalls.md`。

## 打包成免安装 exe

用户手上没有 Python 时，`references/packaging-exe.md` 给出一条实测可用的
PyInstaller 路径：内置 ffmpeg、单文件 133 MB、冷启动 2.1 秒、双轨并存（exe 与
Python 版共源码但端口/配置隔离）。

要点先说三条，避免踩坑：

- ffmpeg 放 spec 的 `datas`，**不要**放 `binaries`（后者会被扫依赖改路径）
- spec 里的相对路径基准是 **spec 所在目录**，不是 cwd
- `upx=False`（压 168 MB 的 ffmpeg 又慢又极易触发杀软误报）

## 输出契约

```
<输出根>/<课程名>/
  00_课程总览.md            # 讲次表 + 状态
  使用须知.md               # 合规声明
  下载计划.json             # 可复用于无界面重跑
  <NN_专栏名>/              # 多专栏时才有这一层
    <NN_课时名>/
      视频.mp4
      课件/
```

命名与序号规则、幂等跳过规则见 `references/download-engine.md` 末节。

## 验收标准（做完要能给出实测数字，不能只说「应该可以」）

1. 随机抽 3 节课 ffprobe，时长 > 0 且无解码错误
2. 全量扫描 `.segs` 残留为 0
3. 总文件数与课时清单一致
4. 打包版：干净 `PATH` 下（`where ffmpeg` 找不到）仍能下载成功 —— 这条才能证明 ffmpeg 真的内置
