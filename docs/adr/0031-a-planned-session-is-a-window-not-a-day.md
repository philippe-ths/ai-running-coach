# A planned session is a window, not a day

The schedule (#830) has to serve three runners with the same screen: one who wants a rigid weekday plan, one who wants a loose four-session week and picks the days as they go, and one who wants no plan at all and just wants their week measured against their own norm. A design that models "the plan" as a single shape — a fixed weekday grid, say — serves the first runner and forces the other two into it.

## Decision

A `Planned session` (`backend/app/models/planned_session.py`) is described along three independent axes, and keeping them independent is what lets one screen serve all three runners.

1. **Placement** — where in the week it sits.
2. **Commitment** — `committed` (part of the plan) or `suggested` (the coach thinks it would serve the runner; ignoring it leaves no trace and generates no follow-up).
3. **Discipline** — run, walk, bike, strength, row. Orthogonal to `intent`: an easy bike and an easy run are the same stimulus, so intent carries the reading and discipline is named alongside it, never folded into it.

### Placement is a window, and pinning is derived

This is the central claim. A session stores one thing about where it sits: an inclusive `[window_start, window_end]`. There is no `placement` column and no mode flag. `pinned`, `window`, and `week` all fall out of the stored pair's width (`services/schedule/placement.py`, `derive_placement`):

- `window_start == window_end` — pinned to that day.
- the window spans the whole week — floating anywhere in the week.
- anything between — floating in a window.

A stored mode would be a second copy of a fact the dates already carry, and the two could disagree: the mode could say "pinned" while the window said Thursday to Saturday, and nothing would say which was true.

Deriving buys two more things for free. First, the "window narrows as days pass" behaviour the design asks for is just `max(window_start, today)..window_end`, computed at read time (`effective_window`) — no per-session job runs to make it true, and a session that has run out of days is `missed`, also derived, also not a flag. Second, the stored window never moves, so a session can always say what it was originally for; "narrowed from Thursday–Saturday" is a comparison between the stored window and the effective one, not an edit history.

### Rules are first-class data with a closed vocabulary

For a runner whose sessions float, spacing rules — "no quality run the day before the long run," "a full rest day after the long run" — *are* the plan. Buried in a chat message they are prose nobody can check; as a closed vocabulary with a pure predicate each (`services/schedule/rules.py`), a violation is detectable. Five kinds ship: `rest_day_after`, `no_intent_day_before`, `min_days_between`, `preferred_days`, `max_sessions_per_day`.

A flexible week does not have a placement to lint — most of its sessions have not been placed yet. The honest question is not "does this arrangement break a rule" but "does a legal arrangement exist," which is a small constraint-satisfaction problem: each session's domain is the days left in its window, the rules are the constraints, and a backtracking search with smallest-domain-first ordering settles it immediately for a week's worth of sessions.

Every predicate is monotone — adding a session can only add violations, never remove one — which is what makes it sound to prune a partial assignment during the search rather than exploring it to the end. This is a property to preserve, not an implementation detail: a predicate that broke it would make the pruning unsound. It is also what lets a coach-generated plan be rejected for breaking its own rules before it is ever stored, rather than merely flagged after the fact.

### Three tables, and why two of them are JSON on the plan

The schedule is three tables: `TrainingPlan`, `PlannedSession`, `GoalRace`. A `TrainingPlan` carries a `goal_race_id` pointer, a `horizon_end`, and two JSON columns — `rules` (`List[SpacingRule]`) and `week_shapes` (`List[PlannedWeekShape]`, the horizon's per-week load/mix/phase, concrete for the near weeks and sketch-only further out).

Both are JSON rather than their own tables for the same reason: both are small, bounded, always read whole, never queried independently of the plan, and rewritten wholesale on every revision. This is the `RunnerMemory` / `CoachingRelationship.receipt_templates` precedent applied here — a table earns its place by being queried on its own terms; these two are read and replaced as one unit every time, so a table would buy nothing but a join. Strict coercion through `schemas/schedule.py` keeps them typed data rather than prose: a rule the coach invents with an unrecognised `kind`, or a shape missing a required field, fails at the schema boundary rather than reaching the store, and `RULE_KINDS`/`PREDICATES` in `rules.py` are the single closed vocabulary both the schema's `Literal` and the checker draw from.

The concrete sessions are their own table because they are queried by date, joined to activities on completion, and updated one at a time — none of which is true of the rules or the shapes.

### "No plan" is the absence of a plan, not a flag

Free mode is not a second product. A runner with no active `TrainingPlan` reads the same week endpoint and the same screen, gets their logged actuals, and is measured against their own norm (the existing `training_volume`/Trends definition of "typical," #400) rather than against sessions that do not exist. `ScheduleWeekRead.has_plan` is `False` and `sessions` is empty; nothing else about the response shape changes.

### Completion converges on one writer

A session can be marked done three ways — auto-matched from a synced activity, tapped on the card, or told to the coach in conversation — and all three write the same `completed_at` / `completed_activity_id` / `completion_source` columns through one writer. This is the `write_checkin` precedent: the in-app check-in form and the Telegram tap both go through one write path rather than each owning a copy of "what it means to check in," and completion gets the same discipline. The columns land in this slice; the writer and the three call sites are the completion slice's job (#830, not yet done — see Not Done, below).

### One definition of "a run"

The schedule's running-km headline (`WeekHeadline.planned_running_distance_m` / `logged_running_distance_m`) is bound to `activity_facts.is_run`, the same predicate the Trends page and the coach pack's `training_volume`/`recent_training` sections already use, rather than a more generous list the schedule maintains on its own. `is_run` currently means `activity_type.lower() == "run"` exactly — it excludes `TrailRun` and `VirtualRun`. That is a known narrowing, not an oversight of this slice: widening what counts as a run is issue #644's job, and it is `is_run`'s job specifically, so the fix lands in one place and the schedule, Trends, and the coach pack widen together rather than drifting apart.

## Consequences

- A session's placement can never disagree with its stored dates, because there is nothing else to disagree with. Every "is it pinned" question is a comparison, not a lookup.
- The rule vocabulary is closed by construction (`Literal[RuleKind]` at the schema boundary, `PREDICATES` at the checker); a rule kind the checker cannot evaluate is reported rather than silently treated as satisfied (`violations_for`'s "this rule kind is not one the checker understands" path) — a plan can never claim a discipline it does not actually enforce.
- `PlannedSession.structure` (the `{"reps_planned", "rep_distance_m", "rest_s"}` shape) lands now, unwritten, because `workout_matching.match_planned_to_detected` has been waiting for a plan to compare against since the beginning and `_extract_planned_workout` has returned `None` as a placeholder all along. Wiring it is a later slice; the column exists now so the feature needs one migration rather than two.
- The rule search's soundness depends on every predicate staying monotone. A future rule kind that can be satisfied by *adding* a session (rather than only violated by one) would silently break pruning and needs its own search strategy, not a seventh entry in `PREDICATES`.

## Not done here

- `UserProfile.upcoming_races` is left in place. `GoalRace` replaces it functionally — nothing in the backend has ever read the old JSON blob, not the coach pack, not the thread turn, not one service — but removing it is its own dependency sweep (the frontend profile type still declares it, and the profile form still round-trips it), and the schedule does not need it gone to be correct. Recorded as follow-up rather than done quietly.
- The coach does not yet see the plan. Nothing in this slice threads a `schedule` section into the coach context pack or the prompt; the schedule is a readable, writable artifact today, not yet something the live production prompt reasons from. Reaching the prompt is a separate slice.
