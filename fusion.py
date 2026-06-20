#!/usr/bin/env python3
"""
OpenFusion — compound model orchestration.

Fan one prompt out to N model branches in parallel, a judge model ranks the
outputs, a synthesizer model combines them into one final answer.

Everything is configured in fusion.json — providers and configs. This script
knows nothing about specific providers or models. It just reads the JSON,
calls the endpoints, and prints the result.

Usage:
  fusion.py <config-name> <prompt>
  fusion.py --models "provider/m1,provider/m2,provider/m3" [--judge provider/mJ --synth provider/mS] <prompt>

Set API keys via the env var each provider declares (key_env in fusion.json),
or a .key / .key.<provider> file next to this script.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_config():
    path = os.environ.get("FUSION_CONFIG", os.path.join(HERE, "fusion.json"))
    with open(path) as f:
        return json.load(f)


def load_key(provider_id, provider_cfg):
    env_var = provider_cfg.get("key_env", "")
    key = os.environ.get(env_var) if env_var else None
    if key:
        return key.strip()
    for p in (os.path.join(HERE, f".key.{provider_id}"), os.path.join(HERE, ".key")):
        if os.path.exists(p):
            with open(p) as f:
                return f.read().strip()
    sys.exit(
        f"ERROR: no API key for provider '{provider_id}'. "
        f"Set ${env_var}, or create .key.{provider_id} / .key next to fusion.py"
    )


def resolve(model_str, providers):
    """Split 'provider/model' into (provider_id, model_id, provider_cfg)."""
    if "/" not in model_str:
        sys.exit(f"ERROR: '{model_str}' has no provider. Use 'provider/model' (e.g. openai/gpt-5.5).")
    provider_id, model_id = model_str.split("/", 1)
    if provider_id not in providers:
        avail = ", ".join(sorted(providers.keys()))
        sys.exit(f"ERROR: unknown provider '{provider_id}'. Available: {avail}")
    return provider_id, model_id, providers[provider_id]


def build_request(model_id, provider_cfg, system, user, max_tokens):
    """Build (url, headers, body) for the provider's API shape."""
    base = provider_cfg["base"].rstrip("/")
    shape = provider_cfg.get("shape", "openai")
    auth = provider_cfg.get("auth", "bearer")
    key = provider_cfg.get("_key", "")
    extra_headers = provider_cfg.get("headers", {})
    ua = "openfusion/1.0"

    headers = {"Content-Type": "application/json", "User-Agent": ua}
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif auth == "x-api-key":
        headers["x-api-key"] = key
    elif auth == "x-goog-api-key":
        headers["x-goog-api-key"] = key
    headers.update(extra_headers)

    if shape == "claude":
        url = f"{base}/messages"
        body = {"model": model_id, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": user}]}
        if system:
            body["system"] = system
    elif shape == "gemini":
        url = f"{base}/models/{model_id}:generateContent"
        body = {"contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
    else:  # openai
        url = f"{base}/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body = {"model": model_id, "messages": messages, "stream": False, "max_tokens": max_tokens}

    return url, headers, body


def extract_text(data, shape):
    if shape == "claude":
        out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return out or None
    if shape == "gemini":
        cands = data.get("candidates", [])
        if cands:
            return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if "text" in p)
        return None
    choices = data.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        return msg.get("content", "") or msg.get("reasoning_content", "") or None
    return None


def call_model(model_str, system, user, providers, timeout_ms, max_tokens=2048):
    provider_id, model_id, pcfg = resolve(model_str, providers)
    pcfg["_key"] = load_key(provider_id, pcfg)
    url, headers, body = build_request(model_id, pcfg, system, user, max_tokens)
    payload = json.dumps(body).encode()
    timeout = max((timeout_ms or 120000) / 1000, 30)
    shape = pcfg.get("shape", "openai")

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            text = extract_text(data, shape)
            if text:
                return text, None
            return None, "empty output"
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
    return None, last_err or "request failed"


def run_branch(branch, prompt, providers, label):
    model = branch["model"]
    system = branch.get("prompt", "")
    tmo = branch.get("timeout", 120000)
    t0 = time.time()
    out, err = call_model(model, system, prompt, providers, tmo, max_tokens=4096)
    elapsed = round(time.time() - t0, 1)
    if err:
        return label, model, None, err, elapsed
    return label, model, out, None, elapsed


def adhoc_config(models, judge_model, synth_model):
    ms = [m.strip() for m in models.split(",") if m.strip()]
    if not ms:
        sys.exit("ERROR: --models needs at least one model id")
    branches = [{"model": m, "prompt": "", "timeout": 120000} for m in ms]
    j = judge_model or ms[0]
    s = synth_model or ms[0]
    return {
        "branches": branches,
        "judge": {"model": j, "prompt": ""},
        "synthesizer": {"model": s, "prompt": ""},
        "limits": {"timeout": 180000, "maxBranches": 8},
    }


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit("Usage:\n  fusion.py <config> <prompt>\n  fusion.py --models \"p/m1,p/m2\" [--judge p/mJ --synth p/mS] <prompt>")

    custom_models = custom_judge = custom_synth = None
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--models":
            custom_models = args[i + 1]; i += 2
        elif a == "--judge":
            custom_judge = args[i + 1]; i += 2
        elif a == "--synth":
            custom_synth = args[i + 1]; i += 2
        else:
            positional.append(a); i += 1

    data = load_config()
    providers = data.get("providers", {})

    if custom_models:
        cfg = adhoc_config(custom_models, custom_judge, custom_synth)
        prompt = " ".join(positional)
        config_name = "custom"
    else:
        if len(positional) < 2:
            sys.exit("Usage: fusion.py <config> <prompt>")
        config_name = positional[0]
        prompt = " ".join(positional[1:])
        configs = data.get("configs", {})
        if config_name not in configs:
            avail = ", ".join(sorted(configs.keys())) or "(none — add one in fusion.json)"
            sys.exit(f"ERROR: config '{config_name}' not found. Available: {avail}")
        cfg = configs[config_name]

    if not prompt:
        sys.exit("ERROR: missing prompt")

    branches = cfg.get("branches", [])
    judge = cfg.get("judge", {})
    synth = cfg.get("synthesizer", {})
    limits = cfg.get("limits", {})
    max_branches = limits.get("maxBranches", 4)
    overall_timeout = limits.get("timeout", 180000)

    if len(branches) > max_branches:
        log(f"WARNING: {len(branches)} branches exceed maxBranches={max_branches}, trimming.")
        branches = branches[:max_branches]
    if not branches:
        sys.exit("ERROR: no branches configured")
    if not judge or not synth:
        sys.exit("ERROR: config needs judge and synthesizer")

    log(f"=== fusion: {config_name} ===")
    log(f"prompt: {prompt[:120]}{'...' if len(prompt)>120 else ''}")
    log(f"branches: {len(branches)} | judge: {judge.get('model')} | synth: {synth.get('model')}")
    log("")

    t_start = time.time()
    deadline = t_start + overall_timeout / 1000

    labels = [chr(ord('A') + i) for i in range(len(branches))]
    results = {}
    with ThreadPoolExecutor(max_workers=len(branches)) as pool:
        futures = {pool.submit(run_branch, b, prompt, providers, labels[i]): i
                   for i, b in enumerate(branches)}
        for fut in as_completed(futures, timeout=max(overall_timeout / 1000, 30)):
            label, model, out, err, elapsed = fut.result()
            status = "ok" if out else f"FAIL: {err}"
            log(f"  [{label}] {model} ({elapsed}s) -> {status}")
            if out:
                results[label] = {"model": model, "output": out}

    if not results:
        sys.exit("ERROR: all branches failed")

    log("")
    log(f"branches done in {round(time.time()-t_start,1)}s | judging...")

    branch_block = "\n\n".join(
        f"=== Branch {l} ({results[l]['model']}) ===\n{results[l]['output']}"
        for l in sorted(results)
    )
    judge_user = (f"ORIGINAL PROMPT:\n{prompt}\n\nBRANCH OUTPUTS:\n{branch_block}\n\n"
                  f"Rank the branch outputs. For each: rank (1=best), one-line strength, "
                  f"one-line weakness. Name the strongest branch.")
    judge_out, jerr = call_model(
        judge["model"], judge.get("prompt", ""), judge_user, providers,
        int(max(deadline - time.time(), 15) * 1000), max_tokens=1024,
    )
    if jerr:
        log(f"  judge FAIL: {jerr} — synthesizing from branches directly")
        judge_out = "Judge unavailable. Synthesize from branch outputs directly."
    else:
        log(f"  judge {judge['model']} -> ok")

    log(f"synthesizing with {synth.get('model')}...")
    synth_user = (f"ORIGINAL PROMPT:\n{prompt}\n\nBRANCH OUTPUTS:\n{branch_block}\n\n"
                  f"JUDGE RANKING:\n{judge_out}\n\n"
                  f"Produce ONE final answer combining the strongest findings. "
                  f"Be concise and concrete. Include next steps where useful.")
    synth_out, serr = call_model(
        synth["model"], synth.get("prompt", ""), synth_user, providers,
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
