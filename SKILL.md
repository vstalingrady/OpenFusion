---
name: fusion
description: Fan out one prompt to multiple model branches across any provider (OpenAI, Anthropic, Gemini, OpenRouter, Groq, ...), judge the results, and synthesize one final answer. Use when the cost of a shallow single-model answer is higher than extra model calls — code reviews, architecture tradeoffs, migration planning, self-critique of a plan, or any high-stakes decision needing independent perspectives. Trigger keywords: fusion, review panel, local fusion, self-critique, pressure-test, multiple models, second opinion, red-team.
---

# Fusion — Local compound model orchestration

Fusion sends the same prompt to several model branches in parallel, asks a judge model to rank the branch outputs, then has a synthesizer model collapse them into one final answer. Each branch can use a different model from a different provider — OpenAI, Anthropic, Google Gemini, OpenRouter, Groq, xAI, Mistral, DeepSeek, and more.

## When to use

Use Fusion when one model answer is not enough:
- **Code review** of a non-trivial change (correctness + product + simpler-path angles)
- **Architecture tradeoffs** (one branch argues for, one against, one for cheapest build)
- **Self-critique / red-team** of a plan before committing
- **Migration planning** where a missed risk is expensive

Do NOT use Fusion for routine edits, small questions, or anything where a single model response is fast and sufficient — it multiplies model calls 5x (3 branches + judge + synth).

## How to run

Run the Python runner with a named config and the prompt:

```bash
python3 fusion.py <config-name> <prompt>
```

The final synthesized answer prints to **stdout** (use it directly). Progress, branch status, and timing print to **stderr**.

### Named configs

Configs live in `fusion.json` next to the runner. Four ship by default:

| Config | Branches | Best for |
|--------|----------|----------|
| `review-panel` | Claude Sonnet 4.6 + GPT-5.5 + Gemini 3.1 Pro | Code review, PR review |
| `architecture` | Claude Sonnet 4.6 + GPT-5.5 + Gemini 3.1 Pro | Architecture decisions |
| `self-critique` | Claude Sonnet 4.6 + GPT-5.5 + Gemini 3.1 Pro | Pressure-test a plan or decision |
| `budget-panel` | Llama 3.3 70B (Groq) + Qwen 2.5 Coder (Groq) + Llama 3.3 70B (Cerebras) | Fast, near-free reviews |

### Example

```bash
python3 fusion.py review-panel "Review the auth refactor in src/auth.ts. Identify correctness risks, security regressions, missing tests, and any simpler implementation path. Return prioritized findings with file references."
```

```bash
python3 fusion.py self-critique "Plan: migrate a monolith Rails app to a modular service architecture over 3 months. Critique for correctness, market risk, and execution risk."
```

## Config shape

Each named config follows this schema:

```json
{
  "my-config": {
    "branches": [
      { "model": "anthropic/claude-sonnet-4-6", "prompt": "Focus on...", "timeout": 120000 },
      { "model": "openai/gpt-5.5", "prompt": "Argue against...", "timeout": 120000 }
    ],
    "judge": { "model": "openai/gpt-5.4-mini", "prompt": "Rank by..." },
    "synthesizer": { "model": "anthropic/claude-sonnet-4-6", "prompt": "Combine into..." },
    "limits": { "timeout": 180000, "maxBranches": 4 }
  }
}
```

- `branches[].model` — any model id. Use `provider/model` to be explicit (e.g. `openai/gpt-5.5`, `anthropic/claude-sonnet-4-6`, `gemini/gemini-3.1-pro-preview`), or a bare name for auto-detection (`gpt-5.5`, `claude-sonnet-4-6`, `gemini-3.1-pro-preview`). Only `gpt-`, `claude-`, `gemini-`, `gemma-` auto-detect; everything else (deepseek, grok, glm, kimi, qwen, minimax, mistral, llama, ...) needs an explicit `provider/model` prefix.
- `branches[].prompt` — the angle/instruction for that branch.
- `branches[].timeout` — per-branch ms budget.
- `judge` / `synthesizer` — single model each, run after branches complete.
- `limits.timeout` — overall orchestration budget (ms). `limits.maxBranches` — caps branch count.

## Supported providers

| Provider | Key env var | Auto-detected prefixes |
|----------|-------------|------------------------|
| OpenAI | `OPENAI_API_KEY` | `gpt-` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-` |
| OpenRouter | `OPENROUTER_API_KEY` | — (use `openrouter/vendor/model`) |
| Google Gemini | `GEMINI_API_KEY` | `gemini-`, `gemma-` |
| Groq | `GROQ_API_KEY` | — |
| xAI | `XAI_API_KEY` | — |
| Mistral | `MISTRAL_API_KEY` | — |
| DeepSeek | `DEEPSEEK_API_KEY` | — |
| Together | `TOGETHER_API_KEY` | — |
| Fireworks | `FIREWORKS_API_KEY` | — |
| Cerebras | `CEREBRAS_API_KEY` | — |

## Key + auth

The runner reads keys per-provider from environment variables. Set the ones for the providers you use:

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENROUTER_API_KEY="sk-or-..."
```

Alternatively, drop a key file next to `fusion.py`:

```bash
printf '%s' "<key>" > .key && chmod 600 .key              # default fallback
printf '%s' "<key>" > .key.openai && chmod 600 .key.openai # provider-specific
```

## Custom config file

Point to a custom config with `FUSION_CONFIG`:

```bash
FUSION_CONFIG=~/.config/my-fusions.json python3 fusion.py my-config "..."
```

## Limitations

- **No tool access in branches.** Branches answer from the prompt text only — paste relevant code/context into the prompt so branches can reason about it.
- **Non-streaming.** The final answer prints all at once when complete.
- **No automatic retries across providers.** If a branch fails, it's dropped; the judge and synthesizer work with whatever branches succeeded.
