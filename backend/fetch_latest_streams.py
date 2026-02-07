
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
            print("No linked Strava account found. Cannot fetch streams.")
            return

            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
