import streamlit as st
import pandas as pd
import re
import os
import datetime
from sqlalchemy import create_engine, text

# ---------------------------
# Database Setup
# ---------------------------
DB_MODE = os.getenv("DB_MODE", "sqlite")  # default sqlite

if DB_MODE == "postgres":
    DB_URL = os.getenv("DB_URL")
    engine = create_engine(DB_URL, pool_pre_ping=True)
else:
    DB_URL = "sqlite:///questions.db"
    engine = create_engine(DB_URL)

# ---------------------------
# Helper functions
# ---------------------------

def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_question TEXT,
                user_answer TEXT,
                correct_answer TEXT,
                explanation TEXT,
                qtype TEXT,
                topic_primary TEXT,
                topic_secondary TEXT,
                mistake_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

# Seed keywords for classification
TOPIC_SEEDS = {
    "Cardiology": ["chest pain", "murmur", "MI", "ST elevation", "troponin", "AFib", "hypertension"],
    "Pulmonology": ["dyspnea", "wheezing", "asthma", "COPD", "pneumonia", "hypoxemia"],
    "Gastroenterology": ["abdominal pain", "diarrhea", "constipation", "IBS", "bloating", "rectal bleeding"],
    "Nephrology": ["AKI", "CKD", "proteinuria", "hematuria", "casts", "dialysis", "oliguria", "anuria", "edema"],
    "ObGyn": ["pregnant", "gestation", "LMP", "miscarriage", "postpartum", "contraception"],
    "Endocrinology": ["diabetes", "thyroid", "cortisol", "adrenal", "pituitary"],
    "Psychiatry": ["anxiety", "depression", "psychosis", "bipolar", "PTSD", "substance"],
    "Neurology": ["seizure", "stroke", "weakness", "MS", "Parkinson", "neuropathy"],
    "Dermatology": ["rash", "lesion", "eczema", "psoriasis", "melanoma"],
    "HemeOnc": ["anemia", "lymphoma", "leukemia", "thrombocytopenia", "bleeding"],
}

QTYPE_SEEDS = {
    "Diagnosis": ["most likely diagnosis", "diagnosis", "dx"],
    "Management": ["next step", "management", "initial treatment", "therapy"],
    "Mechanism": ["pathophysiology", "mechanism"],
    "Prognosis": ["prognosis", "outcome"],
    "Ethics": ["ethics", "legal", "consent"],
}

def classify_topics(text_block):
    text_lower = text_block.lower()
    scores = {topic: 0 for topic in TOPIC_SEEDS}

    # Strong GI override
    if any(kw in text_lower for kw in ["ibs", "irritable bowel", "diarrhea", "bloating", "defecation", "mucus in stool"]):
        return "Gastroenterology", None

    for topic, seeds in TOPIC_SEEDS.items():
        for seed in seeds:
            if seed.lower() in text_lower:
                # Nephrology gating: only count if strong kidney context
                if topic == "Nephrology":
                    if re.search(r"(proteinuria|hematuria|casts|aki|ckd|oliguria|anuria|dialysis|edema)", text_lower):
                        scores[topic] += 2
                else:
                    scores[topic] += 1

    # pick top two
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary = ranked[0][0] if ranked[0][1] > 0 else None
    secondary = ranked[1][0] if ranked[1][1] > 0 else None
    return primary, secondary

def guess_question_type(text_block):
    text_lower = text_block.lower()
    for qtype, seeds in QTYPE_SEEDS.items():
        for seed in seeds:
            if seed in text_lower:
                return qtype
    return None

# ---------------------------
# Streamlit UI
# ---------------------------

st.title("🧠 Step2Hub — Logger")
st.caption("Log NBME-style questions with AI-assisted classification")

init_db()

st.sidebar.header("Navigation")
page = st.sidebar.radio("Go to", ["Log Question", "Dashboard"])

if page == "Log Question":
    st.header("➕ Log a new question")
    with st.form("log_form"):
        raw_question = st.text_area("Paste the full question vignette with answer choices")
        user_answer = st.text_input("Your answer (letter/choice)")
        correct_answer = st.text_input("Correct answer (letter/choice)")
        explanation = st.text_area("Paste NBME explanation")

        # AI suggestions
        suggested_primary, suggested_secondary = classify_topics(raw_question + " " + explanation)
        suggested_qtype = guess_question_type(raw_question + " " + explanation)

        st.markdown("### Auto-suggested classifications")
        topic_primary = st.selectbox("Primary topic", [None] + list(TOPIC_SEEDS.keys()), index=(list(TOPIC_SEEDS.keys()).index(suggested_primary) + 1 if suggested_primary else 0))
        topic_secondary = st.selectbox("Secondary topic", [None] + list(TOPIC_SEEDS.keys()), index=(list(TOPIC_SEEDS.keys()).index(suggested_secondary) + 1 if suggested_secondary else 0))
        qtype = st.selectbox("Question type", [None] + list(QTYPE_SEEDS.keys()), index=(list(QTYPE_SEEDS.keys()).index(suggested_qtype) + 1 if suggested_qtype else 0))

        mistake_reason = st.text_area("Why did you get it wrong? (eg, misread labs, weak concept)")

        submitted = st.form_submit_button("Save Question")
        if submitted:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO questions (raw_question, user_answer, correct_answer, explanation, qtype, topic_primary, topic_secondary, mistake_reason)
                    VALUES (:raw_question, :user_answer, :correct_answer, :explanation, :qtype, :topic_primary, :topic_secondary, :mistake_reason)
                """), {
                    "raw_question": raw_question,
                    "user_answer": user_answer,
                    "correct_answer": correct_answer,
                    "explanation": explanation,
                    "qtype": qtype,
                    "topic_primary": topic_primary,
                    "topic_secondary": topic_secondary,
                    "mistake_reason": mistake_reason,
                })
            st.success("✅ Question saved!")

elif page == "Dashboard":
    st.header("📊 Dashboard")
    with engine.begin() as conn:
        df = pd.read_sql("SELECT * FROM questions ORDER BY created_at DESC", conn)
    
    if df.empty:
        st.info("No questions logged yet.")
    else:
        st.subheader("Overview")
        st.metric("Total questions logged", len(df))

        col1, col2 = st.columns(2)
        with col1:
            topic_counts = df["topic_primary"].value_counts()
            st.bar_chart(topic_counts)
        with col2:
            qtype_counts = df["qtype"].value_counts()
            st.bar_chart(qtype_counts)

        st.subheader("Detailed Table")
        st.dataframe(df)
