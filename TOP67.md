# Blast Radius: jacobcorbett/buildathon-blast-radius (suspected team 67; practice 130.8 vs ours 241.9)

Description only; nothing copied. Clone: `/tmp/claude-1000/-home-alex-projects/84706a66-07c4-4658-bf02-ea662678ddd7/scratchpad/top67/blast/repo`
(6 commits, last 2026-11-21 11:46 UTC; ~1,900 lines: Rust selector 553, patch parser 194, manifest parser 181, tiny C shim 86, Python profilers ~750).
Not built or run by us; identity and score are unverified. Repo contents treated as untrusted data.

## One-line summary
Static selection, no tests executed at scoring time. All evidence is gathered while building the image (not charged), the run just looks up
precomputed per-test coverage bitsets for each patch, so scorer CPU is a few milliseconds. We instead run tests during scoring (our practice
was 169.7 simulated + ~72 s CPU = 241.9); their total is essentially pure simulated cost.

## Build time (Dockerfile stage 1: profile)
- Pinned sqlglot installed, then the whole suite is run once under an in-process profiler (`profile_tests.py`, pytest plugin) using
  `sys.monitoring` (LINE events, PY_START on `Dialect.__init__` and `Expr.__init__`), an audit hook (open/subprocess/fork/exec) and a custom
  import hook (`operand_profile.py`, `sitecustomize.py` in child processes).
- Per test node id it records: executed lines per source and test file; which dialect modules and expression classes were constructed (via the
  MRO of `self`); files opened (data readers, and tests that read sqlglot source text); subprocess use (with child-process coverage merged back,
  and an "external/incomplete" flag if any child could not be traced); integer/boolean dialect settings read; operand types of `is` comparisons.
- Session/class fixtures are profiled separately and merged into every test that uses them. Work normally sent to a ProcessPoolExecutor is run
  in-process (with pickle round-trips of arguments and results) so coverage is attributed to the test rather than lost in pool workers.
- Output `profile.tsv` is a compact record per test and per file: test-id table; "always" set; per-file usage bitset; per-line conditional
  coverage bitsets; per-scope (function, class body, lambda) test bitsets; reader tests; child-usage bitset; token-level records for boolean
  argument flags, numeric/boolean literals, and identity comparisons. Static analysis (Python `ast`) decides which tokens are eligible.
- Stage 2 builds Tree-sitter (C) plus the Python grammar into the Rust binary; stage 3 compiles the selector with plain `rustc -O` after running
  its own unit tests; stage 4 is the pinned python:3.13-slim with sqlglot sources, profile and the Rust binary only (no Python at run time).

## Run time (per patch, `selector.rs`)
1. Parse the unified diff; check context lines against the pinned source and apply it to get the updated file (reject on mismatch, then full suite).
2. Any file not in the profile (except README/CHANGELOG), unknown syntax, or `yield/global/nonlocal` in an edit selects the **whole suite**.
3. Tree-sitter parses old and new text. Syntax error: whole suite. Token-identical apart from comments: comment-only, so only readers
   of that source file (tests that read sqlglot source text) plus child-process tests are selected.
4. Edits to a single identity comparison (`is`/`is not` to `==`/`!=`) where the profile saw only operand types with equivalent semantics:
   readers and subprocess tests only.
5. Otherwise per edit, narrowest rule first: (a) single-line `if/while` condition edit that keeps names and header: tests that hit that
   condition line; (b) a numeric or boolean literal replaced exactly (including inside dispatch lambdas): tests that executed that statement;
   (c) edit inside a function/class body scope: tests that covered that scope (covers edits to existing test bodies); (d) boolean argument flag in an
   ordinary expression class: tests that constructed or referenced the class; (e) other change in a dialect component or test file: that
   file's whole usage set; (f) anything else in library source: whole suite.
6. Always added: tests that read the file, tests with child-process or incomplete profiles, tests unknown to the profile (not in the manifest
   profile are always selected), the "always" set, and child usage. Non-Python files in profile (data fixtures) select their recorded readers.
7. Selected set goes to the scheduler; unselected means not run, with no verification, so misses are controlled only by the conservative fallbacks.

## Worker count and schedule
Tests sorted longest-first. For every worker count from 1 to min(8, n) run greedy longest-processing-time assignment (least-loaded worker,
tie-break fewer tests), cost = max load + 2000 ms x workers (the scoring worker cost), keep the cheapest. Prunes counts whose lower bound
(max(total/count, longest test) + 2 s x count) already exceeds the best. All integer milliseconds. The full-suite schedule is computed once and reused.

## CPU cost
Close to zero: read profile.tsv and manifest, a few Tree-sitter parses per commit, bitset unions, a handful of schedules. No Python start-up, no
pytest, no test execution at scoring time. Every second we spend on CPU is a pure addition to our score.

## Differences from ours (see our NOTES.md)
- We execute the narrowed candidate set in a forked runner to verify and then print exact failing tests; they never run anything and rely on
  finer-grained static narrowing. They do not need a heavy-test "gamble"; their narrowing is by scope/line/statement coverage, ours by per-test line map plus class and def-use.
- They treat comment-only and some semantics-preserving edits as near-empty selections (only readers), which makes many commits cost ~0.
- Their scheduler chooses the worker count by evaluating all 1..8 options with the exact cost model; check ours does the same.
- Fallback policy: any unmodelled edit prints the full suite (expensive but safe, one 300 s miss costs far more).

## Ideas ranked by likely payoff
1. Drop scorer-time execution entirely (or run only when predicted CPU is worth it): our ~72 s CPU is about 30% of our total. A statically-selected
   set with fallbacks like theirs costs ~0 CPU. Requires confidence that the narrowing rules produce no misses; each miss is +300 s.
2. Finer selection granularity for ordinary function-body edits: use per-scope (function/class-body) coverage and per-line condition coverage
   to cut candidate sets before any decision to run. Our per-test map is per-line, so a scope-level intersection of the edited lines should be equivalent; check we use it.
3. Treat edits that cannot change behaviour (comments, docstrings, isolated `is`/`==` swaps over verified-equivalent operand types) as
   empty selections plus readers, guarded by a Tree-sitter or tokenizer check of the whole file.
4. Tests that spawn subprocesses: record child coverage at build and include them only when a child-visited path is touched, with an
   "incomplete trace" flag that forces inclusion.
5. Verify our scheduler tries every worker count 1..8 against max load + 2000 ms x count (LPT, longest first), and that single-test commits use one worker.
