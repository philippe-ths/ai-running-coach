// #995: the wall-clock ceiling a coach turn lives inside, in one place.
//
// A turn is generated on Railway but reaches the runner through the Vercel
// function in `app/api/[...path]/route.ts`, and that function is terminated at
// `maxDuration` seconds whatever the backend is still doing. This deployment is
// on Vercel Hobby, where 60 is the hard maximum rather than a tunable, so the
// work has to fit the ceiling — the ceiling cannot be raised to fit the work.
//
// The backend holds the same number as `TURN_BUDGET_SECONDS` and sizes its own
// generation against it. `backend/tests/test_turn_budget_995.py` reads both
// files and fails when they disagree, because the comment that used to assert
// they agreed ("the underlying LLM call is bounded well below this") stayed
// green for the three days after #989 made it false.
//
// `maxDuration` itself is still written as a literal in the route file: Next
// requires that export to be statically analyzable, and an imported constant is
// not. The test is what keeps the literal honest.
export const TURN_MAX_DURATION_SECONDS = 60;

// What the browser waits before giving up on a stalled turn. Deliberately
// LONGER than the function ceiling: the platform kill is the intended stop, and
// this only catches the case where severing leaves the socket open with no
// further bytes, which otherwise spins forever.
export const CLIENT_TURN_TIMEOUT_MS = (TURN_MAX_DURATION_SECONDS + 10) * 1000;
