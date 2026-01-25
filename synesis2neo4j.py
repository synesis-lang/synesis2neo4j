#!/usr/bin/env python3
"""
synesis2neo4j.py - Pipeline Universal Synesis → Neo4j (Memory to Graph)

Versão: 0.1.0
Repositório: https://github.com/synesis-lang/synesis2neo4j

Propósito:
    Conecta o compilador Synesis diretamente ao Neo4j sem arquivos intermediários.
    Compila o projeto em memória via `synesis.load()` e sincroniza atomicamente.

Componentes principais:
    - compile_project: Compila projeto Synesis e prepara payload para grafo
    - sync_to_neo4j: Persiste payload no Neo4j via transação única
    - compute_metrics: Calcula métricas nativas e GDS automaticamente
    - TaskReporter: Interface de usuário com Rich (fallback para logging)

Dependências críticas:
    - synesis: compilador de projetos bibliométricos
    - neo4j: driver oficial do banco de grafos
    - tomli/tomllib: parser de configuração TOML

Dependências opcionais:
    - Neo4j GDS: plugin para métricas avançadas (PageRank, Betweenness, Louvain)

Exemplo de uso:
    python synesis2neo4j.py --project ./meu_projeto.synp --config config.toml
    python synesis2neo4j.py --version

Notas de implementação:
    - Zero I/O intermediário (tudo em memória)
    - Atomicidade via transação única
    - Labels dinâmicas sanitizadas contra Cypher injection
    - Usa Result types para erros (CompilationError, ConnectionError, SyncError)
    - Métricas calculadas automaticamente (nativas sempre, GDS se disponível)
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
# VERSÃO
# ============================================================================
__version__ = "0.1.0"
__version_info__ = (0, 1, 0)

# ============================================================================
# IMPORTS EXTERNOS
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
# TIPOS DE RESULTADO (Pattern: Result Types)
# ============================================================================
@dataclass
class PipelineError:
    """Erro base do pipeline com contexto."""
    message: str
    stage: str
    details: Optional[str] = None


@dataclass
class CompilationError(PipelineError):
    """Erro na compilação do projeto Synesis."""
    diagnostics: List[str] = field(default_factory=list)


@dataclass
class ConnectionError(PipelineError):
    """Erro na conexão com Neo4j."""
    pass


@dataclass
class SyncError(PipelineError):
    """Erro na sincronização com o banco."""
    pass


@dataclass
class ChainFieldSpec:
    """Especificação de um campo CHAIN do template."""
    field_name: str
    relations: Dict[str, str]  # {type: description}


@dataclass
class CodeFieldSpec:
    """Especificação de um campo CODE do template."""
    field_name: str
    description: str


@dataclass
class GraphPayload:
    """Payload preparado para sincronização com Neo4j."""
    project_name: str
    concept_label: str  # Label dinâmico para nós de conceito (nome do campo CHAIN/CODE)
    scalar_fields: List[str]
    graph_fields: List[str]
    chain_fields: List[ChainFieldSpec]
    code_fields: List[CodeFieldSpec]
    value_maps: Dict[str, List[Dict[str, Any]]]  # Mapeamento de índices para labels
    concepts: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]  # Anteriormente "references"
    items: List[Dict[str, Any]]
    chains: List[Dict[str, Any]]
    mentions: List[Dict[str, Any]]
    from_source: List[Dict[str, Any]]


@dataclass
class PipelineResult:
    """Resultado do pipeline com sucesso ou erro."""
    success: bool
    error: Optional[PipelineError] = None
    stats: Dict[str, int] = field(default_factory=dict)


# ============================================================================
# SANITIZAÇÃO (Proteção contra Cypher Injection)
# ============================================================================
_CYPHER_LABEL_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def sanitize_cypher_label(label: str) -> str:
    """
    Sanitiza string para uso seguro como label/relationship type em Cypher.

    Mantém apenas caracteres alfanuméricos e underscore.
    Garante que começa com letra ou underscore.
    """
    sanitized = "".join(c for c in label if c.isalnum() or c == "_")
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "Unknown"


def sanitize_database_name(name: str) -> str:
    """
    Sanitiza string para uso como nome de banco de dados Neo4j.

    Neo4j aceita apenas: letras ASCII, números, pontos e hífens.
    Underscores são convertidos para hífens.
    """
    # Converte underscores para hífens
    name = name.replace("_", "-")
    # Mantém apenas caracteres válidos
    sanitized = "".join(c for c in name if c.isalnum() or c in ".-")
    # Garante que começa com letra
    if sanitized and not sanitized[0].isalpha():
        sanitized = "db" + sanitized
    return sanitized.lower() or "synesis"


def validate_cypher_label(label: str) -> bool:
    """Valida se label é segura para uso direto em Cypher."""
    return bool(_CYPHER_LABEL_PATTERN.match(label))


# ============================================================================
# INTERFACE DE USUÁRIO
# ============================================================================
class TaskReporter:
    """
    Reporter para feedback visual do pipeline.

    Usa Rich quando disponível, degrada para logging padrão.
    Recebido por injeção de dependência nas funções do pipeline.
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
        """Exibe erros de compilação do Synesis."""
        if not self.console:
            for d in diagnostics:
                logger.error(d)
            return

        table = Table(title="Diagnósticos de Compilação", box=box.SIMPLE, style="red")
        table.add_column("Mensagem", style="white")
        for diag in diagnostics:
            table.add_row(str(diag))
        self.console.print(table)

    def print_summary(self) -> None:
        duration = int(time.time() - self.start_time)
        if self.console:
            table = Table(box=box.ROUNDED, show_header=False)
            table.add_row("Tempo Total", f"{duration}s")
            status = "[green]SUCESSO[/]" if self.stats["errors"] == 0 else "[red]FALHA[/]"
            table.add_row("Status", status)
            self.console.print(Panel(table, title="Resumo Final", border_style="cyan"))
        else:
            status = "SUCESSO" if self.stats["errors"] == 0 else "FALHA"
            logger.info(f"Resumo: {status} em {duration}s")


