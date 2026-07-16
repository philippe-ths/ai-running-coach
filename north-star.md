# The North Star (coach decisions)

Every decision about what the coach LLM receives or is told — a context-pack field, a prompt clause, a new signal, the way a number is framed — passes two questions before any mechanical one:

1. **What does a good coach actually need here?** Feed the coach the read that serves the runner, not everything that happens to be computable. A good coach's judgment is already latent in the model; give it the signal, not a raw dump.
2. **Could an LLM misread it?** Whatever is ambiguous will eventually be read wrong. Frame, label, or tier a signal so its meaning is unmistakable — and prefer fixing the framing over withholding the fact.

This is a test, not a rulebook. It sits *above* the specific coaching rules — the prompt disciplines, the policy validator, the evals — which enforce the particular cases it implies. A change can pass every mechanical check and still be coach-wrong; when it fails either question, it is wrong, however green the tests.

*The hard case.* `effort_score` is cumulative training LOAD — it grows with duration, so a long easy run scores high. Handed to the coach raw it fails both questions at once: a good coach does not need a bare load number, and an LLM reads "high load" as "high intensity." The fix is not to hide the number but to frame it — labelled as load, with the intensity read taken from the effort axis and RPE. That is the #168 discipline, and the pattern generalizes to every signal the coach sees: name what it means, or the model will guess.
