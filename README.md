# devops-fastapi-lab

Projeto-vitrine público e reproduzível de uma API FastAPI com práticas de entrega, segurança, observabilidade e operação. É um laboratório demonstrativo: **não representa uma carga real de produção, não possui SLA e não publica métricas de desempenho inventadas**.

## O que está incluído

- CRUD de tarefas em SQLite e documentação OpenAPI em `/docs`;
- probes separadas de liveness (`/health/live`) e readiness (`/health/ready`);
- métricas Prometheus em `/metrics`, com contagem e latência por rota normalizada;
- testes com cobertura mínima de 90%, Ruff e mypy estrito;
- contêiner não-root, capabilities removidas, healthcheck e limites no Compose;
- CI para qualidade, imagem e scan Trivy; Dependabot para Python, Actions e Docker;
- scripts de backup consistente e restauração atômica do SQLite.

## Arquitetura

```mermaid
flowchart LR
    C[Cliente] -->|HTTPS| N[Nginx / TLS]
    N -->|HTTP localhost:8000| A[FastAPI + Uvicorn]
    A --> D[(SQLite /app/data/tasks.db)]
    P[Prometheus] -->|GET /metrics| A
    A -->|stdout/stderr| L[Docker logs]
    H[Docker healthcheck] -->|live/ready| A
```

A aplicação usa um único processo Uvicorn porque SQLite serializa escritas e o volume é local. Para múltiplas réplicas, migre a persistência para PostgreSQL antes de escalar. O readiness confirma acesso ao banco; liveness apenas confirma que o processo HTTP responde. Labels das métricas usam o template da rota, evitando cardinalidade por ID.

## API

| Método | Rota | Finalidade |
|---|---|---|
| `GET` | `/` | metadados e link da documentação |
| `GET` | `/health/live` | vida do processo |
| `GET` | `/health/ready` | prontidão e acesso ao SQLite |
| `GET` | `/api/v1/tasks` | listar tarefas |
| `POST` | `/api/v1/tasks` | criar tarefa |
| `GET` | `/api/v1/tasks/{id}` | consultar tarefa |
| `PATCH` | `/api/v1/tasks/{id}` | atualizar campos enviados |
| `DELETE` | `/api/v1/tasks/{id}` | remover tarefa |
| `GET` | `/metrics` | exposição Prometheus |

Exemplo:

```bash
curl -fsS -X POST http://localhost:8000/api/v1/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"Validar pipeline","completed":false}'
```

## Desenvolvimento local

