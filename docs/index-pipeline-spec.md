# Index Pipeline Specification

Status: Draft

Build the pipeline that takes parsed Java chunks, embeds them with ColBERT, and stores vectors and metadata locally per workspace. Support full workspace rebuild and single file refresh.

This phase does not include query-time retrieval, reranking, BM25, or MCP integration.

## 1. Pipeline

```
chunks (from parser)
  → filter to indexable kinds
  → construct retrieval text
  → generate ColBERT multi-vector embeddings
  → store vectors in FAISS index
  → store metadata in sidecar
```

## 2. Persistence

FAISS is the vector storage layer. No external server dependency.

Each workspace produces a local index directory:

```
.codelens/index/
  vectors.faiss          # FAISS index (ColBERT multi-vectors)
  metadata.json          # chunk_id → payload mapping
  index.lock             # write lock
```

This keeps everything local to the workspace. No Docker, no Qdrant, no server process.

Metadata sidecar maps FAISS integer IDs back to chunk payloads. Stored as JSON for debuggability. Can move to a binary format later if size becomes an issue.

## 3. Chunk ID

Format: `{repo_root_hash}:{file_path}:{chunk_kind}:{qualified_name_or_offset}`

For named chunks (methods, fields, constructors, types):

```
a1b2c3d4e5f6:src/main/java/com/app/OrderService.java:method:com.app.OrderService.cancelOrder
```

For anonymous or positional chunks (behavior, skeleton):

```
a1b2c3d4e5f6:src/main/java/com/app/OrderService.java:skeleton:0
```

The qualified name path makes IDs stable across rebuilds as long as the symbol exists. Positional chunks use a zero-indexed counter within their kind per file.

## 4. Retrieval Text Construction

Retrieval text is what ColBERT sees. It's not raw source — it's source with structural context prepended.

Format:

```
[{chunk_kind}] {package}.{owner_chain}.{name}
{signature_or_declaration}
{annotations}
{source_code}
```

Example for a method:

```
[method] com.app.orders.OrderService.cancelOrder
public void cancelOrder(String orderId) throws OrderNotFoundException
@Transactional @Retry(maxAttempts = 3)
public void cancelOrder(String orderId) throws OrderNotFoundException {
    Order order = orderRepository.findById(orderId)
        .orElseThrow(() -> new OrderNotFoundException(orderId));
    order.setStatus(Status.CANCELLED);
    eventBus.publish(new OrderCancelledEvent(order));
}
```

Example for a skeleton:

```
[skeleton] com.app.orders.OrderService
public class OrderService implements OrderOperations
extends AbstractService
implements OrderOperations, Auditable
fields: orderRepository, eventBus, retryPolicy
methods: cancelOrder, getOrder, listOrders, retryFailed
```

Rules:

