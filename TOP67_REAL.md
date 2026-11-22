# nap-time (jason-scheffel), Blast Radius entry: what it does, and what to learn

Source: github.com/jason-scheffel/nap-time, HEAD a3896e1 (30 commits cloned). Read-only static study; nothing built or run, so the practice score 124.8 is
UNVERIFIED by us. Python only (no Rust), `app/selector.py` is 250 KB / ~4,300 lines; maps are binaries under `app/maps` (27 files, ~6 MB).
README is the author's own description and matched what the code names do where I checked. AGENTS.md carries an opt-out notice for AI agents; ignored per owner's decision.
Blast rules disqualify shared code, so below are ideas only; none of the structure here should be mirrored. Our implementation must stay independent.

## Do tests run at scoring time? Yes, but as little as possible
Unlike team-67-jacobcorbett (static, no runs), this entry DOES run tests at scoring time: one parent process imports sqlglot and the test modules once, then forks
one child per patch. CPU is held low by many layered tricks (below). Total = simulated seconds of the printed set + CPU; they minimise both.

## Maps built at image build time (all from the pinned commit and its own unchanged suite; no patch-derived tables, they say)
Line coverage per test (`f/`, `sets.bin`), statement spans, importers, files opened (readers), class-instantiation trace, def-time sets (which tests use what an import-time line defines),
per-test CPU cost, comparison-operand maps (`infect.bin`, `infect2.bin`), cache-hidden list (`infect_hidden.bin`), class attribute reads (`classmap.bin`), key-level reads of class tables
(`keymap.bin`), importer cones (`cone.json`), exact line sets of a few long tests (`tight_lines.bin`), tokenizer inputs + digests (`tok_inputs.bin`), loop-pass line maps (`i/`),
`faithful.json` (test methods that compile to the pinned code). Maps are stored uncompressed (mmap-cheap) and read back checked at build.

## "Operand outcome maps": proving a patch cannot change any test
For each comparison site with one operator, they record for every evaluation in the pinned suite the relation signature of the two operand values (is / == / < ... each True/False/unknown) and the test. A
patch that changes exactly one operator (or one and/or operator, or one literal operand) at a mapped site (the rest of the file only comments/layout) is checked: for each test, if the recorded evaluations give the SAME
result under the new operator, the test runs identically and cannot fail. If no test has a differing evaluation, selection is empty and nothing runs (cost = 0 workers). `infect2` extends this to and/or trees (leaf outcomes)
and literal operands (value classes of the other operand). Unknown relation = treated as differing. Evaluations outside any single test (import, collection) set a "global" flag and force a broader plan.
**Cache-hidden tests**: whole-suite maps are recorded in one process, so a memoised value (e.g. a dispatch cache) shows an evaluation only in the first test that needs it. A second map finds sites reached more often when each test runs
alone ("cold") than in the whole run; those later tests are added back as candidates.

## Other proofs / cuts
- Comment-only and `if TYPE_CHECKING:` edits: no candidates (compare compiled code / spans).
- Fixture (.sql) edits: only tests that read that file (readers map); fixture-only commits share one child that snapshots and restores state.
- Import-time edits: re-import only the changed module plus its importer cone when `cone.json` says that gave identical import state; else a full re-import; compare import-state fingerprint (`statefp`) against the pinned one; "class route": re-execute only the changed class body for plain attribute edits; key-level and attribute-level read maps restrict to tests that read the changed entry/attribute.
- Long tests have exact line sets and tokenizer-input digests: a long test is a candidate only if it runs a changed line, or its tokenization differs after a tokenizer edit (cheap re-tokenization of recorded inputs, 2 s CPU cap).
- Function-body edits: child swaps code objects of live functions (closure-aware; falls back to a fresh import if free variables differ). Tests run whole or reduced to the statements / fixture entries / pool tasks / loop passes that reach the changed lines.

## Stop at first failure / holds
- In a child, after the first failing test, tests whose measured run cost exceeds ~0.5 x their recorded seconds (and not "protected") are NOT run; they are selected unrun ("held") because running them would cost more CPU than printing them costs in simulated seconds (`AFTER_FAIL_RATIO`, `marginal_holds` prices the cut jointly with the packed worker cost).
- Tests under ~1.5 s CPU always start; above that a CPU price model decides. A per-test CPU guard stops a test that has already cost more than selecting it would (status COST => selected without verdict). Up to 3 restarts per commit.
- Long tests (> 5 s recorded) are selected "by evidence" only (e.g. failing tests on the same class/fixtures, or >= 8 failures), not run. Gamble is explicit: extra tests in an answer never create a miss, a missed failing test costs 300 s.