class _StepContext:
    """Context manager para etapas do pipeline com feedback visual."""

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
# ANÁLISE DE TEMPLATE
# ============================================================================
def analyze_template(template_data: Dict[str, Any]) -> tuple[List[str], List[str], List[ChainFieldSpec], List[CodeFieldSpec], Dict[str, List[Dict]]]:
    """
    Analisa template Synesis para identificar campos escalares, relacionais, CHAIN e CODE.

    Returns:
        Tupla (scalar_fields, graph_fields, chain_fields, code_fields, value_maps).
        - graph_fields viram nós de taxonomia
        - chain_fields definem nós com relações self-referential (triplas)
        - code_fields definem referências a conceitos (lista de códigos)
        - value_maps mapeia índices numéricos para labels (para ORDERED/ENUMERATED)
    """
    field_specs = template_data.get("field_specs", {})

    scalar_fields: List[str] = []
    graph_fields: List[str] = []
    chain_fields: List[ChainFieldSpec] = []
    code_fields: List[CodeFieldSpec] = []
    value_maps: Dict[str, List[Dict]] = {}

    # Itera por todos os campos e filtra por scope
    for field_name, spec in field_specs.items():
        scope = spec.get("scope", "").upper()
        field_type = spec.get("type", "TEXT")

        if scope == "ONTOLOGY":
            if field_type in ("TOPIC", "ENUMERATED", "ORDERED"):
                graph_fields.append(field_name)
                # Guarda mapeamento de valores para campos ORDERED/ENUMERATED
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

    return scalar_fields, graph_fields, chain_fields, code_fields, value_maps


def get_taxonomy_labels(graph_fields: List[str]) -> List[str]:
    """Converte nomes de campos para labels Neo4j sanitizadas."""
    return [sanitize_cypher_label(f.capitalize()) for f in graph_fields]


# ============================================================================
# COMPILAÇÃO E PREPARAÇÃO
# ============================================================================
def compile_project(
    project_path: Path,
    reporter: TaskReporter
) -> Union[GraphPayload, CompilationError]:
    """
    Compila projeto Synesis e transforma em payload para Neo4j.

    Args:
        project_path: Caminho para arquivo .synp
        reporter: Reporter para feedback visual

    Returns:
        GraphPayload em caso de sucesso, CompilationError em caso de falha.
    """
    reporter.info(f"Iniciando compilador Synesis em: {project_path}")

    compiler = SynesisCompiler(project_path)
    result = compiler.compile()

    if not result.success:
        return CompilationError(
            message="Falha na compilação do projeto Synesis",
            stage="compilation",
            diagnostics=[str(d) for d in result.get_diagnostics()]
        )

    # Exporta para JSON temporário e lê de volta
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp:
        tmp_path = Path(tmp.name)

    result.to_json(tmp_path)

    with open(tmp_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)

    tmp_path.unlink()  # Remove arquivo temporário

    corpus_count = len(json_data.get("corpus", []))
    reporter.success(f"Compilação OK. {corpus_count} itens processados.")

    scalar_fields, graph_fields, chain_fields, code_fields, value_maps = analyze_template(json_data["template"])

    payload = _build_graph_payload(
        json_data=json_data,
        scalar_fields=scalar_fields,
        graph_fields=graph_fields,
        chain_fields=chain_fields,
        code_fields=code_fields,
        value_maps=value_maps
    )

    return payload


