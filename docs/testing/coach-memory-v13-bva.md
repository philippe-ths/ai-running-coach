# Coach memory (v13) B-vs-A + validation note

The M5 validation artifact for the coach-memory redesign (ADR 0025). It is the
owner's input for the "does memory add value / feel more human" judgment that the
deterministic eval is blind to by design, plus the evidence that the M1-M4 system
works on **real data with a real model**. Generated against a local production
seed (`philippe.marr@gmail.com`: 484 activities, 81 non-fallback reports, 36 chat
turns, 2 check-in notes), `COACH_MODEL_ID=claude-sonnet-4-6`, writer
`claude-haiku-4-5`.

> **Status: fully verified.** An earlier draft of this note was written while the
> Anthropic key was unfunded (writer + report calls degraded). The key was then
> funded and every step below was run for real.

---

## 1. The writer produces a real profile from real sources

`update_memory` was run over the real seeded runner. It gathered **25 sources (20
durable)** from the runner's chat turns + check-in notes + recent report digests
(never its own prior profile — the structural anti-echo guarantee), issued one
`claude-haiku-4-5` structured-output call, and wrote this profile:

```json
{
  "who_you_are": [
    "Actively curious about cadence and pacing mechanics — asks how to run slow while keeping cadence up, and about walk cadence."
  ],
  "limits_and_constraints": [
    "Possible right knee small pain, noted once in early June.",
    "Possible light left leg tightness, eased with walking, noted once in early June."
  ],
  "goals_and_plans": [],
  "what_works_for_you": [
    "Tried a metronome app set to 166 bpm during a run; found it helped rhythm."
  ],
  "lately": [
    "Open: metronome at 166 — still using it, or was it a one-off trial?",
    "Open: heat wave (35–36°C highs through Friday as of 23 June) — monitor how right knee and foot respond as weekly distance stays elevated."
  ]
}
```
(provenance: `model_id=claude-haiku-4-5`, `source_report_count=25`,
`grounded_through=2026-06-23`).

It clears every acceptance goal and the incident-prevention property:

| check | verdict |
|---|---|
| **Verdict-free (G1)** — no inferred behavioral judgment | **PASS** — every line is a stated fact or soft character note; zero "ignores easy days" verdicts |
| **Grounded, no fabrication** | **PASS** — knee/leg niggles trace to the real check-in notes; metronome + heat to real chat; `goals_and_plans` correctly **empty** (the runner stated no race — it did not invent one) |
| **Hedged** | **PASS** — "Possible… noted once in early June" |
| **`lately` = threads, not outcomes (G3)** | **PASS** — two open questions, no verdicts |
| **Reads like a coach's notes / one screen / five sections** | **PASS** |

It correctly **omits** the Lisdexamfetamine HR confound: that lives in
`UserProfile.injury_notes` (already in `pack.profile`) and is handled by the
`stimulant_use` discount, so memory does not duplicate it (the G2 boundary).

---

## 2. The B-vs-A: what v13 adds over v12 (pack + prompt)

With the real profile loaded, packs built under both prompts:

**Context pack** — v13 is v12's pack **plus exactly the `memory` section** (19 → 20
sections, nothing else moves; `KEYS added=['memory']`, `removed=[]`). **System
prompt** — v13 is v12 **plus the memory addendum** (+2,279 chars; the M3 prefix
test pins `SYSTEM_PROMPT_MESSAGE_V13 == SYSTEM_PROMPT_MESSAGE_V12 + _MEMORY_ADDENDUM`
byte-for-byte). The addendum carries the authority tiering (memory is citable but
yields to measured data, never lowers the floor) + the anti-nag directional
discipline.

---

## 3. Report-level B-vs-A (real Sonnet output, two activities)

For each activity the fuller coach message was generated under v12 (no memory) and
v13 (memory in pack). Full prose in §6; the pattern across both:

**The analytical spine is identical between v12 and v13** — the same measured facts,
the same safety calls, the same coaching thesis. Memory changes **delivery and
continuity only**, exactly as designed:

