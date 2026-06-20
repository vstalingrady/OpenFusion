# OpenFusion

> Fan one prompt out to multiple models, judge the results, synthesize one final answer.

![Goku fusion dance](https://media.giphy.com/media/d3mlE7uhX8KFgWlI/giphy.gif)

OpenFusion sends your prompt to several models in parallel, asks a judge model to rank the outputs, then a synthesizer model combines them into one answer. Useful for code reviews, architecture tradeoffs, migration planning, or any decision where you want independent perspectives before committing.

## Quickstart

```bash
git clone https://github.com/vstalingrady/OpenFusion.git
cd OpenFusion

# set your API keys
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."

# add your models to fusion.json, then:
python3 fusion.py my-config "Review the auth refactor in src/auth.ts"
```

The final answer prints to **stdout**. Progress prints to **stderr**.

No dependencies. Pure Python 3 stdlib.

## How it works

```
            ┌──────────┐   ┌──────────┐   ┌──────────┐
 prompt ──> │ Branch A │   │ Branch B │   │ Branch C │   (parallel)
            └────┬─────┘   └────┬─────┘   └────┬─────┘
                 └──────┬───────┴──────────┘
                        v
                  ┌──────────┐
                  │  Judge   │
                  └────┬─────┘
                       v
                  ┌────────────┐
                  │ Synthesizer│ ──> final answer
                  └────────────┘
```

1. **Branches** — N models answer the same prompt in parallel, each with its own angle.
2. **Judge** — ranks the branch outputs.
3. **Synthesizer** — combines the strongest findings into one answer.

## Configuration

Everything lives in `fusion.json`. Two sections: **providers** (endpoint + auth) and **configs** (which models to fuse).

### Providers

```json
{
  "providers": {
    "openai": {
      "base": "https://api.openai.com/v1",
      "shape": "openai",
      "auth": "bearer",
      "key_env": "OPENAI_API_KEY"
    },
    "anthropic": {
      "base": "https://api.anthropic.com/v1",
      "shape": "claude",
      "auth": "x-api-key",
      "key_env": "ANTHROPIC_API_KEY",
      "headers": { "anthropic-version": "2023-06-01" }
    }
  }
}
```

- `base` — API base URL (no endpoint path; fusion.py appends `/chat/completions`, `/messages`, or `/models/{model}:generateContent` based on shape).
- `shape` — `openai`, `claude`, or `gemini`. Determines request/response format.
- `auth` — `bearer`, `x-api-key`, or `x-goog-api-key`.
- `key_env` — env var name to read the key from.
- `headers` — any extra headers (optional).

Add any OpenAI/Anthropic/Gemini-compatible endpoint here — self-hosted, OpenRouter, anything.

### Configs

```json
{
  "configs": {
    "my-review": {
      "branches": [
        { "model": "anthropic/claude-sonnet-4-6", "prompt": "Focus on correctness.", "timeout": 120000 },
        { "model": "openai/gpt-5.5", "prompt": "Focus on user impact.", "timeout": 120000 },
        { "model": "gemini/gemini-3.1-pro-preview", "prompt": "Find a simpler path.", "timeout": 120000 }
      ],
      "judge": { "model": "openai/gpt-5.4-mini", "prompt": "Rank by correctness and specificity." },
      "synthesizer": { "model": "anthropic/claude-sonnet-4-6", "prompt": "Combine into one answer." },
      "limits": { "timeout": 180000, "maxBranches": 4 }
    }
  }
}
```

- `branches[].model` — always `provider/model` format (e.g. `openai/gpt-5.5`).
- `branches[].prompt` — the angle/instruction for that branch.
- `judge` / `synthesizer` — single model each.
- `limits.timeout` — overall budget in ms. `limits.maxBranches` — max branches.

### Custom panel at runtime

Skip the config file and pass models directly:

```bash
python3 fusion.py --models "openai/gpt-5.5,anthropic/claude-sonnet-4-6,gemini/gemini-3.1-pro-preview" "your prompt"
python3 fusion.py --models "openai/gpt-5.5,anthropic/claude-sonnet-4-6" --judge "openai/gpt-5.4-mini" --synth "anthropic/claude-sonnet-4-6" "your prompt"
```

## Auth

Keys are read from the env var each provider declares (`key_env`), or from key files next to `fusion.py`:

```bash
printf '%s' "<key>" > .key                    # fallback for any provider
printf '%s' "<key>" > .key.openai             # provider-specific
```

## Related

- **[panwar-stack/opencode](https://github.com/panwar-stack/opencode)** — native `local_fusion` tool with readonly tool access for branches.
- **[OpenRouter Fusion](https://openrouter.ai/fusion)** — server-side compound model as a single slug.
- **[OmniRoute](https://github.com/diegosouzapw/OmniRoute)** — multi-provider gateway that inspired the API-shape handling.

## License

MIT
