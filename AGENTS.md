# Repository Guidelines

## Project Structure & Module Organization

Core code lives in `src/codelens`. The main CLI entrypoint is `src/codelens/__main__.py`, exposed as `codelens`. Indexing code sits under `src/codelens/indexing`, retrieval code under `src/codelens/retrieval`, and SQLite-backed persistence under `src/codelens/db` and `src/codelens/repository`. Tests live in `tests`, with longer environment-dependent coverage in `tests/integration`. Keep new modules close to the feature they support.

## Build, Test, and Development Commands

Use `uv` for local work.

- `uv sync --dev` installs runtime and test dependencies.
- `uv run pytest` runs the full test suite.
- `uv run pytest --cov=codelens` runs tests with coverage.
- `uv run pytest tests/integration` runs integration tests. Some cases skip unless you have exported Gradle workspace metadata and related local fixtures.
- `uv run codelens index status --repo-root /path/to/repo` checks the local index state.

The app creates local SQLite state during indexing, so use a disposable repo path while testing CLI changes.

## Coding Style & Naming Conventions

Follow the existing Python style in `src/`: 4-space indentation, type hints where they help, and small focused functions. Use `snake_case` for modules, functions, and test files like `test_workspace_runtime.py`. Use `PascalCase` for classes. Prefer explicit imports from local packages over wildcard imports. No formatter or linter is wired into `pyproject.toml` yet, so match the surrounding code before introducing new style tooling.

## Testing Guidelines

Write unit tests beside similar files in `tests/` and name them `test_<feature>.py`. Use `pytest` fixtures and `monkeypatch` for CLI and service isolation. Add integration tests only when behavior depends on real workspace layout, JDK classes, jars, or Gradle export data. Run `uv run pytest --cov=codelens` before opening a PR.

## Commit & Pull Request Guidelines

Recent history uses short Conventional Commit prefixes like `feat:` and `chore:`. Keep commit subjects imperative and specific, for example `feat: add workspace index cache invalidation`. For PRs, include a short summary, linked issue if there is one, test results, and sample CLI output when user-facing behavior changes.

---

# Writing Rules

Write like you're talking to a smart friend. If you wouldn't say it in conversation, don't write it.

## Keep it simple

- Short sentences. One thought per sentence.
- Cut every word that doesn't earn its place. "He was happy" not "He was very happy."
- A good argument in five sentences beats a brilliant one in a hundred.

## Sound like a person

- Use "write" not "pen." Use "use" not "utilize." Use "help" not "facilitate."
- No corporate speak. No filler phrases. No throat-clearing.
- Read it back. If it sounds stiff, rewrite it the way you'd actually say it.

## Structure for how brains work

- Active voice over passive. "The boy hit the ball" not "The ball was hit by the boy."
- Put the subject before the action. Readers imagine the actor first.
- Lead with the interesting part. Your first sentence should make people want the second one.

## Don't assume — flag it

- If you're guessing something about my setup, intent, or context, say so. Don't silently bake assumptions into the answer.
- Format assumptions clearly so I can spot and correct them fast.
- Example: "I'm assuming you're using PostgreSQL here. If you're on MySQL, the syntax changes to X."
- Wrong: silently writing Postgres-specific SQL without telling me.
- Right: "Assuming you want this in Python 3.11+ since you mentioned match statements. If you're on an older version, here's the alternative."

## Explain like I'm seeing this for the first time

- Don't skip the "why." If you suggest something, tell me why that approach over the alternatives.
- Use a simple **what → why → how** flow:
  - **What** — what are we doing, in one sentence.
  - **Why** — why this approach. What problem does it solve. What breaks without it.
  - **How** — the actual implementation or steps.
- Example: "**What:** We add an index on `user_id`. **Why:** Your query scans the full table right now — an index turns that from O(n) to O(log n). **How:** `CREATE INDEX idx_user_id ON orders(user_id);`"
- Don't assume I know the jargon. If a term isn't obvious, explain it inline in plain English.

## What this means in practice

- Don't pad responses to seem thorough. Shorter is almost always better.
- Don't hedge with unnecessary qualifiers. Say what you mean.
- If an idea is complex, use simpler language — not fancier language.
- Informal language is the athletic clothing of ideas. The harder the topic, the less you can afford to let language get in the way.
