# Retrieval Evals

## Goal

This folder holds retrieval benchmark assets.

The harness should be thought of in 3 layers:

- Layer 1: offline component evals
  - fixed fixtures
  - reproducible runs
  - automatic scoring for `retrieve search`
- Layer 2: trace or workflow evals
  - grade intermediate retrieval behavior across multi-step tasks
  - track what was searched, what was read, and what was used later
- Layer 3: human annotation
  - manual judgments like `usable_for_agent`
  - short reviewer notes on why a near-miss was or was not usable

Right now, only layer 1 is implemented here.

The structure is intentionally split:

- benchmark docs live here
- pinned dataset fixtures live here
- harness code lives under `src/codelens/retrieval`
- generated results should live separately from the fixture, for example under `evals/results/`

That matches the common pattern used by evaluation tools and benchmark repos:

- dataset or test cases as data
- runner or harness as code
- outputs as generated artifacts

## Current contents

- `micronaut_retrieval.json`
  - pinned retrieval benchmark fixture for Micronaut

## Benchmark shape

This is a search benchmark first.

It checks whether `retrieve search` lands on the right file, symbol, and line neighborhood.

It does not yet score `retrieve read`, trace behavior, or broader agent usability automatically.

It is still small and partly manual.

It should be good enough to compare retrieval changes over time if the test conditions are pinned.

## Pinned test conditions

Results are only comparable when all of these stay fixed:

- repo:
  - `micronaut-core`
- repo commit:
  - `6a11a05950f36193bd865d6b25c2bc17dfb4ff1c`
- model:
  - `lightonai/ColBERT-Zero`
- codelens git commit:
  - recorded in the output artifact
- retrieval version:
  - recorded in the output artifact
- ranking config version:
  - recorded in the output artifact
- codelens dirty flag:
  - recorded in the output artifact
- fixture path:
  - recorded in the output artifact
- fixture sha256:
  - recorded in the output artifact
- line tolerance:
  - recorded in the output artifact
- retrieval command:

```bash
uv run python -m codelens retrieve eval \
  --repo-root /Users/vedangbarhate/Desktop/workspace/micronaut-core \
  --fixture /Users/vedangbarhate/Desktop/workspace/codelens/evals/micronaut_retrieval.json \
  --device mps
```

- current retrieval shape:
  - ColBERT query embedding
  - FAISS per-shard candidate retrieval
  - exact late-interaction scoring on the shortlist
  - heuristic reranking
- repository shortlist policy:
  - `candidate_limit = max(top_k * 20, 100)`
  - `per_query_limit = max(top_k * 8, 32)`
- current search request width from the retrieval service:
  - repository search called with `top_k = max(user_top_k * 10, 50)`

Important:

- if chunking changes, the benchmark version changes
- if ranking logic changes, compare results but record the version change
- if the repo commit changes, do not compare scores directly
- if the codelens git commit changes, compare results only through the recorded run artifact

## Benchmark mode vs smoke mode

### Benchmark mode

Use the pinned conditions above.

This mode is for comparing retrieval changes over time.

### Smoke mode

Use the same query list on any repo snapshot you have locally.

This mode is only for quick sanity checks.

Do not compare smoke-mode results to benchmark-mode results.

## How to run

Benchmark run:

```bash
uv run python -m codelens retrieve eval \
  --repo-root /Users/vedangbarhate/Desktop/workspace/micronaut-core \
  --fixture /Users/vedangbarhate/Desktop/workspace/codelens/evals/micronaut_retrieval.json \
  --device mps
```

Raw JSON:

```bash
uv run python -m codelens retrieve eval \
  --repo-root /Users/vedangbarhate/Desktop/workspace/micronaut-core \
  --fixture /Users/vedangbarhate/Desktop/workspace/codelens/evals/micronaut_retrieval.json \
  --device mps \
  --json
```

Write a result artifact:

```bash
uv run python -m codelens retrieve eval \
  --repo-root /Users/vedangbarhate/Desktop/workspace/micronaut-core \
  --fixture /Users/vedangbarhate/Desktop/workspace/codelens/evals/micronaut_retrieval.json \
  --device mps \
  --output /Users/vedangbarhate/Desktop/workspace/codelens/evals/results/micronaut_run.json
```

