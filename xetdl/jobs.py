# -*- coding: utf-8 -*-
"""任务调度：一个 Job = 一门课的下载计划，带进度、取消与断点续跑。"""
import os
import threading
import time
import traceback
import uuid

from . import archiver, config, courseware as cw_mod, downloader
from .model import Course


class Job(object):
    def __init__(self, course, out_dir, threads=None, jobs=None):
        self.id = uuid.uuid4().hex[:12]
        self.course = course
        self.out_dir = out_dir
        self.threads = threads or config.DEFAULT_THREADS
        self.jobs = jobs or config.DEFAULT_JOBS
        self.state = 'pending'          # pending/running/done/failed/cancelled
        self.created = time.time()
        self.started = None
        self.finished = None
        self.error = ''
        self.lessons = {}               # rid -> {state, done, total, bytes, message}
        self.logs = []
        self.bytes_planned = 0          # 提交时给出的预计总体积（0 = 未估算）
        self._cancel = False
        self._lock = threading.Lock()

    # ---------- 状态 ----------
    def set_lesson(self, rid, **kw):
        with self._lock:
            self.lessons.setdefault(rid, {}).update(kw)

    def log(self, msg):
        line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 800:
                self.logs = self.logs[-800:]
        print(line, flush=True)

    def cancel(self):
        self._cancel = True

    @property
    def cancelled(self):
        return self._cancel

    def snapshot(self):
        with self._lock:
            lessons = dict(self.lessons)
            done = sum(1 for v in lessons.values() if v.get('state') == 'done')
            failed = sum(1 for v in lessons.values() if v.get('state') == 'failed')
            got = sum(int(v.get('bytes') or 0) for v in lessons.values())
            return {
                'id': self.id,
                'title': self.course.title,
                'out_dir': self.out_dir,
                'state': self.state,
                'error': self.error,
                'created': self.created,
                'started': self.started,
                'finished': self.finished,
                'lessons': lessons,
                'logs': self.logs[-120:],
                'total': len(self.course.lessons),
                'done': done,
                'failed': failed,
                'bytes': got,
                'bytes_planned': self.bytes_planned,
            }


class JobManager(object):
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def submit(self, course_dict, out_dir, threads=None, jobs=None):
        course = Course.from_dict(course_dict)
        course.sort()
        job = Job(course, out_dir, threads, jobs)
        with self._lock:
            self._jobs[job.id] = job
        t = threading.Thread(target=_run_job, args=(job,), daemon=True)
        t.start()
        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def list(self):
        with self._lock:
            return list(self._jobs.values())

    def prune(self, keep=30):
        with self._lock:
            if len(self._jobs) <= keep:
                return
            olds = sorted(self._jobs.values(), key=lambda j: j.created)[:-keep]
            for j in olds:
                self._jobs.pop(j.id, None)


def _run_job(job):
    course = job.course
    job.state = 'running'
    job.started = time.time()
    root = archiver.course_root(job.out_dir, course)
    os.makedirs(root, exist_ok=True)
    job.log('开始下载《%s》 -> %s' % (course.title, root))
    # 落盘下载计划：既能当存档，也能给「python -m xetdl.cli 下载计划.json」复用
    try:
        from . import model
        plan_path = os.path.join(root, '下载计划.json')
        model.dump_plan(course, plan_path)
        job.log('下载计划已保存：%s' % plan_path)
    except Exception as e:
        job.log('（下载计划保存失败：%s）' % e)

    status = {}
    try:
        _download_videos(job, root, status)
        if not job.cancelled:
            _download_courseware(job, root)
        if not job.cancelled:
            archiver.write_index(course, root, status)
            archiver.write_readme(course, root)
        job.state = 'cancelled' if job.cancelled else 'done'
    except Exception as e:
        job.error = '%s: %s' % (type(e).__name__, e)
        job.log('[ERROR] %s' % job.error)
        traceback.print_exc()
        job.state = 'failed'
    finally:
        job.finished = time.time()
        job.log('任务结束：%s' % job.state)


def _download_videos(job, root, status):
    import queue
    vids = [l for l in job.course.lessons if l.is_video]
    q = queue.Queue()
    for l in vids:
        q.put(l)
    lock = threading.Lock()

    def worker():
        while True:
            try:
                l = q.get_nowait()
            except queue.Empty:
                return
            ldir = archiver.lesson_dir(root, l, job.course)
            os.makedirs(ldir, exist_ok=True)
            mp4 = os.path.join(ldir, '视频.mp4')
            old, sz = archiver.find_existing_mp4(root, job.course, l)
            if sz > 1024 * 1024:
                job.set_lesson(l.rid, state='done', note='已存在')
                status[l.rid] = ('skipped', '已存在')
                job.log('[HAVE] %s' % l.title)
                if old != mp4:
                    # 老版本是平铺目录，这次分了组：把已有视频搬到新位置，不重下
                    try:
                        os.makedirs(ldir, exist_ok=True)
                        archiver.link_or_copy(old, mp4)
                    except Exception:
                        pass
                continue

            def prog(st, rid=l.rid):
                job.set_lesson(rid, **st)

            try:
                if l.rtype == 4:
                    ok, msg = downloader.download_replay(
                        l, ldir, job.threads, prog, job.log,
                        cancelled=lambda: job.cancelled)
                else:
                    ok, msg = downloader.download_vod(
                        l, ldir, job.threads, prog, job.log,
                        cancelled=lambda: job.cancelled)
            except Exception as e:
                ok, msg = False, '%s: %s' % (type(e).__name__, e)
            if job.cancelled:
                job.set_lesson(l.rid, state='cancelled')
                continue
            if ok:
                job.set_lesson(l.rid, state='done', note=msg)
                status[l.rid] = ('done', '')
            else:
                job.set_lesson(l.rid, state='failed', note=msg)
                status[l.rid] = ('failed', msg)
                job.log('[FAIL] %s — %s' % (l.title, msg))

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, job.jobs))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()


def _download_courseware(job, root):
    cws = job.course.courseware
    if not cws:
        return
    videos = [l for l in job.course.lessons if l.is_video]
    for cw in cws:
        if job.cancelled:
            return
        # 资料课时自己的目录
        holder = next((l for l in job.course.lessons if l.rid == cw.rid), None)
        dest = (archiver.lesson_dir(root, holder, job.course) if holder
                else os.path.join(root, '课件集合'))
        wdir = os.path.join(dest, '课件')
        job.log('课件：%s（%d 个文件）' % (cw.title, len(cw.files)))
        ok, failed = cw_mod.download_courseware(
            cw, wdir, progress=lambda st: job.set_lesson(cw.rid, **st), log=job.log)
        # 按文件名把课件分发到对应视频课时
        for it in cw.files:
            name = cw_mod.ware_filename({'url': it.url, 'title': it.title})
            src = os.path.join(wdir, name)
            if not os.path.exists(src):
                continue
            tgt = archiver.ware_to_lesson(name, videos)
            if tgt:
                tdir = os.path.join(archiver.lesson_dir(root, tgt, job.course), '课件')
                os.makedirs(tdir, exist_ok=True)
                archiver.link_or_copy(src, os.path.join(tdir, name))
