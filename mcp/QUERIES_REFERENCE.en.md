# Cypher Query Reference for Researchers

This document contains reference queries for exploring your Synesis research graph via Claude Desktop.

---

## 1. Graph Overview

### 1.1 General Statistics

```cypher
// Node count by type
CALL db.labels() YIELD label
CALL apoc.cypher.run('MATCH (n:`' + label + '`) RETURN count(n) as count', {})
YIELD value
RETURN label, value.count AS count
ORDER BY count DESC
```

### 1.2 Graph Schema

```cypher
// Visualize relationships between node types
CALL db.schema.visualization()
```

---

## 2. Concept Exploration

### 2.1 Most Relevant Concepts (PageRank)

```cypher
// Top 10 concepts by PageRank
MATCH (c)
WHERE c.pagerank IS NOT NULL
RETURN labels(c)[0] AS type,
       c.name AS concept,
       round(c.pagerank, 4) AS pagerank,
       c.mention_count AS mentions,
       c.source_count AS sources
ORDER BY c.pagerank DESC
LIMIT 10
```

### 2.2 "Bridge" Concepts (Betweenness)

```cypher
// Concepts with high betweenness (connect communities)
MATCH (c)
WHERE c.betweenness IS NOT NULL AND c.betweenness > 0
RETURN c.name AS concept,
       round(c.betweenness, 2) AS betweenness,
       c.community AS community,
       c.pagerank AS pagerank
ORDER BY c.betweenness DESC
LIMIT 10
```

### 2.3 Concepts by Community

```cypher
// Group concepts by community
MATCH (c)
WHERE c.community IS NOT NULL
WITH c.community AS community, collect(c.name) AS concepts, count(c) AS total
RETURN community, total, concepts[0..5] AS examples
ORDER BY total DESC
```

---

## 3. Traceability (Evidence)

### 3.1 Citations of a Concept

```cypher
// All citations that mention a concept
MATCH (c {name: 'CONCEPT_NAME'})<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
RETURN s.title AS source,
       i.content AS citation,
       i.source_file AS file,
       i.line AS line
ORDER BY s.title
```

### 3.2 Sources by Concept

```cypher
// Sources that mention a concept with count
MATCH (c {name: 'CONCEPT_NAME'})<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
WITH s, count(i) AS citations
RETURN s.title AS source,
       s.id AS id,
       citations
ORDER BY citations DESC
```

### 3.3 Full Traceability

```cypher
// Complete path: Concept -> Item -> Source
MATCH path = (c {name: 'CONCEPT_NAME'})<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
RETURN c.name AS concept,
       i.content AS citation,
       i.source_file AS file,
       i.line AS line,
       s.title AS source
LIMIT 20
```

---

## 4. Relationship Analysis

### 4.1 Direct Relationships (RELATES_TO)

```cypher
// Direct relationships of a concept
MATCH (c1 {name: 'CONCEPT_NAME'})-[r:RELATES_TO]-(c2)
RETURN c1.name AS origin,
       type(r) AS relationship,
       r.type AS type,
       c2.name AS target,
       r.description AS description
```

### 4.2 Concepts Related via Taxonomy

```cypher
// Concepts that share the same taxonomy
MATCH (c1 {name: 'CONCEPT_NAME'})-[:GROUPED_BY]->(t:Topic)<-[:GROUPED_BY]-(c2)
WHERE c1 <> c2
RETURN t.name AS topic,
       collect(DISTINCT c2.name) AS related_concepts
```

### 4.3 Relationship Network (2 levels)

```cypher
// Relationship network up to 2 hops
MATCH path = (c {name: 'CONCEPT_NAME'})-[:RELATES_TO*1..2]-(related)
RETURN path
LIMIT 50
```

---

## 5. Taxonomy Analysis

### 5.1 Topics with Most Concepts

```cypher
// Topics ordered by number of concepts
MATCH (t:Topic)<-[:GROUPED_BY]-(c)
WITH t, count(c) AS total
RETURN t.name AS topic,
       total,
       t.weighted_degree AS weight,
       t.aspect_diversity AS aspect_diversity
ORDER BY total DESC
```

### 5.2 Taxonomy Hierarchy

```cypher
// Hierarchical structure Topic -> Concepts
MATCH (t:Topic)<-[:GROUPED_BY]-(c)
WITH t.name AS topic, collect(c.name) AS concepts
RETURN topic, size(concepts) AS total, concepts[0..5] AS examples
ORDER BY total DESC
```

### 5.3 Taxonomy Co-occurrence

