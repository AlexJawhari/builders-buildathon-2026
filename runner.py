"""Run chosen tests against a patched copy of sqlglot in a forked child and report which fail.

The patched tree is an overlay of mostly symlinks into the pristine checkout, so spawned
multiprocessing workers (which inherit sys.path) see the patched code too.
"""
import json
import os
import signal
import sys
import time
import unittest

import patchlib


class _Timeout(BaseException):
    pass


def _alarm(_s, _f):
    raise _Timeout()


def build_overlay(root, src, patched):
    """patched: {rel path: text or None (deleted)}. Directories on a changed path are real,
    everything else is a symlink into src."""
    real = {""}
    for rel in patched:
        parts = rel.split("/")
        real |= {"/".join(parts[:i]) for i in range(1, len(parts))}

    def fill(rel):
        d = os.path.join(root, rel)
        os.makedirs(d, exist_ok=True)
        sd = os.path.join(src, rel)
        for name in os.listdir(sd) if os.path.isdir(sd) else ():
            r = f"{rel}/{name}" if rel else name
            if r in real:
                fill(r)
            else:
                os.symlink(os.path.join(sd, name), os.path.join(d, name))

    fill("")
    for d in sorted(real):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for rel, text in patched.items():
        dst = os.path.join(root, rel)
        if os.path.lexists(dst):
            os.unlink(dst)
        if text is not None:
            with open(dst, "w", encoding="utf-8", newline="") as f:
                f.write(text)


def child_main(wfd, root, src, patched, tests, nums, limits):
    """Runs in the forked child; writes a JSON result line to wfd and exits."""
    res = {"failed": [], "ok": True}
    try:
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        build_overlay(root, src, patched)
        sys.path.insert(0, root)
        os.environ["PYTHONPATH"] = root
        signal.signal(signal.SIGALRM, _alarm)
        by_cls = {}
        for n in sorted(nums, key=lambda n: tests[n]["id"]):
            f, c, m = tests[n]["id"].split("::")
            by_cls.setdefault((f, c), []).append((m, n))
        import importlib
        for (f, c), items in by_cls.items():
            mod_name = f[:-3].replace("/", ".")
            try:
                signal.setitimer(signal.ITIMER_REAL, 30)
                mod = importlib.import_module(mod_name)
                cls = getattr(mod, c)
                signal.setitimer(signal.ITIMER_REAL, 0)
            except BaseException:
                signal.setitimer(signal.ITIMER_REAL, 0)
                res["failed"] += [n for _, n in items]  # an unimportable file fails its tests
                continue
            for m, n in items:
                r = unittest.TestResult()
                suite = unittest.TestSuite([cls(m)])
                signal.setitimer(signal.ITIMER_REAL, limits.get(n, 10))
                try:
                    suite.run(r)
                except BaseException:
                    r.errors.append(("t", "timeout"))
                signal.setitimer(signal.ITIMER_REAL, 0)
                if not r.wasSuccessful():
                    res["failed"].append(n)
    except BaseException as e:  # any internal failure: caller falls back to printing candidates
        res = {"failed": [], "ok": False, "err": repr(e)[:200]}
    try:
        os.write(wfd, json.dumps(res).encode())
    finally:
        os._exit(0)


class Job:
    def __init__(self, cid, root, src, patched, tests, nums, limits, deadline):
        self.cid, self.nums, self.deadline = cid, nums, deadline
        r, w = os.pipe()
        self.pid = os.fork()
        if self.pid == 0:
            os.close(r)
            child_main(w, root, src, patched, tests, nums, limits)
        os.close(w)
        self.fd, self.buf, self.root = r, b"", root

    def poll(self):
        """Returns result dict when done, None while running."""
        import select
        while True:
            rd, _, _ = select.select([self.fd], [], [], 0)
            if not rd:
                break
            chunk = os.read(self.fd, 65536)
            if not chunk:
                break
            self.buf += chunk
        try:
            pid, _ = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pid = self.pid
        if pid == 0:
            if time.time() > self.deadline:
                try:
                    os.kill(self.pid, signal.SIGKILL)
                    os.waitpid(self.pid, 0)
                except OSError:
                    pass
                return self._finish({"ok": False, "failed": [], "err": "deadline"})
            return None
        while True:
            chunk = os.read(self.fd, 65536)
            if not chunk:
                break
            self.buf += chunk
        try:
            return self._finish(json.loads(self.buf))
        except Exception:
            return self._finish({"ok": False, "failed": [], "err": "no result"})

    def _finish(self, res):
        os.close(self.fd)
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)
        return res
