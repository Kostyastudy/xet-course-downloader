/* 小鹅通课程下载器 —— 本地控制台 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var state = {
    course: null,
    selected: {},        // rid -> true
    collapsed: {},       // product_id -> true（折叠）
    est: null,           // 最近一次估算快照 {results:{rid:{bytes}}}
    seq: 0
  };

  /* ---------------- 基础工具 ---------------- */
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
    if (n < 1024) return n + ' B';
    var u = ['KB', 'MB', 'GB', 'TB'], i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return n.toFixed(n >= 100 ? 0 : 1) + ' ' + u[i];
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var KIND = { 4: '直播回放', 3: '录播', 1: '资料', 2: '图文' };

  /* ---------------- 初始化 ---------------- */
  function init() {
    var base = location.origin;

    api('/api/health').then(function (h) {
      $('ver').textContent = 'v' + (h.version || '?');
      var f = $('ffmpeg');
      f.textContent = h.ffmpeg ? 'ffmpeg 就绪' : '⚠ 缺少 ffmpeg';
      f.className = 'pill ' + (h.ffmpeg ? 'ok' : 'err');
      if (h.legal) $('legalText').textContent = h.legal.replace(/\n/g, ' ');
      if (h.settings_path) $('settingsPath').textContent = h.settings_path;
    });

    api('/api/defaults').then(function (d) {
      $('outDir').value = d.out || '';
      $('jobsInput').value = d.jobs || 2;
      $('threadsInput').value = d.threads || 10;
      $('cfgHost').value = d.host || '';
      $('cfgPort').value = d.port || 8777;
      $('cfgWorkers').value = d.workers || 4;
      $('cfgSample').value = d.sample || 8;
      if (d.path) $('settingsPath').textContent = d.path;
    });
    api('/api/settings').then(function (r) {
      var s = (r && r.settings) || {};
      if (s.sizing_workers) $('cfgWorkers').value = s.sizing_workers;
      if (s.sizing_sample) $('cfgSample').value = s.sizing_sample;
    });

    $('bridgeSnippet').textContent =
      "fetch('" + base + "/bridge.js').then(r=>r.text()).then(eval)";

    refreshCourses();
    refreshDisk();
    setInterval(refreshJobs, 1500);
    setInterval(pollEstimate, 1200);
  }

  $('copySnippet').onclick = function () {
    copy($('bridgeSnippet').textContent);
  };
  $('copySnippetBig').onclick = function () {
    // 进阶版：把 column_ids 留空但显式指定大课包页，让 content.js 走 topic.items.get
    var base = location.origin;
    var tip = prompt(
      '粘贴大课包（或专栏）页面链接。\n留空则取当前页面。\n\n' +
      '进阶版会自动展开整包所有子专栏。', location.href);
    if (tip === null) return;
    var u = (tip || '').trim() || location.href;
    copy("fetch('" + base + "/bridge.js?url=" + encodeURIComponent(u) +
         "').then(r=>r.text()).then(eval)");
  };

  function copy(t) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(t).then(function () { flash('已复制'); },
        function () { flash('复制失败，请手动选中'); });
    } else {
      flash('请手动选中复制');
    }
  }
  function flash(msg) {
    $('bridgeState').textContent = msg;
    $('bridgeState').className = 'pill ok';
    setTimeout(refreshBridgeState, 1800);
  }
  function refreshBridgeState() {
    if (state.course) {
      $('bridgeState').textContent = '已同步 ' + state.course.lessons.length + ' 节';
      $('bridgeState').className = 'pill ok';
    } else {
      $('bridgeState').textContent = '未同步';
      $('bridgeState').className = 'pill muted';
    }
  }

  /* ---------------- 磁盘 ---------------- */
  function refreshDisk() {
    var p = ($('outDir').value || '').trim();
    api('/api/disk?path=' + encodeURIComponent(p)).then(function (d) {
      var el = $('diskStat');
      if (d && d.ok) {
        el.textContent = '磁盘剩 ' + human(d.free);
        el.className = 'pill' + (d.free < 5 * 1024 * 1024 * 1024 ? ' err' : ' muted');
      } else {
        el.textContent = '磁盘 —';
      }
    }).catch(function () {});
  }
  $('outDir').addEventListener('change', refreshDisk);
  $('btnPickDefault').onclick = function () {
    api('/api/defaults').then(function (d) {
      $('outDir').value = d.out || '';
      refreshDisk();
    });
  };

  /* ---------------- 课程目录 ---------------- */
  function refreshCourses() {
    api('/api/courses').then(function (c) {
      if (!c || !c.lessons || !c.lessons.length) { refreshBridgeState(); return; }
      if (c.seq && c.seq === state.seq) return;      // 没变化，别重绘（会清掉勾选）
      state.seq = c.seq || 0;
      state.course = c;
      state.selected = {};
      c.lessons.forEach(function (l) { state.selected[l.rid] = true; });
      render();
      refreshBridgeState();
    });
  }

  $('btnReload').onclick = function () { state.seq = -1; refreshCourses(); };

  /* ---------------- 渲染 ---------------- */
  function filteredLessons() {
    var c = state.course;
    if (!c) return [];
    var q = ($('q').value || '').trim().toLowerCase();
    var t = $('typeFilter').value;
    return c.lessons.filter(function (l) {
      if (t && String(l.rtype) !== t) return false;
      if (!q) return true;
      var hay = (l.title || '') + ' ' + (l.group_name || '');
      return hay.toLowerCase().indexOf(q) >= 0;
    });
  }

  function groupsOf(list) {
    var out = [], idx = {};
    list.forEach(function (l) {
      var gid = l.group || '';
      if (!(gid in idx)) {
        idx[gid] = out.length;
        out.push({ id: gid, name: l.group_name || '', items: [] });
      }
      out[idx[gid]].items.push(l);
    });
    return out;
  }

  function render() {
    var c = state.course;
    if (!c) return;
    $('noCourse').classList.add('hidden');
    $('courseBox').classList.remove('hidden');

    $('courseTitle').textContent = c.title || '（未命名课程）';
    var videos = c.lessons.filter(function (l) { return l.rtype === 3 || l.rtype === 4; }).length;
    var texts = c.lessons.length - videos;
    var colCount = (c.columns || []).length;
    $('courseMeta').textContent =
      c.lessons.length + ' 节课 · ' + videos + ' 个视频 · ' + texts + ' 份资料' +
      (colCount > 1 ? ' · ' + colCount + ' 个专栏' : '');

    var box = $('columns');
    box.innerHTML = '';
    var list = filteredLessons();
    var groups = groupsOf(list);
    var multi = groups.length > 1 || (groups[0] && groups[0].id);

    if (!list.length) {
      box.innerHTML = '<p class="muted" style="padding:12px">没有匹配的课时。</p>';
      updateStats();
      return;
    }

    groups.forEach(function (g) {
      var sec = document.createElement('div');
      sec.className = 'col';

      if (multi) {
        var head = document.createElement('div');
        head.className = 'colhead';
        var arrow = document.createElement('span');
        arrow.className = 'arrow';
        arrow.textContent = state.collapsed[g.id] ? '▸' : '▾';
        var name = document.createElement('span');
        name.className = 'colname';
        name.textContent = g.name || '（未分组）';
        var pickedN = g.items.filter(function (l) { return state.selected[l.rid]; }).length;
        var stat = document.createElement('span');
        stat.className = 'colstat';
        stat.textContent = pickedN + ' / ' + g.items.length + ' 已选';
        var sp = document.createElement('span'); sp.className = 'spacer';
        var bAll = document.createElement('button');
        bAll.className = 'ghost tiny'; bAll.textContent = '全选本栏';
        bAll.onclick = function (e) {
          e.stopPropagation();
          g.items.forEach(function (l) { state.selected[l.rid] = true; });
          render();
        };
        var bNone = document.createElement('button');
        bNone.className = 'ghost tiny'; bNone.textContent = '清空';
        bNone.onclick = function (e) {
          e.stopPropagation();
          g.items.forEach(function (l) { state.selected[l.rid] = false; });
          render();
        };
        head.appendChild(arrow); head.appendChild(name); head.appendChild(stat);
        head.appendChild(sp); head.appendChild(bAll); head.appendChild(bNone);
        head.onclick = function () {
          state.collapsed[g.id] = !state.collapsed[g.id];
          render();
        };
        sec.appendChild(head);
      }

      var body = document.createElement('div');
      body.className = 'colbody' + ((multi && state.collapsed[g.id]) ? ' hidden' : '');
      g.items.forEach(function (l) { body.appendChild(rowFor(l)); });
      sec.appendChild(body);
      box.appendChild(sec);
    });
    updateStats();
  }

  function rowFor(l) {
    var row = document.createElement('div');
    row.className = 'lrow';

    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !!state.selected[l.rid];
    cb.onchange = function () { state.selected[l.rid] = cb.checked; updateStats(); refreshGroupStats(); };

    var t = document.createElement('span');
    t.className = 't';
    t.textContent = l.title;
    t.title = l.title;

    var k = document.createElement('span');
    k.className = 'k';
    k.textContent = KIND[l.rtype] || '其他';

    var sz = document.createElement('span');
    sz.className = 'sz';
    sz.textContent = sizeOf(l.rid);

    var d = document.createElement('span');
    d.className = 'd';
    d.textContent = (l.start_at || '').replace(/:\d\d$/, '');

    row.appendChild(cb); row.appendChild(t); row.appendChild(k);
    row.appendChild(sz); row.appendChild(d);
    return row;
  }

  function sizeOf(rid) {
    var r = state.est && state.est.results && state.est.results[rid];
    if (!r) return '';
    if (!r.ok || !r.bytes) return '?';
    return human(r.bytes);
  }

  /* 只更新专栏头部的「x / y 已选」，避免整表重绘（重绘会丢滚动位置） */
  function refreshGroupStats() {
    var heads = document.querySelectorAll('#columns .col');
    var list = filteredLessons();
    var groups = groupsOf(list);
    var multi = groups.length > 1 || (groups[0] && groups[0].id);
    if (!multi) return;
    for (var i = 0; i < heads.length && i < groups.length; i++) {
      var stat = heads[i].querySelector('.colstat');
      if (!stat) continue;
      var pickedN = groups[i].items.filter(function (l) { return state.selected[l.rid]; }).length;
      stat.textContent = pickedN + ' / ' + groups[i].items.length + ' 已选';
    }
  }

  function pickedLessons() {
    var c = state.course;
    if (!c) return [];
    return c.lessons.filter(function (l) { return state.selected[l.rid]; });
  }

  function updateStats() {
    var picked = pickedLessons();
    var videos = picked.filter(function (l) { return l.rtype === 3 || l.rtype === 4; });
    $('pickStat').textContent = '已选 ' + picked.length + ' 节（' + videos.length + ' 个视频）';

    var est = state.est;
    var bytes = 0, known = 0, unknown = 0;
    picked.forEach(function (l) {
      var r = est && est.results && est.results[l.rid];
      if (r && r.ok && r.bytes) { bytes += r.bytes; known++; }
      else if (l.rtype === 3 || l.rtype === 4) unknown++;
    });
    var el = $('sizeStat');
    if (known) {
      el.textContent = '预计 ' + human(bytes) + (unknown ? ' + ' + unknown + ' 节未估' : '');
      el.className = 'pill ok';
    } else {
      el.textContent = '预计 —';
      el.className = 'pill';
    }
  }

  /* ---------------- 筛选 / 勾选 ---------------- */
  $('q').addEventListener('input', render);
  $('typeFilter').addEventListener('change', render);

  $('btnAll').onclick = function () {
    filteredLessons().forEach(function (l) { state.selected[l.rid] = true; });
    render();
  };
  $('btnNone').onclick = function () {
    filteredLessons().forEach(function (l) { state.selected[l.rid] = false; });
    render();
  };
  $('btnInvert').onclick = function () {
    filteredLessons().forEach(function (l) { state.selected[l.rid] = !state.selected[l.rid]; });
    render();
  };

  /* ---------------- 体积估算 ---------------- */
  function startEstimate(scope) {
    var c = state.course;
    if (!c) return;
    var lessons = (scope === 'all' ? c.lessons : pickedLessons());
    var videos = lessons.filter(function (l) {
      return (l.rtype === 3 || l.rtype === 4) && (l.playlist_url || l.vod_url);
    });
    if (!videos.length) {
      $('estState').textContent = '没有带播放地址的视频可估算（资料课时不算体积）';
      return;
    }
    $('estState').textContent = '准备中…';
    $('estBar').classList.remove('hidden');
    post('/api/estimate', {
      lessons: videos.map(function (l) {
        return { rid: l.rid, title: l.title,
                 playlist_url: l.playlist_url || '', vod_url: l.vod_url || '' };
      }),
      workers: parseInt($('cfgWorkers').value, 10) || 4,
      sample: parseInt($('cfgSample').value, 10) || 8
    }).then(function (r) {
      if (r && r.error) { $('estState').textContent = r.error; return; }
      state.est = r.state || state.est;
      pollEstimate(true);
    }).catch(function (e) { $('estState').textContent = '启动失败：' + e; });
  }

  $('btnEstimate').onclick = function () { startEstimate('picked'); };
  $('btnEstimateAll').onclick = function () { startEstimate('all'); };

  var estTimer = null;
  function pollEstimate(force) {
    if (!force && (!state.est || !state.est.running)) return;
    api('/api/estimate').then(function (e) {
      if (!e) return;
      var prevDone = state.est ? state.est.done : -1;
      state.est = e;
      var pct = e.total ? Math.round(100 * e.done / e.total) : 0;
      var bar = $('estBar');
      bar.classList.remove('hidden');
      bar.firstChild.style.width = pct + '%';
      if (e.running) {
        $('estState').textContent = '估算中 ' + e.done + ' / ' + e.total +
          '（已得 ' + e.human + '）';
      } else if (e.total) {
        $('estState').textContent = '估算完成：共 ' + e.total + ' 节，合计 ' + e.human;
        updateStats();
        if (prevDone !== e.done) render();
        return;
      }
      if (e.done !== prevDone) { render(); }
    }).catch(function () {});
  }

  /* ---------------- 开始下载 ---------------- */
  $('btnStart').onclick = function () {
    var c = state.course;
    if (!c) { msg('先取一次课程目录。', true); return; }
    var picked = pickedLessons();
    if (!picked.length) { msg('至少勾一节。', true); return; }
    var out = ($('outDir').value || '').trim();
    if (!out) { msg('填一下保存目录。', true); return; }

    var needUrl = picked.filter(function (l) {
      return (l.rtype === 3 || l.rtype === 4) && !l.playlist_url && !l.vod_url;
    });
    if (needUrl.length) {
      msg('有 ' + needUrl.length + ' 个视频没拿到播放地址（多半没登录）。' +
          '请先去小鹅通登录，再重新「读取目录」。', true);
      return;
    }

    var plan = JSON.parse(JSON.stringify(c));
    plan.lessons = picked;
    var ids = {};
    picked.forEach(function (l) { ids[l.rid] = 1; });
    plan.courseware = (c.courseware || []).filter(function (w) { return ids[w.rid]; });

    var bytesPlanned = 0;
    picked.forEach(function (l) {
      var r = state.est && state.est.results && state.est.results[l.rid];
      if (r && r.ok) bytesPlanned += r.bytes || 0;
    });

    msg('正在建立任务…');
    post('/api/jobs', {
      course: plan,
      out_dir: out,
      threads: parseInt($('threadsInput').value, 10) || 10,
      jobs: parseInt($('jobsInput').value, 10) || 2,
      bytes_planned: bytesPlanned
    }).then(function (j) {
      if (j && j.error) { msg('启动失败：' + j.error, true); return; }
      msg('已开始（任务 ' + j.id + '，共 ' + j.total + ' 节）。可以关掉这个页面，任务在后台跑。');
      refreshJobs();
    }).catch(function (e) { msg('启动失败：' + e, true); });
  };

  function msg(t, err) {
    $('startMsg').textContent = t || '';
    $('startMsg').className = 'msg' + (err ? ' err' : ' ok');
  }

  /* ---------------- 任务列表 ---------------- */
  function refreshJobs() {
    api('/api/jobs').then(function (r) {
      var box = $('jobs');
      if (!r.jobs || !r.jobs.length) {
        box.innerHTML = '<p class="muted">暂无任务。</p>';
        return;
      }
      box.innerHTML = '';
      r.jobs.slice().reverse().forEach(function (j) {
        var total = j.total || 1;
        var pct = Math.min(100, Math.round(100 * (j.done + j.failed) / total));
        var el = document.createElement('div');
        el.className = 'job';

        var h = document.createElement('h4');
        h.textContent = j.title + '  ·  ' + j.id;

        var bar = document.createElement('div');
        bar.className = 'bar' + (j.state === 'done' ? ' done' : (j.state === 'failed' ? ' failed' : ''));
        var fill = document.createElement('i');
        fill.style.width = pct + '%';
        bar.appendChild(fill);

        var meta = document.createElement('div');
        meta.className = 'jmeta';
        var sizeTxt = '已下 ' + human(j.bytes || 0);
        if (j.bytes_planned) sizeTxt += ' / 预计 ' + human(j.bytes_planned);
        meta.innerHTML =
          '<span>状态 <b>' + esc(j.state) + '</b></span>' +
          '<span>完成 ' + j.done + ' / ' + total + '</span>' +
          (j.failed ? '<span class="bad">失败 ' + j.failed + '</span>' : '') +
          '<span>' + sizeTxt + '</span>' +
          '<span>' + esc(j.out_dir) + '</span>';

        el.appendChild(h); el.appendChild(bar); el.appendChild(meta);

        var row = document.createElement('div');
        row.className = 'row tight';
        var btn = document.createElement('button');
        btn.className = 'ghost tiny';
        btn.textContent = '打开目录';
        btn.onclick = function () { post('/api/open', { path: j.out_dir }); };
        row.appendChild(btn);

        if (j.state === 'running' || j.state === 'pending') {
          var bc = document.createElement('button');
          bc.className = 'ghost tiny';
          bc.textContent = '取消';
          bc.onclick = function () {
            post('/api/jobs/' + j.id + '/cancel', {}).then(refreshJobs);
          };
          row.appendChild(bc);
        }
        el.appendChild(row);

        if (j.logs && j.logs.length) {
          var lg = document.createElement('details');
          var sm = document.createElement('summary');
          sm.textContent = '日志（最近 ' + j.logs.length + ' 行）';
          var pre = document.createElement('div');
          pre.className = 'logs';
          pre.textContent = j.logs.join('\n');
          lg.appendChild(sm); lg.appendChild(pre);
          el.appendChild(lg);
        }
        box.appendChild(el);
      });
    });
  }

  /* ---------------- 配置保存 ---------------- */
  $('btnSaveCfg').onclick = function () {
    var patch = {
      host: $('cfgHost').value.trim(),
      port: parseInt($('cfgPort').value, 10),
      sizing_workers: parseInt($('cfgWorkers').value, 10),
      sizing_sample: parseInt($('cfgSample').value, 10),
      default_out: $('outDir').value.trim() || undefined,
      threads: parseInt($('threadsInput').value, 10),
      jobs: parseInt($('jobsInput').value, 10)
    };
    post('/api/settings', patch).then(function (r) {
      $('cfgMsg').textContent = r && r.ok
        ? '已保存到 ' + r.path + '（端口/目录改动需重启 start.py）'
        : '保存失败';
      $('cfgMsg').className = 'msg ' + (r && r.ok ? 'ok' : 'err');
    }).catch(function (e) {
      $('cfgMsg').textContent = '保存失败：' + e;
      $('cfgMsg').className = 'msg err';
    });
  };

  init();
})();
