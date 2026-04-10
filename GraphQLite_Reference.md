Descrição do projeto
GraphQLite Python
Python bindings for GraphQLite, a SQLite extension that adds graph database capabilities using Cypher.

Installation
pip install graphqlite
Quick Start
High-Level Graph API (Recommended)
The Graph class provides an ergonomic interface for common graph operations:

from graphqlite import Graph

# Create a graph (in-memory or file-based)
g = Graph(":memory:")

# Add nodes
g.upsert_node("alice", {"name": "Alice", "age": 30}, label="Person")
g.upsert_node("bob", {"name": "Bob", "age": 25}, label="Person")

# Add edges
g.upsert_edge("alice", "bob", {"since": 2020}, rel_type="KNOWS")

# Query
print(g.stats())              # {'nodes': 2, 'edges': 1}
print(g.get_neighbors("alice"))  # [{'id': 'bob', ...}]
print(g.node_degree("alice"))    # 1

# Graph algorithms
ranks = g.pagerank()
communities = g.community_detection()

# Raw Cypher when needed
results = g.query("MATCH (a)-[:KNOWS]->(b) RETURN a.name, b.name")
Low-Level Cypher API
For complex queries or when you need full control:

from graphqlite import connect

db = connect("graph.db")

db.cypher("CREATE (a:Person {name: 'Alice', age: 30})")
db.cypher("CREATE (b:Person {name: 'Bob', age: 25})")
db.cypher("""
    MATCH (a:Person {name: 'Alice'}), (b:Person {name: 'Bob'})
    CREATE (a)-[:KNOWS]->(b)
""")

results = db.cypher("MATCH (a:Person)-[:KNOWS]->(b) RETURN a.name, b.name")
for row in results:
    print(f"{row['a.name']} knows {row['b.name']}")
API Reference
Graph Class
from graphqlite import Graph, graph

# Constructor
g = Graph(db_path=":memory:", namespace="default", extension_path=None)

# Or use the factory function
g = graph(":memory:")
Node Operations
Method	Description
upsert_node(node_id, props, label="Entity")	Create or update a node
get_node(node_id)	Get node by ID
has_node(node_id)	Check if node exists
delete_node(node_id)	Delete node and its edges
get_all_nodes(label=None)	Get all nodes, optionally by label
Edge Operations
Method	Description
upsert_edge(source, target, props, rel_type="RELATED")	Create edge between nodes
get_edge(source, target)	Get edge properties
has_edge(source, target)	Check if edge exists
delete_edge(source, target)	Delete edge
get_all_edges()	Get all edges
Graph Queries
Method	Description
node_degree(node_id)	Count edges connected to node
get_neighbors(node_id)	Get adjacent nodes
get_node_edges(node_id)	Get all edges for a node
stats()	Get node/edge counts
query(cypher)	Execute raw Cypher query
Graph Algorithms
Centrality

Method	Description
pagerank(damping=0.85, iterations=20)	PageRank importance scores
degree_centrality()	In/out/total degree for each node
betweenness_centrality()	Betweenness centrality scores
closeness_centrality()	Closeness centrality scores
eigenvector_centrality(iterations=100)	Eigenvector centrality scores
Community Detection

Method	Description
community_detection(iterations=10)	Label propagation communities
louvain(resolution=1.0)	Louvain modularity optimization
leiden_communities(resolution, seed)	Leiden algorithm (requires graspologic)
Connected Components

Method	Description
weakly_connected_components()	Weakly connected components
strongly_connected_components()	Strongly connected components
Path Finding

Method	Description
shortest_path(source, target, weight)	Dijkstra's shortest path
astar(source, target, lat, lon)	A* with optional heuristic
all_pairs_shortest_path()	All-pairs shortest paths (Floyd-Warshall)
Traversal

Method	Description
bfs(start, max_depth=-1)	Breadth-first search
dfs(start, max_depth=-1)	Depth-first search
Similarity

Method	Description
node_similarity(n1, n2, threshold, top_k)	Jaccard similarity
knn(node, k=10)	K-nearest neighbors
triangle_count()	Triangle counts and clustering coefficients
Export

Method	Description
to_rustworkx()	Export to rustworkx PyDiGraph (requires rustworkx)
Batch Operations
# Batch insert nodes (upsert semantics)
g.upsert_nodes_batch([
    ("n1", {"name": "Alice"}, "Person"),
    ("n2", {"name": "Bob"}, "Person"),
])

# Batch insert edges (upsert semantics)
g.upsert_edges_batch([
    ("n1", "n2", {"weight": 1.0}, "KNOWS"),
])
Bulk Insert (High Performance)
For maximum throughput when building graphs from external data, use the bulk insert methods. These bypass Cypher parsing entirely and use direct SQL, achieving 100-500x faster insert rates.

# Bulk insert nodes - returns dict mapping external_id -> internal_rowid
id_map = g.insert_nodes_bulk([
    ("alice", {"name": "Alice", "age": 30}, "Person"),
    ("bob", {"name": "Bob", "age": 25}, "Person"),
    ("charlie", {"name": "Charlie"}, "Person"),
])

# Bulk insert edges using the ID map - no MATCH queries needed!
edges_inserted = g.insert_edges_bulk([
    ("alice", "bob", {"since": 2020}, "KNOWS"),
    ("bob", "charlie", {"since": 2021}, "KNOWS"),
], id_map)

# Or use the convenience method for both
result = g.insert_graph_bulk(nodes=nodes, edges=edges)
print(f"Inserted {result.nodes_inserted} nodes, {result.edges_inserted} edges")

# Resolve existing node IDs (for edges to pre-existing nodes)
resolved = g.resolve_node_ids(["alice", "bob"])
Method	Description
insert_nodes_bulk(nodes)	Insert nodes, returns ID mapping dict
insert_edges_bulk(edges, id_map=None)	Insert edges using ID map
insert_graph_bulk(nodes, edges)	Insert both, returns BulkInsertResult
resolve_node_ids(ids)	Resolve external IDs to internal rowids
Connection Class
from graphqlite import connect, wrap

# Open new connection
db = connect("graph.db")
db = connect(":memory:")

# Wrap existing sqlite3 connection
import sqlite3
conn = sqlite3.connect("graph.db")
db = wrap(conn)
Methods
Method	Description
cypher(query)	Execute Cypher query, return results
execute(sql)	Execute raw SQL
close()	Close connection
CypherResult
Results from cypher() calls:

results = db.cypher("MATCH (n) RETURN n.name")

len(results)           # Number of rows
results[0]             # First row as dict
results.columns        # Column names
results.to_list()      # All rows as list

for row in results:
    print(row["n.name"])
Utility Functions
from graphqlite import escape_string, sanitize_rel_type, CYPHER_RESERVED

# Escape strings for Cypher queries
safe = escape_string("It's a test")  # "It\\'s a test"

