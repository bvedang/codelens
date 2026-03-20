# Retrieval Progress

## Current status

We now have a working retrieval stack.

- `retrieve search` works end to end
- `retrieve read` works end to end
- `retrieve eval` works end to end
- line and column metadata is stored in new indexes
- search no longer brute-force scores the whole corpus
- verbose mode shows shard-level retrieval progress
- a minimal eval harness exists under `evals/`
- only the offline search-eval layer exists today

This is a solid base.

It is still not agent-grade.

## What exists now

- CLI:
  - `python -m codelens retrieve search`
  - `python -m codelens retrieve read`
  - `python -m codelens retrieve eval`
- Retrieval service:
  - `src/codelens/retrieval/search.py`
- Eval harness:
  - `src/codelens/retrieval/eval.py`
- Eval assets:
  - `evals/micronaut_retrieval.json`
  - `evals/README.md`
- FAISS repository search:
  - `src/codelens/indexing/faiss_repository.py`
- Location-aware index payloads:
  - `start_line`
  - `end_line`
  - `start_col`
  - `end_col`

## Current search shape

The current search path is:

1. Embed the query with ColBERT.
2. Search each FAISS shard for vector candidates.
3. Map vector hits back to chunk ids.
4. Exact-score only the shortlisted chunks.
5. Apply heuristic reranking.

The current reranking uses:

- lexical overlap on name, symbol, signature, file path, and retrieval text
- light implementation bias toward concrete code
- penalties for some generic infrastructure types

The current shortlist policy is:

- `candidate_limit = max(top_k * 20, 100)`
- `per_query_limit = max(top_k * 8, 32)`

Note:

- the retrieval service now asks the repository for a wider raw candidate set before reranking
- the FAISS repository still uses the internal shortlist policy above

## What is working

- The CLI path is wired and tested.
- Search returns compact hits.
- Read returns chunk content and nearby same-file neighbors.
- A minimal benchmark harness exists and runs from the CLI.
- Benchmark fixtures now live in `evals/`.
- Run artifacts now include benchmark and implementation provenance.
- Focused retrieval and parser tests are passing.
- FAISS candidate retrieval works on large workspace indexes.
- The old brute-force full-corpus search path is gone.

## Measured behavior on Micronaut

Observed on `/Users/vedangbarhate/Desktop/workspace/micronaut-core`:

- query embedding: about `4.8s`
- candidate search + exact scoring: about `7.6s`
- total search: about `12.4s`
- read: about `1s`

This is a major improvement over the old search path, which was taking about `190s`.

Important:

- these Micronaut timings are operational baselines, not the main evidence for retrieval quality
- now that a minimal eval harness exists, benchmark results should be the primary basis for judging retrieval changes
- Micronaut numbers are still useful for latency tracking and regressions on one real repo

Latency takeaway:

- warm runtime helps cold-start overhead
- it does not remove per-query embedding cost
- it does not remove shard traversal cost
- the biggest hot-path costs are still query embedding and shard search

## Current quality assessment

Current state:

- implementation status: good
- prototype quality: decent
- agent quality: still weak

Rough score:

- overall retrieval quality: `5/10`
- wiring and reliability: `8/10`
- relevance quality: `4/10`
- latency: `4/10`
- agent readiness: `5/10`

Why:

- the stack works
- the speed is much better than before
- broad natural-language ranking is still too fuzzy
- latency is still too high for tight agent loops

## What is still wrong

### 1. Ranking is still too generic

For broad queries like:

- `where is bean lookup implemented`

the search still tends to overmatch broad `Bean*` and semantically related types.

It is better than before, but it can still miss the real execution path such as:

- `DefaultBeanContext`
- `getBean(...)`
- `findBean(...)`
- `resolveBeanRegistration(...)`

### 2. Latency is still too high

The current search path still pays for:

- fresh Python process startup
- model load per CLI invocation
- query embedding per CLI invocation
- searching `2186` shard files on Micronaut

That is too expensive for agent loops where retrieval may happen many times per task.

### 3. Older indexes still lack usable location metadata

If a repo was indexed before the location metadata change, results still show:

- `start_line: null`
- `end_line: null`