## How to score

This benchmark is intentionally strict.

We care about whether retrieval lands on the implementation, not merely near it.

### Strong pass

- primary target is in top 3
- and the hit lands on the expected symbol and line range when both are pinned
- or the hit lands on the expected symbol when no line target is pinned
- or the hit lands on the expected line range only when the case has no primary symbol target

### Pass

- primary target is in top 5
- and the same strict matching rule above still holds

### Diagnostic near-miss

- the right file family is found
- but the hit is too generic, such as an interface, provider, annotation, registry, or nearby type

Important:

- a diagnostic near-miss does **not** count as success for ranking work
- it should be recorded as a miss in score summaries

### Fail

- no primary target appears in top 5

## What to record

For each query, record:

- rank of first primary hit
- exact symbol matched
- whether the hit lands in the expected line range
- whether the result is a strong pass, pass, near-miss, or fail

Suggested summary fields:

- `primary_rank`
- `matched_symbol`
- `line_hit`
- `verdict`

Note:

- this harness currently records automatic search metrics only
- if we later add a `read` pass or a manual scorecard, keep those fields separate from the automatic metrics
- symbol-only matches do not get full credit when the case also pins a line target

### Human annotation lane

These do not belong in the automatic artifact yet:

- `usable_for_agent`
- reviewer note
- why a near-miss was not usable

If we add them later, keep them in a separate annotation file or a clearly separate section of the result schema.

## Query set

The pinned query set lives in:

- `micronaut_retrieval.json`

The fixture contains:

- benchmark metadata
- repo and model pinning
- expected primary paths
- expected primary symbols
- expected line anchors or explicit line ranges
- acceptable secondary paths and symbols where needed

## Current limits of this benchmark

This benchmark is much better than ad hoc “top 5 looks fine.”

It is still limited.

Main limits:

- it is one repo only
- it is heavy on Micronaut bean and routing vocabulary
- it is still partly manual
- it is still a search benchmark, not a full agent benchmark
- it does not yet compare experiments side by side
- it does not yet measure retrieval efficiency or utilization

So this should be treated as:

- a useful benchmark for current work
- not a claim of general retrieval quality across codebases

## Residual risks

- scorer judgment can still drift on manual notes outside the artifact
- Micronaut-specific tuning risk still exists
- some cases still use line anchors rather than exact line ranges

This benchmark is good enough to guide early retrieval work.

It is not yet a fully robust benchmark.

## Next benchmark improvements

To reduce Micronaut-specific tuning risk, add a second repo with different vocabulary and architecture.

Good next additions:

- a non-framework Java repo
- a Python repo
- a TypeScript repo

Later, automate score summaries for:

- Recall@k
- first-hit rank
- line-hit rate
- near-miss rate
- agent task success in a separate benchmark layer

If we add a broader agent benchmark later, it should:

- call `retrieve read` after a matched chunk
- keep manual fields like `usable_for_agent` separate from search metrics
- define explicit judging rules for when returned context is enough to act

Good next additions after this layer:

- trace or workflow evals that record search then read behavior
- efficiency metrics like hits returned, chunks read, and context bytes
- pairwise comparison mode for ranking experiments
- living-dataset updates where real retrieval failures become new cases

When this benchmark becomes machine-readable or automated, add:

- `benchmark_version`

That version should change when:

- chunking changes
- gold targets change
- scoring rules change
- benchmark queries change

## References

The structure here follows the same broad separation used by current evaluation tooling:

- OpenAI docs emphasize datasets and reproducible eval runs:
  - https://developers.openai.com/api/docs/guides/evaluation-getting-started
  - https://developers.openai.com/api/docs/guides/agent-evals
- LangSmith evaluation docs emphasize curated examples, component-level evaluation, and separating what is measured:
  - https://docs.langchain.com/langsmith/evaluation-concepts
- Promptfoo uses declarative test fixtures kept separate from execution:
  - https://github.com/promptfoo/promptfoo
- OpenAI `evals` keeps benchmark data and harness concerns separated:
  - https://github.com/openai/evals
