"""Which sqlglot source files does importing each test file load? (fresh interpreter per file)
Usage (dev image): python closure.py TESTS_JSON OUT_JSON"""
import json, subprocess, sys
tests = json.load(open(sys.argv[1]))["tests"]
files = sorted({t["file"] for t in tests})
code = ("import sys\nimport {mod}\nprint(json.dumps(sorted(m.__file__[len('/opt/sqlglot/'):] for m in sys.modules.values() "
        "if getattr(m,'__file__',None) and m.__file__.startswith('/opt/sqlglot/sqlglot/'))))")
out = {}
for f in files:
    r = subprocess.run([sys.executable, "-c", "import json\n" + code.format(mod=f[:-3].replace("/", "."))],
                       capture_output=True, text=True, cwd="/opt/sqlglot")
    for rel in json.loads(r.stdout.strip().splitlines()[-1]):
        out.setdefault(rel, []).append(f)
json.dump(out, open(sys.argv[2], "w"), separators=(",", ":"))
print(len(out), "modules")
