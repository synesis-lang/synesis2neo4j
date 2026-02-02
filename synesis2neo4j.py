#!/usr/bin/env python3
"""
synesis2neo4j.py - Universal Pipeline Synesis → Neo4j (Memory to Graph)

Version: 0.1.0
Repository: https://github.com/synesis-lang/synesis2neo4j

Purpose:
    Connects the Synesis compiler directly to Neo4j without intermediate files.
    Compiles the project in memory via `synesis.load()` and synchronizes atomically.

Main components:
    - compile_project: Compiles Synesis project and prepares payload for graph
    - sync_to_neo4j: Persists payload to Neo4j via single transaction
    - compute_metrics: Calculates native and GDS metrics automatically
    - TaskReporter: User interface with Rich (fallback to logging)

Critical dependencies:
    - synesis: bibliometric project compiler
    - neo4j: official graph database driver
    - tomli/tomllib: TOML configuration parser

Optional dependencies:
    - Neo4j GDS: plugin for advanced metrics (PageRank, Betweenness, Louvain)

Usage example:
    python synesis2neo4j.py --project ./my_project.synp --config config.toml
    python synesis2neo4j.py --version

Implementation notes:
    - Zero intermediate I/O (everything in memory)
    - Atomicity via single transaction
    - Dynamic labels sanitized against Cypher injection
    - Uses Result types for errors (CompilationError, ConnectionError, SyncError)
    - Metrics calculated automatically (native always, GDS if available)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# ============================================================================
# VERSION
# ============================================================================
__version__ = "0.1.2"
__version_info__ = (0, 1, 2)

# ============================================================================
# EXTERNAL IMPORTS
# ============================================================================
try:
    from synesis import SynesisCompiler
except ImportError:
    print("ERRO CRÍTICO: Biblioteca 'synesis' não encontrada.")
    print("Instale via: pip install synesis")
    sys.exit(1)

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None  # type: ignore

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    Console = Panel = Table = box = None  # type: ignore
    RICH_AVAILABLE = False

# ============================================================================
# LOGGING
# ============================================================================
logger = logging.getLogger("synesis2neo4j")
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# ============================================================================
# RESULT TYPES (Pattern: Result Types)
# ============================================================================
@dataclass
class PipelineError:
    """Base pipeline error with context."""
    message: str
    stage: str
    details: Optional[str] = None


@dataclass
class CompilationError(PipelineError):
    """Error in Synesis project compilation."""
    diagnostics: List[str] = field(default_factory=list)


@dataclass
class ConnectionError(PipelineError):
    """Error connecting to Neo4j."""
    pass


@dataclass
class SyncError(PipelineError):
    """Error synchronizing with the database."""
    pass


@dataclass
class ChainFieldSpec:
    """Specification of a CHAIN field from the template."""
    field_name: str
    relations: Dict[str, str]  # {type: description}


@dataclass
class CodeFieldSpec:
    """Specification of a CODE field from the template."""
    field_name: str
    description: str


@dataclass
class GraphPayload:
    """Payload prepared for Neo4j synchronization."""
    project_name: str
    concept_label: str  # Dynamic label for concept nodes (CHAIN/CODE field name)
    scalar_fields: List[str]
    graph_fields: List[str]
    chain_fields: List[ChainFieldSpec]
    code_fields: List[CodeFieldSpec]
    source_fields: List[str]  # Dynamic properties for Source nodes (SCOPE SOURCE)
    value_maps: Dict[str, List[Dict[str, Any]]]  # Mapping of indices to labels
    concepts: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]  # Previously "references"
    items: List[Dict[str, Any]]
    chains: List[Dict[str, Any]]
    mentions: List[Dict[str, Any]]
    from_source: List[Dict[str, Any]]


@dataclass
class PipelineResult:
    """Pipeline result with success or error."""
    success: bool
    error: Optional[PipelineError] = None
    stats: Dict[str, int] = field(default_factory=dict)


# ============================================================================
# SANITIZATION (Protection against Cypher Injection)
# ============================================================================
_CYPHER_LABEL_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def sanitize_cypher_label(label: str) -> str:
    """
    Sanitizes string for safe use as label/relationship type in Cypher.

    Keeps only alphanumeric characters and underscore.
    Ensures it starts with a letter or underscore.
    """
    sanitized = "".join(c for c in label if c.isalnum() or c == "_")
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "Unknown"


def sanitize_database_name(name: str) -> str:
    """
    Sanitizes string for use as Neo4j database name.

    Neo4j accepts only: ASCII letters, numbers, dots and hyphens.
    Underscores are converted to hyphens.
    """
    # Convert underscores to hyphens
    name = name.replace("_", "-")
    # Keep only valid characters
    sanitized = "".join(c for c in name if c.isalnum() or c in ".-")
    # Ensure it starts with a letter
    if sanitized and not sanitized[0].isalpha():
        sanitized = "db" + sanitized
    return sanitized.lower() or "synesis"


def validate_cypher_label(label: str) -> bool:
    """Validates if label is safe for direct use in Cypher."""
    return bool(_CYPHER_LABEL_PATTERN.match(label))


# ============================================================================
# USER INTERFACE
# ============================================================================
class TaskReporter:
    """
    Reporter for visual pipeline feedback.

    Uses Rich when available, falls back to standard logging.
    Received via dependency injection in pipeline functions.
    """

    def __init__(self, title: str):
        self.console = Console() if RICH_AVAILABLE else None
        self.stats: Dict[str, int] = {"errors": 0, "warnings": 0, "successes": 0}
        self.start_time = time.time()
        if self.console:
            self.console.print(Panel(f"[bold cyan]{title}[/]", border_style="cyan"))

    def info(self, msg: str) -> None:
        if self.console:
            self.console.print(f"[bold blue]>[/] {msg}")
        else:
            logger.info(msg)

    def success(self, msg: str) -> None:
        self.stats["successes"] += 1
        if self.console:
            self.console.print(f"[bold green][+][/] {msg}")
        else:
            logger.info(msg)

    def warning(self, msg: str) -> None:
        self.stats["warnings"] += 1
        if self.console:
            self.console.print(f"[bold yellow][!][/] {msg}")
        else:
            logger.warning(msg)

    def error(self, msg: str) -> None:
        self.stats["errors"] += 1
        if self.console:
            self.console.print(f"[bold red][x][/] {msg}")
        else:
            logger.error(msg)

    def step(self, desc: str) -> "_StepContext":
        return _StepContext(self, desc)

    def print_diagnostics(self, diagnostics: List[str]) -> None:
        """Displays Synesis compilation errors."""
        if not self.console:
            for d in diagnostics:
                logger.error(d)
            return

        table = Table(title="Compilation Diagnostics", box=box.SIMPLE, style="red")
        table.add_column("Mensagem", style="white")
        for diag in diagnostics:
            table.add_row(str(diag))
        self.console.print(table)

    def print_summary(self) -> None:
        duration = int(time.time() - self.start_time)
        if self.console:
            table = Table(box=box.ROUNDED, show_header=False)
            table.add_row("Tempo Total", f"{duration}s")
            status = "[green]SUCCESS[/]" if self.stats["errors"] == 0 else "[red]FAIL[/]"
            table.add_row("Status", status)
            self.console.print(Panel(table, title="Summary", border_style="cyan"))
        else:
            status = "SUCCESS" if self.stats["errors"] == 0 else "FALHA"
            logger.info(f"Summary: {status} in {duration}s")


class _StepContext:
    """Context manager for pipeline steps with visual feedback."""

    def __init__(self, reporter: TaskReporter, description: str):
        self.reporter = reporter
        self.description = description
        self._status = None

    def __enter__(self) -> "_StepContext":
        if self.reporter.console:
            self._status = self.reporter.console.status(f"[bold cyan]{self.description}...[/]")
            self._status.__enter__()
        else:
            logger.info(f"--- {self.description} ---")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._status:
            self._status.__exit__(exc_type, exc, tb)
        if exc:
            self.reporter.error(f"{self.description} falhou: {exc}")
        else:
            self.reporter.success(f"{self.description} concluído.")
        return False


# ============================================================================
# TEMPLATE ANALYSIS
# ============================================================================
def analyze_template(template_data: Dict[str, Any]) -> tuple[List[str], List[str], List[ChainFieldSpec], List[CodeFieldSpec], Dict[str, List[Dict]], List[str]]:
    """
    Analyzes Synesis template to identify scalar, relational, CHAIN, CODE and SOURCE fields.

    Returns:
        Tuple (scalar_fields, graph_fields, chain_fields, code_fields, value_maps, source_fields).
        - graph_fields become taxonomy nodes
        - chain_fields define nodes with self-referential relations (triples)
        - code_fields define references to concepts (list of codes)
        - value_maps maps numeric indices to labels (for ORDERED/ENUMERATED)
        - source_fields become dynamic properties on Source nodes
    """
    field_specs = template_data.get("field_specs", {})

    scalar_fields: List[str] = []
    graph_fields: List[str] = []
    chain_fields: List[ChainFieldSpec] = []
    code_fields: List[CodeFieldSpec] = []
    value_maps: Dict[str, List[Dict]] = {}
    source_fields: List[str] = []

    # Iterate through all fields and filter by scope
    for field_name, spec in field_specs.items():
        scope = spec.get("scope", "").upper()
        field_type = spec.get("type", "TEXT")

        if scope == "ONTOLOGY":
            if field_type in ("TOPIC", "ENUMERATED", "ORDERED"):
                graph_fields.append(field_name)
                # Store value mapping for ORDERED/ENUMERATED fields
                if spec.get("values"):
                    value_maps[field_name] = spec["values"]
            else:
                scalar_fields.append(field_name)

        elif scope == "ITEM":
            if field_type == "CHAIN":
                relations = spec.get("relations", {})
                chain_fields.append(ChainFieldSpec(
                    field_name=field_name,
                    relations=relations
                ))
            elif field_type == "CODE":
                code_fields.append(CodeFieldSpec(
                    field_name=field_name,
                    description=spec.get("description", "")
                ))

        elif scope == "SOURCE":
            source_fields.append(field_name)

    return scalar_fields, graph_fields, chain_fields, code_fields, value_maps, source_fields


def get_taxonomy_labels(graph_fields: List[str]) -> List[str]:
    """Converts field names to sanitized Neo4j labels."""
    return [sanitize_cypher_label(f.capitalize()) for f in graph_fields]


# ============================================================================
# COMPILATION AND PREPARATION
# ============================================================================
def compile_project(
    project_path: Path,
    reporter: TaskReporter
) -> Union[GraphPayload, CompilationError]:
    """
    Compiles Synesis project and transforms into payload for Neo4j.

    Args:
        project_path: Path to .synp file
        reporter: Reporter for visual feedback

    Returns:
        GraphPayload on success, CompilationError on failure.
    """
    reporter.info(f"Starting Synesis compiler at: {project_path}")

    compiler = SynesisCompiler(project_path)
    result = compiler.compile()

    if not result.success:
        return CompilationError(
            message="Falha na compilação do projeto Synesis",
            stage="compilation",
            diagnostics=[str(d) for d in result.get_diagnostics()]
        )

    # Export to temporary JSON and read back
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp:
        tmp_path = Path(tmp.name)

    result.to_json(tmp_path)

    with open(tmp_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)

    tmp_path.unlink()  # Remove temporary file

    corpus_count = len(json_data.get("corpus", []))
    reporter.success(f"Compilation OK. {corpus_count} items processed.")

    scalar_fields, graph_fields, chain_fields, code_fields, value_maps, source_fields = analyze_template(json_data["template"])

    payload = _build_graph_payload(
        json_data=json_data,
        scalar_fields=scalar_fields,
        graph_fields=graph_fields,
        chain_fields=chain_fields,
        code_fields=code_fields,
        value_maps=value_maps,
        source_fields=source_fields
    )

    return payload


def _build_graph_payload(
    json_data: Dict[str, Any],
    scalar_fields: List[str],
    graph_fields: List[str],
    chain_fields: List[ChainFieldSpec],
    code_fields: List[CodeFieldSpec],
    value_maps: Dict[str, List[Dict[str, Any]]],
    source_fields: List[str]
) -> GraphPayload:
    """Transforms compiled JSON data into structured payload for Neo4j."""
    project_name = json_data.get("project", {}).get("name", "synesis")
    ontology = json_data.get("ontology", {})
    corpus = json_data.get("corpus", [])
    bibliography = json_data.get("bibliography", {})

    # Determine dynamic label based on first CHAIN or CODE field
    if chain_fields:
        concept_label = sanitize_cypher_label(chain_fields[0].field_name.capitalize())
    elif code_fields:
        concept_label = sanitize_cypher_label(code_fields[0].field_name.capitalize())
    else:
        concept_label = "Concept"  # Fallback

    # Build relations map for quick lookup
    relation_definitions: Dict[str, str] = {}
    for cf in chain_fields:
        relation_definitions.update(cf.relations)

    # Extract CODE field names for corpus search
    code_field_names = [cf.field_name for cf in code_fields]

    concepts = _extract_concepts(ontology, scalar_fields, graph_fields, value_maps)
    sources, items, mentions, chains, from_source = _extract_corpus_data(
        corpus, bibliography, relation_definitions, code_field_names, source_fields
    )

    return GraphPayload(
        project_name=project_name,
        concept_label=concept_label,
        scalar_fields=scalar_fields,
        graph_fields=graph_fields,
        chain_fields=chain_fields,
        code_fields=code_fields,
        source_fields=source_fields,
        value_maps=value_maps,
        concepts=concepts,
        sources=sources,
        items=items,
        chains=chains,
        mentions=mentions,
        from_source=from_source
    )


def _index_to_label(value: Any, value_map: List[Dict[str, Any]]) -> str:
    """Converts numeric index to label using the value mapping."""
    if isinstance(value, int):
        for entry in value_map:
            if entry.get("index") == value:
                return entry.get("label", str(value))
        return str(value)
    return str(value)


def _extract_concepts(
    ontology: Dict[str, Any],
    scalar_fields: List[str],
    graph_fields: List[str],
    value_maps: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Extracts concepts from ontology with properties and relations."""
    concepts = []

    for name, entry in ontology.items():
        fields = entry.get("fields", {})
        props: Dict[str, Any] = {
            "name": name,
            "description": entry.get("description"),
            "created": int(time.time())
        }

        for sf in scalar_fields:
            if sf in fields:
                props[sf] = fields[sf]

        relations: Dict[str, List[str]] = {}
        for gf in graph_fields:
            if gf in fields:
                raw_val = fields[gf]
                # Convert value to label if mapping exists
                if gf in value_maps:
                    if isinstance(raw_val, list):
                        relations[gf] = [_index_to_label(v, value_maps[gf]) for v in raw_val]
                    else:
                        relations[gf] = [_index_to_label(raw_val, value_maps[gf])]
                else:
                    # No mapping, use value directly
                    relations[gf] = raw_val if isinstance(raw_val, list) else [raw_val]

        concepts.append({"props": props, "relations": relations})

    return concepts