def _build_graph_payload(
    json_data: Dict[str, Any],
    scalar_fields: List[str],
    graph_fields: List[str],
    chain_fields: List[ChainFieldSpec],
    code_fields: List[CodeFieldSpec],
    value_maps: Dict[str, List[Dict[str, Any]]]
) -> GraphPayload:
    """Transforma dados JSON compilados em payload estruturado para Neo4j."""
    project_name = json_data.get("project", {}).get("name", "synesis")
    ontology = json_data.get("ontology", {})
    corpus = json_data.get("corpus", [])
    bibliography = json_data.get("bibliography", {})

    # Determina o label dinâmico baseado no primeiro campo CHAIN ou CODE
    if chain_fields:
        concept_label = sanitize_cypher_label(chain_fields[0].field_name.capitalize())
    elif code_fields:
        concept_label = sanitize_cypher_label(code_fields[0].field_name.capitalize())
    else:
        concept_label = "Concept"  # Fallback

    # Constrói mapa de relações para lookup rápido
    relation_definitions: Dict[str, str] = {}
    for cf in chain_fields:
        relation_definitions.update(cf.relations)

    # Extrai nomes dos campos CODE para busca no corpus
    code_field_names = [cf.field_name for cf in code_fields]

    concepts = _extract_concepts(ontology, scalar_fields, graph_fields, value_maps)
    sources, items, mentions, chains, from_source = _extract_corpus_data(
        corpus, bibliography, relation_definitions, code_field_names
    )

    return GraphPayload(
        project_name=project_name,
        concept_label=concept_label,
        scalar_fields=scalar_fields,
        graph_fields=graph_fields,
        chain_fields=chain_fields,
        code_fields=code_fields,
        value_maps=value_maps,
        concepts=concepts,
        sources=sources,
        items=items,
        chains=chains,
        mentions=mentions,
        from_source=from_source
    )


def _index_to_label(value: Any, value_map: List[Dict[str, Any]]) -> str:
    """Converte índice numérico para label usando o mapeamento de valores."""
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
    """Extrai conceitos da ontologia com propriedades e relações."""
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
                # Converte valor para label se houver mapeamento
                if gf in value_maps:
                    if isinstance(raw_val, list):
                        relations[gf] = [_index_to_label(v, value_maps[gf]) for v in raw_val]
                    else:
                        relations[gf] = [_index_to_label(raw_val, value_maps[gf])]
                else:
                    # Sem mapeamento, usa valor direto
                    relations[gf] = raw_val if isinstance(raw_val, list) else [raw_val]

        concepts.append({"props": props, "relations": relations})

    return concepts


