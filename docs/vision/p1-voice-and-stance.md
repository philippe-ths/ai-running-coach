# P1: Voice + Coaching Stance — exploration decisions

Status: drafted 2026-06-13 from an open design session (exploratory, not yet a brief or ADR). Captures the decisions reached so we don't lose them; several leaf-questions remain open. Sits under the north-star (`coach-north-star.md`, epic #177); glossary terms (`Voice`, `Coaching stance`, `Coaching corpus`, `User materials`, `Authority tiering`) live in `CONTEXT.md` and constrain everything here.

Production runs `coach_message_v2`. Phase 0 and Phase 1 (A1-A4) and I1 are shipped. This is the first Phase-2 work.

---

## 1. The Voice philosophy (the spine for this feature)

1. **One expressive space.** "Delight" (entertaining personality) and "fit" (pitched to this runner type) are not competing ambitions; they are the same axis. Different runners occupy different *regions* of one expressive space. The graph is both a costume and a mirror.
2. **Runner-sovereign over voice.** The runner sets the voice, owns it, and re-dials it any time. The system does not second-guess their chosen tone. ("Trust the user.")
3. **Floor invariant beneath voice.** Facts, the data the coach grounds on, and the safety floor never flex, regardless of dial setting. Roast mode and warm mode deliver the *same facts and same safety floor*, differently. This makes voice-independence of the safety floor a **hard requirement we must verify, not assume**, because we have given up the coach's ability to "pull a punch" for the runner's own good.
4. **Refinement = the runner turning the knob, plus the coach noticing out loud.** There is no secret LLM tone-inference (it would violate the standing deterministic-write-back rule, and there is no ground-truth signal for "did a blunt voice land?"). The coach *may* raise the voice question explicitly ("I've been blunt lately, want me to ease off?") and let the runner decide. This replaces the previously-deferred "P1b adaptive refinement"; under trust-the-user, secret refinement is the *wrong* feature, not merely a deferred one.

Consequence: the cautious-beginner risk ("picks savage roast, gets torn down, quits") is handled by the **default** and the **onboarding path** (start moderate, lead them outward), not by the coach overriding their choice.

## 2. Representation: the decoupled graph

- **Few operable axes, many descriptive.** The runner controls and the prompt consumes a small set of axes (the four below). A richer set of literary/descriptive axes lives as preset DNA and flavour copy, never as user-facing sliders.
- The **personality-graph** is allowed to *render* richer than the controls. We accept that the visualization is partly expressive rather than a 1:1 readout of operable state. (How loudly to admit this in the UI is open.)
- A point is placed mostly by **choosing a preset**, then nudging the few real axes; a **free-text escape-hatch** sits on top.

## 3. The four Voice dials

Operable axes the runner sets and the prompt consumes. Pole pairs double as graph axis labels (low pole = more reserved, high pole = more expressive).

1. **Clinical ↔ Warm** — how much it cares out loud.
2. **Earnest ↔ Playful** — how much it jokes (roast lives at the far Playful end).
3. **Gentle ↔ Blunt** — how hard it tells you the truth (absorbs Candour + Tact).
4. **Calm ↔ Fired-up** — how much energy it brings.

Dropped as operable: Formality (derivable from the other four), Practicality/Creativity (more content than voice). "Data vs sentiment" was *parked* — it is not Voice (it is *what* the coach focuses on), so it belongs to Coaching stance.

## 4. Preset cast (the concrete test/ship artifact)

House-original presets (not impersonations). Six presets: **3 strong-but-livable** (daily drivers with real character) and **3 extremes** (the stress-test corners, deliberately three *kinds* of extreme: loud-harsh, loud-funny, flat-minimal). Names are working titles. Dials 1-5 from low pole to high pole.