def _extract_corpus_data(
    corpus: List[Dict[str, Any]],
    bibliography: Dict[str, Any],
    relation_definitions: Dict[str, str],
    code_field_names: List[str],
    source_fields: List[str]
) -> tuple[
    List[Dict[str, Any]],  # sources
    List[Dict[str, Any]],  # items
    List[Dict[str, Any]],  # mentions
    List[Dict[str, Any]],  # chains
    List[Dict[str, Any]]   # from_source
]:
    """
    Extracts sources, items and relationships from corpus.

    Supports two template patterns:
    - CHAIN: triples (source, relation, target) with note as description
    - CODE: list of codes referencing concepts
    """
    sources: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    mentions: List[Dict[str, Any]] = []
    chains: List[Dict[str, Any]] = []
    from_source: List[Dict[str, Any]] = []
    seen_refs: set[str] = set()

    for corpus_item in corpus:
        source_ref = corpus_item["source_ref"].lstrip("@")
        corpus_id = corpus_item["id"]

        # Extract source (SOURCE...END SOURCE block)
        if source_ref not in seen_refs:
            source_props = _build_source_props(source_ref, corpus_item, bibliography, source_fields)
            sources.append(source_props)
            seen_refs.add(source_ref)

        data = corpus_item["data"]

        # Detect template pattern
        has_chain = "chain" in data and data["chain"]
        has_code = any(cf in data and data[cf] for cf in code_field_names)

        if has_chain:
            # CHAIN pattern (bibliometrics): note/chain bundles
            notes = data.get("note", [])
            chain_list = data.get("chain", [])

            for idx, (note, chain) in enumerate(zip(notes, chain_list), 1):
                item_id = f"{corpus_id}_n{idx:04d}"

                items.append({
                    "item_id": item_id,
                    "citation": data.get("text", ""),
                    "description": note
                })
                from_source.append({"item_id": item_id, "ref": source_ref})

                nodes = chain.get("nodes", [])
                if len(nodes) >= 3:
                    src, rel, tgt = (n.strip() for n in nodes[:3])
                    mentions.append({"item_id": item_id, "concept": src, "order": 1})
                    mentions.append({"item_id": item_id, "concept": tgt, "order": 2})

                    # Normalize relation type and lookup description
                    rel_type = rel.upper().replace(" ", "_").replace("-", "_")
                    rel_description = relation_definitions.get(rel, "")

                    chains.append({
                        "source": src,
                        "target": tgt,
                        "type": rel_type,
                        "description": rel_description,
                        "item_id": item_id
                    })

        elif has_code:
            # CODE pattern (gestao_fe): code field bundles
            # Find the first CODE field with data
            code_field = next((cf for cf in code_field_names if cf in data and data[cf]), None)
            if not code_field:
                continue

            code_list = data[code_field]
            if not isinstance(code_list, list):
                code_list = [code_list]

            # Extract descriptions if available (corresponding bundled field)
            descriptions = data.get("justificativa_interna", []) or data.get("descricao", [])
            if not isinstance(descriptions, list):
                descriptions = [descriptions] * len(code_list)

            # Extract base text (first MEMO or TEXT field found)
            base_text = ""
            for field_name in ["ordem_1a", "text", "citation"]:
                if field_name in data and data[field_name]:
                    val = data[field_name]
                    base_text = val[0] if isinstance(val, list) else val
                    break

            for idx, code in enumerate(code_list, 1):
                item_id = f"{corpus_id}_c{idx:04d}"
                description = descriptions[idx-1] if idx <= len(descriptions) else ""

                items.append({
                    "item_id": item_id,
                    "citation": base_text,
                    "description": description
                })
                from_source.append({"item_id": item_id, "ref": source_ref})
                mentions.append({"item_id": item_id, "concept": code, "order": 1})

    return sources, items, mentions, chains, from_source


