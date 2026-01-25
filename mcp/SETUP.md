# Configuração MCP para Claude Desktop

Este guia explica como configurar o acesso ao seu banco de pesquisa Synesis via Claude Desktop usando o Model Context Protocol (MCP).

---

## Pré-requisitos

1. **Neo4j** rodando localmente (Desktop, Docker ou Server)
2. **Claude Desktop** instalado
3. **Python 3.10+** instalado
4. **uv** (gerenciador de pacotes Python)

### Instalar uv (se necessário)

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Passo 1: Localizar o Arquivo de Configuração

O arquivo de configuração do Claude Desktop está em:

| Sistema | Caminho |
|---------|---------|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

**Dica Windows:** Pressione `Win + R`, cole `%APPDATA%\Claude` e pressione Enter.

---

## Passo 2: Configurar o Servidor MCP

### Opção A: Banco Único

Copie o conteúdo abaixo para `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "synesis-neo4j": {
      "command": "uvx",
      "args": ["mcp-neo4j-cypher@0.5.2", "--read-only"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "SUA_SENHA_AQUI",
        "NEO4J_DATABASE": "bibliometrics"
      }
    }
  }
}
```

**Importante:** Substitua:
- `SUA_SENHA_AQUI` pela senha do seu Neo4j
- `bibliometrics` pelo nome do seu banco de dados

### Opção B: Múltiplos Bancos

Para acessar vários projetos de pesquisa simultaneamente:

```json
{
  "mcpServers": {
    "bibliometrics": {
      "command": "uvx",
      "args": ["mcp-neo4j-cypher@0.5.2", "--namespace", "bib", "--read-only"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "SUA_SENHA",
        "NEO4J_DATABASE": "bibliometrics"
      }
    },
    "gestao-fe": {
      "command": "uvx",
      "args": ["mcp-neo4j-cypher@0.5.2", "--namespace", "gfe", "--read-only"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "SUA_SENHA",
        "NEO4J_DATABASE": "gestao-fe"
      }
    }
  }
}
```

---

## Passo 3: Reiniciar o Claude Desktop

Após salvar a configuração, reinicie completamente o Claude Desktop.

---

## Passo 4: Verificar a Conexão

No Claude Desktop, você pode verificar se a conexão está funcionando perguntando:

> "Use a ferramenta get_neo4j_schema para mostrar a estrutura do banco de dados"

Se estiver usando múltiplos bancos:
> "Use bib-get_neo4j_schema para mostrar a estrutura do banco bibliometrics"

---

## Exemplos de Perguntas

### Exploração Básica

> "Quais são os principais conceitos da pesquisa?"

> "Mostre os 10 conceitos com maior PageRank"

> "Quais são as comunidades temáticas identificadas?"

### Rastreabilidade

> "Quais fontes mencionam o conceito 'Acceptance'?"

> "Mostre as citações originais sobre 'Cost' com arquivo e linha"

> "Liste os artigos que tratam de 'Governance' e suas evidências"

### Análise de Relações

> "Quais conceitos estão relacionados a 'Acceptance'?"

> "Encontre conceitos que fazem 'ponte' entre comunidades (alto betweenness)"

> "Quais conceitos pertencem à mesma comunidade que 'Trust'?"

### Consultas Avançadas

> "Compare as métricas de centralidade dos 5 principais conceitos"

> "Quais fontes contribuem com mais conceitos únicos?"

> "Encontre padrões de co-ocorrência entre conceitos"

---

## Prompt de Sistema Recomendado

Para melhores resultados, adicione este contexto no início das suas conversas:

```
Você tem acesso a um banco de grafos Neo4j com dados de pesquisa qualitativa.

Estrutura:
- Source: fontes (artigos, entrevistas)
- Item: citações das fontes
- [Concept]: conceitos codificados (Factor, Ordem_2a, etc.)
- Topic/Aspect/Dimension: taxonomias

Métricas disponíveis:
- pagerank: relevância
- betweenness: papel de ponte
- community: cluster temático
- mention_count: frequência
- source_count: dispersão

Sempre inclua rastreabilidade (arquivo, linha) quando relevante.
```

---

## Solução de Problemas

### "Connection refused"

- Verifique se o Neo4j está rodando
- Confirme a porta (padrão: 7687)
- Teste a conexão no Neo4j Browser

### "Authentication failed"

- Verifique usuário e senha
- Confirme que o usuário tem acesso ao banco especificado

### "Database not found"

- Verifique o nome do banco em `NEO4J_DATABASE`
- Use `:dbs` no Neo4j Browser para listar bancos disponíveis

### Ferramenta não aparece no Claude

- Reinicie completamente o Claude Desktop
- Verifique se `uvx` está no PATH
- Teste no terminal: `uvx mcp-neo4j-cypher@0.5.2 --help`

---

## Segurança

- **Sempre use `--read-only`** para evitar alterações acidentais
- **Não compartilhe** o arquivo de configuração (contém senha)
- **Use variáveis de ambiente** em produção

---

## Referências

- [Neo4j MCP Documentation](https://neo4j.com/developer/genai-ecosystem/model-context-protocol-mcp/)
- [Claude Desktop MCP Guide](https://modelcontextprotocol.io/quickstart/user)
- [mcp-neo4j-cypher PyPI](https://pypi.org/project/mcp-neo4j-cypher/)
