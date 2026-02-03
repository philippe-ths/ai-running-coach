
from app.db.session import SessionLocal
from app.models import ActivityStream, Activity
from app.schemas import ActivityDetailRead

db = SessionLocal()
try:
    # Get the most recent activity
    activity = db.query(Activity).order_by(Activity.start_date.desc()).first()
    if not activity:
        print("No activities found.")
    else:
        print(f"Checking Activity: {activity.name} (ID: {activity.id})")
        stream_count = db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).count()
        print(f"Stream count in DB: {stream_count}")
        
        # Check if the relationship loads
        print(f"Relationship loaded count: {len(activity.streams)}")
        
        # Test serialization
        from app.schemas import ActivityDetailRead
        model = ActivityDetailRead.model_validate(activity)
        print(f"Serialized streams count: {len(model.streams)}")

except Exception as e:
    print(f"Error: {e}")
finally:
    db.close()
