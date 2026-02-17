# API Gateway — Architecture Overview

High-level view for quickly understanding the codebase.

```mermaid
flowchart TB
    subgraph Clients["Clients"]
        Agent["Sazed / Agent"]
        Frontend["Frontend / MCP"]
    end

    subgraph Gateway["api-gateway"]
        API["FastAPI App"]
        Auth["verify_api_key"]
        API --> Auth
    end

    subgraph Routers["Routers (all behind API key)"]
        Health["/health"]
        Notify["/notify"]
        AI["/ai"]
        Cal["/calendar"]
        Tasks["/tasks"]
        Email["/email"]
        Storage["/storage"]
        KB["/kb"]
        Context["/context"]
        Webhooks["/webhooks"]
    end

    subgraph External["External Services"]
        Pushover["Pushover"]
        Google["Google APIs"]
        KBService["KB Service"]
        Anthropic["Anthropic / OpenRouter"]
    end

    Clients --> API
    API --> Routers
    Notify --> Pushover
    Cal --> Google
    Tasks --> Google
    Email --> Google
    Storage --> Google
    KB --> KBService
    AI --> Anthropic
```

**In one sentence:** Single FastAPI app that fronts Google (Calendar, Tasks, Email, Drive), Pushover, AI providers, and the KB service behind one URL and one API key.
