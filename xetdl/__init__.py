# -*- coding: utf-8 -*-
"""xetdl —— 小鹅通课程视频/课件下载工具（纯标准库实现 + ffmpeg）。

设计要点：
  * 浏览器扩展只负责「在登录态页面里取目录与签名 URL」，几十 GB 的实际流量走本地下载器。
  * 下载器只依赖标准库 urllib 与外部 ffmpeg/ffprobe，不需要 pip 安装任何包。
"""
from .config import VERSION as __version__   # 单一事实来源，别再两处对不上

__all__ = ['config', 'model', 'xetapi', 'downloader', 'courseware', 'archiver',
           'jobs', 'server']
