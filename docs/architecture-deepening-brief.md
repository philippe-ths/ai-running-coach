# Architecture Deepening Brief

Implementation brief for the three "Strong" candidates from the 2026-05-27 architecture review.

Vocabulary used throughout: **module**, **interface**, **implementation**, **deep**, **shallow**, **seam**, **adapter**, **leverage**, **locality**. See the report for definitions.

## Approach

Three milestones, delivered sequentially, one PR each, independently shippable. Test-driven, with one wrinkle to keep refactors honest:

**TDD shape for behavior-preserving refactors:**

1. **Characterize.** Before touching code, write tests that snapshot the current outputs of the module under change. These tests target the existing interface and assert against real fixture data. They start green.
2. **Re-aim.** Rewrite those same tests against the new interface. They go red.
3. **Implement.** Build the new module until the re-aimed tests pass.
4. **Migrate callers.** Update every call site to the new interface. All other tests stay green.
5. **Prune.** Delete tests on shallow internals that are now covered through the new interface. Keep tests on algorithmically deep internal seams.

Each milestone follows that loop.

## Cross-cutting principles

- **The interface is the test surface.** Tests assert on observable outputs through the new interface, not on private state.
- **One adapter is a hypothetical seam.** A port is only introduced where two adapters are justified (production + test).
- **Behavior must not change** across any milestone, except where explicitly listed under "Behavior change" in a milestone's scope.
- **Migrations are reversible.** Every alembic migration ships with a working downgrade.
- **Cache compatibility.** The coach report cache key is a hash of the context pack. Any change that re-hashes existing packs invalidates cached reports and must be called out explicitly.

---

## Milestone 1 — Collapse the activity analysis pipeline

### Context

`backend/app/services/processing/engine.py` is a 212-line orchestrator that calls nine submodules in sequence (`metrics`, `classifier`, `intervals`, `flags`, `risk`, `splits`, `stops`, `smoothing`, `workout_matching`). The orchestrator carries no test of its own integrated sequence; coverage lives entirely in per-submodule unit tests.

`engine.py:182` reaches across into `app.services.coach.context._build_training_context` — the analysis pipeline depends on a private helper inside the coach pipeline. That is a real cross-module leak, not a hypothetical one.

### Scope

- Create `backend/app/services/analysis/` package.
- Move existing files into the package:
  - `processing/engine.py` becomes `analysis/_orchestrator.py` (private)
  - `processing/{metrics, classifier, intervals, flags, risk, smoothing, stops, splits, workout_matching}.py` move under `analysis/` as private internals (filenames may stay; the convention is that they are not imported from outside `analysis/`)
- `analysis/__init__.py` exposes exactly one external interface:

  ```python
  def analyze(db: Session, activity_id: str) -> DerivedMetric: ...
  def analyze_with_streams(db: Session, activity_id: str) -> DerivedMetric: ...  # async equivalent of current process_deep
  ```

- Move `_build_training_context` out of `app/services/coach/context.py` into `analysis/_training_context.py`. Persist its output on `DerivedMetric` as a new column `training_context: dict` (JSONB).
- Add alembic migration `add_training_context_to_derived_metric.py` with a working downgrade.
- Update `app/services/coach/context.py` to read `metrics.training_context` from `DerivedMetric` instead of recomputing.
- Update callers: `app/api/activities.py`, `app/jobs/strava_sync.py`, `app/services/activity_service.py`. All previous `engine.process_activity` and `engine.process_deep` callers now import from `app.services.analysis`.
- Replace `backend/tests/test_analysis.py` with interface-level tests on `analyze(db, id)`.
- Delete `backend/tests/test_risk.py` (rule plumbing, covered through the interface). Roll its assertions into the new interface tests where they add coverage that the orchestrator-level test misses.

### Out of scope

- Restructuring `intervals.py`'s detection algorithm.
- Adding new flags, risk signals, or classifier rules.
- Changing `DerivedMetric` columns other than the new `training_context`.
- Touching `app/services/coach/` beyond updating the `_build_training_context` import and consuming `metrics.training_context`.
- Changing the API or frontend.

### Behavior change

