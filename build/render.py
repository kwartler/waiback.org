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
from collections import defaultdict, Counter
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://waiback.org"
REPO = "https://github.com/kwartler/waiback.org"
ISSUE_NEW = REPO + "/issues/new?template=question-suggestion.yml"
BUILT = datetime.now(timezone.utc).strftime("%Y-%m-%d")

def esc(s): return html.escape(str(s if s is not None else ""))

def fmt_usd(c):
    if c is None: return "n/a"
    if c == 0: return "$0"
    if c < 0.01: return "$" + ("%.6f" % c).rstrip("0").rstrip(".")
    return "$%.4f" % c

def resp_meta(r):
    bits = ["captured " + esc(r.get("response_date"))]
    tt, pt, ct = r.get("total_tokens"), r.get("prompt_tokens"), r.get("completion_tokens")
    if tt:
        s = f"{tt:,} tokens"
        if pt is not None and ct is not None: s += f" ({pt:,} in / {ct:,} out)"
        bits.append(s)
    if r.get("cost_usd") is not None:
        bits.append(fmt_usd(r["cost_usd"]))
    mt = r.get("max_tokens")
    if mt:
        re_ = r.get("reasoning_effort")
        bits.append(f"max {int(mt):,} tok" + (f", reasoning {esc(re_)}" if re_ else ""))
    return " &middot; ".join(bits)

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
  <nav class="topnav"><a href="{base}index.html">Archive</a><a href="{base}calendar.html">Calendar</a><a href="{base}publishers/index.html">Publishers</a><a href="{base}questions/index.html">Questions</a><a href="{base}data.html">Data</a><a href="{base}contribute.html">Contribute</a><a href="https://tedkwartler.com/">Ted Kwartler</a></nav>
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

