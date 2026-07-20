# Stream representation experiment (#726)

Does the coach LLM analyse a run better when the activity stream is a **rendered chart
image** instead of the numeric `stream_view` point data it receives today?

This is an **exploratory research harness**, not production code. Nothing here is wired
into the app. It reuses the real coach assembly (`_assemble_generation_request` /
`_generate_message`, `coach_message_lean_grouped_v5`) so results transfer.

## Files

| File | Role |
|---|---|
| `render_chart.py` | streams_dict → full-resolution Garmin-style PNG (HR / pace / elevation+grade / cadence), noise-matched to the 60-point view's bucket width. |
| `run_ab.py` | **Embedded** dual-arm harness: real production pack, two arms differing ONLY in the stream form (numeric `stream_view` vs `stream_view` stripped + chart image), same parse/validate pipeline. Deterministic token counts. |
| `run_bare_ab.py` | **Bare** harness: no coach pack/prompt; the model gets ONLY the stream (60-pt numbers or the chart) + a neutral prose instruction that forbids naming the data format (keeps the two arms blind). |
| `judge.py` | Blind semantic judge (opus-4-8, independent of the sonnet coach) scoring insight / specificity / faithfulness against an independent ground-truth digest. |
| `make_artifact.py` | Builds a blind, interactive A/B HTML page for owner scoring (self-contained markdown renderer). |

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

EXP_OUT="$OUT" PYTHONPATH="$PP" .venv/bin/python "$EXP/judge.py"
EXP_OUT="$OUT" PYTHONPATH="$EXP:." .venv/bin/python "$EXP/make_artifact.py"
```

## Findings so far (3-run smoke: long-hilly · short-high-drift · flat-control)

**Cost:** the 60-point numeric `stream_view` is the cheaper input every time
(~1.1k vs ~1.57k tokens; ~+450 for the image). The "full-res shape at a flat price"
thesis does not beat a 60-point view.

**Quality — depended entirely on output format:**

| Condition | JSON wins | IMAGE wins |
|---|---|---|
| Embedded (image added to full pack) | 2 | 1 (clear, on the shape-dependent run) |
| Bare, tables allowed | 3 | 0 |
| Bare, prose-normalized (blind) | 0 | **3 (all clear)** |

When the numeric arm can tabulate, it wins by transcribing exact values. Forced to
**synthesize prose** — what the coach actually produces (schema 2.0 prose `message`) — it
fumbled (a ~2x pace misread; an invented "sub-4:00 finishing kick" contradicting flat
drift; missing a mid-run stop), and the **image read the run's shape more faithfully on
every run**. A model reconstructs shape poorly from a number list when narrating, but
reads it directly from a picture.

**Caveats:** N=3, single LLM judge (may favor flowing prose); both arms still hallucinate
cadence/grade so neither is trustworthy alone — production's deterministic `DerivedMetric`
ground truth stays essential, this only concerns the *shape* read; image is full-res vs
60-pt numbers (resolution/modality confound, format now controlled). The judge digest
omits cadence/grade and is computed from the raw (not downsampled) stream, so its
faithfulness axis is noisy.

**Next:** fix the judge digest; run the embedded "image as a shape supplement to the pack"
test at larger N (the actual product decision); corroborate with an owner blind A/B.
