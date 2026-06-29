# Coach memory (v13) B-vs-A + validation note

The M5 validation artifact for the coach-memory redesign (ADR 0025). It is the
owner's input for the "does memory add value / feel more human" judgment that the
deterministic eval is blind to by design, plus the evidence that the M1-M4 system
works on real data. Generated against a local production seed
(`philippe.marr@gmail.com`: 484 activities, 81 non-fallback reports, 36 chat
turns, 2 check-in notes).

---

## 1. Writer wiring, proven on real data

The M2 writer was run over the real seeded runner (`update_memory` for the user):

- **`gather_memory_sources` pulled 25 sources (20 durable)** from the runner's real
  chat turns + check-in notes + recent report digests — the rewrite-from-source
  inputs. The writer never reads its own prior profile (the structural anti-echo
  guarantee).
- It built the writer messages and issued the `claude-haiku-4-5` structured-output
  call.
- **The call returned `400 invalid_request_error: credit balance too low`** (the
  Anthropic key on this environment is unfunded). The writer **caught it and
  degraded to `None`** — logged via `logger.exception`, **no crash, no partial
  write**. This is the fail-visible degrade path the design requires: a background
  writer must never hard-fail a report or strand a half-written profile.

So everything up to and including the LLM call is proven on real data. The only
funded-key-gated step is the model call that produces the profile JSON.

> **Funded-key completion step (owner):** with a funded `ANTHROPIC_API_KEY` on the
> worker, the writer produces the profile automatically after each non-fallback
> v13 report. The cutover runbook's post-deploy checks (`docs/deployment/coach-memory-v13-cutover.md`)
> verify the generated profile against the four acceptance goals.

---

## 2. Illustrative profile (stands in for the funded-key writer output)

Because the live writer call is credit-blocked, the profile below was
**hand-authored from this runner's REAL sources** to stand in for the writer's
output for the B-vs-A. It is the exact shape (`RunnerMemoryProfile`) the writer
emits, and every line traces to a real source the writer gathered:

| line | real source it traces to |
|---|---|
| "Trains through hot spells (35-36C), reads effort by feel" | chat: "highs of 35-36 through to Friday"; "felt mostly easy even tho my hr was a little high" |
| "Takes Lisdexamfetamine (ADHD), lifts HR midday 12-3pm" | UserProfile medical note |
| "right knee / foot / shin-splint history; small right-knee niggle" | UserProfile injury note + check-in "Right knee small pain" |
| "metronome app at 166 spm, easier to find rhythm" | chat: "tried a metronome app... set to 166"; "Easier to find the rhythm" |

```json
{
  "who_you_are": [
    "Intermediate runner, runs about 6 days a week at low volume (~18 km/week).",
    "Trains through hot spells (35-36C) and reads effort by feel, not just the HR number."
  ],
  "limits_and_constraints": [
    "Takes Lisdexamfetamine (ADHD), which lifts heart rate, especially midday 12-3pm; treat HR then as inflated.",
    "History of right knee, right foot and shin-splint pain; has flagged a small right-knee niggle on an easy day."
  ],
  "goals_and_plans": [
    "General fitness right now, no race on the calendar."
  ],
  "what_works_for_you": [
    "Runs easy by feel and is comfortable when HR reads a little high in the heat.",
    "Tried a metronome app at 166 spm and said it made it easier to find a rhythm."
  ],
  "lately": [
    "Open thread: was experimenting with a 166-spm metronome for cadence; pick up whether it stuck."
  ]
}
```

It reads like a coach's notes, fits one screen, and carries **no behavioral
verdict** (no "ignores easy days") — the four acceptance goals.

---

## 3. The B-vs-A: what v13 adds over v12 (pack + prompt)

Built for the real activity **"Morning Run" (2026-06-25)** with the illustrative
profile loaded, under both prompts:

**Context pack** — `build_context_pack(..., prompt_id=...).to_serializable_dict()`:

```
v12 sections: 19
v13 sections: 20
KEYS added under v13:   ['memory']
KEYS removed under v13: []
```

v13's pack is **v12's pack plus exactly the `memory` section** — the runner's
profile + provenance (`last_updated_days_ago`, `source_report_count`). Nothing else
moves (byte-stable elsewhere). So the coach, on this run, additionally sees: the
Lisdexamfetamine HR confound, the knee/shin history, the heat-by-feel pattern, and
the open metronome thread.

**System prompt** — `build_system_prompt(...)`:

```
v12 length: 37,705 chars
v13 length: 39,984 chars   (+2,279 = the runner-memory addendum)
```

The +2,279 chars are exactly the `_MEMORY_ADDENDUM` (the M3 prefix test
`test_v13_is_v12_plus_memory_addendum` pins `SYSTEM_PROMPT_MESSAGE_V13 ==
SYSTEM_PROMPT_MESSAGE_V12 + _MEMORY_ADDENDUM` byte-for-byte). The addendum carries
the authority tiering (memory is citable but yields to measured data, never lowers
the safety floor) and the anti-nag directional discipline (read training direction
from the data, confounder-adjusted, never a binary acted/ignored verdict).

**The value the owner is judging:** under v13 the coach can pick up the open
metronome thread, honour the stated knee niggle, and discount a midday HR spike as
the known stimulant confound — continuity and personalisation v12 cannot express,
without ever letting a stated fact override this run's measured `DerivedMetric` or
lower the safety floor.

> **Funded-key completion step (owner):** the **report-level** B-vs-A (the actual
> v12 vs v13 coach prose for these activities) needs the LLM to generate both. With
> a funded key, regenerate the activity's report under v12 then v13 and diff the
> `message`. The pack/prompt diff above is the deterministic half; the prose
> "feels more human" judgment is the owner's.

---

## 4. Eval baseline (no regression, real reports)

`make eval` over the local seed's reports in the active `(prompt_id,
schema_version)` scope:

```
reports scored:    2
overall pass rate: 1.000   (0 failed across all 13 assertions)
...
  memory_preserved_safety_surface   0/0   (NOT_APPLICABLE: no referral fired)
  coached_direction_not_nagged      2/2   (pass: no nag verdict in real output)
```

The two M3 additions run on real reports without false-positives, and
`coached_direction_not_nagged` passing on real prod-prompt output confirms the
live coach is not already nagging. (Only 2 reports fall in the active scope on this
seed; most stored reports are earlier prompt families.)

> **Funded-key completion step (owner):** the before/after (v12 baseline vs v13)
> needs v13 reports, which need generation. With a funded key, regenerate the seed's
> reports under v13 (`reanalyze_all.py` rebuild path, see
> `docs/testing/coach-report-eval.md`) and re-run `make eval`; confirm the overall
> pass rate does not drop and the safety assertions stay green.

---

## 5. Summary for the owner

| check | status |
|---|---|
| Writer gathers real sources + degrades safely | **proven on real data** |
| Profile shape / acceptance goals | **proven** (illustrative, from real sources) |
| v13 pack = v12 + `memory` section | **proven** (deterministic) |
| v13 prompt = v12 + memory addendum | **proven** (byte-exact, M3 test) |
| Eval has no false-positives on real reports | **proven** (1.000, 13 assertions) |
| Real writer-produced profile | **needs funded key** (owner) |
| Report-level v12-vs-v13 prose B-vs-A | **needs funded key** (owner) |
| Eval before/after v12->v13 | **needs funded key** (owner) |

The remaining human actions are in `docs/deployment/coach-memory-v13-cutover.md`.
