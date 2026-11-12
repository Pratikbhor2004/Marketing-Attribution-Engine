"""Build postman/Marketing_Attribution_Engine.postman_collection.json from the live OpenAPI schema.

Every operation in the FastAPI app becomes a request, grouped in folders by tag, so the collection
cannot drift from the code. List requests capture real IDs into collection variables that the
detail requests reuse; a final folder exercises the documented exception paths.

    python -m scripts.build_postman_collection
"""
from __future__ import annotations

import json
from pathlib import Path

from attribution_engine.api import TAGS, app

OUT = Path("postman")
PATH_VARS = {"campaign_id": "campaignId", "audience_id": "audienceId", "contact_id": "contactId", "session_id": "sessionId",
             "lead_id": "leadId", "opportunity_id": "opportunityId", "touchpoint_id": "touchpointId", "run_id": "runId",
             "exception_id": "exceptionId", "job_id": "jobId"}
DEFAULTS = {"baseUrl": "http://localhost:8000", "campaignId": "CMP-2026-000002", "audienceId": "AUD-2026-000001",
            "contactId": "CON-2026-000002", "sessionId": "SES-2026-000001", "leadId": "LEAD-2026-000001",
            "opportunityId": "OPP-2026-000001", "openOpportunityId": "", "touchpointId": "TOUCH-CON-2026-000001-000001",
            "runId": "RUN-BASELINE", "customRunId": "", "exceptionId": "EXC-2026-000001", "jobId": "", "rejectedLeadId": ""}
# Which query params to enable (with example values) per operation; others are included but disabled.
ENABLED_QUERY = {
    ("get", "/api/v1/campaigns"): {"limit": "20"},
    ("get", "/api/v1/contacts"): {"limit": "20"},
    ("get", "/api/v1/sessions"): {"anonymous": "true", "limit": "20"},
    ("get", "/api/v1/leads"): {"has_session": "false", "limit": "20"},
    ("get", "/api/v1/opportunities"): {"stage": "closed_won", "limit": "20"},
    ("get", "/api/v1/touchpoints"): {"has_conversion_claim": "true", "limit": "20"},
    ("get", "/api/v1/orders"): {"limit": "20"},
    ("get", "/api/v1/cost-records"): {"limit": "20"},
    ("get", "/api/v1/creatives"): {"limit": "20"},
    ("get", "/api/v1/fx-rates"): {"currency": "INR"},
    ("get", "/api/v1/attribution/results"): {"run_id": "{{runId}}", "model": "linear_multi_touch", "limit": "50"},
    ("get", "/api/v1/attribution/compare"): {"run_id": "{{runId}}"},
    ("get", "/api/v1/roi/campaigns"): {"model": "linear_multi_touch", "sort_by": "roi_pct", "limit": "25"},
    ("get", "/api/v1/roi/channels"): {"model": "linear_multi_touch"},
    ("get", "/api/v1/exceptions"): {"limit": "100"},
    ("get", "/api/v1/evaluation/chronological"): {"cutoff": "2026-07-01"},
    ("get", "/api/v1/pipeline/validation"): {"only_failed": "true"},
}
# Response captures: test script snippets that store IDs for later requests.
CAPTURE = {
    ("get", "/api/v1/campaigns"): "if (j.items && j.items.length) pm.collectionVariables.set('campaignId', j.items[0].campaign_id);",
    ("get", "/api/v1/contacts"): "if (j.items && j.items.length) pm.collectionVariables.set('contactId', j.items[0].contact_id);",
    ("get", "/api/v1/sessions"): "if (j.items && j.items.length) pm.collectionVariables.set('sessionId', j.items[0].session_id);",
    ("get", "/api/v1/leads"): "if (j.items && j.items.length) pm.collectionVariables.set('leadId', j.items[0].lead_id);",
    ("get", "/api/v1/opportunities"): "if (j.items && j.items.length) pm.collectionVariables.set('opportunityId', j.items[0].opportunity_id);",
    ("get", "/api/v1/touchpoints"): "if (j.items && j.items.length) pm.collectionVariables.set('touchpointId', j.items[0].touchpoint_id);",
    ("get", "/api/v1/audiences"): "if (j.length) pm.collectionVariables.set('audienceId', j[0].audience_id);",
    ("get", "/api/v1/exceptions"): "if (j.items && j.items.length) pm.collectionVariables.set('exceptionId', j.items[0].exception_id);\n"
                                   "const rl = (j.items||[]).find(e => e.issue_type === 'anonymous_session_without_evidence');\n"
                                   "if (rl) pm.collectionVariables.set('rejectedLeadId', rl.record_id);",
    ("post", "/api/v1/attribution/runs"): "pm.collectionVariables.set('customRunId', j.run.run_id);",
    ("post", "/api/v1/pipeline/generate"): "pm.collectionVariables.set('jobId', j.job_id);",
    ("post", "/api/v1/pipeline/reproducibility"): "pm.collectionVariables.set('jobId', j.job_id);",
}
EXPECTED_STATUS = {("post", "/api/v1/pipeline/generate"): 202, ("post", "/api/v1/pipeline/reproducibility"): 202,
                   ("post", "/api/v1/attribution/runs"): 201, ("post", "/api/v1/opportunities/{opportunity_id}/events"): 201}
