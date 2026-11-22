# Builders Buildathon 2026

My entries for the Builders Buildathon (Nov 20–22, 2026): four tracks, one weekend, a few hundred teams per track. Each folder holds one track's entry. The three track folders keep their full commit history, so you can follow every experiment.

| Track | What it is | Folder | Stack | Best practice score |
|---|---|---|---|---|
| Mech Royale | Bot for a 16-mech battle royale over a 50 ms-per-turn protocol | [`mech-royale/`](mech-royale) | Python | 5.80 avg placement points (1.15 baseline) |
| Shopping Sessions | Turn tens of millions of raw shopping events into sessions and daily user stats, as fast as possible | [`shopping-sessions/`](shopping-sessions) | Rust | 0.88 s (1.37 s on the first correct submission) |
| Blast Radius | Pick the tests to run for each commit of a real codebase (sqlglot) to minimise CI time without missing failures | [`blast-radius/`](blast-radius) | Python | 200 simulated CI seconds (1,614 baseline) |
| CAD De-ID | De-identify a large CAD and PDF corpus while keeping engineering data intact | [`cad-deid/`](cad-deid) | Python | 446 / 1000 (292 baseline) |

## Highlights

**Mech Royale.** The bot started as a simple Python bot (2.4) and grew through about 30 scored versions to 4.9: laser-first upgrades, bullet dodging with boost, retreat and heal rules, and supply-drop valuation. The final bot builds on an open-source MIT Go bot (credited in its LICENSE), ported to Python and extended. It adds exact line-of-sight from the arena geometry, a precomputed navigation cache, resumable cover search under a strict per-turn time budget, and seed-salt variation, so repeated runs explore different random choices.

**Shopping Sessions.** A single static Rust binary in a `FROM scratch` image:
- profile-guided optimisation;
- aligned `pread` I/O, which cut system time from 2.5 s to 0.8 s;
- AVX2/`pext` quote validation;
- dense ID tables built to work with keyed, opaque IDs;
- dictionary/RLE Parquet output.

Runtime went 1.37 s → 1.07 → 1.03 → 0.88 s, and every submission passed the correctness gate.

**Blast Radius.** A test selector that proves when a change cannot matter instead of guessing:
- comparison-outcome maps that show an operator or literal edit cannot change any test result;
- import-time and import-closure maps;
- canary tests;
- tests run in forked children;
- CPU-time limits that make decisions deterministic.

It went from 1,614 to 200 simulated CI seconds with zero missed failures on every labelled mutant.

**CAD De-ID.** A pipeline over OLE/CAD files and PDFs:
- HMAC-based, length-preserving pseudonyms that stay consistent across files and formats;
- title-block and dictionary identity discovery;
- glyph-level PDF redaction;
- clustering logos by perceptual hash and replacing them;
- scrubbing OLE free sectors.

Later attempts were staged as diffs and assembled with server-side cloud-storage copies, so only changed files were re-uploaded. Score went 326 → 377 → 385 → 446.

## How I worked

I ran it as an experiment loop: hypothesis → scored submission → keep or drop, logged for every change. I built tooling for scored submissions, leaderboard snapshots and seed lotteries, and used parallel AI coding agents for implementation and local test runs.

## License

MIT. Third-party notices are kept in each folder's LICENSE where they apply.
