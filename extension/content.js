/* ============================================================
 * 小鹅通课程下载器 —— 取数核心（扩展与控制台桥共用同一份实现）
 *
 * 这个文件是唯一的一份「取数」实现：
 *   · 作为扩展内容脚本加载  → 注册 chrome.runtime 消息监听
 *   · 被控制台桥 eval 执行  → 自动开始采集
 * 由本地服务 /bridge.js 直接下发本文件内容（见 xetdl/server.py）。
 *
 * ────────────────────────────────────────────────────────────
 * 接口契约（2026-11-17 在真实已登录/未登录页面上实测确认）
 * ────────────────────────────────────────────────────────────
 *
 * ① 课程目录 —— 【公开可读，不需要登录】
 *    POST /xe.course.business_go.column.items.get/2.0.0
 *    body: bizData[column_id]=p_xxx
 *          &bizData[page_index]=1&bizData[page_size]=100&bizData[sort]=asc
 *    → { code: 0, data: { list: [ {resource_id, resource_title,
 *                                    resource_type, start_at}, ... ] } }
 *    resource_type: 4=直播回放  3=录播  1=资料(课件)
 *    实测：匿名页面也能拿到 code:0 与完整 list。
 *
 * ② 大课包商品信息 —— 参数名是 resource_id（不是 product_id！）
 *    POST /xe.course.business_go.goods.info.get/2.0.0
 *    body: bizData[resource_id]=p_xxx
 *    → { code: 0, data: { resource_id, resource_type: 8, is_free, ... } }
 *    实测：传 product_id 会得到 code:10「参数错误」。
 *
 * ③ 店铺商品清单（找有哪些课可下）—— 需要 category_goods_id
 *    POST /xe.micro_page.category_good.get/2.0.0   （JSON body）
 *    body.buz_data = { category_goods_id: "cg_xxx", micro_page_id: "4760792",
 *                       page_num: 1, page_size: 20, ... }
 *    → { code: 0, data: { list: [ {spu_id, title, jump_url, ...} ] } }
 *    实测：category_goods_id 必须是字符串；首页 4 个分类共用一个 cg id，
 *    切换分类靠前端 tab，不换 cg id。
 *
 * ④ 直播回放播放地址 —— 【需要登录】
 *    GET  /_alive/v3/get_lookback_list?alive_id=l_xxx
 *    → { code: 0, data: [ { default: true, line_sharpness: [ {url} ] } ] }
 *    实测：匿名访问返回 code:11302（风控），登录后 code:0。
 *    m3u8 明文带签名，分片行自带签名，不需要解密。
 *
 * ⑤ 录播播放地址 —— 【需要登录】，AES-128，签名约 30 分钟过期
 *    POST /xe.course.business_go.video.mutli_line/1.0.0
 *    body: bizData[resource_id]=v_xxx
 *    → { code: 0, data: { cloud_data: { zj: { encrypt_url } } } }
 *
 * ⑥ 课件附件 —— 【免登录，CDN 直链】
 *    POST /xe.course.business_go.courseware_list.get/2.0.0
 *    body: bizData[resource_id]=i_xxx
 *    → { code: 0, data: [ { title, url, material_size, ... } ] }
 *
 * ────────────────────────────────────────────────────────────
 * 重要结论
 * ────────────────────────────────────────────────────────────
 *  · 目录是公开的 → 任何页面都能读出「有哪些课、每课多少节」
 *  · 播放地址需要登录态 → 必须在【已登录】的小鹅通页面里取
 *  · 小鹅通对非浏览器客户端返回 HTTP 200 + 空响应体，
 *    get_lookback_list 更是 code:11302 → 必须同源 fetch
 *  · 全程不读取、不存储、不外传任何 Cookie：用的是浏览器自带的登录态
 * ============================================================ */
