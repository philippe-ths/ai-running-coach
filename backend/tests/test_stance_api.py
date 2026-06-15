"""P1.3 Stance — read/write API endpoints (/api/coach/stance)."""

from fastapi.testclient import TestClient


def test_get_stance_returns_default_and_catalog(client: TestClient, db):
    res = client.get("/api/coach/stance")
    assert res.status_code == 200
    data = res.json()
    # undeclared: all current values null
    assert data["current"]["school"] is None
    assert data["current"]["data_sentiment"] is None
    assert data["current"]["process_outcome"] is None
    # catalog carries the five schools and two emphasis axes
    keys = {s["key"] for s in data["catalog"]["schools"]}
    assert keys == {
        "aerobic-base", "polarized", "enjoyment-and-consistency",
        "strength-led", "periodization",
    }
    # each school carries a one-line stance for the picker
    assert all(s["stance"] for s in data["catalog"]["schools"])
    assert [a["key"] for a in data["catalog"]["axes"]] == ["data_sentiment", "process_outcome"]
    # the default the UI starts from
    assert data["catalog"]["default_school"] == "aerobic-base"
    assert data["catalog"]["default_data_sentiment"] == 3
    assert data["catalog"]["default_process_outcome"] == 3


def test_put_stance_persists_school_and_emphasis(client: TestClient, db):
    payload = {"school": "polarized", "data_sentiment": 5, "process_outcome": 1}
    res = client.put("/api/coach/stance", json=payload)
    assert res.status_code == 200
    assert res.json()["current"]["school"] == "polarized"

    # persisted
    got = client.get("/api/coach/stance").json()["current"]
    assert got["school"] == "polarized"
    assert got["data_sentiment"] == 5
    assert got["process_outcome"] == 1


def test_put_stance_can_clear_to_default(client: TestClient, db):
    client.put("/api/coach/stance", json={"school": "periodization", "data_sentiment": 4})
    res = client.put("/api/coach/stance", json={})  # clear everything
    assert res.status_code == 200
    assert res.json()["current"]["school"] is None
    assert res.json()["current"]["data_sentiment"] is None


def test_put_stance_rejects_unknown_school(client: TestClient, db):
    res = client.put("/api/coach/stance", json={"school": "crossfit-only"})
    assert res.status_code == 422


def test_put_stance_rejects_out_of_range_axis(client: TestClient, db):
    assert client.put("/api/coach/stance", json={"data_sentiment": 9}).status_code == 422
    assert client.put("/api/coach/stance", json={"process_outcome": 0}).status_code == 422


def test_put_stance_accepts_a_new_p1_3_school(client: TestClient, db):
    # the two P1.3-authored schools are selectable
    res = client.put("/api/coach/stance", json={"school": "strength-led"})
    assert res.status_code == 200
    assert res.json()["current"]["school"] == "strength-led"
