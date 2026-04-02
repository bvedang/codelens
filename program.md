# codelens autoresearch program

This repo is a Java code indexing and retrieval tool.

It does 3 main things:

- `index`: parse a Gradle Java workspace, chunk it, embed it, and store a local FAISS + SQLite index
- `retrieve search`: rank code chunks for a natural-language query
- `retrieve read`: load one chunk and nearby context

The best tight autoresearch loop in this codebase is retrieval ranking quality.

The single highest-leverage file is:

- `src/codelens/retrieval/search.py`

That file contains the heuristic reranking logic on top of semantic retrieval.
It is also the narrowest place to improve the existing Micronaut benchmark without changing the benchmark itself.

## Setup

Do setup once before the loop starts.

### 1. Use a clean worktree

Do not run this loop in the human's active checkout.
This repo may be dirty, and the loop discards losing experiments with git.

Create a fresh worktree and branch from a clean base:

```bash
git worktree add ../codelens-autoresearch -b autoresearch/retrieval-search main
cd ../codelens-autoresearch
```

### 2. Read the in-scope files

Read these files for context before the first run:

- `src/codelens/retrieval/search.py`
- `src/codelens/retrieval/eval.py`
- `evals/README.md`
- `evals/micronaut_retrieval.json`
- `tests/test_retrieval_search.py`
- `tests/test_retrieval_eval.py`
- `RETRIEVAL_PROGRESS.md`

### 3. Install dependencies

```bash
uv sync --dev
```

### 4. Prepare the pinned benchmark repo

This loop assumes you have a separate local checkout of `micronaut-core`.
Use the pinned commit from the fixture:

- repo: `micronaut-core`
- commit: `6a11a05950f36193bd865d6b25c2bc17dfb4ff1c`

Set these paths:

```bash
export CODELENS_ROOT="$PWD"
export BENCH_REPO="/Users/vedangbarhate/Desktop/workspace/micronaut-core"
export BENCH_COMMIT="6a11a05950f36193bd865d6b25c2bc17dfb4ff1c"
export WORKSPACE_JSON="$BENCH_REPO/build/codelens/workspace.json"
export FIXTURE="$CODELENS_ROOT/evals/micronaut_retrieval.json"
export DEVICE="${DEVICE:-mps}"
```

Verify the benchmark repo is at the pinned commit:

```bash
git -C "$BENCH_REPO" rev-parse HEAD
```

If it does not match `BENCH_COMMIT`, stop and fix that first.
Do not compare scores across different Micronaut commits.

### 5. Export Gradle workspace metadata

```bash
"$BENCH_REPO/gradlew" \
  -I "$CODELENS_ROOT/gradle_export.init.gradle" \
  exportCodeLensWorkspaceModel \
  "-PcodelensOutput=$WORKSPACE_JSON"
```

### 6. Build the local retrieval index

```bash
uv run codelens index workspace \
  --repo-root "$BENCH_REPO" \
  --workspace-json "$WORKSPACE_JSON" \
  --device "$DEVICE"
```

### 7. Initialize autoresearch scratch files

Keep these untracked:

```bash
mkdir -p .autoresearch
printf "commit\tscore\tstrong\tpass\tnear\tfail\tstatus\tdescription\n" > .autoresearch/results.tsv
```

### 8. Run the baseline

The first run is always the current code as-is.
Do not edit anything before the baseline.

Run:

```bash
uv run pytest tests/test_retrieval_eval.py tests/test_retrieval_search.py

uv run python -m codelens retrieve eval \
  --repo-root "$BENCH_REPO" \
  --fixture "$FIXTURE" \
  --device "$DEVICE" \
  --output .autoresearch/latest_eval.json \
  > .autoresearch/latest_eval.log 2>&1
```

Extract the scalar score:

```bash
python -c 'import json; p=json.load(open(".autoresearch/latest_eval.json")); n=max(len(p["cases"]), 1); score=(2*p["strong_passes"] + p["passes"]) / (2*n); print("{:.6f}".format(score))'
```

This is the score to maximize.

## Scoring

The benchmark already exists.
What it does not expose directly is one scalar for the loop.

Use this score:

```text
score = (2 * strong_passes + passes) / (2 * total_cases)
```

Higher is better.
Range is `0.0` to `1.0`.

Why this score:

- `strong_pass` means the correct target landed in the top 3 with strict matching
- `pass` means it landed in the top 5
- `near_miss` does not count as success in `evals/README.md`
- `fail` is zero credit

Tie-breakers, in order:

1. fewer `fails`
2. fewer `near_misses`
3. simpler code in `search.py`

If focused tests fail, or the eval command crashes, treat the run as:

- `score = 0.000000`
- `status = crash`

## What you can modify

Only modify:

- `src/codelens/retrieval/search.py`

You may also write untracked scratch files under:

- `.autoresearch/`

That is it.

