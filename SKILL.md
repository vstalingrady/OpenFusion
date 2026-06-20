---
name: fusion
description: Fan out one prompt to multiple models, judge the results, and synthesize one final answer. Use when the cost of a shallow single-model answer is higher than extra model calls — code reviews, architecture tradeoffs, migration planning, self-critique of a plan, or any high-stakes decision needing independent perspectives. Trigger keywords: fusion, review panel, self-critique, pressure-test, multiple models, second opinion, red-team.
---

# Fusion — compound model orchestration

Fusion sends the same prompt to several model branches in parallel, asks a judge model to rank the branch outputs, then has a synthesizer model collapse them into one final answer.

## When to use

- **Code review** of a non-trivial change (correctness + product + simpler-path angles)
- **Architecture tradeoffs** (one branch argues for, one against, one for cheapest build)
- **Self-critique / red-team** of a plan before committing
- **Migration planning** where a missed risk is expensive

Do NOT use for routine edits or small questions — it multiplies model calls ~5x (N branches + judge + synth).

## How to run

```bash
python3 fusion.py <config-name> <prompt>
```

Final answer prints to **stdout**. Progress prints to **stderr**.

### Custom panel at runtime

```bash
python3 fusion.py --models "provider/m1,provider/m2,provider/m3" [--judge provider/mJ --synth provider/mS] <prompt>
```

## Configuration

Everything is in `fusion.json` next to the runner:

- **providers** — endpoint URLs, API shape (`openai`/`claude`/`gemini`), auth style, key env var. Add any compatible endpoint.
- **configs** — named presets. Each has `branches` (models + their angle prompts), `judge`, `synthesizer`, and `limits`.

Models are always `provider/model` format (e.g. `openai/gpt-5.5`, `anthropic/claude-sonnet-4-6`).

## Auth

Keys read from the env var each provider declares (`key_env` in fusion.json), or `.key` / `.key.<provider>` files next to `fusion.py`.

## Custom config file

```bash
FUSION_CONFIG=~/.config/my-fusions.json python3 fusion.py my-config "..."
```
