"""Export recent coach reports + conversations from production for review.

Why this exists
---------------
Building a feedback loop on the coach means regularly reading what the coach
actually said and how the follow-up conversation went, then recording the things
that need attention. This script is the mechanical half of that loop: it reads
production (READ-ONLY), pulls the most recent activities that have a coach
report, and for each one bundles the report, the full chat thread that followed,
and the runner's check-in. It writes a dated JSON snapshot (the durable record,
"pulled onto local") and renders a self-contained HTML review page.

The qualitative half -- the reviewer's notes on what caught the eye -- is folded
in from an optional `--notes` JSON file so a re-render bakes annotations into the
same page. That keeps the loop repeatable: pull -> render -> annotate -> re-render.

Safety
------
Only ever READS from the source. The source URL comes from `$SEED_SOURCE_URL`
(the `make coach-review` wrapper injects Railway's DATABASE_PUBLIC_URL), mirroring
seed_from_prod.py. No writes, no schema changes.

Usage
-----
    SEED_SOURCE_URL=postgresql://... python scripts/coach_review_export.py
    python scripts/coach_review_export.py --activities 30 --out-dir ../docs/audit
    python scripts/coach_review_export.py --from-json <snapshot.json>   # re-render only
    python scripts/coach_review_export.py --from-json <snapshot.json> --notes notes.json

Notes file shape (all keys optional):
    {
      "summary": "top-of-page markdown-ish summary of themes",
      "per_activity": {
        "<activity_id>": {"flags": ["ISSUE"], "note": "free text"}
      }
    }
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _normalise_driver(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def pull(source_url: str, limit: int) -> dict:
    """Pull the most recent `limit` activities that have a coach report, with the
    latest report per activity plus the full chat thread and the check-in."""
    engine = create_engine(_normalise_driver(source_url))
    out: list[dict] = []
    with engine.connect() as conn:
        act_rows = conn.execute(
            text(
                """
                SELECT DISTINCT a.id, a.user_id, a.name, a.type, a.start_date, a.start_date_local,
                       a.distance_m, a.moving_time_s, a.avg_hr, a.max_hr, a.user_intent
                FROM activities a
                JOIN coach_reports cr ON cr.activity_id = a.id
                WHERE a.is_deleted = false
                ORDER BY a.start_date DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        ).mappings().all()

        for a in act_rows:
            aid = a["id"]
            report = conn.execute(
                text(
                    """
                    SELECT id, prompt_id, schema_version, is_fallback, created_at,
                           report, meta, context_pack
                    FROM coach_reports
                    WHERE activity_id = :aid
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"aid": aid},
            ).mappings().first()

            chat = conn.execute(
                text(
                    """
                    SELECT role, content, created_at
                    FROM coach_chat_messages
                    WHERE activity_id = :aid
                    ORDER BY created_at ASC
                    """
                ),
                {"aid": aid},
            ).mappings().all()

            checkin = conn.execute(
                text(
                    """
                    SELECT rpe, pain_score, pain_location, sleep_quality, notes, created_at
                    FROM check_ins
                    WHERE activity_id = :aid
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"aid": aid},
            ).mappings().first()

            out.append(
                {
                    "activity": {k: _jsonable(v) for k, v in dict(a).items()},
                    "report": {k: _jsonable(v) for k, v in dict(report).items()} if report else None,
                    "chat": [{k: _jsonable(v) for k, v in dict(m).items()} for m in chat],
                    "checkin": {k: _jsonable(v) for k, v in dict(checkin).items()} if checkin else None,
                }
            )

        # The CURRENT runner memory profile per user — the "after" anchor for the
        # newest report (memory is rebuilt whole after each report, so the current
        # stored profile is the product of the newest report's completion).
        current_memory: dict = {}
        for m in conn.execute(
            text(
                """
                SELECT user_id, profile, source_report_count, grounded_through, updated_at
                FROM runner_memory
                """
            )
        ).mappings():
            prof = dict(m["profile"]) if m["profile"] else {}
            prof["source_report_count"] = m["source_report_count"]
            prof["updated_at"] = _jsonable(m["updated_at"])
            prof["grounded_through"] = _jsonable(m["grounded_through"])
            current_memory[str(m["user_id"])] = prof

    engine.dispose()
    return {
        "pulled_at": datetime.utcnow().isoformat() + "Z",
        "count": len(out),
        "items": out,
        "current_memory": current_memory,
    }