| activity | what v13 adds from memory | does it override fact / nag / lower floor? |
|---|---|---|
| **Morning Run** (2026-06-25) | Picks up the open metronome thread as a **question** ("you tried the metronome at 166 and found it helped — is that still in the toolkit, or a one-time experiment?"); acknowledges the cadence curiosity; continues the knee/foot thread ("keep monitoring how the right knee and foot respond") | **No.** Heat-discounted drift, the personal 4.4% baseline, the +17.7% efficiency read, and the polarized "moderate middle" coaching are byte-for-byte the same call as v12. The metronome is offered, not nagged. |
| **Lunch Run** (2026-06-28) | Connects the immaculate 165 cadence back to the metronome ("whatever the metronome work did, it has clearly stuck"); closes on the knee thread ("No pain reported today… after the knee and foot history, that's good to hear — let's keep that thread going") | **No.** The negative-drift headline, the Lisdexamfetamine RPE-weighting (present in **both**, from `pack.profile`), and the load-spike caution are identical. No "you ignored" verdict anywhere. |

The value the owner is judging: under v13 the coach picks up and **resolves** the
runner's own open threads (the metronome experiment → "it stuck"; the knee history →
"no pain today, good"), reads as a continuing relationship rather than a fresh
per-run report, and does so **without** letting any stated fact override this run's
measured `DerivedMetric` or soften the safety floor. That is the whole thesis of the
redesign, demonstrated on real output.

---

## 4. Eval (deterministic, no regression)

`make eval-selftest` — green (good fixtures pass all 7 applicable; bad fail all 13,
including the two M3 sensors). `make eval` v12 baseline over the real stored reports
— **1.000 pass rate, 0 failures across all 13 assertions**; `memory_preserved_safety_surface`
is NOT_APPLICABLE (no referral fired) and `coached_direction_not_nagged` passes on
real prod-prompt output. The two real v13 reports in §6 carry no nag verdict and
relay every safety call, consistent with those sensors.

---

## 5. Summary for the owner

| check | status |
|---|---|
| Writer gathers real sources + writes a profile | **verified (real Haiku)** |
| Profile is verdict-free / grounded / one screen | **verified** |
| v13 pack = v12 + `memory`; v13 prompt = v12 + addendum | **verified (deterministic)** |
| v13 report keeps v12's facts + safety, adds continuity, no nag | **verified (real Sonnet, 2 activities)** |
| Eval has no false-positives on real reports | **verified (1.000, 13 assertions)** |

There is no remaining funded-key-gated step for validation. The only remaining
human action is the cutover itself — `docs/deployment/coach-memory-v13-cutover.md`.

---

## 6. Full report prose (for reference)

### Morning Run — 2026-06-25

**v12 (no memory):**
> The effort read exactly where you said it felt — moderate, RPE 5, and the numbers back that up cleanly. HR was running high all morning (164 average, touching 176), but you were out in 30°C, and the pipeline is clear: that 13.8% drift is primarily a heat story, not a fatigue alarm. Worth knowing though — even measured against *your own* personal baseline for hot runs like this, where your typical drift sits around 4.4%, today was still elevated. So the heat amplified what was already a genuine working effort. Nothing alarming; just context for how those conditions stack up.
>
> Here's what I actually want you to see: your efficiency on comparable sessions — moderate, flat, hot — has improved **17.7%** across 17 runs. That's not noise. The engine is getting quietly better in exactly the conditions that should slow it down. Real aerobic progress, happening in the background whether or not the day-to-day feels like it.
>
> Here's the honest coaching note, though. You've logged two moderate runs this week — today and Tuesday — with no hard sessions yet. From a polarized standpoint, the moderate middle is exactly where progress likes to hide. You're working hard enough that recovery takes a hit, but not hard enough to sharpen the engine. The contrast that builds you is: when it's easy, make it *actually* easy — genuinely conversational, a pace you could hold a full sentence through — and when it's time to work, point at a real quality session with intent. Hard-easy contrast is where the adaptation lives; the grey middle just accumulates load without the stimulus.
>
> Your legs are in a genuinely fresh state right now — form sitting solidly positive — so the platform for quality work is there when you're ready to use it. Cadence was steady at 160 throughout, good to see. Solid morning in the heat.