```cypher
// Topics that share concepts
MATCH (t1:Topic)<-[:GROUPED_BY]-(c)-[:QUALIFIED_BY]->(a:Aspect)
WITH t1, a, count(c) AS shared
RETURN t1.name AS topic,
       a.name AS aspect,
       shared AS shared_concepts
ORDER BY shared DESC
LIMIT 20
```

---

## 6. Source Analysis

### 6.1 Richest Sources

```cypher
// Sources ordered by conceptual richness
MATCH (s:Source)
RETURN s.title AS source,
       s.id AS id,
       s.item_count AS citations,
       s.concept_count AS concepts
ORDER BY s.concept_count DESC
LIMIT 10
```

### 6.2 Concepts by Source

```cypher
// Concepts mentioned in a specific source
MATCH (s:Source {id: 'SOURCE_ID'})<-[:FROM_SOURCE]-(i:Item)-[:MENTIONS]->(c)
RETURN DISTINCT c.name AS concept,
       count(i) AS mentions,
       c.pagerank AS relevance
ORDER BY mentions DESC
```

### 6.3 Sources Covering a Topic

```cypher
// Sources that mention concepts from a topic
MATCH (t:Topic {name: 'TOPIC_NAME'})<-[:GROUPED_BY]-(c)<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
WITH s, count(DISTINCT c) AS concepts
RETURN s.title AS source,
       concepts AS topic_concepts
ORDER BY concepts DESC
```

---

## 7. Comparisons and Cross-Analysis

### 7.1 Compare Communities

```cypher
// Statistics by community
MATCH (c)
WHERE c.community IS NOT NULL
WITH c.community AS com,
     count(c) AS total,
     avg(c.pagerank) AS avg_pr,
     avg(c.mention_count) AS avg_mentions
RETURN com AS community,
       total AS concepts,
       round(avg_pr, 4) AS average_pagerank,
       round(avg_mentions, 1) AS average_mentions
ORDER BY total DESC
```

### 7.2 Concepts in Multiple Sources

```cypher
// Concepts that appear in many sources (generalizable)
MATCH (c)
WHERE c.source_count > 5
RETURN c.name AS concept,
       c.source_count AS sources,
       c.mention_count AS mentions,
       c.pagerank AS pagerank
ORDER BY c.source_count DESC
LIMIT 20
```

### 7.3 Concepts Exclusive to One Source

```cypher
// Concepts that appear in only one source (specific)
MATCH (c)
WHERE c.source_count = 1
MATCH (c)<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
RETURN c.name AS concept,
       s.title AS single_source,
       c.mention_count AS mentions
ORDER BY c.mention_count DESC
LIMIT 20
```

---

## 8. Useful Queries for Research

### 8.1 Finding Gaps (Isolated Concepts)

```cypher
// Concepts with few relationships
MATCH (c)
WHERE c.degree IS NOT NULL AND c.degree < 3
RETURN c.name AS concept,
       c.degree AS relationships,
       c.mention_count AS mentions,
       c.source_count AS sources
ORDER BY c.mention_count DESC
LIMIT 20
```

### 8.2 Evidence Triangulation

```cypher
// Concepts with evidence from multiple sources
MATCH (c)<-[:MENTIONS]-(i:Item)-[:FROM_SOURCE]->(s:Source)
WITH c, collect(DISTINCT s.title) AS sources
WHERE size(sources) >= 3
RETURN c.name AS concept,
       size(sources) AS num_sources,
       sources[0..3] AS example_sources
ORDER BY size(sources) DESC
```

### 8.3 Theoretical Saturation

```cypher
// Check if new concepts are emerging by source (chronological)
MATCH (s:Source)<-[:FROM_SOURCE]-(i:Item)-[:MENTIONS]->(c)
WITH s, count(DISTINCT c) AS new_concepts
RETURN s.id AS source,
       s.title AS title,
       new_concepts
ORDER BY s.id
```

---

## Usage Notes

1. **Replace `CONCEPT_NAME`** with the exact name of the desired concept
2. **Replace `SOURCE_ID`** with the source ID in your database
3. **Dynamic labels:** The concept label depends on your template (Factor, Ordem_2a, etc.)
4. **GDS metrics:** Queries with `pagerank`, `betweenness`, `community` require that metrics have been calculated

---

## Tips for Claude Desktop

When asking Claude, be specific:

**Good:**
> "Show the original citations that mention 'Acceptance', including file and line"

**Better:**
> "Use read_neo4j_cypher to find all citations that mention the concept 'Acceptance', returning the source, citation text, source file, and line"
