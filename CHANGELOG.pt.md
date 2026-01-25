# Changelog

Todas as mudanças notáveis neste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/),
e este projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

**Idioma:** [English](CHANGELOG.md) | [Português](CHANGELOG.pt.md)

**Documentação:** [Synesis Language Docs](https://synesis-lang.github.io/synesis-docs)

---

## [0.1.0] - 2025-01-24

### Adicionado

#### Pipeline Universal
- **Modelagem Dinâmica:** Labels de nós derivados automaticamente do Template (.synt)
- **Suporte a CODE:** Campos CODE criam nós de conceito com label dinâmico
- **Suporte a CHAIN:** Campos CHAIN criam relações RELATES_TO entre conceitos
- **Suporte a Taxonomias:** TOPIC, ASPECT, DIMENSION criam hierarquias navegáveis
- **Rastreabilidade:** Metadados de origem (source_file, line, column) em todos os nós

#### Métricas de Grafo
- **Métricas Nativas (Cypher puro):**
  - `degree`, `in_degree`, `out_degree` para conceitos
  - `mention_count`, `source_count` para conceitos
  - `concept_count` para taxonomias
  - `weighted_degree`, `aspect_diversity`, `dimension_diversity` para Topics
  - `item_count`, `concept_count` para Sources

- **Métricas GDS (opcional):**
  - `pagerank` - PageRank para relevância/centralidade
  - `betweenness` - Betweenness Centrality para nós "ponte"
  - `community` - Louvain para detecção de comunidades

- **Estratégias de Projeção:**
  - `RELATES_TO` - usa relações explícitas (templates CHAIN)
  - `CO_TAXONOMY` - conecta conceitos via taxonomia compartilhada
  - `CO_CITATION` - conecta conceitos via co-citação em Sources

#### Infraestrutura
- **Controle de Versão:** `--version` flag no CLI
- **Fallback Gracioso:** Métricas nativas sempre calculadas; GDS opcional com aviso
- **Sanitização:** Labels e nomes de banco validados contra Cypher injection
- **Transações Atômicas:** Sincronização via transação única

#### Integração MCP (Claude Desktop)
- **Configuração Universal:** Templates para Claude Desktop (`mcp/`)
- **Suporte Multi-Banco:** Namespaces para múltiplos projetos simultâneos
- **Documentação GraphRAG:** Guia de queries com rastreabilidade total
- **Estudo de Viabilidade:** Análise completa em `docs/MCP_VIABILITY_STUDY.md`

### Documentação
- README.md com tabela de mapeamento Template → Grafo
- Documentação completa de métricas (nativas e GDS)
- Diagrama Mermaid do fluxo de dados
- Exemplos de consultas Cypher
- Guia de configuração MCP (`mcp/SETUP.md`)
- Referência de queries Cypher (`mcp/QUERIES_REFERENCE.md`)
- Documentação bilíngue (EN/PT)

---

## Roadmap

### [0.2.0] - Planejado
- [ ] Servidor MCP customizado Synesis-específico
- [ ] Prompts otimizados para pesquisa qualitativa
- [ ] Interface de configuração interativa

### [0.3.0] - Futuro
- [ ] Interface web para visualização do grafo
- [ ] Exportação para formatos externos (GraphML, GEXF)
- [ ] Integração com Jupyter Notebooks

---

## Links

- **Repositório:** [github.com/synesis-lang/synesis2neo4j](https://github.com/synesis-lang/synesis2neo4j)
- **Documentação:** [synesis-lang.github.io/synesis-docs](https://synesis-lang.github.io/synesis-docs)
- **Issues:** [GitHub Issues](https://github.com/synesis-lang/synesis2neo4j/issues)