# Sanitize relationship types
rel = sanitize_rel_type("has-items")  # "has_items"
rel = sanitize_rel_type("CREATE")     # "REL_CREATE" (reserved word)

# Set of Cypher reserved keywords
if "MATCH" in CYPHER_RESERVED:
    print("MATCH is reserved")
Extension Path
The extension is located automatically. To specify a custom path:

db = connect("graph.db", extension_path="/path/to/graphqlite.dylib")
Or set the GRAPHQLITE_EXTENSION_PATH environment variable.

Troubleshooting
See FAQ.md for common issues and solutions.

License
MIT

Cypher Support
GraphQLite implements a substantial subset of the Cypher query language.

Overview
Cypher is a declarative graph query language originally developed by Neo4j. GraphQLite supports the core features needed for most graph operations.

Quick Reference
Feature	Support
Node patterns	✅ Full
Relationship patterns	✅ Full
Variable-length paths	✅ Full
shortestPath/allShortestPaths	✅ Full
Parameterized queries	✅ Full
MATCH/OPTIONAL MATCH	✅ Full
CREATE/MERGE	✅ Full
SET/REMOVE/DELETE	✅ Full
WITH/UNWIND/FOREACH	✅ Full
LOAD CSV	✅ Full
UNION/UNION ALL	✅ Full
RETURN with modifiers	✅ Full
Aggregation functions	✅ Full
CASE expressions	✅ Full
List comprehensions	✅ Full
Pattern comprehensions	✅ Full
Map projections	✅ Full
Multi-graph (FROM clause)	✅ Full
Graph algorithms	✅ 15+ built-in
CALL procedures	❌ Not supported
CREATE INDEX/CONSTRAINT	❌ Use SQLite
Pattern Syntax
Nodes
(n)                           -- Any node
(n:Person)                    -- Node with label
(n:Person {name: 'Alice'})    -- Node with properties
(:Person)                     -- Anonymous node with label
Relationships
-[r]->                        -- Outgoing relationship
<-[r]-                        -- Incoming relationship
-[r]-                         -- Either direction
-[:KNOWS]->                   -- Relationship with type
-[r:KNOWS {since: 2020}]->    -- With properties
Variable-Length Paths
-[*]->                        -- Any length
-[*2]->                       -- Exactly 2 hops
-[*1..3]->                    -- 1 to 3 hops
-[:KNOWS*1..5]->              -- Typed, 1 to 5 hops
Clauses
See Clauses Reference for detailed documentation.

Functions
See Functions Reference for the complete function list.

Operators
See Operators Reference for comparison and logical operators.

Implementation Notes
GraphQLite implements standard Cypher with some differences from full implementations:

No CALL procedures - Use built-in graph algorithm functions instead (e.g., RETURN pageRank())
No CREATE INDEX/CONSTRAINT - Use SQLite's indexing and constraint mechanisms directly
EXPLAIN supported - Returns the generated SQL for debugging instead of a query plan
Multi-graph support - Use the FROM clause to query specific graphs with GraphManager
Substring indexing - Uses 0-based indexing (Cypher standard), automatically converted for SQLite

Cypher Clauses
Reading Clauses
MATCH
Find patterns in the graph:

MATCH (n:Person) RETURN n
MATCH (a)-[:KNOWS]->(b) RETURN a, b
MATCH (n:Person {name: 'Alice'}) RETURN n
Shortest Path Patterns
Find shortest paths between nodes:

// Find a single shortest path
MATCH p = shortestPath((a:Person {name: 'Alice'})-[*]-(b:Person {name: 'Bob'}))
RETURN p, length(p)

// Find all shortest paths (all paths with minimum length)
MATCH p = allShortestPaths((a:Person)-[*]-(b:Person))
WHERE a.name = 'Alice' AND b.name = 'Bob'
RETURN p

// With relationship type filter
MATCH p = shortestPath((a)-[:KNOWS*]->(b))
RETURN nodes(p), relationships(p)

// With length constraints
MATCH p = shortestPath((a)-[*..10]->(b))
RETURN p
OPTIONAL MATCH
Like MATCH, but returns NULL for non-matches (left join semantics):

MATCH (p:Person)
OPTIONAL MATCH (p)-[:MANAGES]->(e)
RETURN p.name, e.name
WHERE
Filter results:

MATCH (n:Person)
WHERE n.age > 21 AND n.city = 'NYC'
RETURN n
Writing Clauses
CREATE
Create nodes and relationships:

CREATE (n:Person {name: 'Alice', age: 30})
CREATE (a)-[:KNOWS {since: 2020}]->(b)
MERGE
Create if not exists, match if exists:

MERGE (n:Person {name: 'Alice'})
ON CREATE SET n.created = timestamp()
ON MATCH SET n.updated = timestamp()
SET
Update properties:

MATCH (n:Person {name: 'Alice'})
SET n.age = 31, n.city = 'LA'
Add labels:

MATCH (n:Person {name: 'Alice'})
SET n:Employee
REMOVE
Remove properties:

MATCH (n:Person {name: 'Alice'})
REMOVE n.temporary_field
Remove labels:

MATCH (n:Person:Employee {name: 'Alice'})
REMOVE n:Employee
DELETE
Delete nodes (must have no relationships):

MATCH (n:Person {name: 'Alice'})
DELETE n
DETACH DELETE
Delete nodes and all their relationships:

MATCH (n:Person {name: 'Alice'})
DETACH DELETE n
Composing Clauses
WITH
Chain query parts, aggregation, and filtering:

MATCH (p:Person)-[:WORKS_AT]->(c:Company)
WITH c, count(p) as employee_count
WHERE employee_count > 10
RETURN c.name, employee_count
UNWIND
Expand a list into rows:

UNWIND [1, 2, 3] AS x
RETURN x

UNWIND $names AS name
CREATE (n:Person {name: name})
FOREACH
Iterate and perform updates:

MATCH p = (start)-[*]->(end)
FOREACH (n IN nodes(p) | SET n.visited = true)
LOAD CSV
Import data from CSV files:

// With headers (access columns by name)
LOAD CSV WITH HEADERS FROM 'file:///people.csv' AS row
CREATE (n:Person {name: row.name, age: toInteger(row.age)})

// Without headers (access columns by index)
LOAD CSV FROM 'file:///data.csv' AS row
CREATE (n:Item {id: row[0], value: row[1]})

// Custom field terminator
LOAD CSV WITH HEADERS FROM 'file:///data.tsv' AS row FIELDTERMINATOR '\t'
CREATE (n:Record {field1: row.col1})
Note: File paths are relative to the current working directory. Use file:/// prefix for local files.

Multi-Graph Queries
FROM Clause
Query specific graphs when using GraphManager (multi-graph support):

// Query a specific graph
MATCH (n:Person) FROM social
RETURN n.name