- `DerivedMetric.training_context` exists where previously it did not. Coach reports generated after this milestone will see this field in their context pack at the path `metrics.training_context` (currently it appears at top-level `training_context`). **This re-hashes the context pack and invalidates all cached `CoachReport` rows for prior activities.** New reports regenerate on next access.

### Success criteria

1. `from app.services.analysis import analyze, analyze_with_streams` is the only sanctioned import path. No code outside `app/services/analysis/` imports any other symbol from the package.
2. `app/services/processing/` no longer exists.
3. `coach/context.py` does not contain `_build_training_context` and does not import from `app.services.analysis` except for the public interface (it should not need to).
4. `make backend-test` passes with the new interface tests in place.
5. `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` round-trips cleanly on a fresh database.
6. Manual smoke: ingest one Strava activity end-to-end via the existing `/api/sync` flow and confirm a `DerivedMetric` row with populated `training_context` and a coach report that uses it.

### TDD steps

1. **Characterize.** Write `test_analysis_interface.py` that pins current behavior:
   - Load a representative fixture activity (real shape, possibly seeded from one of the existing test fixtures).
   - Call `engine.process_activity(db, activity_id)` and snapshot every field of the resulting `DerivedMetric` as a JSON file under `backend/tests/snapshots/analysis/`.
   - Assert equality. Test passes green against current code.
2. **Re-aim.** Change the test to call `from app.services.analysis import analyze` and assert the snapshot still matches. Test goes red because the import does not exist.
3. **Implement.** Create the package, move files, expose `analyze` and `analyze_with_streams`. Re-implement the engine→coach.context leak as a local helper. Test goes green.
4. **Migration.** Add the alembic migration and the `training_context` column. Update the orchestrator to populate it. Update `coach/context.py` to read from it. Adjust the snapshot to include the new field. Test goes green.
5. **Migrate callers.** Update every import in `app/api/`, `app/jobs/`, and `app/services/activity_service.py`. Existing tests stay green.
6. **Prune.** Delete `test_risk.py`. Roll any unique assertions into `test_analysis_interface.py`. Keep `test_intervals.py`, `test_stream_metrics.py`, `test_workout_matching.py`, `test_units_cadence.py` — they cover internal seams with genuine algorithmic depth.

---

## Milestone 2 — Type the coach context pack

### Context

`app/services/coach/context.py:24` declares it has no logic, only data gathering. Its return value is an untyped `dict` whose shape is implicit. Downstream code couples to that shape: `validator.py` reads paths like `context_pack.get("metrics", {}).get("zones_calibrated")`, `service.py` hashes it for cache keys, `llm.py` and `prompts.py` serialize it into the model call. `test_coach_context.py` is 305 lines, more than 1.5× the module it tests, because every test has to hand-build the dict.

### Context

(continued — depends on M1)

After M1, the context pack reads `metrics.training_context` from `DerivedMetric` rather than recomputing. M2 builds on that.

### Scope

- Define `app/schemas/coach_context.py` with a `CoachContextPack` Pydantic model mirroring the current dict structure exactly:
  - Top-level keys: `activity`, `metrics`, `check_in`, `profile`, `training_context`, `recent_training_summary`, `safety_rules`.
  - All nested types explicit. `Optional[...]` everywhere the current dict uses `None`.
- Change `build_context_pack(db, activity) -> CoachContextPack`.
- Move `hash_context_pack` onto the model as `CoachContextPack.fingerprint() -> str`. Implementation uses `model_dump_json(sort_keys=True, ...)` to produce a JSON serialization that is byte-identical to the current `json.dumps(pack, sort_keys=True, default=str)` output. **This is load-bearing for cache compatibility.**
- Change `validate_policy(content, context_pack: CoachContextPack)` signature. Replace every string-key lookup with attribute access.
- Update `service.py`, `chat.py`, `prompts.py`, `llm.py` call sites.
- Drop ~150 lines of test setup from `test_coach_context.py` by using `CoachContextPack` constructors. Add round-trip tests.

### Out of scope

- Restructuring the pack (e.g., flattening or renaming keys). M2 mirrors the current shape exactly.
- Schema versioning beyond keeping the existing hardcoded `"1.1"`.
- Adding fields to the pack.
- Changing the validator's rule set or the LLM prompt.
- Changing what gets passed to Anthropic.