(function () {
  'use strict';

  var IS_CHROME = !!(typeof chrome !== 'undefined' && chrome.runtime &&
                      chrome.runtime.onMessage);
  /* __XETDL_LOCAL__ 由本地服务在下发时替换成它自己的地址 */
  var LOCAL = '__XETDL_LOCAL__';
  var ORIGIN = location.origin;

  var EP = {
    items:  '/xe.course.business_go.column.items.get/2.0.0',
    goods:  '/xe.course.business_go.goods.info.get/2.0.0',
    ware:   '/xe.course.business_go.courseware_list.get/2.0.0',
    vod:    '/xe.course.business_go.video.mutli_line/1.0.0',
    lookback: '/_alive/v3/get_lookback_list?alive_id=',
    goodsList: '/xe.micro_page.category_good.get/2.0.0',
    /* 大课包目录（实测唯一能拿全子专栏的接口） */
    topicItems: '/xe.course.business.topic.items.get/2.0.0'
  };

  /* ------------------------------------------------------------------ */
  /* 基础请求                                                             */
  /* ------------------------------------------------------------------ */

  function form(params) {
    var s = [];
    for (var k in params) {
      s.push(encodeURIComponent('bizData[' + k + ']') + '=' +
             encodeURIComponent(params[k]));
    }
    return s.join('&');
  }

  function api(path, params, method) {
    var opt = {
      method: method || 'POST',
      credentials: 'include',
      headers: { 'Accept': 'application/json, text/plain, */*' }
    };
    if (params) {
      opt.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      opt.body = form(params);
    }
    return fetch(ORIGIN + path, opt).then(function (r) { return r.text(); })
      .then(function (t) {
        if (!t) return { code: -1, _empty: true, msg: '空响应（多半是接口变了或没登录）' };
        try { return JSON.parse(t); }
        catch (e) { return { code: -1, _raw: t.slice(0, 300) }; }
      });
  }

  function postJson(path, body) {
    return fetch(ORIGIN + path, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.text(); })
      .then(function (t) {
        if (!t) return { code: -1, _empty: true, msg: '空响应' };
        try { return JSON.parse(t); }
        catch (e) { return { code: -1, _raw: t.slice(0, 300) }; }
      });
  }

  /* ------------------------------------------------------------------ */
  /* URL 解析                                                             */
  /* ------------------------------------------------------------------ */

  function parseUrl(url) {
    var out = { kind: 'unknown', pid: '', appId: '' };
    url = url || location.href;
    var m = /\/p\/course\/(big_)?column\/([a-z]_[0-9a-z]+)/i.exec(url);
    if (m) {
      out.kind = m[1] ? 'program' : 'column';
      out.pid = m[2];
    }
    m = /\/p\/course\/text\/([a-z]_[0-9a-z]+)/i.exec(url);
    if (m) { out.kind = 'text'; out.pid = m[1]; }
    m = /\/v4\/course\/alive\/([a-z]_[0-9a-z]+)/i.exec(url);
    if (m) { out.kind = 'alive'; out.pid = m[1]; }
    m = /[?&]product_id=([a-z]_[0-9a-z]+)/i.exec(url);
    if (m && !out.pid) { out.pid = m[1]; out.kind = 'column'; }
    m = /[?&]app_id=([0-9a-z]+)/i.exec(url);
    if (m) out.appId = m[1];
    else out.appId = (location.hostname.split('.')[0] || '');
    return out;
  }

  /* ------------------------------------------------------------------ */
  /* 目录：column.items.get（公开可读）                                    */
  /* ------------------------------------------------------------------ */

  function columnItems(pid, pageIndex, pageSize) {
    return api(EP.items, {
      column_id: pid,
      page_index: pageIndex || 1,
      page_size: pageSize || 100,
      sort: 'asc'
    }).then(function (j) {
      if (!j || j.code !== 0) {
        return { ok: false, items: [], why: 'code=' + (j && j.code) +
                ' msg=' + ((j && j.msg) || '') };
      }
      var list = ((j.data || {}).list) || [];
      /* 实测：大课包页用 column.items.get 查会返回 code:0 但 list 为空
         （子专栏在 goods.info.get 里）。所以空 list 必须当成「没查到」，
         否则会误判成功、永远走不到 S2 的子专栏分支。 */
      return { ok: list.length > 0, items: list, why: list.length + ' 个课时' };
    });
  }

  /* 拉完一个专栏的全部分页 */
  function columnAll(pid) {
    var all = [], page = 1;
    function step() {
      return columnItems(pid, page, 100).then(function (r) {
        if (!r.ok) return { ok: false, items: all, why: r.why };
        all = all.concat(r.items);
        if (r.items.length < 100) return { ok: true, items: all, why: all.length + ' 个课时' };
        page++;
        return step();
      });
    }
    return step();
  }

  /* ------------------------------------------------------------------ */
  /* 大课包 → 子专栏                                                     */
  /* ------------------------------------------------------------------ */

  /* 通用结构提取：在任意 JSON 里递归找出所有「像专栏」的 p_ id。
     不依赖具体字段名，平台改字段也不会失效。 */
  function extractColumns(node, exclude, depth) {
    var out = {}, stack = [[node, 0]], guard = 0;
    while (stack.length && guard++ < 20000) {
      var pair = stack.pop(), n = pair[0], d = pair[1];
      if (!n || typeof n !== 'object' || d > 8) continue;
      if (Object.prototype.toString.call(n) === '[object Array]') {
        for (var i = 0; i < n.length; i++) stack.push([n[i], d + 1]);
        continue;
      }
      var pid = n.product_id || n.column_id || n.pid;
      var isBig = /大专栏|大课包|BSC/.test(
        String(n.resource_type_name || n.type_name || n.sell_type || ''));
      if (typeof pid === 'string' && /^p_[0-9a-z]+$/.test(pid) &&
          pid !== exclude && !isBig) {
        var name = n.product_name || n.title || n.name || n.resource_title || '';
        var count = n.item_resource_count || n.update_num || n.resource_count || 0;
        if (!out[pid]) {
          out[pid] = { product_id: pid, name: String(name), count: count };
        } else {
          /* 同一个 id 可能出现多次（有的节点只有 id 没有名字），
             保留信息更全的那份，别让空值把名字盖掉。 */
          if (!out[pid].name && name) out[pid].name = String(name);
          if (!out[pid].count && count) out[pid].count = count;
        }
      }
      for (var k in n) {
        if (typeof n[k] === 'object' && n[k] !== null) stack.push([n[k], d + 1]);
      }
    }
    var list = [];
    for (var p in out) list.push(out[p]);
    list.sort(function (a, b) { return (b.count || 0) - (a.count || 0); });
    return list;
  }

  /* 大课包 -> 子专栏（首选）。实测大课包页自己就是这么调的：
   * POST /xe.course.business.topic.items.get/2.0.0
   *   body: bizData[column_id]=p_xxx & bizData[page_index]=1 & bizData[page_size]=8
   *   → { code:0, data:{ total:14, list:[{resource_id, resource_title, ...}] } }
   * 这是目前唯一能拿全子专栏的接口（goods.info.get 不返回子专栏）。 */
  function findTopicColumns(pid, trace) {
    var page = 1;
    var all = [];
    var total = 0;

    function step() {
      return api(EP.topicItems, {
        column_id: pid, page_index: page, page_size: 50
      }).then(function (j) {
        if (!j || j.code !== 0) {
          var w = 'topic.items.get code=' + (j && j.code);
          if (trace) trace.push('   ' + w);
          return { cols: [], why: w };
        }
        var d = j.data || {};
        total = d.total || total;
        var list = d.list || [];
        for (var i = 0; i < list.length; i++) {
          var it = list[i];
          var cid = it.resource_id || it.product_id || it.column_id;
          if (!cid || !/^p_[0-9a-z]+$/.test(cid)) continue;
          all.push({
            product_id: cid,
            name: String(it.resource_title || it.product_name || it.title || ''),
            count: it.item_resource_count || it.update_num || it.resource_count || 0
          });
        }
        if (list.length >= 50 && page < 20) { page++; return step(); }
        var why = 'topic.items.get 命中 ' + all.length + ' 个专栏（total=' + total + '）';
        if (trace) trace.push('   ' + why);
        return { cols: all, why: why };
      }).catch(function (e) {
        var w2 = 'topic.items.get 异常: ' + e.message;
        if (trace) trace.push('   ' + w2);
        return { cols: [], why: w2 };
      });
    }
    return step();
  }

  /* 大课包商品信息 —— 实测参数名是 resource_id */
  function goodsInfo(pid) {
    return api(EP.goods, { resource_id: pid }).then(function (j) {
      if (!j || j.code !== 0) {
        return { ok: false, data: null, why: 'code=' + (j && j.code) +
                ' msg=' + ((j && j.msg) || '') };
      }
      return { ok: true, data: j.data, why: 'ok' };
    });
  }

  /* 大课包 -> 子专栏。依次试几个常见参数名，谁先返回 code:0 用谁。
     实测（2026-11-17）：resource_id 是有效参数名（传 product_id 会 code:10）。 */
  function findChildColumns(pid, trace) {
    var tries = ['resource_id', 'product_id', 'column_id', 'id'];
    var i = 0;

    function step() {
      if (i >= tries.length) {
        return Promise.resolve({ cols: [], why: 'goods.info.get 参数都试过了' });
      }
      var key = tries[i++];
      return api(EP.goods, { [key]: pid }).then(function (j) {
        if (!j || j.code !== 0) {
          var why = 'goods.info.get[' + key + '] code=' + (j && j.code);
          if (trace) trace.push('   ' + why);
          return { cols: [], why: why };
        }
        var cols = extractColumns(j.data, pid, 0);
        if (!cols.length) {
          var w2 = 'goods.info.get[' + key + '] 里没找到专栏';
          if (trace) trace.push('   ' + w2);
          return { cols: [], why: w2 };
        }
        return { cols: cols, why: 'goods.info.get[' + key + '] 命中 ' + cols.length + ' 个专栏' };
      }).catch(function (e) {
        var w3 = 'goods.info.get[' + key + '] 异常: ' + e.message;
        if (trace) trace.push('   ' + w3);
        return { cols: [], why: w3 };
      }).then(function (r) {
        if (r.cols.length) return r;
        return step();
      });
    }
    return step();
  }

  /* 兜底：resource.available.get 的 other_list */
  function findChildColumnsByAvail(pid) {
    return api('/xe.course.business_go.resource.available.get/2.0.0',
               { resource_id: pid }).then(function (j) {
      if (!j || j.code !== 0) {
        return { cols: [], why: 'resource.available.get code=' + (j && j.code) };
      }
      var cols = extractColumns(j.data, pid, 0);
      return { cols: cols,
               why: cols.length ? 'resource.available.get 命中 ' + cols.length
                                : 'resource.available.get 里没找到专栏' };
    }).catch(function (e) {
      return { cols: [], why: 'resource.available.get 异常: ' + e.message };
    });
  }

  /* ------------------------------------------------------------------ */
  /* 店铺商品清单（哪些课可下）                                           */
  /* ------------------------------------------------------------------ */

  function storeGoods(categoryGoodsId, microPageId) {
    var body = {
      app_id: (location.hostname.split('.')[0] || ''),
      user_id: '', agent_type: 1, agent_version: 1, is_offset: 1,
      app_version: '0.1',
      agent: navigator.userAgent,
      buz_data: {
        channel_id: '', micro_page_id: microPageId || '', agent_mark: '',
        lang: 'zh', client_type: 1, page_size: 20, page_num: 1,
        category_goods_id: categoryGoodsId,
        resource_count: 5, component_id: 40185813, app_type: 1
      }
    };
    return postJson(EP.goodsList, body).then(function (j) {
      if (!j || j.code !== 0) {
        return { ok: false, list: [], why: 'code=' + (j && j.code) +
                ' msg=' + ((j && j.msg) || '') };
      }
      var list = (j.data || {}).list || [];
      return { ok: true, list: list, why: list.length + ' 个商品' };
    });
  }

  /* ------------------------------------------------------------------ */
  /* 课时 -> 播放地址（需要登录态）                                       */
  /* ------------------------------------------------------------------ */

  function resolveLookback(rid) {
    return api(EP.lookback + encodeURIComponent(rid), null, 'GET').then(function (j) {
      var data = (j && j.data) || [];
      if (!data.length) {
        return { url: '', why: 'code=' + (j && j.code) + '（11302=需要登录）' };
      }
      var pick = null, i;
      for (i = 0; i < data.length; i++) { if (data[i].default) { pick = data[i]; break; } }
      if (!pick) pick = data[data.length - 1];
      var sh = pick.line_sharpness || [];
      for (i = 0; i < sh.length; i++) if (sh[i].url) return { url: sh[i].url, why: 'ok' };
      return { url: '', why: '没有 line_sharpness' };
    }).catch(function (e) { return { url: '', why: '异常: ' + e.message }; });
  }

  function resolveVod(rid) {
    return api(EP.vod, { resource_id: rid }).then(function (j) {
      var zj = (((j || {}).data || {}).cloud_data || {}).zj || {};
      var u = zj.encrypt_url || zj.url || '';
      return { url: u, why: u ? 'ok' : 'code=' + (j && j.code) };
    }).catch(function (e) { return { url: '', why: '异常: ' + e.message }; });
  }

  function resolveWare(rid) {
    return api(EP.ware, { resource_id: rid }).then(function (j) {
      var d = (j && j.data) || [];
      return { files: Object.prototype.toString.call(d) === '[object Array]' ? d : [],
               why: j && j.code === 0 ? 'ok' : 'code=' + (j && j.code) };
    }).catch(function (e) { return { files: [], why: '异常: ' + e.message }; });
  }

  /* ------------------------------------------------------------------ */
  /* 采集主流程                                                           */
  /* ------------------------------------------------------------------ */

  function pickTitle(it) {
    return String(it.resource_title || it.title || it.name || '').trim();
  }

  /* opts:
   *   url        要采集的页面（默认当前页）
   *   columnIds  手动指定专栏 id 数组（逗号分隔或数组）
   *   withUrls   是否解析播放地址（需要登录态；匿名页面会自动失败并说明）
   *   withWare   是否拉课件附件（免登录）
   */
  function collect(opts) {
    opts = opts || {};
    var url = opts.url || location.href;
    var parsed = parseUrl(url);
    var trace = [];
    var lessons = [];
    var ware = [];
    var colsUsed = {};
    var colNames = {};

    function pushItems(items, colPid) {
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        var rid = it.resource_id || '';
        if (!rid) continue;
        lessons.push({
          rid: rid,
          title: pickTitle(it),
          rtype: parseInt(it.resource_type || 0, 10),
          start_at: it.start_at || '',
          product_id: colPid || '',
          playlist_url: '',
          vod_url: ''
        });
      }
    }

    function loadColumn(pid, label) {
      return columnAll(pid).then(function (r) {
        trace.push('   ' + label + ' column.items.get(' + pid + ') -> ' + r.why);
        if (r.ok) {
          colsUsed[pid] = true;
          pushItems(r.items, pid);
        }
        return r;
      });
    }

    function nameColumn(pid, name) {
      if (pid && name) colNames[pid] = String(name);
    }

    /* --- S1：URL 里的 id 直接当专栏查 --- */
    return columnAll(parsed.pid).then(function (r) {
      trace.push('S1 column.items.get(' + parsed.pid + ') -> ' + r.why);
      if (r.ok) { colsUsed[parsed.pid] = true; pushItems(r.items, parsed.pid); return null; }

      /* --- S2：大课包 -> 子专栏 ---
       * 顺序：topic.items.get（实测能拿全）→ goods.info.get → other_list */
      if (parsed.kind === 'program') {
        return findTopicColumns(parsed.pid, trace).then(function (t) {
          trace.push('S2 ' + t.why);
          if (t.cols.length) return t.cols;
          return findChildColumns(parsed.pid, trace).then(function (g) {
            trace.push('S2b ' + g.why);
            if (!g.cols.length) {
              return findChildColumnsByAvail(parsed.pid).then(function (a) {
                trace.push('S2c ' + a.why);
                return a.cols;
              });
            }
            return g.cols;
          });
        }).then(function (cols) {
          if (!cols.length) return null;
          var chain = Promise.resolve();
          cols.forEach(function (c) {
            nameColumn(c.product_id, c.name);
            chain = chain.then(function () {
              return loadColumn(c.product_id, 'S2 ' + (c.name || ''));
            });
          });
          return chain;
        });
      }
      return null;
    }).then(function () {
      /* --- S3：手动指定的专栏 id ---
       * 必须【总是】采集，不能只在前面全失败时才跑：
       * 否则「URL 里那一个专栏 + 另外几个手动指定的专栏」只会拿到第一个，
       * 其余的被静默跳过（实测踩到过：5 个专栏只拿到 1 个的 7 个课时）。 */
      var extra = (opts.columnIds || []).filter(function (cid) {
        return !colsUsed[cid] && cid !== parsed.pid;
      });
      if (extra.length) {
        trace.push('S3 手动指定 ' + extra.length + ' 个专栏 id');
        var chain = Promise.resolve();
        extra.forEach(function (cid) {
          chain = chain.then(function () { return loadColumn(cid, 'S3'); });
        });
        return chain;
      }
      return null;
    }).then(function () {
      if (!lessons.length) {
        var err = new Error(
          '没读到课时。已尝试：\n  ' + trace.join('\n  ') +
          '\n提示：大课包页需要登录才能看到子专栏；' +
          '也可以在扩展弹窗里手动指定专栏 id。');
        err.trace = trace;
        throw err;
      }

      /* 去重：同一节课可能同时挂在多个专栏下（实测：四川·分省讲座
         同时出现在第一期/第二期分省讲座里）。只下一遍是对的，
         但要把额外的专栏记下来，别静默吞掉。 */
      var seen = {}, uniq = [];
      lessons.forEach(function (l) {
        if (!seen[l.rid]) {
          seen[l.rid] = 1;
          uniq.push(l);
          return;
        }
        var first = uniq.filter(function (x) { return x.rid === l.rid; })[0];
        if (first) {
          first.also_in = first.also_in || [];
          if (l.product_id && first.product_id && l.product_id !== first.product_id &&
              first.also_in.indexOf(l.product_id) < 0) {
            first.also_in.push(l.product_id);
          }
        }
      });
      lessons = uniq;
      var dupCount = lessons.filter(function (l) { return l.also_in && l.also_in.length; }).length;
      if (dupCount) {
        trace.push('有 ' + dupCount + ' 节课同时挂在多个专栏下（只下了一遍）');
      }

      var withUrls = opts.withUrls !== false;
      var withWare = opts.withWare !== false;

      /* 逐个课时解析播放地址 + 课件。
       *
       * 实测（2026-11-19）：原来是 chain = chain.then(...) 串行跑，192 节课
       * 单节 1~2 秒 —— 全量超过 4 分钟。用户点「读取目录」后四分钟没动静，
       * 会以为程序卡死。改成并发 5 的任务池：同一批请求实测从没触发过
       * 11302 风控，再高没验证过所以不冒险。
       *
       * 单节失败仍然不中断（记 why 继续），trace 行先按课时序号收集，
       * 最后统一排序，保证并发下日志顺序仍然可读。
       */
      var CONCURRENCY = 5;
      var lineOf = {};              // i -> 该课时的 trace 行

      function reportProgress(done, total) {
        try {
          postLocal('/api/bridge/progress', {
            done: done, total: total, step: '解析播放地址'
          }).catch(function () {});
        } catch (e) { /* 本地服务没开也不影响采集 */ }
      }

      function resolveOne(l, i) {
        var tag = (i + 1) + '/' + lessons.length;
        var lines = [];
        function flush() { lineOf[i] = lines.join('\n'); }

        if (l.rtype === 4) {
          if (!withUrls) { return null; }
          return resolveLookback(l.rid).then(function (r) {
            l.playlist_url = r.url;
            lines.push('   ' + tag + ' 回放 ' + l.title + (r.url ? ' OK' : ' 取不到：' + r.why));
            flush();
          }).catch(function (e) {
            lines.push('   ' + tag + ' 回放 ' + l.title + ' 异常：' + (e && e.message));
            flush();
          });
        }
        if (l.rtype === 3) {
          if (!withUrls) { return null; }
          return resolveVod(l.rid).then(function (r) {
            l.vod_url = r.url;
            lines.push('   ' + tag + ' 录播 ' + l.title + (r.url ? ' OK' : ' 取不到：' + r.why));
            flush();
          }).catch(function (e) {
            lines.push('   ' + tag + ' 录播 ' + l.title + ' 异常：' + (e && e.message));
            flush();
          });
        }
        if (!withWare) { return null; }
        return resolveWare(l.rid).then(function (r) {
          if (r.files.length) {
            ware.push({ rid: l.rid, title: l.title, files: r.files });
            lines.push('   ' + tag + ' 资料 ' + l.title + ' ' + r.files.length + ' 个附件');
            flush();
          }
        }).catch(function (e) {
          lines.push('   ' + tag + ' 资料 ' + l.title + ' 异常：' + (e && e.message));
          flush();
        });
      }

      function runLimited(items, limit, fn, onDone) {
        var i = 0, done = 0;
        function step() {
          if (i >= items.length) return Promise.resolve();
          var k = i++;
          return Promise.resolve(fn(items[k], k)).then(function () {
            done++;
            if (onDone) onDone(done, items.length, k);
            return step();
          }, function () {
            /* fn 内部已吞异常；万一 shell 层还抛，也不能让整池停住 */
            done++;
            if (onDone) onDone(done, items.length, k);
            return step();
          });
        }
        var seed = [];
        for (var s = 0; s < Math.min(limit, items.length); s++) seed.push(step());
        return Promise.all(seed);
      }

      return runLimited(lessons, CONCURRENCY, resolveOne, function (done, total) {
        if (done === total || done % 10 === 0) reportProgress(done, total);
        if (done % 10 === 0) log('解析播放地址 ' + done + '/' + total);
      }).then(function () {
        for (var li = 0; li < lessons.length; li++) {
          if (lineOf[li]) trace.push(lineOf[li]);
        }
        return null;
      }).then(function () {
        /* 专栏清单带名字：服务端要靠它把 187 节课分到 14 个专栏目录里。
           没有名字的（比如 S1/S3 手动指定的）先用 id 占位，前端仍能分组。 */
        var colList = [];
        var keys = Object.keys(colsUsed);
        for (var ci = 0; ci < keys.length; ci++) {
          var cpid = keys[ci];
          colList.push({ product_id: cpid, name: colNames[cpid] || cpid });
        }
        return {
          ok: true,
          trace: trace,
          course: {
            title: (document.title || parsed.pid).replace(/\s*[-—|｜].*$/, '').trim() || parsed.pid,
            product_id: parsed.pid,
            source_url: url,
            host: ORIGIN,
            app_id: parsed.appId,
            columns: colList,
            lessons: lessons,
            courseware: ware
          }
        };
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* 输出                                                                 */
  /* ------------------------------------------------------------------ */

  function postLocal(path, body, base) {
    return fetch((base || LOCAL) + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  function log() {
    var a = Array.prototype.slice.call(arguments);
    if (typeof console !== 'undefined') {
      console.log('%c[xetdl]', 'color:#4f8cff;font-weight:700', a.join(' '));
    }
  }

  /* ------------------------------------------------------------------ */
  /* 两种运行模式                                                         */
  /* ------------------------------------------------------------------ */

  if (IS_CHROME) {
    /* 模式一：扩展内容脚本 —— 等 popup 来要 */
    chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
      if (!msg || msg.action !== 'collect') return;
      collect({
        url: msg.url,
        columnIds: msg.columnIds || [],
        withUrls: msg.withUrls !== false,
        withWare: msg.withWare !== false
      })
        .then(function (r) { sendResponse({ ok: true, course: r.course, trace: r.trace }); })
        .catch(function (e) {
          sendResponse({ ok: false, error: String(e && e.message ? e.message : e),
                         trace: e.trace || [] });
        });
      return true;
    });
    log('内容脚本就绪');
  } else {
    /* 模式二：控制台桥 —— 立即开始 */
    var params = new URLSearchParams(location.search);
    var explicit = params.get('url') || '';
    var ids = (params.get('column_ids') || '').split(',')
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return /^[a-z]_[0-9a-z]+$/i.test(s); });

    if (!explicit) {
      try {
        var src = document.currentScript && document.currentScript.src;
        if (src) explicit = new URL(src).searchParams.get('url') || '';
      } catch (e) { }
    }
    if (!explicit) explicit = location.href;

    log('开始采集：', explicit);
    collect({ url: explicit, columnIds: ids })
      .then(function (r) {
        log('读到 ' + r.course.lessons.length + ' 个课时');
        return postLocal('/api/courses', { course: r.course }).then(function (res) {
          if (res && res.ok) {
            log('✔ 已同步到本地下载器：' + res.lessons + ' 个课时');
            log('  现在打开 ' + LOCAL + '/ 勾选要下的课时，点「开始下载」。');
          } else {
            log('✘ 同步失败：', JSON.stringify(res));
          }
        });
      })
      .catch(function (e) {
        log('✘ 出错了：', e && e.message ? e.message : e);
        if (e && e.trace) { e.trace.forEach(function (t) { log('   ' + t); }); }
      });
  }

  /* 暴露给扩展 popup 用 */
  if (typeof window !== 'undefined') {
    window.__xetdl = { collect: collect, parseUrl: parseUrl, columnAll: columnAll,
                       extractColumns: extractColumns, goodsInfo: goodsInfo,
                       storeGoods: storeGoods, EP: EP };
  }
})();
