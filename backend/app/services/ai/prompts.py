from app.models import Activity, DerivedMetric, UserProfile, CheckIn
from app.services.ai.context_builder import build_context_pack
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

def build_commentary_prompt(
    activity: Activity,
    metrics: DerivedMetric,
    profile: Optional[UserProfile],
    advice_data: Dict[str, Any],
    check_in: Optional[CheckIn] = None,
    db: Optional[Session] = None
) -> str:
    """
    Builds a text prompt for the LLM to generate commentary.
    Ensures the context includes the rule-based verdict to prevent contradiction.
    """
    
    # --- Context Pack Construction ---
    # We attempt to build a unified context pack if DB session is provided.
    # Otherwise we fall back to existing manual construction (legacy support).
    context_json = "unavailable"
    if db:
        pack = build_context_pack(activity.id, db)
        if pack:
            context_json = pack.to_prompt_text()

    # Format inputs (kept for fallback / visual debugging, but AI instructed to prefer JSON)
    duration_min = round(activity.moving_time_s / 60)
    distance_km = round(activity.distance_m / 1000, 2)
    hr_info = f"{round(activity.avg_hr)} bpm" if activity.avg_hr else "N/A"
    
    profile_ctx = ""
    if profile:
        profile_ctx = f"Athlete Goal: {profile.goal_type}. Exp: {profile.experience_level}."

    pain_ctx = ""
    if check_in:
        pain_ctx = f"User Feedback: Pain {check_in.pain_score}/10, RPE {check_in.rpe}/10. Note: {check_in.notes}"
    
    prompt = f"""
    You are an expert running coach. Analyze the completed activity below.
    
    CONTEXT PACK (Source of Truth):
    {context_json}

    (Legacy Summary - Ignore if Context Pack is present):
    {profile_ctx}
    Activity: {activity.name} ({activity.type})
    Stats: {distance_km} km in {duration_min} min. Avg HR: {hr_info}.
    Analysis: This was classified as a '{metrics.activity_class}'. Effort Score: {metrics.effort_score}.
    Flags: {', '.join(metrics.flags) if metrics.flags else 'None'}
    {pain_ctx}
    
    RULE-BASED VERDICT:
    "{advice_data['verdict']}"
    
    INSTRUCTIONS:
    0. Use the CONTEXT PACK above as the primary source of truth. If data (e.g. cadence) is marked as missing in 'missing_signals', do not invent it.
    1. Write a short, encouraging, insightful commentary (max 3 sentences) reinforcing the verdict.
    2. Mention one specific detail about their metrics (e.g., HR, consistency, or restraint).
    3. Option to ask ONE short follow-up question if relevant to their feedback or flags.
    4. DO NOT prescribe the next run (that is handled separately).
    5. Tone: Professional, supportive, concise.
    """
    
    return prompt.strip()

