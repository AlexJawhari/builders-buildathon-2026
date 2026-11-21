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
- sample (30 commits) first docker run, coverage candidates + run-and-verify, no heavy gamble: 182.9 sim, 0 misses. Heavy (tpcds 21 s / tpch / generator_memory) candidates cost ~25 s on 5 commits (124 of 183 s).
- Running TestOptimizer tests with the real spawn pool: each test spawns ~cpu_count workers (each re-imports duckdb/pandas) -> CPU explosion + hit 40 s deadline. Fix: synchronous in-process stand-in for ProcessPoolExecutor with pickle round-trip (serde mutants only fail through pickling; without the round-trip 3utz was a miss). KEEP.
- Per-test setUpClass: run one suite per class, not per test (pool/db setup once). KEEP.
- Heavy gamble (drop tpcds etc. when the cheap candidates all pass): 0 of 9 labeled cases where heavy failed with R passing; collecting more labels (tools/gamble.py).
- Fixture edits: any non-comment changed line => reader tests printed without running (12/12 sample fixture commits fail their reader). KEEP.
- Class/module-level edits: tests touching class (MRO of self/cls seen in frames) intersected with tests running lines that mention the assigned name (def-use), fallback to whole class when usage is import-time only. WEEK_OFFSET commit 171 -> 12 candidates. KEEP.
