/* 小鹅通课程下载器 —— 内容脚本
 *
 * 只做一件事：在**你已登录的这个页面里**调用小鹅通接口，取回课程目录与
 * 带签名的播放地址。请求走浏览器自身的登录态，脚本不读取、不存储、
 * 不外传任何 Cookie。
 */
(function () {
  'use strict';

  var EP = {
    items: '/xe.course.business_go.column.items.get/2.0.0',
    ware: '/xe.course.business_go.courseware_list.get/2.0.0',
    vod: '/xe.course.business_go.video.mutli_line/1.0.0',
    lookback: '/_alive/v3/get_lookback_list?alive_id='
  };

  function form(params) {
    var s = [];
    for (var k in params) {
      s.push(encodeURIComponent('bizData[' + k + ']') + '=' + encodeURIComponent(params[k]));
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
    return fetch(path, opt).then(function (r) { return r.text(); })
      .then(function (t) {
        try { return JSON.parse(t); } catch (e) { return { code: -1, raw: (t || '').slice(0, 300) }; }
      });
  }

  function pickProductId(explicit) {
    var cands = [explicit || '', location.href];
    for (var i = 0; i < cands.length; i++) {
      var h = cands[i];
      if (!h) continue;
      var m = h.match(/\/p\/course\/(?:big_)?column\/([a-z]_[0-9a-z]+)/i);
      if (m) return m[1];
      var m2 = h.match(/[?&]product_id=([a-z]_[0-9a-z]+)/i);
      if (m2) return m2[1];
    }
    var links = document.querySelectorAll('a[href*="/p/course/column/"], a[href*="product_id="]');
    for (var k = 0; k < links.length; k++) {
      var m3 = (links[k].getAttribute('href') || '')
        .match(/\/p\/course\/(?:big_)?column\/([a-z]_[0-9a-z]+)/i);
      if (m3) return m3[1];
    }
    return null;
  }

  function resolveLookback(rid) {
    return api(EP.lookback + encodeURIComponent(rid), null, 'GET').then(function (j) {
      var data = j.data || [];
      var pick = null;
      for (var i = 0; i < data.length; i++) { if (data[i].default) { pick = data[i]; break; } }
      if (!pick && data.length) pick = data[data.length - 1];
      if (!pick) return '';
      var sh = pick.line_sharpness || [];
      for (var k = 0; k < sh.length; k++) if (sh[k].url) return sh[k].url;
      return '';
    });
  }

  function resolveVod(rid) {
    return api(EP.vod, { resource_id: rid }, 'POST').then(function (j) {
      var zj = ((j.data || {}).cloud_data || {}).zj || {};
      return zj.encrypt_url || zj.url || '';
    });
  }

  function resolveWare(rid) {
    return api(EP.ware, { resource_id: rid }, 'POST').then(function (j) {
      return j.data || [];
    });
  }

  function collect(explicitUrl) {
    var pid = pickProductId(explicitUrl);
    if (!pid) {
      return Promise.resolve({ ok: false, error: '当前页面看不出是哪门课。请在弹窗里填写课程链接（形如 .../p/course/column/p_xxx）。' });
    }
    return api(EP.items, { column_id: pid, page_index: 1, page_size: 200, sort: 'asc' })
      .then(function (j) {
        var items = (j.data || {}).list || [];
        if (!items.length) {
          return { ok: false, error: '没读到课时列表（code=' + j.code + '）。确认已登录、且课程在「我的」里可见。' };
        }
        var lessons = [];
        var ware = [];
        var chain = Promise.resolve();
        items.forEach(function (it, idx) {
          chain = chain.then(function () {
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
              return resolveLookback(rid).then(function (u) {
                lesson.playlist_url = u || '';
                lessons.push(lesson);
              });
            }
            if (rt === 3) {
              return resolveVod(rid).then(function (u) {
                lesson.vod_url = u || '';
                lessons.push(lesson);
              });
            }
            return resolveWare(rid).then(function (files) {
              if (files.length) ware.push({ rid: rid, title: lesson.title, files: files });
              lessons.push(lesson);
            });
          });
        });
        return chain.then(function () {
          var title = (document.title || pid).replace(/\s*[-—|｜].*$/, '').trim() || pid;
          return {
            ok: true,
            course: {
              title: title,
              product_id: pid,
              source_url: location.href,
              host: location.origin,
              app_id: (location.hostname.split('.')[0]) || '',
              lessons: lessons,
              courseware: ware
            }
          };
        });
      });
  }

  chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    if (!msg || msg.action !== 'collect') return;
    collect(msg.url).then(sendResponse).catch(function (e) {
      sendResponse({ ok: false, error: String(e && e.message ? e.message : e) });
    });
    return true;   // 异步响应
  });
})();
