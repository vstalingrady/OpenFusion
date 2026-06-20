---
name: fusion
description: Fan out one prompt to multiple opencode-go model branches, judge the results, and synthesize one final answer. Use when the cost of a shallow single-model answer is higher than extra model calls — code reviews, architecture tradeoffs, migration planning, self-critique of a plan, or any high-stakes decision needing independent perspectives. Trigger keywords: fusion, review panel, local fusion, self-critique, pressure-test, multiple models, second opinion, red-team.
---

# Fusion — Local compound model orchestration

Fusion sends the same prompt to several opencode-go model branches in parallel, asks a judge model to rank the branch outputs, then has a synthesizer model collapse them into one final answer. It calls opencode-go directly (`https://opencode.ai/zen/go/v1`) — no local proxy.

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
python3 ~/.config/opencode/skills/fusion/fusion.py <config-name> <prompt>
```

The final synthesized answer prints to **stdout** (use it directly). Progress, branch status, and timing print to **stderr**.

### Named configs

Configs live in `~/.config/opencode/skills/fusion/fusion.json`. Three ship by default:

| Config | Branches | Best for |
|--------|----------|----------|
| `review-panel` | glm-5.2 (correctness) + deepseek-v4-pro (product/tests) + minimax-m3 (simpler path) | Code review, PR review |
| `architecture` | glm-5.2 (maintainability) + deepseek-v4-pro (opposing side) + minimax-m3 (cheapest build) | Architecture decisions |
| `self-critique` | glm-5.2 (correctness) + deepseek-v4-pro (market) + minimax-m3 (execution risk) | Pressure-test a plan or decision |

All configs use **glm-5.2** as both judge and synthesizer.

### Example

```bash
python3 ~/.config/opencode/skills/fusion/fusion.py review-panel "Review the auth refactor in src/auth.ts. Identify correctness risks, security regressions, missing tests, and any simpler implementation path. Return prioritized findings with file references."
```

```bash
python3 ~/.config/opencode/skills/fusion/fusion.py self-critique "Plan: 14-day cold-DM outreach to Shopee sellers for a Rp 1.5jt money-leak audit. Zero budget, zero network. Critique for correctness, market risk, and execution risk."
```

## Config shape

Each named config matches the panwar-stack `local_fusion` schema:

```json
{
  "review-panel": {
    "branches": [
      { "model": "glm-5.2", "prompt": "Focus on...", "timeout": 120000 }
    ],
    "judge": { "model": "deepseek-v4-flash", "prompt": "Rank by..." },
    "synthesizer": { "model": "glm-5.2", "prompt": "Combine into..." },
    "limits": { "timeout": 180000, "maxBranches": 4 }
  }
}
```

- `branches[].model` — any opencode-go model id (e.g. `glm-5.2`, `deepseek-v4-flash`, `kimi-k2.6`, `minimax-m3`). Use the bare id, NOT the `opencode-go/` prefix.
- `branches[].prompt` — the angle/instruction for that branch.
- `branches[].timeout` — per-branch ms budget.
- `judge` / `synthesizer` — single model each, run after branches complete.
- `limits.timeout` — overall orchestration budget (ms). `limits.maxBranches` — caps branch count.

## Available opencode-go models

GLM-5, GLM-5.1, GLM-5.2, Kimi K2.5/K2.6/K2.7-code, DeepSeek V4 Pro/Flash, MiMo V2.5/V2.5-Pro/V2-Pro/V2-Omni, MiniMax M3/M2.7/M2.5, Qwen3.7 Max/Plus, Qwen3.6/3.5 Plus, HY3-preview. Run `curl -s https://opencode.ai/zen/go/v1/models -H "Authorization: Bearer $(cat ~/Downloads/opencode.txt)"` for the live list.

## Key + auth

The runner reads the API key from (in order):
1. `$OPENCODE_GO_KEY` env var
2. `~/.config/opencode/skills/fusion/.key` (Linux-side, 600 perms — survives restarts even if WSL /mnt/c mount is slow)
3. `/mnt/c/Users/vstal/Downloads/opencode.txt` (Windows Downloads, WSL fallback)
4. `~/Downloads/opencode.txt`

To rotate the key: `printf '%s' "<new-key>" > ~/.config/opencode/skills/fusion/.key && chmod 600 ~/.config/opencode/skills/fusion/.key`

## Limitations vs the native panwar-stack tool

- **No tool access in branches.** The native `local_fusion` tool can give branches `toolPolicy: "readonly"` to let them read files via opencode. This skill's branches answer from the prompt text only — paste relevant code/context into the prompt so branches can reason about it.
- **`variant` is accepted but ignored** (opencode-internal concept; opencode-go has no variant API).
- **Config is sidecar JSON**, not `opencode.json` — stock opencode rejects the `local_fusion:` top-level key, so configs live in `fusion.json` next to the runner.