BODIES = {
    ("post", "/api/v1/pipeline/generate"): {"scale": "starter", "seed": 20261022},
    ("post", "/api/v1/pipeline/reproducibility"): {"scale": "starter", "seed": 20261022},
    ("post", "/api/v1/attribution/runs"): {"models": ["first_touch", "last_touch", "linear_multi_touch", "position_based", "time_decay"],
                                           "lookback_days": 90, "time_decay_half_life_days": 7, "position_first_weight": 0.4,
                                           "position_last_weight": 0.4, "include_offline": False, "triggered_by": "postman_analyst"},
    ("post", "/api/v1/opportunities/{opportunity_id}/events"): {"to_stage": "proposal", "actor": "sales_ops", "note": "Proposal sent"},
}
DESCRIPTIONS_EXTRA = {
    ("post", "/api/v1/opportunities/{opportunity_id}/events"):
        "Uses {{openOpportunityId}} captured by 'Find an open (qualified) opportunity'. Allowed transitions: "
        "prospecting->qualified|closed_lost, qualified->proposal|closed_lost, proposal->closed_won|closed_lost.",
    ("post", "/api/v1/pipeline/generate"): "Regenerates the whole dataset. Poll 'Job status' until status=succeeded. "
                                           "Use scale=full for 500 campaigns / 50k contacts / 200k touchpoints / 10k opportunities (minutes).",
}
ORDER = [  # list endpoints first so captured IDs feed detail endpoints
    "/health", "/api/v1/meta/config", "/api/v1/meta/manifest", "/api/v1/meta/schema",
    "/api/v1/pipeline/validation", "/api/v1/pipeline/validate", "/api/v1/pipeline/reproducibility", "/api/v1/pipeline/generate",
    "/api/v1/pipeline/jobs", "/api/v1/pipeline/jobs/{job_id}",
    "/api/v1/channels", "/api/v1/campaigns", "/api/v1/campaigns/{campaign_id}", "/api/v1/campaigns/{campaign_id}/costs",
    "/api/v1/campaigns/{campaign_id}/touchpoints", "/api/v1/creatives", "/api/v1/audiences", "/api/v1/audiences/{audience_id}/campaigns",
    "/api/v1/fx-rates", "/api/v1/contacts", "/api/v1/contacts/{contact_id}", "/api/v1/contacts/{contact_id}/journey",
    "/api/v1/sessions", "/api/v1/sessions/{session_id}", "/api/v1/leads", "/api/v1/leads/{lead_id}",
    "/api/v1/opportunities", "/api/v1/opportunities/{opportunity_id}", "/api/v1/opportunities/{opportunity_id}/events",
    "/api/v1/touchpoints", "/api/v1/touchpoints/{touchpoint_id}", "/api/v1/orders", "/api/v1/cost-records",
]


