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
import os, sys, csv, json, time, hashlib
from datetime import date, datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("requests is required (pip install requests)")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_MODELS = "https://openrouter.ai/api/v1/models"
API_CHAT = "https://openrouter.ai/api/v1/chat/completions"
TODAY = date.today().isoformat()
MAX_CALLS = int(os.environ.get("WAIBACK_MAX_CALLS", "0"))    # 0 = no call cap (cost ceiling)
MAX_MODELS = int(os.environ.get("WAIBACK_MAX_MODELS", "0"))  # 0 = no model cap (do N not-yet-captured models)
MAX_TOKENS = int(os.environ.get("WAIBACK_MAX_TOKENS", "600"))

# Standard capture conditions, identical for every model so answers are comparable.
TEMPERATURE = float(os.environ.get("WAIBACK_TEMPERATURE", "0"))
_spf = os.path.join(ROOT, "data/system_prompt.txt")
SYSTEM_PROMPT = open(_spf).read().strip() if os.path.exists(_spf) else ""
PROMPT_SHA = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8] if SYSTEM_PROMPT else "none"

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
    msgs = ([{"role": "system", "content": SYSTEM_PROMPT}] if SYSTEM_PROMPT else []) + \
           [{"role": "user", "content": question}]
    body = {"model": model_id, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
            "messages": msgs,
            "usage": {"include": True}}  # ask OpenRouter to report token usage + charged cost
    headers = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json",
               "HTTP-Referer": "https://waiback.org", "X-Title": "Waiback"}
    # A network/timeout error must never crash the whole batch: retry transient
    # failures a few times with backoff, then return a soft error so the loop
    # keeps going and already-captured work is still saved + committed.
    last_err = "unknown"
    for attempt in range(3):
        try:
            r = requests.post(API_CHAT, headers=headers, json=body, timeout=90)
        except requests.exceptions.RequestException as e:
            last_err = f"request error: {type(e).__name__}"
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                j = r.json()
                u = j.get("usage") or {}
                return {"answer": j["choices"][0]["message"]["content"].strip(),
                        "prompt_tokens": u.get("prompt_tokens"),
                        "completion_tokens": u.get("completion_tokens"),
                        "total_tokens": u.get("total_tokens"),
                        "or_cost": u.get("cost")}, None
            except Exception:
                return None, "no content"
        # 429 / 5xx are transient: back off and retry. Other 4xx are permanent.
        if r.status_code == 429 or r.status_code >= 500:
            last_err = f"http {r.status_code}"
            time.sleep(2 * (attempt + 1))
            continue
        return None, f"http {r.status_code}"
    return None, last_err

def main():
    allow = read_allowlist()
    questions = read_questions()
    # archive the prompt registry (sha -> text) so past capture conditions are preserved
    ppath = os.path.join(ROOT, "data/prompts.json")
    try: prompts = json.load(open(ppath))
    except Exception: prompts = {}
    if SYSTEM_PROMPT and PROMPT_SHA not in prompts:
        prompts[PROMPT_SHA] = {"system_prompt": SYSTEM_PROMPT, "temperature": TEMPERATURE, "since": TODAY}
        json.dump(prompts, open(ppath, "w"), indent=2)
    models = refresh_models(allow)

    # keep only real (non-sample) prior answers; samples get replaced
    try:
        prior = json.load(open(os.path.join(ROOT, "data/responses.json")))
    except Exception:
        prior = []
    responses = [r for r in prior if not r.get("sample")]
    have = {(r["model_id"], r["question_id"]) for r in responses}

    # "attempted" models ledger so batches advance to NEW models instead of re-touching
    # partially-captured ones. Set WAIBACK_RETRY=1 to re-work attempted models and fill gaps.
    APATH = os.path.join(ROOT, "data/attempted.json")
    try: attempted = set(json.load(open(APATH)))
    except Exception: attempted = set()
    RETRY = os.environ.get("WAIBACK_RETRY", "").lower() in ("1", "true", "yes")

    RPATH = os.path.join(ROOT, "data/responses.json")
    def save(): json.dump(responses, open(RPATH, "w"), indent=2)
    def save_attempted(): json.dump(sorted(attempted), open(APATH, "w"), indent=2)

    total_pairs = len(models) * len(questions)
    already = len(have)
    calls = 0; models_worked = 0; stopped = None
    for m in models:
        # the ledger (data/responses.json) is the source of truth: skip pairs already captured
        missing = [q for q in questions if (m["id"], q["id"]) not in have]
        if not missing:
            attempted.add(m["id"])
            continue  # model already fully captured -> never re-polled
        if m["id"] in attempted and not RETRY:
            continue  # attempted in a prior run; skip so batches advance to new models
        if MAX_MODELS and models_worked >= MAX_MODELS:
            stopped = f"model cap WAIBACK_MAX_MODELS={MAX_MODELS}"
            break
        models_worked += 1
        dead = 0  # consecutive errors for this model
        got = 0   # successful captures for this model this run
        for q in missing:
            if MAX_CALLS and calls >= MAX_CALLS:
                stopped = f"call cap WAIBACK_MAX_CALLS={MAX_CALLS}"
                break
            res, err = ask(m["id"], q["question"])
            calls += 1
            if err:
                print(f"  skip {m['id']} / {q['id']}: {err}")
                dead += 1
                # a model that errors on its first few questions and has captured
                # nothing (e.g. a 404 :batch/non-chat variant) is not worth 46
                # wasted calls -> abandon it and move to the next model
                if dead >= 3 and got == 0:
                    print(f"  abandon {m['id']}: {dead} consecutive errors, no captures")
                    break
                time.sleep(0.4)
                continue
            dead = 0
            got += 1
            pt, ct = res.get("prompt_tokens"), res.get("completion_tokens")
            ip, op = m.get("input_per_1m"), m.get("output_per_1m")
            cost = None
            if pt is not None and ct is not None and ip is not None and op is not None:
                cost = round(pt / 1e6 * ip + ct / 1e6 * op, 6)  # cost at the pricing captured now
            responses.append({"model_id": m["id"], "question_id": q["id"], "response_date": TODAY,
                              "answer": res["answer"],
                              "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": res.get("total_tokens"),
                              "input_per_1m": ip, "output_per_1m": op,
                              "cost_usd": cost, "or_cost": res.get("or_cost"),
                              "temperature": TEMPERATURE, "prompt_sha": PROMPT_SHA})
            have.add((m["id"], q["id"]))
            print(f"  captured {m['id']} / {q['id']}")
            time.sleep(0.4)
        attempted.add(m["id"])
        save(); save_attempted()  # checkpoint after every model so an interrupted run loses nothing
        if stopped and "call cap" in stopped:
            break

    save(); save_attempted()
    done = len(have)
    models_left = sum(1 for m in models if any((m["id"], q["id"]) not in have for q in questions))
    print(f"done: {calls} new calls this run across {models_worked} model(s).")
    print(f"progress: {done}/{total_pairs} answers captured ({done - already} new); "
          f"models fully captured {len(models)-models_left}/{len(models)}, {models_left} remaining.")
    if stopped:
        print(f"stopped early ({stopped}). Re-run to continue; captured pairs are skipped automatically.")

if __name__ == "__main__":
    main()
