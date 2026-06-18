# Going live: runaway-cost control options

> Status: exploration, not yet decided. Captured 2026-06-17.
> Context: the single-user MVP is moving toward multi-user public signup (Phase 2, ADR 0005 / ADR 0006). The owner's #1 concern is runaway LLM cost, under a hard rule: **overspend must be structurally impossible, not merely alerted on** (a card on auto-charge does not qualify; this is why Fly was rejected and Railway chosen).
>
> All pricing below was web-verified June 2026 and is date-sensitive; model lineups and console features churn fast. Re-verify before relying on exact numbers.

## The frame: three things you can do about LLM cost

1. **Cap it** so overspend is impossible. The only family that satisfies the hard rule.
2. **Cheapen it** so each bill is smaller. Useful, but caps nothing. Soft against the rule.
3. **Offload it** (BYOK). Eliminates operator cost but carries real tradeoffs.

Key principle: "structurally impossible" exists in only **two** places — the provider billing layer, and a budget gate written in app code. Everything else (rate limits, caching, dashboards, alerts) either shrinks the bill or only *detects* a problem. A dashboard alert is not a cap.

Reframe that matters: the app is already most of the way there for zero engineering, because Anthropic is prepaid and the architecture is deterministic-first (a budget-exhausted state degrades to the existing `is_fallback` path, it does not crash).

## Family 1: Cap it (the only structurally-safe family)

Layered defense, outermost to innermost. Each layer is a true hard stop: when hit, calls fail and the app degrades to its existing `is_fallback` CoachReport path.

