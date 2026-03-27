"""
LLM Provider Benchmark — OpenRouter vs Ollama Cloud

Tests quality, latency, and JSON compliance for all configured models.
Determines best model assignment for QUICK (screening) and DEEP (trade plans) tasks.

Usage:
    cd backend && python -m __tests__.test_llm_providers
"""
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env", override=True)

# ── Config from env ──────────────────────────────────────────────────────────
OR_KEY      = os.getenv("OPENROUTER_API_KEY", "")
OR_BASE     = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OL_KEY      = os.getenv("OLLAMA_API_KEY", "")
OL_BASE     = os.getenv("OLLAMA_BASE_URL", "https://ollama.com/api")

# All models to test
OPENROUTER_MODELS = [
    os.getenv("Openrouter_Quick_Primary_Model",    "google/gemini-2.5-flash-lite-preview-09-2025"),
    os.getenv("Openrouter_Quick_Backup_Model",     "google/gemini-3.1-flash-lite-preview"),
    os.getenv("Openrouter_Research_Primary_Model", "anthropic/claude-haiku-4-5"),
    os.getenv("Openrouter_Research_Backup_Model",  "minimax/minimax-m2.7"),
]
OLLAMA_MODELS = [
    os.getenv("Ollama_Research_Primary_Model", "kimi-k2.5:cloud"),
    os.getenv("Ollama_Research_Backup_Model",  "glm-5:cloud"),
    os.getenv("Ollama_Quick_Primary_Model",    "gemini-3-flash-preview:cloud"),
    os.getenv("Ollama_Quick_Backup_Model",     "nemotron-3-nano:30b-cloud"),
]

# ── Test prompts ─────────────────────────────────────────────────────────────
QUICK_PROMPT = {
    "system": "You are a stock screener AI. Respond ONLY with valid JSON, no markdown.",
    "user": (
        'Given this data: [{"sym":"NVDA","px":875,"rsi":62,"ema_bull":true,"mom5":2.1,"vol_r":1.8},'
        '{"sym":"TSLA","px":215,"rsi":71,"ema_bull":false,"mom5":-0.8,"vol_r":0.9}].\n'
        'Screen for BULLISH signals with confidence >= 0.60. Return JSON array:\n'
        '[{"sym":"...","decision":"BULLISH","confidence":0.0-1.0,"reason":"one sentence"}]'
    ),
}

DEEP_PROMPT = {
    "system": "You are a senior trading analyst. Respond ONLY with valid JSON, no markdown.",
    "user": (
        "NVDA is at $875, RSI=62, up 2.1% in 5 days, volume 1.8x average, EMA bullish.\n"
        "Market regime: neutral. Portfolio: $138K, 3 open positions.\n"
        "Create a detailed trade plan:\n"
        '{"sym":"NVDA","decision":"BUY/PASS","conviction":0.0-1.0,"entry_price":0.0,'
        '"stop_loss":0.0,"take_profit":0.0,"reasoning":"2-3 sentences",'
        '"bull_case":"...","bear_case":"...","risk_level":"low/medium/high"}'
    ),
}


# ── Result dataclass ──────────────────────────────────────────────────────────
@dataclass
class ModelResult:
    provider: str
    model: str
    task: str
    latency_s: float = 0.0
    success: bool = False
    json_valid: bool = False
    has_required_fields: bool = False
    error: str = ""
    response_preview: str = ""
    score: float = 0.0  # computed at end


