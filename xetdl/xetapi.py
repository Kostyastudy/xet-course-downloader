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


def api_call(fetcher, path, params=None, method='POST', host=None, json_body=False):
    """通过注入的 fetcher 调接口。

    fetcher(path, params, method) -> dict
    fetcher(path, params, method, json_body) -> dict   （可选，声明式接口需要 JSON body）

    两种签名都支持，方便调用方与测试用最简单的那种。
    """
    try:
        return fetcher(path, params or {}, method, json_body)
    except TypeError:
        return fetcher(path, params or {}, method)


def column_items(fetcher, product_id, host=None):
    """拉取专栏/课程的课时表。

    实测（2026-11-17）：这个接口【公开可读】，未登录也能拿到完整 list。
    """
    j = api_call(fetcher, config.EP_COLUMN_ITEMS,
                 {'column_id': product_id, 'page_index': 1, 'page_size': 100, 'sort': 'asc'},
                 'POST')
    data = (j.get('data') or {})
    return data.get('list') or []


def column_items_paged(fetcher, product_id, page_size=100, max_pages=50):
    """翻页拉完一个专栏的全部课时。"""
    out = []
    for page in range(1, max_pages + 1):
        j = api_call(fetcher, config.EP_COLUMN_ITEMS,
                     {'column_id': product_id, 'page_index': page,
                      'page_size': page_size, 'sort': 'asc'}, 'POST')
        if j.get('code') != 0:
            break
        lst = ((j.get('data') or {}).get('list') or [])
        out.extend(lst)
        if len(lst) < page_size:
            break
    return out


def goods_info(fetcher, product_id):
    """大课包 / 商品信息。

    实测（2026-11-17）：参数名是 ``resource_id``，传 ``product_id`` 会得到
    code:10「参数错误」。这里固定用 resource_id。
    """
    return api_call(fetcher, config.EP_GOODS_INFO, {'resource_id': product_id}, 'POST')


def resource_available(fetcher, resource_id):
    """资源可购买状态（other_list 里会带出子专栏）。"""
    return api_call(fetcher, config.EP_RESOURCE_AVAILABLE,
                    {'resource_id': resource_id}, 'POST')


def store_goods(fetcher, category_goods_id, app_id='', micro_page_id=''):
    """店铺商品清单（列出店铺里有哪些课）。

    实测（2026-11-17）：
      * 走 JSON body，不是 form
      * ``category_goods_id`` 必须是字符串
      * 未登录也能读（首页公开数据）
    """
    body = {
        'app_id': app_id,
        'user_id': '',
        'agent_type': 1, 'agent_version': 1, 'is_offset': 1,
        'app_version': '0.1',
        'agent': 'Mozilla/5.0',
        'buz_data': {
            'channel_id': '', 'micro_page_id': micro_page_id, 'agent_mark': '',
            'lang': 'zh', 'client_type': 1, 'page_size': 20, 'page_num': 1,
            'category_goods_id': str(category_goods_id),
            'resource_count': 5, 'component_id': 40185813, 'app_type': 1,
        },
    }
    return api_call(fetcher, config.EP_STORE_GOODS, body, 'POST', json_body=True)


def extract_columns(node, exclude='', depth=0):
    """在任意 JSON 结构里递归找出所有「像专栏」的 p_ id。

    不依赖具体字段名 —— 平台改名也不失效。用于从大课包商品信息里
    提取子专栏列表。
    """
    out = {}
    stack = [(node, 0)]
    guard = 0
    while stack and guard < 20000:
        guard += 1
        n, d = stack.pop()
        if not isinstance(n, (dict, list)) or d > 8:
            continue
        if isinstance(n, list):
            for x in n:
                stack.append((x, d + 1))
            continue
        pid = n.get('product_id') or n.get('column_id') or n.get('pid')
        # 注意：判断依据是「值里有没有大课包关键字」，不是「键名」。
        type_name = str(n.get('resource_type_name') or n.get('type_name')
                        or n.get('sell_type') or '')
        is_big = bool(re.search(r'大专栏|大课包|BSC', type_name, re.I))
        if (isinstance(pid, str) and re.match(r'^p_[0-9a-z]+$', pid)
                and pid != exclude and not is_big):
            name = str(n.get('product_name') or n.get('title')
                       or n.get('name') or n.get('resource_title') or '')
            count = n.get('item_resource_count') or n.get('update_num') \
                or n.get('resource_count') or 0
            cur = out.get(pid)
            if cur is None:
                out[pid] = {'product_id': pid, 'name': name, 'count': count}
            else:
                # 同一个 id 可能出现多次（有的节点只有 id 没有名字），
                # 保留信息更全的那份，别让空值把名字盖掉。
                if not cur['name'] and name:
                    cur['name'] = name
                if not cur['count'] and count:
                    cur['count'] = count
        for v in n.values():
            if isinstance(v, (dict, list)):
                stack.append((v, d + 1))
    return list(out.values())


def courseware_list(fetcher, resource_id):
    """拉取资料课时的附件清单。"""
    j = api_call(fetcher, config.EP_COURSEWARE, {'resource_id': resource_id}, 'POST')
    return j.get('data') or []


def lookback_url(fetcher, alive_id):
    """直播回放 -> 明文 m3u8（分片行自带签名，不需要解密）。

    实测（2026-11-17）：这个接口【需要登录态】，
    未登录会返回 code:11302（风控），data 为空。
    """
    j = api_call(fetcher, config.EP_LOOKBACK + urllib.parse.quote(alive_id), None, 'GET')
    if j.get('code') == config.LOOKBACK_AUTH_REQUIRED_CODE:
        return None
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