That weakens agent usability because the agent cannot jump straight to exact lines.

## What we are not measuring well enough yet

This is still the biggest gap in how we are judging retrieval today.

Right now, we mostly look at:

- latency
- whether the top results seem reasonable
- a small manual Micronaut benchmark

That is not enough for agent retrieval.

We now have a first benchmark harness, but it is still:

- manual
- one-repo heavy
- partly subjective in scoring
- not yet strong enough to be the only source of truth
- limited to offline search evaluation

We also need to measure:

### 1. Context precision

Did we retrieve the exact implementation code, or just related code?

### 2. Line-level localization

Did we find the right method and line range, not just the right file?

### 3. Context efficiency

How much context did we retrieve compared to how much was actually useful?

### 4. Context utilization

Did the agent actually use the retrieved chunks in the final answer or edit?

### 5. Task success

Could the agent complete the real coding task from the retrieved context?

That means retrieval should be evaluated as part of an agent workflow, not just as a search experience.

The eval model should be split into 3 layers:

### 1. Offline component evals

What we have now:

- fixed fixture
- reproducible search runs
- automatic scoring

### 2. Trace or workflow evals

What we do not have yet:

- grading of intermediate retrieval behavior
- search then read traces
- efficiency and utilization metrics

### 3. Human annotation

What we do not have yet:

- explicit reviewer judgments like `usable_for_agent`
- short notes on why a near-miss was not usable

Only the first layer exists today.

## External findings that matter

Recent work and platform docs point to a few things we should take seriously:

- strong retrieval systems usually combine semantic search, keyword search, query rewriting, and reranking
- line-level precision matters a lot for coding tasks
- retrieving more context is not automatically better
- agents often retrieve context they never use
- one-shot retrieval is often weaker than iterative retrieval

The practical takeaway:

- relevance alone is not enough
- recall alone is not enough
- retrieval has to be precise, efficient, and usable by an agent

## Can we get below 500ms?

Not with the current one-shot CLI shape.

Why:

- each `uv run python -m codelens ...` starts a fresh process
- the ColBERT model is loaded per invocation
- the query is embedded per invocation
- retrieval still walks shard indexes instead of one compact base index

Sub-500ms is plausible only with a different runtime shape:

- a warm long-lived local process
- one compact final base index
- a cheaper first-stage retrieval pass
- exact reranking on a small shortlist only

Important:

- warm runtime is useful
- warm runtime is not the whole latency solution

## Roadmap

### 1. Expand and automate the offline eval harness

What:

- keep the pinned Micronaut fixture, but make runs easier to compare over time
- add automated result output and summary reporting
- add benchmark versioning
- add at least one more repo so we do not tune only to Micronaut
- keep scoring expected file, symbol, and line-level hits
- keep automatic search metrics separate from any later human annotations

Why:

- we now have a minimal benchmark, but it is not broad or reproducible enough yet
- without stronger evals, later ranking and architecture work will still be guided by anecdotes
- we need a way to tell whether changes improved agent usefulness across repos, not just on Micronaut

The eval should track:

- Recall@k
- rank of first correct hit
- line-level localization
- benchmark version / fixture version

### 2. Add trace or workflow evals

What:

- add a second eval layer that records multi-step retrieval behavior
- track what was searched, what was read, and what was actually used later
- measure efficiency, not just correctness

Why:

- final search ranking is not the whole retrieval story
- agent workflows depend on intermediate retrieval decisions
- this is where context waste and missed follow-up reads show up
### 3. Add warm local runtime and explicit model caching

What:

- run retrieval in a small long-lived local background process
- keep the ColBERT model loaded across requests
- talk to it from the CLI over a Unix socket or another local-only IPC path
- make model cache location explicit and stable on disk

Why:

- the current CLI reloads the model for every query
- caching helps cold starts
- warm runtime helps repeated calls
- this removes cold-start overhead, but not the full hot-path cost

Constraint:

- a shared long-lived process for both retrieval and indexing is only acceptable after step 4 is defined
- until the consistency model exists, treat shared retrieval-plus-indexing state as unsafe by default
- so this step should start as warm retrieval only, with direct CLI fallback still available

