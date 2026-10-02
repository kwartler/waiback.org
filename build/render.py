#!/usr/bin/env python3
"""
Render the waiback.org static site from data/.
Inputs : data/questions.csv, data/models.json, data/responses.json
Outputs: index.html, models/<slug>.html, questions/index.html, questions/<id>.html,
         sitemap.xml, llms.txt, llms-full.txt
No third-party dependencies (stdlib only). Paths are relative so the site works
at a custom domain or a /repo/ preview path.
"""
import csv, json, html, os
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://waiback.org"
BUILT = datetime.now(timezone.utc).strftime("%Y-%m-%d")

def esc(s): return html.escape(str(s if s is not None else ""))

def load():
    with open(os.path.join(ROOT, "data/questions.csv"), newline="") as f:
        questions = [r for r in csv.DictReader(f) if r.get("id") and r.get("question")]
    models = json.load(open(os.path.join(ROOT, "data/models.json")))
    try:
        responses = json.load(open(os.path.join(ROOT, "data/responses.json")))
    except Exception:
        responses = []
    return questions, models, responses

def page(title, desc, body, base="", canonical="/", extra_head=""):
    person = {
        "@context": "https://schema.org", "@type": "WebSite",
        "name": "Waiback", "url": SITE + "/",
        "description": "A wayback machine for large language models: fixed questions, dated answers, preserved over time.",
        "author": {"@type": "Person", "name": "Ted Kwartler", "url": "https://tedkwartler.com/"}
    }
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{SITE}{canonical}">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta name="theme-color" content="#f4efe2">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{SITE}{canonical}">
<meta property="og:image" content="{SITE}/assets/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE}/assets/og-image.png">
<link rel="icon" href="{base}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{base}assets/style.css">
<script type="application/ld+json">{json.dumps(person)}</script>
{extra_head}
</head>
<body>
<header class="site"><div class="wrap masthead">
  <a class="logo" href="{base}index.html">
    <svg class="mark" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="21" fill="none" stroke="#9a6a34" stroke-width="2.5"/><circle cx="24" cy="24" r="3.3" fill="#7a2e2e"/><g stroke="#7c5323" stroke-width="2" stroke-linecap="round"><path d="M24 24 L24 9"/><path d="M24 24 L35 31"/></g><g stroke="#cabfa6" stroke-width="1.4"><circle cx="24" cy="24" r="14" fill="none"/></g></svg>
    <span><b>Waiback</b><span class="sub">the LLM wayback machine</span></span>
  </a>
  <nav class="topnav"><a href="{base}index.html">Archive</a><a href="{base}questions/index.html">Questions</a><a href="https://tedkwartler.com/">Ted Kwartler</a></nav>
</div></header>
<main>
{body}
</main>
<footer class="site"><div class="wrap">
  <span>Waiback &middot; a project by <a href="https://tedkwartler.com/">Ted Kwartler</a></span>
  <span>Model data via <a href="https://openrouter.ai/">OpenRouter</a> &middot; built {BUILT}</span>
</div></footer>
</body>
</html>
"""

def fmt_price(v):
    if v is None: return "n/a"
    return f"${v:g}/1M"

def render():
    questions, models, responses = load()
    # index responses by (model,q)
    rmap = {}
    for r in responses:
        rmap[(r["model_id"], r["question_id"])] = r
    qby = {q["id"]: q for q in questions}
    models_sorted = sorted(models, key=lambda m: (m.get("release_date") or ""), reverse=True)

    os.makedirs(os.path.join(ROOT, "models"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "questions"), exist_ok=True)
    urls = ["/"]

    # ---------- INDEX ----------
    cards = []
    for m in models_sorted:
        answered = sum(1 for q in questions if (m["id"], q["id"]) in rmap)
        caps = [rmap[(m["id"], q["id"])]["response_date"] for q in questions if (m["id"], q["id"]) in rmap]
        captured = max(caps) if caps else None
        cards.append(f"""<article class="snap" data-name="{esc(m['name'].lower())} {esc(m['id'])}">
  <div class="prov">{esc(m['provider'])}</div>
  <a class="title" href="models/{esc(m['slug'])}.html"><h3>{esc(m['name'])}</h3></a>
  <div class="dates">
    <div><span class="k">released</span> {esc(m.get('release_date') or 'unknown')}</div>
    <div><span class="k">captured</span> {esc(captured or 'not yet')}</div>
  </div>
  <div class="foot">{answered} / {len(questions)} answers &middot; <span class="mono">{esc(m['id'])}</span></div>
