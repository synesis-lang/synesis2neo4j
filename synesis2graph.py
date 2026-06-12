#!/usr/bin/env python3
"""
synesis2graph.py - Universal Pipeline Synesis → Graph Databases

Version: 0.3.0
Repository: https://github.com/synesis-lang/synesis2neo4j

Purpose:
    Connects the Synesis compiler directly to graph databases without intermediate files.
    Compiles the project in memory via `synesis.load()` and synchronizes atomically.

Main components:
    - compile_project: Compiles Synesis project and prepares payload for graph
    - sync_to_neo4j: Persists payload to Neo4j via single transaction
    - sync_to_graphqlite: Persists payload to GraphQLite (SQLite + Cypher)
    - compute_metrics: Calculates native and GDS metrics automatically
    - TaskReporter: User interface with Rich (fallback to logging)

Critical dependencies:
    - synesis: bibliometric project compiler
    - neo4j/graphqlite: graph database drivers
    - tomli/tomllib: TOML configuration parser

Optional dependencies:
    - Neo4j GDS: plugin for advanced metrics (PageRank, Betweenness, Louvain)

Usage example:
    synesis-graph neo4j --project ./my_project.synp
    synesis-graph html --project ./my_project.synp --output graph.html --all
    synesis-graph --version

Implementation notes:
    - Zero intermediate I/O (everything in memory)
    - Atomicity via single transaction
    - Dynamic labels sanitized against Cypher injection
    - Uses Result types for errors (CompilationError, ConnectionError, SyncError, DependencyError)
    - Metrics calculated automatically (native always, GDS if available)
"""
from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    import click
    _CLICK_AVAILABLE = True
except ImportError:
    _CLICK_AVAILABLE = False

# ============================================================================
# VERSION
# ============================================================================
__version__ = "0.3.0"
__version_info__ = (0, 3, 0)

# Phase 1: backend selection contract (Neo4j active, GraphQLite planned)
BACKEND_NEO4J = "neo4j"
BACKEND_GRAPHQLITE = "graphqlite"
BACKEND_HTML = "html"
SUPPORTED_BACKENDS = (BACKEND_NEO4J, BACKEND_GRAPHQLITE, BACKEND_HTML)

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
logger = logging.getLogger("synesis2graph")
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


def get_neo4j_driver_factory() -> Any:
    """Loads Neo4j driver factory lazily to isolate backend dependencies."""
    try:
        from neo4j import GraphDatabase as neo4j_graph_database
        return neo4j_graph_database
    except ImportError:
        return None


def get_graphqlite_connect_factory() -> Any:
    """Loads GraphQLite connect function lazily to isolate backend dependencies."""
    try:
        from graphqlite import connect as graphqlite_connect
        return graphqlite_connect
    except ImportError:
        return None

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
    """Error connecting to database backends."""
    pass


@dataclass
class SyncError(PipelineError):
    """Error synchronizing with the database."""
    pass


@dataclass
class DependencyError(PipelineError):
    """Error for missing runtime dependencies."""
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
def analyze_template(template_data: Dict[str, Any]) -> tuple[List[str], List[str], List[ChainFieldSpec], List[CodeFieldSpec], Dict[str, List[Dict]], List[str], str]:
    """
    Analyzes Synesis template to identify scalar, relational, CHAIN, CODE and SOURCE fields.

    Returns:
        Tuple (scalar_fields, graph_fields, chain_fields, code_fields, value_maps,
               source_fields, memo_field_name).
        - graph_fields become taxonomy nodes
        - chain_fields define nodes with self-referential relations (triples)
        - code_fields define references to concepts (list of codes)
        - value_maps maps numeric indices to labels (for ORDERED/ENUMERATED)
        - source_fields become dynamic properties on Source nodes
        - memo_field_name is the ITEM-scoped MEMO field name (e.g. "note", "resumo")
    """
    field_specs = template_data.get("field_specs", {})

    scalar_fields: List[str] = []
    graph_fields: List[str] = []
    chain_fields: List[ChainFieldSpec] = []
    code_fields: List[CodeFieldSpec] = []
    value_maps: Dict[str, List[Dict]] = {}
    source_fields: List[str] = []
    memo_field_name: str = "note"  # default for backwards compatibility

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
            elif field_type == "MEMO":
                memo_field_name = field_name

        elif scope == "SOURCE":
            source_fields.append(field_name)

    return scalar_fields, graph_fields, chain_fields, code_fields, value_maps, source_fields, memo_field_name


def get_taxonomy_labels(graph_fields: List[str]) -> List[str]:
    """Converts field names to sanitized Neo4j labels."""
    return [sanitize_cypher_label(f.capitalize()) for f in graph_fields]


# ============================================================================
# COMPILATION AND PREPARATION
# ============================================================================
def load_json_project(
    json_path: Path,
    reporter: TaskReporter
) -> Union[GraphPayload, CompilationError]:
    """
    Loads a pre-compiled Synesis JSON export (v3.0) and builds a GraphPayload.

    Args:
        json_path: Path to the exported .json file
        reporter: Reporter for visual feedback

    Returns:
        GraphPayload on success, CompilationError on failure.
    """
    reporter.info(f"Loading Synesis JSON export: {json_path}")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
    except Exception as e:
        return CompilationError(
            message="Failed to read JSON export",
            stage="load",
            diagnostics=[str(e)],
        )

    version = json_data.get("version", "")
    if not str(version).startswith("3"):
        reporter.warning(f"JSON version '{version}' may not be fully supported (expected 3.x)")

    corpus_count = len(json_data.get("corpus", []))
    reporter.success(f"JSON loaded. {corpus_count} corpus items.")

    scalar_fields, graph_fields, chain_fields, code_fields, value_maps, source_fields, memo_field_name = analyze_template(
        json_data["template"]
    )

    payload = _build_graph_payload(
        json_data=json_data,
        scalar_fields=scalar_fields,
        graph_fields=graph_fields,
        chain_fields=chain_fields,
        code_fields=code_fields,
        value_maps=value_maps,
        source_fields=source_fields,
        memo_field_name=memo_field_name,
    )

    return payload


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

    scalar_fields, graph_fields, chain_fields, code_fields, value_maps, source_fields, memo_field_name = analyze_template(json_data["template"])

    payload = _build_graph_payload(
        json_data=json_data,
        scalar_fields=scalar_fields,
        graph_fields=graph_fields,
        chain_fields=chain_fields,
        code_fields=code_fields,
        value_maps=value_maps,
        source_fields=source_fields,
        memo_field_name=memo_field_name,
    )

    return payload