### 4. Add compact base index plus overlay, with a clear consistency model

What:

- merge shard files into a final `vectors.faiss` for the stable base index
- keep a small mutable overlay for recent file updates
- define versioning, atomic swap, reload, and read/write isolation rules

Why:

- current retrieval still searches thousands of shard files on large repos
- a compact base index is much cheaper to search
- an overlay keeps incremental updates practical
- without a consistency model, a warm process can serve stale or partially updated state

The design needs explicit answers for:

- when a search request sees new data
- whether writes publish atomically
- how stale snapshots are detected
- how a rebuilt base index swaps in safely

This is not a later refinement.

It is a hard prerequisite for letting one warm process serve both reads and writes safely.

### 5. Add incremental file reindexing

What:

- reindex only the file that changed
- remove stale chunks for that file
- re-chunk, re-embed, and update the live index for that file

Why:

- this is the real agent workflow after user edits
- full workspace reindexing is too expensive for tight edit loops
- warm model reuse makes this practical
- this only becomes safe once the consistency story is defined

### 6. Improve first-stage retrieval and ranking

What:

- add a cheaper first pass before ColBERT exact reranking
- likely lexical or hybrid retrieval
- add query rewriting or multi-query expansion for broad code questions
- bias toward executable paths and concrete framework entrypoints
- prefer things like `getBean`, `findBean`, `resolveBeanRegistration`, `DefaultBeanContext`
- keep penalizing generic infrastructure types when they are not the real implementation path

Why:

- ColBERT query embedding is too expensive to carry the whole search story
- a fast first pass can narrow candidates cheaply
- broad natural-language queries need more than raw vector similarity
- current ranking still overmatches generic `Bean*` classes
- agents need precise implementation anchors, not broad semantic neighbors

### 7. Add a human annotation lane

What:

- add a separate annotation file or result schema for manual judgments
- keep fields like `usable_for_agent` and reviewer notes out of the automatic search artifact

Why:

- human judgment is useful, but it should not be mixed into automatic metrics
- separate annotation keeps scoring honest and reproducible

### 8. Validate on real agent tasks

What:

- run retrieval as part of real multi-step agent workflows
- include cases where the user edits a file and the system must reindex and search again

Why:

- search quality alone is not enough
- we need to know whether the agent can actually solve tasks from the returned context

### 9. Reindex real repos with line metadata

What:

- reindex Micronaut and other test repos with the latest location-aware indexer

Why:

- older indexes still have null line fields
- agents need exact file locations to read and edit safely

## Recommended next step

The highest-impact next step is:

- expand the eval harness so results are easier to compare over time

The next eval step after that is:

- add trace or workflow evals before we call this an agent retrieval harness

The next architecture step after that is:

- add warm local runtime and explicit model caching

## Files changed so far

- `src/codelens/__main__.py`
- `src/codelens/ast_helpers.py`
- `src/codelens/chunker.py`
- `src/codelens/indexing/documents.py`
- `src/codelens/indexing/faiss_repository.py`
- `src/codelens/models/retrieval_document.py`
- `src/codelens/retrieval/__init__.py`
- `src/codelens/retrieval/eval.py`
- `src/codelens/retrieval/documents.py`
- `src/codelens/retrieval/search.py`
- `evals/micronaut_retrieval.json`
- `evals/README.md`
- `tests/test_chunker.py`
- `tests/test_faiss_repository.py`
- `tests/test_index_documents.py`
- `tests/test_main_retrieval_cli.py`
- `tests/test_retrieval_documents.py`
- `tests/test_retrieval_eval.py`
- `tests/test_retrieval_search.py`

## Test status

Focused retrieval, eval, and parser suite is passing.

## Notes

Relevant external references used to shape this review:

- ContextBench: https://contextbench.github.io/
- OpenAI retrieval guide: https://platform.openai.com/docs/guides/retrieval
- OpenAI file search relevance guidance: https://platform.openai.com/docs/assistants/tools/file-search/improve-file-search-result-relevance-with-chunk-ranking.pdf
- Claude Code MCP docs: https://code.claude.com/docs/en/mcp
- RepoCoder: https://arxiv.org/abs/2303.12570
