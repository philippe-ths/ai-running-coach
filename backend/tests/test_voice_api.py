"""P1.1 Voice — read/write API endpoints (/api/coach/voice)."""

from fastapi.testclient import TestClient


def test_get_voice_returns_default_and_catalog(client: TestClient, db):
    res = client.get("/api/coach/voice")
    assert res.status_code == 200
    data = res.json()
    # undeclared: all current values null
    assert data["current"]["preset"] is None
    assert data["current"]["warmth"] is None
    assert data["current"]["freetext"] is None
    # catalog carries the four axes and six presets
    assert [a["key"] for a in data["catalog"]["axes"]] == ["warmth", "humor", "directness", "energy"]
    assert len(data["catalog"]["presets"]) == 6
    keys = {p["key"] for p in data["catalog"]["presets"]}
    assert {"sage", "cornerman", "analyst", "drill_sergeant", "roast", "deadpan"} == keys
    # no example messages are exposed (prompt-internal)
    assert "example_messages" not in data["catalog"]["presets"][0]


def test_put_voice_persists_preset_and_dials(client: TestClient, db):
    payload = {"preset": "roast", "warmth": 3, "humor": 5, "directness": 5, "energy": 5,
               "freetext": "talk like a pirate"}
    res = client.put("/api/coach/voice", json=payload)
    assert res.status_code == 200
    assert res.json()["current"]["preset"] == "roast"

    # persisted
    got = client.get("/api/coach/voice").json()["current"]
    assert got["preset"] == "roast"
    assert got["humor"] == 5
    assert got["freetext"] == "talk like a pirate"


def test_put_voice_can_clear_to_default(client: TestClient, db):
    client.put("/api/coach/voice", json={"preset": "sage", "warmth": 4})
    res = client.put("/api/coach/voice", json={})  # clear everything
    assert res.status_code == 200
    assert res.json()["current"]["preset"] is None
    assert res.json()["current"]["warmth"] is None


def test_put_voice_rejects_unknown_preset(client: TestClient, db):
    res = client.put("/api/coach/voice", json={"preset": "dr_house"})
    assert res.status_code == 422


def test_put_voice_rejects_out_of_range_dial(client: TestClient, db):
    assert client.put("/api/coach/voice", json={"warmth": 9}).status_code == 422
    assert client.put("/api/coach/voice", json={"energy": 0}).status_code == 422


def test_put_voice_blank_freetext_normalised_to_null(client: TestClient, db):
    res = client.put("/api/coach/voice", json={"freetext": "   "})
    assert res.status_code == 200
    assert res.json()["current"]["freetext"] is None