Strong-but-livable:
- **The Sage** — Warmth 4 / Humor 2 / Directness 2 / Energy 1. Quiet, patient mentor; wisdom in few words.
- **The Cornerman** — 5 / 3 / 2 / 4. In your corner with drive; encouraging without drowning you.
- **The Analyst** — 2 / 2 / 4 / 2. Cool, precise, data-forward; honest, never cruel.

Extremes:
- **The Drill Sergeant** — 1 / 1 / 5 / 5. Pure demand; no jokes, no cushion. (loud-harsh)
- **The Roast** — 3 / 5 / 5 / 5. Relentless irreverence; makes you laugh and flinch. (loud-funny)
- **The Deadpan** — 1 / 4 (dry) / 4 / 1. Flat, unbothered, minimal; accidental wisdom between shrugs. (flat-minimal)

**Default coach voice** sits separate at the moderate centre: roughly Warm 4 / Earnest 2 / Gentle 2 / Calm 3. Presets are where you *go*, not where you *start*.

## 5. IP / naming decision

Ship **house-original presets that evoke archetypes**, plus a **free-text escape-hatch** for named characters. A runner typing "talk to me like Dr House" is *their* untrusted input we reason about for tone; *us* shipping a "Dr House" button would be the product impersonating trademarked IP at commercial scale. The runner still gets any character they want; we do not ship the impersonation. (The owner's named examples — House, Oogway, Saitama, Whistledown, Sparrow, Archer, Teddy — were illustrative; the design goal is distinct, varied presets, not resemblance.)

## 6. Coaching stance

- Stance bundles two things: *what the coach emphasises* and *its training philosophy* (schools of thought).
- **Decision: ship full philosophy in P1 via a small retrievable library** (not just emphasis dials, and not hardcoded prompt presets). This extends the existing `retrieval.py` pull-on-demand seam rather than inventing infrastructure.
- This pulls the core of the P2 **Coaching corpus** forward, and drags **Authority tiering** with it. P1 needs only the *partial* tier: **safety and data outrank philosophy.** The hardest tier (user materials beat house philosophy) needs **User materials** (P4), which still does not exist, so it stays deferred.
- Stance stays **goal-tethered**: philosophy reweights emphasis and method-framing but never licenses unsupported advice or overrides the runner's real goal or the measured data.
- **Emphasis axes (two), the operable half of stance:** **Data ↔ Sentiment** (lead with the numbers vs lead with how it felt) and **Process ↔ Outcome** (foreground habits/execution vs results/PRs/goals). Both are *content-focus*, not tone, so they stay orthogonal to the Voice dials (a Blunt voice can foreground encouragement; a Warm voice can be accountability-heavy). Together they span the five-runner scenarios. **Accountability ↔ Encouragement was deliberately NOT given its own axis** — it blurs into Warmth/Directness and is reachable via warm voice + sentiment emphasis.

## 7. Re-slice of P1 (by deliverable, dependency-ordered)

Choosing a retrievable library made "P1" phase-sized. Re-cut into three sequential milestones, one concern / one PR each:

1. **Voice dials** — declared dials, prompt injection so the coach talks in the declared voice, the 4-pole personality graph, preset cast, free-text escape-hatch. No corpus dependency; fastest visible win; ships first and alone.
2. **Corpus substrate** — stored + retrieved philosophy library, partial authority tiering (safety/data > philosophy).
3. **Stance dials** — philosophy selection consuming the corpus, plus the emphasis axes (data ↔ sentiment, accountability ↔ encouragement).

(The roadmap in `coach-north-star.md` §5 needs updating to reflect this re-cut when we converge.)

---

## Open leaf-questions (deferred, not lost)

- **Personality-graph UI/UX** — RESOLVED at the metaphor level: dials/preset are the control, a **radar chart is the expressive mirror** (precedented by Convai), a point is placed by preset then nudged via the four dials. Axis order is a deliberate build-time choice (ordering changes the shape). Remaining detail is build-time. Parked candidate (not adopted): a **live sample message** that re-renders the coach's words in the chosen voice as a dial moves — strong "feels alive" payoff and aligned with the example-messages finding, but would need pre-rendered or generated samples; revisit during milestone 1.
- **Prompt injection form** — inject dial *values* vs rendered persona *prose*; how the four dials + selected preset + free-text become a coherent prompt block in `prompts.py` (note the Vn = V(n-1) + addendum idiom; Voice today is a fixed implicit persona in `SYSTEM_PROMPT_MESSAGE_V2*`).
- **Stance emphasis axes** — RESOLVED: two axes, Data↔Sentiment and Process↔Outcome (see §6). Coexistence with the philosophy library is build-time.
- **Corpus shape** — retrieval mechanism (keyed-by-selected-school vs semantic), how many philosophies, where stored, schema.
- **Authority tiering enforcement** — prompt rule vs code; how "goal-tethered" is enforced.
- **Onboarding flow** — where declaration lives (likely extends `frontend/app/profile/page.tsx`); `coaching_relationship` table ALTERs to hold voice/stance state (currently just id/user_id/created_at).
- How many of the ~20 literary descriptive axes to actually author, and their role in preset DNA / flavour copy.

## Research findings (deep-research, 2026-06-13; manual synthesis — workflow hit session limit at synthesis)

Caveat: synthesis was done by hand from 15 adversarially-verified claims; several radar-chart claims are credible-but-unverified (verifiers abstained at the limit, not refuted).

**Q1 persona-config UX.** Two shipped patterns, and our design is a hybrid of both. Kindroid = structured free-text fields incl. an `example_message` "defining the AI's voice" (kindroid.ai). Convai = 5 bipolar Big-Five-style trait dimensions (high/low poles each) rendered as a **radar chart** (docs.convai.com). Near-direct precedent for our 4 bipolar dials + graph + free-text escape-hatch. (Refuted: that Convai uses discrete 0-4 sliders specifically; dial mechanism unconfirmed.)

**Q2 visualization.** Radar is the precedented idiom (Convai). Confirmed (3-0): axis *ordering* materially changes a radar's shape — same data reads smooth or spiky by sequence alone (observablehq.com). Credible-but-unverified: radar favors holistic shape over precise readout; area/peaks can mislead; 5-8 axes optimal; bars clearer for exact magnitude (datawrapper.de, highcharts.com). Vindicates decision §2 (graph is expressive, not a 1:1 readout). Axis order is a deliberate design choice.

**Q3 prompt-level steering (biggest payload).** Few-shot *example outputs* are the most effective steering method, beating instructions alone (arxiv 2510.04484). Numeric trait values embedded in NL prompts work without fine-tuning (Big5-Scaler, arxiv 2508.06149). Intensity is poorly controllable; concise prompts + *lower* intensities are most reliable — extremes steer worst (arxiv 2510.04484, 2508.06149). Steerability is asymmetric/baseline-skewed; some traits resist elicitation (arxiv 2411.12405, 2406.14703). Refuted: that narrative personas beat trait-name specs (prose-vs-values is a wash).

**Q4 corpus retrieval.** Confirmed (3-0): keyword/lexical search + agentic tool use reaches RAG-level performance with no vector DB (arxiv 2602.23368). Since the runner *selects* the school via the stance dial, keyed lookup suffices — no embeddings.

### What the findings settle (updating the open questions above)

- **Prompt-injection form (was open):** inject **dial values (numeric, prose-rendered is a wash) PLUS 1-2 few-shot example messages written in that voice** per preset. Example messages are the highest-leverage ingredient and carry the extreme presets (which dial-magnitude cannot reliably reach).
- **Corpus shape (was open):** **keyed lookup over a small house-authored library, riding the existing `retrieval.py` seam, no vector DB.**
- **Expectation reset:** intensity and extremes steer unreliably and asymmetrically. The graph is feel-not-readout (a dial at 5 won't reliably *feel* like 5); example messages do the heavy lifting; the three extreme presets are the hardest to land and lean hardest on examples.