// Combined with other clauses
MATCH (p:Person) FROM social
WHERE p.age > 21
RETURN p.name, graph(p) AS source_graph
The graph() function returns which graph a node came from.

Combining Results
UNION
Combine results from multiple queries, removing duplicates:

MATCH (n:Person) WHERE n.city = 'NYC' RETURN n.name
UNION
MATCH (n:Person) WHERE n.age > 50 RETURN n.name
UNION ALL
Combine results keeping all rows (including duplicates):

MATCH (a:Person)-[:KNOWS]->(b) RETURN b.name AS connection
UNION ALL
MATCH (a:Person)-[:WORKS_WITH]->(b) RETURN b.name AS connection
Return Clause
RETURN
Specify what to return:

MATCH (n:Person) RETURN n
MATCH (n:Person) RETURN n.name, n.age
MATCH (n:Person) RETURN n.name AS name
DISTINCT
Remove duplicates:

MATCH (n:Person)-[:KNOWS]->(m)
RETURN DISTINCT m.city
ORDER BY
Sort results:

MATCH (n:Person)
RETURN n.name, n.age
ORDER BY n.age DESC, n.name ASC
SKIP and LIMIT
Pagination:

MATCH (n:Person)
RETURN n
ORDER BY n.name
SKIP 10
LIMIT 5
Aggregation
Use aggregate functions in RETURN or WITH:

MATCH (p:Person)-[:WORKS_AT]->(c:Company)
RETURN c.name, count(p), avg(p.salary), collect(p.name)
See Functions Reference for all aggregate functions.

Cypher Functions
String Functions
Function	Description	Example
toLower(s)	Convert to lowercase	toLower('Hello') → 'hello'
toUpper(s)	Convert to uppercase	toUpper('Hello') → 'HELLO'
trim(s)	Remove leading/trailing whitespace	trim(' hi ') → 'hi'
ltrim(s)	Remove leading whitespace	ltrim(' hi') → 'hi'
rtrim(s)	Remove trailing whitespace	rtrim('hi ') → 'hi'
replace(s, from, to)	Replace occurrences	replace('hello', 'l', 'x') → 'hexxo'
substring(s, start, len)	Extract substring	substring('hello', 1, 3) → 'ell'
left(s, n)	First n characters	left('hello', 2) → 'he'
right(s, n)	Last n characters	right('hello', 2) → 'lo'
split(s, delim)	Split into list	split('a,b,c', ',') → ['a','b','c']
reverse(s)	Reverse string	reverse('hello') → 'olleh'
length(s)	String length	length('hello') → 5
size(s)	String length (alias)	size('hello') → 5
toString(x)	Convert to string	toString(123) → '123'
String Predicates
Function	Description	Example
startsWith(s, prefix)	Check prefix	startsWith('hello', 'he') → true
endsWith(s, suffix)	Check suffix	endsWith('hello', 'lo') → true
contains(s, sub)	Check substring	contains('hello', 'ell') → true
Math Functions
Function	Description	Example
abs(n)	Absolute value	abs(-5) → 5
ceil(n)	Round up	ceil(2.3) → 3
floor(n)	Round down	floor(2.7) → 2
round(n)	Round to nearest	round(2.5) → 3
sign(n)	Sign (-1, 0, 1)	sign(-5) → -1
sqrt(n)	Square root	sqrt(16) → 4
log(n)	Natural logarithm	log(e()) → 1
log10(n)	Base-10 logarithm	log10(100) → 2
exp(n)	e^n	exp(1) → 2.718...
rand()	Random 0-1	rand() → 0.42...
random()	Random 0-1 (alias)	random() → 0.42...
pi()	π constant	pi() → 3.14159...
e()	e constant	e() → 2.71828...
Trigonometric Functions
Function	Description
sin(n)	Sine
cos(n)	Cosine
tan(n)	Tangent
asin(n)	Arc sine
acos(n)	Arc cosine
atan(n)	Arc tangent
List Functions
Function	Description	Example
head(list)	First element	head([1,2,3]) → 1
tail(list)	All but first	tail([1,2,3]) → [2,3]
last(list)	Last element	last([1,2,3]) → 3
size(list)	Length	size([1,2,3]) → 3
range(start, end)	Create range	range(1, 5) → [1,2,3,4,5]
reverse(list)	Reverse list	reverse([1,2,3]) → [3,2,1]
keys(map)	Get map keys	keys({a:1, b:2}) → ['a','b']
Aggregate Functions
Function	Description	Example
count(x)	Count items	count(n), count(*)
sum(x)	Sum values	sum(n.amount)
avg(x)	Average	avg(n.score)
min(x)	Minimum	min(n.age)
max(x)	Maximum	max(n.age)
collect(x)	Collect into list	collect(n.name)
Entity Functions
Function	Description	Example
id(node)	Get node/edge ID	id(n)
labels(node)	Get node labels	labels(n) → ['Person']
type(rel)	Get relationship type	type(r) → 'KNOWS'
properties(x)	Get all properties	properties(n)
startNode(rel)	Start node of relationship	startNode(r)
endNode(rel)	End node of relationship	endNode(r)
Path Functions
Function	Description	Example
nodes(path)	Get all nodes in path	nodes(p)
relationships(path)	Get all relationships	relationships(p)
rels(path)	Get all relationships (alias)	rels(p)
length(path)	Path length (edges)	length(p)
Type Conversion
Function	Description	Example
toInteger(x)	Convert to integer	toInteger('42') → 42
toFloat(x)	Convert to float	toFloat('3.14') → 3.14
toBoolean(x)	Convert to boolean	toBoolean('true') → true
coalesce(x, y, ...)	First non-null value	coalesce(n.name, 'Unknown')
Temporal Functions
Function	Description	Example
date()	Current date	date() → '2025-01-15'
datetime()	Current datetime	datetime()
time()	Current time	time()
timestamp()	Unix timestamp (ms)	timestamp()
localdatetime()	Local datetime	localdatetime()
randomUUID()	Generate random UUID	randomUUID() → '550e8400-e29b-...'
Predicate Functions
Function	Description	Example
exists(pattern)	Pattern exists	EXISTS { (n)-[:KNOWS]->() }
exists(prop)	Property exists	exists(n.email)
all(x IN list WHERE pred)	All match	all(x IN [1,2,3] WHERE x > 0)
any(x IN list WHERE pred)	Any match	any(x IN [1,2,3] WHERE x > 2)
none(x IN list WHERE pred)	None match	none(x IN [1,2,3] WHERE x < 0)
single(x IN list WHERE pred)	Exactly one	single(x IN [1,2,3] WHERE x = 2)
Reduce
Function	Description	Example
reduce(acc = init, x IN list | expr)	Fold/reduce	reduce(s = 0, x IN [1,2,3] | s + x) → 6
CASE Expressions
Searched CASE
Evaluates conditions in order and returns the first matching result:

