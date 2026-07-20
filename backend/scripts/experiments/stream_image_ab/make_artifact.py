"""Build the blind A/B HTML artifact for owner judgment.

Per run: the stream chart + the two coach reports shown ANONYMIZED and order-matched
to the semantic judge (same per-run seed), so the owner and the judge see the same
blind layout. Token counts, arm identities, and the judge's verdict live in a
collapsed reveal the owner opens only AFTER picking. Output: out/artifact.html
(self-contained; charts embedded as data URIs).
"""
from __future__ import annotations

import html
import json
import os
import re
from typing import Any, Dict, List, Optional

from judge import _order  # identical per-run anonymization seed

OUT_DIR = os.environ.get("EXP_OUT") or os.path.join(os.path.dirname(__file__), "out")

_SCRIPT = r"""<script>
(function(){
  var KEY = "streamab_picks_v1";
  var runs = Array.prototype.slice.call(document.querySelectorAll("section.run"));
  var store = {};
  try { store = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch(e){ store = {}; }

  function armFor(sec, choice){
    if(choice === "draw") return "draw";
    var imageIsR1 = sec.getAttribute("data-img1") === "1";
    if(choice === "r1") return imageIsR1 ? "image" : "json";
    return imageIsR1 ? "json" : "image";
  }
  function label(choice){ return choice==="r1"?"Report 1":choice==="r2"?"Report 2":"Draw"; }

  function render(){
    var picks = [];
    runs.forEach(function(sec, i){
      var aid = sec.getAttribute("data-aid");
      var choice = store[aid];
      sec.querySelectorAll(".pk").forEach(function(b){
        b.setAttribute("aria-pressed", String(b.getAttribute("data-c") === choice));
      });
      if(choice) picks.push("Run "+(i+1)+": "+label(choice));
    });
    document.getElementById("prog").textContent = String(picks.length);
    var pe = document.getElementById("picks");
    pe.innerHTML = picks.length ? picks.map(function(p){return p.replace(/[<>&]/g,"");}).join("  ·  ")
                                : '<span class="none">no picks yet</span>';
  }

  runs.forEach(function(sec){
    var aid = sec.getAttribute("data-aid");
    sec.querySelectorAll(".pk").forEach(function(b){
      b.addEventListener("click", function(){
        var c = b.getAttribute("data-c");
        if(store[aid] === c){ delete store[aid]; } else { store[aid] = c; }
        localStorage.setItem(KEY, JSON.stringify(store));
        render();
      });
    });
  });

  function toast(msg){
    var t = document.getElementById("toast");
    t.textContent = msg; t.classList.add("show");
    setTimeout(function(){ t.classList.remove("show"); }, 1800);
  }

  document.getElementById("clear").addEventListener("click", function(){
    if(!confirm("Clear all your picks?")) return;
    store = {}; localStorage.removeItem(KEY); render();
  });

  document.getElementById("copy").addEventListener("click", function(){
    var lines = ["Stream A/B - my blind picks (decoded to arm):"];
    var tally = {image:0, json:0, draw:0};
    runs.forEach(function(sec, i){
      var aid = sec.getAttribute("data-aid");
      var choice = store[aid];
      var lab = sec.getAttribute("data-label") || aid;
      if(!choice){ lines.push("Run "+(i+1)+" ["+lab+"]: (not scored)"); return; }
      var arm = armFor(sec, choice);
      tally[arm]++;
      lines.push("Run "+(i+1)+" ["+lab+"]: "+label(choice)+" -> "+arm.toUpperCase());
    });
    lines.push("");
    lines.push("Tally - IMAGE:"+tally.image+"  JSON:"+tally.json+"  draw:"+tally.draw);
    var text = lines.join("\n");
    function done(){ toast("Picks copied - paste them back to me"); }
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(done, function(){ window.prompt("Copy your picks:", text); });
    } else { window.prompt("Copy your picks:", text); }
  });

  render();
})();
</script>"""


