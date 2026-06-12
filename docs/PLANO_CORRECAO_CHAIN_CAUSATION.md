# Plano de Correção — CHAIN multi-triplo / sem `note` (Causation Coding)

> Estudo de impacto e plano de implementação. **Nenhum código foi alterado ainda.**
> Documento gerado em 2026-06-02 para o repositório `synesis2neo4j`.

---

## 1. Sintoma observado

O relatório HTML do projeto **Causation_Coding_Oratoria** exibe `39 nodes · 0 edges`,
sem nenhuma conexão entre os nós e com o botão **"Evidência"** desabilitado, apesar de:

- O JSON canônico exportado pelo compilador conter **32 chains** corretamente
  (`export_metadata.chain_count = 32`), com a estrutura esperada
  `{ "from": ..., "relation": ..., "to": ... }`.
- As anotações (`annotations.syn`) definirem 16 cadeias causais explícitas.

Os outros dois projetos auditados estão **corretos**:

| Projeto | nodes · edges | Diagnóstico |
|---|---|---|
| Basic | 2 · 0 | Correto — anotações não têm campo CHAIN |
| DSAP Vila dos Coqueiros | 6 · 0 | Correto — anotações não têm campo CHAIN |
| **Causation Oratória** | **39 · 0** | **BUG — deveria ter ~32 arestas** |

---

## 2. Causa raiz

A função [`_extract_corpus_data`](../synesis2graph.py) — único ponto do módulo que
popula `payload.chains` a partir do JSON — assume **um único formato** de BUNDLE
no ramo `has_chain` (linha ~654):

```python
notes = data.get("note", [])
chain_list = data.get("chain", [])

for idx, (note, chain) in enumerate(zip(notes, chain_list), 1):
    ...
    src = chain.get("from", "").strip()
    rel = chain.get("relation", "").strip()
    tgt = chain.get("to", "").strip()
```

Esse código pressupõe:

1. Existe um campo MEMO chamado exatamente **`note`**.
2. `note` e `chain` são **listas paralelas 1:1** (`zip` casa cada nota a um triplo).

### Os dois formatos reais que o compilador produz

Ambos os projetos usam o mesmo padrão de template — `REQUIRED BUNDLE <memo>, chain` —
mas o compilador serializa o BUNDLE de formas diferentes conforme a cardinalidade:

#### Formato A — Bibliométrico (`bibliometrics`, `social_acceptance`)

Cada ITEM agrupa **várias** relações independentes. O MEMO chama-se `note`.
O compilador emite **listas paralelas**:

```json
{
  "note":  ["nota 1", "nota 2", "nota 3", "nota 4"],
  "chain": [
    {"from": "Gender",         "relation": "INFLUENCES", "to": "CCS_Support"},
    {"from": "Knowledge",      "relation": "INFLUENCES", "to": "CCS_Support"},
    {"from": "Economic_Value", "relation": "INFLUENCES", "to": "CCS_Support"},
    {"from": "Risk_Perception","relation": "CONSTRAINS", "to": "CCS_Support"}
  ]
}
```

→ `zip(notes, chain_list)` produz 4 iterações. **Funciona.**

#### Formato B — Causation Coding (`Causation_Coding_Oratoria`)

Cada ITEM contém **uma cadeia encadeada** (`A -> REL -> B -> REL -> C`),
que o compilador serializa como **lista de triplos consecutivos**.
O MEMO chama-se **`resumo`** (string única, não lista), e **não existe** campo `note`:

```json
{
  "citação": "Eu era muito tímido ...",
  "resumo":  "A timidez alimentava medo de exposição ...",
  "chain": [
    {"from": "Timidez",       "relation": "CAUSA",     "to": "Medo_De_Falar"},
    {"from": "Medo_De_Falar", "relation": "RESTRINGE", "to": "Fala_Em_Publico"}
  ]
}
```

→ `notes = data.get("note", [])` retorna `[]` (campo inexistente).
→ `zip([], chain_list)` produz **0 iterações**.
→ **Nenhuma chain é adicionada a `payload.chains`.**

### Verificação empírica realizada

- Compilado `bibliometrics.synp` com `--force` → confirmado `note: list[N]`, `chain: list[N]` paralelos.
- Inspecionado `causation_coding_oratoria.json` → confirmado: sem `note`, `resumo: str`, `chain: list[2 triplos]`.

---

