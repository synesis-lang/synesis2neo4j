# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Language:** [English](CHANGELOG.md) | [Português](CHANGELOG.pt.md)

**Documentation:** [Synesis Language Docs](https://synesis-lang.github.io/synesis-docs)

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

### [0.2.0] - Planned
- [ ] Custom Synesis-specific MCP server
- [ ] Optimized prompts for qualitative research
- [ ] Interactive configuration interface

### [0.3.0] - Future
- [ ] Web interface for graph visualization
- [ ] Export to external formats (GraphML, GEXF)
- [ ] Jupyter Notebooks integration

---

## Links

- **Repository:** [github.com/synesis-lang/synesis2neo4j](https://github.com/synesis-lang/synesis2neo4j)
- **Documentation:** [synesis-lang.github.io/synesis-docs](https://synesis-lang.github.io/synesis-docs)
- **Issues:** [GitHub Issues](https://github.com/synesis-lang/synesis2neo4j/issues)
