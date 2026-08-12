"""Backfill the warm-up and cool-down distance on interval sessions (#876).

Sessions written before #876 carry rep structure and no `warmup_distance_m` or
`cooldown_distance_m`, so they count only their reps: the runner's own 4.5 km
6 x 400m reads as 2.4 km until its plan is redrafted. This fills those two keys
in on the sessions already stored.

It never converts a time into a distance
----------------------------------------
Most of these sessions state their edges in the detail as MINUTES ("Warm up 10
min easy"). Turning that into a distance means multiplying by a pace nobody
wrote down, which is the invention #876 exists to remove -- so this script reads
a distance only where the coach actually wrote one, and ABSTAINS loudly on
everything else rather than estimating. An abstention is not a failure here: it
is the script declining to make up the runner's training, and the value can be
supplied with `--set` once a human decides what it should be.

Scope is deliberately narrow: committed, rep-structured RUN sessions of the
ACTIVE plan whose window has not already passed. A past session is history and
nothing reads its planned distance any more; a superseded plan's sessions are
history too.

    python -m scripts.backfill_session_edges                    # report only
    python -m scripts.backfill_session_edges --set <id>:1100/1000
    python -m scripts.backfill_session_edges --apply            # write proposals

Idempotent: a session that already carries both keys is skipped, so re-running
after an interruption is safe. Dry by default -- `--apply` is the only path that
writes, and it writes exactly what the report showed.
"""

import argparse
import re
import uuid
from datetime import date
from typing import Dict, Optional, Tuple

from app.db.session import SessionLocal
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan

# "1.1 km", "1,1km", "1100 m", "1100m". Deliberately NOT minutes: a duration is
# not a distance until a pace is assumed, and assuming one is the bug.
_KM = re.compile(r"(\d+(?:[.,]\d+)?)\s*k(?:m|ilometres?|ilometers?)\b", re.I)
_M = re.compile(r"(\d+(?:[.,]\d+)?)\s*m(?:etres?|eters?)?\b", re.I)


def _metres(fragment: str) -> Optional[float]:
    """A distance stated in a fragment of prose, or None if it states none."""
    km = _KM.search(fragment)
    if km:
        return float(km.group(1).replace(",", ".")) * 1000
    metres = _M.search(fragment)
    if metres:
        return float(metres.group(1).replace(",", "."))
    return None


def _sentence_with(detail: str, *words: str) -> Optional[str]:
    """The first sentence naming one of `words` -- the edge's own clause.

    Sentence-scoped so a warm-up cannot pick up the rep distance from "6 reps of
    400m" sitting in the next sentence.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", detail or ""):
        lowered = sentence.lower()
        if any(word in lowered for word in words):
            return sentence
    return None


def propose(session: PlannedSession) -> Tuple[Optional[float], Optional[float]]:
    """(warm-up, cool-down) in metres, each None where the coach wrote no distance."""
    detail = session.detail or ""
    warm = _sentence_with(detail, "warm up", "warm-up", "warmup")
    cool = _sentence_with(detail, "cool down", "cool-down", "cooldown")
    return (_metres(warm) if warm else None, _metres(cool) if cool else None)


def candidates(db, *, today: date):
    """Committed rep-structured runs of active plans, still ahead of the runner."""
    rows = (
        db.query(PlannedSession)
        .join(TrainingPlan, PlannedSession.plan_id == TrainingPlan.id)
        .filter(
            TrainingPlan.status == "active",
            PlannedSession.discipline == "run",
            PlannedSession.commitment == "committed",
            PlannedSession.window_end >= today,
            PlannedSession.structure.isnot(None),
        )
        .order_by(PlannedSession.window_start.asc())
        .all()
    )
    out = []
    for row in rows:
        structure = row.structure or {}
        if not structure.get("reps_planned"):
            continue
        # Idempotence: a session already carrying both edges is done.
        if structure.get("warmup_distance_m") and structure.get("cooldown_distance_m"):
            continue
        # A session stating its whole self needs nothing -- the total already wins.
        if row.target_distance_m:
            continue
        out.append(row)
    return out


def _parse_set(values) -> Dict[uuid.UUID, Tuple[Optional[float], Optional[float]]]:
    """`<session_id>:<warmup_m>/<cooldown_m>`; either side may be `-` to skip."""
    out = {}
    for raw in values or []:
        try:
            ident, pair = raw.split(":", 1)
            warm_s, cool_s = pair.split("/", 1)
            out[uuid.UUID(ident.strip())] = (
                None if warm_s.strip() == "-" else float(warm_s),
                None if cool_s.strip() == "-" else float(cool_s),
            )
        except (ValueError, AttributeError) as exc:
            raise SystemExit(f"could not read --set {raw!r}: {exc}")
    return out


def run(db, *, today: date, apply: bool, overrides) -> int:
    rows = candidates(db, today=today)
    if not rows:
        print("nothing to backfill: no rep-structured session is missing its edges.")
        return 0

    written = 0
    abstained = []
    for row in rows:
        structure = dict(row.structure or {})
        warm, cool = overrides.get(row.id, (None, None))
        source = "--set"
        if warm is None and cool is None:
            warm, cool = propose(row)
            source = "detail"

        # Never overwrite an edge the session already carries.
        warm = None if structure.get("warmup_distance_m") else warm
        cool = None if structure.get("cooldown_distance_m") else cool

        reps = structure.get("reps_planned")
        rep_d = structure.get("rep_distance_m") or 0
        work = (reps or 0) * rep_d

        if warm is None and cool is None:
            abstained.append(row)
            print(f"  ABSTAIN  {row.id}  {row.title!r}")
            print(f"           detail states no distance: {(row.detail or '')[:90]!r}")
            continue

        if warm:
            structure["warmup_distance_m"] = float(warm)
        if cool:
            structure["cooldown_distance_m"] = float(cool)
        total = (
            structure.get("warmup_distance_m", 0)
            + work
            + structure.get("cooldown_distance_m", 0)
        )
        print(
            f"  {'WRITE  ' if apply else 'PROPOSE'}  {row.id}  {row.title!r}  [{source}]"
        )
        print(
            f"           warm-up {structure.get('warmup_distance_m', 0):.0f} m + "
            f"work {work:.0f} m + cool-down "
            f"{structure.get('cooldown_distance_m', 0):.0f} m = {total / 1000:.2f} km"
        )
        if apply:
            row.structure = structure
            db.add(row)
            written += 1

    if apply:
        db.commit()
        print(f"\nwrote {written} session(s).")
    else:
        print("\nDRY RUN -- nothing written. Re-run with --apply to write the above.")
    if abstained:
        print(
            f"{len(abstained)} session(s) abstained: their detail gives a time, not a "
            "distance. Supply values with --set <id>:<warmup_m>/<cooldown_m>."
        )
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the proposals (default: dry run)"
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="ID:WARM/COOL",
        help="explicit metres for one session; '-' leaves a side alone",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        run(
            db,
            today=date.today(),
            apply=args.apply,
            overrides=_parse_set(args.set),
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
