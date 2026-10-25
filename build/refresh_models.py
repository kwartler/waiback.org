#!/usr/bin/env python3
"""
Keep data/models.txt current with OpenRouter's catalog.

Runs before each capture. Pulls the live catalog, applies the archive's
allowlist policy, and MERGES any newly-released qualifying models into
data/models.txt (newest first). It is additive: a model already in the list is
never removed, even if OpenRouter later delists it or changes it, so the
historical archive is preserved. Only brand-new qualifying models are appended.

Allowlist policy (text-interview archive): keep chat models whose only output
modality is text. Drop:
  - ":batch" endpoints (OpenRouter batch API, not chat; duplicates of the base model)
  - image / audio / video generators (any non-text output modality)
  - the "openrouter/auto" router pseudo-models
"""
import os, sys, json
try:
    import requests
except ImportError:
    sys.exit("requests is required (pip install requests)")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_MODELS = "https://openrouter.ai/api/v1/models"
MODELS_TXT = os.path.join(ROOT, "data/models.txt")


def qualifies(m):
    mid = m.get("id", "")
    if not mid or mid.endswith(":batch"):
        return False
    if mid in ("openrouter/auto", "openrouter/auto-beta"):
        return False
    outs = (m.get("architecture") or {}).get("output_modalities") or []
    # text-only output: present, and nothing else (no image/audio/video)
    return "text" in outs and all(o == "text" for o in outs)


def read_existing():
    ids = []
    if os.path.exists(MODELS_TXT):
        for line in open(MODELS_TXT):
            s = line.strip()
            if s and not s.startswith("#"):
                ids.append(s)
    return ids


def main():
    r = requests.get(API_MODELS, timeout=40)
    r.raise_for_status()
    catalog = r.json().get("data", [])
    created = {m["id"]: (m.get("created") or 0) for m in catalog}
    qualifying = {m["id"] for m in catalog if qualifies(m)}

    existing = read_existing()
    existing_set = set(existing)
    new_ids = [mid for mid in qualifying if mid not in existing_set]

    if not new_ids:
        print(f"refresh_models: no new models (allowlist stays at {len(existing)}).")
        return

    all_ids = existing + new_ids
    # newest first; delisted ids (no catalog 'created') sort to the bottom but are kept
    all_ids.sort(key=lambda mid: created.get(mid, 0), reverse=True)

    header = (f"# One OpenRouter model id per line; the Action captures these. "
              f"Auto-refreshed from the OpenRouter catalog ({len(all_ids)} text-output chat "
              f"models, newest first; batch/image/audio/router variants excluded).\n")
    with open(MODELS_TXT, "w") as f:
        f.write(header)
        for mid in all_ids:
            f.write(mid + "\n")

    print(f"refresh_models: added {len(new_ids)} new model(s); allowlist now {len(all_ids)}.")
    for mid in new_ids:
        print(f"  + {mid}")


if __name__ == "__main__":
    main()