# ---------- rendering ----------

def _fmt_dur(s):
    if s is None:
        return "-"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _fmt_km(m):
    return f"{m / 1000:.2f} km" if m else "-"


def _fmt_dt(iso):
    if not iso:
        return "-"
    return iso.replace("T", " ")[:16]


def _esc(x) -> str:
    return html.escape(str(x)) if x is not None else ""


def _paras(textblock: str) -> str:
    if not textblock:
        return ""
    parts = [p.strip() for p in str(textblock).split("\n") if p.strip()]
    return "".join(f"<p>{_esc(p)}</p>" for p in parts)


def _render_report_body(report_json: dict) -> str:
    """Render the prose message (2.0) or structured content (1.x)."""
    pieces = []
    opener = report_json.get("opener_message")
    message = report_json.get("message")
    headline = report_json.get("headline")
    if headline:
        pieces.append(f'<div class="headline">{_esc(headline)}</div>')
    if opener:
        pieces.append('<div class="stage-label">Opener</div>')
        pieces.append(f'<div class="prose">{_paras(opener)}</div>')
    if message:
        if opener:
            pieces.append('<div class="stage-label">Fuller</div>')
        pieces.append(f'<div class="prose">{_paras(message)}</div>')

    # structured (legacy 1.x)
    if not message and not opener:
        for k in ("thesis",):
            if report_json.get(k):
                pieces.append(f'<div class="prose"><p>{_esc(report_json[k])}</p></div>')
        for t in report_json.get("key_takeaways", []) or []:
            txt = t.get("text") if isinstance(t, dict) else t
            pieces.append(f'<div class="prose"><p>{_esc(txt)}</p></div>')

    steps = report_json.get("next_steps") or []
    if steps:
        rows = "".join(
            f"<li><b>{_esc(s.get('action',''))}</b> — {_esc(s.get('details',''))} "
            f"<span class='why'>({_esc(s.get('why',''))})</span></li>"
            for s in steps if isinstance(s, dict)
        )
        pieces.append(f'<div class="tail"><div class="tail-h">Next steps</div><ul>{rows}</ul></div>')

    risks = report_json.get("risks") or []
    if risks:
        rows = "".join(
            f"<li><b>{_esc(r.get('flag',''))}</b>: {_esc(r.get('explanation',''))} "
            f"→ {_esc(r.get('mitigation',''))}</li>"
            for r in risks if isinstance(r, dict)
        )
        pieces.append(f'<div class="tail"><div class="tail-h">Risks</div><ul>{rows}</ul></div>')

    questions = report_json.get("questions") or []
    if questions:
        rows = "".join(
            f"<li>{_esc(q.get('question',''))} <span class='why'>({_esc(q.get('reason',''))})</span></li>"
            for q in questions if isinstance(q, dict)
        )
        pieces.append(f'<div class="tail"><div class="tail-h">Questions</div><ul>{rows}</ul></div>')

    if report_json.get("tail_degraded"):
        pieces.append('<div class="degraded">tail_degraded = true</div>')
    return "".join(pieces)


def _render_chat(chat: list[dict]) -> str:
    if not chat:
        return '<div class="nochat">No follow-up conversation.</div>'
    bubbles = []
    for m in chat:
        role = m.get("role", "?")
        cls = "user" if role == "user" else "assistant"
        who = "Runner" if role == "user" else "Coach"
        bubbles.append(
            f'<div class="bubble {cls}"><div class="who">{who}'
            f'<span class="ts">{_fmt_dt(m.get("created_at"))}</span></div>'
            f'<div class="msg">{_paras(m.get("content",""))}</div></div>'
        )
    return f'<div class="chat">{"".join(bubbles)}</div>'


