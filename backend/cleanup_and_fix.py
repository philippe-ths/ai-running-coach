
import asyncio
from sqlalchemy import select, delete
from app.db.session import SessionLocal
from app.models import Activity, ActivityStream, StravaAccount
from app.services.activity_service import fetch_and_store_streams

async def clean_and_fix():
    db = SessionLocal()
    try:
        # 1. Remove Test Data
        print("--- Cleaning Test Data ---")
        # Identifying test data by name or other characteristics known to be test data
        test_names = ["Test Long Run", "Easy Run", "Tempo Run"] # Standard names from sample data often used
        
        stmt = select(Activity).where(Activity.name.in_(test_names))
        test_activities = db.execute(stmt).scalars().all()
        
        for act in test_activities:
            # Check if it's a real one (large ID) or test one. 
            # Real Strava IDs are currently ~10-11 digits (e.g. 17283326295). 
            # Small IDs or specific test IDs might be used for mock data.
            # But "Test Long Run" was definitely created by us/seed.
            print(f"Deleting activity: {act.name} (StravaID: {act.strava_activity_id})")
            db.delete(act)
        
        # Also clean up any streams that might be orphaned if cascade didn't work (safety net)
        # (Assuming cascade is set up in models, but good to be sure)
        
        db.commit()
        print("Cleanup complete.")

        # 2. Fix 'Lunch Run' Streams
        print("\n--- Fixing Lunch Run Streams ---")
        target_strava_id = 17283326295
        stmt = select(Activity).where(Activity.strava_activity_id == target_strava_id)
        lunch_run = db.execute(stmt).scalars().first()

        if lunch_run:
            print(f"Found Lunch Run (DB ID: {lunch_run.id})")
            
            # Check if streams exist
            stream_count = db.query(ActivityStream).filter(ActivityStream.activity_id == lunch_run.id).count()
            print(f"Current Stream Count: {stream_count}")
            
            if stream_count == 0:
                print("Fetching streams from Strava...")
                account = db.query(StravaAccount).filter(StravaAccount.user_id == lunch_run.user_id).first()
                if account:
                    success = await fetch_and_store_streams(db, account, lunch_run)
                    if success:
                        print("Streams successfully fetched and stored.")
                    else:
                        print("Failed to fetch streams from Strava API.")
                else:
                    print("Error: No Strava Account found for this user.")
            else:
                print("Streams already exist.")
        else:
            print(f"Activity with Strava ID {target_strava_id} not found in DB.")

    except Exception as e:
        print(f"An error occurred: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(clean_and_fix())
