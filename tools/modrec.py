"""For every sqlglot module M: which classes get instances (self frames) while M is imported in a fresh
interpreter? Class-body code that builds instances at import (templates, parse calls) makes other
classes' definitions matter to every user of M.
Usage (dev image): python modrec.py OUT_JSON"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SRC = "/opt/sqlglot"
if len(sys.argv) > 2 and sys.argv[1] == "--child":
    from recorder import Recorder
    sys.path.insert(0, SRC)
    rec = Recorder(SRC, 2)
    rec.start()
    import importlib
    try:
        importlib.import_module(sys.argv[2])
    except BaseException as e:
        print("fail", sys.argv[2], repr(e), file=sys.stderr)
    rec.stop()
    print(json.dumps(rec.inst_names()))
else:
    mods = []
    for dp, _, fs in os.walk(SRC + "/sqlglot"):
        for f in fs:
            if f.endswith(".py") and "_version" not in f:
                rel = os.path.relpath(dp + "/" + f, SRC)[:-3].replace("/", ".")
                mods.append(rel[:-9] if rel.endswith(".__init__") else rel)
    out = {}
    for m in sorted(mods):
        r = subprocess.run([sys.executable, __file__, "--child", m], cwd=SRC, env=dict(os.environ, PYTHONPATH=SRC),
                           capture_output=True, text=True)
        try:
            out[m] = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out[m] = None
    json.dump(out, open(sys.argv[1], "w"))
    print(len(out), "modules")
