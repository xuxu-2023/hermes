# Redundant Merge Principle

## Core Rule

**Same capability + different implementation = REDUNDANT/FALLBACK, NOT duplicate.**

User explicitly stated: "能力一样实现方法不一样应该合并作为冗余方案，而不是判断无需入库"

Two skills solving the same problem with different tools/libraries/approaches are NOT duplicates. They are redundant implementations providing:
- Fallback if one approach breaks
- Different tradeoffs (speed vs accuracy, local vs cloud, free vs paid)
- Broader coverage across environments

## Decision Matrix

| Capability | Implementation | Action | Rationale |
|-----------|---------------|--------|-----------|
| Same | Different | `merge_as_redundant` | Preserve both as fallback options |
| Same | Same (high similarity >0.5) | `skip_duplicate` | True duplicate, discard |
| Same | Same (low similarity) | `merge_as_module` | Related but different focus |
| Different | Any | `add_new` | New capability |

## Implementation Detection

Keywords that signal different implementations:
- RAG: `langchain` vs `llamaindex` vs `haystack`
- Browser: `playwright` vs `selenium` vs `puppeteer` vs `camoufox`
- Container: `docker` vs `podman` vs `kubernetes`
- Web: `fastapi` vs `flask` vs `django` vs `streamlit`
- ML: `pytorch` vs `tensorflow` vs `jax` vs `mlx`
- Vector DB: `qdrant` vs `chroma` vs `milvus` vs `pinecone`
- LLM: `openai` vs `anthropic` vs `google` vs `ollama` vs `vllm`
- HTTP: `curl` vs `requests` vs `httpx` vs `aiohttp`

## Example

```
Skill A: rag-engineer (LangChain-based RAG architecture)
Skill B: rag-implementation (LlamaIndex-based RAG implementation)

→ Action: merge_as_redundant into rag-suite
  - rag-suite/references/engineer.md (primary)
  - rag-suite/references/redundant_rag_impl.md (fallback)
  - rag-suite/modules.json includes both + redundant_implementations[]
```

## modules.json Redundant Entry

```json
{
  "skill": "rag-suite",
  "modules": { ... },
  "redundant_implementations": [
    {
      "name": "rag-implementation",
      "source": "rag-implementation",
      "added": "2026-06-16T...",
      "file": "references/redundant_rag_implementation.md",
      "impl_diff": "Uses LlamaIndex instead of LangChain"
    }
  ]
}
```