### Provider layer (zero code, do first)
- **[HARD STOP] Prepaid credits, auto-reload OFF, no card on file for overage.** At zero balance the API returns `400 insufficient-credits` and refuses every call. Max spend = credits loaded. Caveat: community bug reports of credits draining with auto-reload nominally off (claude-code #29108) — manually verify in Console that auto-reload is off AND no fallback card is attached.
- **[HARD STOP] Dedicated NAMED workspace + low monthly spend limit** ($30-50 to start). Mint the key there; rotate `ANTHROPIC_API_KEY` (config.py:31). Caps auto-reload too (belt to the prepaid braces). Cannot be set on the default workspace. Console email alert at ~75%.
- **[HARD STOP, coarse] Tier ceiling** (Tier 1 = $500/mo, service-enforced). Far above real spend; its job is to ensure you never move to Monthly Invoicing (postpaid), which dissolves the foundation.
- **[soft] Rate limits (RPM/ITPM/OTPM).** Never bounds total spend, only the spike rate. Turns a runaway loop into a slow drip, buying the real caps time to catch it.

### App layer (the cap you own in code; a console setting can't be reached by a redeploy)
- **[HARD STOP] Redis global daily + monthly budget gate.** Counter checked BEFORE every call in `llm.py`, wrapping all three call sites (generate_json:70, generate_structured:143, generate_coach_message:217/275). Over budget => refuse => `is_fallback`. Catches the multiplicative retry fan-out by counting every sub-call; gives daily granularity the provider caps lack. ~half a day. Maintenance: a per-MTok price table kept in sync (Sonnet $3/$15, Opus $5/$25, Haiku $1/$5).
- **[HARD STOP while off] Kill switch** via the existing `cadence.py:153 get_active_cadence` seam: a `COACH_LLM_ENABLED` / deterministic-only branch read at fire time, flipped like `COACH_RECEIPT_CADENCE`. Incident lever, not the standing guarantee (needs a human). #296 receipt proves a no-LLM path still ships a useful product.

### Per-user layer (the multi-user blast-radius cap; the reason this matters now)
- **[HARD STOP] Per-user pre-call quota** built into the same budget-gate seam, keyed by activity owner. No-op today (one user); essential at launch so one abusive/buggy signup can't drain the shared budget.
- **[HARD STOP] Regenerate cooldown + deterministic `job_id`** on coach.py:211. Closes the single worst lever (see landmines). A few hours.

## Family 2: Cheapen it (all soft, none caps)
- **Prompt caching** of the stable system prefix: not wired today; strong fit (v1-v7 are large static prompts; byte-stable versioning was designed for it). Opener+fuller of one exchange sharing a cached prefix is the big win. Watch invalidators (timestamps/unsorted JSON in prefix; each `COACH_PROMPT_ID` is its own prefix). Cache reads ~0.1x input.
- **Model tiering:** already done (Sonnet report, Haiku for consolidation/distiller/receipt-voice). Keep the discipline.
- **Batch API (50% off):** wrong for the live ~30-min path; right only for offline eval rebuilds. Low priority.
- **Cheaper providers:** Gemini ~2-3x cheaper on tokens but **fails the hard rule today** (Cloud billing budgets only alert with ~20-min lag; real Spend Caps still private preview). OpenAI is at Sonnet parity and keeps a structural cap. Neither saves enough at launch scale to justify porting the most Claude-coupled surface.

## Family 3: Offload it (BYOK) — do not lead with it
- **Gates the product to technical users** (a casual runner won't create an API account, set up billing, paste a key). Opposite of the consumer vision.
- **Relocates a predictable, cappable cost into a high-severity liability:** custodying a billing credential, on a stack that stores Strava tokens in plaintext and has preview deploys pointing at the prod DB.
- **ToS:** user-brings-own-**Anthropic** key is permitted-by-absence, but never pool keys as a fleet (Anthropic 2026 anti-proxy enforcement, ~2026-04-05). OpenAI explicitly forbids key transfer, so storing a user's OpenAI key server-side is legally gray.
- **If ever wanted:** hybrid only — a frictionless hard-capped free allowance for everyone, BYOK as an optional relief valve for the heavy minority, Claude-only, behind a gateway (store an opaque budget-capped token, not a raw secret), and **only after ADR 0005 per-user auth exists.**

## Provider landscape (verified June 2026, will drift)

| Provider | Sonnet-class | Haiku/cheap-class | Provider hard cap? |
|---|---|---|---|
| **Anthropic (baseline)** | Sonnet 4.6 $3/$15 | Haiku 4.5 $1/$5 (Opus 4.8 $5/$25) | **Structural** — prepaid, auto-reload off by default; tier + workspace spend limits |
| **OpenAI** | GPT-5.4 $2.50/$15 | mini tiers cheaper | **Structural** — prepaid + org monthly budget hard-stops to 429. Trap: auto-recharge with blank monthly limit = uncapped auto-charge |
| **Google Gemini** | 2.5 Pro $1.25/$10 | 2.5 Flash $0.30/$2.50, Flash-Lite $0.10/$0.40 | **Soft** — budgets only alert (~20min lag); real Spend Caps private preview only |

All three offer ~50% Batch discounts and prompt/context caching. Fit: the app is built on Anthropic-specific forced-tool structured output (`output_contract.record_coach_tail`, `generate_structured` for the distiller) + extended thinking; staying on Anthropic is zero migration. OpenAI/Gemini would require rewriting `llm.py` / `output_contract.py` / `material_distiller.py` to their own structured-output mechanism plus an eval re-run.

Gateways (provider-agnostic budget enforcement + per-user virtual keys, keep Claude): **LiteLLM** self-hosted (per-user `max_budget`, lives in the Railway trust boundary, one more container) or **Cloudflare AI Gateway** (zero-ops SaaS, dollar Spend Limits that 429-block). Both: insert via a `base_url` kwarg on the Anthropic client (constructor takes none today, llm.py:55-58) using the native Anthropic wire protocol so forced-tool + thinking are untouched. One mandatory test: confirm the gateway round-trips `tool_use` + extended-thinking blocks faithfully.

## Landmines for going live (found in code; bounded today only by single-user run cadence)
1. **Regenerate endpoint** (coach.py:211): enqueues with no `job_id`, no cooldown, no dedup, gated only by shared Basic-Auth; now carries PIPELINE_RETRY(3x). Idempotency is on the stored row, not on spend. Single most exposed lever.
2. **Prose retry tree × PIPELINE_RETRY** (service.py `_generate_message` / process_new_activity.py:73): empty-prose loop × max_tokens escalation to 16384 × policy-retry, re-run by RQ 3x = ~15-25 large-token calls worst case for ONE activity. #295 confirms truncation-escalation loops have happened in prod.
3. **Coach chat** (chat.py `stream_chat_response`): resends the entire history + full context_pack/report/profile/trends/splits every turn; input tokens grow unbounded with thread length. No cap beyond shared auth.
4. **No in-code budget cap** anywhere (config.py has only key + model). **Hosting cap is an unverified dashboard setting** (topology.md:95-99), not codified — manually confirm Railway/Vercel hard usage caps are actually set.

## Recommended stack
1. **Ring 1:** prepaid, auto-reload off, verified in Console (zero code, today).
2. **Ring 2:** named workspace + ~$30-50 monthly spend limit, scoped key (zero code).
3. **Ring 3:** Redis budget gate in `llm.py` (the cap you own; ~half a day).
4. **Plus:** regenerate cooldown + deterministic `job_id` (closes the worst lever); per-user quota in the same gate.
5. **Plus:** kill switch via the cadence seam.
6. **Defer:** prompt caching, Batch API, gateway, all of BYOK until real spend data justifies them.
7. **Stay on Claude. Don't migrate. Don't do BYOK yet.**

Do not mistake for protection (soft against the rule): rate limits, caching/Batch/model-tiering, and all monitoring/alerts. The structural caps live in the billing layer (Rings 1-2) and the budget gate (Ring 3), nowhere else.

## Decisions only the owner can make
1. **Monthly spend ceiling?** Sizes Ring 2 and Ring 3.
2. **At the cap: degrade or block?** Code already supports degrading to the deterministic receipt / fallback; hard-block is new UX.
3. **Wave-one size (5 / 50 / 500)?** Decides whether per-user quotas matter yet.
4. **Daily sub-cap too,** so one bad day can't burn the month?
5. **Willing to custody users' billing credentials at all?** If no, BYOK is settled (caps only). If yes, plan liability-limitation ToS + likely cyber insurance, and sequence after ADR 0005.

## Sources (key, date-sensitive)
- Anthropic billing/prepaid: https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-api-usage
- Anthropic rate/spend limits + tiers + workspace limits: https://platform.claude.com/docs/en/api/rate-limits
- Anthropic commercial terms / AUP (anti-proxy): https://www.anthropic.com/legal/commercial-terms ; https://www.anthropic.com/legal/aup
- OpenAI spend controls: https://help.openai.com/en/articles/20001155-managing-credits-and-spend-controls-in-chatgpt-business
- Gemini pricing + Spend Caps (preview): https://ai.google.dev/gemini-api/docs/pricing ; https://cloud.google.com/blog/topics/cost-management/introducing-spend-caps-ai-cost-visibility-next26
- OpenRouter BYOK (1M free then 5%): https://openrouter.ai/docs/guides/overview/auth/byok ; https://openrouter.ai/blog/announcements/1-million-free-byok-requests-per-month/
- BYOK UX reality: https://surfmind.ai/blog/byok-bring-your-own-key-future-of-ai-tools
