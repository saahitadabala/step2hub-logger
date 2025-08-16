# Step2Hub — Logger (Streamlit Cloud + Supabase with Safe Fallback)
# ------------------------------------------------
# This version adds **automatic fallback** to SQLite if Postgres fails and a **Health Check** page.
# Goal: one simple website that always loads; we can add cloud DB later without breaking anything.
#
# Quick start (local dev):
#   1) pip install -r requirements.txt
#   2) streamlit run app.py  (uses local SQLite by default)
#
# On Streamlit Cloud:
#   - Deploy the repo with `app.py` and `requirements.txt`
#   - Optional: Add Postgres URL in "App Settings → Secrets" under [db].url to enable cloud DB

import re
import os
from datetime import datetime
from dateutil import tz
from typing import List, Dict

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# ------------------------------
# Config & DB engine (with safe fallback)
# ------------------------------

st.set_page_config(page_title="Step2Hub — Logger (Cloud)", layout="wide")

# Prefer cloud Postgres from secrets; fallback to local SQLite
DATABASE_URL = st.secrets.get("db", {}).get("url") if hasattr(st, "secrets") else None
DB_MODE = "sqlite"
DB_INFO = "SQLite (no cloud DB configured)"
engine: Engine | None = None

# Try Postgres first if a URL is provided; otherwise use SQLite automatically
try:
    if DATABASE_URL:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        # probe the connection early
        with engine.begin() as _conn:
            _conn.execute(text("select 1"))
        DB_MODE = "postgres"
        DB_INFO = "Postgres (cloud DB active)"
    else:
        raise RuntimeError("No DATABASE_URL; using SQLite fallback")
except Exception as e:
    SQLITE_PATH = os.environ.get("STEP2HUB_SQLITE", "step2hub.db")
    engine = create_engine(f"sqlite:///{SQLITE_PATH}")
    DB_MODE = "sqlite"
    DB_INFO = f"SQLite fallback — reason: {e.__class__.__name__}"

st.info(f"DB mode: {DB_INFO}")