def _build_graph_payload(
    json_data: Dict[str, Any],
    scalar_fields: List[str],
    graph_fields: List[str],
    chain_fields: List[ChainFieldSpec],
    code_fields: List[CodeFieldSpec],
    value_maps: Dict[str, List[Dict[str, Any]]],
    source_fields: List[str],
    memo_field_name: str = "note",
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
        corpus, bibliography, relation_definitions, code_field_names, source_fields, memo_field_name
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
        # v3.0: campos aplanados na raiz (sem sub-dict "fields")
        props: Dict[str, Any] = {
            "name": name,
            "description": entry.get("description"),
            "created": int(time.time())
        }

        for sf in scalar_fields:
            if sf in entry:
                props[sf] = entry[sf]

        relations: Dict[str, List[str]] = {}
        for gf in graph_fields:
            if gf in entry:
                raw_val = entry[gf]
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
    source_fields: List[str],
    memo_field_name: str = "note",
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
    - CHAIN: triples (source, relation, target) with per-triple description
    - CODE: list of codes referencing concepts

    The memo_field_name identifies the ITEM-scoped MEMO field (e.g. "note" for
    bibliometrics, "resumo" for causation coding). When the MEMO is a parallel list
    (one entry per chain triple), each triple gets its own description. When the MEMO
    is a single string shared across all triples of a chained sequence, that string is
    used as the description for every triple in the item.
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
            chain_list = data.get("chain", [])
            raw_memo = data.get(memo_field_name, [])
            # Parallel list: one note per triple (bibliometrics format).
            # Single string or absent: shared description across all triples (causation format).
            notes: List[str] = raw_memo if isinstance(raw_memo, list) else []
            shared_note: str = raw_memo if isinstance(raw_memo, str) else ""
            base_text: str = (
                data.get("text") or data.get("citação") or data.get("citation") or ""
            )

            for idx, chain in enumerate(chain_list, 1):
                note = notes[idx - 1] if idx - 1 < len(notes) else shared_note
                item_id = f"{corpus_id}_n{idx:04d}"

                items.append({
                    "item_id": item_id,
                    "citation": base_text,
                    "description": note,
                })
                from_source.append({"item_id": item_id, "ref": source_ref})

                # v3.0: chains como {from, relation, to}
                src = chain.get("from", "").strip()
                rel = chain.get("relation", "").strip()
                tgt = chain.get("to", "").strip()
                if src and tgt:
                    mentions.append({"item_id": item_id, "concept": src, "mention_order": 1})
                    mentions.append({"item_id": item_id, "concept": tgt, "mention_order": 2})

                    # Normalize relation type and lookup description
                    rel_type = rel.upper().replace(" ", "_").replace("-", "_")
                    rel_description = relation_definitions.get(rel, "")

                    chains.append({
                        "source": src,
                        "target": tgt,
                        "type": rel_type,
                        "description": rel_description,
                        "item_id": item_id,
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
                mentions.append({"item_id": item_id, "concept": code, "mention_order": 1})

    return sources, items, mentions, chains, from_source


def _build_source_props(
    source_ref: str,
    item: Dict[str, Any],
    bibliography: Dict[str, Any],
    source_fields: List[str]
) -> Dict[str, Any]:
    """Builds properties of a Source node (SOURCE...END SOURCE block).

    v3.0: source_metadata foi removido do corpus. Todos os campos de fonte
    (bibliograficos e sintetizados) estao em bibliography[source_ref].
    """
    bib_entry = bibliography.get(source_ref, {})

    props: Dict[str, Any] = {"bibtex": source_ref}

    # Standard bibliographic fields
    for key in ("title", "author", "year", "doi", "journal", "abstract"):
        val = bib_entry.get(key)
        if val is not None:
            props[key] = val

    # Dynamic fields from template (SCOPE SOURCE) - agora em bibliography
    for field_name in source_fields:
        if field_name in bib_entry and bib_entry[field_name] is not None:
            props[field_name] = bib_entry[field_name]

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


class _GraphQLiteQueryRunner:
    """Adapts GraphQLite connection to the run(query, **params) interface."""

    def __init__(self, conn: Any):
        self.conn = conn

    def run(self, query: str, **params: Any) -> Any:
        if params:
            return self.conn.cypher(query, params)
        return self.conn.cypher(query)


def sync_to_graphqlite(conn: Any, payload: GraphPayload) -> Optional[SyncError]:
    """
    Synchronizes payload with GraphQLite.

    GraphQLite does not support Neo4j schema commands used in sync_to_neo4j
    (constraints/index management), so synchronization writes directly using
    Cypher MERGE/MATCH statements.
    """
    tx_started = False
    runner = _GraphQLiteQueryRunner(conn)

    try:
        if hasattr(conn, "execute"):
            try:
                conn.execute("BEGIN")
                tx_started = True
            except Exception:
                tx_started = False

        steps = [
            ("sources", lambda: _sync_sources(runner, payload.sources)),
            ("items", lambda: _sync_items(runner, payload.items)),
            ("from_source", lambda: _sync_from_source(runner, payload.from_source)),
            (
                "concepts",
                lambda: _sync_concepts(runner, payload.chains, payload.concepts, payload.concept_label),
            ),
            (
                "taxonomies",
                lambda: _sync_taxonomies(runner, payload.concepts, payload.graph_fields, payload.concept_label),
            ),
            ("mentions", lambda: _sync_mentions(runner, payload.mentions, payload.concept_label)),
        ]

        for step_name, step_fn in steps:
            try:
                step_fn()
            except Exception as step_error:
                raise RuntimeError(f"GraphQLite sync step '{step_name}' failed: {step_error}") from step_error

        if tx_started and hasattr(conn, "execute"):
            conn.execute("COMMIT")
        return None
    except Exception as e:
        if tx_started and hasattr(conn, "execute"):
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        return SyncError(
            message="Synchronization failed",
            stage="sync",
            details=str(e),
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
        SET s = row, s.last_updated = timestamp()
    """, rows=sources)


def _sync_items(tx: Any, items: List[Dict[str, Any]]) -> None:
    if not items:
        return
    tx.run("""
        UNWIND $rows AS row
        MERGE (i:Item {item_id: row.item_id})
        SET i = row, i.last_updated = timestamp()
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

        relation_rows: List[Dict[str, Any]] = []
        for row in concepts:
            props = row.get("props", {})
            relations = row.get("relations", {})
            if not isinstance(props, dict) or not isinstance(relations, dict):
                continue

            concept_name = props.get("name")
            raw_vals = relations.get(field_name)
            if concept_name is None or raw_vals is None:
                continue

            vals = raw_vals if isinstance(raw_vals, list) else [raw_vals]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue

            relation_rows.append({"concept": concept_name, "vals": vals})

        if not relation_rows:
            continue

        query = f"""
            UNWIND $rows AS row
            MATCH (c:{concept_label} {{name: row.concept}})
            UNWIND row.vals AS val
            MERGE (t:{label} {{name: val}})
            MERGE (c)-[:{rel_type}]->(t)
        """
        tx.run(query, rows=relation_rows)

    # Second: create mapping relations between taxonomies
    # Topic -> Aspect (MAPPED_TO_ASPECT)
    if "topic" in graph_fields and "aspect" in graph_fields:
        mapping_rows: List[Dict[str, Any]] = []
        for row in concepts:
            relations = row.get("relations", {})
            if not isinstance(relations, dict):
                continue
            topics_raw = relations.get("topic")
            aspects_raw = relations.get("aspect")
            if topics_raw is None or aspects_raw is None:
                continue
            topics = topics_raw if isinstance(topics_raw, list) else [topics_raw]
            aspects = aspects_raw if isinstance(aspects_raw, list) else [aspects_raw]
            topics = [t for t in topics if t is not None]
            aspects = [a for a in aspects if a is not None]
            if topics and aspects:
                mapping_rows.append({"topics": topics, "aspects": aspects})

        if mapping_rows:
            tx.run("""
                UNWIND $rows AS row
                UNWIND row.topics AS topic_val
                UNWIND row.aspects AS aspect_val
                MATCH (topic:Topic {name: topic_val})
                MATCH (aspect:Aspect {name: aspect_val})
                MERGE (topic)-[:MAPPED_TO_ASPECT]->(aspect)
            """, rows=mapping_rows)

    # Topic -> Dimension (MAPPED_TO_DIMENSION)
    if "topic" in graph_fields and "dimension" in graph_fields:
        mapping_rows: List[Dict[str, Any]] = []
        for row in concepts:
            relations = row.get("relations", {})
            if not isinstance(relations, dict):
                continue
            topics_raw = relations.get("topic")
            dimensions_raw = relations.get("dimension")
            if topics_raw is None or dimensions_raw is None:
                continue
            topics = topics_raw if isinstance(topics_raw, list) else [topics_raw]
            dimensions = dimensions_raw if isinstance(dimensions_raw, list) else [dimensions_raw]
            topics = [t for t in topics if t is not None]
            dimensions = [d for d in dimensions if d is not None]
            if topics and dimensions:
                mapping_rows.append({"topics": topics, "dimensions": dimensions})

        if mapping_rows:
            tx.run("""
                UNWIND $rows AS row
                UNWIND row.topics AS topic_val
                UNWIND row.dimensions AS dimension_val
                MATCH (topic:Topic {name: topic_val})
                MATCH (dimension:Dimension {name: dimension_val})
                MERGE (topic)-[:MAPPED_TO_DIMENSION]->(dimension)
            """, rows=mapping_rows)

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
        MERGE (i)-[:MENTIONS {{mention_order: row.mention_order}}]->(c)
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
        concept_rows: List[Dict[str, Any]] = []
        for row in concepts:
            props = row.get("props", {})
            if isinstance(props, dict) and props.get("name"):
                concept_rows.append(props)

        tx.run(f"""
            UNWIND $rows AS row
            MERGE (c:{concept_label} {{name: row.name}})
            SET c = row
        """, rows=concept_rows)

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
    Calculates Neo4j graph metrics: native (Cypher) and advanced (GDS).

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


def compute_metrics_graphqlite(
    conn: Any,
    payload: GraphPayload,
    reporter: TaskReporter,
) -> Optional[SyncError]:
    """
    Calculates GraphQLite metrics using native Cypher only.

    GraphQLite does not support Neo4j GDS procedures, so this backend keeps
    the common native metrics to preserve cross-backend comparability.
    """
    concept_label = payload.concept_label
    graph_fields = payload.graph_fields
    runner = _GraphQLiteQueryRunner(conn)

    with reporter.step("Calculating Native Metrics (GraphQLite)"):
        try:
            _compute_native_concept_metrics(runner, concept_label)
            _compute_native_taxonomy_metrics(runner, concept_label, graph_fields)
            _compute_native_source_metrics(runner, concept_label)
        except Exception as e:
            reporter.warning(f"GraphQLite native metrics skipped due backend limitation: {e}")

    reporter.info(
        "Advanced metrics skipped for GraphQLite (no Neo4j GDS procedures)."
    )
    return None


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
        OPTIONAL MATCH (c)-[:RELATES_TO]->(out_node)
        OPTIONAL MATCH (c)<-[:RELATES_TO]-(in_node)
        WITH c, count(DISTINCT out_node) AS out_deg, count(DISTINCT in_node) AS in_deg
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


@dataclass
class GraphQLiteConfig:
    """GraphQLite (SQLite) connection configuration."""
    db_path: str
    extension_path: Optional[str] = None


@dataclass
class HTMLConfig:
    """HTML graph output configuration."""
    output_path: str = "./graph.html"
    group_by: Optional[str] = None
    min_frequency: int = 3
    min_source_count: int = 2
    max_nodes: int = 200
    max_hyperedges: int = 50
    include_isolated: bool = False


PipelineConfig = Union[Neo4jConfig, GraphQLiteConfig, HTMLConfig]


def _load_neo4j_config(parsed_cfg: Dict[str, Any]) -> Union[Neo4jConfig, ConnectionError]:
    """Loads and validates Neo4j configuration block."""
    try:
        cfg = parsed_cfg["neo4j"]
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
            details=f"Required field missing in [neo4j]: {e}",
        )
    except Exception as e:
        return ConnectionError(
            message="Error reading Neo4j configuration",
            stage="config",
            details=str(e),
        )


def _load_graphqlite_config(parsed_cfg: Dict[str, Any]) -> Union[GraphQLiteConfig, ConnectionError]:
    """Loads and validates GraphQLite configuration block."""
    try:
        cfg = parsed_cfg["graphqlite"]
        db_path = cfg.get("db_path")
        if not db_path:
            raise KeyError("'db_path'")
        extension_path = cfg.get("extension_path")
        return GraphQLiteConfig(
            db_path=str(db_path),
            extension_path=str(extension_path) if extension_path else None,
        )
    except KeyError as e:
        return ConnectionError(
            message="Incomplete configuration",
            stage="config",
            details=f"Required field missing in [graphqlite]: {e}",
        )
    except Exception as e:
        return ConnectionError(
            message="Error reading GraphQLite configuration",
            stage="config",
            details=str(e),
        )


def _load_html_config(parsed_cfg: Dict[str, Any]) -> HTMLConfig:
    """Loads HTML configuration block with defaults (all fields optional)."""
    cfg = parsed_cfg.get("html", {})
    return HTMLConfig(
        output_path=str(cfg.get("output_path", "./graph.html")),
        group_by=cfg.get("group_by") or None,
        min_frequency=int(cfg.get("min_frequency", 3)),
        min_source_count=int(cfg.get("min_source_count", 2)),
        max_nodes=int(cfg.get("max_nodes", 200)),
        max_hyperedges=int(cfg.get("max_hyperedges", 50)),
        include_isolated=bool(cfg.get("include_isolated", False)),
    )


def load_config(config_path: Path, backend: str) -> Union[PipelineConfig, ConnectionError]:
    """Loads backend-specific configuration from TOML file."""
    if backend == BACKEND_HTML:
        if not config_path.exists():
            return HTMLConfig()
        try:
            parsed_cfg = tomllib.loads(config_path.read_text("utf-8"))
            return _load_html_config(parsed_cfg)
        except Exception:
            return HTMLConfig()

    if not config_path.exists():
        return ConnectionError(
            message="Configuration file not found",
            stage="config",
            details=str(config_path)
        )

    try:
        parsed_cfg = tomllib.loads(config_path.read_text("utf-8"))
    except Exception as e:
        return ConnectionError(
            message="Error reading configuration",
            stage="config",
            details=str(e),
        )

    if backend == BACKEND_NEO4J:
        return _load_neo4j_config(parsed_cfg)
    if backend == BACKEND_GRAPHQLITE:
        return _load_graphqlite_config(parsed_cfg)

    return ConnectionError(
        message="Unsupported backend in configuration loader",
        stage="backend",
        details=f"Supported backends: {', '.join(SUPPORTED_BACKENDS)}",
    )


def validate_backend_config(config: PipelineConfig, backend: str) -> Optional[ConnectionError]:
    """Validates configuration type against selected backend."""
    if backend == BACKEND_NEO4J and isinstance(config, Neo4jConfig):
        return None
    if backend == BACKEND_GRAPHQLITE and isinstance(config, GraphQLiteConfig):
        return None
    if backend == BACKEND_HTML and isinstance(config, HTMLConfig):
        return None

    return ConnectionError(
        message="Configuration/backend mismatch",
        stage="config",
        details=f"Backend '{backend}' does not match loaded configuration type.",
    )


def _resolve_graphqlite_db_path(raw_db_path: str, config_path: Path, project_path: Path) -> Path:
    """
    Resolves GraphQLite db path with placeholders and relative path support.

    Supported placeholders:
    - {project}: project filename stem
    """
    resolved = raw_db_path.replace("{project}", project_path.stem)
    db_path = Path(resolved)
    if not db_path.is_absolute():
        db_path = config_path.parent / db_path
    return db_path.resolve()


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
# BACKEND ADAPTERS (Phase 3)
# ============================================================================
class BackendAdapter(ABC):
    """Contract for backend-specific persistence and metrics operations."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable backend name."""
        raise NotImplementedError

    @abstractmethod
    def preflight(self, reporter: TaskReporter) -> Optional[PipelineError]:
        """Runs backend checks that should happen before compilation."""
        raise NotImplementedError

    @abstractmethod
    def connect(self, reporter: TaskReporter) -> Optional[PipelineError]:
        """Opens backend connection resources."""
        raise NotImplementedError

    @abstractmethod
    def prepare_destination(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        """Prepares destination structures before synchronization."""
        raise NotImplementedError

    @abstractmethod
    def clear_destination(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        """Clears existing destination data when required."""
        raise NotImplementedError

    @abstractmethod
    def synchronize_payload(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        """Writes payload data to the destination backend."""
        raise NotImplementedError

    @abstractmethod
    def compute_backend_metrics(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        """Calculates backend-specific metrics."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Releases open backend resources."""
        raise NotImplementedError


class Neo4jBackendAdapter(BackendAdapter):
    """Neo4j backend implementation bound to the BackendAdapter contract."""

    def __init__(self, config: Neo4jConfig):
        self.config = config
        self.driver: Any = None
        self.session: Any = None
        self.db_name = "neo4j"

    @property
    def backend_name(self) -> str:
        return BACKEND_NEO4J

    def preflight(self, reporter: TaskReporter) -> Optional[PipelineError]:
        return None

    def connect(self, reporter: TaskReporter) -> Optional[PipelineError]:
        driver_factory = get_neo4j_driver_factory()
        if driver_factory is None:
            return DependencyError(
                message="Neo4j dependency is missing",
                stage="dependency",
                details="Install with: pip install neo4j",
            )

        try:
            reporter.info(f"[{self.backend_name}] Connecting to {self.config.uri}")
            self.driver = driver_factory.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
            )
            return None
        except Exception as e:
            return ConnectionError(
                message="Failed to connect to Neo4j",
                stage="connection",
                details=str(e),
            )

    def prepare_destination(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        if self.driver is None:
            return ConnectionError(
                message="Neo4j connection not initialized",
                stage="connection",
            )

        self.db_name = sanitize_database_name(payload.project_name)
        reporter.info(f"[{self.backend_name}] Target database: {self.db_name}")

        with reporter.step("Checking/Creating Database"):
            db_error = ensure_database_exists(self.driver, self.db_name, reporter)
            if db_error:
                return db_error

        try:
            self.session = self.driver.session(database=self.db_name)
            return None
        except Exception as e:
            return ConnectionError(
                message="Failed to open Neo4j session",
                stage="connection",
                details=str(e),
            )

    def clear_destination(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        # Neo4j clearing is currently performed inside sync_to_neo4j.
        return None

    def synchronize_payload(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        if self.session is None:
            return ConnectionError(
                message="Neo4j session not initialized",
                stage="connection",
            )

        with reporter.step("Synchronizing Graph (Transactional)"):
            sync_error = sync_to_neo4j(self.session, payload)
            if sync_error:
                return sync_error
        return None

    def compute_backend_metrics(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        if self.session is None:
            return ConnectionError(
                message="Neo4j session not initialized",
                stage="connection",
            )
        try:
            compute_metrics(self.session, payload, reporter)
            return None
        except Exception as e:
            return SyncError(
                message="Metrics calculation failed",
                stage="metrics",
                details=str(e),
            )

    def close(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None
        if self.driver is not None:
            self.driver.close()
            self.driver = None


class GraphQLiteBackendAdapter(BackendAdapter):
    """GraphQLite backend implementation."""

    def __init__(self, config: GraphQLiteConfig, config_path: Path, project_path: Path):
        self.config = config
        self.config_path = config_path
        self.project_path = project_path
        self.db_path: Optional[Path] = None
        self.conn: Any = None

    @property
    def backend_name(self) -> str:
        return BACKEND_GRAPHQLITE

    def preflight(self, reporter: TaskReporter) -> Optional[PipelineError]:
        self.db_path = _resolve_graphqlite_db_path(
            raw_db_path=self.config.db_path,
            config_path=self.config_path,
            project_path=self.project_path,
        )
        reporter.info(f"[{self.backend_name}] Target database: {self.db_path}")
        return None

    def connect(self, reporter: TaskReporter) -> Optional[PipelineError]:
        connect_factory = get_graphqlite_connect_factory()
        if connect_factory is None:
            return DependencyError(
                message="GraphQLite dependency is missing",
                stage="dependency",
                details="Install with: pip install graphqlite",
            )

        if self.db_path is None:
            return ConnectionError(
                message="GraphQLite database path not resolved",
                stage="config",
                details="Call preflight before connect.",
            )

        if self.db_path.exists() and self.db_path.is_dir():
            return ConnectionError(
                message="Invalid GraphQLite database path",
                stage="config",
                details=f"Path is a directory: {self.db_path}",
            )

        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # Phase 4 requirement: remove existing db and recreate on each run.
            if self.db_path.exists():
                reporter.info(f"[{self.backend_name}] Removing existing database: {self.db_path}")
                self.db_path.unlink()
        except Exception as e:
            return ConnectionError(
                message="Failed to recreate GraphQLite database file",
                stage="database_setup",
                details=str(e),
            )

        try:
            reporter.info(f"[{self.backend_name}] Creating database: {self.db_path}")
            if self.config.extension_path:
                self.conn = connect_factory(str(self.db_path), extension_path=self.config.extension_path)
            else:
                self.conn = connect_factory(str(self.db_path))
            reporter.info(f"[{self.backend_name}] Connection established")
            return None
        except Exception as e:
            return ConnectionError(
                message="Failed to connect to GraphQLite",
                stage="connection",
                details=str(e),
            )

    def prepare_destination(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        if self.conn is None:
            return ConnectionError(
                message="GraphQLite connection not initialized",
                stage="connection",
            )
        return None

    def clear_destination(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        # Database was recreated during connect, so no additional clear is needed.
        return None

    def synchronize_payload(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        if self.conn is None:
            return ConnectionError(
                message="GraphQLite connection not initialized",
                stage="connection",
            )

        with reporter.step("Synchronizing Graph (GraphQLite)"):
            sync_error = sync_to_graphqlite(self.conn, payload)
            if sync_error:
                return sync_error
        return None

    def compute_backend_metrics(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        if self.conn is None:
            return ConnectionError(
                message="GraphQLite connection not initialized",
                stage="connection",
            )
        return compute_metrics_graphqlite(self.conn, payload, reporter)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


# ============================================================================
# HTML BACKEND
# ============================================================================
_HTML_PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#D37295", "#A0CBE8",
]

_HTML_RELATION_COLORS: Dict[str, str] = {
    "ENABLES": "#59A14F",
    "INFLUENCES": "#4E79A7",
    "CONSTRAINS": "#F28E2B",
    "CONTESTED_BY": "#E15759",
    "RELATES_TO": "#9C755F",
}


def _html_relation_color(relation: str) -> str:
    norm = relation.upper().replace("-", "_").replace(" ", "_")
    if norm in _HTML_RELATION_COLORS:
        return _HTML_RELATION_COLORS[norm]
    return _HTML_PALETTE[abs(hash(norm)) % len(_HTML_PALETTE)]


def _html_slug(name: str) -> str:
    """Creates a stable, HTML-safe ID from a concept name."""
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return slug or "node"


def _html_apply_filters(
    payload: GraphPayload,
    min_frequency: int,
    min_source_count: int,
    max_nodes: int,
    include_isolated: bool,
) -> tuple[set, List[Dict[str, Any]]]:
    """
    Filters concepts by mention frequency and source coverage, then by degree.
    Returns (kept_concept_names_set, filtered_chains).
    """
    item_to_source: Dict[str, str] = {r["item_id"]: r["ref"] for r in payload.from_source}

    freq: Dict[str, int] = {}
    concept_sources: Dict[str, set] = {}
    for m in payload.mentions:
        c = m["concept"]
        freq[c] = freq.get(c, 0) + 1
        src = item_to_source.get(m["item_id"])
        if src:
            concept_sources.setdefault(c, set()).add(src)

    degree: Dict[str, int] = {}
    for ch in payload.chains:
        degree[ch["source"]] = degree.get(ch["source"], 0) + 1
        degree[ch["target"]] = degree.get(ch["target"], 0) + 1

    all_names = {c["props"]["name"] for c in payload.concepts}

    kept: set = set()
    for name in all_names:
        f = freq.get(name, 0)
        sc = len(concept_sources.get(name, set()))
        if f >= min_frequency and sc >= min_source_count:
            kept.add(name)

    if not include_isolated:
        has_chain = set()
        for ch in payload.chains:
            has_chain.add(ch["source"])
            has_chain.add(ch["target"])
        kept = {n for n in kept if n in has_chain}

    if max_nodes > 0 and len(kept) > max_nodes:
        sorted_kept = sorted(kept, key=lambda n: degree.get(n, 0), reverse=True)
        kept = set(sorted_kept[:max_nodes])

    filtered_chains = [
        ch for ch in payload.chains
        if ch["source"] in kept and ch["target"] in kept
    ]

    return kept, filtered_chains


def _html_resolve_grouping(
    payload: GraphPayload,
    kept: set,
    group_by: Optional[str],
) -> tuple[Dict[str, int], Dict[str, str], List[Dict[str, Any]], str]:
    """
    Assigns integer community IDs to concepts by grouping on a graph_field.
    Returns (cid_map, cname_map, legend_list, field_name).
    """
    field_name = group_by
    if not field_name and payload.graph_fields:
        for gf in payload.graph_fields:
            if re.match(r'^topic', gf, re.IGNORECASE):
                field_name = gf
                break
        if not field_name:
            field_name = payload.graph_fields[0]

    concept_to_group: Dict[str, str] = {}
    if field_name:
        for c in payload.concepts:
            name = c["props"]["name"]
            if name not in kept:
                continue
            vals = c["relations"].get(field_name)
            if isinstance(vals, list) and vals and vals[0]:
                concept_to_group[name] = str(vals[0])
            elif vals and not isinstance(vals, list):
                concept_to_group[name] = str(vals)
            else:
                concept_to_group[name] = "Other"
    else:
        for name in kept:
            concept_to_group[name] = "All"

    group_counts: Dict[str, int] = {}
    for name in kept:
        g = concept_to_group.get(name, "Other")
        group_counts[g] = group_counts.get(g, 0) + 1

    groups_ordered = sorted(group_counts.keys(), key=lambda g: (-group_counts[g], g))
    group_to_cid = {g: i for i, g in enumerate(groups_ordered)}

    cid_map = {name: group_to_cid[concept_to_group.get(name, "Other")] for name in kept}
    cname_map = {name: concept_to_group.get(name, "Other") for name in kept}

    legend = [
        {
            "cid": group_to_cid[g],
            "color": _HTML_PALETTE[group_to_cid[g] % len(_HTML_PALETTE)],
            "label": g,
            "count": group_counts[g],
        }
        for g in groups_ordered
    ]

    return cid_map, cname_map, legend, (field_name or "All")


def _html_build_hyperedges(
    payload: GraphPayload,
    kept: set,
    max_hyperedges: int,
    slug_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Builds hyperedge dicts from corpus entries that link ≥ 3 distinct concepts.
    Groups chains sharing the same parent corpus entry (same item_id prefix).
    """
    _suffix_re = re.compile(r'^(.+)_[nc]\d{4}$')

    item_desc: Dict[str, str] = {
        item["item_id"]: item.get("description", "") for item in payload.items
    }
    item_source: Dict[str, str] = {r["item_id"]: r["ref"] for r in payload.from_source}

    corpus_concepts: Dict[str, set] = {}
    corpus_first_item: Dict[str, str] = {}
    for ch in payload.chains:
        item_id = ch["item_id"]
        m = _suffix_re.match(item_id)
        corpus_id = m.group(1) if m else item_id
        corpus_concepts.setdefault(corpus_id, set())
        corpus_concepts[corpus_id].add(ch["source"])
        corpus_concepts[corpus_id].add(ch["target"])
        if corpus_id not in corpus_first_item:
            corpus_first_item[corpus_id] = item_id

    candidates = []
    for corpus_id, concepts_set in corpus_concepts.items():
        filtered = concepts_set & kept
        if len(filtered) < 3:
            continue
        first_item_id = corpus_first_item.get(corpus_id, "")
        desc = item_desc.get(first_item_id, "")
        src = item_source.get(first_item_id, corpus_id)
        if desc:
            label = (desc[:57] + "…") if len(desc) > 60 else desc
        else:
            label = f"{src} · {corpus_id}"
        candidates.append({
            "corpus_id": corpus_id,
            "concepts": filtered,
            "label": label,
            "source_ref": src,
            "size": len(filtered),
        })

    candidates.sort(key=lambda c: -c["size"])
    candidates = candidates[:max(0, max_hyperedges)]

    _sm = slug_map or {}
    return [
        {
            "id": _html_slug(cand["corpus_id"]),
            "label": cand["label"],
            "nodes": [_sm.get(c, _html_slug(c)) for c in cand["concepts"]],
            "relation": "RELATES_TO",
            "confidence": "EXTRACTED",
            "source_item": cand["source_ref"],
        }
        for cand in candidates
    ]


def _html_render_payload(
    payload: GraphPayload,
    config: HTMLConfig,
    template_path: Path,
) -> str:
    """Builds the complete HTML string from a GraphPayload and HTMLConfig."""
    kept, filtered_chains = _html_apply_filters(
        payload,
        min_frequency=config.min_frequency,
        min_source_count=config.min_source_count,
        max_nodes=config.max_nodes,
        include_isolated=config.include_isolated,
    )

    cid_map, cname_map, legend, legend_title = _html_resolve_grouping(
        payload, kept, group_by=config.group_by
    )
    legend_title_display = legend_title.replace("_", " ").title()

    # Build ALL_GROUPINGS: one entry per graph_field (for sidebar tabs).
    all_groupings: Dict[str, Any] = {}
    for gf in payload.graph_fields:
        _cid_map, _cname_map, _leg, _fname = _html_resolve_grouping(payload, kept, group_by=gf)
        _title = _fname.replace("_", " ").title()
        all_groupings[gf] = {
            "title":          _title,
            "legend":         _leg,
            "value_to_cid":   {e["label"]: e["cid"]   for e in _leg},
            "value_to_color": {e["label"]: e["color"] for e in _leg},
        }

    degree: Dict[str, int] = {}
    for ch in filtered_chains:
        degree[ch["source"]] = degree.get(ch["source"], 0) + 1
        degree[ch["target"]] = degree.get(ch["target"], 0) + 1

    item_to_source: Dict[str, str] = {r["item_id"]: r["ref"] for r in payload.from_source}
    concept_first_source: Dict[str, str] = {}
    for m in payload.mentions:
        c = m["concept"]
        if c in kept and c not in concept_first_source:
            src = item_to_source.get(m["item_id"], "")
            if src:
                concept_first_source[c] = src

    concept_index: Dict[str, Dict[str, Any]] = {
        c["props"]["name"]: c for c in payload.concepts
    }

    # Build collision-free slug map: two different names must never share an ID.
    _slug_counts: Dict[str, int] = {}
    slug_map: Dict[str, str] = {}
    for name in kept:
        base = _html_slug(name)
        count = _slug_counts.get(base, 0)
        _slug_counts[base] = count + 1
        slug_map[name] = base if count == 0 else f"{base}_{count + 1}"

    # Build evidence data: concept_slug → [{src, type, text, note?}]
    # Uses the full payload.chains (not just filtered_chains) so concepts at the
    # boundary of the filter still show all their evidence records.
    item_index: Dict[str, Dict[str, Any]] = {
        item["item_id"]: item for item in payload.items
    }
    _ev_seen: Dict[str, set] = {}
    evidence_by_slug: Dict[str, List[Dict[str, str]]] = {}
    for ch in payload.chains:
        iid = ch.get("item_id", "")
        if not iid:
            continue
        item = item_index.get(iid)
        if not item:
            continue
        text = (item.get("citation") or "").strip()
        note = (item.get("description") or "").strip()
        if not text and not note:
            continue
        ch_type = ch.get("type", "")
        src_ref = item_to_source.get(iid, iid)
        for role in (ch["source"], ch["target"]):
            if role not in kept:
                continue
            slug = slug_map[role]
            dedup_key = (iid, ch_type)
            if dedup_key in _ev_seen.get(slug, set()):
                continue
            _ev_seen.setdefault(slug, set()).add(dedup_key)
            lst = evidence_by_slug.setdefault(slug, [])
            if len(lst) >= 60:
                continue
            entry: Dict[str, str] = {
                "src":  src_ref[:60],
                "type": ch_type,
                "text": text[:300],
            }
            if note:
                entry["note"] = note[:150]
            lst.append(entry)

    # Build Evidence-mode graph: individual (ungrouped) chain edges.
    # Uses full payload.chains (not filtered_chains) so evidence is available even
    # when both endpoint nodes are hidden by frequency/source filters.
    ev_chain_edges: List[Dict[str, Any]] = []
    for ch in payload.chains:
        src_slug = slug_map.get(ch["source"], _html_slug(ch["source"]))
        tgt_slug = slug_map.get(ch["target"], _html_slug(ch["target"]))
        ch_type  = ch.get("type", "")
        color    = _html_relation_color(ch_type)
        iid      = ch.get("item_id", "")
        src_ref  = item_to_source.get(iid, iid)
        ev_chain_edges.append({
            "from":      src_slug,
            "to":        tgt_slug,
            "label":     "",
            "title":     f"{ch_type} — {src_ref}",
            "color":     {"color": color, "opacity": 0.55},
            "dashes":    False,
            "width":     0.8,
            "arrows":    {"to": {"enabled": True, "scaleFactor": 0.4}},
            "_ev_chain": True,
            "_type":     ch_type,
            "_src":      src_ref,
        })

    # Collect concept names referenced in ev_chain_edges that are absent from kept.
    # These nodes must exist in the vis dataset for Evidence-mode edges to render.
    kept_slugs = {slug_map[n] for n in kept}
    ev_extra_nodes: List[Dict[str, Any]] = []
    _ev_extra_seen: set = set()
    for ev_edge in ev_chain_edges:
        for slug in (ev_edge["from"], ev_edge["to"]):
            if slug in kept_slugs or slug in _ev_extra_seen:
                continue
            _ev_extra_seen.add(slug)
            # Reconstruct original name from slug (best-effort reverse lookup)
            name = next((n for n, s in slug_map.items() if s == slug), slug)
            c_data = concept_index.get(name, {})
            props = c_data.get("props", {})
            cid   = cid_map.get(name, 0)
            color = _HTML_PALETTE[cid % len(_HTML_PALETTE)]
            extra: Dict[str, Any] = {}
            for sf in payload.scalar_fields:
                val = props.get(sf)
                if val is not None and val != "":
                    extra[sf] = val
            for gf in payload.graph_fields:
                vals = c_data.get("relations", {}).get(gf)
                if vals:
                    first_val = vals[0] if isinstance(vals, list) else vals
                    if first_val:
                        extra[gf] = first_val
            ev_extra_nodes.append({
                "id": slug,
                "label": name,
                "color": {
                    "background": color,
                    "border":     color,
                    "highlight":  {"background": "#ffffff", "border": color},
                },
                "size":           8.0,
                "font":           {"size": 12},
                "title":          name,
                "_community":     cid,
                "_community_name": cname_map.get(name, "Other"),
                "_source_file":   concept_first_source.get(name, ""),
                "_file_type":     "concept",
                "_degree":        degree.get(name, 0),
                "_extra":         extra,
            })

    raw_nodes = []
    for name in kept:
        cid = cid_map.get(name, 0)
        color = _HTML_PALETTE[cid % len(_HTML_PALETTE)]
        deg = degree.get(name, 0)
        size = 8 + min(deg, 30) * 1.0

        c_data = concept_index.get(name, {})
        props = c_data.get("props", {})
        relations = c_data.get("relations", {})

        extra: Dict[str, Any] = {}
        for sf in payload.scalar_fields:
            val = props.get(sf)
            if val is not None and val != "":
                extra[sf] = val
        for gf in payload.graph_fields:
            vals = relations.get(gf)
            if vals:
                first_val = vals[0] if isinstance(vals, list) else vals
                if first_val:
                    extra[gf] = first_val

        raw_nodes.append({
            "id": slug_map[name],
            "label": name,
            "color": {
                "background": color,
                "border": color,
                "highlight": {"background": "#ffffff", "border": color},
            },
            "size": size,
            "font": {"size": 12},
            "title": name,
            "community": cid,
            "community_name": cname_map.get(name, "Other"),
            "source_file": concept_first_source.get(name, ""),
            "file_type": "concept",
            "degree": deg,
            "extra": extra,
        })

    # Group all chains by canonical (sorted) pair — merges bidirectional pairs.
    # edge_seen_dirs tracks which original (src, tgt) directions exist per pair.
    edge_groups: Dict[tuple, List[Dict[str, Any]]] = {}
    edge_seen_dirs: Dict[tuple, set] = {}
    for ch in filtered_chains:
        src_id = slug_map.get(ch["source"], _html_slug(ch["source"]))
        tgt_id = slug_map.get(ch["target"], _html_slug(ch["target"]))
        canonical = tuple(sorted([src_id, tgt_id]))
        edge_groups.setdefault(canonical, []).append(ch)
        edge_seen_dirs.setdefault(canonical, set()).add((src_id, tgt_id))

    raw_edges = []
    for canonical, group in edge_groups.items():
        src_id, tgt_id = canonical
        is_bidir = len(edge_seen_dirs[canonical]) > 1

        rel_counts: Dict[str, int] = {}
        for ch in group:
            rel_counts[ch["type"]] = rel_counts.get(ch["type"], 0) + 1

        dominant_rel = max(rel_counts, key=lambda r: rel_counts[r])
        color = _html_relation_color(dominant_rel)
        total = sum(rel_counts.values())
        width = min(1.0 + total * 0.4, 4.0)

        rel_parts = sorted(rel_counts.items(), key=lambda x: -x[1])
        title = " · ".join(
            f"{r}×{c}" if c > 1 else r for r, c in rel_parts
        )

        edge: Dict[str, Any] = {
            "from": src_id,
            "to": tgt_id,
            "label": "",
            "title": title,
            "relation": dominant_rel,
            "relations": [{"type": r, "count": c} for r, c in rel_parts],
            "total": total,
            "dashes": False,
            "width": width,
            "color": {"color": color, "opacity": 0.7},
            "confidence": "EXTRACTED",
            "bidirectional": is_bidir,
        }
        if is_bidir:
            edge["arrows"] = {
                "to":   {"enabled": True, "scaleFactor": 0.4},
                "from": {"enabled": True, "scaleFactor": 0.4},
            }
        raw_edges.append(edge)

    hyperedges = _html_build_hyperedges(payload, kept, config.max_hyperedges, slug_map)

    communities_count = len(set(cid_map.values()))
    hidden_count = len(payload.concepts) - len(kept)
    stats_parts = [
        f"{len(kept)} nodes",
        f"{len(raw_edges)} edges",
        f"{communities_count} communities",
    ]
    if hidden_count > 0:
        stats_parts.append(f"{hidden_count} hidden by filter")
    stats_text = " · ".join(stats_parts)

    tmpl = template_path.read_text("utf-8")
    return (
        tmpl
        .replace("{{TITLE}}", f"{payload.project_name} — Synesis Graph")
        .replace("{{RAW_NODES_JSON}}", json.dumps(raw_nodes, ensure_ascii=False))
        .replace("{{RAW_EDGES_JSON}}", json.dumps(raw_edges, ensure_ascii=False))
        .replace("{{ALL_GROUPINGS_JSON}}", json.dumps(all_groupings, ensure_ascii=False))
        .replace("{{ACTIVE_GROUPING}}", json.dumps(legend_title))
        .replace("{{HYPEREDGES_JSON}}", json.dumps(hyperedges, ensure_ascii=False))
        .replace("{{EVIDENCE_JSON}}",      json.dumps(evidence_by_slug, ensure_ascii=False))
        .replace("{{EV_SOURCE_NODES_JSON}}", json.dumps(ev_extra_nodes, ensure_ascii=False))
        .replace("{{EV_MENTION_EDGES_JSON}}", json.dumps(ev_chain_edges, ensure_ascii=False))
        .replace("{{STATS_TEXT}}", stats_text)
    )


class HTMLBackendAdapter(BackendAdapter):
    """HTML graph backend — renders a self-contained vis-network HTML file."""

    def __init__(self, config: HTMLConfig, config_path: Path):
        self.config = config
        self._template_path = config_path.parent / "templates" / "graph.html.tmpl"
        self._output_path: Optional[Path] = None

    @property
    def backend_name(self) -> str:
        return BACKEND_HTML

    def preflight(self, reporter: TaskReporter) -> Optional[PipelineError]:
        if not self._template_path.exists():
            return DependencyError(
                message="HTML template not found",
                stage="preflight",
                details=str(self._template_path),
            )
        return None

    def connect(self, reporter: TaskReporter) -> Optional[PipelineError]:
        return None

    def prepare_destination(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        output = Path(self.config.output_path)
        if not output.is_absolute():
            output = Path.cwd() / output
        self._output_path = output
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return ConnectionError(
                message="Cannot create output directory",
                stage="prepare",
                details=str(e),
            )
        return None

    def clear_destination(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        return None

    def synchronize_payload(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        if self._output_path is None:
            return ConnectionError(message="Output path not initialized", stage="sync")
        with reporter.step("Rendering HTML graph"):
            try:
                html = _html_render_payload(payload, self.config, self._template_path)
                self._output_path.write_text(html, encoding="utf-8")
                reporter.info(
                    f"[{self.backend_name}] {len(html):,} bytes -> {self._output_path}"
                )
            except Exception as e:
                return SyncError(
                    message="Failed to render HTML graph",
                    stage="sync",
                    details=str(e),
                )
        return None

    def compute_backend_metrics(self, payload: GraphPayload, reporter: TaskReporter) -> Optional[PipelineError]:
        return None

    def close(self) -> None:
        pass


def build_backend_adapter(
    backend: str,
    config: PipelineConfig,
    config_path: Path,
    project_path: Path,
) -> Union[BackendAdapter, ConnectionError]:
    """Creates the backend adapter for the selected backend."""
    if backend == BACKEND_NEO4J:
        if not isinstance(config, Neo4jConfig):
            return ConnectionError(
                message="Internal configuration type mismatch",
                stage="config",
                details="Expected Neo4jConfig for backend 'neo4j'.",
            )
        return Neo4jBackendAdapter(config)

    if backend == BACKEND_GRAPHQLITE:
        if not isinstance(config, GraphQLiteConfig):
            return ConnectionError(
                message="Internal configuration type mismatch",
                stage="config",
                details="Expected GraphQLiteConfig for backend 'graphqlite'.",
            )
        return GraphQLiteBackendAdapter(config, config_path=config_path, project_path=project_path)

    if backend == BACKEND_HTML:
        if not isinstance(config, HTMLConfig):
            return ConnectionError(
                message="Internal configuration type mismatch",
                stage="config",
                details="Expected HTMLConfig for backend 'html'.",
            )
        return HTMLBackendAdapter(config, config_path=config_path)

    return ConnectionError(
        message="Unsupported backend",
        stage="backend",
        details=f"Supported backends: {', '.join(SUPPORTED_BACKENDS)}",
    )


def execute_backend_pipeline(
    adapter: BackendAdapter,
    payload: GraphPayload,
    reporter: TaskReporter,
) -> Optional[PipelineError]:
    """Executes backend pipeline operations using the adapter contract."""
    operation_error: Optional[PipelineError] = None
    close_error: Optional[ConnectionError] = None

    try:
        reporter.info(f"[{adapter.backend_name}] Phase: connect")
        connect_error = adapter.connect(reporter)
        if connect_error:
            operation_error = connect_error
        else:
            reporter.info(f"[{adapter.backend_name}] Phase: prepare_destination")
            prepare_error = adapter.prepare_destination(payload, reporter)
            if prepare_error:
                operation_error = prepare_error
            else:
                reporter.info(f"[{adapter.backend_name}] Phase: clear_destination")
                clear_error = adapter.clear_destination(payload, reporter)
                if clear_error:
                    operation_error = clear_error
                else:
                    reporter.info(f"[{adapter.backend_name}] Phase: synchronize_payload")
                    sync_error = adapter.synchronize_payload(payload, reporter)
                    if sync_error:
                        operation_error = sync_error
                    else:
                        reporter.info(f"[{adapter.backend_name}] Phase: compute_metrics")
                        metrics_error = adapter.compute_backend_metrics(payload, reporter)
                        if metrics_error:
                            operation_error = metrics_error
    except Exception as e:
        operation_error = SyncError(
            message="Unhandled backend execution error",
            stage="sync",
            details=str(e),
        )
    finally:
        reporter.info(f"[{adapter.backend_name}] Phase: shutdown")
        try:
            adapter.close()
        except Exception as e:
            close_error = ConnectionError(
                message="Failed to close backend resources",
                stage="shutdown",
                details=str(e),
            )

        if close_error:
            if operation_error is None:
                operation_error = close_error
            else:
                reporter.warning(
                    f"[{adapter.backend_name}] Shutdown warning after prior failure: {close_error.details}"
                )

    return operation_error


# ============================================================================
# MAIN PIPELINE
# ============================================================================
def run_pipeline(
    project_path: Optional[Path],
    config_path: Path,
    reporter: TaskReporter,
    backend: str = BACKEND_NEO4J,
    html_options: Optional[Dict[str, Any]] = None,
    json_path: Optional[Path] = None,
) -> PipelineResult:
    """
    Executes complete pipeline: compilation → connection → synchronization.

    Args:
        project_path: Path to .synp project (mutually exclusive with json_path)
        config_path: Path to config.toml
        reporter: Reporter for visual feedback
        html_options: Optional CLI overrides for the HTML backend (keys match HTMLConfig fields)
        json_path: Path to pre-compiled Synesis JSON export (alternative to project_path)

    Returns:
        PipelineResult indicating success or typed error.
    """
    # 1. Input validation
    if backend not in SUPPORTED_BACKENDS:
        return PipelineResult(
            success=False,
            error=ConnectionError(
                message="Unsupported backend",
                stage="backend",
                details=f"Supported backends: {', '.join(SUPPORTED_BACKENDS)}",
            ),
        )

    if json_path is None and project_path is None:
        return PipelineResult(
            success=False,
            error=CompilationError(
                message="Either --project or --json must be specified",
                stage="validation",
            )
        )

    source_path = json_path or project_path
    if not source_path.exists():
        return PipelineResult(
            success=False,
            error=CompilationError(
                message="Input file not found",
                stage="validation",
                details=str(source_path)
            )
        )

    # 2. Configuration
    with reporter.step("Loading Configuration"):
        config_result = load_config(config_path, backend)
        if isinstance(config_result, ConnectionError):
            return PipelineResult(success=False, error=config_result)
        config = config_result

        if backend == BACKEND_HTML and html_options and isinstance(config, HTMLConfig):
            for k, v in html_options.items():
                if v is not None and hasattr(config, k):
                    setattr(config, k, v)

        config_error = validate_backend_config(config, backend)
        if config_error:
            return PipelineResult(success=False, error=config_error)

    adapter_result = build_backend_adapter(
        backend=backend,
        config=config,
        config_path=config_path,
        project_path=project_path or Path("."),
    )
    if isinstance(adapter_result, ConnectionError):
        return PipelineResult(
            success=False,
            error=adapter_result,
        )
    adapter = adapter_result

    preflight_error = adapter.preflight(reporter)
    if preflight_error:
        return PipelineResult(success=False, error=preflight_error)

    # 3. Compilation or JSON load
    if json_path is not None:
        step_label = "Loading JSON Export"
        load_fn = lambda: load_json_project(json_path, reporter)
    else:
        step_label = "Compiling Project (In-Memory)"
        load_fn = lambda: compile_project(project_path, reporter)

    with reporter.step(step_label):
        compile_result = load_fn()
        if isinstance(compile_result, CompilationError):
            reporter.print_diagnostics(compile_result.diagnostics)
            return PipelineResult(success=False, error=compile_result)
        payload = compile_result

    # 4. Backend synchronization via adapter contract
    backend_error = execute_backend_pipeline(adapter, payload, reporter)
    if backend_error:
        return PipelineResult(
            success=False,
            error=backend_error,
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


# ============================================================================
# CLI — Click-based (same pattern as synesis and synesis-coder)
# ============================================================================

def _tty() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(text: str, **kwargs) -> str:
    if not _CLICK_AVAILABLE:
        return text
    return click.style(text, **kwargs) if _tty() else text


def _build_main_help() -> str:
    title = _c("SYNESIS GRAPH", fg="green", bold=True) + f" (v{__version__})"
    desc = "Universal pipeline from Synesis projects to graph databases and visualizations."
    usage = _c("Usage:", fg="yellow", bold=True) + " synesis-graph [OPTIONS] COMMAND [ARGUMENTS]..."

    groups = [
        ("Graph Backends", [
            ("neo4j",      "Sync project to a Neo4j database (bolt://)"),
            ("graphqlite", "Sync project to a GraphQLite SQLite file"),
            ("html",       "Render an interactive HTML graph visualization"),
        ]),
    ]

    opt_rows = [
        ("--version", "Show version and exit"),
        ("--help",    "Show this message and exit"),
    ]

    col = max(
        max(len(name) for _, rows in groups for name, _ in rows),
        max(len(name) for name, _ in opt_rows),
    ) + 2

    options = _c("Options:", fg="yellow", bold=True) + "\n" + "\n".join(
        f"  {_c(name.ljust(col), fg='cyan')}  {desc_}"
        for name, desc_ in opt_rows
    )

    def _render_group(label: str, rows: list) -> str:
        lines = [_c("  " + label, fg="yellow", bold=True)]
        for name, desc_ in rows:
            lines.append(f"    {_c(name.ljust(col), fg='green', bold=True)}  {desc_}")
        return "\n".join(lines)

    commands = _c("Commands:", fg="yellow", bold=True) + "\n\n" + "\n\n".join(
        _render_group(label, rows) for label, rows in groups
    )

    hint = _c(
        "Run 'synesis-graph COMMAND --help' for options and examples of each backend.",
        fg="bright_black",
    )

    return "\n\n".join([title, desc, usage, options, commands, hint]) + "\n"


def _ex(*lines: str) -> str:
    out = [_c("Examples:", fg="yellow", bold=True)]
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("#"):
            out.append(indent + _c(stripped, fg="bright_black"))
        else:
            tokens = re.split(r"(\s+)", stripped)
            result = []
            for tok in tokens:
                if tok == "synesis-graph":
                    result.append(_c(tok, fg="green", bold=True))
                elif re.match(r"^--[\w-]+=?", tok):
                    result.append(_c(tok, fg="cyan"))
                elif tok in ("neo4j", "graphqlite", "html"):
                    result.append(_c(tok, fg="green"))
                else:
                    result.append(tok)
            out.append(indent + "".join(result))
    return "\n".join(out)


_EPILOG_NEO4J = _ex(
    "  # Sync with default config (config.toml, bolt://127.0.0.1:7687):",
    "  synesis-graph neo4j --project project.synp",
    "",
    "  # Use a custom config file:",
    "  synesis-graph neo4j --project project.synp --config prod.toml",
    "",
    "  # Load from pre-compiled JSON (Synesis v3.0 export):",
    "  synesis-graph neo4j --json export.json --config prod.toml",
    "",
    "  # Target a specific named database:",
    "  synesis-graph neo4j --project project.synp --database my_corpus",
)

_EPILOG_GRAPHQLITE = _ex(
    "  # Sync to a local SQLite file (default path from config.toml):",
    "  synesis-graph graphqlite --project project.synp",
    "",
    "  # Custom config:",
    "  synesis-graph graphqlite --project project.synp --config custom.toml",
    "",
    "  # Load from pre-compiled JSON:",
    "  synesis-graph graphqlite --json export.json",
)

_EPILOG_HTML = _ex(
    "  # Render with default filters (min 3 mentions, min 2 sources):",
    "  synesis-graph html --project project.synp --output graph.html",
    "",
    "  # Disable all filters (show every concept):",
    "  synesis-graph html --project project.synp --output graph.html --all",
    "",
    "  # Color communities by a taxonomy field:",
    "  synesis-graph html --project project.synp --output graph.html --group-by topic",
    "",
    "  # Tune filters manually:",
    "  synesis-graph html --project project.synp --output graph.html --min-frequency 5 --max-nodes 100",
    "",
    "  # From pre-compiled JSON:",
    "  synesis-graph html --json export.json --output graph.html --all",
)


def _write_help_utf8() -> None:
    out = _build_main_help()
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        print(out)


def _validate_source(project, json_input) -> None:
    if not project and not json_input:
        raise click.UsageError("Provide either --project PATH or --json PATH.")
    if project and json_input:
        raise click.UsageError("--project and --json are mutually exclusive.")


def _run_and_exit(backend: str, project, json_input, config, html_options=None) -> None:
    reporter = TaskReporter(f"Synesis → {backend}")
    result = run_pipeline(
        project_path=Path(project).resolve() if project else None,
        json_path=Path(json_input).resolve() if json_input else None,
        config_path=Path(config).resolve(),
        reporter=reporter,
        backend=backend,
        html_options=html_options,
    )
    reporter.print_summary()
    sys.exit(0 if result.success else 1)


# Shared decorators
def _source_options(fn):
    fn = click.option("--json", "json_input", default=None, metavar="PATH",
                      help="Path to a Synesis v3.0 JSON export (alternative to --project).")(fn)
    fn = click.option("--project", default=None, metavar="PATH",
                      help="Path to a Synesis project file (.synp).")(fn)
    return fn


def _config_option(fn):
    return click.option("--config", default="config.toml", show_default=True, metavar="PATH",
                        help="Path to the TOML configuration file.")(fn)


if _CLICK_AVAILABLE:
    class _SynesisCommand(click.Command):
        def format_epilog(self, ctx, formatter):
            if self.epilog:
                formatter.write("\n")
                for line in self.epilog.splitlines():
                    formatter.write(line + "\n")

    class _SynesisGroup(click.Group):
        command_class = _SynesisCommand

        def format_help(self, ctx, formatter):
            pass

        def get_help(self, ctx):
            _write_help_utf8()
            raise SystemExit(0)

    @click.group(cls=_SynesisGroup, invoke_without_command=True)
    @click.version_option(version=__version__, prog_name="synesis-graph")
    @click.pass_context
    def main(ctx) -> None:
        """Universal pipeline from Synesis projects to graph databases."""
        if ctx.invoked_subcommand is None:
            _write_help_utf8()

    @main.command(cls=_SynesisCommand, name="neo4j", epilog=_EPILOG_NEO4J)
    @_source_options
    @_config_option
    @click.option("--database", default=None,
                  help="Neo4j database name (overrides config).")
    def cmd_neo4j(project, json_input, config, database):
        """Sync a Synesis project to a Neo4j database."""
        _validate_source(project, json_input)
        _run_and_exit(BACKEND_NEO4J, project, json_input, config)

    @main.command(cls=_SynesisCommand, name="graphqlite", epilog=_EPILOG_GRAPHQLITE)
    @_source_options
    @_config_option
    def cmd_graphqlite(project, json_input, config):
        """Sync a Synesis project to a GraphQLite SQLite file."""
        _validate_source(project, json_input)
        _run_and_exit(BACKEND_GRAPHQLITE, project, json_input, config)

    @main.command(cls=_SynesisCommand, name="html", epilog=_EPILOG_HTML)
    @_source_options
    @_config_option
    @click.option("--output", "html_output", default=None, metavar="PATH",
                  help="Output HTML file (default: ./graph.html).")
    @click.option("--group-by", "group_by", default=None, metavar="FIELD",
                  help="Template graph field for community colouring.")
    @click.option("--min-frequency", "min_frequency", type=int, default=None, metavar="N",
                  help="Hide concepts mentioned in fewer than N items (default: 3).")
    @click.option("--min-source-count", "min_source_count", type=int, default=None, metavar="N",
                  help="Hide concepts appearing in fewer than N sources (default: 2).")
    @click.option("--max-nodes", "max_nodes", type=int, default=None, metavar="N",
                  help="Limit to top-N concepts by degree (default: 200; 0 = unlimited).")
    @click.option("--max-hyperedges", "max_hyperedges", type=int, default=None, metavar="N",
                  help="Maximum hyperedges to render (default: 50).")
    @click.option("--include-isolated", "include_isolated", is_flag=True, default=False,
                  help="Include concepts with no chain connections.")
    @click.option("--all", "html_all", is_flag=True, default=False,
                  help="Disable all filters (show every concept).")
    def cmd_html(project, json_input, config, html_output, group_by, min_frequency,
                 min_source_count, max_nodes, max_hyperedges, include_isolated, html_all):
        """Render an interactive HTML graph visualization from a Synesis project."""
        _validate_source(project, json_input)
        html_options: Dict[str, Any] = {}
        if html_output:
            html_options["output_path"] = html_output
        if html_all:
            html_options.update({"min_frequency": 0, "min_source_count": 0,
                                  "max_nodes": 0, "include_isolated": True})
        else:
            if group_by is not None:
                html_options["group_by"] = group_by
            if min_frequency is not None:
                html_options["min_frequency"] = min_frequency
            if min_source_count is not None:
                html_options["min_source_count"] = min_source_count
            if max_nodes is not None:
                html_options["max_nodes"] = max_nodes
            if max_hyperedges is not None:
                html_options["max_hyperedges"] = max_hyperedges
            if include_isolated:
                html_options["include_isolated"] = True
        _run_and_exit(BACKEND_HTML, project, json_input, config, html_options)

else:
    # Fallback: argparse when click is not installed
    import argparse

    def main() -> int:  # type: ignore[misc]
        import argparse as _ap
        parser = _ap.ArgumentParser(description="Synesis Direct Link → Graph Databases")
        parser.add_argument("--version", "-v", action="version",
                            version=f"synesis-graph {__version__}")
        src = parser.add_mutually_exclusive_group(required=True)
        src.add_argument("--project", default=None)
        src.add_argument("--json", default=None, dest="json_input")
        parser.add_argument("--config", default="config.toml")
        parser.add_argument("--backend", choices=SUPPORTED_BACKENDS, default=BACKEND_NEO4J)
        parser.add_argument("--html-output", default=None)
        parser.add_argument("--html-group-by", default=None)
        parser.add_argument("--html-min-frequency", type=int, default=None)
        parser.add_argument("--html-min-source-count", type=int, default=None)
        parser.add_argument("--html-max-nodes", type=int, default=None)
        parser.add_argument("--html-max-hyperedges", type=int, default=None)
        parser.add_argument("--html-include-isolated", action="store_true", default=False)
        parser.add_argument("--html-all", action="store_true", default=False)
        args = parser.parse_args()

        html_options: Optional[Dict[str, Any]] = None
        if args.backend == BACKEND_HTML:
            html_options = {}
            if args.html_output:
                html_options["output_path"] = args.html_output
            if args.html_all:
                html_options.update({"min_frequency": 0, "min_source_count": 0,
                                     "max_nodes": 0, "include_isolated": True})
            else:
                if args.html_group_by:
                    html_options["group_by"] = args.html_group_by
                if args.html_min_frequency is not None:
                    html_options["min_frequency"] = args.html_min_frequency
                if args.html_min_source_count is not None:
                    html_options["min_source_count"] = args.html_min_source_count
                if args.html_max_nodes is not None:
                    html_options["max_nodes"] = args.html_max_nodes
                if args.html_max_hyperedges is not None:
                    html_options["max_hyperedges"] = args.html_max_hyperedges
                if args.html_include_isolated:
                    html_options["include_isolated"] = True

        reporter = TaskReporter(f"Synesis Direct Link ({args.backend})")
        result = run_pipeline(
            project_path=Path(args.project).resolve() if args.project else None,
            json_path=Path(args.json_input).resolve() if args.json_input else None,
            config_path=Path(args.config).resolve(),
            reporter=reporter, backend=args.backend, html_options=html_options,
        )
        reporter.print_summary()
        return 0 if result.success else 1


if __name__ == "__main__":
    if _CLICK_AVAILABLE:
        main(standalone_mode=True)
    else:
        sys.exit(main())
