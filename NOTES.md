# Blast Radius notes (hypothesis -> number -> keep/drop)

## Design
selector.py: per patch -> mapper.py candidate bitset (per-line coverage per test, touched-class map,
fixture-open map, built from pinned sqlglot, data/) -> either run cheap candidates on a patched overlay
(runner.py, forked child, symlink overlay so spawn workers see patched code) and print exact failures,
or print candidates packed over <=8 workers. Comment-only .py changes are dropped by a token-stream compare.

## Experiments
- Image import cost: `import sqlglot` 53 ms; full suite pytest -n4 = 35 s wall (1620 pass, 25k subtests).
- Per-test fork coverage (sys.monitoring LINE + self/cls classes + opens): 1620 tests, map 280 KB compressed. KEEP.
- First collection missed spawn-worker lines (executor/optimizer pool tests: tpcds 21 s, tpch). Fixed with sitecustomize recorder in workers.
- test_max_nodes (28.8 s) and test_execute_tpcds (21.5 s) dominate; any commit whose candidates include them costs >= 25 s.
