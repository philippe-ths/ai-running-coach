import sys
import os
from uuid import UUID
from sqlalchemy import select

# Mock Env
os.environ["DATABASE_URL"] = "postgresql+psycopg://coach:coach@localhost:5433/coach"
os.environ["AI_ENABLED"] = "True"
os.environ["AI_PROVIDER"] = "openai"
from dotenv import load_dotenv
load_dotenv()

from app.db.session import SessionLocal
from app.models import Activity
from app.services.ai.context_builder import build_context_pack
from app.services.ai.verdict_v3.generators import generate_scorecard
from app.services.ai.client import ai_client

def test_generation():
    db = SessionLocal()
    try:
        stmt = select(Activity).where(Activity.name == 'Lunch Run')
        results = db.execute(stmt).scalars().all()
        print(f"Found {len(results)} Lunch Runs")

        for activity in results:
            activity_id = activity.id
            print(f"\n--- Testing {activity_id} ({activity.type}) ---")
            
            try:
                # 1. Context
                cp = build_context_pack(activity_id, db)
                if not cp:
                    print("SKIPPING: Context Pack is None!")
                    continue

                cp_dict = cp.model_dump(mode='json')

                # 2. Scorecard
                print("Generating Scorecard...")
                scorecard = generate_scorecard(cp_dict, ai_client)
                print("SUCCESS: Scorecard generated.")

            except Exception as e:
                print(f"FAILURE on {activity_id}: {e}")

    finally:
        db.close()

if __name__ == "__main__":
    test_generation()