def build_structured_advice_prompt(
    activity: Activity,
    metrics: DerivedMetric,
    profile: Optional[UserProfile],
    check_in: Optional[CheckIn] = None,
    db: Optional[Session] = None
) -> str:
    """
    Builds a prompt requiring strict JSON output for the advice object.
    Follows an expert coach breakdown logic.
    using Unified ContextPack as source of truth.
    """
    
    # --- Context Pack Construction ---
    context_json = "unavailable"
    if db:
        pack = build_context_pack(activity.id, db)
        if pack:
            context_json = pack.to_prompt_text()

    # Format inputs (fallback)
    duration_min = round(activity.moving_time_s / 60)
    distance_km = round(activity.distance_m / 1000, 2)
    hr_info = f"{round(activity.avg_hr)} bpm" if activity.avg_hr else "N/A"
    
    profile_ctx = "Unknown"
    intent_ctx = "None declared"
    if profile:
        profile_ctx = f"Goal: {profile.goal_type}. Exp: {profile.experience_level}. Injury Notes: {profile.injury_notes or 'None'}"
    if activity.user_intent:
        intent_ctx = activity.user_intent

    pain_ctx = "None"
    if check_in:
        pain_ctx = f"Pain: {check_in.pain_score}/10, RPE: {check_in.rpe}/10, Note: {check_in.notes}"
    
    prompt = f"""
    You are an expert running coach. Analyze this activity and output strict JSON advice matching the CoachReport schema.
    
    [CONTEXT PACK (PRIMARY SOURCE OF TRUTH)]
    {context_json}
    
    [FALLBACK CONTEXT]
    [ATHLETE CONTEXT]
    {profile_ctx}
    
    [ACTIVITY DATA]
    Name: {activity.name} ({activity.type})
    Declared Intent: {intent_ctx}
    Stats: {distance_km} km in {duration_min} min. Avg HR: {hr_info}.
    
    [METRICS & FLAGS]
    Class: {metrics.activity_class}
    Effort Score: {metrics.effort_score}
    Flags: {metrics.flags}
    
    [USER FEEDBACK]
    {pain_ctx}
    
    [ANALYSIS PROTOCOL]
    0. **Data Integrity**: Use only the data in the CONTEXT PACK 'available_signals'. If 'missing_signals' includes something, do not infer it.
    1. **Safety First**: If pain_score >= 4 or 'pain_severe' flag exists, HEADLINE must be 'Caution' or 'Stop'.
    2. **Intent vs Execution**: Did the athlete meet their declared intent or the implied intent of the activity type?
    3. **Key Metrics**: Cite specific numbers (Pace, HR, Drift) to back up your points.
    4. **Balance**: Always find a Strength (what went well) and an Opportunity (what to improve).
    
    [JSON SCHEMA]
    Output ONLY raw JSON. No markdown fencing.
    {{
      "headline": "A short, punchy 3-5 word summary of the session",
      "session_type": "The category of run performed (e.g. 'Easy Base', 'Tempo', 'Recovery')",
      "intent_vs_execution": [
          "Bullet point 1 comparing plan to reality",
          "Bullet point 2"
      ],
      "key_metrics": [
          "Metric 1 with value (e.g. 'Avg HR: 145 bpm')",
          "Metric 2",
          "Metric 3"
      ],
      "strengths": [
          "What the runner did well (1-3 items)"
      ],
      "opportunities": [
          "What the runner can improve (1-3 items)"
      ],
      "next_run": {{
        "duration": "Duration description (e.g. '45 mins')",
        "type": "Run type",
        "intensity": "Intensity description",
        "description": "Short description of the workout",
        "duration_minutes": 45,
        "intensity_target": "RPE 3-4"
      }},
      "weekly_focus": [
          "1-2 bullet points on how to adjust the rest of the week"
      ],
      "warnings": [
          "Any safety/injury warnings (empty if none)"
      ],
      "one_question": "A single engaging coaching question or null"
    }}
    """
    return prompt.strip()

def build_chat_prompt(
    user_message: str,
    activity: Optional[Activity] = None,
    metrics: Optional[DerivedMetric] = None,
    advice: Optional[Any] = None, # Advice model
    profile: Optional[UserProfile] = None,
    recent_history: list[Activity] = [],
    db: Optional[Session] = None
) -> str:
    """
    Constructs a context-rich system prompt for the chat.
    Uses Unified ContextPack where available.
    """
    
    # --- Context Pack Construction ---
    context_json = ""
    if db and activity:
         pack = build_context_pack(activity.id, db)
         if pack:
             context_json = f"\n\n[FULL CONTEXT PACK]\n{pack.to_prompt_text()}"

    system_ctx = "You are an expert running coach. Answer the user's question based strictly on the provided context."
    
    # 1. Profile Context
    if profile:
        system_ctx += f"\n\n[ATHLETE PROFILE]\nGoal: {profile.goal_type}\nExp: {profile.experience_level}\nInjury Notes: {profile.injury_notes or 'None'}"
    
    # 2. Activity Context
    if activity:
        dist_km = round(activity.distance_m / 1000, 2)
        dur_min = round(activity.moving_time_s / 60)
        hr_info = f"{round(activity.avg_hr)} bpm" if activity.avg_hr else "N/A"
        
        system_ctx += f"\n\n[CURRENT ACTIVITY]\nName: {activity.name}\nType: {activity.type}\nStats: {dist_km}km, {dur_min}min, Avg HR {hr_info}"
        
        if metrics:
             system_ctx += f"\nClass: {metrics.activity_class}\nEffort Score: {metrics.effort_score}\nFlags: {metrics.flags}"

        if advice:
             system_ctx += f"\nYour Previous Verdict: {advice.verdict}\nPrescribed Next Run: {advice.next_run}"
    
    # 3. Add Unified Context Pack if Available
    if context_json:
        system_ctx += f"\n{context_json}\n(NOTE: Prefer Context Pack for signal availability)"

    # 4. Recent History Summary
    if recent_history:
        count = len(recent_history)
        total_dist = sum(a.distance_m for a in recent_history) / 1000.0
        system_ctx += f"\n\n[RECENT HISTORY (Last {count} activities)]\nTotal Dist: {total_dist:.1f} km"
    
    prompt = f"""
    SYSTEM:
    {system_ctx}
    
    USER QUESTION:
    "{user_message}"
    
    """
    return prompt.strip()