RETURN CASE
    WHEN n.age < 18 THEN 'minor'
    WHEN n.age < 65 THEN 'adult'
    ELSE 'senior'
END AS category
Simple CASE
Compares an expression against values:

RETURN CASE n.status
    WHEN 'A' THEN 'Active'
    WHEN 'I' THEN 'Inactive'
    WHEN 'P' THEN 'Pending'
    ELSE 'Unknown'
END AS status_name
Comprehensions
List Comprehension
Create lists by transforming or filtering:

// Transform each element
RETURN [x IN range(1, 5) | x * 2]
// → [2, 4, 6, 8, 10]

// Filter elements
RETURN [x IN range(1, 10) WHERE x % 2 = 0]
// → [2, 4, 6, 8, 10]

// Filter and transform
RETURN [x IN range(1, 10) WHERE x % 2 = 0 | x * x]
// → [4, 16, 36, 64, 100]
Pattern Comprehension
Extract data from pattern matches within an expression:

// Collect names of friends
MATCH (p:Person)
RETURN p.name, [(p)-[:KNOWS]->(friend) | friend.name] AS friends

// With filtering
RETURN [(p)-[:KNOWS]->(f:Person) WHERE f.age > 21 | f.name] AS adult_friends
Map Projection
Create maps by selecting properties from nodes:

// Select specific properties
MATCH (n:Person)
RETURN n {.name, .age}
// → {name: "Alice", age: 30}

// Include computed values
MATCH (n:Person)
RETURN n {.name, status: 'active', upperName: toUpper(n.name)}

Cypher Operators
Comparison Operators
Operator	Description	Example
=	Equal	n.age = 30
<>	Not equal	n.status <> 'deleted'
<	Less than	n.age < 18
>	Greater than	n.age > 65
<=	Less than or equal	n.score <= 100
>=	Greater than or equal	n.score >= 0
Boolean Operators
Operator	Description	Example
AND	Logical and	n.age > 18 AND n.active = true
OR	Logical or	n.role = 'admin' OR n.role = 'mod'
NOT	Logical not	NOT n.deleted
XOR	Exclusive or	a.flag XOR b.flag
Null Operators
Operator	Description	Example
IS NULL	Check for null	n.email IS NULL
IS NOT NULL	Check for non-null	n.email IS NOT NULL
String Operators
Operator	Description	Example
STARTS WITH	Prefix match	n.name STARTS WITH 'A'
ENDS WITH	Suffix match	n.email ENDS WITH '.com'
CONTAINS	Substring match	n.bio CONTAINS 'developer'
=~	Regex match	n.email =~ '.*@gmail\\.com'
List Operators
Operator	Description	Example
IN	List membership	n.status IN ['active', 'pending']
+	List concatenation	[1, 2] + [3, 4] → [1, 2, 3, 4]
[index]	Index access	list[0] (first element)
Arithmetic Operators
Operator	Description	Example
+	Addition	n.price + tax
-	Subtraction	n.total - discount
*	Multiplication	n.quantity * n.price
/	Division	n.total / n.count
%	Modulo	n.id % 10
String Concatenation
Operator	Description	Example
+	Concatenate strings	n.first + ' ' + n.last
Property Access
Operator	Description	Example
.	Property access	n.name
Operator Precedence
From highest to lowest:

. [] - Property/index access
* / % - Multiplication, division, modulo
+ - - Addition, subtraction
= <> < > <= >= - Comparison
IS NULL IS NOT NULL
IN STARTS WITH ENDS WITH CONTAINS =~
NOT
AND
XOR
OR
Use parentheses to override precedence:

WHERE (n.age > 18 OR n.verified) AND n.active

Graph Algorithms
GraphQLite includes 15+ built-in graph algorithms.

Centrality Algorithms
PageRank
Measures node importance based on incoming links from important nodes.

RETURN pageRank()
RETURN pageRank(0.85, 20)  -- damping, iterations
Returns: [{"node_id": int, "user_id": string, "score": float}, ...]

Parameters:

damping (default: 0.85) - Probability of following a link
iterations (default: 20) - Number of iterations
Degree Centrality
Counts incoming and outgoing connections.

RETURN degreeCentrality()
Returns: [{"node_id": int, "user_id": string, "in_degree": int, "out_degree": int, "degree": int}, ...]

Betweenness Centrality
Measures how often a node lies on shortest paths between other nodes.

RETURN betweennessCentrality()
Returns: [{"node_id": int, "user_id": string, "score": float}, ...]

Closeness Centrality
Measures average distance to all other nodes.

RETURN closenessCentrality()
Returns: [{"node_id": int, "user_id": string, "score": float}, ...]

Eigenvector Centrality
Measures influence based on connections to high-scoring nodes.

RETURN eigenvectorCentrality()
RETURN eigenvectorCentrality(100)  -- max iterations
Returns: [{"node_id": int, "user_id": string, "score": float}, ...]

Community Detection
Label Propagation
Detects communities by propagating labels through the network.

RETURN labelPropagation()
RETURN labelPropagation(10)  -- max iterations
RETURN communities()         -- alias
Returns: [{"node_id": int, "user_id": string, "community": int}, ...]

Louvain
Hierarchical community detection optimizing modularity.

RETURN louvain()
RETURN louvain(1.0)  -- resolution parameter
Returns: [{"node_id": int, "user_id": string, "community": int}, ...]

Connected Components
Weakly Connected Components (WCC)
Groups nodes reachable by ignoring edge direction.

RETURN wcc()
Returns: [{"node_id": int, "user_id": string, "component": int}, ...]

Strongly Connected Components (SCC)
Groups nodes where every node can reach every other node following edge direction.

RETURN scc()
Returns: [{"node_id": int, "user_id": string, "component": int}, ...]

Path Finding
Dijkstra (Shortest Path)
Finds shortest path between two nodes.

RETURN dijkstra('source_id', 'target_id')
Returns: {"found": bool, "distance": int, "path": [node_ids]}

The found field indicates whether a path exists. When found is false, distance is null and path is empty.

A* Search
Shortest path with heuristic. Can use geographic coordinates for distance estimation or fall back to uniform heuristic.

RETURN astar('source_id', 'target_id')
RETURN astar('source_id', 'target_id', 'lat_prop', 'lon_prop')
When lat_prop and lon_prop are provided, A* uses haversine distance as the heuristic. Without these properties, it behaves similarly to Dijkstra but may explore fewer nodes.

Returns: {"found": bool, "distance": float, "path": [node_ids], "nodes_explored": int}

All-Pairs Shortest Paths (APSP)
Computes shortest distances between all node pairs.

RETURN apsp()
Returns: [{"source": string, "target": string, "distance": int}, ...]

Note: O(n²) space and time complexity. Use with caution on large graphs.

Traversal
Breadth-First Search (BFS)
Explores nodes level by level from a starting point.