Requisitos: Python 3.12 e [uv](https://docs.astral.sh/uv/), ou Docker com Compose v2.

```bash
cp .env.example .env
make install
make check
make run
```

Com contêiner:

```bash
make compose-up
curl -fsS http://127.0.0.1:8000/health/ready
docker compose ps
make compose-down
```

O `uv.lock` torna o ambiente reproduzível e o `pyproject.toml` fixa versões diretas. Atualizações devem passar pelo Dependabot e pela CI.

## Decisões e limites

- **SQLite:** reduz dependências e é adequado ao laboratório; o volume nomeado mantém os dados entre recriações. Não é indicado para escrita concorrente em várias réplicas.
- **Sem autenticação:** mantém o exemplo focado em DevOps. Não exponha a API publicamente com dados sensíveis sem autenticação, autorização, rate limiting e revisão de ameaça.
- **Métricas no processo:** suficientes para uma réplica. Em múltiplos workers, configure o modo multiprocess do cliente Prometheus ou use telemetria externa.
- **CI sem publicação:** constrói e verifica localmente no runner; não envia imagens nem requer credenciais de registry.
- **Imagem enxuta:** base slim, usuário UID/GID 10001 e apenas dependências de runtime. Tags de base são atualizadas pelo Dependabot; para ambientes controlados, aprove e fixe também o digest validado.

## Deploy em VPS com Nginx e HTTPS

Pré-requisitos: VPS Linux atualizada, Docker/Compose, DNS `api.exemplo.com` apontando para a VPS e portas 80/443 liberadas. Clone uma release revisada em `/opt/devops-fastapi-lab` e não armazene segredos no Git.

```bash
cd /opt/devops-fastapi-lab
cp .env.example .env
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health/ready
```

O Compose publica apenas em loopback. Exemplo `/etc/nginx/sites-available/devops-fastapi-lab`:

```nginx
server {
    listen 80;
    server_name api.exemplo.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
```

Valide com `sudo nginx -t`, habilite o site e recarregue o Nginx. Em seguida obtenha e renove TLS com Certbot (`certbot --nginx -d api.exemplo.com`) seguindo a documentação da sua distribuição. Confirme redirecionamento HTTPS, renovação automática e firewall. Restrinja `/metrics` por IP/rede no Nginx se o endpoint não deve ser público.

Atualização sugerida: gerar backup, buscar a versão revisada, executar `docker compose build --pull`, `docker compose up -d` e validar readiness/logs. Guarde a referência da imagem anterior para rollback; se houver mudança de dados, teste restauração antes.

## Observabilidade

Configure Prometheus para coletar `http://api:8000/metrics` quando estiver na mesma rede, ou uma rota protegida via proxy. Alertas devem refletir objetivos definidos pelo operador, não valores copiados deste laboratório. Comece observando:

- disponibilidade da probe de readiness;
- taxa de respostas 5xx em `http_requests_total`;
- distribuição de `http_request_duration_seconds`;
- reinícios, CPU, memória e espaço no volume;
- erros e exceções em `docker compose logs`.

Os logs do Uvicorn vão para stdout/stderr e podem ser coletados pelo driver Docker ou por um agente. Defina retenção/rotação no daemon para evitar esgotar disco. Este projeto não inclui dashboards nem afirma resultados de carga.

## Backup e restauração

O script usa a API de backup online do SQLite, produz um arquivo com permissão `0600` e executa `integrity_check`:

```bash
make backup
# copie o arquivo de backups/ para armazenamento externo cifrado e teste-o periodicamente
```

Restauração substitui o banco de forma atômica. Ela exige confirmação explícita e deve ocorrer com a API parada para evitar descritores apontando ao arquivo anterior:

```bash
docker compose stop api
uv run python scripts/restore.py backups/tasks-AAAAMMDDTHHMMSSZ.db \
  --database data/tasks.db --force
docker compose start api
```

No volume Docker, copie o backup para um local temporário e execute a restauração em um contêiner com o volume montado, ou restaure em um novo volume e troque após validação. Defina RPO, retenção e armazenamento externo conforme sua realidade; o repositório não presume esses valores.

## Runbook de incidente

1. **Detectar e classificar:** registre horário UTC, sintomas e impacto; não apague evidências.
2. **Verificar:** rode `docker compose ps`, consulte `/health/live`, `/health/ready`, métricas e `docker compose logs --since 30m api`.
3. **Conter:** se houver suspeita de abuso, restrinja acesso no firewall/Nginx; se o deploy causou falha, volte à imagem previamente validada.
4. **Dados:** confira espaço e permissões do volume. Antes de qualquer reparo, faça uma cópia consistente. Restaure somente um backup cuja integridade foi validada.
5. **Recuperar:** suba uma instância, valide CRUD e probes localmente, depois reabra tráfego gradualmente.
6. **Comunicar:** mantenha uma linha do tempo factual; não prometa prazo sem evidência.
7. **Aprender:** documente causa, impacto, detecção, ações e itens preventivos sem culpabilização; transforme ações em issues com responsáveis.

Se liveness falha, investigue processo/reinícios. Se apenas readiness falha, priorize arquivo, volume, permissões e integridade do SQLite. Se a latência cresce, verifique recursos, bloqueios de escrita e volume antes de reiniciar indiscriminadamente.

## Segurança e contribuição

Consulte [SECURITY.md](SECURITY.md) para relatos privados e [CONTRIBUTING.md](CONTRIBUTING.md) para o fluxo de contribuição. Licenciado sob [MIT](LICENSE).
