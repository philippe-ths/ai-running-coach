# Project glossary

Definitions resolved during design discussions. Implementation details belong in code; this file is the shared vocabulary only.

## Coaching relationship

The durable, per-runner relationship the coach maintains over time: one continuous coaching memory and narrative per `User`, not a pile of per-activity artifacts. A finished `Activity` is an *event* within the relationship that may prompt the coach to speak, never the unit of the relationship itself. The relationship holds the runner-model the coach carries forward (what the runner has told it — the `Runner memory profile` — what the deterministic data layer has learned, and the open threads), and every coach touchpoint reads from and writes back to this one shared memory, so each touchpoint is a continuation rather than a fresh start. Distinct from a single `Coach report generation`, which under this model is one *form* a turn in the relationship can take, not the relationship itself. Materialised since A1 (ADR 0011) as a thin one-row-per-user anchor (`coaching_relationship`), auto-created with the user; P1's voice/stance dials and later relationship state extend that row rather than create it.
_Avoid_: treating the coach's output as a standalone per-activity report; the protagonist is the relationship, and a report is a move within an ongoing conversation, not the conversation itself.

## Exchange

One coach↔runner turn-or-burst within a `Coaching relationship`, anchored to an event (a completed `Block`, but also a `CheckIn` or a chat reply). The default post-activity exchange is two-stage: a light, input-free opener fired once the block looks complete (a brief human reaction plus tappable `Perceived effort`/pain prompts, never blocking, never nagging for missing data), then a fuller turn triggered by the runner's reply or a timer, whichever comes first, folding in any input that arrived. A closed exchange is never re-opened; an activity arriving after the fuller turn starts a new block and a new exchange. The coach decides the depth of each stage from whether the event warrants it: silence or a one-liner on an unremarkable run, depth on an interesting one. Every exchange reads from and writes back to the relationship's memory. A `Coach report generation` is one heavyweight form an exchange can take, not a synonym for it.
_Avoid_: equating "exchange" with "report"; a report is one shape an exchange can take, and many exchanges are light or silent.

## Thread

A runner-initiated, continuing conversation with the coach, held within the `Coaching relationship`. The runner opens a thread from anywhere in the app, leaves it, and resumes it later, or starts a fresh one. A thread is a **topic boundary, not a memory boundary**: starting a new thread never resets the coach, because what the coach carries between turns is `Durable memory` plus a bounded digest of the runner's other recent conversation — never the visible transcript of the current thread alone. Distinct from `Exchange`, which is coach-initiated and anchored to an event (a completed `Block`, a `CheckIn`): a thread is runner-initiated and anchored to nothing, so the two are siblings within the relationship rather than one being a kind of the other. Where they meet, the thread *displays* the exchange rather than absorbing it: a thread anchored to an activity shows that activity's report at its head, and such a thread may be brought into existence by the exchange it displays, so the runner's first message is not always what creates it. An anchor is a framing hint — which screen the thread opens on and where it is listed — never a boundary on what the coach can discuss or fetch.
_Avoid_: calling a thread a "session" — a session is a *training* session in this domain, and the ambiguity would reach the prompt text the coach reads; treating a new thread as a coach with no history, or a thread's transcript as the relationship's memory.

## Screen context

What the runner is currently looking at, carried into a `Thread` turn so the coach can resolve what "this" and "that" refer to. It is a **pointer, not a payload**: the runner's client states which screen and which view selections (which activity, which range, which metric), and the server resolves that pointer into a screen view using the same builders that produced the screen itself. Facts never travel from the client — a view selection is the runner's input and is trusted as such, while every number is recomputed. Only the current screen's view is placed in front of the coach, so it never holds two copies of one fact at different freshnesses; past turns retain only the label of where they were asked. Per `Authority tiering` a screen view sits with the data it is built from: citable, never overriding this run's measured data or the safety floor. A screen change on its own is not an event and never provokes the coach to speak.
_Avoid_: sending rendered screen data as context (that makes the client an authority on facts the server owns); accumulating every visited screen's view in one turn; treating the screen as a data boundary — the coach's reach is scoped to the runner, not to the screen.

## Coaching skill

