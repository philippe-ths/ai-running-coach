# Offline coach-report eval harness (M5)

The eval harness is the deterministic quality gate for the coach report. It
scores coach reports against a human-authored rubric and produces a repeatable
scorecard, so "better than a cold AI" and "gets better over time" become
measurable and preference-drift becomes visible. The long-horizon learning
milestones (M7-M10) are gated by it: nothing that makes the coach adapt over
time ships until its output can be measured here.

It is deliberately **deterministic** in v1. An LLM-judge would reintroduce the
very drift the gate exists to catch, so every assertion is plain code. An
LLM-judge is the documented upgrade path, not a v1 requirement.

## The rubric (the oracle for coach reports)

Each report is scored independently from its own `report` content plus its
stored `context_pack` (no cross-row joins, no re-read of mutable analysis
state), so the score is order-independent and byte-stable. Eight assertions,
each PASS / FAIL / NOT_APPLICABLE:

| Assertion | Asks | NOT_APPLICABLE when |
| --- | --- | --- |
| `led_with_headline` | Did the report open with a headline verdict (N3)? | a prose message with a degraded tail (no headline label to assess) |
| `discounted_inflated_hr` | When a confound fired (`discount_signals.likely_inflated_by`), did the report name it / discount the HR drift rather than read it as fatigue? | no concrete confound fired |
| `no_medical_overreach` | Did it stay in the coaching lane? Reuses the production policy gate (validator rule 5) verbatim. | never |
| `advanced_not_parroted` | When a prior report exists in the pack, did this one advance the narrative instead of restating it (lead/next-step overlap below threshold)? | no prior report in the pack |
| `abstained_on_thin_trend` | When the matching `RunnerBaseline` bucket is still abstaining (`longitudinal.baseline_trend is None`), did the report avoid asserting a like-for-like trend? | never (PASS when grounded or correctly silent) |
| `framed_for_adherence` | (M10) When the runner has a decisive preference profile, are the `next_steps` not confined to themes they demonstrably ignore while offering nothing in a theme they act on? | no decisive profile, or no `next_step` classifies into a known theme |
| `load_not_framed_as_intensity` | (#168) Is the cumulative `effort_score` load number not narrated as an intensity verdict? It grows with duration, so intensity must come from the HR-derived `effort` axis / RPE, never from the load number. | the report does not narrate the effort score in prose |
| `coached_not_caveated` | (#171) When per-rep interval data is present (`interval_structure`), does the report coach it instead of leading with a low detection-confidence caveat, and avoid advising the lap button when the laps were recorded (`source == "recorded_laps"`)? | no per-rep data; or detection confidence is high and the structure is not recorded-lap sourced |

Assertions 2, 4, 5, 7 and 8 inspect free text with documented keyword / overlap
heuristics (see `app/services/coach/eval/rubric.py`). They are the deterministic
floor, not a semantic judge; the parrot-overlap threshold is the single tunable
constant.

## Known blind spots (the gate is necessary, not sufficient)

The deterministic heuristics catch crude and verbatim violations. They do **not**
catch the realistic, reworded ones. Treat a green scorecard as "no detectable
regression," not "the report is good." These gaps are the LLM-judge upgrade
target and are pinned by tests in `TestKnownBlindSpots` so they stay visible:

- **Semantic parroting escapes** (`advanced_not_parroted`). The check is lexical
  word-overlap, so the same advice fully reworded scores low overlap and PASSES.
- **Keyword-free trend claims escape** (`abstained_on_thin_trend`). A like-for-like
  trend claim phrased outside the keyword list ("your aerobic base is bigger now
  than before") PASSES on an abstaining bucket.
- **Confound-named-but-correct discount is only approximated** (`discounted_inflated_hr`).
  The check requires a fired-confound mention AND discount language to co-occur;
  it cannot verify the discount is logically applied, only that both signals are
  present.
- **Spelled-out drug doses escape** (`no_medical_overreach`). This reuses the
  production validator verbatim (single definition, deliberately), so it inherits
  the validator's digit-based dose pattern: "five hundred milligrams" is not
  caught.
- **Cross-sentence load-vs-intensity misframes escape** (`load_not_framed_as_intensity`).
  The check fires only when an explicit `effort score` reference and an
  intensity-verdict phrase ("moderate intensity", "recovery territory", "intensity
  threshold") co-occur in the **same** sentence without a not-intensity disclaimer.
  A misframe split across two sentences, or phrased outside the verdict-term list,
  PASSES. The prompt-side rule 22 (`coach_report_v8`) is the actual fix; this
  assertion is the regression floor, not a complete guard.
- **Caveat-led framing outside the keyword list escapes** (`coached_not_caveated`).
  The check fails only when the **lead** (headline / thesis / lead_argument) carries
  an explicit uncaptured/unreliable phrase, or a `lap button` mention co-occurs with
  recorded-lap structure. A session framed as uncaptured through paraphrase, or a
  bounded caveat that nonetheless dominates the report's tone, PASSES. The prompt-side
  rule 23 (`coach_report_v9`) is the actual fix; this assertion is the regression
  floor, not a semantic judge of whether the report truly led with the rep data.
- **Regression detection is rubric-sensitive.** `--compare` flags drops in the
  dimensions the rubric can see. A prompt change that introduces a failure mode
  the rubric is blind to (e.g. semantic parroting) will not move the score.

## Running it

```bash
# validate the harness itself against its synthetic good/bad fixtures
# (no DB, no API key — safe in CI)
make eval-selftest

# score the coach reports already in the local DB (current version only)
make seed-local SEED_ARGS="--activities 20"   # real data, prod is read-only
make eval

# write a scorecard, then later flag regressions against it
make eval EVAL_ARGS="--output before.json"
make eval EVAL_ARGS="--compare before.json"   # exit 1 if any pass rate dropped
```

Exit codes: `0` ok, `1` self-test failed or a regression was detected, `2` no
reports could be scored.

## The 15-20 activity freeze (the real-data run)

The harness scores the current `(prompt_id, schema_version)` only — exactly the
versioned-cache identity from M0 — so a scorecard is comparable across prompt
and model iterations.

Production reports predate the current schema (older pack shape), so they do not
parse and are not scored. The scorecard says which kind of not-parsing it was
(#810, ADR 0032): a pack written under a prompt id past the declared readability
cutoff is counted in `skipped_unreadable_pack` and listed in `unreadable_packs`,
settled history rather than a fault; any other parse failure stays in `errors`,
which is live schema drift and should be investigated. Neither contributes a
single rubric assertion, so an unreadable pack is never mistaken for a report
that scored badly, and `--compare` flags a RISE in the unreadable count as a
regression.

To take the census over the whole database rather than one version:

```bash
make eval EVAL_ARGS="--all-versions --output /tmp/census.json"
```

The `skipped_unreadable_pack` figure in that scorecard is the answer to "how many
stored packs no longer parse", and `unreadable_packs` names each one with its
prompt id and the first line of the rejection.

To score **real** reports you regenerate them under the current code first:

```bash
make seed-local SEED_ARGS="--activities 20"          # freeze ~20 real activities
cd backend && .venv/bin/python -m scripts.reanalyze_all   # rebuild metrics from real streams
cd .. && make eval EVAL_ARGS="--regenerate --activities 20"  # regen v2 reports, then score
```

`--regenerate` makes real Anthropic calls (needs `ANTHROPIC_API_KEY`); keep
`--activities` bounded to control spend. `reanalyze_all` is free (it re-runs the
deterministic pipeline over the streams already seeded) and is required first so
the regenerated context packs carry the current metrics (axes, `discount_signals`)
and the M4 longitudinal contrast.

## Scoring the A3 prose-message family (schema 2.0)

The harness scores both output shapes (ADR 0009). The rubric extractors
(`_report_text` / `_assertive_text` / `_lead_text` / `_forward_words`) and field
accessors branch over the `CoachReportContent | CoachMessageReport` union, so the
eight assertions are written once over a uniform surface:

- For the prose message the **lead** is its opening sentence (the verdict the
  prompt asks it to lead with), the **assertive** surface is the whole message
  body plus the tail's next_steps/risks (not the tail's questions), and the **full**
  surface is the message plus every tail text field.
- `no_medical_overreach` runs `validate_message_policy` for the prose shape (the
  same rule 5 over message + tail).
- `led_with_headline` is `NOT_APPLICABLE` on a degraded tail (the headline label
  lives in the tail; the prose still led with a verdict).

The loader parses 2.x rows as `CoachMessageReport`, `make eval` defaults to
whichever family the active prompt selects (`active_schema_version`), the scorecard
carries a `tail_degraded` count (operational health: a real message with no usable
tail), and `make eval-selftest` validates a good + deliberately-bad fixture for
**each** shape (`known_good_message_report` / `deliberately_bad_message_report`).

Under the A4 two-stage Exchange (ADR 0010) the harness scores the **fuller turn
only**: an opener-only row (an `opener_message` with an empty `message`, the fuller
turn not yet landed) is a not-yet-complete exchange and is skipped before scoring,
counted in the scorecard's `skipped_opener_only` field (operational health, like
`skipped_fallback`, not a rubric assertion). The skip keys on the row's own report
JSON (`is_opener_only`), preserving the harness's self-containment — no coupling to
the Activity notification sentinels.

The prose lexical floors are re-baselined for prose at cutover: the parrot-overlap
and trend/load/caveat keyword lists are unchanged, but the prose surfaces they scan
are larger, so the realistic-paraphrase blind spots above are wider for the message
shape — treat a green scorecard as "no detectable regression" even more cautiously
until the #164 LLM-judge lands.

### A3 cutover rehearsal (the done-decision evidence)

The cutover from `coach_report_v10` (1.2) to `coach_message_v1` (2.0) is a config
flip (`COACH_PROMPT_ID`), gated by an eval compare on seeded regenerated data. This
step makes **real Anthropic calls** and needs owner-authorised spend:

```bash
make seed-local SEED_ARGS="--activities 20"               # freeze ~20 real activities
cd backend && .venv/bin/python -m scripts.reanalyze_all   # rebuild metrics from real streams
# baseline the active structured family first
cd .. && make eval EVAL_ARGS="--regenerate --activities 20 --output v10.json"
# flip to the prose family and regenerate + score
COACH_PROMPT_ID=coach_message_v1 make eval EVAL_ARGS="--regenerate --activities 20 --output msg.json"
COACH_PROMPT_ID=coach_message_v1 make eval EVAL_ARGS="--compare v10.json"   # no assertion-level regression
```

A clean compare (no assertion-level regression vs the v10/1.2 baseline) plus a
`tail_degraded` count at or near zero is the A3 done-decision evidence. Rollback is
the inverse flip: setting `COACH_PROMPT_ID` back to `coach_report_v10` re-serves the
cached v10 rows with zero code change (the stored rows read back in their own
schema-version family).

## Why each layer is trustworthy

- **Rubric logic** is unit-tested against hand-authored synthetic fixtures
  (`app/services/coach/eval/fixtures.py`, trust level 5, clearly marked NOT
  production data): a deliberately-bad report must fail every dimension, a
  known-good report must pass. This is the brief's "validate the harness itself"
  check, exposed as `make eval-selftest`. Note the fixtures are co-designed with
  the heuristics, so the self-test proves the wiring fires, not that the rubric
  catches every realistic bad report (see Known blind spots above).
- **The DB-load path** is covered by an in-memory-SQLite integration test that
  seeds `CoachReport` rows and runs the full harness.
- **The real-data path** is exercised via `make seed-local` + `reanalyze_all` +
  `--regenerate`, scoring reports generated by the live LLM over real activities.
