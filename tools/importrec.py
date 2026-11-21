"""Record, per test file, the sqlglot lines executed and classes touched while importing it
(test module bodies run at import and can fail every test of the file).
Usage (dev image): python importrec.py TESTS_JSON OUTDIR   (children: python importrec.py --child MODULE OUTFILE)"""
import json, os, pickle, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SRC = "/opt/sqlglot"
if len(sys.argv) > 1 and sys.argv[1] == "--child":
    from recorder import Recorder
    sys.path.insert(0, SRC)
    rec = Recorder(SRC, 2)
    rec.start()
    import importlib
    try:
        importlib.import_module(sys.argv[2])
    except BaseException as e:
        print("import failed", sys.argv[2], repr(e), file=sys.stderr)
    lines, ilines, cn = rec.stop()
    pickle.dump(dict(lines=lines, ilines=ilines, classes=cn), open(sys.argv[3], "wb"))
else:
    tests = json.load(open(sys.argv[1]))["tests"]
    out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    files = sorted({t["file"] for t in tests})
    for i, f in enumerate(files):
        subprocess.run([sys.executable, __file__, "--child", f[:-3].replace("/", "."), f"{out}/{i}.pkl"],
                       cwd=SRC, env=dict(os.environ, PYTHONPATH=SRC), stderr=subprocess.DEVNULL)
    json.dump(files, open(f"{out}/files.json", "w"))