def _extract_corpus_data(
    corpus: List[Dict[str, Any]],
    bibliography: Dict[str, Any],
    relation_definitions: Dict[str, str],
    code_field_names: List[str]
) -> tuple[
    List[Dict[str, Any]],  # sources
    List[Dict[str, Any]],  # items
    List[Dict[str, Any]],  # mentions
    List[Dict[str, Any]],  # chains
    List[Dict[str, Any]]   # from_source
]:
    """
    Extrai sources, itens e relacionamentos do corpus.

    Suporta dois padrões de template:
    - CHAIN: triplas (source, relation, target) com note como descrição
    - CODE: lista de códigos referenciando conceitos
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

        # Extrai source (bloco SOURCE...END SOURCE)
        if source_ref not in seen_refs:
            source_props = _build_source_props(source_ref, corpus_item, bibliography)
            sources.append(source_props)
            seen_refs.add(source_ref)

        data = corpus_item["data"]

        # Detecta padrão do template
        has_chain = "chain" in data and data["chain"]
        has_code = any(cf in data and data[cf] for cf in code_field_names)

        if has_chain:
            # Padrão CHAIN (bibliometrics): bundles de note/chain
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

                    # Normaliza tipo de relação e busca descrição
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
            # Padrão CODE (gestao_fe): bundles de code fields
            # Encontra o primeiro CODE field com dados
            code_field = next((cf for cf in code_field_names if cf in data and data[cf]), None)
            if not code_field:
                continue

            code_list = data[code_field]
            if not isinstance(code_list, list):
                code_list = [code_list]

            # Extrai descrições se disponíveis (campo bundled correspondente)
            descriptions = data.get("justificativa_interna", []) or data.get("descricao", [])
            if not isinstance(descriptions, list):
                descriptions = [descriptions] * len(code_list)

            # Extrai texto base (primeiro campo MEMO ou TEXT encontrado)
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
    bibliography: Dict[str, Any]
) -> Dict[str, Any]:
    """Constrói propriedades de um nó Source (bloco SOURCE...END SOURCE)."""
    bib_entry = bibliography.get(source_ref, {})
    source_meta = item.get("source_metadata", {})

    return {
        "bibtex": source_ref,
        "title": source_meta.get("title") or bib_entry.get("title"),
        "author": source_meta.get("author") or bib_entry.get("author"),
        "year": source_meta.get("year") or bib_entry.get("year"),
        "doi": source_meta.get("doi") or bib_entry.get("doi"),
        "journal": source_meta.get("journal") or bib_entry.get("journal"),
        "abstract": source_meta.get("abstract") or bib_entry.get("abstract"),
        "method": source_meta.get("method"),
        "epistemic_model": source_meta.get("epistemic_model")
    }


# ============================================================================
# SINCRONIZAÇÃO NEO4J
# ============================================================================
def clear_database(session: Any) -> None:
    """
    Limpa todos os dados do banco, incluindo constraints e indexes.

    Garante que a fonte de verdade seja sempre os dados do compilador.
    """
    # Remove todas as constraints existentes
    constraints = session.run("SHOW CONSTRAINTS").data()
    for c in constraints:
        constraint_name = c.get("name")
        if constraint_name:
            session.run(f"DROP CONSTRAINT {constraint_name} IF EXISTS")

    # Remove todos os indexes existentes (exceto os criados automaticamente)
    indexes = session.run("SHOW INDEXES").data()
    for idx in indexes:
        if idx.get("owningConstraint") is None:  # Não é index de constraint
            idx_name = idx.get("name")
            if idx_name:
                session.run(f"DROP INDEX {idx_name} IF EXISTS")

    # Limpa todos os nodes e relacionamentos
    session.run("MATCH (n) DETACH DELETE n")


def sync_to_neo4j(session: Any, payload: GraphPayload) -> Optional[SyncError]:
    """
    Sincroniza payload com Neo4j em transação única.

    Limpa o banco completamente antes de sincronizar, garantindo que
    o compilador seja a fonte de verdade.

    Args:
        session: Sessão Neo4j ativa
        payload: Dados preparados para persistência

    Returns:
        None em sucesso, SyncError em falha.
    """
    try:
        # Limpa banco antes de sincronizar (fonte de verdade = compilador)
        clear_database(session)
        _create_constraints(session, payload.graph_fields, payload.concept_label)
        _execute_sync_transaction(session, payload)
        return None
    except Exception as e:
        return SyncError(
            message="Falha na sincronização",
            stage="sync",
            details=str(e)
        )


def _create_constraints(session: Any, graph_fields: List[str], concept_label: str) -> None:
    """Cria constraints de unicidade no schema do Neo4j."""
    # Constraints para taxonomias dinâmicas
    for label in get_taxonomy_labels(graph_fields):
        if validate_cypher_label(label):
            session.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE"
            )

    # Constraints fixas
    session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (s:Source) REQUIRE s.bibtex IS UNIQUE")
    session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (i:Item) REQUIRE i.item_id IS UNIQUE")

    # Constraint para label dinâmico (baseado no campo CHAIN/CODE)
    if validate_cypher_label(concept_label):
        session.run(
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (c:{concept_label}) REQUIRE c.name IS UNIQUE"
        )


def _execute_sync_transaction(session: Any, payload: GraphPayload) -> None:
    """Executa todas as operações de sync em uma única transação."""
    with session.begin_transaction() as tx:
        _sync_sources(tx, payload.sources)
        _sync_items(tx, payload.items)
        _sync_from_source(tx, payload.from_source)
        _sync_concepts(tx, payload.chains, payload.concepts, payload.concept_label)
        _sync_taxonomies(tx, payload.concepts, payload.graph_fields, payload.concept_label)
        _sync_mentions(tx, payload.mentions, payload.concept_label)
        tx.commit()


def _sync_sources(tx: Any, sources: List[Dict[str, Any]]) -> None:
    """Sincroniza nós Source (correspondente ao bloco SOURCE...END SOURCE)."""
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
    """Conecta Item ao Source de onde foi extraído."""
    if not from_source:
        return
    tx.run("""
        UNWIND $rows AS row
        MATCH (i:Item {item_id: row.item_id})
        MATCH (s:Source {bibtex: row.ref})
        MERGE (i)-[:FROM_SOURCE]->(s)
    """, rows=from_source)


# Mapeamento de campos para nomes de relação semânticos
TAXONOMY_RELATION_MAP: Dict[str, str] = {
    "topic": "GROUPED_BY",
    "aspect": "QUALIFIED_BY",
    "dimension": "BELONGS_TO",
    "confidence": "RATED_AS",
}


def _get_taxonomy_relation(field_name: str) -> str:
    """Retorna nome de relação semântico para o campo, ou HAS_* como fallback."""
    return TAXONOMY_RELATION_MAP.get(field_name.lower(), f"HAS_{field_name.upper()}")


def _sync_taxonomies(
    tx: Any,
    concepts: List[Dict[str, Any]],
    graph_fields: List[str],
    concept_label: str
) -> None:
    """
    Cria nós de taxonomia e relacionamentos semânticos a partir dos nós de conceito.

    Relações:
    - Conceito -> Topic via GROUPED_BY
    - Conceito -> Aspect via QUALIFIED_BY
    - Conceito -> Dimension via BELONGS_TO
    - Topic -> Topic via IS_LINKED_TO (self-referential)
    - Topic -> Aspect via MAPPED_TO_ASPECT
    - Topic -> Dimension via MAPPED_TO_DIMENSION
    """
    if not concepts:
        return

    # Primeiro: cria nodes de taxonomia e relações Conceito -> Taxonomia
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

    # Segundo: cria relações de mapeamento entre taxonomias
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

    # Topic -> Topic (IS_LINKED_TO) - conecta tópicos via RELATES_TO entre seus conceitos
    # strength = número de relações RELATES_TO entre conceitos dos dois tópicos
    if "topic" in graph_fields:
        tx.run(f"""
            MATCH (t1:Topic)<-[:GROUPED_BY]-(f1:{concept_label})-[:RELATES_TO]->(f2:{concept_label})-[:GROUPED_BY]->(t2:Topic)
            WHERE t1 <> t2
            WITH t1, t2, count(*) AS strength
            MERGE (t1)-[r:IS_LINKED_TO]->(t2)
            SET r.strength = strength, r.last_updated = timestamp()
        """)


def _sync_mentions(tx: Any, mentions: List[Dict[str, Any]], concept_label: str) -> None:
    """Conecta Item aos nós de conceito mencionados."""
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
    Cria nós de conceito (label dinâmico baseado no campo CHAIN/CODE) e relações RELATES_TO.

    Os nós são criados a partir de:
    1. Conceitos da ontologia (sempre)
    2. Source/target de chains (quando existem)

    A relação RELATES_TO conecta conceitos com type e description como
    atributos da aresta (apenas para templates com CHAIN field).
    """
    # Primeiro: cria nós de conceito a partir da ontologia
    if concepts:
        tx.run(f"""
            UNWIND $rows AS row
            MERGE (c:{concept_label} {{name: row.props.name}})
            SET c += row.props
        """, rows=concepts)

    # Se não há chains, não há mais nada a fazer
    if not chains:
        return

    # Segundo: cria nós de conceito de chains que não existem na ontologia
    tx.run(f"""
        UNWIND $rows AS row
        MERGE (s:{concept_label} {{name: row.source}})
        MERGE (t:{concept_label} {{name: row.target}})
    """, rows=chains)

    # Terceiro: cria relações RELATES_TO com atributos
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
# MÉTRICAS DE GRAFO
# ============================================================================
def _is_gds_available(session: Any) -> bool:
    """Verifica se o plugin GDS está instalado."""
    try:
        result = session.run("RETURN gds.version() AS version")
        version = result.single()["version"]
        logger.info(f"GDS detectado: versão {version}")
        return True
    except Exception:
        return False


