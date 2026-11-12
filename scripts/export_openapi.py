"""Export the Swagger / OpenAPI documentation for every endpoint.

    python -m scripts.export_openapi            # capture real examples from a fresh starter dataset, then export
    python -m scripts.export_openapi --no-capture   # reuse attribution_engine/openapi_examples.json

Writes:
  attribution_engine/openapi_examples.json   real example response per operation (served in /docs too)
  docs/openapi.json, docs/openapi.yaml        the full OpenAPI 3.1 spec
  docs/swagger.html                           standalone Swagger UI (open in a browser, no server needed to read)
  docs/api_reference.md                       Markdown reference: params, bodies, responses, examples, curl
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

DOCS = Path("docs")
MAX_ITEMS = 2
MAX_KEYS = 12


def shrink(obj, depth: int = 0):
    """Keep examples readable: first items of lists, first keys of very wide maps."""
    if isinstance(obj, list):
        return [shrink(x, depth + 1) for x in obj[:MAX_ITEMS]]
    if isinstance(obj, dict):
        keys = list(obj)
        if len(keys) > MAX_KEYS and all(isinstance(obj[k], (dict, list)) for k in keys):
            keys = keys[:3]                     # e.g. per-table maps in /meta/schema
        return {k: shrink(obj[k], depth + 1) for k in keys}
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


# ------------------------------------------------------------------------------------- example capture
def capture() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="openapi_examples_")) / "generated"
    os.environ["ATTRIBUTION_DATA_DIR"] = str(tmp)
    from fastapi.testclient import TestClient

    from attribution_engine.api import app
    from attribution_engine.config import DatasetConfig
    from attribution_engine.pipeline import connect, export, run_pipeline

    cfg = DatasetConfig.starter()
    export(run_pipeline(cfg), cfg, tmp)
    c = TestClient(app)

    def first(sql: str) -> str:
        with connect(tmp / "attribution.db") as conn:
            return conn.execute(sql).fetchone()[0]

    won = first("SELECT o.opportunity_id FROM opportunity o JOIN attributionresult a USING (opportunity_id) "
                "WHERE a.run_id='RUN-BASELINE' GROUP BY o.opportunity_id HAVING COUNT(DISTINCT a.campaign_id) > 1 "
                "ORDER BY o.opportunity_id LIMIT 1")
    ids = {
        "campaign_id": first("SELECT campaign_id FROM campaignroi WHERE roi_status='calculated' ORDER BY campaign_id LIMIT 1"),
        "audience_id": first("SELECT audience_id FROM campaignaudience ORDER BY audience_id LIMIT 1"),
        "contact_id": first(f"SELECT contact_id FROM opportunity WHERE opportunity_id='{won}'"),
        "session_id": first("SELECT session_id FROM lead WHERE session_id IS NOT NULL ORDER BY lead_id LIMIT 1"),
        "lead_id": first("SELECT lead_id FROM lead WHERE session_id IS NOT NULL ORDER BY lead_id LIMIT 1"),
        "opportunity_id": won,
        "touchpoint_id": first("SELECT touchpoint_id FROM touchpoint ORDER BY touchpoint_id LIMIT 1"),
        "exception_id": first("SELECT exception_id FROM exceptionlog WHERE issue_type='duplicate_revenue' ORDER BY exception_id LIMIT 1"),
        "run_id": "RUN-BASELINE",
    }
    open_opp = first("SELECT opportunity_id FROM opportunity_current_stage WHERE current_stage='qualified' ORDER BY opportunity_id LIMIT 1")

    from attribution_engine.openapi_docs import BODY_EXAMPLES
    spec = app.openapi()
    examples: dict[str, object] = {}

    def call(method: str, path: str, url: str, body=None, expect=(200, 201, 202)):
        r = c.request(method, url, json=body)
        assert r.status_code in expect, (method, url, r.status_code, r.text[:300])
        examples[f"{method} {path}"] = shrink(r.json())
        return r.json()

    # Reproducibility job first so the job endpoints have a real, finished job to show.
    job = call("POST", "/api/v1/pipeline/reproducibility", "/api/v1/pipeline/reproducibility", {"scale": "starter", "seed": cfg.seed})
    for _ in range(600):
        if c.get(f"/api/v1/pipeline/jobs/{job['job_id']}").json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.2)
    ids["job_id"] = job["job_id"]

    query_examples = {"/api/v1/attribution/results": "?model=linear_multi_touch&limit=2",
                      "/api/v1/roi/campaigns": "?model=time_decay&sort_by=roi_pct&limit=2",
                      "/api/v1/opportunities": "?stage=closed_won&limit=2",
                      "/api/v1/exceptions": "?issue_type=duplicate_revenue&limit=2",
                      "/api/v1/pipeline/validation": "?only_failed=true",
                      "/api/v1/fx-rates": "?currency=INR"}
    gets = sorted(p for p, item in spec["paths"].items() if "get" in item)
    for path in gets:
        url = path
        for name, val in ids.items():
            url = url.replace("{" + name + "}", val)
        if "{" in url:
            raise SystemExit(f"no example id for {path}")
        q = query_examples.get(path, "?limit=2" if any(p["name"] == "limit" for p in spec["paths"][path]["get"].get("parameters", [])) else "")
        call("GET", path, url + q)

    # Writes, in an order that keeps the dataset consistent.
    run = call("POST", "/api/v1/attribution/runs", "/api/v1/attribution/runs",
               BODY_EXAMPLES["POST /api/v1/attribution/runs"]["short_lookback"]["value"])
    call("GET", "/api/v1/attribution/runs/{run_id}", f"/api/v1/attribution/runs/{run['run']['run_id']}")
    call("POST", "/api/v1/opportunities/{opportunity_id}/events", f"/api/v1/opportunities/{open_opp}/events",
         BODY_EXAMPLES["POST /api/v1/opportunities/{opportunity_id}/events"]["advance"]["value"])
    call("POST", "/api/v1/exceptions/detect", "/api/v1/exceptions/detect")
    call("POST", "/api/v1/pipeline/validate", "/api/v1/pipeline/validate")
    call("GET", "/api/v1/pipeline/jobs", "/api/v1/pipeline/jobs")
    gen = call("POST", "/api/v1/pipeline/generate", "/api/v1/pipeline/generate", {"scale": "starter", "seed": cfg.seed})
    for _ in range(600):                        # let the background job finish before the temp dir is abandoned
        if c.get(f"/api/v1/pipeline/jobs/{gen['job_id']}").json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.2)

    missing = [f"{m.upper()} {p}" for p, item in spec["paths"].items() for m in item if f"{m.upper()} {p}" not in examples]
    if missing:
        raise SystemExit(f"no example captured for {missing}")
    return dict(sorted(examples.items()))


# ------------------------------------------------------------------------------------------ rendering
def _type(schema: dict) -> str:
    if "anyOf" in schema:
        return " | ".join(_type(s) for s in schema["anyOf"] if s.get("type") != "null") or "null"
    if "enum" in schema:
        return "enum: " + ", ".join(f"`{v}`" for v in schema["enum"])
    if "const" in schema:
        return f"`{schema['const']}`"
    if schema.get("type") == "array":
        return f"array of {_type(schema.get('items', {}))}"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return schema.get("type", "any")


def _bounds(schema: dict) -> str:
    parts = [f"{k} {schema[k]}" for k in ("minimum", "exclusiveMinimum", "maximum", "maxLength") if k in schema]
    return f" ({', '.join(parts)})" if parts else ""


def _json_block(obj, max_lines: int = 45) -> str:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["  ... (truncated, see docs/openapi.json for the full example)"]
    return "```json\n" + "\n".join(lines) + "\n```"


def _anchor(text: str) -> str:
    return "".join(ch for ch in text.lower().replace(" ", "-") if ch.isalnum() or ch in "-_")


def render_markdown(spec: dict) -> str:
    schemas = spec["components"]["schemas"]
    by_tag: dict[str, list] = {t["name"]: [] for t in spec.get("tags", [])}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            by_tag.setdefault(op.get("tags", ["Other"])[0], []).append((method.upper(), path, op))
    total = sum(len(v) for v in by_tag.values())

    out = [f"# {spec['info']['title']}: API reference", "",
           f"Version {spec['info']['version']} · OpenAPI {spec['openapi']} · **{total} operations** · base URL `http://localhost:8000`", "",
           "> Generated by `python -m scripts.export_openapi` from the running code, so it cannot drift from the API.",
           "> Interactive versions: live Swagger UI at `http://localhost:8000/docs`, ReDoc at `/redoc`, "
           "offline [swagger.html](swagger.html). Machine-readable: [openapi.json](openapi.json) / [openapi.yaml](openapi.yaml). "
           "Example responses were captured from the starter dataset (seed 20261022); lists are cut to the first items.", "",
           spec["info"]["description"], "", "## Endpoint index", "", "| Tag | Method | Path | Summary |", "|---|---|---|---|"]
    for tag, ops in by_tag.items():
        for method, path, op in ops:
            out.append(f"| {tag} | `{method}` | [`{path}`](#{_anchor(method + ' ' + path)}) | {op.get('summary', '')} |")
    for tag in spec.get("tags", []):
        ops = by_tag.get(tag["name"], [])
        out += ["", f"## {tag['name']}", "", tag.get("description", "")]
        for method, path, op in ops:
            out += ["", f"### {method} {path}", "", f"**{op.get('summary', '')}**", "", op.get("description", "")]
            params = op.get("parameters", [])
            if params:
                out += ["", "**Parameters**", "", "| Name | In | Type | Required | Default | Description |", "|---|---|---|---|---|---|"]
                for p in params:
                    s = p.get("schema", {})
                    default = f"`{s['default']}`" if "default" in s and s["default"] is not None else ""
                    ex = f" Example: `{p['example']}`." if "example" in p else ""
                    out.append(f"| `{p['name']}` | {p['in']} | {_type(s)}{_bounds(s)} | {'yes' if p.get('required') else 'no'} | "
                               f"{default} | {p.get('description', '')}{ex} |")
            body = op.get("requestBody")
            if body:
                content = body["content"]["application/json"]
                ref = content["schema"].get("$ref", "").rsplit("/", 1)[-1]
                props = schemas.get(ref, {}).get("properties", {})
                req = set(schemas.get(ref, {}).get("required", []))
                out += ["", f"**Request body** (`application/json`, schema `{ref}`)", "",
                        "| Field | Type | Required | Default | Description |", "|---|---|---|---|---|"]
                for name, s in props.items():
                    default = json.dumps(s["default"]) if "default" in s else ""
                    out.append(f"| `{name}` | {_type(s)}{_bounds(s)} | {'yes' if name in req else 'no'} | {default} | {s.get('description', '')} |")
                for ex in content.get("examples", {}).values():
                    out += ["", f"Example: *{ex['summary']}*", "", _json_block(ex["value"])]
            out += ["", "**Responses**", "", "| Status | Meaning |", "|---|---|"]
            for code, r in op["responses"].items():
                out.append(f"| `{code}` | {r.get('description', '')} |")
            for code, r in op["responses"].items():
                ex = r.get("content", {}).get("application/json", {}).get("example")
                if ex is not None:
                    label = "Example response" if code.startswith("2") else f"Example `{code}`"
                    out += ["", f"{label} (`{code}`):", "", _json_block(ex)]
            curl_path = path
            for p in params:
                if p["in"] == "path":
                    curl_path = curl_path.replace("{" + p["name"] + "}", str(p.get("example", p["name"])))
            curl = f"curl -X {method} \"http://localhost:8000{curl_path}\""
            if body:
                first_ex = next(iter(body["content"]["application/json"].get("examples", {}).values()), {"value": {}})["value"]
                curl += f" \\\n  -H \"Content-Type: application/json\" \\\n  -d '{json.dumps(first_ex)}'"
            out += ["", "```bash", curl, "```"]
    schemas_list = [n for n in schemas if not n.startswith(("HTTPValidationError", "ValidationError"))]
    out += ["", "## Schemas", "", "Request models: " + ", ".join(f"`{n}`" for n in schemas_list) +
            ". Validation failures (422) use FastAPI's `HTTPValidationError` (`detail` is a list of `{loc, msg, type}`); "
            "business errors use `ErrorResponse` (`detail` is a string or an object with `error`)."]
    return "\n".join(out) + "\n"


SWAGGER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marketing Attribution Engine API: Swagger</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">
<style>body{margin:0;background:#fafafa}.topbar{display:none}</style>
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>
// Standalone copy of docs/openapi.json. "Try it out" calls the server in `servers` (start it with uvicorn first).
const spec = __SPEC__;
window.ui = SwaggerUIBundle({spec, dom_id: "#swagger-ui", docExpansion: "list", filter: true,
  displayRequestDuration: true, defaultModelsExpandDepth: 0, deepLinking: true});
</script>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-capture", action="store_true", help="reuse the existing openapi_examples.json")
    args = ap.parse_args()

    from attribution_engine import openapi_docs
    if not args.no_capture:
        ex = capture()
        openapi_docs.EXAMPLES_FILE.write_text(json.dumps(ex, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"captured {len(ex)} example responses -> {openapi_docs.EXAMPLES_FILE}")

    from attribution_engine.api import app
    app.openapi_schema = None                     # rebuild with the fresh examples
    spec = app.openapi()
    DOCS.mkdir(exist_ok=True)
    (DOCS / "openapi.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        import yaml
        (DOCS / "openapi.yaml").write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    except ImportError:
        print("PyYAML not installed; skipped docs/openapi.yaml")
    (DOCS / "swagger.html").write_text(SWAGGER_HTML.replace("__SPEC__", json.dumps(spec, ensure_ascii=False)), encoding="utf-8")
    (DOCS / "api_reference.md").write_text(render_markdown(spec), encoding="utf-8")
    n = sum(len(v) for v in spec["paths"].values())
    print(f"{n} operations -> docs/openapi.json, docs/openapi.yaml, docs/swagger.html, docs/api_reference.md")


if __name__ == "__main__":
    main()
