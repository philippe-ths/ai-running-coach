def test_health_check_reports_app_and_database_ready(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "commit": None,  # no RAILWAY_GIT_COMMIT_SHA outside a Railway deploy
    }


def test_health_check_reports_the_deployed_commit(client, monkeypatch):
    # The post-deploy gate tells the new deployment from the old by this (#1027).
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "5d2cd0d0cf275f15b0e21e99ca89ab8c987c6cf9")

    assert client.get("/api/health").json()["commit"] == (
        "5d2cd0d0cf275f15b0e21e99ca89ab8c987c6cf9"
    )
