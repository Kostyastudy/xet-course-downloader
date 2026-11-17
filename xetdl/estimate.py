# -*- coding: utf-8 -*-
"""体积估算任务：把 sizing 的批量估算包成一个可查询进度、可取消的后台任务。

前端点「估算体积」→ 起一个后台线程，边算边出数，算完给出总额。
189 节课全部算完大约 1 分钟，所以不能同步阻塞 HTTP 请求。
"""
import threading
import time

from . import config, sizing


class Estimator(object):
    def __init__(self):
        # Lock 不可重入：所有对外方法各自加锁，内部一律调 _reset_locked()。
        self._lock = threading.Lock()
        self._cancel = False
        self._reset_locked()

    def _reset_locked(self):
        self.running = False
        self.started = 0.0
        self.finished = 0.0
        self.done = 0
        self.total = 0
        self.results = {}       # rid -> {bytes, seconds, ok, method, note}
        self.errors = []

    def reset(self):
        with self._lock:
            self._reset_locked()

    def cancel(self):
        self._cancel = True

    def start(self, lessons, workers=None, sample=None):
        """lessons: [{'rid','title','playlist_url','vod_url'}]"""
        with self._lock:
            if self.running:
                return False
            self._reset_locked()
            self._cancel = False
            self.running = True
            self.started = time.time()
            self.total = len(lessons)
        t = threading.Thread(target=self._run, args=(lessons, workers, sample),
                             daemon=True)
        t.start()
        return True

    def _run(self, lessons, workers, sample):
        workers = workers or config.SIZING_WORKERS
        sample = sample or config.SIZING_SAMPLE

        def on_progress(done, total, r):
            with self._lock:
                self.done = done
                if r:
                    self.results[r.get('rid') or ''] = {
                        'bytes': r.get('bytes') or 0,
                        'seconds': r.get('seconds') or 0,
                        'ok': bool(r.get('ok')),
                        'method': r.get('method') or '',
                        'note': r.get('note') or '',
                    }
                elif done:
                    pass

        try:
            sizing.estimate_batch(lessons, progress=on_progress, workers=workers,
                                  sample=sample, should_stop=lambda: self._cancel)
        except Exception as e:
            with self._lock:
                self.errors.append('%s: %s' % (type(e).__name__, e))
        finally:
            with self._lock:
                self.running = False
                self.finished = time.time()

    def snapshot(self):
        with self._lock:
            total_bytes = sum((v.get('bytes') or 0) for v in self.results.values())
            return {
                'running': self.running,
                'started': self.started,
                'finished': self.finished,
                'done': self.done,
                'total': self.total,
                'bytes': total_bytes,
                'human': sizing.human_size(total_bytes),
                'results': dict(self.results),
                'errors': list(self.errors),
                'cancelled': self._cancel,
            }


estimator = Estimator()
