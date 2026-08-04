# Screen context is a server-resolved pointer, never a client payload

The coach thread (ADR 0027) opens over whatever screen the runner is on, which is the point: a runner looking at a chart types "why is this dropping?", and "this" has to resolve to something. The obvious implementation is for the screen to post what it is showing — the Load page sends its readiness numbers, Trends sends the series it plotted, the activity page sends its metrics. It is one round-trip faster and the data is right there in the component.

It is also the first time in this product's history that a **fact reaching the coach would originate outside the server**. Every existing grounding path is server-derived: the pack is built from `DerivedMetric` rows, the query tools re-derive from the same pure builders the pages use, and even the one deliberately untrusted surface (`User materials`, ADR 0017) is contained by never letting raw text into a prompt. A screen payload would quietly open a second untrusted input — one carrying *numbers*, presented to the coach as measurement — with no containment at all. A stale render, a mid-flight state, or a tampered request becomes something the coach cites as fact.

## Decision

The client sends a **pointer**; the server resolves it.

The pointer carries screen identity and the runner's view selections only — `{screen: "trends", range: "3M", metric: "distance"}`, `{screen: "activity", activity_id}` — and the server resolves it into a **screen view** using the same builders that produced the screen (`build_volume_report`, `build_readiness`, the activity read model). The resolution is owner-scoped like every other read: a pointer naming another runner's activity resolves to nothing.

Three rules follow.

**1. Selections are inputs; numbers are facts.** `range: "3M"` is what the runner chose and is trusted as such. `form: -12.4` is a measurement and is recomputed server-side, always, even as a "hint". This is the line the design turns on.

**2. One live view, never two.** Only the current screen's view is placed in front of the coach. Past turns retain the *label* of where they were asked, not their view — so the coach can resolve a back-reference without ever holding two copies of the same fact at different freshnesses, which is precisely the ambiguity the North Star's second question exists to prevent.

**3. A screen view is not a new authority tier.** It is the same data the pack's `readiness` / `training_volume` sections already carry, so it inherits their place in `Authority tiering`: citable, never overriding this run's re-derived `DerivedMetric` or the safety floor.

A screen earns a bespoke view only when it shows something the relationship baseline does not — parameterised data (Trends: range and metric) or item-specific data (the activity page: that run). Load, Home, and Profile contribute identity only, because the baseline already carries their content and a second copy under a second name is the duplication `#451` and the one-fact-one-place fold have already had to undo twice.

## Consequences

- **The screen has one builder, used two ways.** Each screen view is exposed both as the eager turn-one injection and as a tool the coach can call later in the thread, so there is no parallel per-screen pack-assembly path alongside `context.py`.
- **A screen change never provokes a turn.** The pointer only matters when the runner next speaks. `Salience` already establishes silence as a correct response; an assistant that comments every time you change screens is the nagging failure mode this product keeps designing away from.
- **The deterministic policy rules rebind to the turn's own facts.** With no activity anchor there is no stored pack, and today's `_validate_chat_text` degrades rules 2 and 4 off when the pack will not parse. Both are re-sourced instead: zone calibration resolves from the runner's `UserProfile` (it was never activity-scoped — it only lived on the pack because that was the nearest copy), and interval-execution claims gate against the sessions actually fetched or shown this turn, whose `detection_confidence` and `source` the tool results already carry. Rule 5 is unchanged and unconditional. The floor gets stronger, not weaker, for being moved off a stored artifact.
- **Telegram gains nothing and needs nothing.** A notification link lands on the activity page; the sheet opened there resolves that activity as its screen context, which is the routing a deep link would have hand-built.
