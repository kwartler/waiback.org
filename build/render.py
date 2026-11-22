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

# Shared identity block: one source of truth (data/profile.json) for the author's bio,
# verified profile links (sameAs) and the cross-project list. Every page emits the same
# Person entity, and /about.html is built from it. The SAME file + render logic can be
# reused verbatim on the other sites (5charts, hagll, llmba, llmaid) so the entity graph
# stays byte-for-byte consistent across the network (hub-and-spoke through tedkwartler.com).
PERSON_ID = "https://tedkwartler.com/#person"
try:
    PROFILE = json.load(open(os.path.join(ROOT, "data/profile.json")))
except Exception:
    PROFILE = {"name": "Ted Kwartler", "url": "https://tedkwartler.com/",
               "sameAs": ["https://tedkwartler.com/"], "projects": []}

def _org(o, otype="Organization"):
    """A schema.org Organization node from a {name, url} dict."""
    if not o or not o.get("name"): return None
    d = {"@type": otype, "name": o["name"]}
    if o.get("url"): d["url"] = o["url"]
    return d

def person_node():
    """The schema.org Person for the site author, from the shared profile. Named
    employers, schools and boards are emitted as structured worksFor / alumniOf /
    affiliation / memberOf so search and generative engines can resolve the entities."""
    p = {"@type": "Person", "@id": PERSON_ID,
         "name": PROFILE.get("name", "Ted Kwartler"),
         "url": PROFILE.get("url", "https://tedkwartler.com/"),
         "sameAs": [u for u in PROFILE.get("sameAs", []) if u]}
    jt = PROFILE.get("jobTitle") or PROFILE.get("headline")
    if jt and not jt.startswith("TODO"): p["jobTitle"] = jt
    bio = PROFILE.get("bio")
    if bio and not bio.startswith("TODO"): p["description"] = bio
    wf = _org(PROFILE.get("worksFor"))
    if wf: p["worksFor"] = wf
    al = _org(PROFILE.get("alumniOf"), "CollegeOrUniversity")
    if al: p["alumniOf"] = al
    affs = [_org(a) for a in PROFILE.get("affiliation", [])]
    affs = [a for a in affs if a]
    if affs: p["affiliation"] = affs
    mems = [_org(m) for m in PROFILE.get("memberOf", [])]
    mems = [m for m in mems if m]
    if mems: p["memberOf"] = mems
    return p

def esc(s): return html.escape(str(s if s is not None else ""))

def fmt_usd(c):
    if c is None: return "n/a"
    if c == 0: return "$0"
    if c < 0.01: return "$" + ("%.6f" % c).rstrip("0").rstrip(".")
    return "$%.4f" % c

def resp_meta(r):
    if r.get("error"):
        st = r.get("error_status")
        return "last attempted " + esc(r.get("response_date")) + (f" &middot; {esc(st)}" if st else "")
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
    if r.get("retry"):
        bits.append("retried at a larger token cap" if r.get("retry_success") else "retried, still empty")
    return " &middot; ".join(bits)

def truncation(r):
    """'yes'  = provider reported finish_reason=length (answer cut off at the cap);
    'likely' = captured before finish_reason was recorded, but output tokens reached
               the cap, so it was probably cut off (inferred, not reported);
    None otherwise. Display only: the stored answer is never altered."""
    if not r or r.get("error") or r.get("empty"):
        return None
    fr, mt, ct = r.get("finish_reason"), r.get("max_tokens"), r.get("completion_tokens")
    if fr == "length":
        return "yes"
    if fr is None and mt and ct and ct >= mt:
        return "likely"
    return None

def moderated(r):
    """Provider reported finish_reason=content_filter (output blocked or cut by moderation)."""
    return bool(r) and r.get("finish_reason") == "content_filter"

def provider_message(r):
    """The provider's own words about a block, when captured (records from 2026-11-22 on)."""
    if not r:
        return ""
    parts = []
    msg = r.get("refusal") or r.get("error_message")
    if msg:
        parts.append(f'"{msg}"')
    nat = r.get("native_finish_reason")
    if nat:
        parts.append(f"native reason: {nat}")
    if r.get("provider"):
        parts.append(f"served by {r['provider']}")
    side = r.get("moderation_side")
    if side:
        parts.append("the prompt was blocked before any output" if side == "prompt"
                     else "output was generated, then blocked")
    return "; ".join(parts)

def answer_badges(r):
    """Inline badges for a stored answer: content moderation, token-cap cutoff, retries."""
    out = []
    if moderated(r):
        pm = provider_message(r)
        tip = ("The provider reported finish_reason=content_filter: its content moderation blocked "
               "or cut off the response." + (f" Provider: {pm}." if pm else
               " The provider's own message was not recorded for captures before 2026-11-22."))
        out.append(f'<span class="mod-tag" title="{esc(tip)}">blocked by content moderation</span>')
    fa = (r or {}).get("first_attempt")
    if fa and not (r or {}).get("empty"):
        if fa.get("empty"):
            pass  # empty-content retries are already described in the capture line
        elif str(fa.get("finish_reason")) == "content_filter":
            out.append('<span class="cut-tag" title="The first attempt was blocked by content moderation; '
                       'an identical retry was not. The first attempt is kept below.">shown: retry after moderation block</span>')
        else:
            out.append(f'<span class="cut-tag" title="The first attempt was cut off at '
                       f'{int(fa.get("max_tokens") or 0):,} max tokens. This is the retry at '
                       f'{int(r.get("max_tokens") or 0):,}. The cut-off first attempt is kept below.">'
                       f'retried at {int(r.get("max_tokens") or 0):,} after cutoff at '
                       f'{int(fa.get("max_tokens") or 0):,}</span>')
    t = truncation(r)
    mt = r.get("max_tokens") if r else None
    if t == "yes":
        out.append(f'<span class="cut-tag" title="The provider reported finish_reason=length: the answer '
                   f'stopped at the output cap, not where the model chose to end.">answer cut off at '
                   f'{int(mt):,} max token output</span>')
    elif t == "likely":
        out.append(f'<span class="cut-tag likely" title="Captured before finish_reason was recorded. '
                   f'Output tokens reached the cap, so the answer was probably cut off (inferred, not '
                   f'reported by the provider).">likely cut off at {int(mt):,} max token output</span>')
    return "".join(out)