def _render_checkin(ci: dict | None) -> str:
    if not ci:
        return ""
    bits = []
    if ci.get("rpe") is not None:
        bits.append(f"RPE {ci['rpe']}")
    if ci.get("pain_score") is not None:
        loc = f" ({ci['pain_location']})" if ci.get("pain_location") else ""
        bits.append(f"pain {ci['pain_score']}{loc}")
    if ci.get("sleep_quality") is not None:
        bits.append(f"sleep {ci['sleep_quality']}")
    head = " · ".join(bits) if bits else "check-in"
    notes = f'<div class="ci-notes">{_esc(ci["notes"])}</div>' if ci.get("notes") else ""
    return f'<div class="checkin"><span class="ci-h">Check-in:</span> {_esc(head)}{notes}</div>'


def _render_notes(entry: dict | None) -> str:
    if not entry:
        return ""
    flags = "".join(f'<span class="flag">{_esc(f)}</span>' for f in entry.get("flags", []))
    note = _paras(entry.get("note", ""))
    return f'<div class="notes"><div class="notes-h">Notes for you {flags}</div>{note}</div>'


_SEVERITIES = ("critical", "concern", "minor", "strength")


def _apply_highlights(report_html: str, chat_html: str, highlights: list, label: str) -> tuple[str, str]:
    """Wrap reviewer-chosen quotes in the report/chat with a severity <mark>. Each
    highlight is {quote, severity, why}; the quote must be a verbatim, single-
    paragraph substring of the coach report or a chat message. A miss (quote not
    found in either) is warned to stderr so typos surface at render time."""
    for h in highlights or []:
        quote = h.get("quote", "")
        if not quote:
            continue
        needle = _esc(quote)
        sev = h.get("severity", "concern")
        if sev not in _SEVERITIES:
            sev = "concern"
        mark = f'<mark class="sev-{sev}" title="{_esc(h.get("why", ""))}">{needle}</mark>'
        if needle in report_html:
            report_html = report_html.replace(needle, mark, 1)
        elif needle in chat_html:
            chat_html = chat_html.replace(needle, mark, 1)
        else:
            sys.stderr.write(f"[highlight miss] {label}: {quote[:70]!r}\n")
    return report_html, chat_html


# The runner-memory section field order (ADR 0025 / RunnerMemoryProfile).
_MEMORY_SECTIONS = [
    ("who_you_are", "Who you are"),
    ("limits_and_constraints", "Limits & constraints"),
    ("goals_and_plans", "Goals & plans"),
    ("what_works_for_you", "What works for you"),
    ("lately", "Lately"),
]


def _render_memory(context_pack: dict | None) -> str:
    """Point-in-time memory the coach held when it wrote this report, read from
    the stored context_pack. v13+/lean_v1 carry a structured `memory` section;
    pre-v13 packs carry the retired narrative/believed_facts/preference_profile
    trio instead. This is the ONLY historical record of runner memory (the
    RunnerMemory table is rebuilt-whole and never versioned)."""
    if not isinstance(context_pack, dict):
        return ""
    mem = context_pack.get("memory")
    body = ""
    if isinstance(mem, dict):
        rows = []
        for key, label in _MEMORY_SECTIONS:
            vals = mem.get(key) or []
            if not vals:
                continue
            items = "".join(f"<li>{_esc(v)}</li>" for v in vals)
            rows.append(f'<div class="mem-sec"><span class="mem-k">{label}</span><ul>{items}</ul></div>')
        prov = []
        if mem.get("source_report_count") is not None:
            prov.append(f"from {mem['source_report_count']} reports")
        if mem.get("last_updated_days_ago") is not None:
            prov.append(f"updated {mem['last_updated_days_ago']}d ago")
        prov_html = f'<div class="mem-prov">{_esc(" · ".join(prov))}</div>' if prov else ""
        body = "".join(rows) + prov_html
        title = "Memory the coach saw going in (runner memory)"
    else:
        # Legacy (pre-v13) memory shapes.
        legacy = {k: context_pack.get(k) for k in ("narrative", "believed_facts", "preference_profile")}
        legacy = {k: v for k, v in legacy.items() if v}
        if not legacy:
            return ""
        body = f'<pre class="mem-raw">{_esc(json.dumps(legacy, indent=2))}</pre>'
        title = "What the coach remembered at the time (legacy: narrative / beliefs / preferences)"

    if not body:
        return ""
    return (
        f'<details class="memory"><summary>{title}</summary>'
        f'<div class="mem-body">{body}</div></details>'
    )


