#!/usr/bin/env python3
"""
Local Fusion runner for opencode-go.

Fan out one prompt to multiple model branches, ask a judge model to rank
the branch outputs, then have a synthesizer model produce one final answer.

Calls opencode-go directly (https://opencode.ai/zen/go/v1) — no local proxy.
Reads the API key from ~/Downloads/opencode.txt (Windows) or $OPENCODE_GO_KEY.

Usage:
  fusion.py <config-name> <prompt>
  fusion.py review-panel "Review the auth refactor for correctness risks."

Configs live in fusion.json next to this script.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

BASE = "https://opencode.ai/zen/go/v1"
HERE = os.path.dirname(os.path.abspath(__file__))

ANTHROPIC_MODELS = {
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus",
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_key():
    key = os.environ.get("OPENCODE_GO_KEY")
    if key:
        return key.strip()
    local_key = os.path.join(HERE, ".key")
    if os.path.exists(local_key):
        with open(local_key) as f:
            return f.read().strip()
    for path in (
        "/mnt/c/Users/vstal/Downloads/opencode.txt",
        os.path.expanduser("~/Downloads/opencode.txt"),
        os.path.expanduser("~/opencode.txt"),
    ):
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
    sys.exit("ERROR: no opencode-go key. Set OPENCODE_GO_KEY, create .key next to fusion.py, or ~/Downloads/opencode.txt")


def load_configs():
    cfg_path = os.environ.get("FUSION_CONFIG", os.path.join(HERE, "fusion.json"))
    with open(cfg_path) as f:
        return json.load(f)


def call_model(model, system, user, key, timeout_ms, max_tokens=2048):
    """Call opencode-go. Routes to OpenAI-shape or Anthropic-shape endpoint."""
    timeout = max((timeout_ms or 120000) / 1000, 30)
    is_anthropic = any(model.startswith(m.split("-")[0] + "-") or model == m
                       for m in ANTHROPIC_MODELS) or model in ANTHROPIC_MODELS
    if model in ANTHROPIC_MODELS:
        is_anthropic = True
    elif model.startswith(("minimax-", "qwen3.")):
        is_anthropic = True
    else:
        is_anthropic = False

    ua = "opencode-fusion/1.0 (+https://opencode.ai)"
    if is_anthropic:
        url = f"{BASE}/messages"
        headers = {
            "x-api-key": key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "User-Agent": ua,
        }
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            body["system"] = system
        payload = json.dumps(body).encode()
    else:
        url = f"{BASE}/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": ua,
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
        }
        payload = json.dumps(body).encode()

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:300]
            last_err = f"HTTP {e.code}: {err}"
            if e.code in (502, 503, 429) and attempt == 0:
                time.sleep(2)
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                continue
            return None, last_err
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
    else:
        return None, last_err or "request failed"

    if is_anthropic:
        texts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        out = "".join(texts)
        return (out, None) if out else (None, "empty output")
    else:
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            out = msg.get("content", "") or msg.get("reasoning_content", "") or ""
            return (out, None) if out else (None, "empty output")
    return None, "empty response"


def run_branch(branch, prompt, key, label):
    model = branch["model"]
    sys_prompt = branch.get("prompt", "")
    tmo = branch.get("timeout", 120000)
    t0 = time.time()
    out, err = call_model(model, sys_prompt, prompt, key, tmo, max_tokens=4096)
    elapsed = round(time.time() - t0, 1)
    if err:
        return label, model, None, err, elapsed
    return label, model, out, None, elapsed


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: fusion.py <config-name> <prompt>")
    config_name = sys.argv[1]
    prompt = " ".join(sys.argv[2:])

    key = load_key()
    configs = load_configs()

    if config_name not in configs:
        avail = ", ".join(configs.keys())
        sys.exit(f"ERROR: config '{config_name}' not found. Available: {avail}")
    cfg = configs[config_name]

    branches = cfg.get("branches", [])
    judge = cfg.get("judge", {})
    synthesizer = cfg.get("synthesizer", {})
    limits = cfg.get("limits", {})
    max_branches = limits.get("maxBranches", 4)
    overall_timeout = limits.get("timeout", 180000)

    if len(branches) > max_branches:
        log(f"WARNING: {len(branches)} branches exceed maxBranches={max_branches}, trimming.")
        branches = branches[:max_branches]
    if not branches:
        sys.exit("ERROR: no branches configured")
    if not judge or not synthesizer:
        sys.exit("ERROR: config needs judge and synthesizer")

    log(f"=== fusion: {config_name} ===")
    log(f"prompt: {prompt[:120]}{'...' if len(prompt)>120 else ''}")
    log(f"branches: {len(branches)} | judge: {judge.get('model')} | synth: {synthesizer.get('model')}")
    log(f"budget: {overall_timeout}ms overall, maxBranches={max_branches}")
    log("")

    t_start = time.time()
    deadline = t_start + overall_timeout / 1000

    labels = [chr(ord('A') + i) for i in range(len(branches))]
    results = {}
    with ThreadPoolExecutor(max_workers=len(branches)) as pool:
        futures = {
            pool.submit(run_branch, b, prompt, key, labels[i]): i
            for i, b in enumerate(branches)
        }
        for fut in as_completed(futures, timeout=max(overall_timeout / 1000, 30)):
            label, model, out, err, elapsed = fut.result()
            status = "ok" if out else f"FAIL: {err}"
            log(f"  [{label}] {model} ({elapsed}s) -> {status}")
            if out:
                results[label] = {"model": model, "output": out}

    if not results:
        sys.exit("ERROR: all branches failed")

    elapsed_so_far = time.time() - t_start
    remaining = max(deadline - time.time(), 10)
    log("")
    log(f"branches done in {round(elapsed_so_far,1)}s | judging...")

    branch_block = "\n\n".join(
        f"=== Branch {label} ({results[label]['model']}) ===\n{results[label]['output']}"
        for label in sorted(results)
    )
    judge_user = (
        f"ORIGINAL PROMPT:\n{prompt}\n\n"
        f"BRANCH OUTPUTS:\n{branch_block}\n\n"
        f"Rank the branch outputs. For each branch give: rank (1=best), "
        f"one-line strength, one-line weakness. Then name the single strongest branch."
    )
    judge_out, jerr = call_model(
        judge["model"], judge.get("prompt", ""), judge_user, key,
        int(remaining * 1000), max_tokens=1024,
    )
    if jerr:
        log(f"  judge FAIL: {jerr} — synthesizing from branches directly")
        judge_out = "Judge unavailable. Synthesize from branch outputs directly."
    else:
        log(f"  judge {judge['model']} -> ok")

    log(f"synthesizing with {synthesizer.get('model')}...")
    synth_user = (
        f"ORIGINAL PROMPT:\n{prompt}\n\n"
        f"BRANCH OUTPUTS:\n{branch_block}\n\n"
        f"JUDGE RANKING:\n{judge_out}\n\n"
        f"Produce ONE final answer that combines the strongest findings. "
        f"Be concise and concrete. Include next steps where useful."
    )
    synth_out, serr = call_model(
        synthesizer["model"], synthesizer.get("prompt", ""), synth_user, key,
        int(max(deadline - time.time(), 20) * 1000), max_tokens=2048,
    )
    if serr:
        log(f"  synth FAIL: {serr} — returning best branch + judge")
        print(branch_block)
        print("\n--- JUDGE ---\n" + judge_out)
        sys.exit(1)

    total = round(time.time() - t_start, 1)
    log("")
    log(f"=== fusion complete: {total}s | {len(results)} branches | judge+synth ===")
    print(synth_out)


if __name__ == "__main__":
    main()
