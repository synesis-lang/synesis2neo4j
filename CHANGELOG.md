# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Language:** [English](CHANGELOG.md) | [Português](CHANGELOG.pt.md)

**Documentation:** [Synesis Language Docs](https://synesis-lang.github.io/synesis-docs)

---

## [0.2.0] - 2026-06-12

### Added

- **Installable package structure** (`synesis_graph/`, `pyproject.toml`)
  - New `pyproject.toml` defines the `synesis-graph` package with `click>=8.0` and `synesis>=0.5.0` as core dependencies; `neo4j>=5.0` and `graphqlite` as optional extras (`pip install synesis-graph[neo4j]`).
  - `synesis_graph/__init__.py` re-exports the public API from `synesis2graph.py` (`run_pipeline`, `compile_project`, `load_json_project`, `GraphPayload`, `PipelineResult`, backend constants).

- **Click-based CLI in `synesis2graph.py`** — replaced the `argparse` `main()` directly in the script:
  - Entry point `synesis-graph` registered via `pyproject.toml` (i.e. `pip install -e .` → `synesis-graph` in PATH).
  - Same `_SynesisGroup` / `_SynesisCommand` / `_ex()` pattern used in `synesis` and `synesis-coder`: Unix-style output, ANSI colors (suppressed when stdout is not a TTY), `sys.stdout.buffer.write(UTF-8)` for encoding safety on Windows.
  - Three subcommands replacing the flat `--backend` flag: `neo4j`, `graphqlite`, `html` — each with their own `--help` and colored `Examples:` epilog.
  - `--project` and `--json` are shared source options on every subcommand (mutually exclusive, one required); `--config` defaults to `config.toml`.
  - HTML-specific flags (`--output`, `--group-by`, `--min-frequency`, `--min-source-count`, `--max-nodes`, `--max-hyperedges`, `--include-isolated`, `--all`) moved from a flat arg group to the `html` subcommand.
  - Graceful fallback to `argparse` when `click` is not installed (`python synesis2graph.py --backend ...` still works).

### Changed

- Repository and package renamed from `synesis2neo4j` to `synesis-graph`.

---

## [0.1.2] - 2025-02-01

### Added

#### Dynamic Source Fields
- **SOURCE Scope Support:** Fields with `SCOPE SOURCE` defined in the Template (.synt) are now dynamically transferred as properties of the `Source` node in Neo4j
- **Template-Driven Extraction:** `analyze_template()` now identifies and catalogs SOURCE-scoped fields alongside ONTOLOGY and ITEM fields
- **Dynamic Source Properties:** `_build_source_props()` replaced hardcoded field extraction with dynamic iteration over template-defined SOURCE fields
- **Full Data Flow:** SOURCE field names are now propagated through the entire pipeline: `analyze_template()` → `GraphPayload` → `_extract_corpus_data()` → `_build_source_props()`
- **Backward Compatibility:** Standard bibliographic fields (`title`, `author`, `year`, `doi`, `journal`, `abstract`) remain as fallback from bibliography entries

---

## [0.1.1] - 2025-01-25

### Fixed

#### GDS Compatibility (Neo4j GDS 2.x+)
- **gds.graph.drop:** Added `YIELD graphName` to avoid deprecated `schema` field warning
- **gds.graph.project.cypher:** Replaced deprecated procedure with new aggregation function API
  - CO_TAXONOMY strategy now uses inline `gds.graph.project()` aggregation
  - CO_CITATION strategy now uses inline `gds.graph.project()` aggregation
  - More efficient execution within Cypher flow

---

## [0.1.0] - 2025-01-24

### Added

#### Universal Pipeline
- **Dynamic Modeling:** Node labels automatically derived from Template (.synt)
- **CODE Support:** CODE fields create concept nodes with dynamic label
- **CHAIN Support:** CHAIN fields create RELATES_TO relationships between concepts
- **Taxonomy Support:** TOPIC, ASPECT, DIMENSION create navigable hierarchies
- **Traceability:** Origin metadata (source_file, line, column) on all nodes

#### Graph Metrics
- **Native Metrics (pure Cypher):**
  - `degree`, `in_degree`, `out_degree` for concepts
  - `mention_count`, `source_count` for concepts
  - `concept_count` for taxonomies
  - `weighted_degree`, `aspect_diversity`, `dimension_diversity` for Topics
  - `item_count`, `concept_count` for Sources

- **GDS Metrics (optional):**
  - `pagerank` - PageRank for relevance/centrality
  - `betweenness` - Betweenness Centrality for "bridge" nodes
  - `community` - Louvain for community detection

- **Projection Strategies:**
  - `RELATES_TO` - uses explicit relationships (CHAIN templates)
  - `CO_TAXONOMY` - connects concepts via shared taxonomy
  - `CO_CITATION` - connects concepts via co-citation in Sources

#### Infrastructure
- **Version Control:** `--version` flag in CLI
- **Graceful Fallback:** Native metrics always calculated; GDS optional with warning
- **Sanitization:** Labels and database names validated against Cypher injection
- **Atomic Transactions:** Synchronization via single transaction

#### MCP Integration (Claude Desktop)
- **Universal Configuration:** Templates for Claude Desktop (`mcp/`)
- **Multi-Database Support:** Namespaces for multiple simultaneous projects
- **GraphRAG Documentation:** Query guide with full traceability
- **Viability Study:** Complete analysis in `docs/MCP_VIABILITY_STUDY.md`

### Documentation
- README.md with Template → Graph mapping table
- Complete metrics documentation (native and GDS)
- Mermaid data flow diagram
- Cypher query examples
- MCP configuration guide (`mcp/SETUP.md`)
- Cypher query reference (`mcp/QUERIES_REFERENCE.md`)
- Bilingual documentation (EN/PT)

---

## Roadmap

### [0.3.0] - Planned
- [ ] Custom Synesis-specific MCP server
- [ ] Optimized prompts for qualitative research
- [ ] Interactive configuration interface

### [0.4.0] - Future
- [ ] Web interface for graph visualization
- [ ] Export to external formats (GraphML, GEXF)
- [ ] Jupyter Notebooks integration

---

## Links

- **Repository:** [github.com/synesis-lang/synesis-graph](https://github.com/synesis-lang/synesis-graph)
- **Documentation:** [synesis-lang.github.io/synesis-docs](https://synesis-lang.github.io/synesis-docs)
- **Issues:** [GitHub Issues](https://github.com/synesis-lang/synesis-graph/issues)
