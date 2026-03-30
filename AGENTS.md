---
applyTo: "**"
---
# Coding Standards

## Branch Discipline
- NEVER commit or push directly to `master` or `main`
- Always work on a feature or fix branch

## Code Hygiene
- Always scope code — no globals, no leaking state
- Keep functions and modules focused on a single responsibility

## Testing & Validation
- Test all changes before considering them done
- Never mock API calls or create dummy/stub data — if an API is unavailable, fail loudly with a clear error
- Review your own code before committing

## Commits
- Make small, atomic commits with clear, descriptive messages
- Each commit should represent one logical change