# ── Callers ───────────────────────────────────────────────────────────────────
def _call_openrouter(model: str, system: str, user: str, timeout: int = 40) -> tuple[str, float]:
    start = time.time()
    headers = {
        "Authorization": f"Bearer {OR_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://moonshotx.ai",
        "X-Title": "MoonshotX-Benchmark",
    }
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": 512,
        "temperature": 0.1,
    }
    r = requests.post(f"{OR_BASE.rstrip('/')}/chat/completions", headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"].get("content", "")
    return text, round(time.time() - start, 2)


def _call_ollama_openai_compat(model: str, system: str, user: str, timeout: int = 60) -> tuple[str, float]:
    """Ollama Cloud via OpenAI-compatible endpoint: /v1/chat/completions"""
    start = time.time()
    base = OL_BASE.rstrip("/")
    # Ollama cloud may expose /v1/chat/completions under the base
    url = f"{base}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OL_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": 512,
        "temperature": 0.1,
        "stream": False,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"].get("content", "")
    return text, round(time.time() - start, 2)


def _call_ollama_native(model: str, system: str, user: str, timeout: int = 60) -> tuple[str, float]:
    """Ollama native /api/chat endpoint"""
    start = time.time()
    base = OL_BASE.rstrip("/")
    url = f"{base}/chat"
    headers = {
        "Authorization": f"Bearer {OL_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text = data.get("message", {}).get("content", "")
    return text, round(time.time() - start, 2)


def _call_ollama(model: str, system: str, user: str) -> tuple[str, float]:
    """Try OpenAI-compat first, fallback to native."""
    try:
        return _call_ollama_openai_compat(model, system, user)
    except Exception as e1:
        try:
            return _call_ollama_native(model, system, user)
        except Exception as e2:
            raise RuntimeError(f"Both Ollama endpoints failed. OpenAI-compat: {e1} | Native: {e2}")


# ── JSON validator ─────────────────────────────────────────────────────────────
def _extract_json(text: str):
    import re
    for pat in [r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```"]:
        m = re.search(pat, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    for pat in [r"(\[.*\])", r"(\{.*\})"]:
        m = re.search(pat, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    try:
        return json.loads(text)
    except Exception:
        return None


QUICK_REQUIRED = {"sym", "decision", "confidence"}
DEEP_REQUIRED  = {"sym", "decision", "conviction", "stop_loss", "take_profit"}


def _check_fields(parsed, task: str) -> bool:
    required = QUICK_REQUIRED if task == "quick" else DEEP_REQUIRED
    if isinstance(parsed, list) and len(parsed) > 0:
        return required.issubset(parsed[0].keys())
    if isinstance(parsed, dict):
        return required.issubset(parsed.keys())
    return False


# ── Score formula ──────────────────────────────────────────────────────────────
def _score(r: ModelResult) -> float:
    """Score: 0-10. Weighted: json_valid 30%, has_fields 40%, latency 30%."""
    if not r.success:
        return 0.0
    s = 0.0
    s += 3.0 if r.json_valid else 0.0
    s += 4.0 if r.has_required_fields else 0.0
    # Latency: 10s=3.0, 20s=2.0, 30s=1.0, 60s=0.0
    lat_score = max(0.0, 3.0 - (r.latency_s - 5.0) / 20.0)
    s += round(lat_score, 2)
    return round(s, 2)


# ── Runner ────────────────────────────────────────────────────────────────────
def run_model_test(provider: str, model: str, task: str) -> ModelResult:
    prompt = QUICK_PROMPT if task == "quick" else DEEP_PROMPT
    res = ModelResult(provider=provider, model=model, task=task)

    try:
        if provider == "openrouter":
            text, lat = _call_openrouter(model, prompt["system"], prompt["user"])
        else:
            text, lat = _call_ollama(model, prompt["system"], prompt["user"])

        res.latency_s = lat
        res.success = True
        res.response_preview = text[:200].replace("\n", " ")

        parsed = _extract_json(text)
        res.json_valid = parsed is not None
        if res.json_valid:
            res.has_required_fields = _check_fields(parsed, task)

    except Exception as e:
        res.error = str(e)[:200]

    res.score = _score(res)
    return res


def print_results(results: list[ModelResult]):
    print("\n" + "═" * 100)
    print(f"{'PROVIDER':<12} {'MODEL':<50} {'TASK':<8} {'LAT':>6} {'JSON':>5} {'FIELDS':>6} {'SCORE':>6}  STATUS")
    print("─" * 100)
    for r in sorted(results, key=lambda x: (-x.score, x.latency_s)):
        status = r.error[:40] if not r.success else ("✓ PASS" if r.has_required_fields else "~ PARTIAL")
        lat = f"{r.latency_s:.1f}s" if r.success else "  —"
        print(f"{r.provider:<12} {r.model:<50} {r.task:<8} {lat:>6} {str(r.json_valid):>5} {str(r.has_required_fields):>6} {r.score:>6.1f}  {status}")
    print("═" * 100)


def print_recommendation(results: list[ModelResult]):
    print("\n## RECOMMENDED MODEL ASSIGNMENTS\n")

    def best(task: str):
        candidates = [r for r in results if r.task == task and r.success]
        if not candidates:
            return None
        return max(candidates, key=lambda x: (x.score, -x.latency_s))

    quick = best("quick")
    deep  = best("deep")

    if quick:
        print(f"  QUICK (screening):  [{quick.provider.upper()}] {quick.model}  "
              f"(score={quick.score}/10, lat={quick.latency_s:.1f}s)")
    if deep:
        print(f"  DEEP  (trade plan): [{deep.provider.upper()}] {deep.model}  "
              f"(score={deep.score}/10, lat={deep.latency_s:.1f}s)")

    # Per-provider bests
    for prov in ["openrouter", "ollama"]:
        q = best_for(results, prov, "quick")
        d = best_for(results, prov, "deep")
        if q or d:
            print(f"\n  {prov.upper()} best:")
            if q: print(f"    quick → {q.model}  (score={q.score}, lat={q.latency_s:.1f}s)")
            if d: print(f"    deep  → {d.model}  (score={d.score}, lat={d.latency_s:.1f}s)")

    print()


def best_for(results, provider, task):
    candidates = [r for r in results if r.provider == provider and r.task == task and r.success]
    return max(candidates, key=lambda x: (x.score, -x.latency_s)) if candidates else None


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "═" * 100)
    print("  MoonshotX — LLM Provider Benchmark")
    print(f"  OpenRouter base : {OR_BASE}")
    print(f"  Ollama base     : {OL_BASE}")
    print("═" * 100 + "\n")

    all_results = []
    test_matrix = [
        ("openrouter", OPENROUTER_MODELS[0], "quick"),
        ("openrouter", OPENROUTER_MODELS[1], "quick"),
        ("openrouter", OPENROUTER_MODELS[2], "deep"),
        ("openrouter", OPENROUTER_MODELS[3], "deep"),
        ("ollama",     OLLAMA_MODELS[2],     "quick"),
        ("ollama",     OLLAMA_MODELS[3],     "quick"),
        ("ollama",     OLLAMA_MODELS[0],     "deep"),
        ("ollama",     OLLAMA_MODELS[1],     "deep"),
    ]

    for i, (provider, model, task) in enumerate(test_matrix, 1):
        print(f"[{i}/{len(test_matrix)}] Testing {provider.upper()} / {model} ({task})...", end=" ", flush=True)
        r = run_model_test(provider, model, task)
        all_results.append(r)
        if r.success:
            print(f"✓  {r.latency_s:.1f}s  score={r.score}")
        else:
            print(f"✗  {r.error[:60]}")

    print_results(all_results)
    print_recommendation(all_results)

    # Save raw results for inspection
    out_path = Path(__file__).parent / "llm_benchmark_results.json"
    with open(out_path, "w") as f:
        json.dump([
            {"provider": r.provider, "model": r.model, "task": r.task,
             "latency_s": r.latency_s, "success": r.success, "json_valid": r.json_valid,
             "has_required_fields": r.has_required_fields, "score": r.score,
             "error": r.error, "preview": r.response_preview}
            for r in all_results
        ], f, indent=2)
    print(f"  Results saved → {out_path}\n")

    return all_results


if __name__ == "__main__":
    main()