def url(path: str, query: list[dict]) -> dict:
    raw_path = path
    for name, var in PATH_VARS.items():
        raw_path = raw_path.replace("{" + name + "}", "{{" + var + "}}")
    q = "&".join(f"{p['key']}={p['value']}" for p in query if not p.get("disabled"))
    return {"raw": "{{baseUrl}}" + raw_path + (f"?{q}" if q else ""), "host": ["{{baseUrl}}"],
            "path": [s for s in raw_path.strip("/").split("/")], "query": query}


def test_script(method: str, path: str, expected: int, capture: str | None) -> dict:
    lines = [f"pm.test('status is {expected}', () => pm.response.to.have.status({expected}));",
             "pm.test('responds in < 15s', () => pm.expect(pm.response.responseTime).to.be.below(15000));"]
    if capture:
        lines += ["let j = {}; try { j = pm.response.json(); } catch (e) {}", capture]
    return {"listen": "test", "script": {"type": "text/javascript", "exec": lines}}


def build() -> dict:
    spec = app.openapi()
    folders = {t["name"]: {"name": t["name"], "description": t["description"], "item": []} for t in TAGS}
    ops = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            ops.append((path, method, op))
    rank = {p: i for i, p in enumerate(ORDER)}
    ops.sort(key=lambda x: (rank.get(x[0], 999), x[0], x[1] != "get"))
    for path, method, op in ops:
        tag = (op.get("tags") or ["System"])[0]
        enabled = ENABLED_QUERY.get((method, path), {})
        query = []
        for p in op.get("parameters", []):
            if p["in"] != "query":
                continue
            default = p.get("schema", {}).get("default")
            value = enabled.get(p["name"], "" if default is None else str(default).lower() if isinstance(default, bool) else str(default))
            query.append({"key": p["name"], "value": value, "disabled": p["name"] not in enabled,
                          "description": p.get("description") or p.get("schema", {}).get("title", "")})
        u = url(path, query)
        if method == "post" and path == "/api/v1/opportunities/{opportunity_id}/events":
            u["raw"] = u["raw"].replace("{{opportunityId}}", "{{openOpportunityId}}")
            u["path"] = [s.replace("{{opportunityId}}", "{{openOpportunityId}}") for s in u["path"]]
        req = {"method": method.upper(), "header": [{"key": "Accept", "value": "application/json"}], "url": u,
               "description": (op.get("summary", "") + "\n\n" + DESCRIPTIONS_EXTRA.get((method, path), "")).strip()}
        if (method, path) in BODIES:
            req["header"].append({"key": "Content-Type", "value": "application/json"})
            req["body"] = {"mode": "raw", "raw": json.dumps(BODIES[(method, path)], indent=2), "options": {"raw": {"language": "json"}}}
        expected = EXPECTED_STATUS.get((method, path), 200)
        item = {"name": op.get("summary", f"{method.upper()} {path}"), "request": req,
                "event": [test_script(method, path, expected, CAPTURE.get((method, path)))]}
        folders[tag]["item"].append(item)
        if (method, path) == ("get", "/api/v1/opportunities"):
            folders[tag]["item"].append({
                "name": "Find an open (qualified) opportunity",
                "request": {"method": "GET", "header": [], "description": "Captures {{openOpportunityId}} for the stage-change request.",
                            "url": url(path, [{"key": "stage", "value": "qualified"}, {"key": "limit", "value": "1"}])},
                "event": [test_script("get", path, 200, "if (j.items && j.items.length) pm.collectionVariables.set('openOpportunityId', j.items[0].opportunity_id);")]})
        if (method, path) == ("post", "/api/v1/attribution/runs"):
            folders[tag]["item"].append({
                "name": "Custom run: results, compare, reconciliation",
                "request": {"method": "GET", "header": [], "description": "Reads the run created above ({{customRunId}}).",
                            "url": url("/api/v1/roi/reconciliation", [{"key": "run_id", "value": "{{customRunId}}"}])},
                "event": [test_script("get", "/api/v1/roi/reconciliation", 200,
                                      "pm.test('custom run reconciles', () => pm.expect(j.passed).to.eql(true));")]})

    negative = {"name": "Exception paths (expected errors)", "description": "Negative scenarios: each request must fail with the documented status.", "item": []}

    def neg(name, method, path, status, body=None, query=None, desc=""):
        req = {"method": method, "header": [{"key": "Content-Type", "value": "application/json"}], "url": url(path, query or []), "description": desc}
        if body is not None:
            req["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
        negative["item"].append({"name": name, "request": req, "event": [test_script(method.lower(), path, status, None)]})

    neg("404 unknown campaign", "GET", "/api/v1/campaigns/CMP-2026-999999", 404)
    neg("404 unknown attribution run", "GET", "/api/v1/attribution/runs/RUN-2026-999999", 404)
    neg("422 unknown attribution model", "GET", "/api/v1/roi/campaigns", 422, query=[{"key": "model", "value": "made_up_model"}])
    neg("422 position weights exceed 1", "POST", "/api/v1/attribution/runs", 422,
        body={"position_first_weight": 0.7, "position_last_weight": 0.6}, desc="first + last weight must be <= 1")
    neg("422 lookback must be >= 1", "POST", "/api/v1/attribution/runs", 422, body={"lookback_days": 0})
    neg("409 invalid lifecycle transition (back to prospecting)", "POST", "/api/v1/opportunities/{{openOpportunityId}}/events", 409,
        body={"to_stage": "prospecting"}, desc="Run 'Find an open (qualified) opportunity' first; stages never move backwards.")
    neg("409 closed opportunity accepts no further transitions", "POST", "/api/v1/opportunities/{{opportunityId}}/events", 409,
        body={"to_stage": "qualified"}, desc="{{opportunityId}} is captured from closed_won opportunities; terminal stages are final.")
    neg("422 out-of-order lifecycle event", "POST", "/api/v1/opportunities/{{openOpportunityId}}/events", 422,
        body={"to_stage": "closed_lost", "occurred_at": "2020-01-01T00:00:00"},
        desc="An event older than the opportunity's latest event is rejected; history stays chronological.")
    neg("422 invalid chronological cutoff", "GET", "/api/v1/evaluation/chronological", 422, query=[{"key": "cutoff", "value": "not-a-date"}])
    neg("Rejected identity link is visible", "GET", "/api/v1/leads/{{rejectedLeadId}}", 200,
        desc="Lead stitched to an anonymous session: identity_link must be 'rejected'.")
    negative["item"][-1]["event"][0]["script"]["exec"].append(
        "pm.test('identity link rejected', () => pm.expect(pm.response.json().identity_link).to.eql('rejected'));")

    return {
        "info": {"name": "Marketing Attribution Engine API", "_postman_id": "8f6d3c1e-5a2b-4c7d-9e0f-2026102200a1",
                 "description": "All endpoints of the Marketing Attribution Engine, generated from the OpenAPI schema "
                                "(scripts/build_postman_collection.py). Run folders top to bottom: list requests capture IDs "
                                "used by detail requests. Start the API with `uvicorn attribution_engine.api:app` or `docker compose up`.",
                 "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        # Pipeline (regenerates data) runs last so it cannot wipe the dataset the other folders read.
        "item": [f for n, f in folders.items() if f["item"] and n != "Pipeline"] + [negative, folders["Pipeline"]],
        "variable": [{"key": k, "value": v} for k, v in DEFAULTS.items()],
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    col = build()
    (OUT / "Marketing_Attribution_Engine.postman_collection.json").write_text(json.dumps(col, indent=2), encoding="utf-8")
    env = {"id": "c0ffee00-0000-4000-8000-000000020261", "name": "Marketing Attribution - Local",
           "values": [{"key": "baseUrl", "value": "http://localhost:8000", "enabled": True}], "_postman_variable_scope": "environment"}
    (OUT / "Marketing_Attribution_Engine.postman_environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
    n = sum(len(f["item"]) for f in col["item"])
    ops = sum(len(m) for m in app.openapi()["paths"].values())
    print(f"wrote {n} requests ({ops} OpenAPI operations + helper/negative requests) in {len(col['item'])} folders")


if __name__ == "__main__":
    main()
