# MVP Checklist

This checklist tracks the remaining work needed to turn the resolver into a practical monorepo MVP.

Status markers:

- `[x]` done
- `[ ]` pending
- `[-]` in progress

## Foundation

- [x] Stable exported workspace schema contract
- [x] Reusable orchestration entrypoint for workspace-aware parsing
- [x] Caching for source, binary, and JDK indexes

## End-to-End Validation

- [ ] Generated sources validated in a real repo flow
- [ ] A few real-repo regression tests for dependency-heavy files
- [x] Conflict coverage for ambiguous names:
  - JDK vs dependency
  - dependency vs dependency

## Operational Readiness

- [ ] Document refresh flow for exported workspace metadata
- [ ] Document failure modes for stale or partial workspace metadata

## Current Focus

The next MVP work item is:

- `Document refresh flow for exported workspace metadata`

Why this is next:

- caching is now in place
- the workspace/runtime path is stable enough to document
- generated-source validation is still blocked by the current local real-repo state