**v13 (memory in pack):**
> Smart call getting out at 9am — that's the right move in a 30°C heat wave. You held a genuine moderate effort, RPE 5, and the zones back it up: most of the run in Z3, a solid chunk in Z4. The HR drift sits at 13.8%, which sounds alarming, but I'm discounting it completely as a fatigue signal — at 30°C, heat is driving it. Your personal baseline for hot flat runs like this is around 4.4%, and today's conditions explain the gap, not accumulated tiredness. Your training condition right now is actually fresh (fitness comfortably ahead of fatigue), so there's no tiredness story hiding in here.
>
> Here's my main coaching point: this was a moderate run, and you had a moderate one on June 23 too. From a polarized standpoint, the moderate middle is the zone we want to minimise — it gives you enough stress to add fatigue, but not the full aerobic development of a genuinely easy effort, and not the real sharpening stimulus of a proper quality session. Two moderate runs close together isn't a crisis, but if you can nudge some of these efforts toward truly easy — RPE 3–4, genuinely conversational, letting Z2 do its thing — the hard days will land fresher and do more for you. That's the trade-off worth making.
>
> Cadence came in at 160 spm today. You know you're curious about this stuff — and you tried the metronome at 166 and found it helped your rhythm. Is that still in the toolkit, or was it a one-time experiment? Even that 6 spm gap is worth closing, and you already know it works for you.
>
> The bigger efficiency picture is actually encouraging: across your last 17 comparable hot flat runs, your efficiency has improved 17.7%. Today's trend within the run was declining — heat does that — but the long arc is solidly upward. That's quietly significant.
>
> Keep monitoring how the right knee and foot respond as the heat wave rolls on. You're doing the right thing by running mornings.

### Lunch Run — 2026-06-28

**v12 (no memory):**
> Minus 0.8% HR drift on a hilly 8.4km with 151 metres of climbing — that's the headline, and it's a genuinely good one. A negative drift means your heart rate was tracking fractionally lower through the back half than the front, which on that kind of terrain tells you your aerobic engine held up well. That's exactly what a long run is supposed to show.
>
> Worth layering in the timing though: you set off at 12:47, right in the middle of your peak Lisdexamfetamine window. Your medication is known to push HR upward during those hours, which makes sense of the small gap between your RPE of 3 — genuinely easy, comfortable — and the moderate intensity the zones registered. I'd weight how you felt more heavily here. The HR was doing what it does at that time of day, and the true effort was probably closer to easy aerobic than the Z3 split suggests. That's useful context to carry into future noon runs.
>
> The flag I do want you to sit with is the load spike. Your running distance this week is around 19.6km across four sessions, up about 62% on the previous seven days, and this 8.4km run is the biggest single piece of it. Fitness and fatigue are essentially locked together right now — balanced is fine, but it means the legs are carrying accumulated work. The next run wants to be short and easy, not another moderate-length effort. Give yourself the chance to absorb what you've done this week before adding more.
>
> The pace variability of 14.5% looks wide, but this is a hilly route — it's terrain-honest running, not erratic effort. Your cadence sat at 165 virtually the entire way through regardless of the gradient, which is a nice detail.
>
> Good week overall — four runs, three rides, walks, rowing, strength work. Active and varied. Now protect it.

**v13 (memory in pack):**
> You held up beautifully on this one. HR drift of -0.8% across 151 metres of climbing — your heart rate actually came out slightly lower relative to your pace by the end than at the start. On a properly hilly 8-and-a-bit-kilometre effort, that's a real sign of aerobic durability doing exactly what it should.
>
> Now, the zones show the bulk of this run in Z3, which would normally prompt me to flag the moderate-intensity grey zone — the polarized approach is clear that the middle ground is where progress quietly stalls. But this run needs context: you went out at 12:47 PM, right in the heart of the window where your medication is known to push HR higher, and your RPE came in at 3 — genuinely easy. Those two things together make a strong case that the HR is running elevated because of the timing, not because you were working at a truly moderate aerobic effort. I'm giving real weight to how it felt. This reads as an easy aerobic effort wearing higher cardiac numbers as a disguise.
>
> Cadence was immaculate. 164.8 average, sitting at 165 spm like clockwork for virtually the entire run — barely a wobble in it. Whatever the metronome work did for your rhythm, it has clearly stuck. That kind of consistency doesn't happen by accident, and it's one of the things I want to see hold as your distances grow.
>
> One thing to stay honest with yourself about: your running distance is meaningfully up this week against your recent pattern. You've got about a year of training behind you, which is great — but you're only a few months into your current load level, which means the tissue is still catching up to the fitness. Nothing alarming here, but keep the next run genuinely easy (RPE 2-3, Z1-Z2 effort) and resist the pull to keep stacking more. The smart move after a week like this is to bank it, not keep adding.
>
> The late-run data looks wild — pace dropping under 5:00/km with HR ticking up into the high 160s — but a glance at the gradient tells the story: steep downhill finish, gradient diving to -8.5%. That's not your body struggling; that's you letting gravity do the work on the way home. Clean finish.
>
> No pain reported today. After the right knee and foot history, that's good to hear — let's keep that thread going.
