# AgentAtlas

**Shared browser interaction schema registry for AI agents.**

Reduces LLM token usage by 80-100% on known sites by storing and sharing site interaction schemas across all users.

## How it works
```
First user  → LLM learns the site → saved to shared registry
Every user after → 0 tokens, instant response
```

## Benchmark results (real data)

| | Without AgentAtlas | With AgentAtlas |
|---|---|---|
| Tokens | 2,597 | 0-445 |
| Cost | $0.018 | $0.000-$0.002 |
| Time | 19s | 0.2-12s |
| Real URLs | ❌ | ✅ |

**82.9% token reduction** when LLM still needed. **100% reduction** for repeat workflows.

## Install
```bash
pip install agentatlas
playwright install chromium
```

## Usage
```python
from agentatlas.atlas import Atlas

atlas = Atlas()

# Get schema for any site
# Found in registry → 0 tokens
# New site → learns once, saves for everyone
schema = await atlas.get_schema(
    site="greenhouse.io",
    url="https://boards.greenhouse.io/anthropic"
)

# Pass compact schema to YOUR LLM
# 150-500 tokens instead of 50,000
print(schema.elements)
print(schema.tokens_used)  # 0 if registry hit
print(schema.source)       # "registry" or "llm_learned"
```

## Environment variables
```bash
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_key
OPENAI_API_KEY=your_key
```

## The flywheel
```
More developers use AgentAtlas
        ↓
More new sites get learned automatically
        ↓
Registry grows → higher hit rate
        ↓
Less tokens burned across the whole network
        ↓
Cheaper + faster → more developers adopt
```

## License

MIT
