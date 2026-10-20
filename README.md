# waiback.org

**A wayback machine for large language models.** Every model is asked the same fixed set of questions; its answers are captured and preserved with a date. Each model gets a snapshot page (release date + capture date + answer sheet). Built by [Ted Kwartler](https://tedkwartler.com/).

Static site on GitHub Pages. A GitHub Action calls the OpenRouter API (key hidden as a repo secret) to capture answers, then regenerates the HTML and commits it.

## How it works
```
data/questions.csv   one question per row (id,question,category)  <- you edit this
data/models.txt      OpenRouter model ids to query (allowlist)    <- you edit this
data/models.json     generated: model metadata + release dates
data/responses.json  generated: captured answers (model x question, dated)
build/generate.py    queries OpenRouter (needs OPENROUTER_API_KEY), writes the data
build/render.py      builds index.html + models/*.html + questions/*.html + sitemap + llms.txt
.github/workflows/waiback.yml   daily cron + manual run; capture -> render -> commit
```

## Setup (one time)
1. **Add your OpenRouter key as a secret:** repo Settings -> Secrets and variables -> Actions -> New repository secret, name `OPENROUTER_API_KEY`. It is never committed or exposed to the browser.
2. **Edit `data/questions.csv`** with your real questions (one per row).
3. **Edit `data/models.txt`** with the model ids to capture (one per line; cost scales with models x questions).
4. **Run it:** Actions tab -> "Waiback Capture & Build" -> Run workflow. Or wait for the daily run.

## Cost control & resuming
`data/responses.json` is the ledger: it holds one record per (model, question) already captured. `build/generate.py` loads it and **skips any pair already there**, so nothing is ever re-polled. Batch the work with the manual-run inputs:
- `max_models` (default 15): capture that many not-yet-finished models this run, then stop. Do 15 now, re-run later for the balance; the finished models are skipped automatically.
- `max_calls` (0 = none): a hard ceiling on API calls as a cost backstop.

It checkpoints `responses.json` after every model, so an interrupted run loses nothing, and prints a progress line (models fully captured / remaining).


## Deploy
GitHub Pages, custom domain `waiback.org` (CNAME). Point the apex A records at GitHub Pages IPs (185.199.108-111.153) at your DNS and enable Enforce HTTPS.
