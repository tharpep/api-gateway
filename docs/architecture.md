# API Gateway — Architecture

Central BFF: auth, rate limiting, routing to Google APIs, AI providers, and KB service proxy.

**Color key:** 🔵 Callers &nbsp;|&nbsp; 🔴 Auth &nbsp;|&nbsp; 🟢 Routers &nbsp;|&nbsp; 🟣 AI Providers &nbsp;|&nbsp; 🟡 Google OAuth &nbsp;|&nbsp; 🟠 External Services &nbsp;|&nbsp; ◼ KB proxy

```mermaid
flowchart TB
    classDef caller   fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a,font-weight:bold
    classDef authNode fill:#fee2e2,stroke:#ef4444,color:#7f1d1d,font-weight:bold
    classDef mw       fill:#f1f5f9,stroke:#94a3b8,color:#334155
    classDef router   fill:#ccfbf1,stroke:#14b8a6,color:#134e4a,font-weight:bold
    classDef provider fill:#ede9fe,stroke:#8b5cf6,color:#3b0764
    classDef oaNode   fill:#fef3c7,stroke:#f59e0b,color:#78350f
    classDef extSvc   fill:#ffedd5,stroke:#f97316,color:#7c2d12
    classDef kbProxy  fill:#d1fae5,stroke:#10b981,color:#064e3b,font-weight:bold

    subgraph CALLERS["  Callers  "]
        sazed["Sazed agent"]
        kb_svc["knowledge-base"]
        other["Other clients"]
    end

    subgraph GW["  API Gateway — FastAPI  "]

        subgraph MW["  Middleware  "]
            cors["CORS"]
            rate["Rate Limit\nSlowAPI — /ai only"]
            authKey["verify_api_key\nX-API-Key  ·  Bearer\nSkipped: /health, /docs"]
        end

        subgraph RT["  Routers  "]
            health["/health — public"]
            rnotify["/notify"]
            rai["/ai — rate limited"]
            rcal["/calendar"]
            rtasks["/tasks"]
            remail["/email"]
            rstorage["/storage"]
            rkb["/kb — proxy"]
            rctx["/context"]
            rwh["/webhooks"]
        end

        subgraph PRV["  AI Providers  "]
            anth["AnthropicProvider\nclaude-* models"]
            ort["OpenRouterProvider\nall other models"]
        end

        subgraph GOAUTH["  Google OAuth  "]
            goauth["GoogleOAuth\nrefresh_token → access token\ncached, auto-refreshed on 401"]
        end

    end

    subgraph EXT["  External Services  "]
        ANT["Anthropic API"]
        ORT["OpenRouter"]
        GCAL["Google Calendar API"]
        GTASK["Google Tasks API"]
        GMAIL["Gmail API"]
        GDRIVE["Google Drive API"]
        PUSH["Pushover"]
        KBSVC["KB Service\nKB_SERVICE_URL"]
    end

    sazed --> authKey
    kb_svc --> authKey
    other --> authKey

    authKey --> health
    authKey --> rnotify
    authKey --> rai
    authKey --> rcal
    authKey --> rtasks
    authKey --> remail
    authKey --> rstorage
    authKey --> rkb
    authKey --> rctx
    authKey --> rwh

    rai --> anth
    rai --> ort
    anth --> ANT
    ort --> ORT

    rcal --> goauth
    rtasks --> goauth
    remail --> goauth
    rstorage --> goauth

    goauth --> GCAL
    goauth --> GTASK
    goauth --> GMAIL
    goauth --> GDRIVE

    rnotify --> PUSH
    rkb -->|"forward method + body\nX-API-Key: KB_SERVICE_KEY\n503 if not configured"| KBSVC

    class sazed,kb_svc,other caller
    class cors,rate mw
    class authKey authNode
    class health,rnotify,rai,rcal,rtasks,remail,rstorage,rkb,rctx,rwh router
    class anth,ort provider
    class goauth oaNode
    class ANT,ORT,GCAL,GTASK,GMAIL,GDRIVE,PUSH extSvc
    class KBSVC kbProxy

    style CALLERS  fill:#eff6ff,stroke:#3b82f6,color:#1e3a8a
    style GW       fill:#f8fafc,stroke:#cbd5e1,color:#0f172a
    style MW       fill:#f1f5f9,stroke:#cbd5e1,color:#334155
    style RT       fill:#f0fdfa,stroke:#14b8a6,color:#134e4a
    style PRV      fill:#faf5ff,stroke:#8b5cf6,color:#3b0764
    style GOAUTH   fill:#fffbeb,stroke:#f59e0b,color:#78350f
    style EXT      fill:#fff7ed,stroke:#f97316,color:#7c2d12
```

---

### Request flow

| Route | Behavior |
|---|---|
| `GET /health` | Always public — no auth required |
| `POST /ai/*` | Model routing: `claude-*` → Anthropic or OpenRouter fallback; all others → OpenRouter. Rate-limited per IP via SlowAPI. |
| `/calendar` `/tasks` `/email` `/storage` | `GoogleOAuth.refresh_token()` → cached access token; call respective Google API. Token auto-refreshed on 401. |
| `/kb/*` | Transparent proxy: forward method, path, query params, body to `KB_SERVICE_URL/v1{path}`. 503 if `KB_SERVICE_URL` not set, 504 on timeout. |
| `/notify` | Pushover push notification via API token + user key. |