def _mem_of(context_pack: dict | None) -> dict | None:
    """The structured `memory` section of a pack, or None (pre-v13 has no memory)."""
    if isinstance(context_pack, dict):
        mem = context_pack.get("memory")
        if isinstance(mem, dict):
            return mem
    return None


def _mem_sections(mem: dict) -> dict:
    """Normalise a memory-ish dict (pack section OR stored profile) to {section: [lines]}."""
    return {key: [str(x) for x in (mem.get(key) or [])] for key, _ in _MEMORY_SECTIONS}


def _render_memory_change(saw: dict | None, after: dict | None) -> str:
    """Diff the memory the report SAW against what it BECAME after (line-level per
    section: removed = dropped/reworded away, added = new/reworded in). `after` is
    the next-newer report's view — i.e. the rebuild this report's completion
    produced (memory is rebuilt whole after each report), or the current stored
    profile for the newest report."""
    if not isinstance(saw, dict) or not isinstance(after, dict):
        return ""
    s, a = _mem_sections(saw), _mem_sections(after)
    rows = []
    changed = False
    for key, label in _MEMORY_SECTIONS:
        removed = [x for x in s[key] if x not in a[key]]
        added = [x for x in a[key] if x not in s[key]]
        if not removed and not added:
            continue
        changed = True
        lis = "".join(f'<li class="rm">− {_esc(x)}</li>' for x in removed)
        lis += "".join(f'<li class="add">+ {_esc(x)}</li>' for x in added)
        rows.append(f'<div class="mem-sec"><span class="mem-k">{label}</span><ul class="diff">{lis}</ul></div>')

    sc, ac = saw.get("source_report_count"), after.get("source_report_count")
    prov = f'<div class="mem-prov">source reports {_esc(sc)} → {_esc(ac)}</div>' if (sc or ac) else ""
    caption = ('<div class="mem-note">Memory is rewritten from scratch each report, so a reworded '
               'line shows as a −/+ pair; watch for genuinely new or dropped facts.</div>')
    body = (caption + "".join(rows) + prov) if changed else '<div class="mem-nochange">No change to the memory profile after this report.</div>'
    return (
        '<details class="memchange"><summary>How memory changed after this report &amp; conversation</summary>'
        f'<div class="mem-body">{body}</div></details>'
    )