CAL_TEMPLATE = """<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)">
    <h1>Calendar</h1>
    <p>Browse the archive by date. Toggle between the day each model was <b>collected</b> (its snapshot date) and the day it was <b>released</b>, and filter to a publisher. Click a shaded day to see the models and open their responses.</p>
    <div class="cal-controls">
      <div class="cal-toggle"><button id="btnC" class="active">Collected</button><button id="btnR">Released</button></div>
      <label class="pub-label">Publisher <select id="pubSel" class="pub-sel"><option value="all">All publishers</option></select></label>
    </div>
  </section>
  <section><div id="cal"></div><div id="detail" class="cal-detail"></div></section>
  <p class="crumb"><a href="index.html">the archive</a> &middot; <a href="publishers/index.html">publishers</a></p>
</div>
<script>
var CAL=__CAL_DATA__, mode='collected', pub='all', VIEW={};
var MON=['January','February','March','April','May','June','July','August','September','October','November','December'];
var PUBS={}; ['collected','released'].forEach(function(mm){var dd=CAL[mm]||{}; for(var d in dd){for(var i=0;i<dd[d].length;i++){PUBS[dd[d][i].provider]=dd[d][i].pub;}}});
(function(){var sel=document.getElementById('pubSel'); var ks=Object.keys(PUBS).sort(function(a,b){return PUBS[a].toLowerCase()<PUBS[b].toLowerCase()?-1:1;}); for(var i=0;i<ks.length;i++){var o=document.createElement('option'); o.value=ks[i]; o.textContent=PUBS[ks[i]]; sel.appendChild(o);} sel.addEventListener('change',function(){pub=this.value; render();});})();
function bucket(n){return n>=8?'l4':n>=4?'l3':n>=2?'l2':'l1';}
function computeView(){var raw=CAL[mode]||{}; VIEW={}; for(var d in raw){var arr=pub==='all'?raw[d]:raw[d].filter(function(m){return m.provider===pub;}); if(arr.length) VIEW[d]=arr;}}
function pick(ds){
  var cur=document.querySelector('.cell.sel'); if(cur) cur.classList.remove('sel');
  var c=document.querySelector('.cell[data-d="'+ds+'"]'); if(c) c.classList.add('sel');
  var arr=VIEW[ds]||[], lab=mode==='collected'?'collected':'released', who=pub==='all'?'':(' from '+PUBS[pub]);
  var h='<h3>'+arr.length+' model'+(arr.length!==1?'s':'')+who+' '+lab+' on '+ds+'</h3><div class="snaps">';
  for(var i=0;i<arr.length;i++){var m=arr[i];
    h+='<article class="snap"><a class="prov" href="publishers/'+m.provider+'.html">'+m.pub+'</a><a class="title" href="models/'+m.slug+'.html"><h3>'+m.name+'</h3></a></article>';}
  document.getElementById('detail').innerHTML=h+'</div>';
}
function render(){
  computeView();
  var dates=Object.keys(VIEW).sort(), cal=document.getElementById('cal');
  if(!dates.length){cal.innerHTML='<p class="muted">Nothing '+mode+(pub==='all'?'':' for '+PUBS[pub])+' yet.</p>';document.getElementById('detail').innerHTML='';return;}
  var yms={}; for(var i=0;i<dates.length;i++) yms[dates[i].slice(0,7)]=1;
  var keys=Object.keys(yms).sort(), html='<div class="months">';
  for(var k=0;k<keys.length;k++){
    var y=+keys[k].slice(0,4), mo=+keys[k].slice(5,7);
    html+='<div class="month"><h4>'+MON[mo-1]+' '+y+'</h4><div class="grid7">';
    html+='<div class="dow">S</div><div class="dow">M</div><div class="dow">T</div><div class="dow">W</div><div class="dow">T</div><div class="dow">F</div><div class="dow">S</div>';
    var fd=new Date(y,mo-1,1).getDay(), dn=new Date(y,mo,0).getDate();
    for(var i=0;i<fd;i++) html+='<div class="cell"></div>';
    for(var d=1;d<=dn;d++){
      var ds=y+'-'+String(mo).padStart(2,'0')+'-'+String(d).padStart(2,'0'), arr=VIEW[ds];
      if(arr&&arr.length) html+='<div class="cell has '+bucket(arr.length)+'" data-d="'+ds+'" title="'+ds+': '+arr.length+'">'+d+'</div>';
      else html+='<div class="cell">'+d+'</div>';
    }
    html+='</div></div>';
  }
  cal.innerHTML=html+'</div>';
  pick(dates[dates.length-1]);
}
document.getElementById('cal').addEventListener('click',function(e){var c=e.target.closest('.cell.has'); if(c) pick(c.getAttribute('data-d'));});
document.getElementById('btnC').addEventListener('click',function(){mode='collected';this.classList.add('active');document.getElementById('btnR').classList.remove('active');render();});
document.getElementById('btnR').addEventListener('click',function(){mode='released';this.classList.add('active');document.getElementById('btnC').classList.remove('active');render();});
render();
</script>"""

