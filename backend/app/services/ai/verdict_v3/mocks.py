import json

# Valid Mock JSON strings matching V3 Schemas

MOCK_SCORECARD_JSON = json.dumps({
  "inputs_used_line": "Analyzed HR, Pace, and Elevation data.",
  "headline": { "sentence": "Solid execution with strong aerobic discipline.", "status": "green" },
  "why_it_matters": [
    "Fitness System: Aerobic Base - Enhanced capillary density and fat oxidation efficiency.",
    "Fatigue Cost: Low impact - Full recovery expected within 24 hours."
  ],
  "scorecard": [
    { "item": "Purpose match", "rating": "ok", "reason": "Kept strictly to Zone 2 as planned." },
    { "item": "Control (smoothness)", "rating": "ok", "reason": "Pace showed very little decoupling from HR." },
    { "item": "Aerobic value", "rating": "ok", "reason": "Cardiac drift was less than 3% over the hour." },
    { "item": "Mechanical quality", "rating": "warn", "reason": "Cadence dropped below 165spm on hills." },
    { "item": "Risk / recoverability", "rating": "ok", "reason": "No pain signals reported; load is sustainable." }
  ]
})

MOCK_STORY_JSON = json.dumps({
  "run_story": {
    "start": "You started cautiously, shaking off the morning stiffness with a disciplined warm-up mile.",
    "middle": "Finding your rhythm around minute 15, you locked into a flow state where the effort felt effortless despite the rolling terrain.",
    "finish": "You kept the discipline tight until the very end, resisting the urge to sprint the final block."
  }
})

MOCK_LEVER_JSON = json.dumps({
  "lever": {
    "category": "mechanics",
    "signal": "Cadence (162spm)",
    "cause": "Over-striding on inclines efficiently.",
    "fix": "Shorten your stride and focus on quick turnover when the ground rises.",
    "cue": "\"Hot feet on hills\""
  }
})

MOCK_NEXT_STEPS_JSON = json.dumps({
  "next_steps": {
    "tomorrow": "Easy 30-40 min recovery run or active rest.",
    "next_7_days": "Maintain this volume but introduce one session of hill repeats to address the mechanical weakness."
  }
})

MOCK_QUESTION_JSON = json.dumps({
  "question_for_you": "Did you feel the cadence drop on the hills, or did it happen unconsciously?"
})
