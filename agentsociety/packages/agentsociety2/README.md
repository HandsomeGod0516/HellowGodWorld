# AgentSociety 2

[![PyPI Version](https://img.shields.io/pypi/v/agentsociety2.svg)](https://pypi.org/project/agentsociety2/)
[![Python Version](https://img.shields.io/pypi/pyversions/agentsociety2.svg)](https://pypi.org/project/agentsociety2/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-latest-brightgreen.svg)](https://agentsociety2.readthedocs.io/)
[![中文文档](https://img.shields.io/badge/docs-%E4%B8%AD%E6%96%87-red.svg)](https://agentsociety2.readthedocs.io/zh_CN/latest/)

> **AgentSociety 2** is a modern, LLM-native agent simulation platform designed for social science research and experimentation.

## Features

- **LLM-Native Design**: Built from the ground up for LLM-driven agents
- **Flexible Environment System**: Modular environment components with hot-pluggable tools
- **Multiple Reasoning Patterns**: ReAct, Plan-Execute, Code Generation, and Two-Tier routers
- **Developer-Friendly**: Pythonic API with type hints and comprehensive documentation
- **Experiment Replay**: Full SQLite-based replay system for analysis and debugging
- **MCP Support**: Model Context Protocol integration for tool extensibility

## Installation

```bash
pip install agentsociety2
```

### Requirements

- Python >= 3.11
- An LLM API key (OpenAI, Anthropic, or any provider supported by litellm)

## Quick Start

### Create Your First Agent

```python
import asyncio
from datetime import datetime
from agentsociety2 import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.contrib.env import SimpleSocialSpace
from agentsociety2.society import AgentSociety

async def main():
    # Create an agent with a profile
    agent = PersonAgent(
        id=1,
        profile={
            "name": "Alice",
            "age": 28,
            "personality": "friendly and curious",
            "bio": "A software engineer who loves hiking and reading."
        }
    )

    # Create environment module with agent info
    social_env = SimpleSocialSpace(
        agent_id_name_pairs=[(agent.id, agent.name)]
    )

    # Create environment router
    env_router = CodeGenRouter(env_modules=[social_env])

    # Create the society
    society = AgentSociety(
        agents=[agent],
        env_router=env_router,
        start_t=datetime.now(),
    )

    # Initialize (sets up agents with environment)
    await society.init()

    # Query (read-only)
    response = await society.ask("What's your favorite activity?")
    print(f"Agent: {response}")

    # Close the society
    await society.close()

if __name__ == "__main__":
    asyncio.run(main())
```

### Create a Custom Environment Module

```python
from agentsociety2.env import EnvBase, tool

class MyCustomEnvironment(EnvBase):
    """A custom environment module."""

    @tool(readonly=True, kind="observe")
    def get_weather(self, agent_id: int) -> str:
        """Get the current weather for an agent."""
        return "The weather is sunny and 25°C."

    @tool(readonly=False)
    def set_mood(self, agent_id: int, mood: str) -> str:
        """Change the mood of an agent."""
        return f"Agent {agent_id}'s mood is now {mood}."

# Use the custom module
from agentsociety2.env import ReActRouter

env = ReActRouter()
env.register_module(MyCustomEnvironment())
```

### Run a Complete Experiment

```python
import asyncio
from datetime import datetime
from pathlib import Path
from agentsociety2 import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.contrib.env import SimpleSocialSpace
from agentsociety2.storage import ReplayWriter
from agentsociety2.society import AgentSociety

async def main():
    # Setup replay writer for environment dataset tracking
    writer = ReplayWriter(Path("my_experiment.db"))
    await writer.init()

    # Create agents first (needed for SimpleSocialSpace)
    agents = [
        PersonAgent(id=i, profile={"name": f"Player{i}", "personality": "friendly"})
        for i in range(1, 4)
    ]

    # Create environment router
    env_router = CodeGenRouter(
        env_modules=[SimpleSocialSpace(
            agent_id_name_pairs=[(a.id, a.name) for a in agents]
        )],
        replay_writer=writer,
    )

    # Create the society
    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=datetime.now(),
        replay_writer=writer,
    )
    await society.init()

    # Query (read-only)
    answer = await society.ask("What are the names of all agents?")
    print(f"Answer: {answer}")

    # Intervene (read-write)
    result = await society.intervene("Set all agents' happiness to 0.8")
    print(f"Result: {result}")

    await society.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Core Concepts

### Agents

Agents are autonomous entities that interact with environments through LLM-powered reasoning:

- **AgentBase**: Abstract base class for all agents
- **PersonAgent**: Skills-based agent — a lightweight orchestrator whose capabilities are provided by a pluggable skill pipeline
- Agents support two interaction modes:
  - `ask(question, readonly=True)`: Query without side effects
  - `intervene(instruction)`: Make changes to the environment

#### Agent Skills

PersonAgent follows a **metadata-first, selected-only** model. Skills are self-contained directories under `agent/skills/`:

```
agent/skills/
├── observation/        # SKILL.md + scripts/observation.py
├── memory/             # SKILL.md + scripts/memory.py
├── needs/              # SKILL.md + scripts/needs.py
├── cognition/          # SKILL.md + scripts/cognition.py
└── plan/               # SKILL.md + scripts/plan.py
```

Each skill has:
- `SKILL.md` — YAML frontmatter (name, description, priority, requires/provides) + behavior docs
- `scripts/<name>.py` — exports `async def run(agent, ctx)`

Skills follow metadata-first selection:
- selection stage reads compact metadata (name/description/priority/requires/provides)
- execution stage loads and runs only LLM-selected skills (unselected skills do not run)

Custom skills can be placed in `workspace/custom/skills/` and hot-loaded at runtime via the API or VSCode extension.

### Environment Modules

Environment modules encapsulate specific functionality through tools:

- **EnvBase**: Base class for creating custom modules
- **@tool decorator**: Register methods as discoverable tools
- Tool kinds:
  - `observe`: Single-parameter observation functions
  - `statistics`: No-parameter aggregation functions
  - Regular tools: Full read/write operations

### Routers

Routers mediate agent-environment interactions using different reasoning patterns:

- **ReActRouter**: Reasoning + Acting loop
- **PlanExecuteRouter**: Plan-first, then execute
- **CodeGenRouter**: Code generation based tool use
- **TwoTierReActRouter**: Two-level reasoning hierarchy
- **TwoTierPlanExecuteRouter**: Two-level planning hierarchy

### Storage

AgentSociety 2 currently has two persistence paths:

```python
from agentsociety2.storage import ReplayWriter
from pathlib import Path

writer = ReplayWriter(Path("experiment.db"))
await writer.init()

# Replay catalog tables (auto-created):
# - replay_dataset_catalog
# - replay_column_catalog

# Environment modules can register and write their own replay tables.
from agentsociety2.storage import ColumnDef, TableSchema
schema = TableSchema(
    name="custom_metrics",
    columns=[
        ColumnDef("metric_id", "INTEGER", nullable=False),
        ColumnDef("value", "REAL"),
    ],
    primary_key=["metric_id"],
)
await writer.register_table(schema)
```

- **ReplayWriter / SQLite**: stores environment replay datasets plus dataset/column catalog metadata.
- **PersonAgent workspace**: stores per-agent local files under `run/agents/agent_xxxx/`, such as `agent_config.json`, `session_state.json`, `tool_calls.jsonl`, and `thread_messages.jsonl`.

Legacy SQLite tables like `agent_profile`, `agent_status`, and `agent_dialog` are kept only for compatibility when reading old experiment databases; new runs no longer write them.

## Configuration

Set your LLM API credentials via environment variables:

**Required Configuration**

```bash
# Default LLM (required - used for most operations)
export AGENTSOCIETY_LLM_API_KEY="your-api-key"
export AGENTSOCIETY_LLM_API_BASE="https://api.openai.com/v1"
export AGENTSOCIETY_LLM_MODEL="gpt-5.4"
```

**Optional Configuration**

For specialized tasks, you can configure separate LLM instances:

```bash
# Code Generation LLM (for code-related tasks)
# Falls back to default LLM if not set
export AGENTSOCIETY_CODER_LLM_API_KEY="your-coder-api-key"
export AGENTSOCIETY_CODER_LLM_API_BASE="https://api.openai.com/v1"
export AGENTSOCIETY_CODER_LLM_MODEL="gpt-5.4"

# Nano LLM (for high-frequency, low-latency operations)
# Falls back to default LLM if not set
export AGENTSOCIETY_NANO_LLM_API_KEY="your-nano-api-key"
export AGENTSOCIETY_NANO_LLM_API_BASE="https://api.openai.com/v1"
export AGENTSOCIETY_NANO_LLM_MODEL="gpt-5.4-nano"

# Embedding Model (for text embeddings and semantic search)
# Falls back to default LLM if not set
export AGENTSOCIETY_EMBEDDING_API_KEY="your-embedding-api-key"
export AGENTSOCIETY_EMBEDDING_API_BASE="https://api.openai.com/v1"
export AGENTSOCIETY_EMBEDDING_MODEL="text-embedding-3-large"
export AGENTSOCIETY_EMBEDDING_DIMS="1024"

# Data directory (optional, default: ./agentsociety_data)
export AGENTSOCIETY_HOME_DIR="/path/to/your/data"
```

Or use a `.env` file:

```bash
cp .env.example .env
# Edit .env with your credentials
```

.. note::

   The upstream code validates ``AGENTSOCIETY_LLM_API_KEY`` at import time. Make sure it is set
   before importing `agentsociety2` (or load `.env` early in your entrypoint).

## Examples

The `examples/` directory contains ready-to-run examples:

- `basics/`: Basic agent and environment usage
- `games/`: Classic game theory simulations
  - Prisoner's Dilemma
  - Public Goods Game
  - Trust Game
  - Volunteer's Dilemma
  - Commons Tragedy
- `advanced/`: Advanced usage patterns
  - Custom environment modules
  - Multi-router setups
  - Experiment replay and analysis

## Documentation

- [English Documentation](https://agentsociety2.readthedocs.io/)
- [中文文档](https://agentsociety2.readthedocs.io/zh_CN/latest/)
- [API Reference](https://agentsociety2.readthedocs.io/en/latest/api.html)

## Development

For development and contribution guidelines, see [DEVELOPMENT.md](DEVELOPMENT.md).

### Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Citation

If you use AgentSociety 2 in your research, please cite:

```bibtex
@software{agentsociety2,
  title = {AgentSociety 2: A Modern LLM-Native Agent Simulation Platform},
  author = {Zhang, Jun and others},
  year = {2025},
  url = {https://github.com/tsinghua-fib-lab/agentsociety}
}
```

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

## Acknowledgments

AgentSociety 2 builds upon excellent open-source projects:

- [litellm](https://github.com/BerriAI/litellm) - Unified LLM API
- [mem0ai](https://github.com/mem0ai/mem0) - Memory management
- [FastAPI](https://fastapi.tiangolo.com/) - Backend API framework
- [Pydantic](https://docs.pydantic.dev/) - Data validation

## Contact

- **Issues**: [GitHub Issues](https://github.com/tsinghua-fib-lab/agentsociety/issues)
- **Discussions**: [GitHub Discussions](https://github.com/tsinghua-fib-lab/agentsociety/discussions)

---

For the original AgentSociety (v1.x) focused on city simulation, see the [agentsociety package](https://pypi.org/project/agentsociety/).
