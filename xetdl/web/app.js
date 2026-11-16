/* 小鹅通课程下载器 —— 本地控制台 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var state = { course: null, selected: {}, jobs: {} };

  function api(path, opts) {
    return fetch(path, opts).then(function (r) { return r.json(); });
  }

  function post(path, body) {
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  function human(n) {
    n = n || 0;
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(i ? 1 : 0) + ' ' + u[i];
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* ---------------- 初始化 ---------------- */
  function init() {
    api('/api/health').then(function (h) {
      $('ver').textContent = 'v' + (h.version || '?');
      var f = $('ffmpeg');
      f.textContent = h.ffmpeg ? 'ffmpeg 已就绪' : '⚠ 未找到 ffmpeg';
      f.className = 'pill ' + (h.ffmpeg ? 'ok' : 'err');
      if (h.legal) $('legalText').textContent = h.legal;
    });
    api('/api/defaults').then(function (d) {
      $('outDir').value = d.out || '';
    });
    var base = location.origin;
    $('bridgeBase').value = base;
    $('bridgeSnippet').textContent =
      "fetch('" + base + "/bridge.js').then(r=>r.text()).then(eval)";
    refreshCourses();
    setInterval(refreshJobs, 1500);
  }

  $('copySnippet').onclick = function () {
    var t = $('bridgeSnippet').textContent;
    navigator.clipboard.writeText(t).then(function () {
      $('bridgeState').textContent = '已复制';
      setTimeout(function () { $('bridgeState').textContent = '未连接'; }, 1500);
    });
  };

  /* ---------------- 课程目录 ---------------- */
  function refreshCourses() {
    api('/api/courses').then(function (c) {
      if (c && c.lessons && c.lessons.length) {
        state.course = c;
        renderLessons();
        $('bridgeState').textContent = '已同步 ' + c.lessons.length + ' 个课时';
        $('bridgeState').className = 'pill ok';
      }
    });
  }

  $('btnParse').onclick = function () {
    var url = $('courseUrl').value.trim();
    $('parseMsg').className = 'msg';
    if (!url) {
      $('parseMsg').textContent = '先粘贴课程链接，再点扩展图标里的「同步到下载器」。';
      $('parseMsg').className = 'msg err';
      return;
    }
    $('parseMsg').textContent = '正在读取本地已同步的目录…';
    api('/api/courses').then(function (c) {
      if (c && c.lessons && c.lessons.length) {
        state.course = c;
        renderLessons();
        $('parseMsg').textContent = '已加载：' + c.title;
        $('parseMsg').className = 'msg ok';
      } else {
        $('parseMsg').textContent =
          '本地还没有课程目录。点浏览器工具栏里的扩展图标 → 粘贴上面的链接 → 「同步到下载器」。';
        $('parseMsg').className = 'msg err';
      }
    });
  };

  function renderLessons() {
    var c = state.course;
    var box = $('lessons');
    box.innerHTML = '';
    c.lessons.forEach(function (l) {
      var row = document.createElement('div');
      row.className = 'lrow';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!state.selected[l.rid];
      cb.onchange = function () { state.selected[l.rid] = cb.checked; };
      var t = document.createElement('span');
      t.className = 't';
      t.textContent = l.title;
      var k = document.createElement('span');
      k.className = 'k';
      k.textContent = { 4: '直播回放', 3: '录播', 1: '资料' }[l.rtype] || '其他';
      var d = document.createElement('span');
      d.className = 'd';
      d.textContent = l.start_at || '';
      row.appendChild(cb); row.appendChild(t); row.appendChild(k); row.appendChild(d);
      box.appendChild(row);
    });
    $('lessonBox').classList.remove('hidden');
    updateCount();
  }

  function updateCount() {
    var n = Object.keys(state.selected).filter(function (k) { return state.selected[k]; }).length;
    $('lessonCount').textContent = '已选 ' + n + ' / ' + (state.course ? state.course.lessons.length : 0);
  }

  $('btnAll').onclick = function () {
    state.course.lessons.forEach(function (l) { state.selected[l.rid] = true; });
    renderLessons();
  };
  $('btnNone').onclick = function () {
    state.selected = {};
    renderLessons();
  };

  $('btnStart').onclick = function () {
    var c = state.course;
    if (!c) { $('parseMsg').textContent = '还没有课程目录。'; $('parseMsg').className = 'msg err'; return; }
    var picked = c.lessons.filter(function (l) { return state.selected[l.rid]; });
    if (!picked.length) { $('parseMsg').textContent = '至少选一个课时。'; $('parseMsg').className = 'msg err'; return; }
    var plan = JSON.parse(JSON.stringify(c));
    plan.lessons = picked;
    var cws = (c.courseware || []).filter(function (w) {
      return picked.some(function (l) { return l.rid === w.rid; }) ||
             w.files.some(function () { return true; });
    });
    plan.courseware = cws;
    post('/api/jobs', { course: plan, out_dir: $('outDir').value }).then(function (j) {
      if (j.error) { $('parseMsg').textContent = '启动失败: ' + j.error; $('parseMsg').className = 'msg err'; }
      else { $('parseMsg').textContent = '已启动任务 ' + j.id; $('parseMsg').className = 'msg ok'; }
      refreshJobs();
    });
  };

  /* ---------------- 任务 ---------------- */
  function refreshJobs() {
    api('/api/jobs').then(function (r) {
      var box = $('jobs');
      if (!r.jobs || !r.jobs.length) { box.innerHTML = '<p class="muted">暂无任务。</p>'; return; }
      box.innerHTML = '';
      r.jobs.slice().reverse().forEach(function (j) {
        var total = j.total || 1;
        var pct = Math.min(100, Math.round(100 * (j.done + j.failed) / total));
        var el = document.createElement('div');
        el.className = 'job';
        var h = document.createElement('h4');
        h.textContent = j.title;
        var bar = document.createElement('div');
        bar.className = 'bar' + (j.state === 'done' ? ' done' : (j.state === 'failed' ? ' failed' : ''));
        var fill = document.createElement('i');
        fill.style.width = pct + '%';
        bar.appendChild(fill);
        var meta = document.createElement('div');
        meta.className = 'jmeta';
        meta.innerHTML =
          '<span>状态 ' + esc(j.state) + '</span>' +
          '<span>完成 ' + j.done + ' / ' + total + '</span>' +
          (j.failed ? '<span>失败 ' + j.failed + '</span>' : '') +
          '<span>目录 ' + esc(j.out_dir) + '</span>';
        el.appendChild(h); el.appendChild(bar); el.appendChild(meta);
        var btn = document.createElement('button');
        btn.className = 'ghost';
        btn.textContent = '打开目录';
        btn.style.marginTop = '8px';
        btn.onclick = function () { post('/api/open', { path: j.out_dir }); };
        el.appendChild(btn);
        if (j.logs && j.logs.length) {
          var lg = document.createElement('div');
          lg.className = 'logs';
          lg.textContent = j.logs.join('\n');
          el.appendChild(lg);
        }
        box.appendChild(el);
      });
    });
  }

  init();
})();
