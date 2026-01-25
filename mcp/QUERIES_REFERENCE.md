# Referência de Queries Cypher para Pesquisadores

Este documento contém queries de referência para explorar seu grafo de pesquisa Synesis via Claude Desktop.

---

## 1. Visão Geral do Grafo

### 1.1 Estatísticas Gerais

```cypher
// Contagem de nós por tipo
CALL db.labels() YIELD label
CALL apoc.cypher.run('MATCH (n:`' + label + '`) RETURN count(n) as count', {})
YIELD value
RETURN label, value.count AS count
ORDER BY count DESC
```

### 1.2 Schema do Grafo

```cypher
// Visualizar relações entre tipos de nós
CALL db.schema.visualization()
```

---

## 2. Exploração de Conceitos

### 2.1 Conceitos mais Relevantes (PageRank)

```cypher
// Top 10 conceitos por PageRank
MATCH (c)
WHERE c.pagerank IS NOT NULL
RETURN labels(c)[0] AS tipo,
       c.name AS conceito,
       round(c.pagerank, 4) AS pagerank,
       c.mention_count AS mencoes,
       c.source_count AS fontes
ORDER BY c.pagerank DESC
LIMIT 10
```

### 2.2 Conceitos "Ponte" (Betweenness)

```cypher
// Conceitos com alto betweenness (conectam comunidades)
MATCH (c)
WHERE c.betweenness IS NOT NULL AND c.betweenness > 0
RETURN c.name AS conceito,
       round(c.betweenness, 2) AS betweenness,
       c.community AS comunidade,
       c.pagerank AS pagerank
ORDER BY c.betweenness DESC
LIMIT 10
```

### 2.3 Conceitos por Comunidade

```cypher
// Agrupar conceitos por comunidade
MATCH (c)
WHERE c.community IS NOT NULL
WITH c.community AS comunidade, collect(c.name) AS conceitos, count(c) AS total
RETURN comunidade, total, conceitos[0..5] AS exemplos
ORDER BY total DESC
```

---

## 3. Rastreabilidade (Evidências)

### 3.1 Citações de um Conceito

```cypher
// Todas as citações que mencionam um conceito
MATCH (c {name: 'NOME_DO_CONCEITO'})<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
RETURN s.title AS fonte,
       i.content AS citacao,
       i.source_file AS arquivo,
       i.line AS linha
ORDER BY s.title
```

### 3.2 Fontes por Conceito

```cypher
// Fontes que mencionam um conceito com contagem
MATCH (c {name: 'NOME_DO_CONCEITO'})<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
WITH s, count(i) AS citacoes
RETURN s.title AS fonte,
       s.id AS id,
       citacoes
ORDER BY citacoes DESC
```

### 3.3 Rastreabilidade Completa

```cypher
// Caminho completo: Conceito -> Item -> Source
MATCH path = (c {name: 'NOME_DO_CONCEITO'})<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
RETURN c.name AS conceito,
       i.content AS citacao,
       i.source_file AS arquivo,
       i.line AS linha,
       s.title AS fonte
LIMIT 20
```

---

## 4. Análise de Relações

### 4.1 Relações Diretas (RELATES_TO)

```cypher
// Relações diretas de um conceito
MATCH (c1 {name: 'NOME_DO_CONCEITO'})-[r:RELATES_TO]-(c2)
RETURN c1.name AS origem,
       type(r) AS relacao,
       r.type AS tipo,
       c2.name AS destino,
       r.description AS descricao
```

### 4.2 Conceitos Relacionados via Taxonomia

```cypher
// Conceitos que compartilham a mesma taxonomia
MATCH (c1 {name: 'NOME_DO_CONCEITO'})-[:GROUPED_BY]->(t:Topic)<-[:GROUPED_BY]-(c2)
WHERE c1 <> c2
RETURN t.name AS topic,
       collect(DISTINCT c2.name) AS conceitos_relacionados
```

### 4.3 Rede de Relações (2 níveis)

```cypher
// Rede de relações até 2 saltos
MATCH path = (c {name: 'NOME_DO_CONCEITO'})-[:RELATES_TO*1..2]-(related)
RETURN path
LIMIT 50
```

---

## 5. Análise de Taxonomias

### 5.1 Topics com mais Conceitos

```cypher
// Topics ordenados por número de conceitos
MATCH (t:Topic)<-[:GROUPED_BY]-(c)
WITH t, count(c) AS total
RETURN t.name AS topic,
       total,
       t.weighted_degree AS peso,
       t.aspect_diversity AS diversidade_aspectos
ORDER BY total DESC
```

### 5.2 Hierarquia de Taxonomia

```cypher
// Estrutura hierárquica Topic -> Concepts
MATCH (t:Topic)<-[:GROUPED_BY]-(c)
WITH t.name AS topic, collect(c.name) AS conceitos
RETURN topic, size(conceitos) AS total, conceitos[0..5] AS exemplos
ORDER BY total DESC
```

### 5.3 Co-ocorrência de Taxonomias