## 3. Efeito em cascata (por que tudo desaparece)

`payload.chains` é a **única fonte** a jusante. Com a lista vazia:

| Consumidor | Localização | Efeito quando `chains == []` |
|---|---|---|
| `degree` (grau dos nós) | `_html_apply_filters` + `_build_html` | Todos os nós com grau 0 → tamanho mínimo, slider sem efeito |
| `filtered_chains` | `_html_apply_filters` | Vazio → `raw_edges` vazio → **0 arestas** |
| `evidence_by_slug` | `_build_html` ~2300 | Vazio → modo Evidência sem registros |
| `ev_chain_edges` | `_build_html` ~2336 | Vazio → botão "Evidência" desabilitado no JS (`_evActiveNodes.size === 0`) |
| `_sync_concepts` (Neo4j) | `_build_*` Neo4j paths | Nenhuma relação `RELATES_TO` criada no banco |

> **Os nós aparecem** porque `include_isolated=True` preserva conceitos da ontologia
> mesmo sem arestas. Por isso o sintoma é "39 nós soltos".

---

## 4. Solução proposta

Tornar o ramo `has_chain` de [`_extract_corpus_data`](../synesis2graph.py) **agnóstico ao formato**,
processando os triplos de `chain` **diretamente**, sem depender de um campo `note` paralelo.

### Princípio de design

> Cada triplo `{from, relation, to}` da lista `chain` vira **uma aresta**, em ambos os formatos.
> Isto já é semanticamente correto: no formato A os triplos são relações independentes;
> no formato B são segmentos de uma cadeia — em ambos os casos o grafo quer uma aresta por triplo.

### Lógica revisada (pseudocódigo — não aplicar ainda)

```python
if has_chain:
    chain_list = data.get("chain", [])
    notes      = data.get("note", [])          # pode não existir
    # MEMO genérico: 'note' (biblio) OU primeiro campo MEMO do BUNDLE (causation)
    memo_fallback = _resolve_memo_text(data)   # ex.: 'resumo', 'note', etc.
    base_text     = data.get("text") or data.get("citação") or data.get("citation") or ""

    for idx, chain in enumerate(chain_list, 1):
        # nota específica do triplo, se houver lista paralela; senão MEMO do item
        if idx - 1 < len(notes):
            note = notes[idx - 1]
        else:
            note = memo_fallback

        item_id = f"{corpus_id}_n{idx:04d}"
        items.append({"item_id": item_id, "citation": base_text, "description": note})
        from_source.append({"item_id": item_id, "ref": source_ref})

        src = chain.get("from", "").strip()
        rel = chain.get("relation", "").strip()
        tgt = chain.get("to", "").strip()
        if src and tgt:
            mentions.append({"item_id": item_id, "concept": src, "mention_order": 1})
            mentions.append({"item_id": item_id, "concept": tgt, "mention_order": 2})
            rel_type = rel.upper().replace(" ", "_").replace("-", "_")
            chains.append({
                "source": src, "target": tgt, "type": rel_type,
                "description": relation_definitions.get(rel, ""),
                "item_id": item_id,
            })
```

### Como `_resolve_memo_text` deve descobrir o MEMO (Template como Fonte da Verdade)

Para respeitar a regra inviolável **"nenhum campo hardcoded"** (AI_INSTRUCTIONS §10),
o nome do campo MEMO **não** deve ser fixado como `"resumo"`. Opções, em ordem de preferência:

1. **Derivar do template** — passar `field_specs` para `_extract_corpus_data` e localizar
   o campo `TYPE MEMO` cujo `scope == "ITEM"` que está em BUNDLE com o `chain`.
   (Mais correto, porém aumenta a superfície da assinatura da função.)
2. **Heurística sobre `data`** — escolher o primeiro valor `str` (não-lista) do `data`
   que não seja um campo já conhecido (`text`, `citação`, `citation`, `chain`, campos CODE).
   (Menos invasivo; suficiente para o caso atual.)

> Decisão a confirmar com o usuário na §8.

---

## 5. Garantia de não-regressão

### 5.1 Formato A (bibliométrico) permanece idêntico

A nova lógica itera `for idx, chain in enumerate(chain_list)` e usa `notes[idx-1]`
**quando a lista paralela existe**. Para o formato A:

