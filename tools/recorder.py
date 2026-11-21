"""Line / touched-class recorder on sys.monitoring, shared by collect.py and the worker sitecustomize."""
import sys

mon = sys.monitoring
E = mon.events


class Recorder:
    def __init__(self, src, tool_id=2):
        self.src, self.pkg, self.tid = src, src + "/sqlglot/", tool_id
        self.lines, self.classes = {}, set()

    def start(self):
        pkg, n, lines, classes = self.pkg, len(self.src) + 1, self.lines, self.classes
        mon.use_tool_id(self.tid, "rec")

        def on_line(code, line):
            fn = code.co_filename
            if fn.startswith(pkg):
                lines.setdefault(fn[n:], set()).add(line)
            return mon.DISABLE

        def on_start(code, off):
            if code.co_argcount and code.co_filename.startswith(pkg):
                a = code.co_varnames[0]
                if a == "self" or a == "cls":
                    try:
                        v = sys._getframe(1).f_locals.get(a)
                    except Exception:
                        return
                    classes.add(v if isinstance(v, type) else type(v))

        mon.register_callback(self.tid, E.LINE, on_line)
        mon.register_callback(self.tid, E.PY_START, on_start)
        mon.set_events(self.tid, E.LINE | E.PY_START)

    def stop(self):
        mon.set_events(self.tid, 0)
        cn = set()
        for c in self.classes:
            for k in c.__mro__:
                if k.__module__.startswith("sqlglot"):
                    cn.add(f"{k.__module__}.{k.__qualname__}")
        return self.lines, cn