def _build_source_props(
    source_ref: str,
    item: Dict[str, Any],
    bibliography: Dict[str, Any],
    source_fields: List[str]
) -> Dict[str, Any]:
    """Builds properties of a Source node (SOURCE...END SOURCE block)."""
    bib_entry = bibliography.get(source_ref, {})
    source_meta = item.get("source_metadata", {})

    props: Dict[str, Any] = {"bibtex": source_ref}

    # Standard bibliographic fields (from bibliography entry)
    for key in ("title", "author", "year", "doi", "journal", "abstract"):
        val = source_meta.get(key) or bib_entry.get(key)
        if val is not None:
            props[key] = val

    # Dynamic fields from template (SCOPE SOURCE)
    for field_name in source_fields:
        if field_name in source_meta and source_meta[field_name] is not None:
            props[field_name] = source_meta[field_name]

    return props


# ============================================================================
# NEO4J SYNCHRONIZATION
# ============================================================================
def clear_database(session: Any) -> None:
    """
    Clears all data from the database, including constraints and indexes.

    Ensures that the source of truth is always the compiler data.
    """
    # Remove all existing constraints
    constraints = session.run("SHOW CONSTRAINTS").data()
    for c in constraints:
        constraint_name = c.get("name")
        if constraint_name:
            session.run(f"DROP CONSTRAINT {constraint_name} IF EXISTS")

    # Remove all existing indexes (except automatically created ones)
    indexes = session.run("SHOW INDEXES").data()
    for idx in indexes:
        if idx.get("owningConstraint") is None:  # Not a constraint index
            idx_name = idx.get("name")
            if idx_name:
                session.run(f"DROP INDEX {idx_name} IF EXISTS")

    # Clear all nodes and relationships
    session.run("MATCH (n) DETACH DELETE n")