- `notes` tem o mesmo comprimento de `chain_list` → cada triplo recebe sua nota correta.
- `item_id` permanece no padrão `{corpus_id}_n{idx:04d}` → **inalterado**.
- `mentions`, `chains`, `from_source` gerados de forma idêntica à atual.

✅ **Saída byte-a-byte equivalente** para projetos bibliométricos.

### 5.2 Formato CODE permanece intocado

O ramo `elif has_code:` **não é modificado**. Projetos como `Davi_Projeto_Completo`
(`ordem_1a` / `justificativa_interna`) seguem inalterados.

### 5.3 Projetos sem CHAIN permanecem corretos

Basic e DSAP não entram no ramo `has_chain` (sem campo `chain`) → continuam `0 edges`.

### 5.4 Consumidores a jusante não mudam

Como **apenas** `_extract_corpus_data` é alterada e ela preenche as mesmas estruturas
(`sources`, `items`, `mentions`, `chains`, `from_source`) com os mesmos esquemas de chave,
**nenhum** dos consumidores precisa de alteração:

- HTML: `_html_apply_filters`, `_build_html`, modo Evidência — funcionam automaticamente.
- Neo4j: `_sync_concepts`, `_sync_mentions`, GDS projection — funcionam automaticamente.

---

## 6. Análise de impacto (GitNexus — obrigatório antes de editar)

> Ainda **não executado** (sessão somente de estudo). Antes de aplicar o código:

```
gitnexus_impact({target: "_extract_corpus_data", direction: "upstream"})
```

Esperado (a confirmar): chamadores diretos são `_build_graph_payload`
(d=1) e, transitivamente, todos os caminhos de export HTML/CSV/Neo4j (d=2/d=3).
Como a mudança é **aditiva** (passa a processar casos antes ignorados) e preserva
o esquema de saída, o risco material é **baixo**, mas o blast radius nominal é amplo.

---

## 7. Plano de validação

1. **Recompilar/regenerar** o HTML de Causation Oratória e confirmar:
   - `~32 edges` (ou o nº de triplos com `from`+`to` válidos).
   - Botão "Evidência" habilitado; clicar numa aresta mostra `citação` + `resumo`.
   - Grau dos nós > 0; slider "Min connections" passa a filtrar.
2. **Regenerar** o HTML de um projeto bibliométrico (`bibliometrics` ou
   `social_acceptance`) e confirmar que o nº de arestas e a tabela de evidência
   **não mudaram** em relação à saída atual (diff vazio no payload de chains).
3. **Reconfirmar** Basic e DSAP: continuam `0 edges`.
4. **Testes**: rodar `pytest tests/` em `synesis2neo4j`. Adicionar caso cobrindo
   o Formato B (chain multi-triplo sem `note`) — atualmente não há fixture para ele.
5. `gitnexus_detect_changes()` antes de commit: confirmar que só
   `_extract_corpus_data` (e teste novo) mudaram.

---

## 8. Decisões a confirmar com o usuário

1. **Estratégia de descoberta do MEMO** (§4): derivar do template (opção 1, mais
   robusta porém altera assinatura de `_extract_corpus_data`) **ou** heurística
   sobre `data` (opção 2, mais contida)?
2. **`item_id` no formato B**: manter o prefixo `_n{idx}` (consistente com biblio)
   ou usar um prefixo distinto? (Recomendação: manter `_n` — sem impacto funcional.)
3. **Texto de evidência no formato B**: como todos os triplos de uma cadeia
   compartilham a mesma `citação`/`resumo`, a tabela de Evidência mostrará o mesmo
   texto para cada aresta da cadeia. Aceitável? (Recomendação: sim — é o
   comportamento semântico correto, a cadeia inteira deriva da mesma fala.)

---

## 9. Resumo executivo

| | |
|---|---|
| **Arquivo a alterar** | `synesis2neo4j/synesis2graph.py` — função `_extract_corpus_data`, ramo `has_chain` |
| **Linhas-foco** | ~649–682 |
| **Natureza** | Aditiva (processar formato antes ignorado), preservando o formato existente |
| **Risco de regressão** | Baixo — saída idêntica para biblio/CODE; nenhum consumidor alterado |
| **Funcionalidades que NÃO quebram** | HTML biblio, Neo4j sync, CODE pattern, projetos sem CHAIN, modo Evidência |
| **Ganho** | Causation Oratória passa a renderizar ~32 arestas + evidência + grafo Neo4j |
