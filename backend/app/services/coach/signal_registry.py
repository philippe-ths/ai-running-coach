"""The coach signal registry (#800) — ONE declaration per thing the coach receives.

Before this module, one coach signal was declared across six modules, and five of
those declarations RESTATED a fact another already held: its flat position
(``_FLAT_ORDER``), its coaching-question group (``_SECTION_GROUP``), its
prompt-version gate — twice, once on the ``PackSection`` descriptor and again on the
``ReadTimeSignal`` construction — and its kill switch, hand-written a third time as
an ``if not settings.X: return None`` inside the builder. Nothing forced them to
agree, so they drifted.

This module holds the declaration. Everything else DERIVES from it:

  - ``app.schemas.coach_context`` derives ``_SECTION_GROUP`` (the ADR 0026 group
    relocation map), ``_FLAT_ORDER`` (the flat serialization order) and
    ``PACK_SECTIONS`` (the byte-stable-drop registry) from ``COACH_SIGNALS``.
  - ``app.services.coach.context`` derives its ``ReadTimeSignal`` objects — each
    signal's prompt-feature gate and its whole-section kill switch — from the same
    rows, and only supplies the ``compute`` adapter.
  - ``app.services.coach.prompts`` keeps deriving its ``*_PROMPT_IDS`` sets and
    ``is_*_prompt`` predicates from ``prompt_features.PROMPT_FEATURES``, which this
    module references rather than restates.

Adding a signal is therefore: one row here, one builder, one prompt clause, one
manifest row, and the Pydantic field Pydantic requires be declared statically.

What this module deliberately is NOT
------------------------------------
It is PURE DATA plus pure dict post-processors. It imports the prompt-feature enum
and the settings CLASS (to check a declared kill switch names a real setting), and
nothing else from the coach layer — so ``coach_context`` and ``context`` can both
import it with no cycle. The ``compute`` adapters stay in ``context.py`` (they read
ORM rows, which is ``context``'s job) and are bound to these declarations there,
with a completeness check that fails at import if a declared read-time signal has
no adapter or an adapter has no declaration.

The ungated-signal rule
-----------------------
Every signal must state its gate EXPLICITLY: either a real ``COACH_*_ENABLED``
setting, or an ``ungated_reason`` recording why it has none. A signal that states
neither fails at import. This is the #800 criterion "a signal without a kill switch
fails loudly rather than shipping ungated" read as *state the gate*, not *invent a
flag*: most of these signals genuinely have no switch today, and minting a settings
field per gap to satisfy a check would be a behaviour change wearing a consistency
costume — each new flag is a live production lever nobody asked for. The reasons are
in the rows, so the gaps are readable rather than silent, and a test pins the ungated
list so a new one is a decision someone takes rather than a default they inherit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from app.core.config import Settings
from app.services.coach.prompt_features import PromptFeature

# The ADR 0026 coaching-question groups, in serialization order. A signal's `group`
# is one of these, or None for a top-level meta field (salience, safety_rules, the
# retired stubs).
GROUP_NAMES: Tuple[str, ...] = (
    "this_run",
    "right_now",
    "the_runner",
    "our_thread",
    "how_to_coach",
)


# --- Kill switches -----------------------------------------------------------


@dataclass(frozen=True)
class KillSwitch:
    """One ``COACH_*_ENABLED`` operator switch bearing on a signal (#522).

    ``setting`` is a field name on ``Settings`` (checked at import). ``effect`` says
    what OFF does to the pack, which is what decides where the gate can live:

      - ``"drop"``   — the whole section becomes None and vanishes from
        serialization. A drop switch on a read-time signal is applied by the shared
        seam (``read_time_signals.gather``) straight from this declaration.
      - ``"degrade"`` — the section is still emitted, in a reduced or empty form.
      - ``"trim"``   — a NESTED field inside the section is dropped; the section
        itself is unaffected.

    ``applied_in`` names where the gate actually runs, so a reader can find it.
    """

    setting: str
    effect: str
    applied_in: str
    note: str = ""


_EFFECTS = frozenset({"drop", "degrade", "trim"})


# --- The signal declaration --------------------------------------------------


@dataclass(frozen=True)
class ReadTimeAdapter:
    """A read-time history-scan (or read-stored) adapter feeding a signal's section.

    ``name`` is the seam key ``context.py`` registers its ``compute`` under.
    ``gate_feature`` is the prompt capability that must be present for it to run, and
    ``kill_switch`` the OPTIONAL drop-effect setting the seam applies before it.

    A section normally has exactly one adapter. ``training_history`` has two
    (mutually exclusive by prompt feature — the original 60d ladder and the ADR 0026
    Slice 2 rebased one), which is why this is a tuple per signal rather than a
    field on the signal itself.
    """

    name: str
    gate_feature: Optional[PromptFeature] = None
    kill_switch: Optional[str] = None


@dataclass(frozen=True)
class CoachSignal:
    """ONE thing the coach receives, declared once.

    ``field``        the flat pack section name (a declared field on the group model
                     or on ``CoachContextPack`` itself).
    ``group``        its ADR 0026 coaching-question group, or None for top-level meta.
    ``gate_feature`` the prompt capability that turns it on, or None for a section
                     that is not prompt-version gated.
    ``droppable``    True when the section must VANISH from serialization while None
                     (the byte-stable-drop rule); it then becomes a ``PackSection``.
    ``nested_drop``  the byte-stable trim that is not a simple top-level pop.
    ``adapters``     its read-time signal adapters, empty when it is built inline.
    ``kill_switches`` every operator switch bearing on it (see ``KillSwitch``).
    ``ungated_reason`` why it has NO kill switch. Exactly one of ``kill_switches`` /
                     ``ungated_reason`` must be non-empty.
    ``why``          provenance: the issue or ADR that earned the signal.
    """

    field: str
    group: Optional[str] = None
    gate_feature: Optional[PromptFeature] = None
    droppable: bool = False
    nested_drop: Optional[Callable[[Any, Dict[str, Any]], None]] = None
    adapters: Tuple[ReadTimeAdapter, ...] = ()
    kill_switches: Tuple[KillSwitch, ...] = ()
    ungated_reason: str = ""
    why: str = ""

    @property
    def drop_switches(self) -> Tuple[KillSwitch, ...]:
        return tuple(k for k in self.kill_switches if k.effect == "drop")


# --- The byte-stable nested trims -------------------------------------------
# These are pure post-processors over the SERIALIZED dict, applied by
# `to_serializable_dict` through the descriptor derived from each signal's row. They
# live beside the declaration that carries them (they are part of how a signal is
# framed for the coach), and touch no schema type, which is what keeps this module
# importable from `coach_context` with no cycle.


def drop_corpus_user_materials(corpus: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """P4 (#286): ``user_materials`` rides INSIDE the corpus section, but only under
    a user-materials-aware prompt. When None (every non-v7 corpus prompt) drop the
    nested key entirely, so the corpus section is byte-identical to its P1.2/P1.3
    shape under v4/v5/v6 (the activation boundary), exactly as the top-level
    Optional-and-drop idiom does for the section itself."""
    if corpus.get("user_materials") is None:
        corpus.pop("user_materials", None)


def drop_profile_body(profile: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """#742: `body` rides INSIDE the always-present profile section, but only under a
    body-aware prompt (and only when the runner has stated a figure). When None --
    every prior prompt, and any runner who has stated neither -- drop the nested key
    entirely, so the profile section is byte-identical to its pre-#742 shape. Same
    idiom as `drop_corpus_user_materials`; `profile` is never None itself, so its
    descriptor exists purely to carry the nested trim."""
    if profile.get("body") is None:
        profile.pop("body", None)


def drop_training_volume_rolling_current(
    tv: Dict[str, Any], data: Dict[str, Any]
) -> None:
    """#400 fold (one lane per fact): drop `current_all`/`current_runs` from the
    `rolling_7d` window only. That window spans the same trailing 7 days as
    `recent_training.last_7d`, so its raw totals (current_all == that window's
    total_*/activity_count, current_runs == its by_type Run entry) were a second copy
    of the descriptive section. `rolling_7d` keeps only the vs-norm VERDICT
    (norm + direction + pct); the actual trailing-7d numbers live in `recent_training`.
    `calendar_week` is left untouched — it is the current Monday-to-date window, which
    no other section carries. Re-parse stays safe: both fields are Optional and default
    to None when absent.

    GATED on `recent_training` being present: the fold only holds when the descriptive
    lane exists to carry the numbers (recent-training-aware prompts, v11+). Under a
    volume-aware-but-not-recent-training prompt (v9/v10) `recent_training` is absent, so
    we KEEP rolling_7d's current values — dropping them there would lose the trailing-7d
    numbers entirely. This is the ONE cross-section trim, and the reason the drop loop
    runs in declaration order: `training_volume` is declared before `recent_training`,
    so the latter's raw value is still readable here."""
    if not data.get("recent_training"):
        return
    rolling = tv.get("rolling_7d")
    if not rolling:
        return
    for comp in rolling.get("metrics", []):
        comp.pop("current_all", None)
        comp.pop("current_runs", None)


def drop_recent_training_dedup(rt: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """#444/#451 pack trim: drop the deduplicated/empty fields from the
    recent-training section so they cost no tokens. Per window: the basis strings
    live once on the window (None on previous_30d). Per comparison: current_all/
    current_runs (already in the window totals/by_type and in training_volume), the
    per-row basis (moved up a level), and the 7d vs_typical_direction are all None
    and dropped. Re-parse stays safe — every dropped field is Optional and defaults
    to None when absent."""
    # #522: COACH_PREVIOUS_30D_ENABLED drops the whole previous_30d window (the
    # builder sets it None); pop the null key so the section stays byte-stable.
    if rt.get("previous_30d") is None:
        rt.pop("previous_30d", None)
    for wkey in ("last_7d", "last_30d", "previous_30d"):
        win = rt.get(wkey)
        if not win:
            continue
        for k in ("prev_basis", "typical_basis"):
            if win.get(k) is None:
                win.pop(k, None)
        for comp in win.get("comparisons", []):
            for k in [k for k, v in comp.items() if v is None]:
                comp.pop(k, None)
        # #650: drop the null session-shape fields so they cost no tokens — a continuous
        # run has no rep shape, and a non-run (ride/walk) has no running structure at all.
        # weekday is always populated (from the date), so it stays. Re-parse safe: both
        # default to None when absent.
        for act in win.get("activities", []):
            for k in ("structure", "interval_shape", "source", "long_run"):
                if act.get(k) is None:
                    act.pop(k, None)


def strip_nulls_in_place(obj: Any) -> None:
    """Recursively remove None-valued keys from a nested dict/list structure."""
    if isinstance(obj, dict):
        for k in [k for k, v in obj.items() if v is None]:
            obj.pop(k)
        for v in obj.values():
            strip_nulls_in_place(v)
    elif isinstance(obj, list):
        for v in obj:
            strip_nulls_in_place(v)


def drop_training_history_v2_fields(th: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """ADR 0026 Slice 2 (#670): the grouped_v2-only training-history enrichments
    (per-bucket `by_type`/`avg_weekly_load`/`from_date`/`to_date` + the trait
    `peak_sustained_weekly_load`/`current_vs_peak_load_pct`) ride the SAME schema as the
    original 60d ladder. On the original ladder they are unset (None); pop exactly those
    keys so every prior prompt's section is BYTE-IDENTICAL to its pre-Slice-2 shape. The
    grouped_v2 ladder always populates them (a bucket's `by_type` is a non-empty list, the
    traits' `peak_sustained_weekly_load` is a set int), so this is a no-op there — and it
    deliberately does NOT touch the pre-existing Optional trait nulls (`current_vs_peak_pct`
    etc.), which still serialize as null exactly as today. Re-parse stays safe: every popped
    field defaults to None when absent."""
    traits = th.get("traits")
    if isinstance(traits, dict) and traits.get("peak_sustained_weekly_load") is None:
        traits.pop("peak_sustained_weekly_load", None)
        traits.pop("current_vs_peak_load_pct", None)
    for bucket in th.get("timeline", []) or []:
        if isinstance(bucket, dict) and bucket.get("by_type") is None:
            for key in ("from_date", "to_date", "avg_weekly_load", "by_type"):
                bucket.pop(key, None)


def drop_recent_weeks_nulls(rw: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """ADR 0026 Slice 2 (#670): make the recent_weeks section's sparse per-activity/day/
    verdict fields cost nothing when absent. Every leaf that does not apply (a no-HR
    session's `avg_hr`/`hr_drift`, a non-run's `structure`/`shape`, a run without a
    check-in's `rpe`/`pain`/`notes`, a rest day's `day_totals`, a `no_norm` metric's
    `typical`/`pct`) is Optional-None on the model; drop it recursively so the section
    carries only present facts. Re-parse stays safe — every dropped field defaults to
    None when absent."""
    strip_nulls_in_place(rw)


def drop_intensity_read_nulls(ir: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """ADR 0026 Slice 3 (#673): make the intensity_read section's sparse fields cost
    nothing when absent. Drop the null leaves (a no-HR run's `band`, a no-zone run's
    `within_run`, a no-check-in run's `felt_vs_measured`, a no-drift run's
    `drift_vs_typical`, a clean run's `drift_vs_typical.confounded`) recursively, and
    drop the `confounders` list when empty so it only appears — leading the block — when
    a confounder actually fired. Re-parse stays safe: every dropped field defaults to
    None/empty when absent."""
    if ir.get("confounders") == []:
        ir.pop("confounders", None)
    strip_nulls_in_place(ir)


# --- THE DECLARATIONS --------------------------------------------------------
# One row per flat pack section, in FLAT SERIALIZATION ORDER. This order is the
# artifact: `_FLAT_ORDER` is this sequence, and the byte-stable drop loop runs in it
# (which the one cross-section trim, training_volume -> recent_training, relies on).
# Inserting a row anywhere but the end CHANGES the serialized pack; append.
_F = PromptFeature

COACH_SIGNALS: Tuple[CoachSignal, ...] = (
    CoachSignal(
        field="activity",
        group="this_run",
        ungated_reason=(
            "The run being coached. A pack without its subject is not a pack; there "
            "is nothing an operator would switch off."
        ),
        why="the original pack",
    ),
    CoachSignal(
        field="metrics",
        group="this_run",
        kill_switches=(
            KillSwitch(
                "COACH_STOPS_ANALYSIS_ENABLED",
                "trim",
                "context.build_focus_payload",
                "drops metrics.stops_analysis for the coach; the pipeline still "
                "computes and stores it for risk/flags",
            ),
        ),
        why="the original pack",
    ),
    CoachSignal(
        field="check_in",
        group="this_run",
        kill_switches=(
            KillSwitch(
                "COACH_SLEEP_QUALITY_ENABLED",
                "trim",
                "context.build_focus_payload",
                "drops check_in.sleep_quality; risk.py/flags.py keep their null-defaults",
            ),
        ),
        why="the original pack",
    ),
    CoachSignal(
        field="profile",
        group="the_runner",
        gate_feature=_F.BODY,
        droppable=True,  # never None itself: the descriptor carries the nested body trim
        nested_drop=drop_profile_body,
        ungated_reason=(
            "The runner's own stated profile is the floor of coaching them rather "
            "than the median; it has never had a switch. Its #742 `body` sub-field "
            "is prompt-gated (BODY) but likewise unswitched — a candidate for a "
            "COACH_BODY_ENABLED flag if the operator ever wants one."
        ),
        why="the original pack; #742 added the nested `body`",
    ),
    CoachSignal(
        field="recent_training_summary",
        droppable=True,
        ungated_reason=(
            "Retired (#451). Never populated; the field survives only so a pre-#451 "
            "stored pack still strict-parses under extra='forbid'."
        ),
        why="#451 (retired)",
    ),
    CoachSignal(
        field="longitudinal",
        group="our_thread",
        droppable=True,
        kill_switches=(
            KillSwitch(
                "COACH_LONGITUDINAL_ENABLED",
                "drop",
                "context._build_longitudinal_context",
                "drops the whole section (both the prior-report digests and the "
                "numeric baseline trend)",
            ),
            KillSwitch(
                "COACH_PRIOR_REPORTS_ENABLED",
                "trim",
                "context._build_longitudinal_context",
                "empties the prior-report digest half; the baseline trend is retained",
            ),
        ),
        why="M4",
    ),
    CoachSignal(
        field="perceived_effort",
        group="this_run",
        droppable=True,
        ungated_reason=(
            "Always emitted under every prompt below the ADR 0026 intensity-read "
            "prompts, which retire it into `intensity_read`. Its droppability is "
            "that retirement, not an operator switch."
        ),
        why="M6",
    ),
    CoachSignal(
        field="adherence",
        group="our_thread",
        adapters=(ReadTimeAdapter("adherence"),),
        kill_switches=(
            KillSwitch(
                "COACH_ADHERENCE_ENABLED",
                "degrade",
                "context.build_b_baseline",
                "off => the EMPTY AdherenceContext, not a dropped section, so the "
                "always-present shape is preserved; the seam cannot apply it",
            ),
        ),
        why="M7",
    ),
    CoachSignal(
        field="calibration",
        group="this_run",
        droppable=True,
        adapters=(ReadTimeAdapter("calibration"),),
        ungated_reason=(
            "Carries the non-diagnostic referral nudge, part of the safety surface; "
            "retired into `intensity_read`/`referral` under the ADR 0026 grouped "
            "prompts rather than switched off."
        ),
        why="M9",
    ),
    CoachSignal(
        field="believed_facts",
        droppable=True,
        ungated_reason=(
            "Retired (M4/ADR 0025). Never populated; kept so a pre-M4 stored pack parses."
        ),
        why="M8 (retired)",
    ),
    CoachSignal(
        field="preference_profile",
        droppable=True,
        ungated_reason=(
            "Retired (M4/ADR 0025). Never populated; kept so a pre-M4 stored pack parses."
        ),
        why="M10 (retired)",
    ),
    CoachSignal(
        field="narrative",
        droppable=True,
        ungated_reason=(
            "Retired (M4/ADR 0025). Never populated; kept so a pre-M4 stored pack parses."
        ),
        why="A2c (retired)",
    ),
    CoachSignal(
        field="salience",
        droppable=True,
        kill_switches=(
            KillSwitch(
                "COACH_SALIENCE_ENABLED",
                "drop",
                "context.build_b_baseline",
                "drops the pack section only; the deterministic fuller-turn "
                "scheduling reads its own salience compute and is untouched",
            ),
        ),
        why="A4",
    ),
    CoachSignal(
        field="continuity",
        group="our_thread",
        droppable=True,
        kill_switches=(
            KillSwitch(
                "COACH_CONTINUITY_ENABLED",
                "drop",
                "context.build_context_pack",
                "drops the fuller-turn continuity section",
            ),
        ),
        why="A4",
    ),
    CoachSignal(
        field="block",
        group="this_run",
        droppable=True,
        ungated_reason=(
            "The A1 block data model is always on (ADR 0011). A block-of-one already "
            "emits nothing, so there is no ungated cost to switch off."
        ),
        why="A1 / ADR 0011",
    ),
    CoachSignal(
        field="corpus",
        group="how_to_coach",
        gate_feature=_F.CORPUS,
        droppable=True,
        nested_drop=drop_corpus_user_materials,
        kill_switches=(
            KillSwitch(
                "COACH_HOUSE_SCHOOLS_ENABLED",
                "trim",
                "context._build_corpus_context",
                "drops corpus.school (the five named SCHOOLS); HOUSE_CORE is retained",
            ),
            KillSwitch(
                "COACH_USER_MATERIALS_ENABLED",
                "trim",
                "context._build_corpus_context",
                "drops corpus.user_materials and skips the materials read entirely",
            ),
        ),
        why="P1.2 / ADR 0014; P4 (#286) added the nested user_materials",
    ),
    CoachSignal(
        field="stance",
        group="how_to_coach",
        gate_feature=_F.STANCE,
        droppable=True,
        kill_switches=(
            KillSwitch(
                "COACH_RELATIONSHIP_ENABLED",
                "degrade",
                "service._resolve_stance_for_activity",
                "off => the coaching_relationship row is never read, so voice and "
                "stance resolve to their defaults; the section is still emitted",
            ),
        ),
        why="P1.3 / ADR 0015",
    ),
    CoachSignal(
        field="training_load",
        group="right_now",
        gate_feature=_F.TRAINING_LOAD,
        droppable=True,
        adapters=(ReadTimeAdapter("training_load", _F.TRAINING_LOAD),),
        ungated_reason=(
            "A deterministic read-time read with no stored state and no operator "
            "switch; rolled back by flipping COACH_PROMPT_ID off a training-load "
            "prompt. Superseded by `readiness` on the grouped lineage."
        ),
        why="P3 / ADR 0016",
    ),
    CoachSignal(
        field="training_volume",
        group="right_now",
        gate_feature=_F.VOLUME,
        droppable=True,
        nested_drop=drop_training_volume_rolling_current,
        adapters=(ReadTimeAdapter("training_volume", _F.VOLUME),),
        ungated_reason=(
            "A deterministic read-time read with no operator switch; rolled back by "
            "flipping COACH_PROMPT_ID off a volume prompt. Superseded by "
            "`recent_weeks` on the grouped lineage."
        ),
        why="#400",
    ),
    CoachSignal(
        field="stream_view",
        group="this_run",
        gate_feature=_F.STREAM_VIEW,
        droppable=True,
        ungated_reason=(
            "Rides the focus payload via the `deep` flag rather than the read-time "
            "seam, and has no operator switch; rolled back by flipping "
            "COACH_PROMPT_ID off a stream-view prompt."
        ),
        why="#443 / A2a",
    ),
    CoachSignal(
        field="recent_training",
        group="right_now",
        gate_feature=_F.RECENT_TRAINING,
        droppable=True,
        nested_drop=drop_recent_training_dedup,
        adapters=(ReadTimeAdapter("recent_training", _F.RECENT_TRAINING),),
        kill_switches=(
            KillSwitch(
                "COACH_PREVIOUS_30D_ENABLED",
                "trim",
                "context._build_recent_training_context",
                "drops the previous_30d window; the vs-prev comparisons are unaffected",
            ),
        ),
        why="#444",
    ),
    CoachSignal(
        field="readiness",
        group="right_now",
        gate_feature=_F.READINESS,
        droppable=True,
        adapters=(ReadTimeAdapter("readiness", _F.READINESS),),
        ungated_reason=(
            "The keyed rename of `training_load` (identical content, coaching-question "
            "key); it inherits that signal's absence of an operator switch."
        ),
        why="ADR 0026 Slice 2 (#670)",
    ),
    CoachSignal(
        field="recent_weeks",
        group="right_now",
        gate_feature=_F.RECENT_WEEKS,
        droppable=True,
        nested_drop=drop_recent_weeks_nulls,
        adapters=(ReadTimeAdapter("recent_weeks", _F.RECENT_WEEKS),),
        ungated_reason=(
            "The merged successor to `training_volume` + `recent_training`; it "
            "inherits their absence of an operator switch. Note it does NOT honour "
            "COACH_PREVIOUS_30D_ENABLED, which only ever bound the retired section."
        ),
        why="ADR 0026 Slice 2 (#670)",
    ),
    CoachSignal(
        field="training_history",
        group="the_runner",
        gate_feature=_F.TRAINING_HISTORY,
        droppable=True,
        nested_drop=drop_training_history_v2_fields,
        # TWO adapters, ONE section: mutually exclusive by prompt feature (the original
        # 60d ladder and the ADR 0026 Slice 2 ladder rebased after the 2-week window),
        # so exactly one ever fires and the assembler `or`s them into this key.
        adapters=(
            ReadTimeAdapter(
                "training_history", _F.TRAINING_HISTORY, "COACH_TRAINING_HISTORY_ENABLED"
            ),
            ReadTimeAdapter(
                "training_history_2wk",
                _F.TRAINING_HISTORY_2WK,
                "COACH_TRAINING_HISTORY_ENABLED",
            ),
        ),
        kill_switches=(
            KillSwitch(
                "COACH_TRAINING_HISTORY_ENABLED",
                "drop",
                "read_time_signals.gather",
                "drops the section under either ladder without a prompt rollback",
            ),
        ),
        why="#561, redefined by ADR 0026 Slice 2 (#670)",
    ),
    CoachSignal(
        field="memory",
        group="the_runner",
        gate_feature=_F.MEMORY,
        droppable=True,
        adapters=(ReadTimeAdapter("memory", _F.MEMORY, "COACH_MEMORY_ENABLED"),),
        kill_switches=(
            KillSwitch(
                "COACH_MEMORY_ENABLED",
                "drop",
                "read_time_signals.gather",
                "drops the section AND disables the runner-memory update writer "
                "(the writer half is gated in service.py)",
            ),
        ),
        why="ADR 0025 / M3",
    ),
    CoachSignal(
        field="intensity",
        group="this_run",
        gate_feature=_F.INTENSITY,
        droppable=True,
        adapters=(ReadTimeAdapter("intensity", _F.INTENSITY),),
        ungated_reason=(
            "A deterministic read-time read with no operator switch; rolled back by "
            "flipping COACH_PROMPT_ID off an intensity prompt. Split into "
            "`intensity_read` + `intensity_mix` on the grouped lineage."
        ),
        why="#578",
    ),
    CoachSignal(
        field="intensity_read",
        group="this_run",
        gate_feature=_F.INTENSITY_READ,
        droppable=True,
        nested_drop=drop_intensity_read_nulls,
        ungated_reason=(
            "Built by the intensity scan's FAN-OUT in build_context_pack (one scan, "
            "three sections), not the one-signal-one-section seam, and carries no "
            "operator switch."
        ),
        why="ADR 0026 Slice 3 (#673)",
    ),
    CoachSignal(
        field="referral",
        group="this_run",
        gate_feature=_F.INTENSITY_READ,
        droppable=True,
        ungated_reason=(
            "The promoted non-diagnostic clinician nudge: part of the safety surface, "
            "so it is deliberately not switchable."
        ),
        why="ADR 0026 Slice 3 (#673)",
    ),
    CoachSignal(
        field="intensity_mix",
        group="right_now",
        gate_feature=_F.INTENSITY_MIX,
        droppable=True,
        ungated_reason=(
            "The other half of the intensity fan-out; inherits `intensity`'s absence "
            "of an operator switch."
        ),
        why="ADR 0026 Slice 3 (#673)",
    ),
    CoachSignal(
        field="schedule",
        group="right_now",
        gate_feature=_F.SCHEDULE,
        droppable=True,
        adapters=(ReadTimeAdapter("schedule", _F.SCHEDULE, "COACH_SCHEDULE_ENABLED"),),
        kill_switches=(
            KillSwitch(
                "COACH_SCHEDULE_ENABLED",
                "drop",
                "read_time_signals.gather",
                "the coach stops seeing the runner's plan; the schedule surface "
                "itself is unaffected (that is SCHEDULE_ENABLED)",
            ),
        ),
        why="#830",
    ),
    CoachSignal(
        field="safety_rules",
        ungated_reason=(
            "The safety floor. Never gated, never dropped, never switchable — that is "
            "the point of it (ADR 0024)."
        ),
        why="the original pack",
    ),
)


# --- Derived views -----------------------------------------------------------

#: Flat section name -> its ADR 0026 group. A name absent here is top-level meta.
SECTION_GROUP: Dict[str, str] = {
    s.field: s.group for s in COACH_SIGNALS if s.group is not None
}

#: The flat serialization order (and the byte-stable drop order).
FLAT_ORDER: Tuple[str, ...] = tuple(s.field for s in COACH_SIGNALS)

#: field -> declaration.
SIGNALS_BY_FIELD: Dict[str, CoachSignal] = {s.field: s for s in COACH_SIGNALS}

#: Every read-time adapter across every signal, in declaration order. This is what
#: `context.py` binds its `compute` functions to.
READ_TIME_ADAPTERS: Tuple[ReadTimeAdapter, ...] = tuple(
    adapter for s in COACH_SIGNALS for adapter in s.adapters
)


def signal_for_adapter(name: str) -> CoachSignal:
    """The signal an adapter feeds. Raises KeyError for an unknown adapter."""
    for s in COACH_SIGNALS:
        if any(a.name == name for a in s.adapters):
            return s
    raise KeyError(name)


# --- Import-time consistency checks ------------------------------------------


def _assert_registry_consistent() -> None:
    """Fail loudly AT IMPORT on an inconsistent declaration.

    Turns each way this registry could silently disagree with itself into a startup
    ``RuntimeError``, in the spirit of the #328 prompt-feature manifest and the #493
    descriptor check this replaces.
    """
    setting_names = set(Settings.model_fields)
    seen_fields: set = set()
    seen_adapters: set = set()
    for s in COACH_SIGNALS:
        if s.field in seen_fields:
            raise RuntimeError(f"coach signal registry: duplicate signal {s.field!r}.")
        seen_fields.add(s.field)
        if s.group is not None and s.group not in GROUP_NAMES:
            raise RuntimeError(
                f"coach signal registry: {s.field!r} declares group {s.group!r}, "
                f"which is not an ADR 0026 group ({GROUP_NAMES})."
            )
        if s.nested_drop is not None and not s.droppable:
            raise RuntimeError(
                f"coach signal registry: {s.field!r} declares a nested_drop but is "
                f"not droppable, so the trim would never run."
            )
        # The ungated rule: state the gate, either a real switch or a recorded reason.
        if not s.kill_switches and not s.ungated_reason.strip():
            raise RuntimeError(
                f"coach signal registry: {s.field!r} declares neither a kill switch "
                f"nor an ungated_reason. Every signal must state its gate — name a "
                f"COACH_*_ENABLED setting, or record why it has none."
            )
        for ks in s.kill_switches:
            if ks.setting not in setting_names:
                raise RuntimeError(
                    f"coach signal registry: {s.field!r} names kill switch "
                    f"{ks.setting!r}, which is not a field on Settings."
                )
            if ks.effect not in _EFFECTS:
                raise RuntimeError(
                    f"coach signal registry: {s.field!r} kill switch {ks.setting!r} "
                    f"declares effect {ks.effect!r}, not one of {sorted(_EFFECTS)}."
                )
        for adapter in s.adapters:
            if adapter.name in seen_adapters:
                raise RuntimeError(
                    f"coach signal registry: duplicate read-time adapter "
                    f"{adapter.name!r}."
                )
            seen_adapters.add(adapter.name)
            if adapter.kill_switch is not None:
                if adapter.kill_switch not in setting_names:
                    raise RuntimeError(
                        f"coach signal registry: adapter {adapter.name!r} names kill "
                        f"switch {adapter.kill_switch!r}, which is not a Settings field."
                    )
                # An adapter-applied switch must be declared on its signal as a DROP,
                # so the declaration and the seam cannot disagree about the effect.
                if adapter.kill_switch not in {k.setting for k in s.drop_switches}:
                    raise RuntimeError(
                        f"coach signal registry: adapter {adapter.name!r} applies "
                        f"{adapter.kill_switch!r}, which {s.field!r} does not declare "
                        f"as a drop-effect kill switch."
                    )


_assert_registry_consistent()