RETURN bfs('start_id')
RETURN bfs('start_id', 3)  -- max depth
Returns: [{"node_id": int, "user_id": string, "depth": int, "order": int}, ...]

The order field indicates the traversal order (0 = starting node, then incrementing).

Depth-First Search (DFS)
Explores as far as possible along each branch.

RETURN dfs('start_id')
RETURN dfs('start_id', 5)  -- max depth
Returns: [{"node_id": int, "user_id": string, "depth": int, "order": int}, ...]

Similarity
Node Similarity (Jaccard)
Computes Jaccard similarity between node neighborhoods.

RETURN nodeSimilarity()
Returns: [{"node1": int, "node2": int, "similarity": float}, ...]

K-Nearest Neighbors (KNN)
Finds k most similar nodes to a given node based on Jaccard similarity of neighborhoods.

RETURN knn('node_id', 10)  -- node, k
Returns: [{"neighbor": string, "similarity": float, "rank": int}, ...]

Results are ordered by similarity (highest first), with rank starting at 1.

Triangle Count
Counts triangles and computes clustering coefficient.

RETURN triangleCount()
Returns: [{"node_id": int, "user_id": string, "triangles": int, "clustering_coefficient": float}, ...]

Using Results in SQL
Extract algorithm results using SQLite JSON functions:

SELECT
    json_extract(value, '$.node_id') as id,
    json_extract(value, '$.score') as score
FROM json_each(cypher('RETURN pageRank()'))
ORDER BY score DESC
LIMIT 10;

Python API Reference
Installation
pip install graphqlite
Module Functions
graphqlite.connect()
Create a connection to a SQLite database with GraphQLite loaded.

from graphqlite import connect

conn = connect(":memory:")
conn = connect("graph.db")
conn = connect("graph.db", extension_path="/path/to/graphqlite.dylib")
Parameters:

database (str) - Database path or :memory:
extension_path (str, optional) - Path to extension file
Returns: Connection

graphqlite.load()
Load GraphQLite into an existing sqlite3 connection.

import sqlite3
import graphqlite

conn = sqlite3.connect(":memory:")
graphqlite.load(conn)
Parameters:

conn - sqlite3.Connection or apsw.Connection
entry_point (str, optional) - Extension entry point
graphqlite.loadable_path()
Get the path to the loadable extension.

path = graphqlite.loadable_path()
Returns: str

graphqlite.wrap()
Wrap an existing sqlite3 connection with GraphQLite support.

import sqlite3
import graphqlite

conn = sqlite3.connect(":memory:")
wrapped = graphqlite.wrap(conn)
results = wrapped.cypher("RETURN 1 AS x")
Parameters:

conn - sqlite3.Connection object
extension_path (str, optional) - Path to extension file
Returns: Connection

graphqlite.graph()
Factory function to create a Graph instance.

from graphqlite import graph

g = graph(":memory:")
g = graph("graph.db", namespace="myapp")
Parameters:

db_path (str) - Database path or :memory:
namespace (str, optional) - Graph namespace (default: "default")
extension_path (str, optional) - Path to extension file
Returns: Graph

CypherResult Class
Result container returned by cypher() queries.

results = conn.cypher("MATCH (n:Person) RETURN n.name, n.age")

# Length
print(len(results))  # Number of rows

# Indexing
first_row = results[0]  # Get first row as dict

# Iteration
for row in results:
    print(row["n.name"])

# Column names
print(results.columns)  # ["n.name", "n.age"]

# Convert to list
all_rows = results.to_list()  # List of dicts
Properties:

columns - List of column names
Methods:

to_list() - Return all rows as a list of dictionaries
Connection Class
Connection.cypher()
Execute a Cypher query with optional parameters.

conn.cypher("CREATE (n:Person {name: 'Alice'})")
results = conn.cypher("MATCH (n) RETURN n.name")
for row in results:
    print(row["n.name"])

# With parameters
results = conn.cypher(
    "MATCH (n:Person {name: $name}) RETURN n",
    {"name": "Alice"}
)
The query parameter is the Cypher query string. The optional params parameter accepts a dictionary that will be converted to JSON for parameter binding.

Returns: CypherResult object (iterable, supports indexing and len())

Connection.execute()
Execute raw SQL.

conn.execute("SELECT * FROM nodes")
Graph Class
High-level API for graph operations.

Constructor
from graphqlite import Graph

g = Graph(":memory:")
g = Graph("graph.db")
Node Operations
upsert_node()
Create or update a node.

g.upsert_node("alice", {"name": "Alice", "age": 30}, label="Person")
Parameters:

node_id (str) - Unique node identifier
properties (dict) - Node properties
label (str, optional) - Node label
get_node()
Get a node by ID.

node = g.get_node("alice")
# {"id": "alice", "label": "Person", "properties": {"name": "Alice", "age": 30}}
Returns: dict or None

has_node()
Check if a node exists.

exists = g.has_node("alice")  # True
Returns: bool

delete_node()
Delete a node.

g.delete_node("alice")
get_all_nodes()
Get all nodes, optionally filtered by label.

all_nodes = g.get_all_nodes()
people = g.get_all_nodes(label="Person")
Returns: List of dicts

Edge Operations
upsert_edge()
Create or update an edge.

g.upsert_edge("alice", "bob", {"since": 2020}, rel_type="KNOWS")
Parameters:

source_id (str) - Source node ID
target_id (str) - Target node ID
properties (dict) - Edge properties
rel_type (str, optional) - Relationship type
get_edge()
Get an edge between two nodes.

edge = g.get_edge("alice", "bob")
Returns the first edge found between the source and target nodes, or None if no edge exists.

has_edge()
Check if an edge exists.

exists = g.has_edge("alice", "bob")
Returns: bool

delete_edge()
Delete an edge between two nodes.

g.delete_edge("alice", "bob")
get_all_edges()
Get all edges.

edges = g.get_all_edges()
Returns: List of dicts

Graph Operations
get_neighbors()
Get a node's neighbors (connected by edges in either direction).

neighbors = g.get_neighbors("alice")
Parameters:

node_id (str) - Node ID
Returns: List of neighbor node dicts

node_degree()
Get a node's degree, which is the total number of edges connected to the node (both incoming and outgoing).

degree = g.node_degree("alice")  # 5
Returns an integer count of connected edges.

stats()
Get graph statistics.

stats = g.stats()
# {"nodes": 100, "edges": 250}
Returns: dict

Query Methods
query()
Execute a Cypher query and return results as a list of dictionaries.

results = g.query("MATCH (n:Person) RETURN n.name")
for row in results:
    print(row["n.name"])
This method is for queries that don't require parameters. For parameterized queries, access the underlying connection:

results = g.connection.cypher(
    "MATCH (n:Person {name: $name}) RETURN n",
    {"name": "Alice"}
)
Algorithm Methods
Centrality Algorithms
pagerank()
Compute PageRank scores for all nodes.