def sync_to_neo4j(session: Any, payload: GraphPayload) -> Optional[SyncError]:
    """
    Synchronizes payload with Neo4j in a single transaction.

    Clears the database completely before synchronizing, ensuring that
    the compiler is the source of truth.

    Args:
        session: Active Neo4j session
        payload: Data prepared for persistence

    Returns:
        None on success, SyncError on failure.
    """
    try:
        # Clear database before synchronizing (source of truth = compiler)
        clear_database(session)
        _create_constraints(session, payload.graph_fields, payload.concept_label)
        _execute_sync_transaction(session, payload)
        return None
    except Exception as e:
        return SyncError(
            message="Synchronization failed",
            stage="sync",
            details=str(e)
        )


def _create_constraints(session: Any, graph_fields: List[str], concept_label: str) -> None:
    """Creates uniqueness constraints in Neo4j schema."""
    # Constraints for dynamic taxonomies
    for label in get_taxonomy_labels(graph_fields):
        if validate_cypher_label(label):
            session.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE"
            )

    # Fixed constraints
    session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (s:Source) REQUIRE s.bibtex IS UNIQUE")
    session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (i:Item) REQUIRE i.item_id IS UNIQUE")

    # Constraint for dynamic label (based on CHAIN/CODE field)
    if validate_cypher_label(concept_label):
        session.run(
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (c:{concept_label}) REQUIRE c.name IS UNIQUE"
        )


def _execute_sync_transaction(session: Any, payload: GraphPayload) -> None:
    """Executes all sync operations in a single transaction."""
    with session.begin_transaction() as tx:
        _sync_sources(tx, payload.sources)
        _sync_items(tx, payload.items)
        _sync_from_source(tx, payload.from_source)
        _sync_concepts(tx, payload.chains, payload.concepts, payload.concept_label)
        _sync_taxonomies(tx, payload.concepts, payload.graph_fields, payload.concept_label)
        _sync_mentions(tx, payload.mentions, payload.concept_label)
        tx.commit()


def _sync_sources(tx: Any, sources: List[Dict[str, Any]]) -> None:
    """Synchronizes Source nodes (corresponding to SOURCE...END SOURCE block)."""
    if not sources:
        return
    tx.run("""
        UNWIND $rows AS row
        MERGE (s:Source {bibtex: row.bibtex})
        SET s += row, s.last_updated = timestamp()
    """, rows=sources)


def _sync_items(tx: Any, items: List[Dict[str, Any]]) -> None:
    if not items:
        return
    tx.run("""
        UNWIND $rows AS row
        MERGE (i:Item {item_id: row.item_id})
        SET i += row, i.last_updated = timestamp()
    """, rows=items)


def _sync_from_source(tx: Any, from_source: List[Dict[str, Any]]) -> None:
    """Connects Item to the Source from which it was extracted."""
    if not from_source:
        return
    tx.run("""
        UNWIND $rows AS row
        MATCH (i:Item {item_id: row.item_id})
        MATCH (s:Source {bibtex: row.ref})
        MERGE (i)-[:FROM_SOURCE]->(s)
    """, rows=from_source)


# Mapping of fields to semantic relationship names
TAXONOMY_RELATION_MAP: Dict[str, str] = {
    "topic": "GROUPED_BY",
    "aspect": "QUALIFIED_BY",
    "dimension": "BELONGS_TO",
    "confidence": "RATED_AS",
}


