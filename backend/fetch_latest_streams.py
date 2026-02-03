
import asyncio
import logging
from app.db.session import SessionLocal
from app.models import Activity, StravaAccount
from app.services.activity_service import fetch_and_store_streams
from sqlalchemy import select

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    db = SessionLocal()
    try:
        # Get latest activity
        stmt = select(Activity).order_by(Activity.start_date.desc()).limit(1)
        activity = db.execute(stmt).scalars().first()
        
        if not activity:
            print("No activities found.")
            return

        print(f"Fetching streams for: {activity.name} (ID: {activity.id})")
        
        # Get Strava Account
        account = db.query(StravaAccount).filter(StravaAccount.user_id == activity.user_id).first()
        
        if account:
            # Fetch Real
            success = await fetch_and_store_streams(db, account, activity)
            if success:
                print("Streams fetched and stored successfully (REAL)!")
            else:
                print("Failed to fetch streams (maybe API error).")
        else:
            print("No linked Strava account found. Generating MOCK streams for demo...")
            from app.models import ActivityStream
            import math
            import random
            
            # Generate 60 minutes of mock data
            points = 3600 
            
            # Velocity: 3.0 m/s with some noise
            velocity_data = [3.0 + (math.sin(i/100) * 0.2) + random.uniform(-0.1, 0.1) for i in range(points)]
            
            # HR: 140 bpm with drift
            nr_data = [140 + (i/points * 10) + random.uniform(-2, 2) for i in range(points)]
            
            s1 = ActivityStream(activity_id=activity.id, stream_type="velocity_smooth", data=velocity_data)
            s2 = ActivityStream(activity_id=activity.id, stream_type="heartrate", data=nr_data)
            
            db.add(s1)
            db.add(s2)
            db.commit()
            print("Mock streams created successfully!")

            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