def _inline(s: str) -> str:
    """Inline markdown on already-HTML-escaped text: bold, italic, code."""
    s = re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\*\w])\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+?)`", r"<code>\1</code>", s)
    return s


def _is_table_sep(line: str) -> bool:
    t = line.strip()
    return "|" in t and bool(t) and set(t) <= set("|:- ") and "-" in t


def _md_to_html(text: str) -> str:
    """Self-contained markdown -> HTML: headings, GitHub pipe tables, ordered/unordered
    lists, horizontal rules, fenced code, and inline bold/italic/code. Escapes first so
    model output can never inject markup."""
    if not text:
        return "<em>(empty)</em>"
    lines = html.escape(text).split("\n")
    n = len(lines)
    out: List[str] = []
    i = 0
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue
        # fenced code block
        if s.startswith("```"):
            buf = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # consume closing fence
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue
        # horizontal rule
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            out.append("<hr>")
            i += 1
            continue
        # heading
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            lvl = min(len(m.group(1)) + 2, 6)
            out.append(f"<h{lvl} class='md-h'>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        # pipe table (header row + separator row)
        if "|" in raw and i + 1 < n and _is_table_sep(lines[i + 1]):
            def cells(row: str) -> List[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]
            header = cells(s)
            i += 2
            body = []
            while i < n and "|" in lines[i] and lines[i].strip():
                body.append(cells(lines[i]))
                i += 1
            thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body
            )
            out.append(
                f"<div class='md-tw'><table class='md-table'>"
                f"<thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>"
            )
            continue
        # unordered list
        if re.match(r"[-*+]\s+", s):
            items = []
            while i < n and re.match(r"[-*+]\s+", lines[i].strip()):
                items.append(_inline(re.sub(r"^[-*+]\s+", "", lines[i].strip())))
                i += 1
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue
        # ordered list
        if re.match(r"\d+\.\s+", s):
            items = []
            while i < n and re.match(r"\d+\.\s+", lines[i].strip()):
                items.append(_inline(re.sub(r"^\d+\.\s+", "", lines[i].strip())))
                i += 1
            out.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            continue
        # paragraph
        buf = [_inline(s)]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith("```") or "|" in lines[i]
                    or re.match(r"(#{1,6}\s|[-*+]\s|\d+\.\s)", nxt)
                    or re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", nxt)):
                break
            buf.append(_inline(nxt))
            i += 1
        out.append("<p>" + "<br>".join(buf) + "</p>")
    return "\n".join(out)