A named procedure for handling one KIND of request in a `Thread`: when it applies, which tools to run and in what order, what must be checked before answering, the shape the answer takes, and the safety discipline that binds it. Procedure only — a coaching skill says how to conduct a turn, never what is true about training. Any claim about what good training IS belongs to the `Coaching corpus` or the runner's `User materials`, which the skill defers to, so the runner's selected school still governs the substance of an answer a skill merely shaped. Distinct from `Voice` (how the coach sounds) and `Coaching stance` (what it foregrounds): those personalize a turn, a skill conducts one.
_Avoid_: putting coaching doctrine in a skill; organising skills by topic rather than by kind of request; letting a skill relax the safety floor, widen what the coach may write, or come from anywhere but the house (runner-supplied procedure is `User materials`).

## Salience

How much an event (a finished `Activity`, later a `Block`) is worth the coach speaking about, and how strongly: the coach's read of an event's noteworthiness to this `Coaching relationship` right now. It is the axis that drives the two-stage `Exchange` cadence and coach-decided depth: a high-salience event pulls the coach's response up in weight, a low-salience one toward a one-liner or silence (silence is a valid, correct response, not a failure to do work). Salience is the computable proxy for the runner's own expectation of how noteworthy the event was; the coach aims to mirror that expectation, and a divergence between the data-derived salience and the runner's felt importance is itself coaching signal, not error (the same shape as the `Stated intent`-vs-execution and `Perceived effort`-vs-`Effort` gaps). Read from already-computed signals: unusual versus the runner's own baseline, a safety flag, novelty (first of its kind in their history), and relevance to an open thread (prior advice, a tracked pattern). Distinct from `Effort` (how hard, from HR) and `Training load` (cumulative strain): a planned-and-nailed hard session can be low-salience, while a physically light first-ever activity is high.
_Avoid_: equating salience with intensity or load; an easy or short run can be highly salient (novel, or it breaks a pattern), and a hard run can be unremarkable (exactly as planned).

## Working context

