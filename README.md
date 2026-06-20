# OpenFusion

Fan out one prompt to multiple opencode-go model branches, judge the results, and synthesize one final answer.

Use when the cost of a shallow single-model answer is higher than extra model calls — code reviews, architecture tradeoffs, migration planning, self-critique of a plan, or any high-stakes decision needing independent perspectives.

## Quickstart

```bash
pip install -r requirements.txt  # or: nothing, stdlib only
python3 fusion.py review-panel "Review the auth refactor in src/auth.ts"
```

The final synthesized answer prints to **stdout**. Progress prints to **stderr**.

## Built-in configs

| Config | Branches | Best for |
|--------|----------|----------|
| `review-panel` | glm-5.2 + deepseek-v4-pro + minimax-m3 | Code review |
| `architecture` | glm-5.2 + deepseek-v4-pro + minimax-m3 | Architecture decisions |
| `self-critique` | glm-5.2 + deepseek-v4-pro + minimax-m3 | Pressure-test a plan |
| `growth` | glm-5.2 + deepseek-v4-pro + minimax-m3 + glm-5.2 | 14-day marketing plan |

See `fusion.json` for the full config schema and all branch prompts.

## Auth

The runner reads the API key from (in order):

1. `$OPENCODE_GO_KEY` env var
2. `.key` file next to `fusion.py`
3. `~/Downloads/opencode.txt`

Set your key:

```bash
printf '%s' "<your-key>" > .key && chmod 600 .key
```

## Add your own config

Edit `fusion.json` — each config follows this shape:

```json
{
  "my-config": {
    "branches": [
      { "model": "glm-5.2", "prompt": "Focus on...", "timeout": 120000 }
    ],
    "judge": { "model": "deepseek-v4-flash", "prompt": "Rank by..." },
    "synthesizer": { "model": "glm-5.2", "prompt": "Combine into..." },
    "limits": { "timeout": 180000, "maxBranches": 4 }
  }
}
```

Available models: `glm-5.2`, `deepseek-v4-pro`, `deepseek-v4-flash`, `minimax-m3`, `kimi-k2.6`, and more. Run `curl -s https://opencode.ai/zen/go/v1/models -H "Authorization: Bearer <key>"` for the live list.

## How it works

1. **Branches** — 3-4 models answer the same prompt in parallel, each with a different angle
2. **Judge** — a separate model ranks the branches by correctness, specificity, and risk coverage
3. **Synthesizer** — a final model combines the strongest findings into one answer

## License

MIT
