# Workspace Schema

The exported Gradle workspace JSON is the contract between Gradle metadata export and workspace-aware parsing.

## Top-Level Fields

- `schema_version`: integer, currently `1`
- `jdk_home`: absolute path to the JDK Gradle used for the export
- `source_sets`: object keyed by source-set id, for example `:micronaut-context:main`

## Source Set Fields

Each `source_sets[<key>]` entry may contain:

- `source_roots`: regular source roots
- `generated_source_roots`: generated source roots discovered from the source set
- `project_dependencies`: visible project source sets
- `project_artifact_entries`: resolved classpath artifacts produced by local Gradle projects
- `external_binary_entries`: resolved third-party classpath entries, including jars and class directories
- `external_jars`: subset of `external_binary_entries` that are jars
- `output_dirs`: compiled classes/resources output directories for that source set
- `compile_classpath_entries`: raw resolved compile classpath files
- `runtime_classpath_entries`: raw resolved runtime classpath files

## Compatibility Rules

- `schema_version` is required when loading from exported JSON
- hand-built in-memory test fixtures may omit `schema_version`
- incompatible schema versions should fail fast during load instead of being silently accepted

## Resolution Rules

- local project symbols come from visible source roots
- third-party symbols come from `external_binary_entries`
- JDK symbols come from indexing `jdk_home`
- local project artifacts on the classpath are not treated as third-party dependencies