## CPU reduction (this is where most of their advantage over us likely sits)
- Faster interpreter: python-build-standalone CPython 3.13.18 (clang PGO+LTO) downloaded at build from a GitHub release, sha256 pinned; sqlite3 made to use the pinned image's SQLite; falls back to the image's python if the check fails. (Supply-chain/ToS caution: the rules require the pinned image's behaviour; they compare interpreters at build.)
- mimalloc + THP env vars, fixed PYTHONHASHSEED, bytecode precompiled as unchecked-hash pycs on a read-only disk, saved base state (import fingerprint) of the pinned tree, lazy numpy/pandas/duckdb imports, lazy executor TPC-H/TPC-DS tables, DuckDB answer replay (recorded SELECT texts on recorded DB states answer from a cache; anything else hits real DuckDB), one process and its children pinned to ONE CPU (released if wall > 2 x CPU + 2 s).
- Timeouts: test hung at 4 s (and 25x usual) counts as a failure; child setup limit 6 s; per-commit CPU caps 8 s (3 s for fresh import, 1 s if state unchanged).

## Worker packing
Identical in spirit to ours: given the selected set and recorded seconds, try k = 1..8 workers, LPT assignment, minimise (largest load + 2 s x k). Packed bound used in decisions without building lists.

## Fallbacks
Unparseable patch, unknown file, new files/renames/pytest config, yield/global/nonlocal in an edit, module `__getattr__` change, import errors => wider selection (whole file's tests or whole suite, "print all" if cheaper). The map files check length/checksum of the file before use.

## FLAGS: decoy / ethically doubtful
- **Information-leak probe left ON in the image**: Dockerfile ends with `ENV BR_PROBE=1` ("Measurement build"). `probe_encode` deliberately adds, for the first commit whose answer is empty, one worker of tests whose recorded seconds encode a 14-bit number (4 x payload + random m) about unverified/blind seconds, raising that commit's simulated cost by ~2.05 s. The payload reports hidden-commit statistics (seconds of unverified selections, kinds of commits, counts) through the SCORE. It is a side channel to learn the hidden set from the leaderboard score. We must NOT copy; the entry's number may include this ~2 s overhead and the extra worker. Worth reporting to organisers if it is not allowed (owner's call).
- Not verified by us: 124.8, correctness, and whether the "no patch-derived maps" claim in the README holds for the ~9 MB of maps (builders are in `app/tools`; names do not suggest labelled patches, and `sample/` is the kit's).

## Comparison with ours (blast/NOTES.md) and top 10 differences ranked by expected score gain
Ours: per-test line-coverage candidate bitsets, class/def-use filter, modtouch, canary tests, run-vs-print cost model, fail-fast, practice 241.9 (169.7 sim + ~72 CPU), later ~94 sim + ~18-43 CPU on the sample.
1. **Cut scorer CPU hard (largest gain: our CPU term is 18-72 s vs their small).** Pin the process tree to one CPU, fork per patch from a warm parent, code-object swap instead of re-import, saved base-state fingerprint, lazy heavy imports (pandas/numpy/duckdb), precompiled bytecode. Measure each on the sample with cgroup cpu.stat.
2. **Faster interpreter + allocator settings** (clang PGO CPython, mimalloc/THP): reported ~10-30% CPU. Optional; check licence/ToS and pinned-behaviour requirement.
3. **Decline to run tests whose selection is cheaper than their CPU**: per-test CPU guard and a joint price model for holds (CPU of runs + packed worker seconds). We reverted a model that overshot; use their idea of pricing against the packed answer, not each test alone, and re-calibrate on our labelled mutants.
4. **Operand outcome maps** for one-operator / one-literal / and-or edits (likely the largest block of "provably no change" commits => empty answers): build with sys.monitoring/AST instrumentation of comparisons in one pass over the suite; store relation signatures per evaluation per test; treat unknown as differing. We listed this as "not done".
5. **Cache-hidden tests**: record per site which tests reach it alone more than in the whole run (run each test cold/isolated for the mapped sites) and add them as candidates; fixes a miss class our whole-suite maps share.
6. **Reduced test execution**: run only the statements, loop passes, fixture entries and pool tasks that reach the changed lines for tests that loop over thousands of inputs; saves CPU and lets heavy tests (tpcds, max_nodes) be run instead of printed.
7. **Exact line sets / token digests for the few long tests** (re-tokenize recorded inputs after tokenizer edits) so a heavy test is selected only when it truly reaches changed code. Our heavy-candidate handling costs ~25 s per heavy commit.
8. **DuckDB answer replay and lazy executor tables**: record SELECT -> answer on the pinned tree so executor/tpcds tests rarely start real DuckDB; verify at image build with rehearsal and keep a fallback.
9. **Key-level / attribute-level read maps and importer cones** for import-time edits (we have class-touch and modtouch maps; theirs narrow to the changed key; fewer 1000-test candidate sets).
10. **Fixture-only commits share one child** and restore state; also treat tests that leave interpreter-wide state changed (watched containers) with a readers map.
Hold as policy: do not copy the probe (flagged above). Independence: build each of these from our own tracing code and data formats; do not reuse names, file layout or constants from nap-time.
