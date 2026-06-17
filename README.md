# Agentic AI Platform

A modular, extensible platform for developing and deploying specialized AI agents, originally designed as an intelligent Telegram assistant.

## Architectural Overview

This project has been refactored from a monolithic script into a clean, agent-based architecture designed for maintainability and scalability. The system utilizes an **Orchestrator** pattern to route user requests to specialized **Sub-agents**, separating interface logic from domain-specific AI reasoning.

### Key Engineering Decisions

- **Orchestrator Pattern**: Centralized routing system that delegates tasks to specialized sub-agents based on the user's selected mode (Nutrition, Math, General).
- **Base Agent Abstraction**: Defines a uniform interface for all agents, ensuring the system can be easily extended with new capabilities without modifying the core logic.
- **Resilience**: Implements automatic API key rotation and fallback mechanisms (Gemini API to OpenRouter) to ensure high availability.
- **Persistence & Telemetry**: Uses an asynchronous SQLite database to maintain user context (history management) and record interaction metrics (latency, token usage) for performance monitoring.
- **Docker Ready**: Fully containerized to guarantee environment consistency across development, staging, and production environments.

## Project Structure

```text
src/
├── agents/          # Specialized sub-agents (Business logic)
├── handlers/        # Telegram interface event handling
├── orchestrator.py  # Request routing and agent management
├── llm.py           # Infrastructure layer (API interaction, context management)
├── database.py      # Persistence and telemetry storage
└── ...
```

## Getting Started

### Prerequisites
- Python 3.13+
- Docker (optional, for production deployment)

### Configuration
Create a `.env` file in the root directory based on your environment needs:
```text
TELEGRAM_BOT_TOKEN=your_token
GOOGLE_API_KEYS=key1,key2
OPENROUTER_API_KEY=your_key
```

### Running the Application
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the bot:
   ```bash
   python -m src.bot
   ```

## Design Philosophy

The system adheres to the Open-Closed Principle, allowing for the addition of new AI capabilities by simply registering new agents within the orchestrator. The decoupling of the user interface from the AI reasoning engine enables seamless integration of alternative interfaces (e.g., Web API) in the future.
