import asyncio
import aiohttp
import json

ACTIVITY_ID = "4f889b25-de20-47c7-8a33-ce1bd9d6f90c"
URL = "http://localhost:8000/api/verdict/v3"

async def fetch_scorecard(session):
    async with session.post(f"{URL}/scorecard", json={"activity_id": ACTIVITY_ID}) as resp:
        txt = await resp.text()
        print(f"Scorecard: {resp.status}")
        if resp.status != 200:
            print(f"Scorecard Error: {txt}")
        return resp.status

async def fetch_story(session):
    async with session.post(f"{URL}/story", json={"activity_id": ACTIVITY_ID}) as resp:
        txt = await resp.text()
        print(f"Story: {resp.status}")
        if resp.status != 200:
            print(f"Story Error: {txt}")
        return resp.status

async def main():
    async with aiohttp.ClientSession() as session:
        print("Firing parallel requests...")
        # Simulate the frontend behavior
        results = await asyncio.gather(
            fetch_scorecard(session),
            fetch_story(session)
        )
        print(f"Results: {results}")

if __name__ == "__main__":
    asyncio.run(main())