## What you must not touch

Do not modify any of these:

- `src/codelens/retrieval/eval.py`
- `evals/micronaut_retrieval.json`
- `evals/README.md`
- `src/codelens/indexing/*`
- `src/codelens/retrieval/documents.py`
- `src/codelens/__main__.py`
- `tests/*`
- `pyproject.toml`
- lockfiles
- the Micronaut benchmark repo

Do not install new packages.
Do not change the benchmark fixture.
Do not change the scoring rule during the run.
Do not commit `.autoresearch/*`.

## Experiment loop

The branch is the experiment history.
Only keep commits that improve the score.

Stop rules:

1. stop after `100` completed experiments
2. stop after `12` consecutive `discard` or `crash` outcomes
3. stop early if you are clearly repeating yourself and do not have a genuinely new ranking idea

This loop is expensive.
Do not keep running once the search space is obviously exhausted.

For each iteration:

1. Read the recent experiment history before choosing the next idea.

Read `.autoresearch/results.tsv`.
Look at at least the last `10` completed experiments, or all prior experiments if fewer exist.
Do not repeat a strategy that was already discarded unless you are combining it with a meaningfully different idea.

2. Note the current best commit:

```bash
git rev-parse --short HEAD
```

3. Edit `src/codelens/retrieval/search.py` with one concrete ranking idea.

Good idea types in this repo:

- better implementation-vs-interface bias
- better query term expansion for Java code symbols
- better penalties for generic infrastructure matches
- better use of `kind`, `name`, `signature`, `file_path`, or `retrieval_text`
- better tie-breaking that favors concrete executable code

Bad idea types for this loop:

- parser changes
- chunking changes
- index format changes
- benchmark edits
- dependency changes

4. Run focused safety tests:

```bash
uv run pytest tests/test_retrieval_eval.py tests/test_retrieval_search.py
```

If tests fail:

- append a `crash` row to `.autoresearch/results.tsv`
- restore `src/codelens/retrieval/search.py` with `git restore --source=HEAD --worktree --staged src/codelens/retrieval/search.py`
- increment the consecutive discard counter by `1`
- continue unless a stop rule has been hit

5. Run the benchmark:

```bash
uv run python -m codelens retrieve eval \
  --repo-root "$BENCH_REPO" \
  --fixture "$FIXTURE" \
  --device "$DEVICE" \
  --output .autoresearch/latest_eval.json \
  > .autoresearch/latest_eval.log 2>&1
```

6. Read the result:

```bash
python -c 'import json; p=json.load(open(".autoresearch/latest_eval.json")); n=max(len(p["cases"]), 1); score=(2*p["strong_passes"] + p["passes"]) / (2*n); print("score={:.6f} strong={} pass={} near={} fail={}".format(score, p["strong_passes"], p["passes"], p["near_misses"], p["fails"]))'
```

If the eval file is missing or invalid:

- inspect `.autoresearch/latest_eval.log`
- if the bug is trivial, fix and rerun once
- otherwise log `crash`, restore the file with `git restore --source=HEAD --worktree --staged src/codelens/retrieval/search.py`, increment the consecutive discard counter by `1`, and continue unless a stop rule has been hit

7. Commit the experiment:

```bash
git add src/codelens/retrieval/search.py
git commit -m "exp: <short description>"
```

8. Compare against the best score so far.

If the score is better:

- keep the commit
- append a `keep` row to `.autoresearch/results.tsv`
- update the running best score
- reset the consecutive discard counter to `0`

If the score is equal or worse:

- append a `discard` row to `.autoresearch/results.tsv`
- discard the experiment commit
- increment the consecutive discard counter by `1`

Because this is a dedicated clean worktree, discard with:

```bash
git reset --hard HEAD~1
```

If the run crashes:

- append a `crash` row to `.autoresearch/results.tsv`
- restore `src/codelens/retrieval/search.py` with `git restore --source=HEAD --worktree --staged src/codelens/retrieval/search.py`
- increment the consecutive discard counter by `1`
- continue unless a stop rule has been hit

## Logging format

Append one row per finished experiment to `.autoresearch/results.tsv`:

```bash
commit	score	strong	pass	near	fail	status	description
```

Example:

```bash
a1b2c3d	0.541667	6	1	3	2	keep	baseline
b2c3d4e	0.583333	7	0	2	3	keep	penalize annotation and interface hits for implementation queries
c3d4e5f	0.541667	6	1	3	2	discard	add file path bonus for router terms
d4e5f6g	0.000000	0	0	0	0	crash	broke query token expansion
```

## Practical guidance

Prefer small, legible changes.
This file is heuristic code.
A tiny improvement with cleaner logic is a win.
An equal score with simpler code is also a win.

Do not chase broad architectural changes in this loop.
If `search.py` plateaus, that means this loop has done its job.
At that point the next loop should target a different file with a different benchmark.
