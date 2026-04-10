# Plano de Implementação MultiBanco (`synesis2graph`)

## 1. Objetivo
Evoluir o pipeline atual para um único script (`synesis2graph.py`) capaz de transferir dados do Synesis para dois backends selecionáveis por parâmetro:
- `neo4j`
- `graphqlite` (SQLite + Cypher)

## 2. Decisões Fechadas
- O script final passa a ser `synesis2graph.py`.
- O arquivo antigo `synesis2neo4j.py` permanece como backup, sem ser removido.
- Para `graphqlite`, o banco `.db` deve ser apagado e recriado em toda execução.
- Configuração permanece centralizada em `config.toml`.

## 3. Escopo da Entrega
- Seleção de backend por CLI.
- Leitura de configuração por backend no mesmo `config.toml`.
- Sincronização completa para Neo4j e GraphQLite.
- Criação/recriação automática do banco GraphQLite.
- Métricas compatíveis por backend.
- Mensagens de erro e observabilidade consistentes.

Fora de escopo nesta fase:
- Migração incremental de dados.
- Modo sem limpeza total do destino.
- Refatoração extensa de domínio além do necessário para multibanco.

## 4. Arquitetura Alvo (alto nível)
Separar o pipeline em três blocos:
1. Núcleo comum
- Compilação Synesis.
- Construção do `GraphPayload`.
- Regras de sanitização e transformação de dados.

2. Adaptadores de backend
- Adaptador Neo4j: conexão, sync, métricas Neo4j/GDS.
- Adaptador GraphQLite: conexão SQLite/Cypher, recriação de `.db`, sync e métricas compatíveis.

3. Orquestração
- `run_pipeline` seleciona backend e delega para o adaptador correto.
- Relato de progresso e tratamento de falhas permanecem centralizados.

## 5. Fases de Implementação

### Fase 1: Preparação e estrutura
1. Criar `synesis2graph.py` a partir do script atual.
2. Renomear identidade do projeto no código:
- título, descrição, versão exibida e mensagens principais.
3. Definir enum/constantes de backend (`neo4j`, `graphqlite`).
4. Atualizar CLI:
- incluir `--backend` com default `neo4j` para compatibilidade.
- manter `--project` e `--config`.

Critério de aceite:
- `--help` exibe backend e mantém uso anterior funcional com Neo4j.

### Fase 2: Configuração multibanco no `config.toml`
1. Manter `[neo4j]` como está, com ajustes mínimos de documentação.
2. Adicionar seção `[graphqlite]`, por exemplo:
- `db_path` (caminho do arquivo SQLite).
- `extension_path` (opcional).
3. Refatorar `load_config` para carregar configuração conforme backend.
4. Definir erros explícitos para:
- seção ausente,
- campos obrigatórios ausentes,
- formato inválido.

Critério de aceite:
- validação de config correta para os dois backends com mensagens claras.

### Fase 3: Contrato de backend (abstração)
1. Definir um contrato interno mínimo para operações de persistência:
- preparar destino,
- limpar destino,
- sincronizar payload,
- calcular métricas,
- fechar conexão.
2. Mapear implementação atual do Neo4j nesse contrato sem mudar comportamento.
3. Isolar dependências específicas:
- `neo4j` driver e GDS apenas no adaptador Neo4j.
- `graphqlite` apenas no adaptador GraphQLite.

Critério de aceite:
- execução Neo4j mantém resultado funcional equivalente ao atual.

### Fase 4: Implementação GraphQLite (recriação total do `.db`)
1. Resolver caminho do `.db` a partir da config.
2. Garantir diretório pai existente.
3. Se arquivo `.db` existir, apagar antes da conexão.
4. Criar nova conexão GraphQLite, carregando extensão quando necessário.
5. Executar sincronização com Cypher compatível:
- criar nós de `Source`, `Item`, conceitos e taxonomias,
- criar relacionamentos (`FROM_SOURCE`, `MENTIONS`, `RELATES_TO`, etc.),
- preservar semântica de propriedades e labels dinâmicos.
6. Confirmar transacionalidade via transação SQLite quando aplicável.

Critério de aceite:
- execução `--backend graphqlite` cria novo `.db` sempre e popula dados corretamente.

### Fase 5: Métricas por backend
1. Neo4j:
- manter métricas nativas existentes,
- manter métricas GDS com fallback quando plugin ausente.
2. GraphQLite:
- implementar métricas nativas equivalentes viáveis.
- não depender de `CALL gds.*` (incompatível).
3. Padronizar campos de métrica para manter comparabilidade mínima.

Critério de aceite:
- ambos os backends terminam pipeline com etapa de métricas sem erro crítico.

### Fase 6: Robustez e mensagens operacionais
1. Padronizar mensagens por backend (conexão, sync, métricas, encerramento).
2. Diferenciar falhas de:
- compilação,
- configuração,
- dependência ausente,
- conexão,
- sync.
3. Melhorar diagnósticos quando faltar biblioteca:
- `pip install neo4j`
- `pip install graphqlite`

Critério de aceite:
- erros acionáveis e sem ambiguidade para os dois backends.

### Fase 7: Testes e validação
1. Testes de smoke por backend:
- Neo4j: fluxo atual completo.
- GraphQLite: criação do `.db`, inserção e consultas básicas de sanidade.
2. Casos negativos:
- backend inválido,
- seção de config ausente,
- credenciais Neo4j inválidas,
- path GraphQLite inválido.
3. Verificar consistência estatística:
- contagens de conceitos/sources/items/chains entre backends no mesmo input.

Critério de aceite:
- suíte mínima validando execução ponta a ponta nos dois destinos.

### Fase 8: Documentação e transição
1. Atualizar README/README.pt:
- novo nome `synesis2graph.py`,
- exemplos Neo4j e GraphQLite,
- formato do `config.toml`.
2. Documentar estratégia de recriação do `.db` no backend GraphQLite.
3. Registrar changelog com breaking/behavior changes.

Critério de aceite:
- documentação permite execução sem leitura de código.

## 6. Ordem recomendada de execução
1. Fase 1
2. Fase 2
3. Fase 3
4. Fase 4
5. Fase 5
6. Fase 6
7. Fase 7
8. Fase 8

## 7. Riscos principais e mitigação
- Divergências de Cypher entre Neo4j e GraphQLite:
  Mitigação: manter consultas em blocos por backend e validar cedo as queries críticas.
- Dependências opcionais ausentes no ambiente:
  Mitigação: mensagens explícitas e falha rápida com instrução de instalação.
- Regressão no fluxo Neo4j por refatoração:
  Mitigação: preservar contrato atual e executar smoke tests após cada fase.
- Custo de recriar `.db` em projetos grandes:
  Mitigação: documentar claramente comportamento e avaliar modo incremental em fase futura.

## 8. Critérios finais de pronto
- `synesis2graph.py` executa com `--backend neo4j` e mantém compatibilidade operacional.
- `synesis2graph.py` executa com `--backend graphqlite` e recria `.db` em toda execução.
- `config.toml` suporta os dois backends com validação adequada.
- Pipeline completo (compilar, sincronizar, métricas, resumo) funciona em ambos.
- Documentação atualizada com exemplos reais de uso.
