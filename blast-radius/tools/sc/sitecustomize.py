import os
if os.environ.get("COLLECT_TAG"):
    import atexit, pickle, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from recorder import Recorder
    _r = Recorder(os.environ["COLLECT_SRC"], 3)
    _r.start()
    def _dump():
        lines, ilines, cn = _r.stop()
        with open(f"{os.environ['COLLECT_OUT']}/w_{os.environ['COLLECT_TAG']}_{os.getpid()}.pkl", "wb") as f:
            pickle.dump(dict(lines=lines, ilines=ilines, classes=cn), f)
    atexit.register(_dump)
