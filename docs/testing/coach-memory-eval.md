# Runner-memory eval harness (#658)

The durable-memory counterpart to the coach-report eval (`docs/testing/coach-report-eval.md`).
It scores a runner-memory WRITE against deterministic ADR 0025 assertions, so a
change to the memory writer (`services/coach/memory_update.py` — its sources,
prompt, or graduation) has a repeatable quality gate instead of a one-off human
read of the resulting profile.

## What it scores

The memory writer is an LLM (`claude-haiku-4-5`) that rebuilds the whole profile
from source each pass, so its behaviour cannot be pinned by deterministic unit
tests. The unit the harness scores is the triple:

- **`sources`** (`MemorySources`) — what the writer was handed this pass.
- **`candidates`** (`list[MemoryCandidate]`) — the writer's RAW proposed lines,
  before graduation, each carrying its `supporting_source_ids` citations and the
  `safety_relevant` flag. Provenance lives here.
- **`profile`** (`RunnerMemoryProfile`) — what `apply_graduation` stored.

The stored profile keeps only line text (graduation discards provenance), so the
grounding sensors read the candidates, matched back to a profile line by
`(section, text)`. `candidates` are the memory analogue of the coach-report eval's
LLM `content`; `sources` the analogue of its `pack`.

## The rubric (deterministic v1)

`app/services/coach/eval/memory/rubric.py`. Each returns PASS / FAIL /
NOT_APPLICABLE, reusing the coach-eval `AssertionResult` vocabulary.

| Assertion | ADR 0025 rule | What it catches |
|---|---|---|
| `no_inferred_verdict` | Rule 1 | An inferred behavioural verdict about training compliance ("ignores easy days") — the rest-day-fixation floor. Narrow, high-precision markers, so a legitimately STATED constraint ("no morning runs") never trips it. |
| `durable_lines_grounded` | Rule 6 (anti-echo) | A durable character/limit/preference line that rests only on the coach's words, or on no candidate at all — the coach's opinion echoed as the runner's stated fact. |
| `plan_from_commitment` | — (#657 regression) | A `goals_and_plans` line with no runner commitment behind it (a coach proposal, or an option the runner only weighed, graduated as a plan). |
| `safety_limit_held` | Rule 4 | A grounded `safety_relevant` limit dropped from `limits_and_constraints` instead of being held on first mention. |

### Deliberately deterministic

An LLM judge would reintroduce the drift the gate exists to detect. The semantic
judgments #658 also lists — a genuine *elliptical* commitment ("yeah, do that")
IS captured, a *reworded* verdict, and newer-supersedes-older — need a judge to
score reliably and are a documented opt-in follow-up (mirroring the coach eval's
off-by-default `judge.py`), not part of this deterministic core.

## Running it

```
make eval-memory-selftest      # validate the rubric against its fixtures (no DB, no key)
make eval-memory EVAL_MEMORY_ARGS="--scan"   # score stored runner_memory profiles (needs a seeded DB)
```

- **Self-test** (`--self-test`): validates the rubric against the synthetic
  good/bad fixtures in `fixtures.py`. The good fixture must fail nothing; the bad
  fixture must FAIL every assertion. This is the inverted-oracle gate — it proves
  each assertion can both pass a clean write and catch its violation. CI-safe.
  Also exercised by `tests/test_memory_eval.py` in the normal suite.
- **Scan** (`--scan`): runs the profile-only verdict floor (`no_inferred_verdict`)
  over every stored `runner_memory` profile. Only the floor applies, because a
  stored profile has no candidates/sources on hand. Seed real data first
  (`make seed-local`).

## Why each layer is trustworthy

- The fixtures are SYNTHETIC (ground-truth trust level 5), co-designed so the
  inverted-oracle self-test is meaningful: the bad fixture trips each assertion by
  exactly one authored violation.
- The grounding sensors re-derive support from the current source set (the same
  contract `apply_graduation` enforces), so a hallucinated citation contributes
  nothing.
- The verdict floor reuses the same narrow, high-precision-over-recall discipline
  as the coach report's `coached_direction_not_nagged` sensor.

## Follow-ups (not in #658's first cut)

- An opt-in LLM-judge layer for the semantic assertions above.
- A `--regenerate` path that runs the real writer over a conversation fixture set
  and scores the full triple, mirroring `eval_coach_reports.py --regenerate`
  (this is the generalisation of #657's real-conversation fixtures into the
  harness).

Related: ADR 0025 (`docs/adr/0025-runner-memory-is-a-rewritten-profile.md`),
#657 (the writer change this harness guards).
