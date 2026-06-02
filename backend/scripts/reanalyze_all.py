"""Re-run the analysis pipeline over every stored activity.

Used after the classification-axes migration (ADR 0007): existing DerivedMetric
rows lose `activity_class` and have NULL axes until re-analyzed. This repopulates
them from the streams already in the database — no Strava calls are made.

    python -m scripts.reanalyze_all            # all activities
    python -m scripts.reanalyze_all --limit 50 # bound the run

Idempotent: analyze() upserts the DerivedMetric, so re-running is safe.
"""
import argparse

from app.db.session import SessionLocal
from app.models import Activity
from app.services.analysis import analyze


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-analyze stored activities.")
    parser.add_argument("--limit", type=int, default=None, help="max activities to process")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = db.query(Activity.id).order_by(Activity.start_date.desc())
        if args.limit:
            q = q.limit(args.limit)
        ids = [row[0] for row in q.all()]

        ok = 0
        failed = 0
        for activity_id in ids:
            try:
                if analyze(db, str(activity_id)) is not None:
                    ok += 1
                else:
                    failed += 1
                    print(f"  skipped (no result): {activity_id}")
            except Exception as exc:  # keep going; report at the end
                failed += 1
                db.rollback()
                print(f"  error on {activity_id}: {exc}")

        print(f"Re-analyzed {ok}/{len(ids)} activities ({failed} skipped/failed).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
