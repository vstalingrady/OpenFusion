#!/usr/bin/env python3
"""
OpenFusion — local compound model orchestration.

Fan out one prompt to multiple model branches (across any provider),
ask a judge model to rank the branch outputs, then have a synthesizer
model produce one final answer.

Supports any OpenAI-compatible, Anthropic-compatible, Google Gemini, or
OpenRouter endpoint. Provider is auto-detected from the model name, or set
explicitly with a `provider/model` prefix.

Usage:
  fusion.py <config-name> <prompt>
  fusion.py review-panel "Review the auth refactor for correctness risks."

  # custom panel at runtime (comma-separated models):
  fusion.py --models "openai/gpt-5.5,anthropic/claude-sonnet-4-6,gemini/gemini-3.1-pro-preview" "prompt"
  fusion.py --models "m1,m2,m3" --judge "mJ" --synth "mS" "prompt"

Configs live in fusion.json next to this script.
Set the API key via env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...) or a
.key file next to this script.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Provider registry — inspired by OmniRoute's provider config.
# Each entry: base URL, API shape, auth header style, extra headers.
# https://github.com/diegosouzapw/OmniRoute
# ---------------------------------------------------------------------------
PROVIDERS = {
    "openai": {
        "base": "https://api.openai.com/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "base": "https://api.anthropic.com/v1",
        "format": "claude",
        "auth": "x-api-key",
        "key_env": "ANTHROPIC_API_KEY",
        "headers": {"anthropic-version": "2023-06-01"},
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "OPENROUTER_API_KEY",
        "headers": {"HTTP-Referer": "https://github.com/vstalingrady/OpenFusion",
                     "X-Title": "OpenFusion"},
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "format": "gemini",
        "auth": "x-goog-api-key",
        "key_env": "GEMINI_API_KEY",
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "GROQ_API_KEY",
    },
    "xai": {
        "base": "https://api.x.ai/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "XAI_API_KEY",
    },
    "mistral": {
        "base": "https://api.mistral.ai/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "MISTRAL_API_KEY",
    },
    "deepseek": {
        "base": "https://api.deepseek.com/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "together": {
        "base": "https://api.together.xyz/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "TOGETHER_API_KEY",
    },
    "fireworks": {
        "base": "https://api.fireworks.ai/inference/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "FIREWORKS_API_KEY",
    },
    "cerebras": {
        "base": "https://api.cerebras.ai/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "CEREBRAS_API_KEY",
    },
    "opencode": {
        "base": "https://opencode.ai/zen/go/v1",
        "format": "openai",
        "auth": "bearer",
        "key_env": "OPENCODE_GO_KEY",
    },
}

# Model-prefix -> provider, used when the model has no explicit provider/.
# Matches OmniRoute's inference rules (open-sse/services/model.ts):
#   ^gpt-  -> openai
#   ^claude- -> anthropic
#   ^gemini- | ^gemma- -> gemini
# Everything else (deepseek-, grok-, glm-, kimi, qwen, minimax-, mistral-,
# llama, gpt-oss-, mimo-, ...) requires an explicit `provider/model` prefix.
PREFIX_RULES = [
    ("gpt-", "openai"),
    ("claude-", "anthropic"),
    ("gemini-", "gemini"),
    ("gemma-", "gemini"),
]

# Models known to use the Anthropic messages shape on providers that otherwise
# look OpenAI-compatible. Add bare ids here only if a provider's docs say so.
ANTHROPIC_SHAPE_MODELS = set()


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------
def load_key(provider_id):
    cfg = PROVIDERS[provider_id]
    env_var = cfg["key_env"]
    key = os.environ.get(env_var)
    if key:
        return key.strip()
    local_key_named = os.path.join(HERE, f".key.{provider_id}")
    if os.path.exists(local_key_named):
        with open(local_key_named) as f:
            return f.read().strip()
    local_key = os.path.join(HERE, ".key")
    if os.path.exists(local_key):
        with open(local_key) as f:
            return f.read().strip()
    sys.exit(
        f"ERROR: no API key for provider '{provider_id}'. "
        f"Set ${env_var}, create .key next to fusion.py, or .key.{provider_id}"
    )


def load_configs():
    cfg_path = os.environ.get("FUSION_CONFIG", os.path.join(HERE, "fusion.json"))
    with open(cfg_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Provider / model resolution
# ---------------------------------------------------------------------------
def resolve_provider(model_str):
    """Resolve a model string to (provider_id, model_id, api_format).

    Accepts:
      - "provider/model"       -> explicit provider
      - "openrouter/openai/gpt-5" -> openrouter, model "openai/gpt-5"
      - "gpt-5"               -> auto-detect via PREFIX_RULES
    """
    if "/" in model_str:
        head, rest = model_str.split("/", 1)
        if head in PROVIDERS:
            return head, rest, PROVIDERS[head]["format"]
    for prefix, provider_id in PREFIX_RULES:
        if model_str.lower().startswith(prefix):
            fmt = PROVIDERS[provider_id]["format"]
            if model_str in ANTHROPIC_SHAPE_MODELS:
                fmt = "claude"
            return provider_id, model_str, fmt
    return "openai", model_str, "openai"


# ---------------------------------------------------------------------------
# Model calls — shape-aware (openai / claude / gemini)
# ---------------------------------------------------------------------------
def call_model(model_str, system, user, key, timeout_ms, max_tokens=2048):
    provider_id, model_id, fmt = resolve_provider(model_str)
    cfg = PROVIDERS[provider_id]
    timeout = max((timeout_ms or 120000) / 1000, 30)
    ua = "openfusion/1.0 (+https://github.com/vstalingrady/OpenFusion)"

    if fmt == "claude":
        url = f"{cfg['base']}/messages"
        headers = {
            "x-api-key": key,
            "Content-Type": "application/json",
            "User-Agent": ua,
        }
        for k, v in cfg.get("headers", {}).items():
            headers[k] = v
        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            body["system"] = system
        payload = json.dumps(body).encode()

    elif fmt == "gemini":
        url = f"{cfg['base']}/models/{model_id}:generateContent"
        headers = {
            "x-goog-api-key": key,
            "Content-Type": "application/json",
            "User-Agent": ua,
        }
        contents = [{"role": "user", "parts": [{"text": user}]}]
        body = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        body["generationConfig"] = {"maxOutputTokens": max_tokens}
        payload = json.dumps(body).encode()

    else:  # openai shape
        url = f"{cfg['base']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": ua,
        }
        for k, v in cfg.get("headers", {}).items():
            headers[k] = v
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body = {
            "model": model_id,
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

    return _extract_text(data, fmt)


def _extract_text(data, fmt):
    if fmt == "claude":
        texts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        out = "".join(texts)
        return (out, None) if out else (None, "empty output")
    elif fmt == "gemini":
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            out = "".join(p.get("text", "") for p in parts if "text" in p)
            return (out, None) if out else (None, "empty output")
        return None, "empty response"
    else:  # openai
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            out = msg.get("content", "") or msg.get("reasoning_content", "") or ""
            return (out, None) if out else (None, "empty output")
    return None, "empty response"


def run_branch(branch, prompt, label):
    model = branch["model"]
    sys_prompt = branch.get("prompt", "")
    tmo = branch.get("timeout", 120000)
    t0 = time.time()
    provider_id, _, _ = resolve_provider(model)
    key = load_key(provider_id)
    out, err = call_model(model, sys_prompt, prompt, key, tmo, max_tokens=4096)
    elapsed = round(time.time() - t0, 1)
    if err:
        return label, model, None, err, elapsed
    return label, model, out, None, elapsed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def build_adhoc_config(models, judge_model, synth_model):
    """Build a config dict from a comma-separated model list (--models flag)."""
    branch_list = [m.strip() for m in models.split(",") if m.strip()]
    if not branch_list:
        sys.exit("ERROR: --models needs at least one model id")
    branches = [
        {"model": m, "prompt": "Answer the prompt independently and thoroughly. Cite specifics.", "timeout": 120000}
        for m in branch_list
    ]
    j = judge_model or branch_list[0]
    s = synth_model or branch_list[0]
    return {
        "branches": branches,
        "judge": {"model": j, "prompt": "Rank the branch outputs by correctness, specificity, and risk coverage."},
        "synthesizer": {"model": s, "prompt": "Combine the strongest findings into one concise answer with concrete next steps. Deduplicate."},
        "limits": {"timeout": 180000, "maxBranches": 8},
    }


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(
            "Usage:\n"
            "  fusion.py <config-name> <prompt>\n"
            "  fusion.py --models \"m1,m2,m3\" [--judge mJ] [--synth mS] <prompt>"
        )

    custom_models = None
    custom_judge = None
    custom_synth = None
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--models":
            custom_models = args[i + 1]
            i += 2
        elif a == "--judge":
            custom_judge = args[i + 1]
            i += 2
        elif a == "--synth":
            custom_synth = args[i + 1]
            i += 2
        else:
            positional.append(a)
            i += 1

    if not positional:
        sys.exit("ERROR: missing prompt")
    if custom_models:
        config_name = "custom"
        cfg = build_adhoc_config(custom_models, custom_judge, custom_synth)
        prompt = " ".join(positional)
    else:
        if not positional:
            sys.exit("ERROR: missing config name and prompt")
        config_name = positional[0]
        prompt = " ".join(positional[1:])
        if not prompt:
            sys.exit("ERROR: missing prompt")
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
            pool.submit(run_branch, b, prompt, labels[i]): i
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
    j_provider, _, _ = resolve_provider(judge["model"])
    j_key = load_key(j_provider)
    judge_out, jerr = call_model(
        judge["model"], judge.get("prompt", ""), judge_user, j_key,
        int(max(deadline - time.time(), 15) * 1000), max_tokens=1024,
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
    s_provider, _, _ = resolve_provider(synthesizer["model"])
    s_key = load_key(s_provider)
    synth_out, serr = call_model(
        synthesizer["model"], synthesizer.get("prompt", ""), synth_user, s_key,
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