def first_attempt_block(r):
    """Collapsed view of the first attempt when a retry is the shown answer."""
    note = ""
    if moderated(r) and provider_message(r):   # visible on phones, where hover titles don't show
        note = f'<div class="mod-note">Content moderation, per the provider: {esc(provider_message(r))}</div>'
    fa = (r or {}).get("first_attempt")
    if not fa or fa.get("empty") or not fa.get("answer"):
        return note
    why = ("blocked by content moderation" if fa.get("finish_reason") == "content_filter"
           else f"cut off at {int(fa.get('max_tokens') or 0):,} max token output")
    toks = fa.get("completion_tokens")
    return note + (f'<details class="first-attempt"><summary>First attempt ({why}'
            + (f", {toks:,} tokens out" if toks else "") + ')</summary>'
            f'<div class="fa-body">{esc(fa["answer"])}</div></details>')

def answered(r):
    """A record counts as an answer only if it carries non-empty content. A gap
    record (empty=True) or an error record (error=True) is a preserved, explained
    non-capture, not an answer."""
    return bool(r) and not r.get("empty") and not r.get("error")

def is_error(r):
    """An error record: the model could not be reached (rate-limited / rejected /
    delisted), so no answer was captured. Distinct from a 200-but-empty non-response."""
    return bool(r) and bool(r.get("error"))

def unavail_reason(r):
    """Human-readable reason a model could not be reached for an error record."""
    return r.get("error_reason") or "the model could not be reached"

def gap_reason(r):
    """Human-readable reason an answer is missing for a stored non-capture record."""
    if is_error(r):
        return unavail_reason(r)
    if moderated(r):
        pm = provider_message(r)
        return ("the provider's content moderation blocked the response (finish_reason=content_filter)"
                + (f". Provider: {pm}" if pm else ""))
    if r.get("retry_reason"):
        return r["retry_reason"] + (" and still returned no content" if not r.get("retry_success") else "")
    mt = r.get("max_tokens")
    return "the model returned HTTP 200 with empty content" + (f" at max_tokens {int(mt):,}" if mt else "")

def load():
    with open(os.path.join(ROOT, "data/questions.csv"), newline="") as f:
        questions = [r for r in csv.DictReader(f) if r.get("id") and r.get("question")]
    models = json.load(open(os.path.join(ROOT, "data/models.json")))
    try:
        responses = json.load(open(os.path.join(ROOT, "data/responses.json")))
    except Exception:
        responses = []
    # Safety net: an archive never hides captured answers. Any model with saved
    # responses but no metadata gets a minimal entry so its snapshot page renders.
    known = {m["id"] for m in models}
    for mid in sorted({r["model_id"] for r in responses} - known):
        models.append({"id": mid, "slug": mid.replace("/", "_"), "name": mid,
                       "provider": mid.split("/")[0], "release_date": "",
                       "delisted": True, "delisted_since": ""})
    return questions, models, responses

