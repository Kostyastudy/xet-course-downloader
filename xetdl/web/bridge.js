/* ============================================================
 * 小鹅通课程下载器 —— 控制台桥（无需安装扩展的备选方案）
 *
 * 用法：在你已登录的小鹅通页面按 F12 → 控制台，粘贴执行：
 *     fetch('http://127.0.0.1:8777/bridge.js').then(r=>r.text()).then(eval)
 *
 * 可选：显式指定课程链接
 *     fetch('http://127.0.0.1:8777/bridge.js?url=<编码后的课程链接>')
 *         .then(r=>r.text()).then(eval)
 *
 * 它做三件事：
 *   1. 用页面自身的登录态读取课程目录
 *   2. 为每个视频课时取回带签名的播放列表地址
 *   3. 把整份计划 POST 给本地下载器
 *
 * 全程不读取、不外传你的 Cookie —— 用的是浏览器本来就带上的登录态。
 * ============================================================ */
(function () {
  'use strict';

  var LOCAL = 'http://127.0.0.1:8777';
  var ORIGIN = location.origin;
  var EP = {
    items: '/xe.course.business_go.column.items.get/2.0.0',
    ware: '/xe.course.business_go.courseware_list.get/2.0.0',
    vod: '/xe.course.business_go.video.mutli_line/1.0.0',
    lookback: '/_alive/v3/get_lookback_list?alive_id='
  };

  function log() {
    var a = Array.prototype.slice.call(arguments);
    console.log('%c[xetdl]', 'color:#4f8cff;font-weight:700', a.join(' '));
  }

  function post(path, body) {
    return fetch(LOCAL + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  function form(params) {
    var s = [];
    for (var k in params) {
      s.push(encodeURIComponent('bizData[' + k + ']') + '=' + encodeURIComponent(params[k]));
    }
    return s.join('&');
  }

  function api(path, params, method) {
    var opt = { method: method || 'POST', credentials: 'include',
                headers: { 'Accept': 'application/json, text/plain, */*' } };
    if (params) {
      opt.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      opt.body = form(params);
    }
    return fetch(ORIGIN + path, opt).then(function (r) { return r.text(); })
      .then(function (t) { try { return JSON.parse(t); } catch (e) { return { code: -1, raw: t }; } });
  }

  function pickProductId(explicit) {
    if (explicit) {
      var m1 = explicit.match(/\/p\/course\/(?:big_)?column\/([a-z]_[0-9a-z]+)/i);
      if (m1) return m1[1];
      var m2 = explicit.match(/[?&]product_id=([a-z]_[0-9a-z]+)/i);
      if (m2) return m2[1];
    }
    var m3 = location.href.match(/\/p\/course\/(?:big_)?column\/([a-z]_[0-9a-z]+)/i);
    if (m3) return m3[1];
    var m4 = location.href.match(/[?&]product_id=([a-z]_[0-9a-z]+)/i);
    if (m4) return m4[1];
    // 页面上找课程链接
    var links = document.querySelectorAll('a[href*="/p/course/column/"], a[href*="product_id="]');
    for (var i = 0; i < links.length; i++) {
      var h = links[i].getAttribute('href') || '';
      var m5 = h.match(/\/p\/course\/(?:big_)?column\/([a-z]_[0-9a-z]+)/i);
      if (m5) return m5[1];
    }
    return null;
  }

  function resolveLookback(rid) {
    return api(EP.lookback + encodeURIComponent(rid), null, 'GET').then(function (j) {
      var data = j.data || [];
      var pick = null;
      for (var i = 0; i < data.length; i++) { if (data[i].default) { pick = data[i]; break; } }
      if (!pick && data.length) pick = data[data.length - 1];
      if (!pick) return null;
      var sh = pick.line_sharpness || [];
      for (var k = 0; k < sh.length; k++) if (sh[k].url) return sh[k].url;
      return null;
    });
  }

  function resolveVod(rid) {
    return api(EP.vod, { resource_id: rid }, 'POST').then(function (j) {
      var cd = (j.data || {}).cloud_data || {};
      var zj = cd.zj || {};
      return zj.encrypt_url || zj.url || null;
    });
  }

  function resolveWare(rid) {
    return api(EP.ware, { resource_id: rid }, 'POST').then(function (j) {
      return j.data || [];
    });
  }

  var run = async function () {
    var params = new URLSearchParams(location.search);
    var explicit = null;
    try {
      var src = document.currentScript && document.currentScript.src;
      if (src) explicit = new URL(src).searchParams.get('url');
    } catch (e) { }
    if (!explicit) explicit = params.get('url');

    var pid = pickProductId(explicit);
    if (!pid) {
      var manual = prompt('请粘贴课程 / 专栏链接（形如 .../p/course/column/p_xxx）：');
      if (!manual) { log('没拿到课程链接，已退出。'); return; }
      pid = pickProductId(manual);
    }
    if (!pid) { log('还是没能从链接里解析出 product_id，请确认是课程页地址。'); return; }
    log('课程 product_id =', pid);

    var j = await api(EP.items, { column_id: pid, page_index: 1, page_size: 200, sort: 'asc' });
    var items = (j.data || {}).list || [];
    if (!items.length) { log('没读到课时列表（code=' + j.code + '）。确认已登录且课程可访问。'); return; }
    log('读到', items.length, '个课时，开始解析播放地址…');

    var lessons = [];
    var ware = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var rt = parseInt(it.resource_type || 0, 10);
      var rid = it.resource_id || '';
      var lesson = {
        rid: rid,
        title: (it.resource_title || '').trim(),
        rtype: rt,
        start_at: it.start_at || '',
        product_id: pid,
        playlist_url: '',
        vod_url: ''
      };
      if (rt === 4) {
        lesson.playlist_url = await resolveLookback(rid) || '';
        log('  ' + (i + 1) + '/' + items.length, '回放', lesson.title,
            lesson.playlist_url ? 'OK' : '取不到地址');
      } else if (rt === 3) {
        lesson.vod_url = await resolveVod(rid) || '';
        log('  ' + (i + 1) + '/' + items.length, '录播', lesson.title,
            lesson.vod_url ? 'OK' : '取不到地址');
      } else {
        var files = await resolveWare(rid);
        if (files.length) {
          ware.push({ rid: rid, title: lesson.title, files: files });
          log('  ' + (i + 1) + '/' + items.length, '资料', lesson.title, files.length + ' 个附件');
        }
      }
      lessons.push(lesson);
    }

    var course = {
      title: document.title ? document.title.replace(/\s*[-—|].*$/, '').trim() : pid,
      product_id: pid,
      source_url: location.href,
      host: ORIGIN,
      app_id: (location.hostname.split('.')[0]) || '',
      lessons: lessons,
      courseware: ware
    };
    var res = await post('/api/courses', { course: course });
    if (res && res.ok) {
      log('✔ 已同步到本地下载器：' + res.lessons + ' 个课时');
      log('  现在打开 ' + LOCAL + '/ 勾选要下的课时，点「开始下载」。');
    } else {
      log('✘ 同步失败：', JSON.stringify(res));
    }
  };

  run().catch(function (e) { log('出错了：', e && e.message ? e.message : e); });
})();
