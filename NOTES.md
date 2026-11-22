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
- Fail-fast: when heavy candidates exist the cheap run only decides "keep heavy?"; stop at first failure and print all cheap candidates alongside (hidden under the heavy worker's wall time). Sample CPU 52 -> 36 s. KEEP.
- Measured per-test CPU under our runner (tools/cpuprof.py, data/cpu.json): total 21.8 s vs 38.7 s recorded; only TestExecutor.setUpClass is costly (0.9 s). Used for run-vs-print decision. KEEP.
- Import-time statements (class/module body, decorators, defaults): runner imports the changed modules first; an import error means every test that loads the module fails (data/closure.json = test files whose import loads each module). Found by mutant m063 (`set + set` at module level, 150 failing tests). KEEP.
- changed_old_lines: a '+' block directly after '-' is a replacement, not an insertion (insertion neighbours blew candidate sets to the whole class). KEEP.
- Heavy gamble off vs on on sample+mutants: ~30% cheaper with it; 0 observed heavy-only failures so far. Risk is a 300 s miss when a heavy test is the only failing one; unproven.
- Dropped: running only cheap tests as evidence (0/17 observed misses but sample too small for a 300 s penalty).
- Runner false failures: two tests (test_enum, starrocks roundtrip) fail in-process on a big run but pass under pytest (state leakage); only costs seconds.
- RESULT sample (30 commits): simulated 94.351 s, 0 misses, container CPU 37.4 s (cgroup cpu.stat, wall 22 s) => about 131.7. Mutants (67, one-token edits + fixtures): simulated 277.4 s, 0 misses, CPU 139 s.
- Import-time state (experiment): recorded per test file the lines/classes executed while importing it, then (a) re-imported those test files patched, (b) included their tests. (a) found nothing for m051 and cost +10 s CPU on the sample; (b) blew up candidate sets for core-line commits (4d63, ktz3 -> 1000+ tests). DROPPED, code reverted (tools/importrec.py, build_impmap.py kept for reference).
- KNOWN HOLE (1 of ~120 mutants): m051 `"option": False -> True` in exp.JSONExtract.arg_types breaks 1119 tests (duckdb dialect parse of a template fails on first use). Per-test class-touch map has only 69 tests for JSONExtract, so 1050 failing tests were missed (300 s). Cause not pinned down (probably state created in the parent process before forking, so per-test isolation never sees it). Next: record class touches with a fresh interpreter per test file (not a pre-imported parent), or treat required-arg (False -> True) arg_types flips as global.
- FINAL (commit below): sample 94.351 simulated, 0 misses, cpu 39.1 s (container cgroup), wall 23 s. ~120 labeled mutants (one-token edits, fixtures, statement deletes): 1 miss (m051).

## Round 2 (after practice result 241.9 = 169.7 sim + ~72 CPU; scorer CPU ~1.6x local)
- m051 root cause: class body of the duckdb generator parses a SQL template (MINHASH_TEMPLATE) at import, instantiating JSONExtract; per-test isolation never saw it (module already imported in the parent). Fix 1: tools/modrec.py records, per sqlglot module, which classes get instances while it is imported in a fresh interpreter (data/modtouch.json, 113 classes); a class-level edit adds every test that loads those modules. m051 candidates 69 -> 1113 of 1119 failing. KEEP.
- The residual 20 failing tests were collateral (a failed lazy dialect load poisons later tests in the same pytest process). Not predictable per test. Fix 2: print everything when the chosen set costs >= 0.8 x print-all or names >= 50% of all tests. KEEP (insurance).
- Canary guard (non-function-body edits only): in the same forked run, import-check changed modules, load every dialect and push one SELECT through it (tools: runner._smoke, ~0.09 s), plus 52 canary tests chosen by greedy line cover per CPU second under a 0.25 s budget (data/canary.json, 50% of covered lines, 28 files). Any failure outside the candidate set, or a dialect failing to load, prints all tests. With the modtouch map switched off (BLAST_NO_MODTOUCH=1) m051 is caught by the smoke test: 0 misses, cost 36.8. Over 182 labeled mutants: 2 alarms, both genuine (m051, am076), 0 false alarms, 0 misses. KEEP.
- Cost model recalibrated: run iff 1.6 x local CPU + 0.6 < 0.55 x cost(print everything we would otherwise print). Sample: simulated 94.4 -> 131.6, local CPU 37 -> 17; at the scorer factor 154 -> 159 (neutral on the sample, safer if scorer CPU is slow).
- Fixed overhead: Mapper() 2 ms, interpreter+imports ~50 ms; the CPU is the tests themselves (30.5 of 35 s on the sample), not setup. No fixed-cost fix found.
- 182 labeled mutants (one-token, fixtures, deletes, targeted heavy-candidate): 0 misses at this commit (earlier version: 1).

## Round 3 (practice: 8551c38 = 251.0 sim + 18.7 CPU = 269.7 vs b86e759 = 169.7 + 72)
- The scorer-calibrated run/print model overshot: on the sample it printed 2haz/4d63/5y72 instead of running them (+37 simulated for -19 CPU); practice was +81 for -53. REVERTED to run-when-cheaper-than-printing (RUN_MUL 1.1, RUN_FIX 0.4; heavy candidates always get the evidence run). Sample simulated back to the oracle floor 94.351 (sum of exact failing-set costs).
- Heavy gamble stats (212 labeled commits): when cheap tests fail, tpcds fails 11/35 (31%), tpch 4/19; proxies (optimizer test_tpcds/test_tpch, executor tests) do not predict it (2 of 21 tpcds failures had no proxy failing, normalize.py edits). No cheaper predictor found; kept.
- test_generator_memory (6.7 s) dropped from candidates unless generator core changes: removes the heavy evidence-run path for the ~28% of commits where it was the only heavy candidate.
- Canary escalation now goes to the union (failing dialects' modules + tests/dialects/test_<d>.py, failing canary files, changed-module import failures) and prints everything only through the existing 0.8-cost / 50%-of-tests rules.
- New hole found by diverse mutants: renaming a function (m017 `def isolate_table_selects` -> `_renamed`) breaks the importing module (727 tests). Fix: the import check also imports every module that imports a changed module (mapper.importer_files, multi-line from-imports handled). 0 misses after.
- Canary budget 0.25 -> 0.12 s (26 tests, 17 files).
- Not done: is/== equivalence detection (needs an operand-type profile; `==` on a str-Enum is not strictly equal to `is`), per-line operand profiles like TOP67.
- Results at this commit (real image): sample 94.351 simulated, 0 misses, CPU 43.1 s (local); 182 mutants 1332.3 simulated, 0 misses, CPU 508.5; 25 diverse mutants 539.3, 0 misses.

## Round 4 (static-first CPU cuts; sample 94.351 sim, 0 misses, container CPU 59 -> 13.9 s)
- Measured where the sample CPU went: 85% of child CPU was spent verifying code commits with ZERO failing tests (2haz, ktz3, 4d63, 66ks...). Failing commits cost little. So the lever is proving "no test can change", not faster runs.
- data/cmp.z (cmpmap.py, built by tools/cmprec.py): every single-operator comparison in sqlglot is wrapped; per test (cold fork) we record the outcome of all ten operators on the real operands (plus the value of the other operand when one side is a literal) and, in a second set, which arg_types keys are missing at Expression.error_messages (required flags are read nowhere else). A patch whose AST differs only in comparison operators, literal operands of comparisons, or bool flips in a class arg_types dict selects exactly the tests whose recorded outcome changes (often none -> print []). Evaluations during import set a "global" bit -> not eligible. Non-bool results (Expression > 0 builds a node) and Python-level __eq__ operands are recorded as unknown (always different). Tests with no recording (test_max_nodes crawls under instrumentation) are added only when coverage of the patch reaches them.
- Pitfalls found: alternatives evaluated through __eq__ recurse forever (guard flag); orig op must run outside the guard or nested sites go unrecorded (m036 miss); operator overloads fooled bool() (m047 miss); `is` vs `==` on str-Enums is a real difference only for non-Enum values.
- tools/cmpcheck.py: affected set must contain the labeled failing set. 38 eligible of 272 labeled patches, 0 misses; 242 labeled mutants through the real image: 0 misses.
- runner: TestExecutor.setUpClass (loads TPC-H/DS csv, 0.9 s local) is replaced by a stub unless a selected test needs it and the executor tests/fixtures are not patched; gc threshold raised (~3%).
- selector: one child at a time until 70 s of wall time (two concurrent children cost ~8% more CPU); budget/time guards unchanged.
- Remaining CPU on the sample is dominated by 4d63 (tuple literal in a tokenizer table, 273 candidates, 10 s). Next idea: key-level read map for class tables.
- Studied colinbenaissa/blast-radius-selector (no tests run at scoring time): same families of proofs (comparison profiles, import-time state diff of every module global/class attribute, name-mention + class-touch narrowing). Their import-state digest diff is the better answer for class-table edits like 4d63; ours stays run-based there.
