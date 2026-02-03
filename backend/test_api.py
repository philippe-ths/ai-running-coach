
import requests
import json

URL = "http://localhost:8000/api/activities"

try:
    # 1. Get List
    resp = requests.get(URL)
    data = resp.json()
    if not data:
        print("No activities.")
        exit()
        
    latest_id = data[0]['id']
    print(f"Latest ID: {latest_id}")
    
    # 2. Get Detail
    detail_url = f"{URL}/{latest_id}"
    detail_resp = requests.get(detail_url)
    detail_data = detail_resp.json()
    
    # 3. Check for streams field
    if "streams" in detail_data:
        print("SUCCESS: 'streams' field found in API response.")
        streams = detail_data["streams"]
        print(f"Streams count: {len(streams)}")
        if len(streams) > 0:
            print("Streams data is populated!")
            print(f"Sample Stream: {streams[0].get('stream_type')}")
        else:
            print("Streams field exists but is empty.")
    else:
        print("FAILURE: 'streams' field NOT found in API response.")

except Exception as e:
    print(f"Error: {e}")