def render():
    questions, models, responses = load()
    # capture conditions (shown for transparency)
    sp_path = os.path.join(ROOT, "data/system_prompt.txt")
    system_prompt = open(sp_path).read().strip() if os.path.exists(sp_path) else ""
    temp = next((r["temperature"] for r in responses if r.get("temperature") is not None), 0)
    # current (most recent) completion cap + reasoning setting, for the conditions display
    _latest = sorted((r for r in responses if r.get("max_tokens")),
                     key=lambda r: r.get("response_date") or "", reverse=True)
    cur_max_tokens = _latest[0]["max_tokens"] if _latest else None
    cur_reasoning = _latest[0].get("reasoning_effort") if _latest else None
    # index responses by (model,q)
    rmap = {}
    for r in responses:
        rmap[(r["model_id"], r["question_id"])] = r
    qby = {q["id"]: q for q in questions}
    models_sorted = sorted(models, key=lambda m: (m.get("release_date") or ""), reverse=True)

    # group models by publisher (OpenRouter author slug), with a display label from the name prefix
    pubs = defaultdict(list)
    for m in models_sorted:
        pubs[m["provider"]].append(m)
    pub_name = {}
    for prov, ms in pubs.items():
        labels = [mm["name"].split(":")[0].strip() for mm in ms if ":" in mm["name"]]
        pub_name[prov] = Counter(labels).most_common(1)[0][0] if labels else prov.replace("-", " ").title()

    def snap_card(m, prefix):
        answered = sum(1 for q in questions if (m["id"], q["id"]) in rmap)
        caps = [rmap[(m["id"], q["id"])]["response_date"] for q in questions if (m["id"], q["id"]) in rmap]
        captured = max(caps) if caps else None
        return f"""<article class="snap" data-name="{esc(m['name'].lower())} {esc(m['id'])}">
  <a class="prov" href="{prefix}publishers/{esc(m['provider'])}.html">{esc(pub_name[m['provider']])}</a>
  <a class="title" href="{prefix}models/{esc(m['slug'])}.html"><h3>{esc(m['name'])}</h3></a>
  <div class="dates">
    <div><span class="k">released</span> {esc(m.get('release_date') or 'unknown')}</div>
    <div><span class="k">captured</span> {esc(captured or 'not yet')}</div>
  </div>
  <div class="foot">{answered} / {len(questions)} answers &middot; <span class="mono">{esc(m['id'])}</span></div>
</article>"""

    for d in ("models", "questions", "publishers"):
        dd = os.path.join(ROOT, d)
        os.makedirs(dd, exist_ok=True)
        for fn in os.listdir(dd):  # prune stale pages so renamed/removed ids don't linger
            if fn.endswith(".html"):
                os.remove(os.path.join(dd, fn))
    urls = ["/"]

    # ---------- INDEX ----------
    cards = [snap_card(m, "") for m in models_sorted]
    intro = f"""<section class="hero"><div class="wrap">
  <h1>A wayback machine for language models.</h1>
  <p>Waiback asks every model the same fixed set of questions and preserves its answers, dated. Each model gets a snapshot page showing when it was released and when we captured how it answers, so you can see how machine knowledge shifts as models are added and updated.</p>
  <p class="meta">{len(models)} models &middot; {len(questions)} questions &middot; a project by <a href="https://tedkwartler.com/">Ted Kwartler</a></p>
  <details class="conditions"><summary>Capture conditions</summary>
    <p>Every answer is captured under fixed conditions, the same for every model: <b>temperature {temp:g}</b>, one shared system prompt, a completion cap of <b>{(f"{int(cur_max_tokens):,} max tokens" if cur_max_tokens else "a set token budget")}</b>{(f", and <b>reasoning effort {esc(cur_reasoning)}</b>" if cur_reasoning else "")}. Each answer also records the exact settings used, so earlier snapshots captured under different limits stay self-describing.</p>
    <blockquote class="sysprompt">{esc(system_prompt)}</blockquote>
  </details>
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
        snap = [rmap[(m["id"], q["id"])] for q in questions if (m["id"], q["id"]) in rmap]
        caps = [r["response_date"] for r in snap]
        captured = max(caps) if caps else None
        tot_tok = sum((r.get("total_tokens") or 0) for r in snap)
        tot_cost = sum((r.get("cost_usd") or 0) for r in snap)
        if snap and tot_tok:
            chips.append(f'<span class="chip"><b>this snapshot</b> {tot_tok:,} tokens / {fmt_usd(tot_cost)}</span>')
        cond = next((r for r in snap if r.get("temperature") is not None), None)
        if cond:
            ctext = f'temp {cond["temperature"]:g} / prompt {esc(cond.get("prompt_sha", "?"))}'
            if cond.get("max_tokens"):
                ctext += f' / max {int(cond["max_tokens"]):,} tok'
            if cond.get("reasoning_effort"):
                ctext += f' / reasoning {esc(cond["reasoning_effort"])}'
            chips.append(f'<span class="chip"><b>conditions</b> {ctext}</span>')
        entries = []
        for q in questions:
            r = rmap.get((m["id"], q["id"]))
            cat = f'<span class="cat">{esc(q.get("category"))}</span>' if q.get("category") else ""
            if r:
                tag = '<span class="sample-tag">sample</span>' if r.get("sample") else ""
                entries.append(f"""<div class="entry">
  <p class="q"><a href="../questions/{esc(q['id'])}.html" style="text-decoration:none;color:inherit">{esc(q['question'])}</a>{cat}{tag}</p>
  <div class="a">{esc(r['answer'])}</div>
  <div class="when">{resp_meta(r)}</div>
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
        <a class="prov" href="../publishers/{esc(m['provider'])}.html">{esc(pub_name[m['provider']])}</a>
        <h1>{esc(m['name'])}</h1>
        <div class="id">{esc(m['id'])}</div>
      </div>
      {stamp}
    </div>
    <div class="metabar">{''.join(chips)}</div>
    {('<p class="desc">'+esc(m['description'][:420])+('...' if len(m.get('description',''))>420 else '')+'</p>') if m.get('description') else ''}
  </div>
  <div class="qa"><h2 style="font-size:1.3rem;margin-bottom:4px">Answer sheet</h2>{''.join(entries)}</div>
  <p class="crumb"><a href="../index.html">&larr; back to the archive</a> &middot; <a href="../api/models/{esc(m['slug'])}.json">download this snapshot as JSON</a></p>
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
  <div class="who"><a href="../models/{esc(m['slug'])}.html">{esc(m['name'])}</a>{tag}<span class="d">released {esc(m.get('release_date') or '?')} &middot; {resp_meta(r)}</span></div>
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
  <p class="crumb"><a href="index.html">&larr; all questions</a> &middot; <a href="../index.html">the archive</a> &middot; <a href="../api/questions/{esc(q['id'])}.json">download as JSON</a></p>
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

    # ---------- PUBLISHER PAGES ----------
    pub_cards = []
    for prov in sorted(pubs, key=lambda p: (-len(pubs[p]), p)):
        ms = sorted(pubs[prov], key=lambda x: (x.get("release_date") or ""), reverse=True)
        disp = pub_name[prov]
        rels = [m.get("release_date") for m in ms if m.get("release_date")]
        answered = 0; tot_tok = 0; tot_cost = 0.0; caps = []
        for m in ms:
            for q in questions:
                r = rmap.get((m["id"], q["id"]))
                if r:
                    answered += 1
                    tot_tok += (r.get("total_tokens") or 0)
                    tot_cost += (r.get("cost_usd") or 0)
                    caps.append(r["response_date"])
        rel_range = (min(rels) + " to " + max(rels)) if rels else "unknown"
        chips = [f'<span class="chip"><b>models</b> {len(ms)}</span>',
                 f'<span class="chip"><b>released</b> {esc(rel_range)}</span>']
        if answered: chips.append(f'<span class="chip"><b>answers captured</b> {answered}</span>')
        if tot_tok: chips.append(f'<span class="chip"><b>captured</b> {tot_tok:,} tokens / {fmt_usd(tot_cost)}</span>')
        cards_p = "".join(snap_card(m, "../") for m in ms)
        body = f"""<div class="wrap">
  <div class="model-head">
    <div class="prov">publisher</div>
    <h1>{esc(disp)}</h1>
    <div class="id">{esc(prov)} &middot; {len(ms)} model{'s' if len(ms)!=1 else ''} in the archive</div>
    <div class="metabar">{''.join(chips)}</div>
  </div>
  <section><div class="section-head"><h2>Models</h2><span class="count">newest first</span></div>
  <div class="snaps">{cards_p}</div></section>
  <p class="crumb"><a href="index.html">&larr; all publishers</a> &middot; <a href="../index.html">the archive</a></p>
</div>"""
        open(os.path.join(ROOT, "publishers", prov + ".html"), "w").write(
            page(f"{disp} models on Waiback", f"Every {disp} model in the Waiback archive ({len(ms)} models), with release dates and dated answer snapshots. By Ted Kwartler.",
                 body, base="../", canonical=f"/publishers/{prov}.html"))
        urls.append(f"/publishers/{prov}.html")
        pub_cards.append(f"""<article class="snap" data-name="{esc(disp.lower())} {esc(prov)}">
  <a class="title" href="{esc(prov)}.html"><h3>{esc(disp)}</h3></a>
  <div class="dates"><div><span class="k">models</span> {len(ms)}</div><div><span class="k">released</span> {esc(rel_range)}</div></div>
  <div class="foot">{answered} answers captured &middot; <span class="mono">{esc(prov)}</span></div>
</article>""")

    pbody = f"""<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)"><h1>Publishers</h1>
  <p>The labs behind the models. Pick one to see all of its models and their answer snapshots.</p></section>
  <section><input class="search" id="q" placeholder="filter publishers..." oninput="(function(v){{document.querySelectorAll('.snap').forEach(function(c){{c.style.display=c.dataset.name.indexOf(v.toLowerCase())>=0?'':'none'}})}})(this.value)">
  <div class="snaps">{''.join(pub_cards)}</div></section>
  <p class="crumb"><a href="../index.html">&larr; the archive</a></p>
</div>"""
    open(os.path.join(ROOT, "publishers", "index.html"), "w").write(
        page("Publishers | Waiback", "The model publishers (labs) in the Waiback archive, each with all of its models and dated answer snapshots.", pbody, base="../", canonical="/publishers/index.html"))
    urls.append("/publishers/index.html")

    # ---------- CALENDAR ----------
    mbyid = {m["id"]: m for m in models}
    def cal_entry(m): return {"name": m["name"], "slug": m["slug"], "provider": m["provider"], "pub": pub_name[m["provider"]]}
    collected = {}
    seen = set()
    for r in responses:
        kk = (r["model_id"], r.get("response_date"))
        if kk in seen or not r.get("response_date"): continue
        seen.add(kk)
        m = mbyid.get(r["model_id"])
        if m: collected.setdefault(r["response_date"], []).append(cal_entry(m))
    released = {}
    for m in models_sorted:
        d = m.get("release_date")
        if d: released.setdefault(d, []).append(cal_entry(m))
    cal_body = CAL_TEMPLATE.replace("__CAL_DATA__", json.dumps({"collected": collected, "released": released}))
    open(os.path.join(ROOT, "calendar.html"), "w").write(
        page("Calendar | Waiback", "Browse the Waiback archive by date: toggle between model collection dates and release dates.",
             cal_body, base="", canonical="/calendar.html"))
    urls.append("/calendar.html")

    # ---------- CONTRIBUTE ----------
    cbody = f"""<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)">
    <h1>Suggest a question</h1>
    <p>Waiback asks every model the same fixed set of questions. If you have a good one, propose it and Ted will review it. Strong questions force a model to make a judgment it cannot dodge, and stay meaningful as models change over the years.</p>
    <p><a class="btn-issue" href="{ISSUE_NEW}">Open a question suggestion &rarr;</a></p>
    <p class="meta">This opens a prefilled issue on GitHub. You will need a free GitHub account. Every suggestion lands in the project's issue tracker for review.</p>
  </section>
  <section><div class="section-head"><h2>What makes a good question</h2></div>
    <ul class="give">
      <li>It demands a decision or a stance, not a summary. The standing system prompt tells models not to placate both sides.</li>
      <li>It is self-contained: no links, no attachments, no context the model would not have.</li>
      <li>It will still make sense in five years, so the archive stays comparable over time.</li>
      <li>It is one question (follow-up clauses are fine), phrased the same way for every model.</li>
    </ul>
    <p class="crumb">Prefer email or want to browse the set first? See <a href="questions/index.html">the current questions</a>, or open an issue directly on <a href="{REPO}/issues">GitHub</a>.</p>
  </section>
</div>"""
    open(os.path.join(ROOT, "contribute.html"), "w").write(
        page("Suggest a question | Waiback", "Propose a question for Waiback to ask every language model. Submissions are reviewed by Ted Kwartler via GitHub issues.",
             cbody, base="", canonical="/contribute.html"))
    urls.append("/contribute.html")

    # ---------- DATA (for researchers) ----------
    year = BUILT[:4]
    bibtex = (f"@misc{{waiback,\n"
              f"  author       = {{Kwartler, Ted}},\n"
              f"  title        = {{Waiback: A Wayback Machine for Language Models}},\n"
              f"  year         = {{{year}}},\n"
              f"  howpublished = {{\\url{{{SITE}}}}},\n"
              f"  note         = {{Dataset. Accessed YYYY-MM-DD}}\n"
              f"}}")
    apa = f"Kwartler, T. ({year}). <i>Waiback: A wayback machine for language models</i> [Data set]. {SITE}"
    dbody = f"""<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)">
    <h1>Data for researchers</h1>
    <p>The entire archive is open. Every model is asked the same fixed questions under fixed conditions (temperature {temp:g}, one shared system prompt, a completion cap of {(f"{int(cur_max_tokens):,} max tokens" if cur_max_tokens else "a set token budget")}{(f", reasoning effort {esc(cur_reasoning)}" if cur_reasoning else "")}), and each answer is stored with the model's <b>release date</b>, the <b>date it was collected</b>, and the exact capture settings used, so you can study how answers shift as models are released and updated over time. Free to use for research and journalism with attribution.</p>
  </section>

  <section><div class="section-head"><h2>How to obtain the data</h2></div>
    <ul class="give">
      <li><b>Programmatic (JSON).</b> Start at <a href="{SITE}/api/index.json"><span class="mono">/api/index.json</span></a>, a catalog of every model and question with links to:
        <br><span class="mono">/api/models/&lt;slug&gt;.json</span> (one model, its full answer sheet) and
        <span class="mono">/api/questions/&lt;id&gt;.json</span> (one question, every model's answer).</li>
      <li><b>Bulk / raw.</b> The whole repository is public: <a href="{REPO}">{REPO.split('//')[1]}</a>. The <span class="mono">data/</span> folder holds <span class="mono">questions.csv</span>, <span class="mono">models.json</span>, <span class="mono">responses.json</span> (every answer), <span class="mono">prompts.json</span> (the capture conditions over time), and the generated <span class="mono">api/</span> files.</li>
      <li><b>Reproducible.</b> The capture and build scripts (<span class="mono">build/generate.py</span>, <span class="mono">build/render.py</span>) are in the same repo, so the full pipeline is inspectable.</li>
    </ul>
  </section>

  <section><div class="section-head"><h2>What each record contains</h2></div>
    <p>Per-answer fields: <span class="mono">question_id</span>, <span class="mono">answer</span>, <span class="mono">response_date</span> (collected), <span class="mono">prompt_tokens</span>, <span class="mono">completion_tokens</span>, <span class="mono">total_tokens</span>, <span class="mono">input_per_1m</span> and <span class="mono">output_per_1m</span> (USD per million tokens, frozen at capture), <span class="mono">cost_usd</span> (computed from those), <span class="mono">or_cost</span> (OpenRouter's reported charge), <span class="mono">temperature</span>, and <span class="mono">prompt_sha</span> (which system prompt was used). Per-model records add <span class="mono">release_date</span>, <span class="mono">knowledge_cutoff</span>, <span class="mono">context_length</span>, pricing, and <span class="mono">description</span>.</p>
  </section>

  <section><div class="section-head"><h2>How to cite</h2><span class="count">dataset slug: <span class="mono">waiback</span></span></div>
    <p>{apa}</p>
    <pre class="cite">{esc(bibtex)}</pre>
    <p class="crumb"><a href="index.html">&larr; the archive</a> &middot; <a href="contribute.html">suggest a question</a></p>
  </section>
</div>"""
    open(os.path.join(ROOT, "data.html"), "w").write(
        page("Data for researchers | Waiback", "Open dataset: how every language model answers a fixed set of questions, with release and collection dates. Download, API, and citation.",
             dbody, base="", canonical="/data.html"))
    urls.append("/data.html")

    # ---------- machine-readable JSON (for researchers) ----------
    # One file per model and per question, plus an index, under /api so they are
    # fetchable at https://waiback.org/api/... These are derived purely from the
    # stored responses: no API calls are needed to (re)generate them.
    def answer_fields(r):
        return {k: r.get(k) for k in (
            "response_date", "answer", "prompt_tokens", "completion_tokens", "total_tokens",
            "input_per_1m", "output_per_1m", "cost_usd", "or_cost", "temperature", "prompt_sha",
            "max_tokens", "reasoning_effort")}
    conditions = {"temperature": temp, "system_prompt": system_prompt,
                  "prompt_sha": next((r.get("prompt_sha") for r in responses if r.get("prompt_sha")), None),
                  "max_tokens": cur_max_tokens, "reasoning_effort": cur_reasoning,
                  "note": "Per-answer temperature, prompt_sha, max_tokens and reasoning_effort record the exact conditions for each capture; earlier snapshots may differ from the current values shown here."}
    api = os.path.join(ROOT, "api")
    for d in ("models", "questions"):
        dd = os.path.join(api, d)
        os.makedirs(dd, exist_ok=True)
        for fn in os.listdir(dd):  # prune stale json so removed ids don't linger
            if fn.endswith(".json"):
                os.remove(os.path.join(dd, fn))
    # per model
    for m in models_sorted:
        snap = [rmap[(m["id"], q["id"])] for q in questions if (m["id"], q["id"]) in rmap]
        caps = [r["response_date"] for r in snap]
        ans = []
        for q in questions:
            r = rmap.get((m["id"], q["id"]))
            if not r: continue
            ans.append({"question_id": q["id"], "question": q["question"],
                        "category": q.get("category") or None, **answer_fields(r)})
        json.dump({"model": m, "captured": (max(caps) if caps else None),
                   "answered": len(ans), "question_count": len(questions),
                   "conditions": conditions, "answers": ans},
                  open(os.path.join(api, "models", m["slug"] + ".json"), "w"), indent=2)
    # per question
    for q in questions:
        ans = []
        for m in models_sorted:
            r = rmap.get((m["id"], q["id"]))
            if not r: continue
            ans.append({"model_id": m["id"], "model_name": m["name"], "provider": m["provider"],
                        "publisher": pub_name[m["provider"]], "release_date": m.get("release_date"),
                        **answer_fields(r)})
        json.dump({"question": {"id": q["id"], "question": q["question"], "category": q.get("category") or None},
                   "answered": len(ans), "conditions": conditions, "answers": ans},
                  open(os.path.join(api, "questions", q["id"] + ".json"), "w"), indent=2)
    # index
    json.dump({"site": SITE, "updated": BUILT, "model_count": len(models), "question_count": len(questions),
               "conditions": conditions,
               "models": [{"id": m["id"], "slug": m["slug"], "name": m["name"], "provider": m["provider"],
                           "release_date": m.get("release_date"),
                           "answered": sum(1 for q in questions if (m["id"], q["id"]) in rmap),
                           "json": f"{SITE}/api/models/{m['slug']}.json"} for m in models_sorted],
               "questions": [{"id": q["id"], "question": q["question"],
                              "json": f"{SITE}/api/questions/{q['id']}.json"} for q in questions]},
              open(os.path.join(api, "index.json"), "w"), indent=2)
    print(f"json api: {len(models_sorted)} model files + {len(questions)} question files + index.json")

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
        llms.append(f"- [{m['name']}]({SITE}/models/{m['slug']}.html): released {m.get('release_date','?')}, publisher {pub_name[m['provider']]}.")
    llms += ["", "## Publishers"]
    for prov in sorted(pubs, key=lambda p: (-len(pubs[p]), p)):
        llms.append(f"- [{pub_name[prov]}]({SITE}/publishers/{prov}.html): {len(pubs[prov])} models.")
    llms += ["", "## Data (machine-readable)",
             f"- Index of all data: {SITE}/api/index.json",
             f"- Per-model answers: {SITE}/api/models/<slug>.json",
             f"- Per-question answers: {SITE}/api/questions/<id>.json"]
    llms += ["", "## Data",
             f"- For researchers (download, API, citation): {SITE}/data.html",
             f"- JSON index: {SITE}/api/index.json",
             "", "## Contribute",
             f"- Suggest a question: {SITE}/contribute.html (opens a GitHub issue reviewed by Ted Kwartler)"]
    llms += ["", "## Related", f"- By Ted Kwartler: https://tedkwartler.com/"]
    open(os.path.join(ROOT, "llms.txt"), "w").write("\n".join(llms) + "\n")
    open(os.path.join(ROOT, "llms-full.txt"), "w").write("\n".join(llms) + "\n")

    print(f"rendered: index + {len(models_sorted)} model pages + {len(pubs)} publisher pages + {len(questions)} question pages; sitemap {len(urls)} urls")

if __name__ == "__main__":
    render()