# ------------------------------
# Schema management
# ------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ,
    source TEXT,
    exam TEXT,
    qnum TEXT,
    raw_question TEXT,
    choices TEXT,
    your_answer TEXT,
    correct_answer TEXT,
    confidence INTEGER,
    explanation_raw TEXT,
    topics TEXT,
    question_type TEXT,
    error_types TEXT,
    missed_clues TEXT,
    notes TEXT
);
""" if DB_MODE == "postgres" else """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    source TEXT,
    exam TEXT,
    qnum TEXT,
    raw_question TEXT,
    choices TEXT,
    your_answer TEXT,
    correct_answer TEXT,
    confidence INTEGER,
    explanation_raw TEXT,
    topics TEXT,
    question_type TEXT,
    error_types TEXT,
    missed_clues TEXT,
    notes TEXT
);
"""


def init_db():
    """Create table in whichever backend is active."""
    with engine.begin() as conn:
        conn.execute(text(CREATE_TABLE_SQL))


# ------------------------------
# Lightweight auto classification
# ------------------------------

QUESTION_TYPE_MAP = {
    "diagnosis": [r"most likely diagnosis", r"what is the diagnosis", r"etiology", r"cause of"],
    "management": [r"next best step", r"initial management", r"most appropriate management", r"treatment", r"therapy"],
    "workup": [r"next test", r"most appropriate test", r"diagnostic step", r"screening", r"evaluation"],
    "interpretation": [r"interpret the (ecg|abg|cxr|labs)", r"most likely finding", r"what does this (lab|image) indicate"],
    "mechanism": [r"mechanism", r"pathophysiology", r"pharmacodynamics", r"moa", r"mechanism of action"],
}

ERROR_TYPES = [
    "Content gap", "Interpretation", "NBME language trap",
    "Priority/sequence", "Risk/benefit", "Premature closure", "Math/units"
]

TOPIC_SEEDS = {
    "Cardiology": ["stemi", "nstemi", "heart failure", "chf", "afib", "valve", "jvp", "murmur"],
    "Pulmonology": ["asthma", "copd", "pneumonia", "pe", "pneumothorax", "pleural"],
    "Nephrology": ["ckd", "aki", "hyperkalemia", "hyponatremia", "bicarb", "metabolic acidosis", "diuretic"],
    "Endocrine": ["thyroid", "graves", "hashimoto", "dka", "hhs", "adrenal", "cortisol"],
    "Gastroenterology": ["cirrhosis", "ulcer", "gi bleed", "ibs", "ibd", "pancreatitis", "bilirubin"],
    "Infectious Dz": ["sepsis", "meningitis", "endocarditis", "mrsa", "pseudomonas", "hiv", "cdiff"],
    "Heme/Onc": ["anemia", "leukemia", "lymphoma", "multiple myeloma", "platelet", "transfusion"],
    "OBGYN": ["pregnan", "preeclampsia", "postpartum", "ectopic", "sti", "pid"],
    "Pediatrics": ["child", "infant", "vaccin", "bronchiolitis", "rsv", "otitis"],
    "Psych": ["depress", "mania", "bipolar", "schizo", "anxiety", "ocd", "ptsd"],
    "Surgery/Acute": ["trauma", "appendicitis", "cholecystitis", "bowel obstruction", "peritonitis"],
}


def guess_question_type(text: str) -> str:
    t = text.lower()
    for qtype, pats in QUESTION_TYPE_MAP.items():
        for pat in pats:
            if re.search(pat, t):
                return qtype
    if re.search(r"next (best )?step|initial management|treatment", t):
        return "management"
    if re.search(r"diagnosis|etiology|cause", t):
        return "diagnosis"
    return "management"


def guess_topics(text: str) -> List[str]:
    t = text.lower()
    hits = []
    for topic, seeds in TOPIC_SEEDS.items():
        if any(re.search(seed, t) for seed in seeds):
            hits.append(topic)
    return hits or ["General IM"]


def suggest_error_types(your_answer: str, correct_answer: str, stem: str, explanation: str) -> List[str]:
    ya = (your_answer or "").strip().lower()
    ca = (correct_answer or "").strip().lower()
    ex = (explanation or "").lower()
    stxt = (stem or "").lower()
    suggested = set()

    if re.search(r"first[- ]line|initial (therapy|management)|standard of care", ex) and ya and ca and ya != ca:
        suggested.update(["Content gap", "Priority/sequence"])
    if re.search(r"ecg|ekg|cxr|ct|mri|abg|pft|spirom", stxt + " " + ex):
        suggested.add("Interpretation")
    if re.search(r"always|never|except|most|least", stxt):
        suggested.add("NBME language trap")
    if re.search(r"anion gap|osm(olarity|olality)|dose|units|rate|fractional excretion|clearance", ex):
        suggested.add("Math/units")
    if not suggested:
        suggested.add("Content gap")
    return sorted(suggested)


# ------------------------------
# DB helpers
# ------------------------------

def local_now_iso() -> str:
    return datetime.now(tz.tzlocal()).isoformat(timespec="seconds")


def insert_log(row: Dict):
    with engine.begin() as conn:
        if DB_MODE == "postgres":
            sql = text(
                """
                INSERT INTO logs (
                    created_at, source, exam, qnum, raw_question, choices,
                    your_answer, correct_answer, confidence, explanation_raw,
                    topics, question_type, error_types, missed_clues, notes
                ) VALUES (
                    :created_at, :source, :exam, :qnum, :raw_question, :choices,
                    :your_answer, :correct_answer, :confidence, :explanation_raw,
                    :topics, :question_type, :error_types, :missed_clues, :notes
                )
                """
            )
            conn.execute(sql, row)
        else:
            # SQLite accepts the same named params
            sql = text(
                """
                INSERT INTO logs (
                    created_at, source, exam, qnum, raw_question, choices,
                    your_answer, correct_answer, confidence, explanation_raw,
                    topics, question_type, error_types, missed_clues, notes
                ) VALUES (
                    :created_at, :source, :exam, :qnum, :raw_question, :choices,
                    :your_answer, :correct_answer, :confidence, :explanation_raw,
                    :topics, :question_type, :error_types, :missed_clues, :notes
                )
                """
            )
            conn.execute(sql, row)


def fetch_logs() -> pd.DataFrame:
    with engine.begin() as conn:
        df = pd.read_sql(text("SELECT * FROM logs ORDER BY id DESC"), conn)
    return df


# ------------------------------
# Analytics
# ------------------------------

def compute_stats(df: pd.DataFrame) -> Dict:
    stats: Dict = {}
    if df.empty:
        return stats
    df = df.copy()
    df["is_correct"] = (
        df["your_answer"].fillna("").str.strip().str.lower()
        == df["correct_answer"].fillna("").str.strip().str.lower()
    )
    stats["total"] = len(df)
    stats["accuracy"] = float(df["is_correct"].mean()) if len(df) else 0.0

    topics_exp = df.copy()
    topics_exp["topics_list"] = topics_exp["topics"].fillna("").apply(lambda x: [t.strip() for t in x.split(",") if t.strip()])
    topics_exp = topics_exp.explode("topics_list")
    topic_perf = (
        topics_exp.groupby("topics_list").agg(n=("id","count"), acc=("is_correct","mean")).reset_index()
        .sort_values(["n","acc"], ascending=[False, True])
    )
    stats["topic_perf"] = topic_perf

    errs = df.copy()
    errs["err_list"] = errs["error_types"].fillna("").apply(lambda x: [t.strip() for t in x.split(",") if t.strip()])
    errs = errs.explode("err_list")
    err_counts = errs["err_list"].value_counts(dropna=True).rename_axis("error_type").reset_index(name="count")
    stats["err_counts"] = err_counts

    last20 = df.head(20)
    stats["recent_acc"] = float(last20["is_correct"].mean()) if len(last20) else None
    return stats


# ------------------------------
# UI
# ------------------------------

init_db()

st.title("🧠 Step2Hub — Logger (Cloud)")
st.caption("Cloud hub with Postgres (Supabase) or local SQLite fallback. MVP v0.3 — now with Health Check.")

page = st.sidebar.radio("Navigate", ["Log New Question", "Dashboard", "Review / Export", "Health Check"], index=0)

if page == "Log New Question":
    st.subheader("Log a question")
    with st.form("log_form", clear_on_submit=False):
        colA, colB, colC = st.columns([1,1,1])
        with colA:
            source = st.text_input("Source (e.g., NBME, UWorld)")
        with colB:
            exam = st.text_input("Exam/Block (e.g., NBME 27, UWorld Block 15)")
        with colC:
            qnum = st.text_input("Question # (optional)")

        raw_question = st.text_area("Question stem (paste full text)", height=180)
        choices = st.text_area("Choices (paste A–E)", height=120)

        col1, col2, col3 = st.columns([1,1,1])
        with col1:
            your_answer = st.text_input("Your answer (letter or text)")
        with col2:
            correct_answer = st.text_input("Correct answer (letter or text)")
        with col3:
            confidence = st.slider("Confidence", 1, 5, 3)

        explanation_raw = st.text_area("Official explanation (paste)", height=180)

        with st.expander("Auto-suggested classifications (editable)", expanded=True):
            suggested_qtype = guess_question_type((raw_question or "") + "
" + (explanation_raw or ""))
            suggested_topics = guess_topics((raw_question or "") + "
" + (explanation_raw or ""))
            suggested_errors = suggest_error_types(your_answer, correct_answer, raw_question, explanation_raw)

            question_type = st.selectbox(
                "Question type",
                options=["diagnosis", "management", "workup", "interpretation", "mechanism"],
                index=["diagnosis","management","workup","interpretation","mechanism"].index(suggested_qtype)
            )
            topics = st.multiselect("Topic tags", options=sorted(list(TOPIC_SEEDS.keys()) + ["General IM"]), default=suggested_topics)
            error_types = st.multiselect("Error types", options=ERROR_TYPES, default=suggested_errors)

            missed_clues = st.text_area("Missed/Key clues (optional)", placeholder="e.g., S3, JVP↑, pulmonary edema")
            notes = st.text_area("Notes / Next action (optional)", placeholder="e.g., 3 cards on ADHF tx ladder; 5 mini-qs on diuretics")

        submitted = st.form_submit_button("➕ Save log")

        if submitted:
            if not raw_question.strip():
                st.error("Please paste the question stem.")
            elif not (your_answer.strip() and correct_answer.strip()):
                st.error("Enter both your answer and the correct answer.")
            else:
                row = {
                    "created_at": datetime.utcnow().isoformat(timespec="seconds"),
                    "source": (source or "").strip(),
                    "exam": (exam or "").strip(),
                    "qnum": (qnum or "").strip(),
                    "raw_question": (raw_question or "").strip(),
                    "choices": (choices or "").strip(),
                    "your_answer": (your_answer or "").strip(),
                    "correct_answer": (correct_answer or "").strip(),
                    "confidence": int(confidence),
                    "explanation_raw": (explanation_raw or "").strip(),
                    "topics": ", ".join(topics),
                    "question_type": question_type,
                    "error_types": ", ".join(error_types),
                    "missed_clues": (missed_clues or "").strip(),
                    "notes": (notes or "").strip(),
                }
                try:
                    insert_log(row)
                    st.success("Saved! Add another or open Dashboard.")
                except Exception as e:
                    st.error(f"Save failed: {e}")

elif page == "Dashboard":
    st.subheader("Overview")
    try:
        df = fetch_logs()
    except Exception as e:
        st.error(f"Could not load logs: {e}")
        df = pd.DataFrame()

    if df.empty:
        st.info("No logs yet. Add your first one in 'Log New Question'.")
    else:
        stats = compute_stats(df)
        col1, col2, col3 = st.columns([1,1,1])
        with col1:
            st.metric("Total logged", stats.get("total", 0))
        with col2:
            st.metric("Overall accuracy", f"{stats.get('accuracy', 0.0)*100:.1f}%")
        with col3:
            ra = stats.get("recent_acc")
            st.metric("Last 20 accuracy", f"{ra*100:.1f}%" if ra is not None else "–")

        st.markdown("---")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Top Topics (volume & accuracy)**")
            topic_perf = stats["topic_perf"]
            st.dataframe(topic_perf, use_container_width=True)
            st.bar_chart(topic_perf.set_index("topics_list")["n"], use_container_width=True)
        with c2:
            st.markdown("**Error Type Breakdown**")
            err_counts = stats["err_counts"]
            st.dataframe(err_counts, use_container_width=True)
            st.bar_chart(err_counts.set_index("error_type")["count"], use_container_width=True)

        st.markdown("---")
        st.markdown("**Recent Entries**")
        show_cols = ["created_at","source","exam","qnum","topics","question_type","your_answer","correct_answer"]
        st.dataframe(df[show_cols].head(10), use_container_width=True)

elif page == "Review / Export":
    st.subheader("Search, filter, and export your logs")
    try:
        df = fetch_logs()
    except Exception as e:
        st.error(f"Could not load logs: {e}")
        df = pd.DataFrame()

    if df.empty:
        st.info("No logs yet.")
    else:
        fcol1, fcol2, fcol3 = st.columns(3)
        with fcol1:
            srcs = ["(all)"] + sorted([s for s in df["source"].dropna().unique() if s])
            f_source = st.selectbox("Source", srcs)
        with fcol2:
            exms = ["(all)"] + sorted([s for s in df["exam"].dropna().unique() if s])
            f_exam = st.selectbox("Exam/Block", exms)
        with fcol3:
            qtypes = ["(all)"] + sorted([q for q in df["question_type"].dropna().unique() if q])
            f_qtype = st.selectbox("Question type", qtypes)

        mask = pd.Series([True]*len(df))
        if f_source != "(all)":
            mask &= (df["source"] == f_source)
        if f_exam != "(all)":
            mask &= (df["exam"] == f_exam)
        if f_qtype != "(all)":
            mask &= (df["question_type"] == f_qtype)

        view = df[mask].copy()
        st.dataframe(view, use_container_width=True)

        csv = view.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", csv, file_name="step2hub_logs.csv", mime="text/csv")

elif page == "Health Check":
    st.subheader("Health Check & Diagnostics")
    st.write("Use this page to sanity‑check connectivity and data write/read in your current DB mode.")
    st.code(DB_INFO)

    colA, colB = st.columns(2)
    with colA:
        if st.button("Create table (idempotent)"):
            try:
                init_db()
                st.success("Table ensured.")
            except Exception as e:
                st.error(f"Init failed: {e}")
        if st.button("Insert test row"):
            try:
                row = {
                    "created_at": datetime.utcnow().isoformat(timespec="seconds"),
                    "source": "HEALTHCHECK",
                    "exam": "SELFTEST",
                    "qnum": "-",
                    "raw_question": "Ping",
                    "choices": "",
                    "your_answer": "-",
                    "correct_answer": "-",
                    "confidence": 3,
                    "explanation_raw": "",
                    "topics": "Diagnostics",
                    "question_type": "management",
                    "error_types": "",
                    "missed_clues": "",
                    "notes": "healthcheck"
                }
                insert_log(row)
                st.success("Inserted test row.")
            except Exception as e:
                st.error(f"Insert failed: {e}")
    with colB:
        try:
            df = fetch_logs()
            st.write(f"Rows in table: {len(df)}")
            st.dataframe(df.head(5), use_container_width=True)
        except Exception as e:
            st.error(f"Read failed: {e}")

# End of file
