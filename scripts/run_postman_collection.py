"""Execute the Postman collection without Node/newman.

Substitutes {{variables}}, checks each request's expected status, applies the collection's ID
captures and waits for background jobs. Runs in-process against the FastAPI app by default, or
against a live server with --base-url.

    python -m scripts.run_postman_collection                 # in-process (TestClient)
    python -m scripts.run_postman_collection --base-url http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

COLLECTION = Path("postman/Marketing_Attribution_Engine.postman_collection.json")
CAPTURE_PATTERNS = [
    (re.compile(r"set\('(\w+)', j\.items\[0\]\.(\w+)\)"), lambda j, f: j["items"][0][f] if j.get("items") else None),
    (re.compile(r"set\('(\w+)', j\[0\]\.(\w+)\)"), lambda j, f: j[0][f] if j else None),
    (re.compile(r"set\('(\w+)', j\.run\.(\w+)\)"), lambda j, f: j["run"][f]),
    (re.compile(r"set\('(\w+)', j\.(\w+)\)"), lambda j, f: j.get(f)),
]


def sub(text: str, env: dict) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(env.get(m.group(1), m.group(0))), text)


def walk(items, prefix=""):
    for it in items:
        if "item" in it:
            yield from walk(it["item"], prefix + it["name"] + " / ")
        else:
            yield prefix + it["name"], it


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url")
    ap.add_argument("--collection", default=str(COLLECTION))
    args = ap.parse_args(argv)
    col = json.loads(Path(args.collection).read_text(encoding="utf-8"))
    env = {v["key"]: v["value"] for v in col["variable"]}
    if args.base_url:
        import httpx
        client, env["baseUrl"] = httpx.Client(base_url=args.base_url, timeout=600), ""
    else:
        from fastapi.testclient import TestClient
        from attribution_engine.api import app
        client, env["baseUrl"] = TestClient(app), ""
    failures, n = [], 0
    for name, it in walk(col["item"]):
        req = it["request"]
        exec_lines = it["event"][0]["script"]["exec"]
        expected = int(re.search(r"have\.status\((\d+)\)", exec_lines[0]).group(1))
        url = sub(req["url"]["raw"], env)
        body = json.loads(sub(req["body"]["raw"], env)) if req.get("body") else None
        resp = client.request(req["method"], url, json=body)
        n += 1
        ok = resp.status_code == expected
        try:
            j = resp.json()
        except Exception:
            j = {}
        script = "\n".join(exec_lines)
        if "rejectedLeadId" in script and isinstance(j, dict):
            hit = next((e for e in j.get("items", []) if e["issue_type"] == "anonymous_session_without_evidence"), None)
            if hit:
                env["rejectedLeadId"] = hit["record_id"]
        for line in script.splitlines():
            for pat, get in CAPTURE_PATTERNS:
                m = pat.search(line)
                if m and ok:
                    try:
                        val = get(j, m.group(2))
                    except (KeyError, IndexError, TypeError):
                        val = None
                    if val:
                        env[m.group(1)] = val
                    break
        if "identity_link" in script and ok and j.get("identity_link") != "rejected":
            ok = False
        if "custom run reconciles" in script and ok and j.get("passed") is not True:
            ok = False
        if ok and "job_id" in script:  # wait for background job
            for _ in range(600):
                st = client.get(f"/api/v1/pipeline/jobs/{env['jobId']}").json()
                if st["status"] in ("succeeded", "failed"):
                    ok = st["status"] == "succeeded"
                    break
                time.sleep(0.5)
        print(f"{'PASS' if ok else 'FAIL'} {resp.status_code} {req['method']:<4} {name}")
        if not ok:
            failures.append((name, resp.status_code, expected, resp.text[:300]))
    print(f"\n{n - len(failures)}/{n} requests passed")
    for f in failures:
        print("  FAILED:", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