def _get_taxonomy_relation(field_name: str) -> str:
    """Returns semantic relationship name for the field, or HAS_* as fallback."""
    return TAXONOMY_RELATION_MAP.get(field_name.lower(), f"HAS_{field_name.upper()}")


def _sync_taxonomies(
    tx: Any,
    concepts: List[Dict[str, Any]],
    graph_fields: List[str],
    concept_label: str
) -> None:
    """
    Creates taxonomy nodes and semantic relationships from concept nodes.

    Relations:
    - Concept -> Topic via GROUPED_BY
    - Concept -> Aspect via QUALIFIED_BY
    - Concept -> Dimension via BELONGS_TO
    - Topic -> Topic via IS_LINKED_TO (self-referential)
    - Topic -> Aspect via MAPPED_TO_ASPECT
    - Topic -> Dimension via MAPPED_TO_DIMENSION
    """
    if not concepts:
        return

    # First: create taxonomy nodes and Concept -> Taxonomy relations
    for field_name in graph_fields:
        label = sanitize_cypher_label(field_name.capitalize())
        rel_type = _get_taxonomy_relation(field_name)

        if not validate_cypher_label(label) or not validate_cypher_label(rel_type):
            continue

        query = f"""
            UNWIND $rows AS row
            WITH row
            WHERE row.relations['{field_name}'] IS NOT NULL
            MATCH (c:{concept_label} {{name: row.props.name}})
            UNWIND row.relations['{field_name}'] AS val
            MERGE (t:{label} {{name: val}})
            MERGE (c)-[:{rel_type}]->(t)
        """
        tx.run(query, rows=concepts)

    # Second: create mapping relations between taxonomies
    # Topic -> Aspect (MAPPED_TO_ASPECT)
    if "topic" in graph_fields and "aspect" in graph_fields:
        tx.run("""
            UNWIND $rows AS row
            WITH row
            WHERE row.relations['topic'] IS NOT NULL AND row.relations['aspect'] IS NOT NULL
            UNWIND row.relations['topic'] AS topic_val
            UNWIND row.relations['aspect'] AS aspect_val
            MATCH (topic:Topic {name: topic_val})
            MATCH (aspect:Aspect {name: aspect_val})
            MERGE (topic)-[:MAPPED_TO_ASPECT]->(aspect)
        """, rows=concepts)

    # Topic -> Dimension (MAPPED_TO_DIMENSION)
    if "topic" in graph_fields and "dimension" in graph_fields:
        tx.run("""
            UNWIND $rows AS row
            WITH row
            WHERE row.relations['topic'] IS NOT NULL AND row.relations['dimension'] IS NOT NULL
            UNWIND row.relations['topic'] AS topic_val
            UNWIND row.relations['dimension'] AS dimension_val
            MATCH (topic:Topic {name: topic_val})
            MATCH (dimension:Dimension {name: dimension_val})
            MERGE (topic)-[:MAPPED_TO_DIMENSION]->(dimension)
        """, rows=concepts)

    # Topic -> Topic (IS_LINKED_TO) - connects topics via RELATES_TO between their concepts
    # strength = number of RELATES_TO relations between concepts of both topics
    if "topic" in graph_fields:
        tx.run(f"""
            MATCH (t1:Topic)<-[:GROUPED_BY]-(f1:{concept_label})-[:RELATES_TO]->(f2:{concept_label})-[:GROUPED_BY]->(t2:Topic)
            WHERE t1 <> t2
            WITH t1, t2, count(*) AS strength
            MERGE (t1)-[r:IS_LINKED_TO]->(t2)
            SET r.strength = strength, r.last_updated = timestamp()
        """)


def _sync_mentions(tx: Any, mentions: List[Dict[str, Any]], concept_label: str) -> None:
    """Connects Item to mentioned concept nodes."""
    if not mentions:
        return
    tx.run(f"""
        UNWIND $rows AS row
        MATCH (i:Item {{item_id: row.item_id}})
        MATCH (c:{concept_label} {{name: row.concept}})
        MERGE (i)-[m:MENTIONS]->(c)
        SET m.order = row.order
    """, rows=mentions)


def _sync_concepts(tx: Any, chains: List[Dict[str, Any]], concepts: List[Dict[str, Any]], concept_label: str) -> None:
    """
    Creates concept nodes (dynamic label based on CHAIN/CODE field) and RELATES_TO relations.

    Nodes are created from:
    1. Ontology concepts (always)
    2. Source/target from chains (when they exist)

    The RELATES_TO relation connects concepts with type and description as
    edge attributes (only for templates with CHAIN field).
    """
    # First: create concept nodes from ontology
    if concepts:
        tx.run(f"""
            UNWIND $rows AS row
            MERGE (c:{concept_label} {{name: row.props.name}})
            SET c += row.props
        """, rows=concepts)

    # If there are no chains, nothing more to do
    if not chains:
        return

    # Second: create concept nodes from chains that don't exist in ontology
    tx.run(f"""
        UNWIND $rows AS row
        MERGE (s:{concept_label} {{name: row.source}})
        MERGE (t:{concept_label} {{name: row.target}})
    """, rows=chains)

    # Third: create RELATES_TO relations with attributes
    tx.run(f"""
        UNWIND $rows AS row
        MATCH (s:{concept_label} {{name: row.source}})
        MATCH (t:{concept_label} {{name: row.target}})
        MERGE (s)-[r:RELATES_TO]->(t)
        SET r.type = row.type,
            r.description = row.description,
            r.item_id = row.item_id
    """, rows=chains)


# ============================================================================
# GRAPH METRICS
# ============================================================================
def _is_gds_available(session: Any) -> bool:
    """Checks if the GDS plugin is installed."""
    try:
        result = session.run("RETURN gds.version() AS version")
        version = result.single()["version"]
        logger.info(f"GDS detectado: versão {version}")
        return True
    except Exception:
        return False