def page(title, desc, body, base="", canonical="/", extra_head=""):
    # Site-wide JSON-LD graph: the Waiback WebSite plus the author Person (with sameAs),
    # so every page corroborates the same entity. The WebSite points at the Person by @id.
    person = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "@id": SITE + "/#website", "name": "Waiback", "url": SITE + "/",
             "description": "A wayback machine for large language models: fixed questions, dated answers, preserved over time.",
             "author": {"@id": PERSON_ID}, "creator": {"@id": PERSON_ID}},
            person_node(),
        ],
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
  <nav class="topnav"><a href="{base}index.html">Archive</a><a href="{base}calendar.html">Calendar</a><a href="{base}publishers/index.html">Publishers</a><a href="{base}questions/index.html">Questions</a><a href="{base}data.html">Data</a><a href="{base}gaps.html">Gaps</a><a href="{base}contribute.html">Contribute</a><a href="{base}about.html">Ted Kwartler</a></nav>
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
    # the larger cap a 200-but-empty answer is retried at, derived from the data itself
    _retry_caps = [r.get("max_tokens") for r in responses if r.get("retry") and r.get("max_tokens")]
    retry_max_tokens = max(_retry_caps) if _retry_caps else None
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
        n_ans = sum(1 for q in questions if answered(rmap.get((m["id"], q["id"]))))
        caps = [rmap[(m["id"], q["id"])]["response_date"] for q in questions
                if (m["id"], q["id"]) in rmap and not is_error(rmap[(m["id"], q["id"])])]
        captured = max(caps) if caps else None
        return f"""<article class="snap" data-name="{esc(m['name'].lower())} {esc(m['id'])}">
  <a class="prov" href="{prefix}publishers/{esc(m['provider'])}.html">{esc(pub_name[m['provider']])}</a>
  <a class="title" href="{prefix}models/{esc(m['slug'])}.html"><h3>{esc(m['name'])}</h3></a>
  <div class="dates">
    <div><span class="k">released</span> {esc(m.get('release_date') or 'unknown')}</div>
    <div><span class="k">captured</span> {esc(captured or 'not yet')}</div>
  </div>
  <div class="foot">{n_ans} / {len(questions)} answers{' &middot; <span class="gap-tag">no longer served</span>' if m.get('delisted') else ''} &middot; <span class="mono">{esc(m['id'])}</span></div>
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
    # headline ("glamor") stats for the hero, derived straight from the data
    n_answers = sum(1 for r in responses if answered(r))
    n_tokens = sum((r.get("total_tokens") or 0) for r in responses)
    _dts = [r.get("response_date") for r in responses if r.get("response_date")]
    since = min(_dts) if _dts else None
    def _h(n):
        n = int(n)
        for u, d in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
            if n >= d:
                return (f"{n/d:.1f}".rstrip("0").rstrip(".")) + u
        return f"{n:,}"
    _tiles = [(f"{len(models):,}", "Models"), (f"{len(questions)}", "Questions"),
              (f"{n_answers:,}", "Answers"), (f"{len(pubs)}", "Publishers"),
              (_h(n_tokens), "Tokens analyzed")]
    stats_html = '<div class="stats">' + "".join(
        f'<div class="stat"><div class="n">{esc(v)}</div><div class="l">{esc(l)}</div></div>'
        for v, l in _tiles) + '</div>'
    intro = f"""<section class="hero"><div class="wrap">
  <h1>A wayback machine for language models.</h1>
  <p>Waiback asks every model the same fixed set of questions and preserves its answers, dated. Each model gets a snapshot page showing when it was released and when we captured how it answers, so you can see how machine knowledge shifts as models are added and updated.</p>
  {stats_html}
  <p class="meta">Capturing since {esc(since or '?')}, updated {BUILT} &middot; a project by <a href="https://tedkwartler.com/">Ted Kwartler</a></p>
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
        if m.get("delisted"):
            chips.append('<span class="chip"><b>status</b> no longer served on OpenRouter'
                         + (f' (since {esc(m["delisted_since"])})' if m.get("delisted_since") else '') + '</span>')
        if m.get("knowledge_cutoff"): chips.append(f'<span class="chip"><b>knowledge cutoff</b> {esc(m["knowledge_cutoff"])}</span>')
        if m.get("context_length"): chips.append(f'<span class="chip"><b>context</b> {int(m["context_length"]):,}</span>')
        snap = [rmap[(m["id"], q["id"])] for q in questions if (m["id"], q["id"]) in rmap]
        # price is frozen at the moment each answer was captured, read from the stored
        # record (never the live catalog), so it never changes if OpenRouter re-prices later.
        _priced = next((r for r in snap if r.get("input_per_1m") is not None and not is_error(r)),
                       next((r for r in snap if r.get("input_per_1m") is not None), None))
        _pin = _priced.get("input_per_1m") if _priced else m.get("input_per_1m")
        _pout = _priced.get("output_per_1m") if _priced else m.get("output_per_1m")
        _ptag = " at capture" if _priced else ""
        chips.append(f'<span class="chip"><b>price{_ptag}</b> {fmt_price(_pin)} in / {fmt_price(_pout)} out</span>')
        caps = [r["response_date"] for r in snap if not is_error(r)]  # real captures only
        captured = max(caps) if caps else None
        tot_tok = sum((r.get("total_tokens") or 0) for r in snap)
        tot_cost = sum((r.get("cost_usd") or 0) for r in snap)
        n_ans = sum(1 for r in snap if answered(r))
        n_gap = sum(1 for r in snap if r.get("empty"))
        n_err = sum(1 for r in snap if is_error(r))
        n_cut = sum(1 for r in snap if truncation(r))
        n_mod = sum(1 for r in snap if moderated(r))
        chips.append(f'<span class="chip"><b>answers</b> {n_ans} / {len(questions)}'
                     + (f' &middot; {n_cut} cut off' if n_cut else '')
                     + (f' &middot; {n_mod} moderated' if n_mod else '')
                     + (f' &middot; {n_gap} no-answer' if n_gap else '')
                     + (f' &middot; {n_err} unavailable' if n_err else '') + '</span>')
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
            if answered(r):
                tag = ('<span class="sample-tag">sample</span>' if r.get("sample") else "") + answer_badges(r)
                entries.append(f"""<div class="entry">
  <p class="q"><a href="../questions/{esc(q['id'])}.html" style="text-decoration:none;color:inherit">{esc(q['question'])}</a>{cat}{tag}</p>
  <div class="a">{esc(r['answer'])}</div>
  {first_attempt_block(r)}
  <div class="when">{resp_meta(r)}</div>
</div>""")
            elif r is not None and r.get("empty"):
                entries.append(f"""<div class="entry">
  <p class="q"><a href="../questions/{esc(q['id'])}.html" style="text-decoration:none;color:inherit">{esc(q['question'])}</a>{cat}<span class="gap-tag">no answer</span>{answer_badges(r)}</p>
  <div class="a gap">No answer captured. The model was reached but {esc(gap_reason(r))}. <a href="../gaps.html">Why this happens &rarr;</a></div>
  <div class="when">{resp_meta(r)}</div>
</div>""")
            elif is_error(r):
                entries.append(f"""<div class="entry">
  <p class="q"><a href="../questions/{esc(q['id'])}.html" style="text-decoration:none;color:inherit">{esc(q['question'])}</a>{cat}<span class="gap-tag">unavailable</span></p>
  <div class="a gap">Not captured: {esc(unavail_reason(r))}. We retry on later runs, so it may fill in. <a href="../gaps.html">More on gaps &rarr;</a></div>
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
    {'<p class="pricenote">This model is no longer served, so it will not be captured again. The answers below are preserved exactly as captured.</p>' if m.get('delisted') else ''}
    {'<p class="pricenote">Price and cost shown are a snapshot at the moment of capture and are not updated if the model is re-priced later.</p>' if _priced else ''}
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
        ans = []; gaps = []; unavail = []
        for m in models_sorted:
            r = rmap.get((m["id"], q["id"]))
            if answered(r):
                tag = ('<span class="sample-tag">sample</span>' if r.get("sample") else "") + answer_badges(r)
                ans.append(f"""<div class="ans">
  <div class="who"><a href="../models/{esc(m['slug'])}.html">{esc(m['name'])}</a>{tag}<span class="d">released {esc(m.get('release_date') or '?')} &middot; {resp_meta(r)}</span></div>
  <div class="body">{esc(r['answer'])}</div>
  {first_attempt_block(r)}
</div>""")
            elif r is not None and r.get("empty"):
                gaps.append(f'<div class="gap-row"><a href="../models/{esc(m["slug"])}.html">{esc(m["name"])}</a> <span class="d">{esc(gap_reason(r))}</span></div>')
            elif is_error(r):
                unavail.append(f'<div class="gap-row"><a href="../models/{esc(m["slug"])}.html">{esc(m["name"])}</a> <span class="d">{esc(unavail_reason(r))}</span></div>')
        cat = f'<span class="cat">{esc(q.get("category"))}</span>' if q.get("category") else ""
        gap_block = (f'<details class="gaps-list"><summary>{len(gaps)} model{"s" if len(gaps)!=1 else ""} returned no answer to this question</summary>'
                     f'<p class="muted">Reached successfully, but produced empty content even after the token cap was raised. See <a href="../gaps.html">non-responses</a>.</p>'
                     f'{"".join(gaps)}</details>') if gaps else ""
        unavail_block = (f'<details class="gaps-list"><summary>{len(unavail)} model{"s" if len(unavail)!=1 else ""} could not be reached for this question</summary>'
                         f'<p class="muted">The provider rate-limited, rejected, or no longer serves these models, so no answer was captured. We retry on later runs. See <a href="../gaps.html">gaps</a>.</p>'
                         f'{"".join(unavail)}</details>') if unavail else ""
        body = f"""<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)">
    <div class="prov mono" style="color:var(--sepia);font-size:12px;text-transform:uppercase;letter-spacing:.12em">question {esc(q['id'])}</div>
    <h1 style="margin-top:6px">{esc(q['question'])} {cat}</h1>
    <p class="meta">How {len(ans)} models answer, each captured on a date.{(f" {len(gaps)} reached but returned no answer." if gaps else "")}{(f" {len(unavail)} could not be reached." if unavail else "")}</p>
  </section>
  <section><div class="ans-list">{''.join(ans) if ans else '<p class="muted">No answers captured yet.</p>'}</div>{gap_block}{unavail_block}</section>
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
        n_answered = 0; tot_tok = 0; tot_cost = 0.0; caps = []
        for m in ms:
            for q in questions:
                r = rmap.get((m["id"], q["id"]))
                if answered(r):
                    n_answered += 1
                    tot_tok += (r.get("total_tokens") or 0)
                    tot_cost += (r.get("cost_usd") or 0)
                if r is not None:
                    caps.append(r["response_date"])
        rel_range = (min(rels) + " to " + max(rels)) if rels else "unknown"
        chips = [f'<span class="chip"><b>models</b> {len(ms)}</span>',
                 f'<span class="chip"><b>released</b> {esc(rel_range)}</span>']
        if n_answered: chips.append(f'<span class="chip"><b>answers captured</b> {n_answered}</span>')
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
  <div class="foot">{n_answered} answers captured &middot; <span class="mono">{esc(prov)}</span></div>
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
    <p>Per-answer fields: <span class="mono">question_id</span>, <span class="mono">answer</span>, <span class="mono">response_date</span> (collected), <span class="mono">prompt_tokens</span>, <span class="mono">completion_tokens</span>, <span class="mono">total_tokens</span>, <span class="mono">input_per_1m</span> and <span class="mono">output_per_1m</span> (USD per million tokens, frozen at capture), <span class="mono">cost_usd</span> (computed from those), <span class="mono">or_cost</span> (OpenRouter's reported charge), <span class="mono">temperature</span>, <span class="mono">prompt_sha</span> (which system prompt was used), <span class="mono">max_tokens</span> (the completion cap actually used for that capture), <span class="mono">reasoning_effort</span>, and <span class="mono">finish_reason</span>. Per-model records add <span class="mono">release_date</span>, <span class="mono">knowledge_cutoff</span>, <span class="mono">context_length</span>, pricing, and <span class="mono">description</span>.</p>
  </section>

  <section><div class="section-head"><h2>Retries and non-responses</h2></div>
    <p>Some models (usually reasoning or "thinking" models) return HTTP&nbsp;200 but <b>no visible answer</b>: they spend the whole output budget on hidden reasoning. When that happens at the standard cap of {(f"{int(cur_max_tokens):,}" if cur_max_tokens else "the normal")} max tokens, the question is retried <b>once</b> at a larger cap of {(f"{int(retry_max_tokens):,}" if retry_max_tokens else "a higher value")} max tokens. Two other cases also get <b>one</b> extra attempt (since 2026-11-22). The prompt, system prompt and temperature never change:</p>
    <ul class="give">
      <li><b>Cut off at the cap</b> (the provider reports <span class="mono">finish_reason: length</span>): retried once at the larger cap. The fuller retry is shown, and the cut-off first attempt is kept in <span class="mono">first_attempt</span>, one click away on the page. Older answers that hit the cap carry a "cut off" or "likely cut off" badge instead.</li>
      <li><b>Blocked by content moderation</b> (<span class="mono">finish_reason: content_filter</span>): if the provider blocked the <i>prompt</i> (no output at all), there is no retry, because the same prompt is blocked again. If output was generated and then blocked, it is retried once at the <i>same</i> cap. The provider's own message is saved in <span class="mono">refusal</span>, <span class="mono">error_message</span> and <span class="mono">native_finish_reason</span>.</li>
    </ul>
    <p>Every record therefore carries flags so you are never blind to a parameter change:</p>
    <ul class="give">
      <li><span class="mono">retry</span> (bool): the answer was captured only after the larger-cap retry. Its <span class="mono">max_tokens</span> reflects the larger cap, and <span class="mono">retry_reason</span> states exactly what changed.</li>
      <li><span class="mono">retry_success</span> (bool, when <span class="mono">retry</span> is true): whether the retry produced an answer.</li>
      <li><span class="mono">first_attempt</span> / <span class="mono">retry_attempt</span> (object): the attempt that is <i>not</i> shown, with its own answer, tokens, cap and finish reason, so no call is ever discarded.</li>
      <li><span class="mono">empty</span> (bool): the model was reached but returned no content even after the retry. These <b>non-responses are kept, not dropped</b>, with <span class="mono">retry_success: false</span> and an explanatory <span class="mono">retry_reason</span>.</li>
      <li><span class="mono">error</span> (bool): the model <b>could not be reached at all</b>, so no answer was captured. This is distinct from a non-response: the provider rate-limited us (<span class="mono">http 429</span>), rejected the request (<span class="mono">http 400/403</span>), or no longer serves the model (<span class="mono">http 404</span>, delisted). <span class="mono">error_status</span> gives the short status and <span class="mono">error_reason</span> a plain-English explanation. These are <b>re-attempted on later runs</b> and clear automatically once a capture succeeds, so a rate-limited model can still fill in.</li>
    </ul>
    <p>Browse both at <a href="gaps.html">Gaps</a>, or get them all as JSON at <a href="{SITE}/api/gaps.json"><span class="mono">/api/gaps.json</span></a>, where each entry carries a <span class="mono">kind</span> of <span class="mono">no_answer</span> (reached but empty) or <span class="mono">unavailable</span> (could not be reached). The <span class="mono">conditions</span> block in every API file also reports <span class="mono">max_tokens</span> and <span class="mono">retry_max_tokens</span>.</p>
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
        d = {k: r.get(k) for k in (
            "response_date", "answer", "prompt_tokens", "completion_tokens", "total_tokens",
            "input_per_1m", "output_per_1m", "cost_usd", "or_cost", "temperature", "prompt_sha",
            "max_tokens", "reasoning_effort", "finish_reason")}
        # retry / non-response transparency: these are present on every record so a
        # researcher is never blind to a parameter change or a missing answer.
        d["retry"] = bool(r.get("retry"))                       # was this answer captured only after a larger-cap retry?
        d["retry_success"] = r.get("retry_success") if r.get("retry") else None
        d["retry_reason"] = r.get("retry_reason")               # why the retry happened / what the larger cap was
        d["empty"] = bool(r.get("empty"))                       # True = reached the model but it returned no answer (a gap)
        d["error"] = bool(r.get("error"))                       # True = could not reach the model (rate-limited / rejected / delisted)
        d["error_status"] = r.get("error_status")               # short status, e.g. "http 429"
        d["error_reason"] = r.get("error_reason")               # human-readable reason this pair is uncaptured
        d["truncated"] = truncation(r)                          # "yes" (finish_reason=length), "likely" (inferred: tokens hit cap), or null
        d["moderated"] = moderated(r)                           # True = finish_reason=content_filter
        for k in ("native_finish_reason", "refusal", "error_message", "provider",
                  "moderation_side", "first_attempt", "retry_attempt"):
            d[k] = r.get(k)                                     # provider's own words + every attempt (null on older records)
        return d
    conditions = {"temperature": temp, "system_prompt": system_prompt,
                  "prompt_sha": next((r.get("prompt_sha") for r in responses if r.get("prompt_sha")), None),
                  "max_tokens": cur_max_tokens, "reasoning_effort": cur_reasoning,
                  "retry_max_tokens": retry_max_tokens,
                  "note": ("Per-answer temperature, prompt_sha, max_tokens and reasoning_effort record the exact "
                           "conditions for each capture; earlier snapshots may differ from the current values shown here. "
                           "If a model returns HTTP 200 with empty content at the standard max_tokens, the question is "
                           f"retried ONCE at a larger cap ({retry_max_tokens or 'a higher value'}); that answer's max_tokens "
                           "reflects the larger cap and carries retry=true. If it is still empty after the retry, the record "
                           "is kept with empty=true, retry_success=false and an explanatory retry_reason (a non-response), "
                           "rather than being dropped. Since 2026-11-22, an answer cut off at the standard cap is also retried ONCE at "
                           "the larger cap (the cut-off attempt is kept in first_attempt), and an output-side content-moderation "
                           "block is retried ONCE at the same cap; a prompt-side block (no output) is not retried. The prompt, "
                           "system prompt and temperature never change between attempts.")}
    api = os.path.join(ROOT, "api")
    for d in ("models", "questions"):
        dd = os.path.join(api, d)
        os.makedirs(dd, exist_ok=True)
        for fn in os.listdir(dd):  # prune stale json so removed ids don't linger
            if fn.endswith(".json"):
                os.remove(os.path.join(dd, fn))
    # per model (answers array includes gap + error records, flagged empty/error=true, so non-captures are not hidden)
    gap_records = []  # (model, question, record) for every stored 200-but-empty non-response
    err_records = []  # (model, question, record) for every stored could-not-reach error
    for m in models_sorted:
        snap = [rmap[(m["id"], q["id"])] for q in questions if (m["id"], q["id"]) in rmap]
        caps = [r["response_date"] for r in snap if not is_error(r)]
        ans = []
        for q in questions:
            r = rmap.get((m["id"], q["id"]))
            if not r: continue
            ans.append({"question_id": q["id"], "question": q["question"],
                        "category": q.get("category") or None, **answer_fields(r)})
            if r.get("empty"): gap_records.append((m, q, r))
            elif is_error(r): err_records.append((m, q, r))
        json.dump({"model": m, "captured": (max(caps) if caps else None),
                   "answered": sum(1 for r in snap if answered(r)),
                   "gaps": sum(1 for r in snap if r.get("empty")),
                   "unavailable": sum(1 for r in snap if is_error(r)),
                   "question_count": len(questions),
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
                   "answered": sum(1 for a in ans if not a.get("empty") and not a.get("error")),
                   "gaps": sum(1 for a in ans if a.get("empty")),
                   "unavailable": sum(1 for a in ans if a.get("error")),
                   "conditions": conditions, "answers": ans},
                  open(os.path.join(api, "questions", q["id"] + ".json"), "w"), indent=2)
    # dedicated gaps feed: every non-capture in one place, with the reason and a kind:
    #   "no_answer"   = reached (HTTP 200) but returned empty content even after the larger-cap retry
    #   "unavailable" = could not be reached (rate-limited / rejected / delisted), no answer captured
    def _gap_entry(m, q, r, kind):
        return {"model_id": m["id"], "model_name": m["name"], "slug": m["slug"],
                "provider": m["provider"], "publisher": pub_name[m["provider"]],
                "question_id": q["id"], "question": q["question"], "kind": kind,
                "response_date": r.get("response_date"),
                "max_tokens": r.get("max_tokens"), "retry": bool(r.get("retry")),
                "retry_success": r.get("retry_success") if r.get("retry") else None,
                "finish_reason": r.get("finish_reason"),
                "error_status": r.get("error_status"),
                "reason": gap_reason(r).replace("&rarr;", "").strip(),
                "completion_tokens": r.get("completion_tokens")}
    gaps_json = ([_gap_entry(m, q, r, "no_answer") for (m, q, r) in gap_records]
                 + [_gap_entry(m, q, r, "unavailable") for (m, q, r) in err_records])
    json.dump({"site": SITE, "updated": BUILT, "count": len(gaps_json),
               "no_answer_count": len(gap_records), "unavailable_count": len(err_records),
               "about": ("Non-captures, preserved honestly rather than dropped. kind=no_answer: the model was "
                         "reached (HTTP 200) but returned empty content even after the question was retried once "
                         "at a larger max_tokens cap (usually a reasoning model that spent its whole budget thinking). "
                         "kind=unavailable: the model could not be reached at all (the provider rate-limited, rejected, "
                         "or no longer serves it), so no answer was captured; these are re-attempted on later runs."),
               "conditions": conditions, "gaps": gaps_json},
              open(os.path.join(api, "gaps.json"), "w"), indent=2)
    # index
    json.dump({"site": SITE, "updated": BUILT, "model_count": len(models), "question_count": len(questions),
               "gap_count": len(gap_records), "unavailable_count": len(err_records), "gaps_json": f"{SITE}/api/gaps.json",
               "conditions": conditions,
               "models": [{"id": m["id"], "slug": m["slug"], "name": m["name"], "provider": m["provider"],
                           "release_date": m.get("release_date"),
                           "delisted": bool(m.get("delisted")),
                           "answered": sum(1 for q in questions if answered(rmap.get((m["id"], q["id"])))),
                           "json": f"{SITE}/api/models/{m['slug']}.json"} for m in models_sorted],
               "questions": [{"id": q["id"], "question": q["question"],
                              "json": f"{SITE}/api/questions/{q['id']}.json"} for q in questions]},
              open(os.path.join(api, "index.json"), "w"), indent=2)
    print(f"json api: {len(models_sorted)} model files + {len(questions)} question files + index.json + gaps.json ({len(gaps_json)} gaps)")

    # ---------- GAPS (non-responses) ----------
    # Group the stored non-responses by model so a reader sees which models decline/fail.
    by_model = defaultdict(list)
    for (m, q, r) in gap_records:
        by_model[m["id"]].append((m, q, r))
    gap_rows = []
    for mid in sorted(by_model, key=lambda i: by_model[i][0][0]["name"].lower()):
        items = by_model[mid]
        m = items[0][0]
        def _gap_li(q, r):
            fr = f' &middot; finish_reason {esc(r.get("finish_reason"))}' if r.get("finish_reason") else ""
            qt = esc(q["question"][:90]) + ("..." if len(q["question"]) > 90 else "")
            return (f'<li><a href="questions/{esc(q["id"])}.html">{qt}</a>'
                    f'<span class="d">{esc(gap_reason(r))}{fr}</span></li>')
        lis = "".join(_gap_li(q, r) for (_, q, r) in sorted(items, key=lambda it: it[1]["id"]))
        gap_rows.append(f"""<article class="gap-card">
  <div class="gap-head"><a class="title" href="models/{esc(m['slug'])}.html">{esc(m['name'])}</a>
  <span class="prov mono">{esc(m['id'])}</span></div>
  <ul class="gap-qs">{lis}</ul>
</article>""")

    # "Could not be reached" group: models that errored (rate-limited / rejected / delisted).
    by_model_err = defaultdict(list)
    for (m, q, r) in err_records:
        by_model_err[m["id"]].append((m, q, r))
    err_rows = []
    for mid in sorted(by_model_err, key=lambda i: by_model_err[i][0][0]["name"].lower()):
        items = by_model_err[mid]
        m = items[0][0]
        # one reason per model (they share a cause); note how many questions it affects
        reason = unavail_reason(items[0][2])
        st = items[0][2].get("error_status")
        n = len(items)
        err_rows.append(f"""<article class="gap-card">
  <div class="gap-head"><a class="title" href="models/{esc(m['slug'])}.html">{esc(m['name'])}</a>
  <span class="prov mono">{esc(m['id'])}</span></div>
  <p class="d" style="margin:6px 0 0">{esc(reason)}{(f' &middot; {esc(st)}' if st else '')} &middot; affects {n} question{'s' if n!=1 else ''}. Re-attempted on later runs.</p>
</article>""")
    err_section = (f"""
  <section style="margin-top:28px"><div class="section-head"><h2>Could not be reached</h2><span class="count">{len(err_records)} across {len(by_model_err)} model{'s' if len(by_model_err)!=1 else ''}</span></div>
    <p class="muted" style="margin:-4px 0 14px">Different from a non-response above: these models were never reached, so no answer exists to preserve. The provider rate-limited us (HTTP&nbsp;429), rejected the request (HTTP&nbsp;400/403), or no longer serves the model (delisted). Each is recorded with <span class="mono">error: true</span> and an <span class="mono">error_reason</span>, and is re-attempted automatically on later runs, so a rate-limited model can still fill in.</p>
    <div class="gap-cards">{''.join(err_rows)}</div>
  </section>""" if err_rows else "")

    gaps_intro = f"""<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)">
    <h1>Gaps</h1>
    <p>Honesty about what is missing. An answer can be absent for two different reasons, and Waiback preserves and explains both rather than leaving a blank that looks like pending work.</p>
    <p><b>Reached, but no answer</b> (below): the request returned HTTP&nbsp;200 but the model produced <b>no visible answer</b>, almost always a reasoning or "thinking" model that spent its entire output budget on hidden reasoning. When that happens at the standard cap of <b>{(f"{int(cur_max_tokens):,}" if cur_max_tokens else "the normal")} max tokens</b>, Waiback retries the question <b>once</b> at a larger cap of <b>{(f"{int(retry_max_tokens):,}" if retry_max_tokens else "a higher value")} max tokens</b>. If it still returns nothing, the attempt is kept as a non-response, carrying <span class="mono">empty: true</span>, <span class="mono">retry: true</span>, <span class="mono">retry_success: false</span> and a <span class="mono">retry_reason</span> in the <a href="data.html">research data</a>.</p>
    <p class="meta">{len(gap_records)} non-response{'s' if len(gap_records)!=1 else ''} across {len(by_model)} model{'s' if len(by_model)!=1 else ''}{(f" &middot; {len(err_records)} unavailable across {len(by_model_err)} model" + ('s' if len(by_model_err)!=1 else '')) if err_records else ''} &middot; full list as JSON: <a href="api/gaps.json"><span class="mono">/api/gaps.json</span></a></p>
  </section>
  <section><div class="section-head"><h2>Reached, but no answer</h2><span class="count">{len(gap_records)} non-response{'s' if len(gap_records)!=1 else ''}</span></div>
  <div class="gap-cards">{''.join(gap_rows) if gap_rows else '<p class="muted">No non-responses recorded. Every reached model has answered every question.</p>'}</div>{err_section}
  <p class="crumb"><a href="index.html">&larr; the archive</a> &middot; <a href="data.html">research data</a></p></section>
</div>"""
    open(os.path.join(ROOT, "gaps.html"), "w").write(
        page("Gaps | Waiback", "Where answers are missing and why: models reached but returning no answer, and models that could not be reached (rate-limited, rejected, or delisted). Waiback preserves and explains both.",
             gaps_intro, base="", canonical="/gaps.html"))
    urls.append("/gaps.html")

    # ---------- ABOUT (author + project network hub) ----------
    # Built entirely from data/profile.json so the same page + Person schema can be
    # reused across Ted's sites. Links out to tedkwartler.com (rel=me) and lists the
    # sibling projects, which is genuinely useful context and reinforces the entity.
    name = esc(PROFILE.get("name", "Ted Kwartler"))
    ted_url = PROFILE.get("url", "https://tedkwartler.com/")
    headline = PROFILE.get("headline", "")
    bio = PROFILE.get("bio", "")
    # never surface TODO placeholder text to a reader; fall back to a neutral sentence
    bio_display = bio if (bio and not bio.startswith("TODO")) else f"{PROFILE.get('name','Ted Kwartler')} is the creator of Waiback and other AI and data projects."
    projects = PROFILE.get("projects", [])
    sameas = [u for u in PROFILE.get("sameAs", []) if u and u.rstrip("/") != ted_url.rstrip("/")]
    proj_cards = "".join(
        f"""<article class="gap-card">
  <div class="gap-head"><a class="title" href="{esc(p['url'])}" rel="noopener">{esc(p['name'])}</a>
  <span class="prov mono">{'this site' if p.get('self') else esc(p['url'].split('//')[-1].strip('/'))}</span></div>
  <p class="d" style="margin:6px 0 0">{esc(p['desc']) if p.get('desc') and not p['desc'].startswith('TODO') else ''}</p>
</article>""" for p in projects)
    sameas_links = (" &middot; ".join(
        f'<a href="{esc(u)}" rel="me noopener">{esc(u.split("//")[-1].strip("/"))}</a>' for u in sameas)
        if sameas else "")
    # research / publications: a crawlable list plus a ScholarlyArticle per paper tied
    # to the author by @id, which is the structured signal search and generative engines
    # use to attribute the work (arXiv entries also feed LLM training corpora directly).
    publications = PROFILE.get("publications", [])
    pub_rows = "".join(
        f"""<article class="gap-card">
  <div class="gap-head"><a class="title" href="{esc(p['url'])}" rel="noopener">{esc(p['title'])}</a></div>
  <p class="d" style="margin:6px 0 0">{esc(p.get('venue',''))}{(' &middot; ' + esc(str(p['date']))) if p.get('date') else ''}</p>
</article>""" for p in publications)
    pub_section = (f"""
  <section style="margin-top:28px"><div class="section-head"><h2>Research &amp; publications</h2><span class="count">{len(publications)} selected</span></div>
    <div class="gap-cards">{pub_rows}</div>
  </section>""" if publications else "")
    about_graph = [{"@type": "ProfilePage", "url": f"{SITE}/about.html", "mainEntity": {"@id": PERSON_ID}}]
    for p in publications:
        art = {"@type": "ScholarlyArticle", "name": p["title"], "headline": p["title"],
               "url": p["url"], "author": {"@id": PERSON_ID}}
        if p.get("date"): art["datePublished"] = str(p["date"])
        if p.get("venue"): art["isPartOf"] = {"@type": "CreativeWork", "name": p["venue"]}
        about_graph.append(art)
    about_jsonld = json.dumps({"@context": "https://schema.org", "@graph": about_graph})
    about_body = f"""<div class="wrap">
  <section class="hero" style="border-bottom:1px solid var(--line)">
    <h1>{name}</h1>
    {f'<p class="meta">{esc(headline)}</p>' if headline and not headline.startswith('TODO') else ''}
    <p>{esc(bio_display)}</p>
    <p><a class="btn" href="{esc(ted_url)}" rel="me noopener"><b>{esc(ted_url.split('//')[-1].strip('/'))}</b> &rarr;</a></p>
    {f'<p class="meta">Elsewhere: {sameas_links}</p>' if sameas_links else ''}
  </section>
  <section><div class="section-head"><h2>Projects by {name}</h2><span class="count">{len(projects)} projects</span></div>
    <p class="muted" style="margin:-4px 0 14px">A connected set of AI and data projects. Each links back to <a href="{esc(ted_url)}" rel="me noopener">{esc(ted_url.split('//')[-1].strip('/'))}</a>.</p>
    <div class="gap-cards">{proj_cards}</div>
  </section>{pub_section}
  <p class="crumb"><a href="index.html">&larr; the archive</a> &middot; <a href="{esc(ted_url)}" rel="me noopener">{esc(ted_url.split('//')[-1].strip('/'))}</a></p>
</div>"""
    open(os.path.join(ROOT, "about.html"), "w").write(
        page(f"{PROFILE.get('name','Ted Kwartler')} | Waiback",
             (bio[:155] if bio and not bio.startswith('TODO') else f"About {PROFILE.get('name','Ted Kwartler')}, creator of Waiback and other AI and data projects."),
             about_body, base="", canonical="/about.html",
             extra_head=f'<script type="application/ld+json">{about_jsonld}</script>'))
    urls.append("/about.html")

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
            "", "## About the author",
            f"> {PROFILE.get('bio','').replace(chr(10),' ')}" if (PROFILE.get('bio') and not PROFILE.get('bio','').startswith('TODO')) else f"> Waiback was created by {PROFILE.get('name','Ted Kwartler')}.",
            "",
            f"- {PROFILE.get('name','Ted Kwartler')}: {PROFILE.get('url','https://tedkwartler.com/')}",
            f"- About / profile: {SITE}/about.html"]
    for u in [u for u in PROFILE.get("sameAs", []) if u and u.rstrip('/') != PROFILE.get('url','').rstrip('/')]:
        llms.append(f"- Also at: {u}")
    llms += ["", "## Other projects by " + PROFILE.get("name", "Ted Kwartler")]
    for p in PROFILE.get("projects", []):
        if p.get("self"): continue
        d = p.get("desc", "")
        llms.append(f"- [{p['name']}]({p['url']})" + (f": {d}" if d and not d.startswith("TODO") else ""))
    llms += ["", "## Models (newest first)"]
    for m in models_sorted:
        llms.append(f"- [{m['name']}]({SITE}/models/{m['slug']}.html): released {m.get('release_date','?')}, publisher {pub_name[m['provider']]}.")
    llms += ["", "## Publishers"]
    for prov in sorted(pubs, key=lambda p: (-len(pubs[p]), p)):
        llms.append(f"- [{pub_name[prov]}]({SITE}/publishers/{prov}.html): {len(pubs[prov])} models.")
    llms += ["", "## Data (machine-readable)",
             f"- Index of all data: {SITE}/api/index.json",
             f"- Per-model answers: {SITE}/api/models/<slug>.json",
             f"- Per-question answers: {SITE}/api/questions/<id>.json",
             f"- Gaps (models reached but returning no answer, and models that could not be reached, each with a reason and kind): {SITE}/api/gaps.json"]
    llms += ["", "## Data",
             f"- For researchers (download, API, citation): {SITE}/data.html",
             f"- Gaps, explained (non-responses and unavailable models): {SITE}/gaps.html",
             f"- JSON index: {SITE}/api/index.json",
             "", "## Contribute",
             f"- Suggest a question: {SITE}/contribute.html (opens a GitHub issue reviewed by Ted Kwartler)"]
    llms += ["", "## Related", f"- By Ted Kwartler: https://tedkwartler.com/"]
    open(os.path.join(ROOT, "llms.txt"), "w").write("\n".join(llms) + "\n")
    open(os.path.join(ROOT, "llms-full.txt"), "w").write("\n".join(llms) + "\n")

    print(f"rendered: index + {len(models_sorted)} model pages + {len(pubs)} publisher pages + {len(questions)} question pages; sitemap {len(urls)} urls")

if __name__ == "__main__":
    render()