results = g.pagerank(damping=0.85, iterations=20)
# [{"node_id": "alice", "score": 0.25}, ...]
Parameters:

damping (float, default: 0.85) - Damping factor
iterations (int, default: 20) - Number of iterations
degree_centrality()
Compute in-degree, out-degree, and total degree for all nodes.

results = g.degree_centrality()
# [{"node_id": "alice", "in_degree": 2, "out_degree": 3, "degree": 5}, ...]
betweenness_centrality()
Compute betweenness centrality (how often a node lies on shortest paths).

results = g.betweenness_centrality()
# Alias: g.betweenness()
Returns: List of {"node_id": str, "score": float}

closeness_centrality()
Compute closeness centrality (average distance to all other nodes).

results = g.closeness_centrality()
# Alias: g.closeness()
Returns: List of {"node_id": str, "score": float}

eigenvector_centrality()
Compute eigenvector centrality (influence based on connections to high-scoring nodes).

results = g.eigenvector_centrality(iterations=100)
Parameters:

iterations (int, default: 100) - Maximum iterations
Community Detection
community_detection()
Detect communities using label propagation.

results = g.community_detection(iterations=10)
# [{"node_id": "alice", "community": 1}, ...]
Parameters:

iterations (int, default: 10) - Maximum iterations
louvain()
Detect communities using the Louvain algorithm (modularity optimization).

results = g.louvain(resolution=1.0)
Parameters:

resolution (float, default: 1.0) - Higher values produce more communities
leiden_communities()
Detect communities using the Leiden algorithm.

results = g.leiden_communities(resolution=1.0, random_seed=42)
Parameters:

resolution (float, default: 1.0) - Resolution parameter
random_seed (int, optional) - Random seed for reproducibility
Requires: graspologic>=3.0 (pip install graspologic)

Connected Components
weakly_connected_components()
Find weakly connected components (ignoring edge direction).

results = g.weakly_connected_components()
# Aliases: g.connected_components(), g.wcc()
Returns: List of {"node_id": str, "component": int}

strongly_connected_components()
Find strongly connected components (respecting edge direction).

results = g.strongly_connected_components()
# Alias: g.scc()
Returns: List of {"node_id": str, "component": int}

Path Finding
shortest_path()
Find the shortest path between two nodes using Dijkstra's algorithm.

path = g.shortest_path("alice", "bob", weight_property="distance")
# {"distance": 2, "path": ["alice", "carol", "bob"], "found": True}
# Alias: g.dijkstra()
Parameters:

source_id (str) - Starting node ID
target_id (str) - Ending node ID
weight_property (str, optional) - Edge property to use as weight
Returns: {"path": list, "distance": float|None, "found": bool}

astar()
Find the shortest path using A* algorithm with optional geographic heuristic.

path = g.astar("alice", "bob", lat_prop="latitude", lon_prop="longitude")
# Alias: g.a_star()
Parameters:

source_id (str) - Starting node ID
target_id (str) - Ending node ID
lat_prop (str, optional) - Latitude property name for heuristic
lon_prop (str, optional) - Longitude property name for heuristic
Returns: {"path": list, "distance": float|None, "found": bool, "nodes_explored": int}

all_pairs_shortest_path()
Compute shortest distances between all node pairs (Floyd-Warshall).

results = g.all_pairs_shortest_path()
# Alias: g.apsp()
Returns: List of {"source": str, "target": str, "distance": float}

Note: O(n²) complexity. Use with caution on large graphs.

Traversal
bfs()
Breadth-first search from a starting node.

results = g.bfs("alice", max_depth=3)
# Alias: g.breadth_first_search()
Parameters:

start_id (str) - Starting node ID
max_depth (int, default: -1) - Maximum depth (-1 for unlimited)
Returns: List of {"user_id": str, "depth": int, "order": int}

dfs()
Depth-first search from a starting node.

results = g.dfs("alice", max_depth=5)
# Alias: g.depth_first_search()
Parameters:

start_id (str) - Starting node ID
max_depth (int, default: -1) - Maximum depth (-1 for unlimited)
Returns: List of {"user_id": str, "depth": int, "order": int}

Similarity
node_similarity()
Compute Jaccard similarity between node neighborhoods.

# All pairs above threshold
results = g.node_similarity(threshold=0.5)

# Specific pair
results = g.node_similarity(node1_id="alice", node2_id="bob")

# Top-k most similar pairs
results = g.node_similarity(top_k=10)
Parameters:

node1_id (str, optional) - First node ID
node2_id (str, optional) - Second node ID
threshold (float, default: 0.0) - Minimum similarity threshold
top_k (int, default: 0) - Return only top-k pairs (0 for all)
Returns: List of {"node1": str, "node2": str, "similarity": float}

knn()
Find k-nearest neighbors for a node based on Jaccard similarity.

results = g.knn("alice", k=10)
Parameters:

node_id (str) - Node to find neighbors for
k (int, default: 10) - Number of neighbors to return
Returns: List of {"neighbor": str, "similarity": float, "rank": int}

triangle_count()
Count triangles and compute clustering coefficients.

results = g.triangle_count()
# Alias: g.triangles()
Returns: List of {"node_id": str, "triangles": int, "clustering_coefficient": float}

Export
to_rustworkx()
Export the graph to a rustworkx PyDiGraph for use with rustworkx algorithms.

graph, node_map = g.to_rustworkx()
Returns: Tuple of (rustworkx.PyDiGraph, dict mapping node IDs to indices)

Requires: rustworkx>=0.13 (pip install rustworkx)

Batch Operations
upsert_nodes_batch()
nodes = [
    ("alice", {"name": "Alice"}, "Person"),
    ("bob", {"name": "Bob"}, "Person"),
]
g.upsert_nodes_batch(nodes)
upsert_edges_batch()
edges = [
    ("alice", "bob", {"since": 2020}, "KNOWS"),
    ("bob", "carol", {"since": 2021}, "KNOWS"),
]
g.upsert_edges_batch(edges)
GraphManager Class
Manages multiple graph databases in a directory with cross-graph query support.

Constructor
from graphqlite import graphs, GraphManager

# Using factory function (recommended)
gm = graphs("./data")

# Or direct instantiation
gm = GraphManager("./data")
gm = GraphManager("./data", extension_path="/path/to/graphqlite.dylib")
Context Manager
with graphs("./data") as gm:
    # Work with graphs...
    pass  # All connections closed automatically
Graph Management
list()
List all graphs in the directory.

names = gm.list()  # ["products", "social", "users"]
Returns: List of graph names (sorted)

exists()
Check if a graph exists.

if gm.exists("social"):
    print("Graph exists")
Returns: bool

create()
Create a new graph.

g = gm.create("social")
Parameters:

name (str) - Graph name
Returns: Graph instance

Raises: FileExistsError if graph already exists

open()
Open an existing graph.

g = gm.open("social")
Parameters:

