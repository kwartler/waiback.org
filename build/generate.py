#!/usr/bin/env python3
"""
Capture model answers for Waiback via the OpenRouter API.

Reads  : data/questions.csv, data/models.txt (allowlist of OpenRouter model ids)
Writes : data/models.json  (metadata snapshot incl. release date, pricing, context)
         data/responses.json (one record per model x question, with response_date)

Security: the API key is read ONLY from the OPENROUTER_API_KEY environment
variable (a GitHub Actions secret). It is never written to disk or printed.

Cost control: this run only queries (model, question) pairs that have not been
captured yet, up to WAIBACK_MAX_CALLS new calls (default 300). Re-runs fill in
new models/questions, so it is safe and idempotent. Sample placeholders are
replaced by real answers.
"""
import os, sys, csv, json, time
from datetime import date, datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("requests is required (pip install requests)")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_MODELS = "https://openrouter.ai/api/v1/models"
API_CHAT = "https://openrouter.ai/api/v1/chat/completions"
TODAY = date.today().isoformat()
MAX_CALLS = int(os.environ.get("WAIBACK_MAX_CALLS", "300"))
MAX_TOKENS = int(os.environ.get("WAIBACK_MAX_TOKENS", "600"))

KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
if not KEY:
    sys.exit("ERROR: OPENROUTER_API_KEY is not set. Add it as a GitHub Actions secret.")

def dts(ts):
    try: return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    except Exception: return ""
def per1m(x):
    try: return round(float(x) * 1_000_000, 4)
    except Exception: return None

def read_allowlist():
    p = os.path.join(ROOT, "data/models.txt")
    out = []
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out

def read_questions():
    with open(os.path.join(ROOT, "data/questions.csv"), newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("id") and r.get("question")]

def refresh_models(allow):
    r = requests.get(API_MODELS, timeout=40)
    r.raise_for_status()
    byid = {m["id"]: m for m in r.json().get("data", [])}
    models = []
    for mid in allow:
        m = byid.get(mid)
        if not m:
            print(f"  note: {mid} not found in OpenRouter catalog (kept in allowlist)")
            continue
        pr = m.get("pricing", {})
        models.append({
            "id": mid, "slug": mid.replace("/", "_"),
            "name": m.get("name", mid), "provider": mid.split("/")[0],
            "release_date": dts(m.get("created")),
            "knowledge_cutoff": m.get("knowledge_cutoff") or "",
            "context_length": m.get("context_length"),
            "input_per_1m": per1m(pr.get("prompt")), "output_per_1m": per1m(pr.get("completion")),
            "description": (m.get("description") or "").strip(),
        })
    json.dump(models, open(os.path.join(ROOT, "data/models.json"), "w"), indent=2)
    print(f"refreshed data/models.json: {len(models)} models")
    return models

def ask(model_id, question):
    body = {"model": model_id, "temperature": 0, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": question}]}
    headers = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json",
               "HTTP-Referer": "https://waiback.org", "X-Title": "Waiback"}
    r = requests.post(API_CHAT, headers=headers, json=body, timeout=90)
    if r.status_code != 200:
        return None, f"http {r.status_code}"
    j = r.json()
    try:
        return j["choices"][0]["message"]["content"].strip(), None
    except Exception:
        return None, "no content"

def main():
    allow = read_allowlist()
    questions = read_questions()
    models = refresh_models(allow)

    # keep only real (non-sample) prior answers; samples get replaced
    try:
        prior = json.load(open(os.path.join(ROOT, "data/responses.json")))
    except Exception:
        prior = []
    responses = [r for r in prior if not r.get("sample")]
    have = {(r["model_id"], r["question_id"]) for r in responses}

    calls = 0
    for m in models:
        for q in questions:
            key = (m["id"], q["id"])
            if key in have:
                continue
            if calls >= MAX_CALLS:
                print(f"reached WAIBACK_MAX_CALLS={MAX_CALLS}; {len(responses)} answers saved, rest will fill on the next run.")
                json.dump(responses, open(os.path.join(ROOT, "data/responses.json"), "w"), indent=2)
                return
            answer, err = ask(m["id"], q["question"])
            calls += 1
            if err:
                print(f"  skip {m['id']} / {q['id']}: {err}")
                time.sleep(0.4)
                continue
            responses.append({"model_id": m["id"], "question_id": q["id"],
                              "answer": answer, "response_date": TODAY})
            have.add(key)
            print(f"  captured {m['id']} / {q['id']}")
            time.sleep(0.4)

    json.dump(responses, open(os.path.join(ROOT, "data/responses.json"), "w"), indent=2)
    print(f"done: {calls} new calls, {len(responses)} total answers saved.")

if __name__ == "__main__":
    main()
