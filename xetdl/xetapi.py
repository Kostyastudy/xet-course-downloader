# -*- coding: utf-8 -*-
"""小鹅通接口封装与 URL 解析。

认证不在这里解决：所有需要登录态的调用都通过一个 ``fetcher(path, params, method)``
回调注入。回调可以是：
  * 浏览器扩展在页面里用同源 fetch 调用（推荐）
  * 任何自带 Cookie 的 HTTP 客户端
这样本地下载器完全不需要持有用户凭证。
"""
import io
import json
import re
import urllib.parse

from . import config

# 形如 https://<app_id>.h5.xet.pomoho.com/p/course/column/<product_id>?type=3
RE_COLUMN = re.compile(r'/p/course/(?:big_)?column/([a-z]_[0-9a-z]+)', re.I)
RE_TEXT = re.compile(r'/p/course/text/([a-z]_[0-9a-z]+)', re.I)
RE_ALIVE = re.compile(r'/v4/course/alive/([a-z]_[0-9a-z]+)', re.I)
RE_HOST = re.compile(r'^https?://([0-9a-z]+)\.(h5\.xet\.pomoho\.com|xet\.pomoho\.com)', re.I)
RE_APPID = re.compile(r'app_id=([0-9a-z]+)', re.I)


def parse_course_url(url):
    """从任意小鹅通页面 URL 里解析出可用的定位信息。

    返回 dict: kind / product_id / resource_id / host / app_id
    kind: column（专栏/课程页）| text（图文课时）| alive（直播回放）| unknown
    """
    url = (url or '').strip()
    out = {'kind': 'unknown', 'product_id': '', 'resource_id': '',
           'host': config.DEFAULT_HOST, 'app_id': '', 'raw': url}
    if not url:
        return out
    m = RE_HOST.match(url)
    if m:
        out['host'] = 'https://%s.%s' % (m.group(1), m.group(2))
    m = RE_APPID.search(url)
    if m:
        out['app_id'] = m.group(1)
    m = RE_COLUMN.search(url)
    if m:
        out['kind'] = 'column'
        out['product_id'] = m.group(1)
    m = RE_TEXT.search(url)
    if m:
        out['kind'] = 'text'
        out['resource_id'] = m.group(1)
    m = RE_ALIVE.search(url)
    if m:
        out['kind'] = 'alive'
        out['resource_id'] = m.group(1)
    q = urllib.parse.urlparse(url).query
    qs = urllib.parse.parse_qs(q)
    if not out['product_id'] and qs.get('product_id'):
        out['product_id'] = qs['product_id'][0]
        out['kind'] = 'column'
    if not out['resource_id'] and qs.get('resource_id'):
        out['resource_id'] = qs['resource_id'][0]
    if not out['app_id'] and qs.get('app_id'):
        out['app_id'] = qs['app_id'][0]
    return out


def form_body(params, pack='bizData'):
    """小鹅通网关要求 application/x-www-form-urlencoded + bizData[字段]=值。"""
    if pack is None:
        return urllib.parse.urlencode(params)
    return urllib.parse.urlencode({'%s[%s]' % (pack, k): v for k, v in params.items()})


def api_call(fetcher, path, params=None, method='POST', host=None):
    """通过注入的 fetcher 调接口。fetcher(path, params, method) -> dict。"""
    return fetcher(path, params or {}, method)


def column_items(fetcher, product_id, host=None):
    """拉取专栏/课程的课时表。"""
    j = api_call(fetcher, config.EP_COLUMN_ITEMS,
                 {'column_id': product_id, 'page_index': 1, 'page_size': 200, 'sort': 'asc'},
                 'POST')
    data = (j.get('data') or {})
    return data.get('list') or []


def courseware_list(fetcher, resource_id):
    """拉取资料课时的附件清单。"""
    j = api_call(fetcher, config.EP_COURSEWARE, {'resource_id': resource_id}, 'POST')
    return j.get('data') or []


def lookback_url(fetcher, alive_id):
    """直播回放 -> 明文 m3u8（分片行自带签名，不需要解密）。"""
    j = api_call(fetcher, config.EP_LOOKBACK + urllib.parse.quote(alive_id), None, 'GET')
    data = j.get('data') or []
    pick = None
    for line in data:
        if line.get('default'):
            pick = line
            break
    if pick is None and data:
        pick = data[-1]
    if not pick:
        return None
    for sh in (pick.get('line_sharpness') or []):
        if sh.get('url'):
            return sh['url']
    return None


def vod_url(fetcher, resource_id):
    """录播 -> AES-128 m3u8。签名约 30 分钟过期，必须现取现用。"""
    j = api_call(fetcher, config.EP_VOD_LINES, {'resource_id': resource_id}, 'POST')
    zj = ((j.get('data') or {}).get('cloud_data') or {}).get('zj') or {}
    return zj.get('encrypt_url') or zj.get('url') or None


def normalize_items(items, product_id=''):
    """把 column.items.get 的原始条目转成 Lesson 列表（未解析播放地址）。"""
    from .model import Lesson
    out = []
    for it in items:
        try:
            rt = int(it.get('resource_type') or 0)
        except Exception:
            rt = 0
        out.append(Lesson(
            rid=it.get('resource_id') or '',
            title=(it.get('resource_title') or '').strip(),
            rtype=rt,
            start_at=it.get('start_at') or '',
            product_id=product_id,
        ))
    return out


def to_json(obj):
    return json.dumps(obj, ensure_ascii=False)