name (str) - Graph name
Returns: Graph instance

Raises: FileNotFoundError if graph doesn't exist

open_or_create()
Open a graph, creating it if it doesn't exist.

g = gm.open_or_create("cache")
Returns: Graph instance

drop()
Delete a graph and its database file.

gm.drop("old_graph")
Raises: FileNotFoundError if graph doesn't exist

Cross-Graph Queries
query()
Execute a Cypher query across multiple graphs.

result = gm.query(
    "MATCH (n:Person) FROM social RETURN n.name, graph(n) AS source",
    graphs=["social"]
)
for row in result:
    print(f"{row['n.name']} from {row['source']}")
Parameters:

cypher (str) - Cypher query with FROM clauses
graphs (list) - Graph names to attach
params (dict, optional) - Query parameters
Returns: CypherResult

query_sql()
Execute raw SQL across attached graphs.

result = gm.query_sql(
    "SELECT COUNT(*) FROM social.nodes",
    graphs=["social"]
)
Parameters:

sql (str) - SQL query with graph-prefixed table names
graphs (list) - Graph names to attach
parameters (tuple, optional) - Query parameters
Returns: List of tuples

Collection Interface
# Length
len(gm)  # Number of graphs

# Membership
"social" in gm  # True/False

# Iteration
for name in gm:
    print(name)
Utility Functions
escape_string()
Escape a string for use in Cypher.

from graphqlite import escape_string

safe = escape_string("It's a test")
sanitize_rel_type()
Sanitize a relationship type name.

from graphqlite import sanitize_rel_type

safe = sanitize_rel_type("has-friend")  # "HAS_FRIEND"
CYPHER_RESERVED
A set of reserved Cypher keywords that need special handling in queries.

from graphqlite import CYPHER_RESERVED

if my_label.upper() in CYPHER_RESERVED:
    my_label = f"`{my_label}`"  # Quote reserved words
Contains keywords like: MATCH, CREATE, RETURN, WHERE, AND, OR, NOT, IN, AS, WITH, ORDER, BY, LIMIT, SKIP, DELETE, SET, REMOVE, MERGE, ON, CASE, WHEN, THEN, ELSE, END, TRUE, FALSE, NULL, etc.

SQL Interface
GraphQLite works as a standard SQLite extension, providing the cypher() function.

Loading the Extension
SQLite CLI
sqlite3 graph.db
.load /path/to/graphqlite
Or with automatic extension loading:

sqlite3 -cmd ".load /path/to/graphqlite" graph.db
Programmatically
SELECT load_extension('/path/to/graphqlite');
The cypher() Function
Basic Usage
SELECT cypher('MATCH (n) RETURN n.name');
With Parameters
SELECT cypher(
    'MATCH (n:Person {name: $name}) RETURN n',
    '{"name": "Alice"}'
);
Return Format
The cypher() function returns results as JSON:

SELECT cypher('MATCH (n:Person) RETURN n.name, n.age');
-- Returns: [{"n.name": "Alice", "n.age": 30}, {"n.name": "Bob", "n.age": 25}]
Working with Results
Extract Values with JSON Functions
SELECT json_extract(value, '$.n.name') AS name
FROM json_each(cypher('MATCH (n:Person) RETURN n'));
Algorithm Results
SELECT
    json_extract(value, '$.node_id') AS id,
    json_extract(value, '$.score') AS score
FROM json_each(cypher('RETURN pageRank()'))
ORDER BY score DESC
LIMIT 10;
Join with Regular Tables
-- Assuming you have a regular 'users' table
SELECT u.email, json_extract(g.value, '$.degree')
FROM users u
JOIN json_each(cypher('RETURN degreeCentrality()')) g
    ON u.id = json_extract(g.value, '$.user_id');
Write Operations
-- Create nodes
SELECT cypher('CREATE (n:Person {name: "Alice", age: 30})');

