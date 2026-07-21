# Stream representation experiment (#726)

Does the coach LLM analyse a run better when the activity stream is a **rendered chart
image** instead of the numeric `stream_view` point data it receives today?

This is an **exploratory research harness**, not production code. Nothing here is wired
into the app. It reuses the real coach assembly (`_assemble_generation_request` /
`_generate_message`, `coach_message_lean_grouped_v5`) so results transfer.

## Files

| File | Role |
|---|---|
| `render_chart.py` | streams_dict → full-resolution Garmin-style PNG (HR / pace / elevation+grade / cadence), noise-matched to the 60-point view's bucket width. `optimized=True` renders a vision-legibility-tuned chart: pace axis clamped to the running range with M:SS gridlines, minimum elevation span + fixed grade range (flat looks flat), dense time grid, larger fonts. |
| `run_ab.py` | **Embedded** dual-arm harness: real production pack, two arms differing ONLY in the stream form (numeric `stream_view` vs `stream_view` stripped + chart image), same parse/validate pipeline. Deterministic token counts. |
| `run_bare_ab.py` | **Bare** harness: no coach pack/prompt; the model gets ONLY the stream (60-pt numbers or the chart) + a neutral prose instruction that forbids naming the data format (keeps the two arms blind). |
| `run_intervals_ab.py` | **Interval** harness: the REAL production system prompt (lightly redacted to describe only the stream it is given), stream-only, on interval sessions. `CHART_OPTIMIZED=1` uses the vision-tuned chart. |
| `judge.py` | Blind semantic judge (opus-4-8, independent of the sonnet coach) scoring insight / specificity / faithfulness against an independent ground-truth digest (HR/pace/elevation/cadence stats + derived metrics + the rep-level `interval_structure` for interval runs). |
| `make_artifact.py` | Builds a blind, interactive A/B HTML page for owner scoring (self-contained markdown renderer, per-run Report 1 / Draw / Report 2 picker). |

## Requirements

Runs under the backend venv (editable-installed `app`), plus **matplotlib** which is an
experiment-only dependency, deliberately NOT added to `pyproject.toml`. Install it into a
throwaway target and put it on `PYTHONPATH`:

```bash
backend/.venv/bin/python -m pip install --target /tmp/exp-pylibs matplotlib
```

Needs a local DB with real activities + streams (`make seed-local`) and `ANTHROPIC_API_KEY`.

## Run

Run from the `backend/` directory so `pydantic-settings` finds `backend/.env`.

```bash
cd backend
EXP=scripts/experiments/stream_image_ab
OUT=/tmp/stream_ab_out            # results land here
PP="/tmp/exp-pylibs:$EXP:."       # pylibs (matplotlib) + experiment dir + backend on path

# bare, blind, prose-normalized (the most product-relevant condition)
EXP_OUT="$OUT" COACH_PROMPT_ID=coach_message_lean_grouped_v5 \
  PYTHONPATH="$PP" MPLCONFIGDIR=/tmp/mpl \
  .venv/bin/python "$EXP/run_bare_ab.py" <activity_id> <activity_id> ...

# or the embedded (image-as-part-of-the-full-pack) condition:
#   .venv/bin/python "$EXP/run_ab.py" <activity_id> ...

# or intervals with the real production prompt + vision-optimized chart:
#   CHART_OPTIMIZED=1 EXP_OUT="$OUT" COACH_PROMPT_ID=coach_message_lean_grouped_v5 \
#     PYTHONPATH="$PP" MPLCONFIGDIR=/tmp/mpl .venv/bin/python "$EXP/run_intervals_ab.py" <interval_id> ...

EXP_OUT="$OUT" PYTHONPATH="$PP" .venv/bin/python "$EXP/judge.py"
EXP_OUT="$OUT" PYTHONPATH="$EXP:." .venv/bin/python "$EXP/make_artifact.py"
```

## Findings (3-run smokes per condition)

**Cost:** the 60-point numeric `stream_view` is the cheaper input every time
(~1.1k vs ~1.57k tokens; ~+450 for the image). The "full-res shape at a flat price"
thesis does not beat a 60-point view.

**Quality by condition:**

| Condition | JSON wins | IMAGE wins |
|---|---|---|
| Embedded (image added to full pack) | 2 | 1 (clear, on the shape-dependent run) |
| Bare, tables allowed | 3 | 0 |
| Bare, prose-normalized (blind) | 0 | 3 (all clear) |
| Intervals, production prompt, bad chart | 2 | 1 |
| Intervals, production prompt, **vision-optimized chart** | 2 | 1 |

**The result resolves to gestalt-vs-per-value, not image-vs-numbers.**

- **Gestalt** (steady / fading / flat / interval session / where's the big stop): an image
  reads it well, sometimes better. In the bare prose condition — which mirrors the coach's
  actual prose output — the image won all three continuous runs, while the numeric arm,
  forced to *synthesize* prose from a number list, fumbled (a ~2x pace misread, an invented
  finishing kick, a missed mid-run stop).
- **Per-value detail** (rep count, the rep-to-rep HR climb, a final-rep surge, exact drift):
  numbers win, *even with an excellent chart*, because vision can't resolve small deltas
  between similar-looking marks.

**Intervals** feel like a gestalt case but the coaching turns on per-rep values, so numbers
win. The first interval chart caused *catastrophic* image errors (rep pace read as 8:20/km
vs actual 3:55; invented ±8-10% rolling terrain on flat runs) — but those were **chart-design
bugs**, not vision limits: a pace axis compressed by walk-recoveries, and an elevation panel
auto-zoomed so 10 m of noise looked like mountains. The `optimized=True` chart fixed both
(image then read reps at 3:30-4:30/km and called the course flat), leaving only residual
per-value gaps (rep count off by one; missing a rep-to-rep HR climb).

**Conclusion for the product:** this reinforces the current architecture. Per-rep detail
already reaches the coach deterministically via `interval_structure`, and exact drift/zones
via `DerivedMetric`, so the stream's only real job is gestalt. An optimized image is a *safe*
gestalt channel but adds nothing over the numbers for the per-value substance production
already supplies — so it is **not worth a pipeline swap or the ~450-token-per-report vision
tax**. The one marginal case is an on-demand deep-dive image on long *continuous* runs
(gestalt-dominated). The "conditional inclusion" heuristic, if ever pursued, is long
continuous runs — NOT intervals.

**Spin-off worth doing regardless:** the chart-scaling bugs found here (auto-scaled elevation
making flat runs look hilly; pace compressed by recoveries) would mislead any viewer,
including a human on the activity page. Porting `render_chart.py`'s `optimized` scaling
(flat-looks-flat elevation, clamped pace) to the real UI charts is independently valuable.

**Caveats:** N=3 per condition, single LLM judge (may favor flowing prose). The judge digest
was extended with cadence and the rep-level `interval_structure` but is still computed from
the raw (not downsampled) stream, so it can over-penalize the 60-point arm on extremes.