</article>""")
    intro = f"""<section class="hero"><div class="wrap">
  <h1>A wayback machine for language models.</h1>
  <p>Waiback asks every model the same fixed set of questions and preserves its answers, dated. Each model gets a snapshot page showing when it was released and when we captured how it answers, so you can see how machine knowledge shifts as models are added and updated.</p>
  <p class="meta">{len(models)} models &middot; {len(questions)} questions &middot; a project by <a href="https://tedkwartler.com/">Ted Kwartler</a></p>
</div></section>
<section><div class="wrap">
  <div class="section-head"><h2>The archive</h2><span class="count">{len(models)} snapshots, newest first</span></div>
  <input class="search" id="q" placeholder="filter models by name or provider..." oninput="(function(v){{document.querySelectorAll('.snap').forEach(function(c){{c.style.display=c.dataset.name.indexOf(v.toLowerCase())>=0?'':'none'}})}})(this.value)">
  <div class="snaps">{''.join(cards)}</div>
</div></section>"""
    open(os.path.join(ROOT, "index.html"), "w").write(
        page("Waiback: the LLM wayback machine", "Fixed questions, dated answers, preserved over time. A wayback machine for large language models by Ted Kwartler.", intro, base="", canonical="/"))

    # ---------- MODEL PAGES ----------
    for m in models_sorted:
        chips = [f'<span class="chip"><b>released</b> {esc(m.get("release_date") or "?")}</span>']
        if m.get("knowledge_cutoff"): chips.append(f'<span class="chip"><b>knowledge cutoff</b> {esc(m["knowledge_cutoff"])}</span>')
        if m.get("context_length"): chips.append(f'<span class="chip"><b>context</b> {int(m["context_length"]):,}</span>')
        chips.append(f'<span class="chip"><b>price</b> {fmt_price(m.get("input_per_1m"))} in / {fmt_price(m.get("output_per_1m"))} out</span>')
        caps = [rmap[(m["id"], q["id"])]["response_date"] for q in questions if (m["id"], q["id"]) in rmap]
        captured = max(caps) if caps else None
        entries = []
        for q in questions:
            r = rmap.get((m["id"], q["id"]))
            cat = f'<span class="cat">{esc(q.get("category"))}</span>' if q.get("category") else ""
            if r:
                tag = '<span class="sample-tag">sample</span>' if r.get("sample") else ""
                entries.append(f"""<div class="entry">
  <p class="q"><a href="../questions/{esc(q['id'])}.html" style="text-decoration:none;color:inherit">{esc(q['question'])}</a>{cat}{tag}</p>
  <div class="a">{esc(r['answer'])}</div>
  <div class="when">captured {esc(r['response_date'])}</div>
</div>""")
            else:
                entries.append(f"""<div class="entry">
  <p class="q">{esc(q['question'])}{cat}</p>
  <div class="a empty">not yet captured</div>
</div>""")
        stamp = f'<div class="stamp">captured {esc(captured)}</div>' if captured else '<div class="stamp">not yet captured</div>'
        body = f"""<div class="wrap">
  <div class="model-head">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap">
      <div>
        <div class="prov">{esc(m['provider'])}</div>
        <h1>{esc(m['name'])}</h1>
        <div class="id">{esc(m['id'])}</div>
      </div>
      {stamp}
    </div>
    <div class="metabar">{''.join(chips)}</div>
    {('<p class="desc">'+esc(m['description'][:420])+('...' if len(m.get('description',''))>420 else '')+'</p>') if m.get('description') else ''}
  </div>
  <div class="qa"><h2 style="font-size:1.3rem;margin-bottom:4px">Answer sheet</h2>{''.join(entries)}</div>
  <p class="crumb"><a href="../index.html">&larr; back to the archive</a></p>
