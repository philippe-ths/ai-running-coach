from datetime import datetime
from sqlalchemy import text
from app.db.session import SessionLocal
from app.models import User, Activity

def test_create_user_and_activity():
    db = SessionLocal()
    try:
        # 1. Create User
        user = User(email="test@example.com")
        db.add(user)
        db.commit()
        db.refresh(user)
        
        assert user.id is not None
        assert user.email == "test@example.com"
        
        # 2. Create Activity
        activity = Activity(
            user_id=user.id,
            strava_activity_id=123456,
            start_date=datetime.now(),
            type="Run",
            name="Morning Run",
            distance_m=5000,
            moving_time_s=1500,
            elapsed_time_s=1500,
            elev_gain_m=10.0,
            raw_summary={"foo": "bar"}
        )
        db.add(activity)
        db.commit()
        db.refresh(activity)
        
        assert activity.id is not None
        assert activity.strava_activity_id == 123456
        assert activity.user_id == user.id
        
        print(f"Success! Created user {user.id} and activity {activity.id}")
        
    finally:
        # Cleanup
        if 'activity' in locals() and activity.id:
            db.delete(activity)
        if 'user' in locals() and user.id:
            db.delete(user)
        db.commit()
        db.close()

if __name__ == "__main__":
    test_create_user_and_activity()