def _get_graph_strategy(payload: GraphPayload) -> str:
    """
    Determines the graph strategy for GDS metrics.

    Preference hierarchy:
    1. RELATES_TO - explicit relation (CHAIN templates)
    2. CO_TAXONOMY - weighted co-taxonomy (CODE templates with TOPIC)
    3. CO_CITATION - co-citation via Source (fallback)
    """
    if payload.chains:
        return "RELATES_TO"
    elif payload.graph_fields:
        return "CO_TAXONOMY"
    else:
        return "CO_CITATION"


def compute_metrics(
    session: Any,
    payload: GraphPayload,
    reporter: TaskReporter
) -> None:
    """
    Calculates graph metrics: native (Cypher) and advanced (GDS).

    Native metrics are always calculated.
    GDS metrics are calculated if the plugin is available.
    """
    concept_label = payload.concept_label
    graph_fields = payload.graph_fields

    # 1. Native metrics (always run)
    with reporter.step("Calculating Native Metrics"):
        _compute_native_concept_metrics(session, concept_label)
        _compute_native_taxonomy_metrics(session, concept_label, graph_fields)
        _compute_native_source_metrics(session, concept_label)

    # 2. GDS metrics (optional with fallback)
    if not _is_gds_available(session):
        reporter.warning(
            "GDS not installed. Install the Graph Data Science plugin for "
            "advanced metrics (PageRank, Betweenness, Communities)."
        )
        return

    strategy = _get_graph_strategy(payload)
    reporter.info(f"GDS graph strategy: {strategy}")

    with reporter.step("Calculating GDS Metrics"):
        try:
            _compute_gds_metrics(session, payload, strategy, reporter)
        except Exception as e:
            reporter.warning(f"Error calculating GDS metrics: {e}")


# ----------------------------------------------------------------------------
# NATIVE METRICS (Pure Cypher - always available)
# ----------------------------------------------------------------------------
def _compute_native_concept_metrics(session: Any, concept_label: str) -> None:
    """
    Calculates native metrics for concept nodes.

    Metrics:
    - degree: total degree (in + out)
    - in_degree: incoming relations
    - out_degree: outgoing relations
    - mention_count: Items that mention the concept
    - source_count: distinct Sources where it appears
    """
    if not validate_cypher_label(concept_label):
        return

    # Degree centrality (based on RELATES_TO)
    session.run(f"""
        MATCH (c:{concept_label})
        OPTIONAL MATCH (c)-[:RELATES_TO]->(out)
        OPTIONAL MATCH (c)<-[:RELATES_TO]-(in)
        WITH c, count(DISTINCT out) AS out_deg, count(DISTINCT in) AS in_deg
        SET c.out_degree = out_deg,
            c.in_degree = in_deg,
            c.degree = out_deg + in_deg
    """)

    # Mention count and source count
    session.run(f"""
        MATCH (c:{concept_label})
        OPTIONAL MATCH (c)<-[:MENTIONS]-(i:Item)
        OPTIONAL MATCH (i)-[:FROM_SOURCE]->(s:Source)
        WITH c, count(DISTINCT i) AS mentions, count(DISTINCT s) AS sources
        SET c.mention_count = mentions,
            c.source_count = sources
    """)


def _compute_native_taxonomy_metrics(
    session: Any,
    concept_label: str,
    graph_fields: List[str]
) -> None:
    """
    Calculates native metrics for taxonomy nodes (Topic, Aspect, Dimension, etc).

    Metrics:
    - concept_count: classified concepts
    - weighted_degree: sum of IS_LINKED_TO strengths (if exists)
    - aspect_diversity: distinct aspects (if Topic)
    - dimension_diversity: distinct dimensions (if Topic)
    """
    if not validate_cypher_label(concept_label):
        return

    for field_name in graph_fields:
        label = sanitize_cypher_label(field_name.capitalize())
        rel_type = _get_taxonomy_relation(field_name)

        if not validate_cypher_label(label) or not validate_cypher_label(rel_type):
            continue

        # Concept count
        session.run(f"""
            MATCH (t:{label})<-[:{rel_type}]-(c:{concept_label})
            WITH t, count(c) AS cnt
            SET t.concept_count = cnt
        """)

    # Topic-specific metrics (if exists)
    if "topic" in graph_fields:
        # Weighted degree (sum of IS_LINKED_TO strengths)
        session.run("""
            MATCH (t:Topic)
            OPTIONAL MATCH (t)-[r:IS_LINKED_TO]-()
            WITH t, coalesce(sum(r.strength), 0) AS wd
            SET t.weighted_degree = wd
        """)

        # Aspect diversity (if aspect exists)
        if "aspect" in graph_fields:
            session.run(f"""
                MATCH (t:Topic)<-[:GROUPED_BY]-(c:{concept_label})
                OPTIONAL MATCH (c)-[:QUALIFIED_BY]->(a:Aspect)
                WITH t, count(DISTINCT a) AS div
                SET t.aspect_diversity = div
            """)

        # Dimension diversity (if dimension exists)
        if "dimension" in graph_fields:
            session.run(f"""
                MATCH (t:Topic)<-[:GROUPED_BY]-(c:{concept_label})
                OPTIONAL MATCH (c)-[:BELONGS_TO]->(d:Dimension)
                WITH t, count(DISTINCT d) AS div
                SET t.dimension_diversity = div
            """)


def _compute_native_source_metrics(session: Any, concept_label: str) -> None:
    """
    Calculates native metrics for Source nodes.

    Metrics:
    - item_count: Items extracted from the source
    - concept_count: mentioned concepts
    """
    if not validate_cypher_label(concept_label):
        return

    session.run(f"""
        MATCH (s:Source)
        OPTIONAL MATCH (s)<-[:FROM_SOURCE]-(i:Item)
        OPTIONAL MATCH (i)-[:MENTIONS]->(c:{concept_label})
        WITH s, count(DISTINCT i) AS items, count(DISTINCT c) AS concepts
        SET s.item_count = items,
            s.concept_count = concepts
    """)