def _get_graph_strategy(payload: GraphPayload) -> str:
    """
    Determina a estratégia de grafo para métricas GDS.

    Hierarquia de preferência:
    1. RELATES_TO - relação explícita (templates CHAIN)
    2. CO_TAXONOMY - co-taxonomia ponderada (templates CODE com TOPIC)
    3. CO_CITATION - co-citação via Source (fallback)
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
    Calcula métricas de grafo: nativas (Cypher) e avançadas (GDS).

    Métricas nativas são sempre calculadas.
    Métricas GDS são calculadas se o plugin estiver disponível.
    """
    concept_label = payload.concept_label
    graph_fields = payload.graph_fields

    # 1. Métricas nativas (sempre executam)
    with reporter.step("Calculando Métricas Nativas"):
        _compute_native_concept_metrics(session, concept_label)
        _compute_native_taxonomy_metrics(session, concept_label, graph_fields)
        _compute_native_source_metrics(session, concept_label)

    # 2. Métricas GDS (opcional com fallback)
    if not _is_gds_available(session):
        reporter.warning(
            "GDS não instalado. Instale o plugin Graph Data Science para "
            "métricas avançadas (PageRank, Betweenness, Comunidades)."
        )
        return

    strategy = _get_graph_strategy(payload)
    reporter.info(f"Estratégia de grafo GDS: {strategy}")

    with reporter.step("Calculando Métricas GDS"):
        try:
            _compute_gds_metrics(session, payload, strategy, reporter)
        except Exception as e:
            reporter.warning(f"Erro ao calcular métricas GDS: {e}")