```cypher
// Topics que compartilham conceitos
MATCH (t1:Topic)<-[:GROUPED_BY]-(c)-[:QUALIFIED_BY]->(a:Aspect)
WITH t1, a, count(c) AS shared
RETURN t1.name AS topic,
       a.name AS aspect,
       shared AS conceitos_compartilhados
ORDER BY shared DESC
LIMIT 20
```

---

## 6. Análise de Fontes

### 6.1 Fontes mais Ricas

```cypher
// Fontes ordenadas por riqueza conceitual
MATCH (s:Source)
RETURN s.title AS fonte,
       s.id AS id,
       s.item_count AS citacoes,
       s.concept_count AS conceitos
ORDER BY s.concept_count DESC
LIMIT 10
```

### 6.2 Conceitos por Fonte

```cypher
// Conceitos mencionados em uma fonte específica
MATCH (s:Source {id: 'ID_DA_FONTE'})<-[:FROM_SOURCE]-(i:Item)-[:MENTIONS]->(c)
RETURN DISTINCT c.name AS conceito,
       count(i) AS mencoes,
       c.pagerank AS relevancia
ORDER BY mencoes DESC
```

### 6.3 Fontes que Cobrem um Topic

```cypher
// Fontes que mencionam conceitos de um topic
MATCH (t:Topic {name: 'NOME_DO_TOPIC'})<-[:GROUPED_BY]-(c)<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
WITH s, count(DISTINCT c) AS conceitos
RETURN s.title AS fonte,
       conceitos AS conceitos_do_topic
ORDER BY conceitos DESC
```

---

## 7. Comparações e Análises Cruzadas

### 7.1 Comparar Comunidades

```cypher
// Estatísticas por comunidade
MATCH (c)
WHERE c.community IS NOT NULL
WITH c.community AS com,
     count(c) AS total,
     avg(c.pagerank) AS avg_pr,
     avg(c.mention_count) AS avg_mentions
RETURN com AS comunidade,
       total AS conceitos,
       round(avg_pr, 4) AS pagerank_medio,
       round(avg_mentions, 1) AS mencoes_media
ORDER BY total DESC
```

### 7.2 Conceitos em Múltiplas Fontes

```cypher
// Conceitos que aparecem em muitas fontes (generalizáveis)
MATCH (c)
WHERE c.source_count > 5
RETURN c.name AS conceito,
       c.source_count AS fontes,
       c.mention_count AS mencoes,
       c.pagerank AS pagerank
ORDER BY c.source_count DESC
LIMIT 20
```

### 7.3 Conceitos Exclusivos de Uma Fonte

```cypher
// Conceitos que aparecem em apenas uma fonte (específicos)
MATCH (c)
WHERE c.source_count = 1
MATCH (c)<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
RETURN c.name AS conceito,
       s.title AS fonte_unica,
       c.mention_count AS mencoes
ORDER BY c.mention_count DESC
LIMIT 20
```

---

## 8. Queries Úteis para Pesquisa

### 8.1 Encontrar Gaps (Conceitos Isolados)

```cypher
// Conceitos com poucas relações
MATCH (c)
WHERE c.degree IS NOT NULL AND c.degree < 3
RETURN c.name AS conceito,
       c.degree AS relacoes,
       c.mention_count AS mencoes,
       c.source_count AS fontes
ORDER BY c.mention_count DESC
LIMIT 20
```

### 8.2 Triangulação de Evidências

```cypher
// Conceitos com evidências de múltiplas fontes
MATCH (c)<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
WITH c, collect(DISTINCT s.title) AS fontes
WHERE size(fontes) >= 3
RETURN c.name AS conceito,
       size(fontes) AS num_fontes,
       fontes[0..3] AS exemplos_fontes
ORDER BY size(fontes) DESC
```

### 8.3 Saturação Teórica

```cypher
// Verificar se novos conceitos estão surgindo por fonte (cronológico)
MATCH (s:Source)<-[:FROM_SOURCE]-(i:Item)-[:MENTIONS]->(c)
WITH s, count(DISTINCT c) AS conceitos_novos
RETURN s.id AS fonte,
       s.title AS titulo,
       conceitos_novos
ORDER BY s.id
```

---

## Notas de Uso

1. **Substitua `NOME_DO_CONCEITO`** pelo nome exato do conceito desejado
2. **Substitua `ID_DA_FONTE`** pelo ID da fonte no seu banco
3. **Labels dinâmicos:** O label do conceito depende do seu template (Factor, Ordem_2a, etc.)
4. **Métricas GDS:** Queries com `pagerank`, `betweenness`, `community` requerem que as métricas tenham sido calculadas

---

## Dicas para Claude Desktop

Ao perguntar ao Claude, seja específico:

**Bom:**
> "Mostre as citações originais que mencionam 'Acceptance', incluindo arquivo e linha"

**Melhor:**
> "Use read_neo4j_cypher para encontrar todas as citações que mencionam o conceito 'Acceptance', retornando a fonte, o texto da citação, o arquivo de origem e a linha"
