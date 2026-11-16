/* 小鹅通课程下载器 —— 扩展弹窗
 *
 * 职责：确认本地服务在线 → 在当前已登录页面里采集目录 → 把计划交给本地下载器。
 * 扩展自身不搬运任何视频字节，也不保存任何账号信息。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var state = { course: null, selected: {}, busy: false };

  function base() {
    return ($('base').value || 'http://127.0.0.1:8777').replace(/\/+$/, '');
  }

  function api(path, opts) {
    return fetch(base() + path, opts).then(function (r) { return r.json(); });
  }

  function post(path, body) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  function setState(txt, cls) {
    var el = $('state');
    el.textContent = txt;
    el.className = 'pill' + (cls ? ' ' + cls : '');
  }

  function setMsg(txt, cls) {
    var el = $('msg');
    el.textContent = txt || '';
    el.className = 'msg' + (cls ? ' ' + cls : '');
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* ---------------- 本地服务探测 ---------------- */
  function ping() {
    return api('/api/health').then(function (h) {
      setState('服务已连接', 'ok');
      if (h.default_out && !$('out').value) $('out').value = h.default_out;
      if (h.legal) setMsg(h.legal.split('\n')[0], '');
      return true;
    }).catch(function () {
      setState('本地服务未启动', 'err');
      setMsg('请先运行 start.py（或双击「一键启动.bat」）', 'err');
      return false;
    });
  }

  /* ---------------- 采集 ---------------- */
  function activeTab() {
    return new Promise(function (resolve) {
      chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
        resolve((tabs && tabs[0]) || null);
      });
    });
  }

  function sendCollect(tab, url) {
    return new Promise(function (resolve) {
      chrome.tabs.sendMessage(tab.id, { action: 'collect', url: url }, function (res) {
        var err = chrome.runtime.lastError;
        if (err) { resolve({ ok: false, error: err.message }); return; }
        resolve(res);
      });
    });
  }

  function injectContent(tab) {
    return new Promise(function (resolve) {
      chrome.scripting.executeScript(
        { target: { tabId: tab.id }, files: ['content.js'] },
        function () { resolve(!chrome.runtime.lastError); }
      );
    });
  }

  $('btnCollect').onclick = function () {
    if (state.busy) return;
    state.busy = true;
    $('btnCollect').disabled = true;
    setMsg('正在读取课程目录…');

    activeTab().then(function (tab) {
      if (!tab) { setMsg('拿不到当前标签页', 'err'); return null; }
      var url = $('url').value.trim();
      return sendCollect(tab, url).then(function (res) {
        if (res && res.ok) return res;
        // 页面里没有 content script（比如不是小鹅通域名）→ 注入一次再试
        return injectContent(tab).then(function (ok) {
          if (!ok) return { ok: false, error: '无法在该页面注入脚本' };
          return sendCollect(tab, url);
        });
      }).then(function (res) {
        if (!res) return;
        if (!res.ok) { setMsg((res && res.error) || '读取失败', 'err'); return; }
        state.course = res.course;
        state.selected = {};
        (res.course.lessons || []).forEach(function (l) { state.selected[l.rid] = true; });
        render();
        setMsg('读到 ' + (res.course.lessons || []).length + ' 个课时', 'ok');
      });
    }).catch(function (e) {
      setMsg(String(e && e.message ? e.message : e), 'err');
    }).then(function () {
      state.busy = false;
      $('btnCollect').disabled = false;
    });
  };

  /* ---------------- 渲染课时列表 ---------------- */
  function render() {
    var c = state.course;
    var box = $('lessons');
    box.innerHTML = '';
    (c.lessons || []).forEach(function (l) {
      var row = document.createElement('div');
      row.className = 'lrow';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!state.selected[l.rid];
      cb.onchange = function () { state.selected[l.rid] = cb.checked; count(); };
      var t = document.createElement('span');
      t.className = 't';
      t.textContent = l.title;
      var k = document.createElement('span');
      k.className = 'k';
      k.textContent = { 4: '回放', 3: '录播', 1: '资料' }[l.rtype] || '其他';
      row.appendChild(cb);
      row.appendChild(t);
      row.appendChild(k);
      box.appendChild(row);
    });
    $('lessonBox').classList.remove('hidden');
    count();
  }

  function count() {
    var n = Object.keys(state.selected).filter(function (k) { return state.selected[k]; }).length;
    $('count').textContent = '已选 ' + n + ' / ' + ((state.course && state.course.lessons.length) || 0);
  }

  $('btnAll').onclick = function () {
    if (!state.course) return;
    state.course.lessons.forEach(function (l) { state.selected[l.rid] = true; });
    render();
  };

  $('btnNone').onclick = function () {
    state.selected = {};
    render();
  };

  /* ---------------- 交给本地下载器 ---------------- */
  function buildPlan() {
    var c = state.course;
    if (!c) return null;
    var picked = c.lessons.filter(function (l) { return state.selected[l.rid]; });
    var plan = JSON.parse(JSON.stringify(c));
    plan.lessons = picked;
    plan.courseware = (c.courseware || []).filter(function (w) {
      return picked.some(function (l) { return l.rid === w.rid; });
    });
    return plan;
  }

  $('btnSync').onclick = function () {
    var plan = buildPlan();
    if (!plan) { setMsg('先点「读取目录」', 'err'); return; }
    post('/api/courses', { course: plan }).then(function (r) {
      if (r && r.ok) {
        setMsg('已同步 ' + r.lessons + ' 个课时到本地控制台', 'ok');
      } else {
        setMsg('同步失败：' + JSON.stringify(r), 'err');
      }
    }).catch(function (e) { setMsg(String(e), 'err'); });
  };

  $('btnStart').onclick = function () {
    var plan = buildPlan();
    if (!plan || !plan.lessons.length) { setMsg('至少选一个课时', 'err'); return; }
    var out = ($('out').value || '').trim();
    post('/api/jobs', { course: plan, out_dir: out }).then(function (j) {
      if (j && j.error) { setMsg('启动失败：' + j.error, 'err'); }
      else { setMsg('已开始下载（任务 ' + j.id + '）', 'ok'); }
    }).catch(function (e) { setMsg(String(e), 'err'); });
  };

  /* ---------------- 打开本地控制台 ---------------- */
  $('openConsole').onclick = function () {
    chrome.tabs.create({ url: base() + '/' });
  };

  $('base').addEventListener('change', function () { ping(); });

  ping();
  count();
})();