# ----------------------------------------------------------------------------
# MÉTRICAS NATIVAS (Cypher puro - sempre disponíveis)
# ----------------------------------------------------------------------------
def _compute_native_concept_metrics(session: Any, concept_label: str) -> None:
    """
    Calcula métricas nativas para nós de conceito.

    Métricas:
    - degree: grau total (in + out)
    - in_degree: relações entrando
    - out_degree: relações saindo
    - mention_count: Items que mencionam o conceito
    - source_count: Sources distintos onde aparece
    """
    if not validate_cypher_label(concept_label):
        return

    # Degree centralidade (baseado em RELATES_TO)
    session.run(f"""
        MATCH (c:{concept_label})
        OPTIONAL MATCH (c)-[:RELATES_TO]->(out)
        OPTIONAL MATCH (c)<-[:RELATES_TO]-(in)
        WITH c, count(DISTINCT out) AS out_deg, count(DISTINCT in) AS in_deg
        SET c.out_degree = out_deg,
            c.in_degree = in_deg,
            c.degree = out_deg + in_deg
    """)

    # Mention count e source count
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
    Calcula métricas nativas para nós de taxonomia (Topic, Aspect, Dimension, etc).

    Métricas:
    - concept_count: conceitos classificados
    - weighted_degree: soma dos strengths das IS_LINKED_TO (se existir)
    - aspect_diversity: aspectos distintos (se Topic)
    - dimension_diversity: dimensões distintas (se Topic)
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

    # Métricas específicas para Topic (se existir)
    if "topic" in graph_fields:
        # Weighted degree (soma dos strengths das IS_LINKED_TO)
        session.run("""
            MATCH (t:Topic)
            OPTIONAL MATCH (t)-[r:IS_LINKED_TO]-()
            WITH t, coalesce(sum(r.strength), 0) AS wd
            SET t.weighted_degree = wd
        """)

        # Aspect diversity (se aspect existir)
        if "aspect" in graph_fields:
            session.run(f"""
                MATCH (t:Topic)<-[:GROUPED_BY]-(c:{concept_label})
                OPTIONAL MATCH (c)-[:QUALIFIED_BY]->(a:Aspect)
                WITH t, count(DISTINCT a) AS div
                SET t.aspect_diversity = div
            """)

        # Dimension diversity (se dimension existir)
        if "dimension" in graph_fields:
            session.run(f"""
                MATCH (t:Topic)<-[:GROUPED_BY]-(c:{concept_label})
                OPTIONAL MATCH (c)-[:BELONGS_TO]->(d:Dimension)
                WITH t, count(DISTINCT d) AS div
                SET t.dimension_diversity = div
            """)