-- Create relationships
SELECT cypher('
    MATCH (a:Person {name: "Alice"}), (b:Person {name: "Bob"})
    CREATE (a)-[:KNOWS]->(b)
');

-- Update properties
SELECT cypher('
    MATCH (n:Person {name: "Alice"})
    SET n.age = 31
');

-- Delete
SELECT cypher('
    MATCH (n:Person {name: "Alice"})
    DETACH DELETE n
');
Schema Tables
GraphQLite creates these tables automatically. See Storage Model for detailed documentation.

Core Tables
SELECT * FROM nodes;
-- id (auto-increment primary key)

SELECT * FROM node_labels;
-- node_id, label

SELECT * FROM edges;
-- id, source_id, target_id, type

SELECT * FROM property_keys;
-- id, key (normalized property names)
Property Tables
Properties use key_id as a foreign key to property_keys for normalization:

SELECT * FROM node_props_text;   -- node_id, key_id, value
SELECT * FROM node_props_int;    -- node_id, key_id, value
SELECT * FROM node_props_real;   -- node_id, key_id, value
SELECT * FROM node_props_bool;   -- node_id, key_id, value

SELECT * FROM edge_props_text;   -- edge_id, key_id, value
SELECT * FROM edge_props_int;    -- edge_id, key_id, value
SELECT * FROM edge_props_real;   -- edge_id, key_id, value
SELECT * FROM edge_props_bool;   -- edge_id, key_id, value
Direct SQL Access
You can query the underlying tables directly for debugging or advanced use cases:

-- Count nodes by label
SELECT label, COUNT(*) FROM node_labels GROUP BY label;

-- Find nodes with a specific property (join through property_keys)
SELECT n.id, pk.key, p.value
FROM nodes n
JOIN node_props_text p ON n.id = p.node_id
JOIN property_keys pk ON p.key_id = pk.id
WHERE pk.key = 'name';

-- Find all properties for a specific node
SELECT pk.key, p.value
FROM node_props_text p
JOIN property_keys pk ON p.key_id = pk.id
WHERE p.node_id = 1;

-- Find edges with their endpoint info
SELECT e.id, e.type, e.source_id, e.target_id
FROM edges e
WHERE e.type = 'KNOWS';
Transaction Support
GraphQLite respects SQLite transactions:

BEGIN;
SELECT cypher('CREATE (a:Person {name: "Alice"})');
SELECT cypher('CREATE (b:Person {name: "Bob"})');
SELECT cypher('MATCH (a:Person {name: "Alice"}), (b:Person {name: "Bob"}) CREATE (a)-[:KNOWS]->(b)');
COMMIT;
Or rollback on error:

BEGIN;
SELECT cypher('CREATE (n:Person {name: "Test"})');
ROLLBACK;  -- Node is not create

Storage Model
GraphQLite uses a typed property graph model stored in regular SQLite tables. The schema is designed for query efficiency using an Entity-Attribute-Value (EAV) pattern with property key normalization.

Schema Overview
┌─────────────────────────────────────┐
│              nodes                   │
│  id (PK, auto-increment)            │
├─────────────────────────────────────┤
│  1                                  │
│  2                                  │
│  3                                  │
└─────────────────────────────────────┘
           │
           │ 1:N
           ▼
┌─────────────────────────────────────┐
│           node_labels                │
│  node_id (FK) │ label               │
├───────────────┼─────────────────────┤
│  1            │ "Person"            │
│  2            │ "Person"            │
│  3            │ "Company"           │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│           property_keys              │
│  id (PK) │ key (UNIQUE)             │
├──────────┼──────────────────────────┤
│  1       │ "name"                   │
│  2       │ "age"                    │
│  3       │ "id"                     │
└─────────────────────────────────────┘
           │
           │ 1:N (via key_id)
           ▼
┌───────────────────────────────────────────┐
│            node_props_text                 │
│  node_id (FK) │ key_id (FK) │ value       │
├───────────────┼─────────────┼─────────────┤
│  1            │ 3           │ "alice"     │
│  1            │ 1           │ "Alice"     │
│  2            │ 3           │ "bob"       │
│  2            │ 1           │ "Bob"       │
└───────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                         edges                            │
│  id (PK) │ source_id (FK) │ target_id (FK) │ type       │
├──────────┼────────────────┼────────────────┼────────────┤
│  1       │ 1              │ 2              │ "KNOWS"    │
│  2       │ 1              │ 3              │ "WORKS_AT" │
└─────────────────────────────────────────────────────────┘
Core Tables
nodes
The nodes table stores graph nodes with a simple auto-incrementing ID. Node metadata such as labels and properties are stored in separate tables, enabling nodes to have multiple labels and efficient property queries.

Column	Type	Description
id	INTEGER PRIMARY KEY AUTOINCREMENT	Internal node identifier
node_labels
Labels are stored in a separate table allowing nodes to have multiple labels. This normalized design enables efficient label-based filtering through indexed lookups.

Column	Type	Description
node_id	INTEGER FK → nodes(id)	References the node
label	TEXT	Label name (e.g., "Person")
The primary key is the composite (node_id, label), which prevents duplicate labels on the same node.

edges
The edges table stores relationships between nodes with a required relationship type.

Column	Type	Description
id	INTEGER PRIMARY KEY AUTOINCREMENT	Internal edge identifier
source_id	INTEGER FK → nodes(id)	Source node
target_id	INTEGER FK → nodes(id)	Target node
type	TEXT NOT NULL	Relationship type (e.g., "KNOWS")
Foreign keys use ON DELETE CASCADE so removing a node automatically removes its edges.

property_keys
Property names are normalized into a lookup table to reduce storage overhead and improve query performance. Instead of storing the property name string with every property value, we store a small integer key ID.

Column	Type	Description
id	INTEGER PRIMARY KEY AUTOINCREMENT	Property key identifier
key	TEXT UNIQUE	Property name (e.g., "name", "age")
Property Tables
Properties are stored in separate tables by type. This approach enables type-safe queries, efficient indexing by value, and proper numeric comparisons without type conversion overhead.

Node property tables:

node_props_text — String values
node_props_int — Integer values
node_props_real — Floating-point values
node_props_bool — Boolean values (stored as 0 or 1)
Edge property tables:

edge_props_text
edge_props_int
edge_props_real
edge_props_bool
Each property table has the same structure:

Column	Type	Description
node_id / edge_id	INTEGER FK	References the owner entity
key_id	INTEGER FK → property_keys(id)	References the property name
value	(varies by table)	The property value
The primary key is the composite (node_id, key_id) or (edge_id, key_id), ensuring each entity has at most one value per property.

Indexes
GraphQLite creates indexes optimized for common graph query patterns:

-- Edge traversal (covers both directions and type filtering)
CREATE INDEX idx_edges_source ON edges(source_id, type);
CREATE INDEX idx_edges_target ON edges(target_id, type);
CREATE INDEX idx_edges_type ON edges(type);

-- Label filtering
CREATE INDEX idx_node_labels_label ON node_labels(label, node_id);

-- Property key lookup
CREATE INDEX idx_property_keys_key ON property_keys(key);

-- Property value queries (enables efficient WHERE clauses)
CREATE INDEX idx_node_props_text_key_value ON node_props_text(key_id, value, node_id);
CREATE INDEX idx_node_props_int_key_value ON node_props_int(key_id, value, node_id);
-- ... similar for other property tables
The property indexes are designed "key-first" to efficiently satisfy queries like WHERE n.name = 'Alice', which translate to lookups by key_id and value.

Why This Design?
Typed property tables provide several advantages over storing all properties as JSON or a single TEXT column. Integer comparisons are performed natively rather than through string parsing. Type-specific indexes enable efficient range queries. Storage is more compact since values don't require type metadata.

Property key normalization through the property_keys table reduces storage by replacing repeated property name strings with integer IDs. This also enables efficient property-first queries and simplifies schema introspection.

Separate label table allows nodes to have multiple labels, which is a common requirement in graph modeling. The label index supports efficient label-based filtering without scanning all nodes.

Query Translation
When you write:

MATCH (p:Person {name: 'Alice'})
WHERE p.age > 25
RETURN p.name, p.age
GraphQLite translates this to SQL that joins the appropriate tables:

SELECT
    name_prop.value AS "p.name",
    age_prop.value AS "p.age"
FROM nodes p
JOIN node_labels p_label ON p.id = p_label.node_id AND p_label.label = 'Person'
LEFT JOIN node_props_text name_prop
    ON p.id = name_prop.node_id
    AND name_prop.key_id = (SELECT id FROM property_keys WHERE key = 'name')
LEFT JOIN node_props_int age_prop
    ON p.id = age_prop.node_id
    AND age_prop.key_id = (SELECT id FROM property_keys WHERE key = 'age')
WHERE name_prop.value = 'Alice'
    AND age_prop.value > 25
In practice, the query optimizer uses cached prepared statements for property key lookups, making this translation efficient.

Direct SQL Access
You can query the underlying tables directly for advanced use cases:


-- Count nodes by label
SELECT label, COUNT(*) FROM node_labels GROUP BY label;

-- Find all properties of a specific node
SELECT pk.key, 'text' as type, pt.value
FROM node_props_text pt
JOIN property_keys pk ON pt.key_id = pk.id
WHERE pt.node_id = 1
UNION ALL
SELECT pk.key, 'int' as type, CAST(pi.value AS TEXT)
FROM node_props_int pi
JOIN property_keys pk ON pi.key_id = pk.id
WHERE pi.node_id = 1;

-- Find nodes with a specific property value
SELECT nl.node_id, nl.label, pt.value as name
FROM node_props_text pt
JOIN property_keys pk ON pt.key_id = pk.id
JOIN node_labels nl ON pt.node_id = nl.node_id
WHERE pk.key = 'name' AND pt.value 