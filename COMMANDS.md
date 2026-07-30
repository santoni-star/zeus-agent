# Zeus Agent — Commands Reference

## Interactive Mode Commands

All commands available in `python -m zeus --interactive`.

### General

| Command | Description | Example |
|---------|-------------|---------|
| `/help` | Show available commands | `/help` |
| `/exit` or `/quit` | Exit interactive mode | `/exit` |
| `<any text>` | Send as user message to Zeus | `привіт, як справи?` |

### Conversation & Search

| Command | Description | Example |
|---------|-------------|---------|
| `/history` | Show current dialog history | `/history` |
| `/search <query>` | Search past sessions | `/search що ми вчора робили` |
| `/search` | Show search usage | `/search` |

### Memory & Profile

| Command | Description | Example |
|---------|-------------|---------|
| `/remember <fact>` | Save a fact permanently | `/remember я люблю короткі відповіді` |
| `/forget <query>` | Forget matching facts | `/forget довгі` |
| `/facts` | Show all saved facts | `/facts` |
| `/profile` | Show user profile | `/profile` |

### Skills

| Command | Description | Example |
|---------|-------------|---------|
| `/skills` or `/skill list` | List all available skills | `/skills` |
| `/skill show <name>` | Show skill details | `/skill show getting-started` |
| `/skill create <name>: <desc>` | Create a new skill | `/skill create tdd: Test-driven development workflow` |
| `/do <name>` | Execute a skill | `/do getting-started` |

### Tools

| Command | Description | Example |
|---------|-------------|---------|
| `/tools` | List all available tools | `/tools` |
| `/tool <name>` | Show tool help and schema | `/tool web_fetch` |
| `/tool create <desc>` | Create a tool from description | `/tool create a tool that counts lines in files` |

### Code Review (SelfReviewModule)

| Command | Description | Example |
|---------|-------------|---------|
| `/review scan` | Run heuristic code analysis | `/review scan` |
| `/review list` | Show pending suggestions | `/review list` |
| `/review show <n>` | Show suggestion details | `/review show 3` |
| `/review approve <n>` | Accept and apply suggestion | `/review approve 2` |
| `/review reject <n>` | Reject suggestion | `/review reject 5` |

### Telemetry

| Command | Description | Example |
|---------|-------------|---------|
| `/stats` | Module performance stats (latency) | `/stats` |
| `/insights` | Bottleneck detection + ideas | `/insights` |
| `/errors` | Error reports | `/errors` |

### Context & Debug

| Command | Description | Example |
|---------|-------------|---------|
| `/context` | Show current context budget | `/context` |
| `/config` | Show current config | `/config` |

---

## CLI Arguments

| Argument | Description | Example |
|----------|-------------|---------|
| `-i`, `--interactive` | Interactive session | `python -m zeus -i` |
| `--provider <name>` | Set LLM provider | `--provider openrouter` |
| `--model <name>` | Set model | `--model deepseek/deepseek-v4-flash-free` |
| `--doctor` | System health check | `python -m zeus --doctor` |
| `--providers` | List all providers | `python -m zeus --providers` |
| `--config <path>` | Custom config path | `--config ~/.zeus/config.yaml` |
| `--no-interactive` | Force non-interactive | `python -m zeus "query" --no-interactive` |

### Single Query Mode

```bash
# Default provider
python -m zeus "search for python 3.14 news"

# With specific provider
python -m zeus --provider anthropic --model claude-sonnet-4 "analyze this code"

# Pipe output
python -m zeus "what time is it?" | cat
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ZEUS_AGENT_MODE` | Set to `child` for child agent mode |
| `ZEUS_CONFIG` | Path to config file |
| `ZEUS_PROVIDER` | Default provider name |
| `ZEUS_MODEL` | Default model name |
| `ZEUS_API_KEY` | API key (if not in config) |

---

## Config File (`~/.zeus/config.yaml`)

```yaml
provider: opencode-zen
model: deepseek-v4-flash-free
api_key: ""  # or use env var

memory:
  enabled: true
  max_history: 20
  db_path: ~/.zeus/memory.db

scheduler:
  enabled: true
  db_path: ~/.zeus/scheduler.db

telemetry:
  enabled: true
  db_path: ~/.zeus/telemetry.db

context:
  reserve_output: 4096
  reserve_tools: 2048
  default_language: uk

skills:
  directory: ~/.zeus/skills

modules:  # enabled EventBus modules
  - classifier
  - memory
  - router
  - pipeline
  - reflection
  - sub_agent
  - mcp
  - self_review
  - telemetry
```

---

## Return Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | LLM provider error |
| 2 | Config error |
| 3 | Timeout |
| 127 | Command not found |
