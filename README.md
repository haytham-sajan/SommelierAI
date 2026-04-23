## Schlumberger AI Wine Assistant (MVP)

Production-ready MVP of a **closed-domain** wine recommendation assistant.

### Rules (non-negotiable)

- Recommendations must **only** come from the local dataset: `app/data/schlumberger_wines.json`
- If no match is found, the app must return:
  - `Keine passenden Weine in der Schlumberger-Datenbank gefunden.`

### Setup

1. Create a virtual environment and install dependencies:

```bash
pip install -r requirements.txt
```

2. Create `.env` (copy from `.env.example`) and set `GEMINI_API_KEY`.

3. Run the Streamlit app:

```bash
streamlit run app.py
```

