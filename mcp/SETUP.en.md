# MCP Configuration for Claude Desktop

This guide explains how to configure access to your Synesis research database via Claude Desktop using the Model Context Protocol (MCP).

---

## Prerequisites

1. **Neo4j** running locally (Desktop, Docker, or Server)
2. **Claude Desktop** installed
3. **Python 3.10+** installed
4. **uv** (Python package manager)

### Install uv (if needed)

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Step 1: Locate the Configuration File

The Claude Desktop configuration file is located at:

| System | Path |
|--------|------|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

**Windows Tip:** Press `Win + R`, paste `%APPDATA%\Claude` and press Enter.

---

## Step 2: Configure the MCP Server

### Option A: Single Database

Copy the content below to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "synesis-neo4j": {
      "command": "uvx",
      "args": ["mcp-neo4j-cypher@0.5.2", "--read-only"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "YOUR_PASSWORD_HERE",
        "NEO4J_DATABASE": "bibliometrics"
      }
    }
  }
}
```

**Important:** Replace:
- `YOUR_PASSWORD_HERE` with your Neo4j password
- `bibliometrics` with your database name

### Option B: Multiple Databases

To access multiple research projects simultaneously:

```json
{
  "mcpServers": {
    "bibliometrics": {
      "command": "uvx",
      "args": ["mcp-neo4j-cypher@0.5.2", "--namespace", "bib", "--read-only"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "YOUR_PASSWORD",
        "NEO4J_DATABASE": "bibliometrics"
      }
    },
    "gestao-fe": {
      "command": "uvx",
      "args": ["mcp-neo4j-cypher@0.5.2", "--namespace", "gfe", "--read-only"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "YOUR_PASSWORD",
        "NEO4J_DATABASE": "gestao-fe"
      }
    }
  }
}
```

---

## Step 3: Restart Claude Desktop

After saving the configuration, completely restart Claude Desktop.

---

## Step 4: Verify the Connection

In Claude Desktop, you can verify if the connection is working by asking:

> "Use the get_neo4j_schema tool to show the database structure"

If using multiple databases:
> "Use bib-get_neo4j_schema to show the bibliometrics database structure"

---

## Example Questions

### Basic Exploration

> "What are the main concepts in the research?"

> "Show the top 10 concepts with highest PageRank"

> "What are the identified thematic communities?"

### Traceability

> "Which sources mention the concept 'Acceptance'?"

> "Show the original citations about 'Cost' with file and line"

> "List the articles that discuss 'Governance' and their evidence"

### Relationship Analysis

> "Which concepts are related to 'Acceptance'?"

> "Find concepts that 'bridge' between communities (high betweenness)"

> "Which concepts belong to the same community as 'Trust'?"

### Advanced Queries

> "Compare the centrality metrics of the top 5 concepts"

> "Which sources contribute the most unique concepts?"

> "Find co-occurrence patterns between concepts"

---

## Recommended System Prompt

For best results, add this context at the beginning of your conversations:

```
You have access to a Neo4j graph database with qualitative research data.

Structure:
- Source: sources (articles, interviews)
- Item: citations from sources
- [Concept]: coded concepts (Factor, Ordem_2a, etc.)
- Topic/Aspect/Dimension: taxonomies

Available metrics:
- pagerank: relevance
- betweenness: bridge role
- community: thematic cluster
- mention_count: frequency
- source_count: dispersion

Always include traceability (file, line) when relevant.
```

---

## Troubleshooting

### "Connection refused"

- Check if Neo4j is running
- Confirm the port (default: 7687)
- Test the connection in Neo4j Browser

### "Authentication failed"

- Verify username and password
- Confirm that the user has access to the specified database

### "Database not found"

- Check the database name in `NEO4J_DATABASE`
- Use `:dbs` in Neo4j Browser to list available databases

### Tool not appearing in Claude

- Completely restart Claude Desktop
- Check if `uvx` is in the PATH
- Test in terminal: `uvx mcp-neo4j-cypher@0.5.2 --help`

---

## Security

- **Always use `--read-only`** to prevent accidental changes
- **Do not share** the configuration file (contains password)
- **Use environment variables** in production

---

## References

- [Neo4j MCP Documentation](https://neo4j.com/developer/genai-ecosystem/model-context-protocol-mcp/)
- [Claude Desktop MCP Guide](https://modelcontextprotocol.io/quickstart/user)
- [mcp-neo4j-cypher PyPI](https://pypi.org/project/mcp-neo4j-cypher/)