# ----------------------------------------------------------------------------
# GDS METRICS (requires Graph Data Science plugin)
# ----------------------------------------------------------------------------
def _compute_gds_metrics(
    session: Any,
    payload: GraphPayload,
    strategy: str,
    reporter: TaskReporter
) -> None:
    """
    Calculates GDS metrics (PageRank, Betweenness, Louvain).

    Graph projection depends on strategy:
    - RELATES_TO: uses explicit relation
    - CO_TAXONOMY: uses weighted co-taxonomy
    - CO_CITATION: uses co-citation via Source
    """
    concept_label = payload.concept_label
    graph_name = "synesis_metrics_graph"

    # Clear previous projection if exists
    _drop_gds_graph(session, graph_name)

    # Create projection based on strategy
    node_count, rel_count = _create_gds_projection(
        session, graph_name, payload, strategy
    )

    if node_count == 0 or rel_count == 0:
        reporter.warning("Empty graph - skipping GDS metrics")
        return

    reporter.info(f"GDS projection: {node_count} nodes, {rel_count} relationships")

    # Calculate metrics
    try:
        _run_pagerank(session, graph_name, concept_label)
        reporter.success("PageRank calculated")
    except Exception as e:
        reporter.warning(f"PageRank failed: {e}")

    try:
        # Betweenness can be slow on large graphs
        _run_betweenness(session, graph_name, concept_label)
        reporter.success("Betweenness calculated")
    except Exception as e:
        reporter.warning(f"Betweenness failed: {e}")

    try:
        _run_louvain(session, graph_name, concept_label)
        reporter.success("Communities (Louvain) calculated")
    except Exception as e:
        reporter.warning(f"Louvain failed: {e}")

    # Clear projection
    _drop_gds_graph(session, graph_name)


def _drop_gds_graph(session: Any, graph_name: str) -> None:
    """Removes GDS projection if exists."""
    try:
        # Use YIELD graphName to avoid deprecated 'schema' field warning
        session.run(f"CALL gds.graph.drop('{graph_name}', false) YIELD graphName")
    except Exception:
        pass  # Ignore if doesn't exist


def _create_gds_projection(
    session: Any,
    graph_name: str,
    payload: GraphPayload,
    strategy: str
) -> tuple[int, int]:
    """
    Creates GDS projection based on strategy.

    Uses the new gds.graph.project aggregation function API (GDS 2.x+)
    instead of the deprecated gds.graph.project.cypher procedure.

    Returns:
        Tuple (node_count, relationship_count)
    """
    concept_label = payload.concept_label

    if strategy == "RELATES_TO":
        # Native projection - more efficient
        result = session.run(f"""
            CALL gds.graph.project(
                '{graph_name}',
                '{concept_label}',
                'RELATES_TO'
            )
            YIELD nodeCount, relationshipCount
            RETURN nodeCount, relationshipCount
        """)

    elif strategy == "CO_TAXONOMY":
        # Projection via weighted co-taxonomy using aggregation function
        # Build taxonomy relations list dynamically
        taxonomy_rels = []
        for field_name in payload.graph_fields:
            rel_type = _get_taxonomy_relation(field_name)
            if validate_cypher_label(rel_type):
                taxonomy_rels.append(rel_type)

        if not taxonomy_rels:
            return (0, 0)

        rel_pattern = "|".join(taxonomy_rels)

        # New aggregation function API (replaces deprecated gds.graph.project.cypher)
        result = session.run(f"""
            MATCH (f1:{concept_label})-[:{rel_pattern}]->(t)<-[:{rel_pattern}]-(f2:{concept_label})
            WHERE f1 <> f2
            WITH f1, f2, count(DISTINCT t) AS weight
            WITH gds.graph.project(
                '{graph_name}',
                f1,
                f2,
                {{relationshipProperties: {{weight: weight}}}}
            ) AS g
            RETURN g.nodeCount AS nodeCount, g.relationshipCount AS relationshipCount
        """)

    else:  # CO_CITATION
        # Projection via co-citation (Source) using aggregation function
        result = session.run(f"""
            MATCH (f1:{concept_label})<-[:MENTIONS]-(:Item)-[:FROM_SOURCE]->(s:Source)
                  <-[:FROM_SOURCE]-(:Item)-[:MENTIONS]->(f2:{concept_label})
            WHERE f1 <> f2
            WITH f1, f2, count(DISTINCT s) AS weight
            WITH gds.graph.project(
                '{graph_name}',
                f1,
                f2,
                {{relationshipProperties: {{weight: weight}}}}
            ) AS g
            RETURN g.nodeCount AS nodeCount, g.relationshipCount AS relationshipCount
        """)

    record = result.single()
    return (record["nodeCount"], record["relationshipCount"])


def _run_pagerank(session: Any, graph_name: str, concept_label: str) -> None:
    """Executes PageRank and persists in nodes."""
    session.run(f"""
        CALL gds.pageRank.stream('{graph_name}')
        YIELD nodeId, score
        WITH gds.util.asNode(nodeId) AS node, score
        WHERE '{concept_label}' IN labels(node)
        SET node.pagerank = score
    """)


def _run_betweenness(session: Any, graph_name: str, concept_label: str) -> None:
    """Executes Betweenness Centrality and persists in nodes."""
    session.run(f"""
        CALL gds.betweenness.stream('{graph_name}')
        YIELD nodeId, score
        WITH gds.util.asNode(nodeId) AS node, score
        WHERE '{concept_label}' IN labels(node)
        SET node.betweenness = score
    """)


def _run_louvain(session: Any, graph_name: str, concept_label: str) -> None:
    """Executes Louvain (community detection) and persists in nodes."""
    session.run(f"""
        CALL gds.louvain.stream('{graph_name}')
        YIELD nodeId, communityId
        WITH gds.util.asNode(nodeId) AS node, communityId
        WHERE '{concept_label}' IN labels(node)
        SET node.community = communityId
    """)


# ============================================================================
# CONFIGURATION
# ============================================================================
@dataclass
class Neo4jConfig:
    """Neo4j connection configuration."""
    uri: str
    user: str
    password: str
    database: str = "neo4j"