def render_html(snapshot: dict, notes: dict) -> str:
    per_activity = notes.get("per_activity", {})
    summary = notes.get("summary", "")
    current_memory = snapshot.get("current_memory", {})

    # After-chain: memory is rebuilt whole after each report, so the profile a
    # report produced == the profile the NEXT-newer report saw. Items are
    # newest-first; walking them, each memory-bearing report's "after" is the saw
    # of the previous (newer) memory-bearing report — seeded, for the newest, with
    # the current stored profile. Kept per-user so a multi-user pull stays correct.
    after_by_aid: dict = {}
    prev_saw_by_user: dict = {}
    for item in snapshot["items"]:
        uid = str(item["activity"].get("user_id"))
        saw = _mem_of((item.get("report") or {}).get("context_pack"))
        if saw is None:
            continue
        after_by_aid[item["activity"]["id"]] = prev_saw_by_user.get(uid, current_memory.get(uid))
        prev_saw_by_user[uid] = saw

    cards = []
    for item in snapshot["items"]:
        a = item["activity"]
        r = item["report"]
        rj = (r or {}).get("report") or {}
        aid = a["id"]
        badge_fb = '<span class="badge fb">FALLBACK</span>' if (r and r.get("is_fallback")) else ""
        prompt = _esc(r["prompt_id"]) if r else "-"
        meta_line = (
            f'{_esc(a["type"])} · {_fmt_km(a["distance_m"])} · {_fmt_dur(a["moving_time_s"])}'
            f' · avg HR {_esc(a["avg_hr"]) or "-"} · {_fmt_dt(a["start_date"])}'
        )
        chat_n = len(item["chat"])
        entry = per_activity.get(str(aid)) or {}
        report_html = f'<div class="report">{_render_report_body(rj)}</div>'
        chat_html = _render_chat(item["chat"])
        report_html, chat_html = _apply_highlights(
            report_html, chat_html, entry.get("highlights"), f'{a["start_date"][:10]} {a["name"]}'
        )
        cards.append(
            f"""
        <section class="card" id="a-{_esc(aid)}">
          <div class="card-head">
            <div>
              <div class="title">{_esc(a["name"])} {badge_fb}</div>
              <div class="meta">{meta_line}</div>
            </div>
            <div class="prompt-badge">{prompt}<span class="chatn">{chat_n} chat msgs</span></div>
          </div>
          {_render_notes(entry)}
          {_render_checkin(item["checkin"])}
          {_render_memory((r or {}).get("context_pack"))}
          {report_html}
          {chat_html}
          {_render_memory_change(_mem_of((r or {}).get("context_pack")), after_by_aid.get(aid))}
        </section>"""
        )

    summary_html = f'<div class="summary">{_paras(summary)}</div>' if summary else ""
    generated = snapshot.get("pulled_at", "")
    n = snapshot.get("count", len(snapshot["items"]))

    return f"""<meta charset="utf-8">
<title>Coach Report & Conversation Review</title>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  margin: 0; line-height: 1.5; background: #f4f4f5; color: #18181b; }}
.wrap {{ max-width: 900px; margin: 0 auto; padding: 32px 20px 80px; }}
h1 {{ font-size: 1.6rem; margin: 0 0 4px; }}
.sub {{ color: #71717a; font-size: 0.9rem; margin-bottom: 10px; }}
.legend {{ color: #52525b; font-size: 0.8rem; margin-bottom: 12px; padding: 10px 12px;
  background: #ececee; border-radius: 8px; }}
.sevkey {{ display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 0.78rem; color: #52525b;
  margin-bottom: 24px; padding: 0 2px; }}
mark {{ padding: 0 3px; border-radius: 3px; color: inherit; }}
mark.sev-critical {{ background: #fecaca; box-shadow: inset 0 -2px 0 #dc2626; }}
mark.sev-concern {{ background: #fed7aa; box-shadow: inset 0 -2px 0 #ea580c; }}
mark.sev-minor {{ background: #e2e8f0; box-shadow: inset 0 -2px 0 #94a3b8; }}
mark.sev-strength {{ background: #bbf7d0; box-shadow: inset 0 -2px 0 #16a34a; }}
.summary {{ background: #fef9c3; border: 1px solid #fde047; border-radius: 10px;
  padding: 16px 18px; margin-bottom: 28px; }}
.summary p {{ margin: 0 0 8px; }}
.card {{ background: #fff; border: 1px solid #e4e4e7; border-radius: 12px;
  padding: 20px; margin-bottom: 22px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
.card-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
  border-bottom: 1px solid #f0f0f0; padding-bottom: 12px; margin-bottom: 12px; }}
.title {{ font-weight: 650; font-size: 1.1rem; }}
.meta {{ color: #71717a; font-size: 0.82rem; margin-top: 3px; }}
.prompt-badge {{ font-family: ui-monospace, Menlo, monospace; font-size: 0.72rem;
  color: #3f3f46; background: #f4f4f5; border-radius: 6px; padding: 4px 8px; text-align: right;
  white-space: nowrap; }}
.chatn {{ display: block; color: #a1a1aa; margin-top: 3px; }}
.badge.fb {{ background: #fee2e2; color: #b91c1c; font-size: 0.62rem; padding: 2px 6px;
  border-radius: 5px; vertical-align: middle; }}
.headline {{ font-weight: 650; font-size: 1.02rem; margin-bottom: 8px; }}
.stage-label {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: #a1a1aa; margin: 10px 0 2px; }}
.prose p {{ margin: 0 0 10px; }}
.tail {{ margin-top: 12px; }}
.tail-h {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: #71717a;
  margin-bottom: 4px; }}
.tail ul {{ margin: 0; padding-left: 20px; }}
.tail li {{ margin-bottom: 4px; font-size: 0.9rem; }}
.why {{ color: #a1a1aa; }}
.degraded {{ color: #b45309; font-size: 0.75rem; margin-top: 8px; }}
.checkin {{ background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px;
  padding: 8px 12px; font-size: 0.85rem; margin-bottom: 12px; }}
.ci-h {{ font-weight: 600; }}
.ci-notes {{ margin-top: 4px; color: #1e40af; font-style: italic; }}
.notes {{ background: #f0fdf4; border: 1px solid #86efac; border-left: 4px solid #22c55e;
  border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; }}
.notes-h {{ font-weight: 650; font-size: 0.85rem; margin-bottom: 6px; }}
.flag {{ background: #dc2626; color: #fff; font-size: 0.62rem; padding: 2px 6px;
  border-radius: 5px; margin-left: 6px; letter-spacing: 0.03em; }}
.memory {{ background: #faf5ff; border: 1px solid #e9d5ff; border-radius: 8px;
  padding: 8px 12px; margin-bottom: 12px; font-size: 0.85rem; }}
.memory summary {{ cursor: pointer; font-weight: 600; color: #7e22ce; }}
.mem-body {{ margin-top: 8px; }}
.mem-sec {{ margin-bottom: 6px; }}
.mem-k {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; color: #a855f7; }}
.mem-sec ul {{ margin: 2px 0 0; padding-left: 18px; }}
.mem-prov {{ color: #a1a1aa; font-size: 0.72rem; margin-top: 6px; }}
.mem-raw {{ white-space: pre-wrap; font-size: 0.75rem; color: #6b21a8; margin: 0; }}
.memchange {{ background: #f8fafc; border: 1px solid #e2e8f0; border-left: 4px solid #64748b;
  border-radius: 8px; padding: 8px 12px; margin-top: 14px; font-size: 0.85rem; }}
.memchange summary {{ cursor: pointer; font-weight: 600; color: #475569; }}
.memchange .mem-body {{ margin-top: 8px; }}
ul.diff {{ list-style: none; margin: 2px 0 0; padding-left: 4px; }}
ul.diff li {{ font-family: ui-monospace, Menlo, monospace; font-size: 0.78rem; }}
ul.diff li.add {{ color: #15803d; }}
ul.diff li.rm {{ color: #b91c1c; }}
.mem-nochange {{ color: #71717a; font-style: italic; }}
.mem-note {{ color: #94a3b8; font-size: 0.72rem; margin-bottom: 6px; }}
.chat {{ margin-top: 14px; border-top: 1px dashed #e4e4e7; padding-top: 14px; }}
.bubble {{ margin-bottom: 10px; max-width: 85%; }}
.bubble.user {{ margin-left: auto; }}
.bubble .who {{ font-size: 0.7rem; color: #71717a; margin-bottom: 2px; }}
.bubble .ts {{ margin-left: 6px; color: #c4c4c8; }}
.bubble .msg {{ padding: 9px 13px; border-radius: 12px; font-size: 0.9rem; }}
.bubble.user .msg {{ background: #dbeafe; }}
.bubble.assistant .msg {{ background: #f4f4f5; }}
.bubble .msg p {{ margin: 0 0 6px; }}
.bubble .msg p:last-child {{ margin-bottom: 0; }}
.nochat {{ color: #a1a1aa; font-size: 0.85rem; font-style: italic; margin-top: 10px; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #0a0a0b; color: #e4e4e7; }}
  .card {{ background: #18181b; border-color: #27272a; box-shadow: none; }}
  .card-head {{ border-color: #27272a; }}
  .meta, .sub, .chatn, .why {{ color: #a1a1aa; }}
  .legend {{ background: #18181b; color: #a1a1aa; }}
  .sevkey {{ color: #a1a1aa; }}
  mark.sev-critical {{ background: rgba(220,38,38,0.38); }}
  mark.sev-concern {{ background: rgba(234,88,12,0.38); }}
  mark.sev-minor {{ background: rgba(100,116,139,0.42); }}
  mark.sev-strength {{ background: rgba(22,163,74,0.38); }}
  .prompt-badge {{ background: #27272a; color: #d4d4d8; }}
  .summary {{ background: #292524; border-color: #57534e; }}
  .checkin {{ background: #172554; border-color: #1e40af; }}
  .ci-notes {{ color: #93c5fd; }}
  .memory {{ background: #1e1030; border-color: #4c1d95; }}
  .memory summary {{ color: #c084fc; }}
  .mem-k {{ color: #c084fc; }}
  .mem-raw {{ color: #d8b4fe; }}
  .memchange {{ background: #0f172a; border-color: #334155; border-left-color: #64748b; }}
  .memchange summary {{ color: #94a3b8; }}
  ul.diff li.add {{ color: #4ade80; }}
  ul.diff li.rm {{ color: #f87171; }}
  .notes {{ background: #052e16; border-color: #166534; border-left-color: #22c55e; }}
  .bubble.user .msg {{ background: #1e3a5f; }}
  .bubble.assistant .msg {{ background: #27272a; }}
  .badge.fb {{ background: #450a0a; color: #fca5a5; }}
}}
</style>
<div class="wrap">
  <h1>Coach Report &amp; Conversation Review</h1>
  <div class="sub">{n} activities · pulled {_esc(generated)}</div>
  <div class="legend">Each card shows, in order: the <b>memory the coach saw going in</b> (purple),
  the report, the conversation that followed, then <b>how memory changed after</b> (grey diff).
  Memory is rebuilt whole after each report, so "after" is the profile the next report saw
  (the current stored profile for the newest); the diff is what this report + conversation changed.</div>
  <div class="sevkey">
    <span class="sk"><mark class="sev-critical">Critical</mark> wrong, misleading, or trust-breaking</span>
    <span class="sk"><mark class="sev-concern">Concern</mark> questionable judgment, stale, or repetitive</span>
    <span class="sk"><mark class="sev-minor">Minor</mark> small nit</span>
    <span class="sk"><mark class="sev-strength">Strength</mark> coaching at its best, protect this</span>
  </div>
  {summary_html}
  {"".join(cards)}
</div>"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-url", default=os.environ.get("SEED_SOURCE_URL"))
    p.add_argument("--activities", type=int, default=30, help="Most recent N activities with a report.")
    p.add_argument("--out-dir", default=str(BACKEND_DIR.parent / "docs" / "audit"))
    p.add_argument("--from-json", help="Skip the pull; re-render from an existing snapshot JSON.")
    p.add_argument("--notes", help="Optional notes JSON to fold into the render.")
    p.add_argument("--tag", default=date.today().isoformat(), help="Filename date tag.")
    args = p.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.from_json:
        snapshot = json.loads(Path(args.from_json).read_text())
        json_path = Path(args.from_json)
    else:
        if not args.source_url:
            raise SystemExit("No source URL. Set $SEED_SOURCE_URL or pass --source-url.")
        snapshot = pull(args.source_url, args.activities)
        json_path = out_dir / f"coach-review-{args.tag}.json"
        json_path.write_text(json.dumps(snapshot, indent=2, default=str))
        print(f"Wrote snapshot: {json_path}  ({snapshot['count']} activities)")

    notes = {}
    if args.notes:
        notes = json.loads(Path(args.notes).read_text())

    html_path = out_dir / f"coach-review-{args.tag}.html"
    html_path.write_text(render_html(snapshot, notes))
    print(f"Wrote HTML: {html_path}")


if __name__ == "__main__":
    main()