def build_coach_verdict_v3_prompt(context_pack_json: str) -> str:
    """
    Builds the prompt for Coach Verdict V3 directly from the ContextPack JSON.
    Enforces the V3 strict schema and safety rules.
    """
    return f"""
You are an expert running coach and commentator. You are evidence-based, supportive, and practical.
Your job: write a Run Report using ONLY the ContextPack JSON provided by the user message.

[CONTEXT PACK]
{context_pack_json}

[TASK]
You must implement “Run Report Template v3 (coach + commentator)” inside the CoachVerdictV3 JSON schema.
Output ONLY a valid JSON object. No markdown.

[HARD REQUIREMENTS BY FIELD]

0) inputs_used_line (one line)
- Must list what is present AND mention key missing signals that increase uncertainty.
- Format: "Data used: X, Y, Z. Missing: A, B (so conclusions about ____ are lower confidence)."
- Use ContextPack.available_signals and missing_signals; do not guess.

1) headline
- sentence: exactly 1 sentence, plain English, supportive.
- status: green/amber/red using rubric below.

[STATUS RUBRIC - execution + cost]
- green: purpose matched + controlled execution + recoverable cost. (0–1 warn in scorecard)
- amber: good value but ONE meaningful issue (messy execution OR higher cost). (2 warns OR 1 fail)
- red: wrong stimulus OR cost/risk too high. (2+ fails OR clear risk flag)

2) why_it_matters (exactly 2 bullets)
- Bullet 1 MUST be Fitness implication: name the trained system (aerobic base / threshold tolerance / speed / durability) + why, anchored to evidence.
- Bullet 2 MUST be Fatigue implication: likely cost + what that changes in next 24–72h, anchored to evidence (RPE, HR, load trend, flags).
- If you can’t support one with evidence, say what’s missing.

3) scorecard (aim for 5 items; 5 max)
- Must include ALL 5 items unless impossible due to missing signals.
- Items: "Purpose match", "Control (smoothness)", "Aerobic value", "Mechanical quality", "Risk / recoverability".
- Ratings: "ok" (✅), "warn" (⚠️), "fail" (❌), "unknown".
- Reason: 1–2 sentences, must cite a concrete signal from ContextPack (metric value, evidence text, flag, RPE/pain).

4) run_story (3 short acts)
- 1–3 sentences per act (start, middle, finish).
- Must stay anchored to provided evidence; if you don’t have splits, avoid fabricated “km 3–4” style detail.

5) lever (one lever)
- Choose ONE category and ONE primary signal.
- signal: include what was seen, with evidence reference.
- cause: one sentence (most likely), not a list.
- fix: one sentence describing one change to trial.
- cue: a short coachable cue in quotes.

6) next_steps
- tomorrow: Must start with "Rest", "Easy", or "Quality". Then 1–3 sentences why. Can include Option A/B if brief.
- next_7_days: ONE adjustment only. Can offer A/B alternatives if clearly stated when to choose each.
- If status is amber/red, tomorrow must reflect recovery.

7) question_for_you
- One question that would materially change interpretation next time.

[GLOBAL RULES]
- Use ONLY ContextPack. Never invent weather, stops, hills, or pace splits not in evidence.
- Encourage the athlete (1–2 encouraging phrases per report), but keep it grounded.
- Output valid JSON only.

[JSON SCHEMA REFERENCE]
{{
  "inputs_used_line": "Data used: HR... Missing: Power...",
  "headline": {{ "sentence": "...", "status": "green"|"amber"|"red" }},
  "why_it_matters": ["Fitness: ...", "Fatigue: ..."],
  "scorecard": [
    {{ "item": "Purpose match", "rating": "ok", "reason": "..." }}
  ],
  "run_story": {{ "start": "...", "middle": "...", "finish": "..." }},
  "lever": {{ "category": "pacing", "signal": "HR", "cause": "...", "fix": "...", "cue": "..." }},
  "next_steps": {{ "tomorrow": "...", "next_7_days": "..." }},
  "question_for_you": "..."
}}
""".strip()