### Behavior change

None. Cache keys must remain identical for activities whose `DerivedMetric` is unchanged.

### Success criteria

1. `build_context_pack` returns a `CoachContextPack` instance.
2. `validate_policy` accepts `CoachContextPack` and rejects raw `dict` at the type-check level.
3. For any fixture activity used in tests, `CoachContextPack.model_validate(legacy_dict).fingerprint() == hash_context_pack(legacy_dict)` — round-trip preserves the cache hash.
4. No remaining string-key lookups against the pack outside `CoachContextPack.model_validate` itself. Grep for `context_pack.get(` and `context_pack[` must return zero matches in `app/services/coach/`.
5. `test_coach_context.py` is at least 100 lines shorter than today.
6. `make backend-test` passes.

### TDD steps

1. **Characterize.** Write `test_coach_context_pack.py` with a hash-stability test: build the current dict from a fixture, hash it, then assert `CoachContextPack.model_validate(dict).fingerprint() == previous_hash`. Currently red because `CoachContextPack` does not exist.
2. **Implement schema.** Create `CoachContextPack` until step 1 is green. The Pydantic model must serialize via the exact same JSON form `hash_context_pack` produces.
3. **Re-type build_context_pack.** Failing test: `assert isinstance(build_context_pack(db, activity), CoachContextPack)`. Implement.
4. **Re-type validator.** Failing test: pass a `CoachContextPack` into `validate_policy` and assert the same violations as today's dict-based call. Implement attribute-access conversion.
5. **Migrate remaining call sites.** Each call site gets a small failing assertion (type, or attribute access works), then the implementation update.
6. **Prune.** Compress `test_coach_context.py` by replacing hand-built dicts with `CoachContextPack(...)` constructors.

---

## Milestone 3 — Strava ingestion as one module with a port

### Context

Today the Strava integration is split across two modules with blurry responsibilities:

- `app/services/strava/client.py` (155 lines) is described as the HTTP client but also mutates `StravaAccount` (refresh-token writes at line 68).
- `app/services/activity_service.py` (154 lines) is glue: it composes `client.ensure_valid_token` → `client.list_activities` → `upsert_activity` → `client.get_streams` → `engine.process_activity`.

There is no test adapter for Strava. Every test that touches ingestion mocks `httpx` ad hoc.

### Scope

- Create `app/services/strava_ingestion/` package.
- Define `StravaPort` as a `typing.Protocol` exposing the Strava operations the deep module needs:
  - `exchange_code(code: str) -> Tokens`
  - `refresh_token(refresh_token: str) -> Tokens`
  - `list_recent_activities(access_token: str, since: datetime) -> list[dict]`
  - `get_activity_streams(access_token: str, activity_id: int, keys: list[str]) -> dict`
  - The port returns plain data; it does not touch the DB.
- Implement two adapters:
  - `HTTPStravaAdapter` — production. Replaces today's `strava/client.py`. Pure HTTP, no DB.
  - `InMemoryStravaAdapter` — for tests. Seedable with canned activities and streams.
- Implement the deep ingestion module `app/services/strava_ingestion/__init__.py` exposing:

  ```python
  async def ingest_activity(db, account, raw_activity, port: StravaPort) -> Activity: ...
  async def ingest_recent_activities(db, account, port: StravaPort, since=None) -> list[Activity]: ...
  ```

  The deep module owns: token expiry check, calling `port.refresh_token` when needed, persisting new tokens, fetching streams, idempotent upsert of `Activity` and `ActivityStream`, partial-failure logging.
- Ingestion does **not** call `analyze()`. Callers compose:
  - `app/api/activities.py` sync endpoint: ingest, then loop over returned activities and call `analyze`.
  - `app/jobs/strava_sync.py` webhook job: same.
- Wire the production port in a dependency module so tests can inject `InMemoryStravaAdapter`.
- Delete `app/services/strava/client.py` and `app/services/activity_service.py`.
- Existing `test_sync_integration.py` and `test_strava_auth.py` and `test_webhooks.py` migrate to using `InMemoryStravaAdapter` instead of mocking `httpx`.

### Out of scope

