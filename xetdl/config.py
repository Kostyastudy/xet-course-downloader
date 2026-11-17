# -*- coding: utf-8 -*-
"""全局配置与常量。"""
import os
import shutil

APP_NAME = 'xet-course-downloader'
VERSION = '1.1.0'

# ---- 小鹅通接口 ----
HOST = 'https://appisa6gqvx4105.h5.xet.pomoho.com'
EP_COLUMN_ITEMS = '/xe.course.business_go.column.items.get/2.0.0'
EP_COURSEWARE = '/xe.course.business_go.courseware_list.get/2.0.0'
EP_VOD_LINES = '/xe.course.business_go.video.mutli_line/1.0.0'
EP_LOOKBACK = '/_alive/v3/get_lookback_list?alive_id='
EP_SUBSCRIBE_CHECK = '/xe.course.business_go.subscribe.check.get/2.0.0'
# 实测（2026-11-17）：大课包商品信息的参数名是 resource_id，不是 product_id
EP_GOODS_INFO = '/xe.course.business_go.goods.info.get/2.0.0'
EP_RESOURCE_AVAILABLE = '/xe.course.business_go.resource.available.get/2.0.0'
# 店铺商品清单（找有哪些课可下）
EP_STORE_GOODS = '/xe.micro_page.category_good.get/2.0.0'
# 实测：目录接口公开可读；播放地址需要登录态（11302 = 未登录风控）
LOOKBACK_AUTH_REQUIRED_CODE = 11302

# 常见的公开商城域（不同店铺 app_id 不同，host 形如 https://<app_id>.h5.xet.pomoho.com）
DEFAULT_HOST = HOST

# ---- 本地服务 ----
DEFAULT_PORT = 8777
HOST_BIND = '127.0.0.1'

# ---- 目录 ----
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(HERE, 'xetdl', 'web')
EXT_DIR = os.path.join(HERE, 'extension')

# 默认下载根目录：用户主目录 / 小鹅通课程
DEFAULT_OUT = os.path.join(os.path.expanduser('~'), '小鹅通课程')

# ---- 下载参数 ----
DEFAULT_THREADS = 10          # 每讲并发分片数
DEFAULT_JOBS = 2              # 同时下载几讲
DEFAULT_CHUNK_MINUTES = 10    # 录播分块（分钟）
FFMPEG = shutil.which('ffmpeg') or 'ffmpeg'
FFPROBE = shutil.which('ffprobe') or 'ffprobe'

# ---- 教学/课程类型 ----
TYPE_LIVE = 4        # 直播回放
TYPE_VOD = 3         # 录播
TYPE_TEXT = 1        # 图文 / 资料

TYPE_NAMES = {4: '直播回放', 3: '录播', 1: '图文资料', 2: '图文'}

# ---- 合规提示 ----
LEGAL_NOTICE = (
    '本工具仅供下载你自己已购买、且平台允许下载的内容，用于个人离线学习与复习。\n'
    '下载所得音视频与课件的著作权归原权利人所有，请勿传播、转载、上传至公开平台、'
    '或用于任何商业用途（不得商用）。二次分发前请先取得权利人授权。'
)
