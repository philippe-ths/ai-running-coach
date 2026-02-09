# SPEC.md — Running Coach (Strava) MVP Spec

## 1) Product goal
Build a Strava-connected running coach that:
- ingests Strava activities,
- computes meaningful training signals,
- produces short, actionable analysis with safety guardrails,
- provides a weekly view (load + hard/easy balance + flags).

The MVP must be useful without perfect data (no HR/power), but becomes more confident when HR/streams/RPE are available.

## 2) MVP user flows
### 2.1 Connect Strava
1. User clicks "Connect Strava"
2. OAuth completes
3. App pulls last 30 days of activities
4. App displays: recent activities list, analysis view for the latest activity

### 2.2 After each new Strava activity
1. Backend receives webhook event (or user triggers manual sync)
2. Backend fetches activity summary + streams
3. Backend computes derived metrics (classification, effort, flags)
4. UI updates activity card with metrics and flags

### 2.3 Quick check-in (optional but supported in MVP)
After viewing analysis, user can set:
- RPE (1-10), Pain (0-10) + location, Sleep quality (1-5)
These tune analysis confidence and risk flags.

## 3) Analysis output requirements (fixed structure)
Every activity analysis must include:
1. Classification (activity type: easy, tempo, intervals, long, etc.)
2. Effort score (TRIMP-like or duration-based proxy)
3. Flags (data quality, intensity, fatigue, load, pain)
4. Confidence level (low, medium, high) + reasons

## 4) Data inputs
### 4.1 From Strava activity summary (required)
start_date, type, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, splits, average_heartrate?, max_heartrate?, average_cadence?

### 4.2 Streams (optional; fetch selectively)
time, distance, velocity_smooth, heartrate, cadence, altitude/grade

### 4.3 User profile (required minimal)
goal_type: {general_fitness, 5k, 10k, half, marathon}, target_date?, experience_level: {new, intermediate, advanced}, weekly_days_available (1-7), injury_notes

### 4.4 Check-in signals (optional)
RPE 1-10, pain_score 0-10 + pain_location, sleep_quality 1-5

## 5) Derived metrics
- activity_class: {easy, recovery, steady, long, tempo, intervals, hills, race, unknown}
- effort_score (TRIMP-like if HR; else duration x intensity proxy)
- pace_variability (split or stream variance)
- hr_drift / decoupling (if HR + steady segment)
- time_in_zones (if HR zones available)
- flags: list of strings (see section 6)
- confidence: {low, medium, high} + reason(s)

## 6) Flags
Generate flags conservatively:
- "data_low_confidence_hr" (wrist HR spikes/flatline; unrealistic jumps)
- "gps_low_confidence" (pace spikes; large split anomalies)
- "intensity_too_high_for_easy" (easy-intent but sustained moderate+)
- "fatigue_possible" (elevated effort vs baseline; drift; big slowdown)
- "load_spike" (weekly load jump beyond safe heuristic)
- "pain_reported" (pain_score >= 4)
- "pain_severe" (pain_score >= 7)
- "illness_or_extreme_fatigue" (if user reports)

## 7) Safety rules
- Never diagnose injuries.
- If pain_score >= 7 OR sharp localized pain: recommend reducing load and seeking professional assessment.
- If confidence is low, default to conservative analysis.

## 8) Weekly dashboard (MVP)
Show for current week:
- total distance/time
- count of hard sessions (tempo/interval/race)
- hard/easy ratio estimate
- load trend vs prior week (simple %)
- list of flags triggered this week

## 9) Success criteria (MVP)
- User can connect Strava and see last 30 days.
- New activity triggers analysis within a minute locally.
- Metrics are consistent and include evidence (flags, confidence).
- Flags appear when appropriate and do not hallucinate.