</div>"""
        open(os.path.join(ROOT, "models", m["slug"] + ".html"), "w").write(
            page(f"{m['name']} on Waiback", f"How {m['name']} (released {m.get('release_date','?')}) answers {len(questions)} fixed questions, captured {captured or 'soon'}. A Waiback snapshot by Ted Kwartler.",
                 body, base="../", canonical=f"/models/{m['slug']}.html"))
        urls.append(f"/models/{m['slug']}.html")

    # ---------- QUESTION PAGES ----------
    qrows = []
    for i, q in enumerate(questions, 1):
        ans = []
        for m in models_sorted:
            r = rmap.get((m["id"], q["id"]))
            if not r: continue
            tag = '<span class="sample-tag">sample</span>' if r.get("sample") else ""
            ans.append(f"""<div class="ans">
  <div class="who"><a href="../models/{esc(m['slug'])}.html">{esc(m['name'])}</a>{tag}<span class="d">released {esc(m.get('release_date') or '?')} &middot; captured {esc(r['response_date'])}</span></div>
  <div class="body">{esc(r['answer'])}</div>
</div>""")
        cat = f'<span class="cat">{esc(q.get("category"))}</span>' if q.get("category") else ""
        body = f"""<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)">
    <div class="prov mono" style="color:var(--sepia);font-size:12px;text-transform:uppercase;letter-spacing:.12em">question {esc(q['id'])}</div>
    <h1 style="margin-top:6px">{esc(q['question'])} {cat}</h1>
    <p class="meta">How {len(ans)} models answer, each captured on a date.</p>
  </section>
  <section><div class="ans-list">{''.join(ans) if ans else '<p class="muted">No answers captured yet.</p>'}</div></section>
  <p class="crumb"><a href="index.html">&larr; all questions</a> &middot; <a href="../index.html">the archive</a></p>
</div>"""
        open(os.path.join(ROOT, "questions", q["id"] + ".html"), "w").write(
            page(f"How language models answer: {q['question'][:60]}", f"A Waiback comparison: how {len(ans)} language models answer the question, with release and capture dates. By Ted Kwartler.",
                 body, base="../", canonical=f"/questions/{q['id']}.html"))
        urls.append(f"/questions/{q['id']}.html")
        qrows.append(f'<a href="{esc(q["id"])}.html"><span class="num">{i:02d}</span><span>{esc(q["question"])}</span></a>')

    # questions index
    qbody = f"""<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)"><h1>The question set</h1>
  <p>Every model in the archive is asked these {len(questions)} questions. Pick one to see how the models compare.</p></section>
  <section><div class="qlist">{''.join(qrows)}</div></section>
  <p class="crumb"><a href="../index.html">&larr; the archive</a></p>
</div>"""
    open(os.path.join(ROOT, "questions", "index.html"), "w").write(
        page("The question set | Waiback", "The fixed set of questions every model answers on Waiback.", qbody, base="../", canonical="/questions/index.html"))
    urls.append("/questions/index.html")

    # ---------- sitemap + llms ----------
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        sm.append(f"  <url><loc>{SITE}{u}</loc><lastmod>{BUILT}</lastmod></url>")
    sm.append("</urlset>")
    open(os.path.join(ROOT, "sitemap.xml"), "w").write("\n".join(sm))

    llms = [f"# Waiback",
            "",
            "> Waiback is a wayback machine for large language models by Ted Kwartler. Every model is asked the same fixed set of questions and its answers are preserved with a date, so you can see how models answer and how that changes as they are added and updated.",
            "",
            f"Models: {len(models)} | Questions: {len(questions)} | Updated: {BUILT}",
            "", "## Models (newest first)"]
    for m in models_sorted:
        llms.append(f"- [{m['name']}]({SITE}/models/{m['slug']}.html): released {m.get('release_date','?')}, provider {m['provider']}.")
    llms += ["", "## Related", f"- By Ted Kwartler: https://tedkwartler.com/"]
    open(os.path.join(ROOT, "llms.txt"), "w").write("\n".join(llms) + "\n")
    open(os.path.join(ROOT, "llms-full.txt"), "w").write("\n".join(llms) + "\n")

    print(f"rendered: 1 index + {len(models_sorted)} model pages + {len(questions)} question pages + questions index; sitemap {len(urls)} urls")

if __name__ == "__main__":
    render()