- Adding retry policy, rate-limit backoff, or batch optimization (the review noted these as opportunities but they are net-new behavior; this milestone preserves current semantics).
- Changing the Strava OAuth flow surface.
- Changing what gets ingested (same fields, same streams).
- Touching the analysis pipeline (M1) or coach (M2).

### Behavior change

None. Same activities ingested, same streams stored, same upsert semantics, same error logging.

### Success criteria

1. `app/services/strava/` and `app/services/activity_service.py` no longer exist.
2. `HTTPStravaAdapter` does not touch the database. Grep `Session` or `db.add` or `db.commit` in the adapter file returns zero matches.
3. `InMemoryStravaAdapter` exists and is used in at least three test files.
4. Token refresh policy lives in the deep ingestion module, not in the adapter.
5. `ingest_recent_activities` does not call `analyze`. Callers (`api/activities.py`, `jobs/strava_sync.py`) call it explicitly.
6. `make backend-test` passes. `test_sync_integration.py` runs without `httpx` mocks.
7. Manual smoke: trigger a real Strava sync end-to-end against a real account and verify activities ingest and reports generate.

### TDD steps

1. **Characterize.** Snapshot current sync behavior with a fixture: given a canned Strava response, what `Activity` and `ActivityStream` rows result? Assert against the current `activity_service.sync_recent_activities` call path.
2. **Define port + in-memory adapter.** Failing test: `InMemoryStravaAdapter` seeded with the same canned data, called through the (not-yet-existing) `ingest_recent_activities`, must produce the same rows. Currently red.
3. **Implement deep module.** Get the test green. Token refresh covered by a separate test: seed the adapter with an expired token, assert the module calls `port.refresh_token` and persists the result.
4. **Build HTTP adapter.** Mirror today's `strava/client.py` HTTP code into `HTTPStravaAdapter` minus the DB writes. A focused HTTP-shape test ensures the request body still matches what Strava expects (compare against today's outgoing request bytes).
5. **Migrate callers.** Update `api/activities.py` and `jobs/strava_sync.py`. Each loses one call (`sync_recent_activities`) and gains two (`ingest_recent_activities` + `analyze` per activity).
6. **Migrate tests.** Replace `httpx` mocks with `InMemoryStravaAdapter` injections in the three test files. Each test stays green through the migration.
7. **Prune.** Delete `app/services/strava/` and `app/services/activity_service.py`. Confirm no remaining imports.

---

## Sequencing summary

| | Milestone | Depends on | Schema change | Cache invalidation | Estimated PR size |
|---|---|---|---|---|---|
| M1 | Collapse analysis pipeline | none | yes (new column) | yes (training_context moves under metrics) | medium-large |
| M2 | Type coach context pack | M1 (reads new field) | no | no (hash preserved) | medium |
| M3 | Strava ingestion + port | none | no | no | medium |

M3 is technically independent of M1 and M2. Sequenced last per the agreed order to keep one refactor in flight at a time.

## Open questions to revisit during implementation

- `process_deep` (current async stream-fetch variant) becomes `analyze_with_streams` in M1. Confirm during implementation whether the existing `app/api/activities.py` deep-process endpoint needs both interfaces or whether the async one alone suffices.
- M2's hash compatibility requires `CoachContextPack.model_dump_json(sort_keys=True)` to produce byte-identical output to today's `json.dumps(..., default=str)`. Verify with a round-trip test against several real fixtures before committing to the new schema. If they diverge on edge cases (e.g., datetime serialization), the migration plan needs a one-time cache invalidation.
- M3's `HTTPStravaAdapter` request-shape test (TDD step 4) needs a reference of what Strava actually expects. Source it from current `strava/client.py` byte-for-byte, not from Strava's docs (the code is the ground truth for "what works today").

## Decisions worth recording as ADRs

If you want them captured for future architecture reviews so they aren't re-suggested:

1. `_build_training_context` moved into the analysis module and persisted on `DerivedMetric`. The earlier decision to colocate it with the coach module is reversed.
2. Strava OAuth token refresh policy lives outside the HTTP adapter. Adapters are pure transport.
3. The ingestion module deliberately does not call `analyze`. Callers compose.

Each of these is hard to reverse, surprising without context, and the result of a real trade-off — the criteria the grilling skill applies for ADR-worthiness.