- if metadata is unresolved, omit it (don't insert placeholders)
- skeleton chunks summarize structure, they don't include method bodies

## 5. Embedding

Model: ColBERT-Zero-supervised. Apache 2.0. No fallback model in MVP.

ColBERT produces per-token embeddings (multi-vector). These are stored as-is to preserve late interaction capability at query time.

Batch size: 32 documents per embedding call.

## 6. FAISS Index Layout

ColBERT multi-vectors need special handling in FAISS since each document produces a variable number of token vectors.

Storage approach:

- all token vectors across all documents are stored in a single flat FAISS index
- a separate mapping tracks which FAISS vector IDs belong to which chunk_id
- this mapping lives in `metadata.json` alongside the payload data

```json
{
  "model": "ColBERT-Zero-supervised",
  "indexed_at": "ISO 8601",
  "chunks": {
    "<chunk_id>": {
      "faiss_ids": [0, 1, 2, ..., N],
      "payload": { ... }
    }
  }
}
```

This is the standard approach for ColBERT + FAISS: flatten all token embeddings into one index, then use the ID mapping to aggregate scores per document at query time (MaxSim).

## 7. Indexable Chunk Kinds

The service filters parsed chunks to these kinds before indexing:

`method`, `constructor`, `field`, `skeleton`, `behavior`, `type`, `record_component`

Everything else (e.g. `file`) is dropped.

## 8. Chunk Size Bounds

Minimum: 20 characters of source code. Anything shorter (trivial getters, empty constructors) is noise.

No splitting of large chunks in this phase. A 500-line method becomes one document. Splitting strategies are a retrieval-quality concern for a later phase.

## 9. Payload Schema

Each chunk carries this metadata in the sidecar:

```json
{
  "chunk_id": "string",
  "repo_root": "string",
  "file_path": "string",
  "chunk_kind": "string",
  "owner_chain": ["string"],
  "package": "string",
  "name": "string",
  "signature": "string | null",
  "return_type": "string | null",
  "field_type": "string | null",
  "annotations": ["string"],
  "modifiers": ["string"],
  "resolved_calls": ["string"],
  "fields_accessed": ["string"],
  "throws": ["string"],
  "implements": ["string"],
  "extends": "string | null",
  "source_set": "string | null",
  "retrieval_text": "string",
  "source_text": "string",
  "indexed_at": "ISO 8601 timestamp"
}
```

Null/empty fields are omitted, not stored as nulls.

## 10. Full Workspace Rebuild

Input:

- repo root (required)
- workspace metadata JSON (required)

Behavior:

1. Validate repo root exists and workspace JSON is parseable.
2. Derive file list from workspace metadata visibility model.
3. Delete existing index directory (`.codelens/index/`), start fresh.
4. Parse all files. On per-file parse failure, log the error and continue.
5. Filter to indexable chunk kinds.
6. Construct retrieval text for each chunk.
7. Embed in batches of 32.
8. Build FAISS index from all token vectors.
9. Write `vectors.faiss` and `metadata.json`.
10. Log summary: files processed, chunks indexed, failures.

Full rebuild is deterministic for the same input set.

## 11. Single File Refresh

Input:

- repo root (required)
- file path (required)
- workspace metadata JSON (required)

Behavior:

1. Validate file exists.
2. Load existing FAISS index and metadata.
3. Remove all vectors belonging to chunks from the target file (using the faiss_ids mapping).
4. Parse file with workspace-aware resolution.
5. Filter, construct retrieval text, embed.
6. Add new vectors to the FAISS index.
7. Update metadata: remove old chunk entries for the file, add new ones.
8. Write updated `vectors.faiss` and `metadata.json`.
9. Log summary.

Note: FAISS doesn't support in-place deletion efficiently. For file refresh, the practical approach is to rebuild the index from the remaining vectors plus the new ones. For large repos this is still fast since it's CPU-bound and FAISS rebuilds are cheap relative to embedding.

## 12. Concurrency

No concurrent jobs against the same index. The service acquires `index.lock` before any write operation. A second job attempting the same workspace fails fast with a clear error.

File watcher integration (watchdog) must debounce: batch file change events over a 2-second window, then issue one `index_file` call per changed file, sequentially.

Multiple files changed in a single git operation (checkout, rebase) should be queued and processed in order, not in parallel.

## 13. CLI

### Workspace index

```bash
codelens index workspace \
  --repo-root <path> \
  --workspace-json <path>
```

### File index

```bash
codelens index file \
  --repo-root <path> \
  --file <path> \
  --workspace-json <path>
```

### Status

```bash
codelens index status \
  --repo-root <path>
```

Returns: chunk count, last indexed timestamp, embedding model used.

## 14. Failure Handling

| Scenario                                         | Behavior                                    |
| ------------------------------------------------ | ------------------------------------------- |
| Unresolved symbols during resolution             | Index anyway, omit unresolved metadata      |
| Single file parse error during workspace rebuild | Log, skip file, continue                    |
| Workspace JSON missing                           | Fail fast                                   |
| Embedding model fails to load                    | Fail fast                                   |
| Lock acquisition fails                           | Fail fast, tell user another job is running |
| Metadata sidecar corrupted                       | Log error, force full rebuild               |

## 15. Testing

**Unit**: retrieval text construction, chunk ID generation, chunk kind filtering, payload building, FAISS ID mapping logic.

**Integration**: index a small fixture repo, verify chunk count and payload correctness. File refresh replaces old chunks. Full rebuild clears and repopulates.

**Smoke** (optional): index conductor-oss/conductor, verify no crashes, spot-check retrieval text quality.

## 16. Open Questions

1. **Metadata sidecar format**: JSON is simple and debuggable but may get large for huge repos. Could move to SQLite or msgpack later if needed. JSON for MVP.
2. **Schema versioning**: when the payload schema changes, do we force full rebuild or attempt migration? Recommendation: force full rebuild for now.
