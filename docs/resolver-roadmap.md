# Resolver Implementation Roadmap

This roadmap tracks the work needed to get Java type resolution as close as possible to build-aware, compiler-grade behavior for monorepos.

Status markers:

- `[x]` done
- `[ ]` pending
- `[-]` in progress

## Phase 1: Compiler-Grade Local Resolution

Goal: resolve local repo symbols correctly per source set.

- [x] Add `TypeResolver` abstraction and typed resolution outcomes
- [x] Add `GradleWorkspaceModel`
- [x] Add source symbol indexing, including nested member types
- [x] Add parser injection points so resolution is decoupled from parsing
- [x] Validate on fixture monorepos
- [x] Validate on a real Gradle repo (`micronaut-core`)
- [x] Prove end-to-end local symbol resolution on selected real files
- [x] Add regression fixtures for local edge cases:
  - same-package types
  - nested/member types
  - wildcard imports of local modules
  - invisible module types
  - test source set seeing test-only deps
  - generated source roots

Exit criteria:

- local project types resolve correctly for `main` and `test`
- invisible project types stay unresolved
- no repo-wide guessing

## Phase 2: External Dependency Resolution

Goal: resolve third-party library symbols from the real Gradle classpath.

- [x] Fix exporter to capture resolved external classpath entries for each source set
- [x] Distinguish local output dirs from external jars/classes
- [x] Build jar/class directory symbol indexes from exported classpaths
- [x] Merge visible external symbols into the file-scoped `TypeIndex`
- [x] Add tests for:
  - wildcard import from external jar
  - explicit import from external jar
  - multiple jars exporting same simple name
  - missing jars / partial classpath
- [x] Validate on real Micronaut files that use external libraries

Exit criteria:

- external types on the compile/test classpath resolve
- unresolved or ambiguous external types are not guessed
- jar visibility matches Gradle source-set boundaries

## Phase 3: JDK Resolution

Goal: stop relying on a curated `java.lang` list.

- [x] Replace hardcoded `JAVA_LANG_TYPES` with a real JDK index
- [x] Index `java.lang`, `java.util`, and the broader platform from the installed JDK
- [x] Make JDK selection explicit per export/runtime
- [x] Add tests for:
  - implicit `java.lang` types
  - standard library imports
- [ ] Add tests for:
  - conflicts between JDK and dependency types
- [x] Validate against the JDK actually used by Gradle for the target repo

Exit criteria:

- JDK symbols resolve from a real index
- no hand-maintained JDK type list required

## Phase 4: Generated and Advanced Source Sets

Goal: support real build-generated code and nonstandard source sets.

- [x] Export generated roots
- [ ] Verify generated roots are indexed in end-to-end resolution
- [ ] Support annotation processor/KSP/KAPT generated outputs consistently
- [ ] Validate source sets like:
  - `testFixtures`
  - `jmh`
  - custom Gradle source sets
  - Groovy/Kotlin mixed roots
- [ ] Add tests around generated code visibility and custom source-set dependencies

Exit criteria:

- generated and custom source sets behave like real Gradle compilation inputs

## Phase 5: Accuracy and Safety Hardening

Goal: make wrong resolution rarer than unresolved resolution.

- [ ] Add explicit ambiguity handling metadata to outputs
- [ ] Track provenance for each resolution:
  - explicit import
  - same package
  - wildcard import
  - project classpath
  - external jar
  - JDK
- [ ] Add conflict tests:
  - `java.util.List` vs `java.awt.List`
  - same simple name in two visible modules
  - same simple name in jar and project
- [ ] Add snapshot tests on selected real monorepo files
- [ ] Add metrics/logging hooks for unresolved and ambiguous symbols

Exit criteria:

- ambiguous cases are surfaced, not guessed
- resolution provenance is inspectable
- regressions are caught by real-repo tests

## Phase 6: Operational Integration

Goal: make this usable in the context engine pipeline.

- [ ] Define a stable exported workspace JSON schema
- [ ] Add a command/tooling path to refresh Gradle metadata
- [ ] Cache source/jar/JDK indexes
- [ ] Support incremental refresh for changed modules/files
- [ ] Document required environment:
  - Gradle
  - JDK version
  - repo root
- [ ] Define failure modes when metadata is stale or incomplete

Exit criteria:

- resolver can be refreshed and reused cheaply
- context engine can depend on it without ad hoc setup

## Current Focus

The next active work item is the first unchecked item in Phase 4:

- `Verify generated roots are indexed in end-to-end resolution`

Phase 3 outcome:

- the curated `JAVA_LANG_TYPES` fallback is gone
- `java.lang` resolution now only happens when a real JDK index is provided
- Gradle export now emits `jdk_home`, and the workspace model preserves it
- JDK indexing is built from installed `jmods`
- the demo path accepts `--jdk-home` and also picks up `jdk_home` from exported workspace metadata
- tests cover strict unresolved behavior without a JDK index, resolved behavior with one, and real Micronaut validation using the exported Gradle JDK
