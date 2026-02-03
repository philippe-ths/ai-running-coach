# SPEC.md — AI Running Coach (Strava) MVP Spec

## 1) Product goal
Build a Strava-connected running coach that:
- ingests Strava activities,
- computes meaningful training signals,
- produces short, actionable advice with safety guardrails,
- provides a weekly view (load + hard/easy balance + flags).

The MVP must be useful without perfect data (no HR/power), but it should become more confident when HR/streams/RPE are available.

## 2) MVP user flows
### 2.1 Connect Strava
1) User clicks “Connect Strava”
2) OAuth completes
3) App pulls last 30 days of activities (or last 50 runs, whichever smaller)
4) App displays:
   - recent activities list
   - an “analysis” view for the latest activity
   - weekly dashboard for the current week

### 2.2 After each new Strava activity
1) Backend receives webhook event (or user triggers manual sync)
2) Backend fetches activity summary
3) Backend computes derived metrics
4) Backend generates advice for that activity
5) UI updates activity card to show “Verdict + Next run”

### 2.3 Quick check-in (optional but supported in MVP)
After viewing advice, user can set:
- RPE (1–10)
- Pain (0–10) + location (free text)
- Sleep quality (1–5)
These are used to tune advice confidence and risk flags.

## 3) Advice output requirements (fixed structure)
Every activity advice must follow this structure:

1) Verdict (single sentence)
2) Evidence (2–4 bullets, objective signals)
3) Next run (one prescription, concrete)
4) This week (one adjustment or “no change”)
5) Warnings (only if needed)
6) One question (optional; ask only if uncertainty is high)

### 3.1 “Next run” prescription format
Must include:
- duration (minutes) OR distance
- intensity target using ONE of:
  - RPE (preferred universal)
  - HR zone (if reliable HR)
  - pace range (only if confidence is high)
- optional: 1 technique cue OR fueling cue

Examples:
- “40 min easy (RPE 3–4). Keep it conversational.”
- “60 min easy + 6×20s strides (full easy jog recovery).”
- “45 min easy (RPE 3–4). If HR available: keep mostly Z2.”

## 4) Data inputs & assumptions
### 4.1 From Strava activity summary (required)
- start_date, type
- distance, moving_time, elapsed_time
- total_elevation_gain
- average_speed OR pace derivation
- splits (if provided)
- average_heartrate, max_heartrate (optional)
- average_cadence (optional)

### 4.2 Streams (optional; fetch selectively in MVP)
If run is classified as tempo/interval/long OR user explicitly requests deep analysis:
- time, distance
- velocity_smooth (pace)
- heartrate
- cadence
- altitude/grade

### 4.3 User profile (required minimal)
- goal_type: {general_fitness, 5k, 10k, half, marathon}
- target_date (optional in MVP)
- experience_level: {new, intermediate, advanced}
- weekly_days_available (1–7)
- injury_notes (free text)

### 4.4 Check-in signals (optional but supported)
- RPE 1–10
- pain_score 0–10 + pain_location text
- sleep_quality 1–5

## 5) Derived metrics (MVP)
Compute at least:
- activity_class: {easy, recovery, steady, long, tempo, intervals, hills, race, unknown}
- effort_score (TRIMP-like if HR; else duration×intensity proxy)
- pace_variability (split variance; or stream variance if available)
- hr_drift / decoupling (only if HR + steady segment)
- time_in_zones (if HR zones available; else omit)
- flags: list of strings (see below)
- confidence: {low, medium, high} + reason(s)

## 6) Flags (MVP)
Generate flags conservatively:
- "data_low_confidence_hr" (wrist HR spikes/flatline; unrealistic jumps)
- "gps_low_confidence" (pace spikes; large split anomalies)
- "intensity_too_high_for_easy" (easy-intent but sustained moderate+)
- "fatigue_possible" (elevated effort vs baseline; drift; big slowdown)
- "load_spike" (weekly load jump beyond safe heuristic)
- "pain_reported" (pain_score >= 4)
- "pain_severe" (pain_score >= 7)
- "illness_or_extreme_fatigue" (if user reports)

## 7) Safety & non-negotiables
- Never diagnose injuries.
- If pain_score >= 7 OR pain changes gait OR sharp localized pain:
  - recommend reducing load and seeking medical/pro assessment.
- No “make-up workouts”.
- If confidence is low, advice must default to conservative (“easy + recover”) and ask one clarifying question.

## 8) Weekly dashboard (MVP)
Show for current week:
- total distance/time
- count of hard sessions (tempo/interval/race)
- hard/easy ratio estimate
- load trend vs prior week (simple %)
- list of flags triggered this week
- one “weekly suggestion” (e.g., reduce intensity, add easy day, add strides)

## 9) Success criteria (MVP)
- User can connect Strava and see last 30 days.
- New activity triggers analysis + advice within a minute locally (webhook simulated ok).
- Advice is consistent, short, and includes evidence + next run.
- Flags appear when appropriate and do not hallucinate.