def load_config(config_path: Path) -> Union[Neo4jConfig, ConnectionError]:
    """Loads Neo4j configuration from TOML file."""
    if not config_path.exists():
        return ConnectionError(
            message="Configuration file not found",
            stage="config",
            details=str(config_path)
        )

    try:
        cfg = tomllib.loads(config_path.read_text("utf-8"))["neo4j"]
        # Accept both 'uri' and 'URI'
        uri = cfg.get("uri") or cfg.get("URI")
        if not uri:
            raise KeyError("'uri'")
        return Neo4jConfig(
            uri=uri,
            user=cfg["user"],
            password=cfg["password"],
            database=cfg.get("database", "neo4j")
        )
    except KeyError as e:
        return ConnectionError(
            message="Incomplete configuration",
            stage="config",
            details=f"Required field missing: {e}"
        )
    except Exception as e:
        return ConnectionError(
            message="Error reading configuration",
            stage="config",
            details=str(e)
        )


# ============================================================================
# DATABASE CREATION
# ============================================================================
def ensure_database_exists(driver: Any, database_name: str, reporter: TaskReporter) -> Optional[SyncError]:
    """
    Creates the database if it doesn't exist.

    Neo4j Community Edition only supports one database, so it fails silently if not supported.
    Neo4j Enterprise/Aura support multiple databases.
    """
    safe_name = sanitize_database_name(database_name)

    try:
        with driver.session(database="system") as session:
            # Check if database exists
            result = session.run("SHOW DATABASES")
            existing = {record["name"] for record in result}

            if safe_name not in existing:
                reporter.info(f"Creating database: {safe_name}")
                session.run(f"CREATE DATABASE `{safe_name}` IF NOT EXISTS")
                # Wait for database to become available
                import time as _time
                _time.sleep(2)
            else:
                reporter.info(f"Database already exists: {safe_name}")
        return None
    except Exception as e:
        # If fails (e.g.: Community Edition), try using default database
        error_msg = str(e)
        if "Unsupported" in error_msg or "not supported" in error_msg.lower():
            reporter.warning(f"Multi-database not supported. Using default database.")
            return None
        return SyncError(
            message="Failed to create database",
            stage="database_setup",
            details=error_msg
        )


def get_database_name_from_project(json_data: Dict[str, Any]) -> str:
    """Extracts project name to use as database name."""
    project_name = json_data.get("project", {}).get("name", "synesis")
    # Sanitize to valid database name (Neo4j only accepts letters, numbers, dots and hyphens)
    return sanitize_database_name(project_name)


# ============================================================================
# MAIN PIPELINE
# ============================================================================
def run_pipeline(
    project_path: Path,
    config_path: Path,
    reporter: TaskReporter
) -> PipelineResult:
    """
    Executes complete pipeline: compilation → connection → synchronization.

    Args:
        project_path: Path to .synp project
        config_path: Path to config.toml
        reporter: Reporter for visual feedback

    Returns:
        PipelineResult indicating success or typed error.
    """
    # 1. Input validation
    if not project_path.exists():
        return PipelineResult(
            success=False,
            error=CompilationError(
                message="Project not found",
                stage="validation",
                details=str(project_path)
            )
        )

    # 2. Compilation
    with reporter.step("Compiling Project (In-Memory)"):
        compile_result = compile_project(project_path, reporter)
        if isinstance(compile_result, CompilationError):
            reporter.print_diagnostics(compile_result.diagnostics)
            return PipelineResult(success=False, error=compile_result)
        payload = compile_result

    # 3. Configuration
    with reporter.step("Loading Configuration"):
        config_result = load_config(config_path)
        if isinstance(config_result, ConnectionError):
            return PipelineResult(success=False, error=config_result)
        config = config_result

    # 4. Synchronization
    if GraphDatabase is None:
        return PipelineResult(
            success=False,
            error=ConnectionError(
                message="Neo4j driver not installed",
                stage="connection",
                details="pip install neo4j"
            )
        )

    # Database name based on project
    db_name = sanitize_database_name(payload.project_name)
    reporter.info(f"Target database: {db_name}")

    try:
        with GraphDatabase.driver(config.uri, auth=(config.user, config.password)) as driver:
            # 4a. Create database if needed
            with reporter.step("Checking/Creating Database"):
                db_error = ensure_database_exists(driver, db_name, reporter)
                if db_error:
                    return PipelineResult(success=False, error=db_error)

            # 4b. Synchronize data
            with driver.session(database=db_name) as session:
                with reporter.step("Synchronizing Graph (Transactional)"):
                    sync_error = sync_to_neo4j(session, payload)
                    if sync_error:
                        return PipelineResult(success=False, error=sync_error)

                # 4c. Calculate graph metrics
                compute_metrics(session, payload, reporter)
    except Exception as e:
        return PipelineResult(
            success=False,
            error=ConnectionError(
                message="Failed to connect to Neo4j",
                stage="connection",
                details=str(e)
            )
        )

    return PipelineResult(
        success=True,
        stats={
            "concepts": len(payload.concepts),
            "sources": len(payload.sources),
            "items": len(payload.items),
            "chains": len(payload.chains)
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synesis Direct Link → Neo4j",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python synesis2neo4j.py --project ./meu_projeto.synp
  python synesis2neo4j.py --project ./analise.synp --config prod.toml
        """
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"synesis2neo4j {__version__}"
    )
    parser.add_argument("--project", required=True, help="Caminho para o arquivo .synp")
    parser.add_argument("--config", default="config.toml", help="Configurações do Neo4j")
    args = parser.parse_args()

    reporter = TaskReporter("Synesis Direct Link")

    result = run_pipeline(
        project_path=Path(args.project).resolve(),
        config_path=Path(args.config).resolve(),
        reporter=reporter
    )

    if result.success:
        reporter.info(f"Estatísticas: {result.stats}")
    else:
        reporter.error(f"[{result.error.stage}] {result.error.message}")
        if result.error.details:
            reporter.info(f"Detalhes: {result.error.details}")

    reporter.print_summary()
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
