from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
from dotenv import load_dotenv

from app.services.data_loader import load_wines
from app.services.gemini_service import GeminiServiceError, generate_recommendation
from app.services.wine_filter import FALLBACK_MESSAGE, filter_wines


# Load environment variables from .env file
load_dotenv()

DATASET_PATH = Path(__file__).parent / "app" / "data" / "schlumberger_products.json"
LOGO_PATH = Path(__file__).parent / "Schlumberger_Logo_Web_600px.png"


def _product_card(p: Dict[str, Any], *, why: Optional[str] = None) -> None:
    name = p.get("name") or "Unbekanntes Produkt"
    manufacturer = p.get("manufacturer") or p.get("producer")
    categories = ", ".join(p.get("categories") or []) or None
    url = p.get("url")

    props = p.get("properties") or {}
    land = ", ".join(props.get("Land") or []) if isinstance(props, dict) else None
    gebiet = ", ".join(props.get("Gebiet") or []) if isinstance(props, dict) else None

    headline_bits = [b for b in [manufacturer, categories, gebiet or land] if b]
    subtitle = " · ".join(headline_bits) if headline_bits else None

    st.markdown(f"**{name}**")
    if subtitle:
        st.caption(subtitle)

    # Show a few key property groups when present (works for wine + non-wine)
    key_groups = ["Rebsorte", "Aroma", "Geschmack", "Süße", "Stil", "Land", "Gebiet", "Alkoholgehalt", "Inhalt"]
    shown = 0
    cols = st.columns(3)
    if isinstance(props, dict):
        for g in key_groups:
            vals = props.get(g)
            if not vals:
                continue
            col = cols[shown % 3]
            with col:
                st.markdown(f"**{g}**\n\n{', '.join([str(v) for v in vals])}")
            shown += 1
            if shown >= 3:
                break

    with st.expander("Details", expanded=False):
        if why:
            st.write(why)
        price = p.get("price")
        if isinstance(price, (int, float)) and price > 0:
            st.markdown(f"**Price**\n\n€ {price:.2f}")
        if url:
            st.markdown(f"[Produktseite]({url})")


def main() -> None:
    # Use a brand icon instead of an emoji. Streamlit accepts a local image path.
    st.set_page_config(
        page_title="AI Sommelier",
        page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else None,
        layout="wide",
    )

    st.markdown(
        """
        <style>
          :root{
            --bg: #ffffff;
            --surface: #f6f7fb;
            --text: #0f172a;
            --muted: #64748b;
            --border: rgba(15,23,42,.10);
            --shadow: 0 10px 30px rgba(15,23,42,.06);
            --primary: #6d5bd0;
            --primary-600: #5b4bb6;
            --ring: rgba(109,91,208,.22);
          }

          /* App background + typography */
          .stApp { background: var(--bg); color: var(--text); }
          .stMarkdown, .stText, label, p, li { color: var(--text); }
          .stCaption { color: var(--muted) !important; }

          /* Sidebar */
          section[data-testid="stSidebar"]{
            background: var(--surface) !important;
            border-right: 1px solid var(--border) !important;
          }
          section[data-testid="stSidebar"] *{
            color: var(--text);
          }

          /* Cards/containers */
          [data-testid="stVerticalBlockBorderWrapper"]{
            border: 1px solid var(--border);
            border-radius: 16px;
            background: var(--bg);
            box-shadow: var(--shadow);
          }

          /* Inputs */
          .stTextInput input, .stTextArea textarea, .stSelectbox [data-baseweb="select"] > div{
            border-radius: 12px !important;
            border-color: var(--border) !important;
            background: var(--bg) !important;
            box-shadow: none !important;
          }
          .stTextInput input:focus, .stTextArea textarea:focus{
            border-color: var(--primary) !important;
            box-shadow: 0 0 0 4px var(--ring) !important;
          }

          /* Buttons (primary + default) */
          .stButton > button{
            border-radius: 12px !important;
            border: 1px solid var(--border) !important;
            background: var(--bg) !important;
            color: var(--text) !important;
            padding: 0.65rem 0.95rem !important;
            transition: transform .05s ease, box-shadow .2s ease, border-color .2s ease;
          }
          .stButton > button:hover{
            border-color: rgba(109,91,208,.35) !important;
            box-shadow: 0 8px 20px rgba(15,23,42,.08) !important;
          }
          .stButton > button:active{ transform: translateY(1px); }

          /* Primary button */
          .stButton > button[kind="primary"]{
            background: var(--primary) !important;
            border-color: rgba(0,0,0,0) !important;
            color: #ffffff !important;
            box-shadow: 0 10px 26px rgba(109,91,208,.25) !important;
          }
          .stButton > button[kind="primary"]:hover{
            background: var(--primary-600) !important;
            box-shadow: 0 12px 30px rgba(109,91,208,.30) !important;
          }

          /* Expanders */
          details{
            border: 1px solid var(--border) !important;
            border-radius: 14px !important;
            background: var(--surface) !important;
            padding: 0.25rem 0.75rem !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Top-left branding: prefer st.logo when available, fall back to a simple header row.
    try:
        # st.logo sizing options are limited; use a header image for a larger presence.
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=100)
        st.title("AI Sommelier")
    except Exception:
        c_logo, c_title = st.columns([1, 5], vertical_alignment="center")
        with c_logo:
            if LOGO_PATH.exists():
                st.image(str(LOGO_PATH), width=100)
        with c_title:
            st.title("AI Sommelier")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    wines = load_wines(DATASET_PATH)

    with st.sidebar:
        st.markdown("**Settings**")
        mode = st.selectbox("Mode", options=["consumer", "training", "sales"], index=0)
        price_ceiling = st.slider("Price ceiling (€)", min_value=0, max_value=200, value=0, step=5)

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_query = st.chat_input(
        "Ask for recommendations (food pairing, wine style, aroma, origin, producer, ...)"
    )

    if user_query:
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)

        query = user_query
        if price_ceiling and price_ceiling > 0:
            query = f"{query} bis {price_ceiling} €"

        filtered = filter_wines(query, wines, max_results=5)
        if not filtered:
            with st.chat_message("assistant"):
                st.info(FALLBACK_MESSAGE)
            return

        try:
            response = generate_recommendation(
                user_query=user_query,
                mode=mode,
                filtered_wines=filtered,
            )
        except GeminiServiceError as e:
            with st.chat_message("assistant"):
                st.error(f"AI request failed. Please try again.\n\nDetails: {e}")
            return

        st.session_state.messages.append({"role": "assistant", "content": response})
        with st.chat_message("assistant"):
            st.markdown(response)

            link_lines = []
            response_lc = response.lower()
            for item in filtered[:5]:
                name = item.get("name") or "Product"
                # Only link items that the AI actually mentioned (prompt asks for exact names)
                if name and name.lower() not in response_lc:
                    continue
                url = item.get("url")
                if url:
                    link_lines.append(f"- [{name}]({url})")
                else:
                    link_lines.append(f"- {name}")
            if link_lines:
                st.markdown("**Product links**\n\n" + "\n".join(link_lines))

            st.subheader("Suggested Products")
            for w in filtered[:5]:
                with st.container(border=True):
                    _product_card(w)


if __name__ == "__main__":
    main()

