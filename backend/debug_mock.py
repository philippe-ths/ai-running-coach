import json
from app.schemas import VerdictScorecardResponse
from app.services.ai.verdict_v3.mocks import MOCK_SCORECARD_JSON

def test_mock():
    print("Testing MOCK_SCORECARD_JSON validation...")
    try:
        data = json.loads(MOCK_SCORECARD_JSON)
        vm = VerdictScorecardResponse(**data)
        print("SUCCESS: Mock is valid.")
        print(vm.model_dump_json(indent=2))
    except Exception as e:
        print(f"FAILURE: Mock validation failed: {e}")

if __name__ == "__main__":
    test_mock()