The bounded set of information assembled into the prompt for one turn of coaching — an `Exchange` anchored to an event, or a `Thread` turn anchored to the runner and to now. In both cases: a lean baseline always present (the relationship's narrative summary, this run's measured facts, the relevant deterministic facts, the last exchange's digest) plus a trigger-scoped focus payload about whatever the exchange is anchored to, kept deliberately lean. It is a **view, not a store**: assembled per turn from the raw store, the `Processed artifacts` layer, and `Durable memory`, with deeper detail pulled on demand (retrieval) rather than received as a fixed, pre-decided pack. Distinct from `Durable memory` (what persists across exchanges) and the raw store (the append-only source of truth, never loaded wholesale). The thing context engineering protects: small by default, deep on demand.
_Avoid_: equating working context with "everything we know about the runner"; it is the lean slice assembled for one turn, not the memory itself.

## Processed artifacts

The layer of pre-digested, retrievable records derived from the raw store on ingestion, the moment data lands, so that retrieval later is cheap (the "do the work on ingestion" principle, attributed to Karpathy's LLM knowledge bases). Holds the per-run measured facts (`DerivedMetric`), the consolidated stream view (a small downsampled HR/pace/grade/cadence snapshot of one activity), the digest of each past `Exchange` (headline, lead argument, commitments), and the deterministic signals extracted from user responses (RPE-vs-HR divergence, pain trend, pushback). Distinct from the raw store (it is derived, not truth), from `Durable memory` (it is per-event detail, not cross-exchange generalisation), and from `Working context` (it is stored, not assembled fresh per turn). `Working context` pulls from it on demand; raw streams themselves never enter context, only their processed view does.
_Avoid_: treating a processed artifact as ground truth that overrides a re-derived `DerivedMetric`, or loading it wholesale; it is a retrieval-shaped convenience over the raw store, pulled only when the turn needs it.

## Durable memory

The per-runner relationship memory that persists across exchanges and is carried forward, split into two layers with different authority. The deterministic data layer holds rule-derived, auditable facts the coach grounds claims on (`RunnerBaseline` numeric norms, training load, confirmed confounds from `Activity analysis`) and is authoritative for facts. The `Stated memory` layer — realized as the `Runner memory profile` — holds what the runner has told the coach plus soft character, rewritten from source each pass; it is citable but yields to today's measured `DerivedMetric` on a factual conflict. The boundary is absolute: neither layer can override a re-derived `DerivedMetric`, and the profile can never lower the safety floor. Maintained by the memory update pass (see `Consolidation`).
_Avoid_: treating durable memory as a transcript of raw runs, or letting the stated profile harden a derived conclusion into fact; the raw store remains the source of truth the current run always re-derives from. (The earlier LLM-narrative layer and the deterministic `Belief`/`Preference profile` layer were retired in ADR 0025; the profile and the data layer replace them.)

## Consolidation

The background process — the memory update pass — that rewrites the `Runner memory profile` after an `Exchange`, decoupled from producing the exchange itself so the user-facing turn never waits on it. It rewrites the profile WHOLE from the raw sources (past reports, chat, check-in notes) plus the deterministic data layer, never from the profile's own prior text, so the relationship's memory self-corrects against ground truth rather than drifting or echoing. The compaction mechanism that keeps the relationship's history bounded without discarding it: detail stays queryable in the raw store via retrieval.
_Avoid_: coupling the update pass to exchange generation, handing it the profile's own prior text (the echo loop ADR 0025 forbids), or letting it store an inferred behavioral verdict the data does not support.

## Stated memory

The runner-told layer of `Durable memory`: the durable record of what the runner has explicitly told the coach (in a `CheckIn` note or a chat reply), as opposed to what the pipeline measured (`Activity analysis`, the deterministic data layer). It is grounded and citable — unlike the voice-only narrative, the coach may act on it and reference it — but per `Authority tiering` it yields to this run's measured `DerivedMetric` on a factual conflict, because a stated fact can be mistaken or fabricated. Each record is tagged by FUNCTION (how the coach must use it), not by topic: a **Constraint** (bounds or gates advice — an injury, "no morning runs", "gels make me sick"), a **Goal or plan** (a forward-dated intention; see `Goals and plans`), a **stated Preference** (frames choices — gear, fuelling, tone), or an **Open thread** (a pending question the next `Exchange` should pick up). Every record carries its provenance (that the runner said it, when, and the context it was said in) and a clock (confirm, expire, or permanent), so nothing lingers as confident-stale. When a new statement meets an existing one, a time-ordered update silently supersedes the old, but two things asserted true at once surface as one focused question rather than being silently reconciled. It is realized concretely as the `Runner memory profile` — the five-section, rewritten-from-source document the coach reads.
_Avoid_: treating stated memory as able to override measured data or the safety floor; reading a **stated Preference** as a behavioral verdict (it frames choices because the runner *said* so, never because the pipeline inferred it); filing records by topic (shoes, sleep, nutrition) rather than by function; treating a runner-supplied coaching *philosophy* document as stated memory rather than `User materials`.

## Runner memory profile

The concrete realization of `Stated memory`: one rewritten-from-source document per runner, five named sections (Who you are / Limits & constraints / Goals & plans / What works for you / Lately), each a short capped list of plain-language lines — what the runner told the coach plus soft, non-gating character, never an inferred behavioral verdict. Rewritten WHOLE from the raw sources (past reports, chat, check-in notes) plus the deterministic data layer on every update pass; the writer is never handed the profile's own prior text, so a derived conclusion cannot harden and replay (the anti-echo guarantee is structural, a property of input construction, not a behaviour the model is asked to exhibit). A line earns a permanent section (1-4) only when ≥2 distinct source exchanges support it, re-derived from the current sources every pass (no stored counter to poison); `Lately` is the dense recent-window section holding thread-state (the open thread, the open question), not outcomes. Per `Authority tiering` it is citable but yields to this run's measured `DerivedMetric` on a factual conflict and never lowers the safety floor. Caps keep it to one screen for every runner.
_Avoid_: letting the writer read its own prior profile (that is the echo loop the rewrite-from-source rule forbids); storing an inferred behavioral verdict ("ignores easy days") rather than a stated fact; adding a retrieval/ranking layer over it (it is surfaced whole — if it grows too big, tighten the writer); treating it as able to override measured data or the safety floor.

## Goals and plans

The forward-looking kind of `Stated memory`: something the runner has said they intend to do, carried with a date of relevance — a race ("Valencia half, sub-1:45, October"), a standing target ("sub-20 5k eventually"), or a near-term plan ("4 days a week next month"). Distinct from `Stated intent`, which is what a single past session was meant to be: goals and plans point forward and steer the coach's direction until they are reached, retired (the runner switches goals), or pass their date. A dateless aspiration is held open rather than expired. Never inferred by the coach — only recorded from what the runner stated.
_Avoid_: conflating with `Stated intent` (per-session, backward-looking); treating a goal as permanent (it can be retired or superseded); the coach inventing a goal the runner did not state.

## Voice

How the coach talks, as a personalization dial independent of `Coaching stance`: warm vs blunt, cheerleader vs drill-sergeant, terse vs expansive, playful vs clinical. Runner-sovereign: declared by the runner at onboarding (placed by choosing a preset, then nudging the four operable dials Clinical↔Warm, Earnest↔Playful, Gentle↔Blunt, Calm↔Fired-up, plus a free-text escape-hatch) and re-dialled explicitly any time. The coach does NOT secretly infer voice from how the runner responds (that would violate the deterministic-write-back rule and has no ground-truth signal); it may raise the voice question out loud and let the runner decide. Voice may flex freely: it reshapes framing and delivery only, never the facts, the safety floor, or the data the coach grounds on.
_Avoid_: conflating voice with `Coaching stance`; letting a preferred voice soften a data-warranted message (tone changes, substance does not); treating runner free-text as instructions rather than untrusted tone-data; calling explicit re-dialling "adaptive refinement".

## Coaching stance

What the coach focuses on and the training philosophy behind it, as a personalization dial independent of `Voice`. Stance has two operable parts the runner sets: a **selected school of thought** from the `Coaching corpus` (e.g. an aerobic-base durability lens, a polarized-quality lens, an enjoyment-and-consistency lens for a recreational runner), and **two emphasis axes** — *Data ↔ Sentiment* (lead with the numbers vs lead with how it felt) and *Process ↔ Outcome* (foreground habits and execution vs results, PRs, and goals). The emphasis axes are content-focus, not tone, so they stay orthogonal to `Voice` (a blunt voice can foreground encouragement; a warm voice can be accountability-heavy); accountability vs encouragement is deliberately NOT its own axis, being reachable via `Voice` warmth plus sentiment emphasis. Stance is runner-sovereign like `Voice` — declared by the runner and re-declared explicitly any time, never secretly inferred — but unlike `Voice` it is tethered: it reweights emphasis and method-framing only within the bounds of the runner's actual goal and what the data supports, and never overrides measured data or the safety/grounding floor. The coach does not give a runner training that contradicts their real goal because a stance was selected. A runner declares stance by selection and dials only; free-form coaching philosophy the runner supplies is `User materials`, not stance.
_Avoid_: letting stance license advice the data does not support, or treating it as fixed; conflating its emphasis axes with `Voice` tone; treating runner-supplied philosophy text as stance rather than `User materials`. It is goal-tethered and re-declared by the runner, not secretly inferred.

## Coaching corpus

The retrievable knowledge layer the coach grounds its coaching *judgment* in (as opposed to its *facts*, which come from the data): a compact set of always-present house coaching principles, a deeper retrievable library of training schools of thought (selected and weighted by `Coaching stance`), and the runner's own `User materials`. Distinct from `Durable memory` (what the relationship has learned about THIS runner) and the raw store (measured data): the corpus is coaching knowledge, not runner state.
_Avoid_: treating the corpus as fact (it informs judgment, not grounding), or letting corpus text issue instructions to the coach.

## User materials

Coaching content the runner supplies to the relationship: their own methodology, a human coach's plan, a physio rehab protocol, race-day plans, a book passage that resonates. Ingested into the `Coaching corpus` as a high-authority source for `Coaching stance` (it beats the house philosophy, since it is *their* coach), but per `Authority tiering` never overrides measured data or the safety floor, and always treated as reference data the coach reasons about, never as instructions it obeys (untrusted input). User materials **augment** the runner's selected school rather than replacing it: they sit above the house philosophy in authority and win on conflict, while the selected school still colours everything they are silent on. The coach reasons over a compact distillation of each material (the corpus's own emphasis shape), not the raw document; the raw text stays the source of truth, retrieved only on demand. This is the relationship's home for runner-supplied coaching philosophy, which `Coaching stance` deliberately excludes — a runner who wants a philosophy outside the house schools supplies it here.
_Avoid_: treating user materials as commands, as fact, or as able to lower the safety floor; treating a material as replacing the runner's selected `Coaching stance` rather than augmenting it.

## Authority tiering

The explicit precedence order resolving conflicts between the coach's knowledge sources, highest first: the safety/grounding floor; this run's measured data (`DerivedMetric`); deterministic durable facts (training load, `RunnerBaseline` norms, confirmed confounds); user-asserted facts (`Stated memory`); `User materials` and philosophy; house principles; the schools corpus; base-model generic knowledge. The load-bearing calls: user materials beat the house philosophy for `Coaching stance`, but never override measured data or the safety floor, and user-asserted facts yield to measured data on a factual conflict. Mirrors the standing rule that a deterministic durable fact never overrides today's re-derived `DerivedMetric`.
_Avoid_: letting any lower tier (corpus, materials, user assertions, generic knowledge) override measured data or the safety floor.

## Notification

A side-channel delivery of an `Exchange` to the runner — distinct from the in-app artifact. The `CoachReport` is the stored record of that exchange (under A3 / ADR 0009 its product is a human prose `message`, not a structured form; older rows are the legacy structured shape), rendered by the frontend; a notification is one transmission of that message (or a representation of it) to an external channel such as Telegram or email.

A notification is at-most-once per `Exchange` stage per channel: a two-stage exchange (ADR 0010) sends one opener notification and, if a fuller turn fires, one fuller notification, each deduped by its own sentinel (see `Notified at`). The sentinels live on the `Exchange` row since A1 (ADR 0011; before that, on `Activity`), not on the `CoachReport`, because re-generating a report (e.g., `force=true`) must not re-fire a notification.

## Notifier

The abstraction for sending a notification. Modelled as a port (`NotifierPort`) with adapters per channel, mirroring `StravaPort` (see [ADR 0002](docs/adr/0002-strava-adapter-is-pure-transport.md)). Today: `TelegramNotifier` (HTTPS Bot API) is the deployed channel because Railway blocks outbound SMTP, `SMTPNotifier` is the local/Pro-plan email fallback, `InMemoryNotifier` is for tests, and `NoOpNotifier` is used when no channel is configured. Adding a new channel means adding a new adapter, not modifying the port.

## Notified at

The per-stage sentinels for notification dedup: timestamp columns on the `Exchange` row (A1, ADR 0011) indicating that a notification has been successfully sent for that stage of the exchange. A two-stage `Exchange` (ADR 0010) carries one per stage (the opener sentinel and the fuller-turn sentinel, the latter also marking the exchange closed); a single-stage exchange uses only the fuller-turn sentinel (on `Activity`, the legacy home the single-shot rollback path still uses). Null means "not yet notified" for that stage. Set after a successful send; left null on failure so retries can re-send.

## Process new activity

The convergence pipeline triggered when a previously-unseen activity appears, regardless of source (Strava webhook `create` event or polling discovery). One pipeline: ingest → analyze → generate coach report → notify. Both source paths enqueue the same job; the `Notified at` sentinel makes re-entry safe.

## Polling job

A scheduled catch-up that periodically asks Strava for recent activities, diffs against the local DB, and enqueues `Process new activity` for each unseen one. Exists alongside the webhook path, not instead of it. Rationale in [ADR 0004](docs/adr/0004-activity-notification-dual-source-pipeline.md).

## Activity ingestion

Persisting a Strava activity (and its streams) into the local DB. Does not include analysis or notification. Composed with those steps at the job/orchestrator layer per [ADR 0003](docs/adr/0003-ingestion-does-not-call-analyze.md).

## Activity analysis

The deterministic processing pipeline that takes a persisted `Activity` + its `ActivityStream` rows and produces a `DerivedMetric`. Does not include coach report generation or notification.

## Coach report generation

The LLM-driven step that builds a context pack from a `DerivedMetric`, calls Anthropic, validates the response against the schema and the policy validator, and persists a `CoachReport`. It is one heavyweight form an `Exchange` can take, not the relationship itself. Under A3 (ADR 0009) the generated product is a human prose `message` written before any structure (the thin structured tail carries only affordances and memory hooks); the policy validator polices the full prose plus tail, and the stored `CoachReport` is the exchange record. Distinct from notification: a report can exist without ever being notified (e.g., older runs, low-confidence runs delivered only on-demand via the UI).

## Activity classification

The set of independent descriptors `Activity analysis` assigns to an activity, replacing the former single mutually-exclusive `activity_class` label. An activity is described along several orthogonal axes at once, because a real effort is several things at once (a long run can be run at threshold; an interval session is short, structured, and hard). The axes are sport-agnostic by design so the coach can reason about every cardio effort on one timeline. Rationale in [ADR 0007](docs/adr/0007-activity-classification-is-orthogonal-axes.md).
_Avoid_: activity class, activity type — both implied a single label.

## Effort

The intensity axis of `Activity classification`: how hard the effort was, derived from heart rate relative to the runner's maximum. Universal — applied to any activity with heart-rate data, whatever the sport. Distinct from `Duration` (how long) and `Structure` (the shape of the effort).
_Avoid_: intensity (reserve for informal use).

## Duration class

The length axis of `Activity classification`: whether an effort is long or standard relative to the runner's own recent efforts of the same sport. Relative, not absolute — "long" is defined against the individual's recent history, never a fixed time. Indeterminate for the first efforts in a runner's history, before there is enough history to compare against.

## Structure

The shape axis of `Activity classification`: whether an effort is continuous or composed of repeated work/rest intervals. Independent of `Effort` — a steady run and an interval session can reach the same average intensity.

## Terrain

A modifier in `Activity classification` describing the route as flat or hilly. A qualifier on an effort, not a class of its own — any run can be hilly.

## Race

A modifier in `Activity classification` marking a competitive effort, taken from the runner's `Stated intent` or the activity name, never inferred from the data.

## Headline

The single human-readable label for an activity (e.g. "Long run (tempo)"), composed from the `Activity classification` axes at read time. Presentation only — derived from the axes, never stored as a source of truth. Replaces the former stored `activity_class`.

## Stated intent

What the runner meant a session to be (`user_intent`), as opposed to what the `Activity classification` axes show was executed. The two are separate and may legitimately disagree; that gap is coaching signal, not an error. Stated intent overrides the `Headline` the runner sees but never overwrites the measured axes.

## Perceived effort

How hard a session felt to the runner, captured as `CheckIn.rpe` (a Borg-style 1-10 rating), as opposed to `Effort`, which is the intensity measured from heart rate. The two can legitimately disagree, and that gap is coaching signal, not error: when a confounder suppresses heart rate (heat, for example) a run can feel hard while `Effort` reads easy. The coach weights perceived effort above the heart-rate read when a `discount_signals` confounder fired, because perception survives the distortion. This mirrors the `Stated intent` vs measured-axes gap, moved from intent-vs-execution to perception-vs-physiology.
_Avoid_: treating RPE as a synonym for `Effort`; they are the subjective and measured sides of the same question.

## Adherence

Whether the runner appears to have acted on the coach's prior advice, judged from their own subsequent runs at zero extra effort. The unit is the `Next-step outcome`: for each `next_step` the last report emitted, the runner's next comparable activity is labelled `acted-on`, `ignored`, or `contradicted` by re-deriving from its `Activity analysis`. Adherence is advisory and never a compliance score or a moral judgement: the coach uses it to advance the relationship (acknowledge follow-through, gently revisit a miss), never to scold.

A label fires only on a comparable, non-noisy run. "Comparable" means the subsequent run is a fair test of that advice: easy-discipline advice is judged against the next run that was not a race, a detected interval session, or a declared workout (a clearly-deliberate hard effort is never counted against easy advice), and the strong `contradicted` verdict is only asserted when the runner's `Stated intent` for that run was explicitly easy, so an unlabelled run that came out hard is softened to `ignored` (it may have been a deliberate session) rather than treated as defiance. A low-confidence `DerivedMetric` is noise and abstains; a window theme abstains until the runner has had enough comparable runs to fairly call a miss. Adherence is contrast about past advice and never overrides the re-derived `DerivedMetric`, which remains the ground truth about what happened today.
_Avoid_: framing adherence as compliance, obedience, or a score; it is a coaching observation, not a verdict on the runner.

## Disputed

The `Next-step outcome` label when the runner has explicitly pushed back on the prior advice (a `CheckIn` note or a chat reply saying it was off). Explicit feedback beats the noisy implicit read: a disputed outcome is not non-adherence but a legitimate correction the coach takes the runner's word on and adapts to. Mirrors the `Stated intent` precedent that the runner's stated meaning overrides what the data alone would imply.

## Calibrated correction

Reading a run's signal against the runner's OWN typical value for the same conditions rather than a population rule of thumb: "your HR drift was 12%, vs your typical ~5% for these conditions" instead of "drift over 5% means fatigue". Computed at read time from the runner's prior comparable runs in the same `effort|terrain|temperature-band` bucket. It abstains to a LABELED population heuristic until enough comparable history accrues, so a confident personal claim never outruns the evidence. It refines interpretation and never overrides the re-derived `Activity analysis`.
_Avoid_: stating a population threshold as if it were this runner's established norm before the baseline is sufficient.

## Referral nudge

A deterministic, pipeline-owned suggestion to consider a healthcare professional, fired only on a computable red-flag pattern (several strain signals together; pain persisting across runs) and surfaced as a non-diagnostic prompt. It is the permitted form of the medical-scope boundary: it never names a condition, uses a diagnosis verb, or asserts what a pattern means, and its text is written to pass the deterministic policy validator (which still governs it). It abstains rather than ever fabricating a health concern. This keeps the product inside the general-wellness lane.
_Avoid_: treating the referral nudge as a diagnosis, a screening result, or a clinical claim; it is a general "worth getting this looked at" prompt, nothing more.

## Training load

The deterministic read of the runner's current condition (fresh, fatigued, ramping, or detraining), built as our own acute/chronic/balance model from the per-activity load primitive (`effort_score`), so it is device-independent and auditable. It resolves into three named reads the coach reasons over: **fitness** (the slow-moving chronic load the runner has accumulated), **fatigue** (the fast-moving acute load they are currently carrying), and **form** (the balance between them — freshness when fitness outweighs fatigue, deep fatigue when the reverse), plus how fast fitness is ramping. A tier-3 deterministic durable fact (see `Authority tiering`): it grounds the coach's judgment of readiness but never overrides this run's measured data or the safety floor. While a runner's history is shorter than the chronic window the read is still **warming up** and abstains from a confident condition verdict rather than guessing. Platform numbers (Strava Relative Effort/Fitness, Garmin Training Load) are used only to validate our computation, never as the authoritative value and never even as a cold-start seed (they are a different unit on a different scale); a divergence is a signal to fix our model, not to defer to theirs. Depends on a correct per-activity load primitive (the comparable `effort_score`, #186, now shipped).
_Avoid_: treating a platform's load number as our source of truth or as a cold-start seed; asserting a confident condition while the chronic baseline is still warming up; or letting the readiness read override this run's measured data or the safety floor.

## Block

A deterministically-detected group of temporally-contiguous activities treated as one training event: the walk→run→row→bike done back-to-back in a morning is one block; a solo run is a block of one. Grouping is by time-gap clustering (auditable); per-activity `Activity analysis` is unchanged underneath (each activity's measured metrics remain the truth), and the block adds an aggregate layer (combined load, the sequence and shape of the bout). The coach reasons and speaks about the block, not each sub-activity: one `Exchange` per block, fired once the block looks complete. The runner can split or merge a grouping the detector got wrong.
_Avoid_: treating a block as a replacement for per-activity analysis (it composes it, never overrides it), or assuming the time-gap grouping is always right.

## Schedule

The runner's forward-looking view of training: what is planned, what has already happened, and how the two compare, always for one week at a time. It renders every runner the same screen regardless of how much structure they want — a rigid weekday plan, a loose week of `Planned session`s with only rough placement, or no plan at all, in which case the screen shows the runner's actual training measured against their own norm rather than against sessions that do not exist. Backed by at most one active plan per runner; "no plan" is the absence of that plan, not a flag on one. Distinct from `Training load`, which reads backward from what happened: the schedule reads forward from what is intended, and the two meet only at the moment a `Planned session` is completed.
_Avoid_: treating the schedule as a to-do list the runner keeps — it is a coaching artifact the coach proposes and revises, not a checklist the runner maintains; assuming a runner with no plan has nothing to look at (free mode is the same screen, not a blank one).

## Planned session

One training session — the schedule's atomic unit, describing something the runner is meant to do or has been told they might want to do. Named along three independent axes: `Placement` (where in the week it sits), commitment (committed, part of the plan, or suggested, an idea that leaves no trace if ignored), and discipline (run, walk, bike, strength, row). Each also carries an intent (rest, easy, long, quality, strength) that is the coaching read of what the session is FOR; discipline is orthogonal to it — an easy bike ride and an easy run are the same stimulus — so intent carries the read and discipline is named alongside it, never folded into it. That intent is the plan's own, declared before the session happens, and is distinct from `Stated intent`, which is the runner's label on an `Activity` they have already done; a completed planned session has both, and they can disagree. Converges to completion through one writer regardless of how it was finished: auto-matched from a synced `Activity`, tapped done on the card, or told to the coach in a `Thread`.
_Avoid_: using "session" for anything but a training session — this domain already overloads the word once (see `Thread`), and a planned session is the training sense; treating discipline as if it changes the read (an easy row is still easy; only intent says that).

## Placement

Where in the week a `Planned session` sits, expressed as one inclusive window rather than a chosen mode. A session pinned to one day is a window of width one; a session floating anywhere in the week is a window spanning the whole week; anything between is floating within that range. There is no separate flag recording which of the three the runner intended — the window's width is the only fact stored, and pinned, windowed, and week-floating are all read off it, never written to it. As days pass, a floating session's effective window narrows to today through its end at read time; the stored window itself never moves, so a session can always say what it was originally for.
_Avoid_: treating placement as a mode the runner picks and the system remembers separately from the dates — the window is the single source of truth, and a second "mode" field would just be a copy of the same fact that could disagree with it; assuming a narrowed window means the session moved (it means time passed).

## Spacing rule

A constraint on WHEN sessions may fall relative to each other within a week — a full rest day after the long run, no quality session the day before it, a minimum gap between two intents, only certain weekdays, at most so many sessions in a day. Rules are first-class, closed-vocabulary data with a pure predicate per kind, which is what makes a violation detectable rather than merely readable prose in a chat message: a runner's flexible week can be checked for whether a legal arrangement of its sessions exists at all, and a coach-generated plan can be rejected for breaking its own rules before it is ever stored.
_Avoid_: putting a claim about what good training IS into a spacing rule — a rule governs timing only, never doctrine; doctrine belongs to the `Coaching corpus`. "No quality the day before a race" is a spacing rule; "quality work builds fitness faster than volume" is not, and never becomes one.

## Goal race

The race a `Schedule`'s current plan is built around, or one the runner expects to run through along the way: a name, a date, a distance, and the runner's own priority (A = what the plan bends around, B/C = raced but not the anchor). The plan's horizon, phases, and taper are measured backward from the goal race's date. Distinct from `UserProfile.upcoming_races`, the earlier untyped free-form list nothing in the backend ever read — a goal race is the typed, queryable replacement the schedule anchors to, not another name for the same field.
_Avoid_: equating a goal race with `UserProfile.upcoming_races`; treating priority as a claim about the runner's fitness or chance of success rather than their own ranking of which race matters.