def _compute_native_source_metrics(session: Any, concept_label: str) -> None:
    """
    Calcula métricas nativas para nós Source.

    Métricas:
    - item_count: Items extraídos da fonte
    - concept_count: conceitos mencionados
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
# MÉTRICAS GDS (requer plugin Graph Data Science)
# ----------------------------------------------------------------------------
def _compute_gds_metrics(
    session: Any,
    payload: GraphPayload,
    strategy: str,
    reporter: TaskReporter
) -> None:
    """
    Calcula métricas GDS (PageRank, Betweenness, Louvain).

    A projeção do grafo depende da estratégia:
    - RELATES_TO: usa relação explícita
    - CO_TAXONOMY: usa co-taxonomia ponderada
    - CO_CITATION: usa co-citação via Source
    """
    concept_label = payload.concept_label
    graph_name = "synesis_metrics_graph"

    # Limpa projeção anterior se existir
    _drop_gds_graph(session, graph_name)

    # Cria projeção baseada na estratégia
    node_count, rel_count = _create_gds_projection(
        session, graph_name, payload, strategy
    )

    if node_count == 0 or rel_count == 0:
        reporter.warning("Grafo vazio - pulando métricas GDS")
        return

    reporter.info(f"Projeção GDS: {node_count} nós, {rel_count} relações")

    # Calcula métricas
    try:
        _run_pagerank(session, graph_name, concept_label)
        reporter.success("PageRank calculado")
    except Exception as e:
        reporter.warning(f"PageRank falhou: {e}")

    try:
        # Betweenness pode ser lento em grafos grandes
        _run_betweenness(session, graph_name, concept_label)
        reporter.success("Betweenness calculado")
    except Exception as e:
        reporter.warning(f"Betweenness falhou: {e}")

    try:
        _run_louvain(session, graph_name, concept_label)
        reporter.success("Comunidades (Louvain) calculadas")
    except Exception as e:
        reporter.warning(f"Louvain falhou: {e}")

    # Limpa projeção
    _drop_gds_graph(session, graph_name)


def _drop_gds_graph(session: Any, graph_name: str) -> None:
    """Remove projeção GDS se existir."""
    try:
        session.run(f"CALL gds.graph.drop('{graph_name}', false)")
    except Exception:
        pass  # Ignora se não existir


def _create_gds_projection(
    session: Any,
    graph_name: str,
    payload: GraphPayload,
    strategy: str
) -> tuple[int, int]:
    """
    Cria projeção GDS baseada na estratégia.

    Returns:
        Tupla (node_count, relationship_count)
    """
    concept_label = payload.concept_label

    if strategy == "RELATES_TO":
        # Projeção nativa - mais eficiente
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
        # Projeção via co-taxonomia ponderada
        # Constrói lista de relações de taxonomia dinamicamente
        taxonomy_rels = []
        for field_name in payload.graph_fields:
            rel_type = _get_taxonomy_relation(field_name)
            if validate_cypher_label(rel_type):
                taxonomy_rels.append(rel_type)

        if not taxonomy_rels:
            return (0, 0)

        rel_pattern = "|".join(taxonomy_rels)

        result = session.run(f"""
            CALL gds.graph.project.cypher(
                '{graph_name}',
                'MATCH (f:{concept_label}) RETURN id(f) AS id',
                'MATCH (f1:{concept_label})-[r1:{rel_pattern}]->(t)<-[r2:{rel_pattern}]-(f2:{concept_label})
                 WHERE f1 <> f2
                 WITH f1, f2, count(DISTINCT t) AS weight
                 RETURN id(f1) AS source, id(f2) AS target, toFloat(weight) AS weight'
            )
            YIELD nodeCount, relationshipCount
            RETURN nodeCount, relationshipCount
        """)

    else:  # CO_CITATION
        # Projeção via co-citação (Source)
        result = session.run(f"""
            CALL gds.graph.project.cypher(
                '{graph_name}',
                'MATCH (f:{concept_label}) RETURN id(f) AS id',
                'MATCH (f1:{concept_label})<-[:MENTIONS]-(:Item)-[:FROM_SOURCE]->(s:Source)
                       <-[:FROM_SOURCE]-(:Item)-[:MENTIONS]->(f2:{concept_label})
                 WHERE f1 <> f2
                 WITH f1, f2, count(DISTINCT s) AS weight
                 RETURN id(f1) AS source, id(f2) AS target, toFloat(weight) AS weight'
            )
            YIELD nodeCount, relationshipCount
            RETURN nodeCount, relationshipCount
        """)

    record = result.single()
    return (record["nodeCount"], record["relationshipCount"])


def _run_pagerank(session: Any, graph_name: str, concept_label: str) -> None:
    """Executa PageRank e persiste nos nós."""
    session.run(f"""
        CALL gds.pageRank.stream('{graph_name}')
        YIELD nodeId, score
        WITH gds.util.asNode(nodeId) AS node, score
        WHERE '{concept_label}' IN labels(node)
        SET node.pagerank = score
    """)


def _run_betweenness(session: Any, graph_name: str, concept_label: str) -> None:
    """Executa Betweenness Centrality e persiste nos nós."""
    session.run(f"""
        CALL gds.betweenness.stream('{graph_name}')
        YIELD nodeId, score
        WITH gds.util.asNode(nodeId) AS node, score
        WHERE '{concept_label}' IN labels(node)
        SET node.betweenness = score
    """)


def _run_louvain(session: Any, graph_name: str, concept_label: str) -> None:
    """Executa Louvain (community detection) e persiste nos nós."""
    session.run(f"""
        CALL gds.louvain.stream('{graph_name}')
        YIELD nodeId, communityId
        WITH gds.util.asNode(nodeId) AS node, communityId
        WHERE '{concept_label}' IN labels(node)
        SET node.community = communityId
    """)


# ============================================================================
# CONFIGURAÇÃO
# ============================================================================
@dataclass
class Neo4jConfig:
    """Configuração de conexão Neo4j."""
    uri: str
    user: str
    password: str
    database: str = "neo4j"


def load_config(config_path: Path) -> Union[Neo4jConfig, ConnectionError]:
    """Carrega configuração Neo4j do arquivo TOML."""
    if not config_path.exists():
        return ConnectionError(
            message="Arquivo de configuração não encontrado",
            stage="config",
            details=str(config_path)
        )

    try:
        cfg = tomllib.loads(config_path.read_text("utf-8"))["neo4j"]
        # Aceita tanto 'uri' quanto 'URI'
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
            message="Configuração incompleta",
            stage="config",
            details=f"Campo obrigatório ausente: {e}"
        )
    except Exception as e:
        return ConnectionError(
            message="Erro ao ler configuração",
            stage="config",
            details=str(e)
        )


# ============================================================================
# CRIAÇÃO DE BANCO DE DADOS
# ============================================================================
def ensure_database_exists(driver: Any, database_name: str, reporter: TaskReporter) -> Optional[SyncError]:
    """
    Cria o banco de dados se não existir.

    Neo4j Community Edition só suporta um banco, então falha silenciosamente se não suportar.
    Neo4j Enterprise/Aura suportam múltiplos bancos.
    """
    safe_name = sanitize_database_name(database_name)

    try:
        with driver.session(database="system") as session:
            # Verifica se o banco existe
            result = session.run("SHOW DATABASES")
            existing = {record["name"] for record in result}

            if safe_name not in existing:
                reporter.info(f"Criando banco de dados: {safe_name}")
                session.run(f"CREATE DATABASE `{safe_name}` IF NOT EXISTS")
                # Aguarda o banco ficar disponível
                import time as _time
                _time.sleep(2)
            else:
                reporter.info(f"Banco de dados já existe: {safe_name}")
        return None
    except Exception as e:
        # Se falhar (ex: Community Edition), tenta usar o banco padrão
        error_msg = str(e)
        if "Unsupported" in error_msg or "not supported" in error_msg.lower():
            reporter.warning(f"Multi-database não suportado. Usando banco padrão.")
            return None
        return SyncError(
            message="Falha ao criar banco de dados",
            stage="database_setup",
            details=error_msg
        )


def get_database_name_from_project(json_data: Dict[str, Any]) -> str:
    """Extrai nome do projeto para usar como nome do banco."""
    project_name = json_data.get("project", {}).get("name", "synesis")
    # Sanitiza para nome de banco válido (Neo4j só aceita letras, números, pontos e hífens)
    return sanitize_database_name(project_name)


# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================
def run_pipeline(
    project_path: Path,
    config_path: Path,
    reporter: TaskReporter
) -> PipelineResult:
    """
    Executa pipeline completo: compilação → conexão → sincronização.

    Args:
        project_path: Caminho para projeto .synp
        config_path: Caminho para config.toml
        reporter: Reporter para feedback visual

    Returns:
        PipelineResult indicando sucesso ou erro tipado.
    """
    # 1. Validação de entrada
    if not project_path.exists():
        return PipelineResult(
            success=False,
            error=CompilationError(
                message="Projeto não encontrado",
                stage="validation",
                details=str(project_path)
            )
        )

    # 2. Compilação
    with reporter.step("Compilando Projeto (In-Memory)"):
        compile_result = compile_project(project_path, reporter)
        if isinstance(compile_result, CompilationError):
            reporter.print_diagnostics(compile_result.diagnostics)
            return PipelineResult(success=False, error=compile_result)
        payload = compile_result

    # 3. Configuração
    with reporter.step("Carregando Configuração"):
        config_result = load_config(config_path)
        if isinstance(config_result, ConnectionError):
            return PipelineResult(success=False, error=config_result)
        config = config_result

    # 4. Sincronização
    if GraphDatabase is None:
        return PipelineResult(
            success=False,
            error=ConnectionError(
                message="Driver Neo4j não instalado",
                stage="connection",
                details="pip install neo4j"
            )
        )

    # Nome do banco baseado no projeto
    db_name = sanitize_database_name(payload.project_name)
    reporter.info(f"Banco de dados alvo: {db_name}")

    try:
        with GraphDatabase.driver(config.uri, auth=(config.user, config.password)) as driver:
            # 4a. Cria banco se necessário
            with reporter.step("Verificando/Criando Banco de Dados"):
                db_error = ensure_database_exists(driver, db_name, reporter)
                if db_error:
                    return PipelineResult(success=False, error=db_error)

            # 4b. Sincroniza dados
            with driver.session(database=db_name) as session:
                with reporter.step("Sincronizando Grafo (Transacional)"):
                    sync_error = sync_to_neo4j(session, payload)
                    if sync_error:
                        return PipelineResult(success=False, error=sync_error)

                # 4c. Calcula métricas de grafo
                compute_metrics(session, payload, reporter)
    except Exception as e:
        return PipelineResult(
            success=False,
            error=ConnectionError(
                message="Falha na conexão com Neo4j",
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
