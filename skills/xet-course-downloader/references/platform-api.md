# 平台接口契约（2026-11-17 / 11-19 真实抓包确认）

所有接口都在 `https://<app_id>.h5.xet.pomoho.com` 下，路径以 `/xe.course.business...` 开头。
表单类接口要求 `application/x-www-form-urlencoded` 且参数包一层 `bizData[字段]=值`。

## ① 课时目录 —— 免登录

```
POST /xe.course.business_go.column.items.get/2.0.0
body: bizData[column_id]=p_xxx&bizData[page_index]=1&bizData[page_size]=100&bizData[sort]=asc
→ { code: 0, data: { list: [ { resource_id, resource_title, resource_type, start_at } ] } }
```

`resource_type`: **4 = 直播回放，3 = 录播，1 = 图文/资料**。

匿名页面也能拿到 `code:0` 与完整 list —— 所以先用它验证链路。翻页直到
`len(list) < page_size`。

## ② 大课包 → 子专栏 —— 唯一正解

```
POST /xe.course.business.topic.items.get/2.0.0
body: bizData[column_id]=p_xxx&bizData[page_index]=1&bizData[page_size]=50
→ { code: 0, data: { total: 14, list: [ { resource_id, resource_title, item_resource_count } ] } }
```

**重要**：`goods.info.get` 不返回子专栏。实测一个含 9 个子专栏、122 课时的大课包，
只有这个接口能拿全。大课包页面前端自己也是这么调的。

## ③ 商品信息 —— 参数名是 resource_id

```
POST /xe.course.business_go.goods.info.get/2.0.0
body: bizData[resource_id]=p_xxx        ← 不是 product_id
→ { code: 0, data: { resource_id, resource_type: 8, is_free, ... } }
```

传 `product_id` 会得到 `code:10 参数错误`。这个坑很隐蔽，因为大部分接口确实用
`column_id` / `product_id`。

## ④ 直播回放播放地址 —— 需要登录

```
GET /_alive/v3/get_lookback_list?alive_id=l_xxx
→ { code: 0, data: [ { default: true, line_sharpness: [ { url } ] } ] }
```

匿名访问返回 **`code:11302`（风控）**，`data` 为空。登录后 `code:0`。
m3u8 是明文，分片行自带签名，**不需要解密**。

挑线路：优先 `default:true`，否则取最后一个；再在其 `line_sharpness` 里取第一个有 url 的。

## ⑤ 录播播放地址 —— 需要登录，AES-128

```
POST /xe.course.business_go.video.mutli_line/1.0.0
body: bizData[resource_id]=v_xxx
→ { code: 0, data: { cloud_data: { zj: { encrypt_url } } } }
```

**签名约 30 分钟过期**，必须现取现用。`encrypt_url` 优先，回退 `url`。

## ⑥ 课件附件 —— 免登录，CDN 直链

```
POST /xe.course.business_go.courseware_list.get/2.0.0
body: bizData[resource_id]=i_xxx
→ { code: 0, data: [ { title, url, material_size } ] }
```

## ⑦ 店铺商品清单 —— JSON body

```
POST /xe.micro_page.category_good.get/2.0.0
Content-Type: application/json
body: { app_id, buz_data: { category_goods_id: "cg_xxx", micro_page_id, page_num, page_size, ... } }
→ { code: 0, data: { list: [ { spu_id, title, jump_url } ] } }
```

两个坑：`category_goods_id` **必须是字符串**；这个是 JSON body 而非 form。
首页多个分类共用一个 `cg` id，切分类靠前端 tab。

## 取数桥实现要点

```js
function api(path, params, method) {
  var opt = {
    method: method || 'POST',
    credentials: 'include',                    // ← 关键：带上页面登录态
    headers: { 'Accept': 'application/json, text/plain, */*' }
  };
  if (params) {
    opt.headers['Content-Type'] = 'application/x-www-form-urlencoded';
    opt.body = form(params);                   // bizData[k]=v
  }
  return fetch(ORIGIN + path, opt).then(r => r.text()).then(t => {
    if (!t) return { code: -1, _empty: true, msg: '空响应（接口变了或没登录）' };
    try { return JSON.parse(t); } catch (e) { return { code: -1, _raw: t.slice(0, 300) }; }
  });
}
```

`ORIGIN` 用 `location.origin`，不要硬编码域名 —— 换店铺就能直接复用。

## 递归提取专栏 id（抗改名的写法）

不依赖具体字段名，遍历 JSON 找所有形如 `p_[0-9a-z]+` 的值，同时排除
`resource_type_name` 含「大专栏 / 大课包 / BSC」的节点（那是大课包自己，
混进来会导致重复展开）：

```js
var pid = n.get('product_id') || n.get('column_id') || n.get('pid');
var isBig = /大专栏|大课包|BSC/i.test(str(n.get('resource_type_name') || n.get('type_name')));
if (/^p_[0-9a-z]+$/.test(pid) && pid !== exclude && !isBig) { /* 收下 */ }
```

同一个 id 可能多次出现（有的节点只有 id 没名字），要保留**信息更全**的那份，
别让空值把已有名字盖掉。这是实测踩过的：名字被空串覆盖后目录全是 `p_xxx`。
