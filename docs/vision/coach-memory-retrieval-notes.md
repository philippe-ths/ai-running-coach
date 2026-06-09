# Memory and Data Retrieval System: design inputs (Phase 1 / A2)

Status: design input captured 2026-06-09, not yet a settled design. This is the starting point for a fresh session that will design the coach's memory-and-data-retrieval system (milestone A2 in `coach-north-star.md`). Nothing here is decided beyond what the "Settled so far" section marks.

## How to run the next session (owner preference)

Present every decision as: one short paragraph of framing, then a clear list of options, then a recommendation with rationale. Keep it plain. Do not dump unexplained terminology; define any term in plain language the first time it appears. Long jargon-heavy responses lose the owner and cause drift. This is a hard preference, recorded in memory as well.

## The reframe (the angle to design from)

Frame the system by **data type** and by **trigger point**, with the organizing principle that the hard work happens **on ingestion, in the background**, not when the coach asks. Each data type is shaped when it arrives into a form that is cheap and easy to retrieve later (the "process at ingestion so retrieval is easy" idea, attributed by the owner to Andrej Karpathy's LLM knowledge-bases). So the system is: per-data-type ingestion pipelines that pre-process and store, feeding one easy retrieval layer that the coach reads from.

## Owner thought-dump (verbatim, 2026-06-09)

> I think we need to frame this from another angle. However I'm worried that we've used too much of your context in this chat session. But I'm going to do a thought dump here now: One thing that occurs to me is the data types we have or could have. We have the raw data from strava, we have LLM responses, we have user metrics, we have User preferences, we have uploaded materials, we have LLM constructed narrative, We have summarised activity history, we have user resposes, we have a constructed memory, there could be more types I don't know yet or future types like raw health sleep data. The other thing that occurs to me is the Initialisation point, we have events like a new activity landing, or user submiting Activity Check-In, or chat in telegram, or chat with coach in app on an activity, we also could have a general chat not tied to a specific activity. It also occurs to me that some processes for data and memory can be run in the background, "Andrej Karpathy's LLM Knowledge Bases" has a layer that process on ingestion To make it easier for the LLM on retrieval. This leads me to another thought about How LLMs handle data best.

(The trailing thought, "how LLMs handle data best," was not finished and should be drawn out at the start of the next session.)

## Data types in play (owner's list, to be organised)

- Raw Strava data (activities, streams)
- LLM responses (the coach's own past output)
- User metrics (derived metrics)
- User preferences
- Uploaded materials (the runner's own coaching content)
- LLM-constructed narrative (the relationship story)
- Summarised activity history
- User responses (check-ins, chat replies)
- Constructed memory (the durable runner-model)
- Future / unknown types (e.g. raw health and sleep data)

## Trigger points (when ingestion or retrieval fires)

- A new activity landing
- User submitting an activity check-in
- Chat in Telegram
- In-app chat with the coach on a specific activity
- A general chat not tied to any activity
- (Future triggers as new data sources are added)

## Settled so far (from the design session, plain language)

- The coach is an ongoing relationship, not a per-activity report (see `coach-north-star.md`).
- Retrieval model: the coach starts with a lean summary and looks up specific detail on demand, rather than being pre-loaded with everything (Q5 in the session).
- Durable memory is split: deterministic, auditable facts (beliefs, preferences, training load) plus an LLM-written narrative that is colour, never fact (Q6).
- Output is prose first, with a thin structured tail carrying only what the system reads back later (the advice/commitments), so the learning loop keeps working (confirmed by a code audit: the belief write-back already reads only deterministic signals, but the loop reads the prior advice's structured next-steps, so that structure must survive).

## Candidate mechanics raised but NOT decided (keep plain next time)

These came up and were judged too jargon-heavy for the owner mid-session. Reintroduce only in plain language, one at a time, and only if needed:

- How the coach varies depth per run (two fixed gears vs an adaptive spending limit). Leaning: two fixed gears first.
- Letting the coach compute over raw streams in a sandbox so raw samples never fill its view (the answer to "we crush streams to one number, but raw is too bulky").
- Keeping a stable, reusable chunk of the prompt cached to save cost.
- Using a cheaper, faster model for the background ingestion/summarising work and the strongest model for the coaching message itself.
- Where the narrative gets written: by the coach itself, or by a separate background job we control.

## Open questions to resume on

1. Finish the owner's "how LLMs handle data best" thought.
2. For each data type, what does ingestion-time processing produce, and where is it stored?
3. What does the lean default summary contain, and what is left to look up on demand?
4. Which trigger points run background processing, and what does each one do?
5. How do the data types map onto the layers already named in the glossary (raw store, working context, durable memory)?
