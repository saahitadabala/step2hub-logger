# Step2Hub — Logger (Cloud‑easy)

**Goal:** Easiest possible “true hub” you can open from any computer.

- Deploy on **Streamlit Cloud** in a few clicks.
- By default, stores data in a local **SQLite** file (works on Cloud between runs; may reset on redeploy).
- When you’re ready for permanent storage, add a **Supabase (Postgres)** URL in Streamlit **Secrets** — no code changes.

---

## One‑page deploy (no terminal)

1. **Create a GitHub repo** (web UI): click “New”, name it `step2hub-logger`.
2. Upload three files from this folder:
   - `app.py`
   - `requirements.txt`
   - (optional) `.streamlit/secrets.toml` — you can add this later in Streamlit Secrets instead.
3. Go to **Streamlit Community Cloud** → “New app” → pick your repo, branch, and `app.py`.
4. Click **Deploy**.

**Done.** You now have a URL you can open anywhere.

> Data durability: With default SQLite, data persists as long as the app instance isn’t rebuilt. For guaranteed persistence across redeploys, add a Postgres URL (below).

---

## Optional: Make data permanent with Supabase (5 min)

- Create a Supabase project → copy the **connection URI**.
- In Streamlit → your app → **⋯ → Edit app → Advanced settings → Secrets**, add:
  ```toml
  [db]
  url = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require"
  ```
- Redeploy (auto). The app will use Postgres automatically and auto‑create the `logs` table.

SQL for manual table creation (optional):
```sql
create table if not exists logs (
  id bigserial primary key,
  created_at timestamptz,
  source text,
  exam text,
  qnum text,
  raw_question text,
  choices text,
  your_answer text,
  correct_answer text,
  confidence integer,
  explanation_raw text,
  topics text,
  question_type text,
  error_types text,
  missed_clues text,
  notes text
);
```

---

## Using the app

- **Log New Question**: paste stem + choices + explanation; add your answer, correct answer, confidence. Auto‑suggested **question type**, **topics**, **error types** (editable) → **Save**.
- **Dashboard**: total logged, accuracy, top topics (count & accuracy), error‑type breakdown, recent entries.
- **Review/Export**: filter and **Download CSV**.

---

## Tweak anything

Open `app.py`:
- Add topic seeds in `TOPIC_SEEDS`.
- Add phrases in `QUESTION_TYPE_MAP`.
- Tune heuristics in `suggest_error_types()`.

Push to GitHub; Streamlit Cloud auto‑updates.

---

## Local dev (optional)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

By default uses `step2hub.db` in the folder. To point local dev at Supabase, create `.streamlit/secrets.toml` with the `[db].url` shown above.