def _judgment_index(judgments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {j["activity_id"]: j for j in judgments}


def build(results: Dict[str, Any], judgments: Optional[List[Dict[str, Any]]]) -> str:
    jidx = _judgment_index(judgments or [])
    runs = results["runs"]
    cards = []
    for i, run in enumerate(runs, 1):
        aid = run["activity_id"]
        image_is_1 = _order(aid)
        a_json, a_image = run["arm_json"], run["arm_image"]
        r1, r2 = (a_image, a_json) if image_is_1 else (a_json, a_image)
        r1_kind = "image" if image_is_1 else "json"
        r2_kind = "json" if image_is_1 else "image"

        j = jidx.get(aid)
        judge_block = ""
        if j:
            sc = j["scores"]
            def row(kind):
                s = sc[kind]
                uc = "".join(f"<li>{html.escape(c)}</li>" for c in s["unsupported_claims"]) or "<li><em>none</em></li>"
                return (f"<tr><td>{kind}</td><td>{s['insight']}</td><td>{s['specificity']}</td>"
                        f"<td>{s['faithfulness']}</td><td><ul class='uc'>{uc}</ul></td></tr>")
            judge_block = (
                f"<h4>Semantic judge</h4>"
                f"<table class='sc'><tr><th>arm</th><th>insight</th><th>specificity</th>"
                f"<th>faithful</th><th>unsupported claims</th></tr>{row('json')}{row('image')}</table>"
                f"<p><strong>Judge winner:</strong> {html.escape(str(j.get('winner_arm') or 'tie'))} "
                f"({html.escape(j.get('margin',''))})</p>"
                f"<p class='rat'>{html.escape(j.get('rationale',''))}</p>"
            )

        reveal = (
            f"<details class='reveal'><summary>Reveal (identities · tokens · judge)</summary>"
            f"<p>Report 1 = <strong>{r1_kind.upper()}</strong> arm · "
            f"Report 2 = <strong>{r2_kind.upper()}</strong> arm</p>"
            f"<table class='sc'><tr><th>arm</th><th>input tok (counted)</th><th>input tok (usage)</th><th>output tok</th><th>fallback</th></tr>"
            f"<tr><td>JSON (numeric stream_view)</td><td>{a_json['input_tokens_counted']}</td>"
            f"<td>{a_json['input_tokens_usage']}</td><td>{a_json['output_tokens']}</td><td>{a_json['is_fallback']}</td></tr>"
            f"<tr><td>IMAGE (chart)</td><td>{a_image['input_tokens_counted']}</td>"
            f"<td>{a_image['input_tokens_usage']}</td><td>{a_image['output_tokens']}</td><td>{a_image['is_fallback']}</td></tr></table>"
            f"{judge_block}</details>"
        )

        img1 = "1" if image_is_1 else "0"
        cards.append(f"""
        <section class="run" data-aid="{aid}" data-img1="{img1}" data-label="{html.escape(run['label'], quote=True)}">
          <h2><span class="n">{i}</span> <span class="label">{html.escape(run['label'])}</span></h2>
          <img class="chart" src="data:image/png;base64,{run['chart_png_b64']}" alt="stream chart"/>
          <div class="pair">
            <div class="report"><h3 class="rhead">Report 1</h3>{_md_to_html(r1['message'])}</div>
            <div class="report"><h3 class="rhead">Report 2</h3>{_md_to_html(r2['message'])}</div>
          </div>
          <div class="picker" role="group" aria-label="Your pick for run {i}">
            <span class="picker-label">Which reads this run better?</span>
            <div class="pk-row">
              <button type="button" class="pk" data-c="r1">Report 1</button>
              <button type="button" class="pk" data-c="draw">Draw</button>
              <button type="button" class="pk" data-c="r2">Report 2</button>
            </div>
          </div>
          {reveal}
        </section>""")

    tally = {"json": 0, "image": 0, "tie": 0}
    for j in (judgments or []):
        tally[j.get("winner_arm") or "tie"] += 1
    spoiler = (f"<details class='spoiler'><summary>Spoiler — semantic-judge tally (open after you've scored)</summary>"
               f"<p class='tally'><span class='chip img'>IMAGE {tally['image']}</span>"
               f"<span class='chip jsn'>JSON {tally['json']}</span>"
               f"<span class='chip tie'>tie {tally['tie']}</span></p></details>") if judgments else ""

    return f"""<title>Stream representation A/B — numeric vs chart image</title>
<style>
  :root {{
    --bg:#f7f8f7; --panel:#fefffe; --fg:#12140f; --muted:#5c635a; --faint:#828a7f;
    --line:#dfe4dd; --line2:#eef1ed; --accent:#0e7c86; --img:#0e7c86; --jsn:#5b6bd6;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,sans-serif;
  }}
  @media (prefers-color-scheme:dark) {{ :root {{
    --bg:#0f1210; --panel:#161a17; --fg:#e7ebe4; --muted:#a2aa9d; --faint:#7c847790;
    --line:#282e28; --line2:#1e231e; --accent:#4fd0c9; --img:#4fd0c9; --jsn:#93a0f0;
  }} }}
  :root[data-theme=dark] {{
    --bg:#0f1210; --panel:#161a17; --fg:#e7ebe4; --muted:#a2aa9d; --faint:#7c8477;
    --line:#282e28; --line2:#1e231e; --accent:#4fd0c9; --img:#4fd0c9; --jsn:#93a0f0;
  }}
  :root[data-theme=light] {{
    --bg:#f7f8f7; --panel:#fefffe; --fg:#12140f; --muted:#5c635a; --faint:#828a7f;
    --line:#dfe4dd; --line2:#eef1ed; --accent:#0e7c86; --img:#0e7c86; --jsn:#5b6bd6;
  }}
  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--fg); font:16px/1.65 var(--sans);
    max-width:1080px; margin:0 auto; padding:2.4rem 1.3rem 4rem; }}
  .eyebrow {{ font:600 12px/1 var(--mono); letter-spacing:.14em; text-transform:uppercase;
    color:var(--accent); margin:0 0 .6rem; }}
  h1 {{ font-size:1.85rem; line-height:1.15; margin:0 0 .5rem; text-wrap:balance; letter-spacing:-.01em; }}
  .lede {{ color:var(--muted); margin:0; max-width:64ch; }}
  .lede code {{ font:.85em var(--mono); background:var(--line2); padding:.08em .4em; border-radius:4px; }}
  .brief {{ border:1px solid var(--line); background:var(--panel); border-radius:12px;
    padding:1rem 1.2rem; margin:1.4rem 0; display:grid; gap:.35rem; }}
  .brief b {{ color:var(--fg); }} .brief li {{ color:var(--muted); }}
  .brief ol {{ margin:.2rem 0; padding-left:1.2rem; }}
  .run {{ border:1px solid var(--line); border-radius:14px; padding:1.3rem 1.4rem;
    margin:1.5rem 0; background:var(--panel); }}
  .run > h2 {{ font-size:1.15rem; margin:0 0 .3rem; display:flex; align-items:baseline; gap:.6rem; flex-wrap:wrap; }}
  .run > h2 .n {{ font:700 .8rem/1 var(--mono); color:var(--accent); border:1px solid var(--accent);
    border-radius:999px; padding:.3em .6em; }}
  .label {{ color:var(--faint); font-weight:400; font-size:.9rem; font-family:var(--mono); }}
  img.chart {{ width:100%; height:auto; border-radius:9px; border:1px solid var(--line); background:#fff; display:block; margin:.4rem 0 0; }}
  .pair {{ display:grid; grid-template-columns:1fr 1fr; gap:1.1rem; margin-top:1.1rem; align-items:start; }}
  @media (max-width:800px) {{ .pair {{ grid-template-columns:1fr; }} }}
  /* min-width:0 lets each column hold a true 1fr; without it a wide table forces its
     track wider and squashes the sibling. Wide content scrolls in its own container. */
  .report {{ min-width:0; border:1px solid var(--line); border-radius:10px; padding:.4rem 1.1rem 1rem; background:var(--bg); }}
  .report .rhead {{ font:600 .78rem/1 var(--mono); letter-spacing:.1em; text-transform:uppercase;
    color:var(--faint); margin:1rem 0 .7rem; }}
  .report p {{ margin:.6rem 0; }}
  /* rendered markdown inside a report */
  .report .md-h {{ font-family:var(--sans); font-weight:700; color:var(--fg); line-height:1.25;
    margin:1.1rem 0 .35rem; letter-spacing:-.005em; }}
  .report h3.md-h {{ font-size:1.06rem; }}
  .report h4.md-h {{ font-size:.98rem; }}
  .report h5.md-h, .report h6.md-h {{ font-size:.86rem; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }}
  .report ul, .report ol {{ margin:.45rem 0; padding-left:1.35rem; }}
  .report li {{ margin:.2rem 0; }}
  .report hr {{ border:0; border-top:1px solid var(--line); margin:.9rem 0; }}
  .report pre {{ background:var(--line2); border:1px solid var(--line); border-radius:7px;
    padding:.6rem .8rem; overflow-x:auto; font:.8rem/1.5 var(--mono); margin:.6rem 0; }}
  .report :not(pre) > code {{ font:.86em var(--mono); background:var(--line2); padding:.06em .35em; border-radius:4px; }}
  .report pre code {{ background:none; padding:0; }}
  .md-tw {{ overflow-x:auto; margin:.6rem 0; }}
  .md-table {{ border-collapse:collapse; width:100%; font-size:.85rem; font-variant-numeric:tabular-nums; }}
  .md-table th, .md-table td {{ border:1px solid var(--line); padding:.34rem .6rem; text-align:left; vertical-align:top; }}
  .md-table th {{ background:var(--line2); font-weight:600; white-space:nowrap; }}
  .pick {{ text-align:center; color:var(--muted); margin:1.2rem 0 .2rem; font-size:.95rem; }}
  .pick b {{ color:var(--fg); }}
  details {{ margin-top:.9rem; }}
  details.reveal {{ border-top:1px dashed var(--line); padding-top:.7rem; }}
  summary {{ cursor:pointer; color:var(--accent); font-weight:600; font-size:.92rem; }}
  summary:focus-visible {{ outline:2px solid var(--accent); outline-offset:3px; border-radius:4px; }}
  table.sc {{ border-collapse:collapse; width:100%; font-size:.86rem; margin:.6rem 0;
    font-variant-numeric:tabular-nums; }}
  table.sc th {{ font:600 .75rem/1.2 var(--mono); letter-spacing:.04em; text-transform:uppercase;
    color:var(--faint); text-align:left; }}
  table.sc th, table.sc td {{ border-bottom:1px solid var(--line2); padding:.42rem .5rem; text-align:left; vertical-align:top; }}
  table.sc td:not(:first-child) {{ font-family:var(--mono); }}
  h4 {{ font:600 .78rem/1 var(--mono); letter-spacing:.08em; text-transform:uppercase; color:var(--faint); margin:1rem 0 .3rem; }}
  ul.uc {{ margin:0; padding-left:1.05rem; color:var(--muted); font-family:var(--sans); }}
  .rat {{ color:var(--muted); font-size:.92rem; border-left:2px solid var(--line); padding-left:.8rem; }}
  .chip {{ display:inline-block; font:700 .8rem/1 var(--mono); padding:.4em .7em; border-radius:999px; margin-right:.5rem; }}
  .chip.img {{ color:var(--img); border:1px solid var(--img); }}
  .chip.jsn {{ color:var(--jsn); border:1px solid var(--jsn); }}
  .chip.tie {{ color:var(--faint); border:1px solid var(--line); }}
  .tally {{ margin:.5rem 0 0; }}
  details.spoiler {{ margin:1.2rem 0; }}
  code {{ font-family:var(--mono); }}

  /* picker */
  .picker {{ margin:1.2rem 0 .2rem; display:flex; flex-direction:column; align-items:center; gap:.55rem; }}
  .picker-label {{ font-size:.9rem; color:var(--muted); }}
  .pk-row {{ display:inline-flex; border:1px solid var(--line); border-radius:999px; overflow:hidden; }}
  .pk {{ appearance:none; border:0; background:transparent; color:var(--fg); font:600 .9rem var(--sans);
    padding:.55rem 1.15rem; cursor:pointer; border-left:1px solid var(--line); transition:background .12s,color .12s; }}
  .pk:first-child {{ border-left:0; }}
  .pk:hover {{ background:var(--line2); }}
  .pk:focus-visible {{ outline:2px solid var(--accent); outline-offset:-2px; }}
  .pk[aria-pressed=true] {{ background:var(--accent); color:#fff; }}
  .pk[data-c=draw][aria-pressed=true] {{ background:var(--faint); }}
  @media (prefers-reduced-motion:reduce) {{ .pk {{ transition:none; }} }}

  /* summary bar */
  .summary {{ position:sticky; top:0; z-index:5; margin:1.4rem 0; padding:.85rem 1.1rem;
    border:1px solid var(--line); border-radius:12px; background:var(--panel);
    display:flex; align-items:center; gap:1rem 1.4rem; flex-wrap:wrap; backdrop-filter:blur(6px); }}
  .summary .prog {{ font:600 .95rem var(--sans); }}
  .summary .prog b {{ font-family:var(--mono); font-variant-numeric:tabular-nums; }}
  .summary .picks {{ color:var(--muted); font:.86rem/1.5 var(--mono); flex:1 1 240px; }}
  .summary .picks .none {{ color:var(--faint); }}
  .summary .btns {{ display:flex; gap:.5rem; margin-left:auto; }}
  .tbtn {{ appearance:none; border:1px solid var(--line); background:var(--bg); color:var(--fg);
    font:600 .85rem var(--sans); padding:.5rem .9rem; border-radius:8px; cursor:pointer; }}
  .tbtn:hover {{ border-color:var(--accent); color:var(--accent); }}
  .tbtn:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
  .tbtn.primary {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
  .tbtn.primary:hover {{ filter:brightness(1.06); color:#fff; }}
  .toast {{ position:fixed; left:50%; bottom:2rem; transform:translateX(-50%);
    background:var(--fg); color:var(--bg); padding:.6rem 1.1rem; border-radius:8px; font:600 .9rem var(--sans);
    opacity:0; pointer-events:none; transition:opacity .2s; }}
  .toast.show {{ opacity:1; }}
  @media (prefers-reduced-motion:reduce) {{ .toast {{ transition:none; }} }}
</style>
<p class="eyebrow">Coach experiment · blind A/B</p>
<h1>Does the coach read a run better from numbers or a picture?</h1>
<p class="lede">{html.escape(results.get('setup') or f"Same coach, same context pack, same prompt ({results.get('prompt_id','')}) on {results.get('model','')}. The one thing that changes between the two reports on each run: the stream arrives as the numeric stream_view or as the chart above it.")} Reports are blind and order-shuffled ({html.escape(results.get('model',''))}).</p>
<div class="brief">
  <b>How to score</b>
  <ol>
    <li>Read the chart, then both reports. Which one reads the <b>shape and story</b> of this run better — and is it <b>faithful</b> to what the chart shows?</li>
    <li>Call Report 1 / Report 2 / tie and note why.</li>
    <li>Only then open the reveal for identities, token cost, and the judge.</li>
  </ol>
</div>
<div class="summary" id="summary">
  <span class="prog">Scored <b id="prog">0</b> / <b>{len(runs)}</b></span>
  <span class="picks" id="picks"><span class="none">no picks yet</span></span>
  <span class="btns">
    <button type="button" class="tbtn" id="clear">Clear</button>
    <button type="button" class="tbtn primary" id="copy">Copy my picks</button>
  </span>
</div>
{spoiler}
{''.join(cards)}
<div class="toast" id="toast" role="status" aria-live="polite"></div>
{_SCRIPT}
"""


def main():
    results = json.load(open(os.path.join(OUT_DIR, "results.json")))
    jpath = os.path.join(OUT_DIR, "judgments.json")
    judgments = json.load(open(jpath)) if os.path.exists(jpath) else None
    html_out = build(results, judgments)
    out = os.path.join(OUT_DIR, "artifact.html")
    with open(out, "w") as f:
        f.write(html_out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
