# Stored context packs are readable until a declared cutoff

Every `coach_reports` row stores the `context_pack` its report was generated from. That pack is not a log line: the offline eval harness re-parses it, months or years later, to score the report against the rubric, and any future reader that wants to know what the coach actually saw for a given run has to read it back through `CoachContextPack`.

The pack schema keeps evolving, and every model in it is `extra="forbid"`. So a stored pack can stop parsing in either direction: a section that is now required and did not exist when the row was written, or a field that was written and has since been renamed away. Measured on a 300-activity production snapshot (#810), 26 of 131 stored packs no longer parsed, and every one of them was written under `coach_report_v1`, the very first prompt id, from before the pack had `discount_signals`, `adherence`, `longitudinal`, or the ADR 0026 groups.

Nothing crashed. The harness caught each failure per row and filed it as an error, so a run degraded quietly. The condition was neither tolerated nor declared. It was accidental.

## Decision

**`extra="forbid"` stays, and a stored pack's readability has a declared cutoff.**

A stored pack is expected to strict-parse under the current schema unless it was written under one of the prompt ids named in `UNREADABLE_PACK_PROMPT_IDS` (`backend/app/schemas/coach_context.py`), which predate the pack shape settling. Those rows keep their stored pack verbatim. They are not migrated, not rescued by widening the schema, and not deleted. Every reader that re-parses a stored pack goes through `load_stored_pack(data, prompt_id=...)`, which raises the distinct `StoredPackUnreadable` for a row past the cutoff and re-raises the underlying `ValidationError` for anything else.

That gives three outcomes where there used to be two, and the third is the one that was missing:

1. The pack parses. The report is scored.
2. The pack does not parse and the row is past the declared cutoff. It is **settled history**: counted in the scorecard's `skipped_unreadable_pack`, listed with its prompt id and rejection reason in `unreadable_packs`, and scored not at all.
3. The pack does not parse under any other prompt id. It is **live drift**, and it stays a loud error.

A row in bucket 2 or 3 contributes no rubric assertions whatsoever, so an unreadable pack can never be confused with a report that parsed and scored badly.

The census is a figure the scorecard always states, including when it is zero, and `compare_scorecards` treats a rise in it as a regression. The set is a declaration, not a prediction: a `coach_report_v1` row whose pack does happen to parse is scored normally. The set only says which prompt ids are *allowed* to be unreadable.

## What was rejected

**Widening the schema to tolerate every shape ever written.** This is the expensive option disguised as the kind one. `extra="forbid"` is what makes pack drift visible in *both* directions, and a large part of the coach-pack discipline is built on that visibility: the byte-stable-drop registry, the import-time descriptor checks, the diagram drift guard, the grouped/flat equivalence proof. Loosening it, or defaulting every newly-required section so an old pack slides through, would buy back a closed set of 26 historical rows and pay for them forever with a schema that no longer says what a pack is. Worse, the rescued parse would be a lie: a pack that "parses" only because `adherence` defaulted to empty does not tell a future reader that the coach saw no adherence signal, it tells them nothing at all, and the eval harness would then score those reports against sections the coach never had.

**A one-off migration of the stored packs.** Rewriting stored `context_pack` JSON to the current shape would destroy the only record of what the coach was actually handed for those runs, in order to make a number go to zero. The pack is evidence. Backfilling `discount_signals` into a report generated before the discount stage existed would manufacture a fact.

**Doing nothing, on the grounds that `coach_report_v1` is long retired.** True but not sufficient. The cost of the status quo is not the 26 rows, it is that nothing distinguishes them from a schema regression introduced tomorrow. Under the declared cutoff, a newly-unreadable pack under a live prompt id is immediately loud.

## The census as measured

Re-measured on a seeded local snapshot of production (`make seed-local`) on 2026-08-19, through `load_stored_pack`:

| | |
|---|---|
| rows carrying a stored pack | 133 |
| readable under the current schema | 124 |
| unreadable, past the declared cutoff | 9 (all `coach_report_v1`) |
| unreadable, **not** covered by the declaration | 0 |

The nine rejections fall into exactly the three shapes the issue reported, and nothing at all fails outside the declared set. Eleven further `coach_report_v1` rows in that same snapshot parse fine, which is why the declaration names prompt ids that are *allowed* to be unreadable rather than predicting which rows will be. This is a different snapshot from the one in the issue (131 / 105 / 26), so the counts differ; the classification does not.

## Consequences

- The declared set is expected to stay closed. Adding a prompt id to `UNREADABLE_PACK_PROMPT_IDS` is a deliberate act that says "packs from this era are being retired from readability", and it should be rare.
- A future schema change that breaks a pack written under a *current* prompt id fails as an error, not as a count. That is the intended asymmetry.
- The rows stay in the database, unmodified, and still serve every other purpose: the report itself, the digest, the meta, the raw LLM response.
- `services/coach/voice_probe.py` also re-parses stored packs, and silently skips a row it cannot parse. That is deliberate for a probe (it is choosing baselines, not taking a census) and is left alone.
