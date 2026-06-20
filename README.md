# OpenFusion

> Fan out one prompt to multiple local model branches, judge the results, and synthesize a final answer.

![Goku fusion dance](https://media.giphy.com/media/d3mlE7uhX8KFgWlI/giphy.gif)

OpenFusion is a **compound model workflow** for moments when one model answer is not enough. It sends the same prompt to several configured branches (each can be a different model from a different provider), asks a judge model to compare the branch outputs, then has a synthesizer model produce one final response.

That makes it useful for **code reviews, architecture tradeoffs, migration planning, self-critique of a plan**, or any task where you want independent perspectives before committing to an answer.

---

## Why

A single model can be confidently wrong. A **panel** of models, each attacking the problem from a different angle, surfaces risks a solo answer misses. Research from [OpenRouter's Fusion benchmark](https://openrouter.ai/blog/announcements/fusion-beats-frontier/) found:

- Panels of models consistently outperform individual models
- A panel of budget models can surpass a frontier model at a fraction of the cost
- ~75% of the lift comes from **synthesis**, ~25% from **diversity**

OpenFusion brings that same idea to your local workflow — you pick the models, you pick the angles, you own the keys. No server-side proxy, no per-token markup.

### Related work

- **[panwar-stack/opencode](https://github.com/panwar-stack/opencode)** — ships a native `local_fusion` tool with `toolPolicy: "readonly"` branches that can read your codebase. OpenFusion started as a standalone port of that concept; it trades tool access for provider-agnosticism (any OpenAI / Anthropic / Gemini / OpenRouter endpoint).
- **[OpenRouter Fusion](https://openrouter.ai/fusion)** — server-side compound model exposed as a single slug (`openrouter/fusion`). Great if you want zero setup; OpenFusion is for when you want to control the panel, the prompts, and the providers yourself.

---

## Quickstart

```bash
git clone https://github.com/vstalingrady/OpenFusion.git
cd OpenFusion

# set your API keys (any mix of providers)
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENROUTER_API_KEY="sk-or-..."

python3 fusion.py review-panel "Review the auth refactor in src/auth.ts. Identify correctness risks, security regressions, missing tests, and any simpler implementation path."
```

The final synthesized answer prints to **stdout**. Progress, branch status, and timing print to **stderr**.

> No dependencies. Pure Python 3 stdlib.

---

## How it works

```
            ┌──────────┐   ┌──────────┐   ┌──────────┐
 prompt ──> │ Branch A │   │ Branch B │   │ Branch C │   (parallel)
            └────┬─────┘   └────┬─────┘   └────┬─────┘
                 │              │              │
                 └──────┬───────┴──────┬───────┘
                        │              │
                        v              │
                  ┌──────────┐         │
                  │  Judge   │ <───────┘
                  └────┬─────┘
                       │
                       v
                  ┌────────────┐
                  │ Synthesizer│ ──> final answer (stdout)
                  └────────────┘
```

1. **Branches** — 3-4 models answer the same prompt in parallel, each with a different angle/instruction.
2. **Judge** — a separate model ranks the branch outputs by correctness, specificity, and risk coverage.
3. **Synthesizer** — a final model combines the strongest findings into one answer, deduplicating and leading with the highest-risk item.

---

## Built-in configs

| Config | Branches | Best for |
|--------|----------|----------|
| `review-panel` | Claude Sonnet 4.6 + GPT-5.5 + Gemini 3.1 Pro | Code review, PR review |
| `architecture` | Claude Sonnet 4.6 + GPT-5.5 + Gemini 3.1 Pro | Architecture decisions |
| `self-critique` | Claude Sonnet 4.6 + GPT-5.5 + Gemini 3.1 Pro | Pressure-test a plan or decision |
| `budget-panel` | Llama 3.3 70B (Groq) + Qwen 2.5 Coder (Groq) + Llama 3.3 70B (Cerebras) | Fast, near-free reviews |

See [`fusion.json`](fusion.json) for the full config schema and all branch prompts.

---

## Supported providers

OpenFusion talks to any OpenAI-compatible, Anthropic-compatible, or Google Gemini endpoint. Provider routing is inspired by [OmniRoute](https://github.com/diegosouzapw/OmniRoute).

| Provider | API shape | Key env var | Auto-detected model prefixes |
|----------|-----------|-------------|------------------------------|
| OpenAI | OpenAI | `OPENAI_API_KEY` | `gpt-` |
| Anthropic | Claude | `ANTHROPIC_API_KEY` | `claude-` |
| Google Gemini | Gemini | `GEMINI_API_KEY` | `gemini-`, `gemma-` |
| OpenRouter | OpenAI | `OPENROUTER_API_KEY` | — (use `openrouter/vendor/model`) |
| Groq | OpenAI | `GROQ_API_KEY` | — |
| xAI | OpenAI | `XAI_API_KEY` | — (use `xai/grok-4.3`) |
| Mistral | OpenAI | `MISTRAL_API_KEY` | — (use `mistral/mistral-large-latest`) |
| DeepSeek | OpenAI | `DEEPSEEK_API_KEY` | — (use `deepseek/deepseek-v4-pro`) |
| Together | OpenAI | `TOGETHER_API_KEY` | — |
| Fireworks | OpenAI | `FIREWORKS_API_KEY` | — |
| Cerebras | OpenAI | `CEREBRAS_API_KEY` | — |

Only `gpt-`, `claude-`, `gemini-`, and `gemma-` auto-detect (matching [OmniRoute](https://github.com/diegosouzapw/OmniRoute)'s inference rules). Every other model — `deepseek-v4-pro`, `grok-4.3`, `glm-5.2`, `kimi-k2.6`, `qwen3.6-plus`, `minimax-m3`, `mistral-large-latest`, `llama-3.3-70b` — needs an explicit `provider/model` prefix, because most are served by multiple providers and a bare name would be ambiguous.

### Specifying a model

Use a `provider/model` prefix to be explicit, or let OpenFusion auto-detect from the model name:

```json
"openai/gpt-5.5"                       // explicit provider
"anthropic/claude-sonnet-4-6"          // explicit provider
"gemini/gemini-3.1-pro-preview"        // explicit provider
"openrouter/anthropic/claude-opus-4-7" // OpenRouter routing to Claude
"deepseek/deepseek-v4-pro"             // explicit provider
"gpt-5.5"                              // auto-detected -> openai
"claude-sonnet-4-6"                    // auto-detected -> anthropic
"gemini-3.1-pro-preview"               // auto-detected -> gemini
"deepseek-v4-pro"                      // NO auto-detect -> use deepseek/deepseek-v4-pro
```

---

## Auth

The runner reads keys per-provider from environment variables (see table above). Set the ones for the providers you use:

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENROUTER_API_KEY="sk-or-..."
```

Alternatively, drop a key file next to `fusion.py`:

```bash
printf '%s' "<your-key>" > .key && chmod 600 .key              # default (used as fallback)
printf '%s' "<your-key>" > .key.openai && chmod 600 .key.openai # provider-specific
```

### Using with Claude Code, Codex, Cursor, etc.

OpenFusion is a standalone CLI — it doesn't integrate into any specific editor. But because it calls standard OpenAI / Anthropic / Gemini endpoints, you can point it at any compatible gateway (including [OmniRoute](https://github.com/diegosouzapw/OmniRoute)) by setting the provider base URL in `fusion.py`'s `PROVIDERS` dict, or by adding a custom provider.

To add a custom OpenAI-compatible endpoint, add an entry to `PROVIDERS` in `fusion.py`:

```python
"my-gateway": {
    "base": "https://my-gateway.example.com/v1",
    "format": "openai",
    "auth": "bearer",
    "key_env": "MY_GATEWAY_KEY",
},
```

Then reference it in a config: `"my-gateway/claude-sonnet-4-5"`.

---

## Add your own config

Edit `fusion.json` — each config follows this shape:

```json
{
  "my-config": {
    "branches": [
      { "model": "anthropic/claude-sonnet-4-6", "prompt": "Focus on...", "timeout": 120000 },
      { "model": "openai/gpt-5.5", "prompt": "Argue against...", "timeout": 120000 },
      { "model": "gemini/gemini-3.1-pro-preview", "prompt": "Find a simpler path...", "timeout": 120000 }
    ],
    "judge": { "model": "openai/gpt-5.4-mini", "prompt": "Rank by..." },
    "synthesizer": { "model": "anthropic/claude-sonnet-4-6", "prompt": "Combine into..." },
    "limits": { "timeout": 180000, "maxBranches": 4 }
  }
}
```

**Fields:**

- `branches[].model` — any model id. Use `provider/model` to be explicit, or a bare name for auto-detection.
- `branches[].prompt` — the angle/instruction for that branch.
- `branches[].timeout` — per-branch ms budget.
- `judge` / `synthesizer` — single model each, run after branches complete.
- `limits.timeout` — overall orchestration budget (ms).
- `limits.maxBranches` — caps branch count.

Point to a custom config file with `FUSION_CONFIG`:

```bash
FUSION_CONFIG=~/.config/my-fusions.json python3 fusion.py my-config "..."
```

---

## When to use

Use OpenFusion when the cost of a shallow answer is higher than the cost of extra model calls:

- **Code review** of a non-trivial change (correctness + product + simpler-path angles)
- **Architecture tradeoffs** (one branch argues for, one against, one for cheapest build)
- **Self-critique / red-team** of a plan before committing
- **Migration planning** where a missed risk is expensive

Do **not** use OpenFusion for routine edits, small questions, or anything where a single model response is fast and sufficient — it multiplies model calls ~5x (3 branches + judge + synth).

---

## Limitations

- **No tool access in branches.** Branches answer from the prompt text only — paste relevant code/context into the prompt so branches can reason about it. (The native panwar-stack `local_fusion` tool supports `toolPolicy: "readonly"` for file access; this standalone version does not.)
- **Non-streaming.** The final synthesized answer prints all at once when complete.
- **No automatic retries across providers.** If a branch fails, it's dropped; the judge and synthesizer work with whatever branches succeeded.

---

## License

MIT
