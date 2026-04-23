from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from dotenv import load_dotenv

try:
    import streamlit as st
except ImportError:  # pragma: no cover
    st = None


SYSTEM_INSTRUCTION = """You are a closed-domain Schlumberger Product Assistant.

You are ONLY allowed to use the provided Schlumberger product dataset.

Rules:

* Never invent products or facts
* If a product is not in the dataset, say it cannot be found
* Only recommend products from the provided context
* Always justify recommendations using dataset attributes (name, categories, properties, description, price, etc.)
* Keep answers structured and clear

Response format:

1. Top 2–3 product recommendations
2. Why they match food/preferences
3. Key attributes (type/category, notable properties, price if available)
4. Optional alternative(s)

Modes:

* consumer: simple explanations
* training: detailed sommelier education
* sales: persuasive, concise selling points

Tone: professional, warm, precise."""


class GeminiServiceError(RuntimeError):
    pass


def _list_supported_model_ids(genai: Any) -> List[str]:
    """
    Returns model ids (without the 'models/' prefix) that support generateContent.
    """
    try:
        models = list(genai.list_models())
    except Exception as e:
        return []

    def supports_generate_content(m: Any) -> bool:
        try:
            return "generateContent" in (getattr(m, "supported_generation_methods", None) or [])
        except Exception:
            return False

    candidates = [m for m in models if supports_generate_content(m)]
    ids: List[str] = []
    for m in candidates:
        name = getattr(m, "name", "") or ""
        # SDK often returns "models/<id>"
        if name.startswith("models/"):
            name = name[len("models/") :]
        if name:
            ids.append(name)
    return ids

def _build_model_try_order(available_ids: List[str], preferred: str) -> List[str]:
    """
    Build a prioritized list of model ids to try.
    We prefer the caller's preferred model name, then newer Pro models,
    then newer Flash models (often available on lower tiers), then anything else.
    """
    if not available_ids:
        return [preferred]

    wanted_prefixes: List[str] = [
        preferred,
        # Pro (newer first)
        "gemini-3.1-pro",
        "gemini-3-pro",
        "gemini-2.5-pro",
        "gemini-pro-latest",
        "gemini-2.5-pro-preview",
        # Flash (newer first)
        "gemini-3.1-flash",
        "gemini-3-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-flash-latest",
        # Any gemini
        "gemini",
    ]

    ordered: List[str] = []
    seen = set()
    for prefix in wanted_prefixes:
        for mid in available_ids:
            if mid == prefix or mid.startswith(prefix):
                if mid not in seen:
                    ordered.append(mid)
                    seen.add(mid)

    # Ensure we still try any remaining supported models
    for mid in available_ids:
        if mid not in seen:
            ordered.append(mid)
            seen.add(mid)

    return ordered


def generate_recommendation(
    *,
    user_query: str,
    mode: str,
    filtered_wines: List[Dict[str, Any]],
    model_name: str = "gemini-1.5-pro",
) -> str:
    """
    Calls Gemini with STRICT closed-domain constraints.

    This function assumes `filtered_wines` already contains only Schlumberger wines.
    If `filtered_wines` is empty, it returns the fallback message without calling Gemini.
    """
    user_query = (user_query or "").strip()
    mode = (mode or "consumer").strip().lower()

    if not filtered_wines:
        return "Keine passenden Produkte in der Schlumberger-Datenbank gefunden."

    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key and st is not None:
        api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key and st is not None:
        api_key = st.secrets.get("general", {}).get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiServiceError(
            "Missing GEMINI_API_KEY environment variable or Streamlit secret."
        )

    try:
        import google.generativeai as genai
    except Exception as e:  # pragma: no cover
        raise GeminiServiceError(f"Failed to import google-generativeai: {e}") from e

    genai.configure(api_key=api_key)

    supported_ids = _list_supported_model_ids(genai)
    model_try_order = _build_model_try_order(supported_ids, model_name)

    # Tight, machine-readable context to reduce hallucination risk
    wines_json = json.dumps(filtered_wines, ensure_ascii=False, indent=2)
    prompt = f"""MODE: {mode}

USER QUERY:
{user_query}

SCHLUMBERGER DATASET (ONLY SOURCE OF TRUTH):
{wines_json}

INSTRUCTIONS:
- Recommend ONLY products present in the JSON above.
- When you mention a product, you MUST use its name EXACTLY as it appears in the JSON field "name".
- Start your answer with a section titled "Recommended products" and list 2–3 bullet points, each bullet starting with the exact product name (verbatim).
- If the JSON does not contain a suitable match, reply exactly:
  Keine passenden Produkte in der Schlumberger-Datenbank gefunden.
"""

    last_error: Optional[Exception] = None
    for mid in model_try_order:
        model = genai.GenerativeModel(
            model_name=mid,
            system_instruction=SYSTEM_INSTRUCTION,
        )
        try:
            resp = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.2,
                    "max_output_tokens": 800,
                },
            )
            last_error = None
            break
        except Exception as e:
            # try next model (handles 404 model-not-found and 429 quota blocks)
            last_error = e
            continue

    if last_error is not None:
        raise GeminiServiceError(f"Gemini request failed: {last_error}") from last_error

    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise GeminiServiceError("Gemini returned an empty response.")

    return text

