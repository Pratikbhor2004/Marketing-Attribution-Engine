import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

COLLECTION = Path("postman/Marketing_Attribution_Engine.postman_collection.json")


@pytest.fixture(scope="module")
def client(exported):
    from attribution_engine.api import app
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json()["dataset_available"] is True


def test_lifecycle_is_append_only(client):
    opp = client.get("/api/v1/opportunities", params={"stage": "qualified", "limit": 1}).json()["items"][0]["opportunity_id"]
    before = client.get(f"/api/v1/opportunities/{opp}/events").json()
    assert client.post(f"/api/v1/opportunities/{opp}/events", json={"to_stage": "closed_won"}).status_code == 409
    r = client.post(f"/api/v1/opportunities/{opp}/events", json={"to_stage": "proposal", "actor": "pytest"})
    assert r.status_code == 201 and r.json()["current"]["current_stage"] == "proposal"
    after = client.get(f"/api/v1/opportunities/{opp}/events").json()
    assert after[: len(before)] == before and len(after) == len(before) + 1
    detail = client.get(f"/api/v1/opportunities/{opp}").json()
    assert detail["stage_snapshot"] == "qualified" and detail["current"]["current_stage"] == "proposal"
    assert client.post("/api/v1/pipeline/validate").json()["passed"]


def test_custom_attribution_run_persists_and_reconciles(client):
    r = client.post("/api/v1/attribution/runs", json={"models": ["time_decay"], "lookback_days": 30}).json()
    run_id = r["run"]["run_id"]
    assert r["reconciliation_passed"]
    assert client.get(f"/api/v1/attribution/runs/{run_id}").json()["lookback_days"] == 30
    assert client.get("/api/v1/attribution/results", params={"run_id": run_id, "limit": 1}).json()["total"] > 0


def _requests(items):
    for it in items:
        if "item" in it:
            yield from _requests(it["item"])
        else:
            yield it["request"]


def test_postman_collection_covers_every_route():
    from attribution_engine.api import app
    col = json.loads(COLLECTION.read_text(encoding="utf-8"))
    norm = lambda p: re.sub(r"\{\{?\w+\}?\}", "{}", p)  # noqa: E731
    covered = {(r["method"], norm("/" + "/".join(r["url"]["path"]))) for r in _requests(col["item"])}
    for path, methods in app.openapi()["paths"].items():
        for m in methods:
            assert (m.upper(), norm(path)) in covered, f"{m.upper()} {path} missing from the Postman collection"


def test_postman_collection_runs_green(exported):
    from scripts.run_postman_collection import main
    assert main([]) == 0
