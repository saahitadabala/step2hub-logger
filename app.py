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
                source TEXT DEFAULT 'USER_PASTED',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # Lightweight migration for older tables (ignore error if column exists)
        try:
            conn.execute(text("ALTER TABLE questions ADD COLUMN source TEXT DEFAULT 'USER_PASTED'"))
        except Exception:
            pass

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
    text_lower = (text_block or "").lower()
    scores = {topic: 0 for topic in TOPIC_SEEDS}

    # Strong GI override (prevents benign labs from hijacking as Nephro)
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
    text_lower = (text_block or "").lower()
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
page = st.sidebar.radio("Go to", ["Log Question", "Practice QBank", "Dashboard"])

# ---------------------------
# Page: Log Question (user-pasted)
# ---------------------------
if page == "Log Question":
    st.header("➕ Log a new question")
    with st.form("log_form"):
        raw_question = st.text_area("Paste the full question vignette with answer choices")
        user_answer = st.text_input("Your answer (letter/choice)")
        correct_answer = st.text_input("Correct answer (letter/choice)")
        explanation = st.text_area("Paste NBME explanation")

        # AI suggestions
        suggested_primary, suggested_secondary = classify_topics((raw_question or "") + " " + (explanation or ""))
        suggested_qtype = guess_question_type((raw_question or "") + " " + (explanation or ""))

        st.markdown("### Auto-suggested classifications")
        topic_primary = st.selectbox(
            "Primary topic",
            [None] + list(TOPIC_SEEDS.keys()),
            index=(list(TOPIC_SEEDS.keys()).index(suggested_primary) + 1 if suggested_primary else 0),
        )
        topic_secondary = st.selectbox(
            "Secondary topic",
            [None] + list(TOPIC_SEEDS.keys()),
            index=(list(TOPIC_SEEDS.keys()).index(suggested_secondary) + 1 if suggested_secondary else 0),
        )
        qtype = st.selectbox(
            "Question type",
            [None] + list(QTYPE_SEEDS.keys()),
            index=(list(QTYPE_SEEDS.keys()).index(suggested_qtype) + 1 if suggested_qtype else 0),
        )

        mistake_reason = st.text_area("Why did you get it wrong? (eg, misread labs, weak concept)")

        submitted = st.form_submit_button("Save Question")
        if submitted:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO questions (raw_question, user_answer, correct_answer, explanation, qtype, topic_primary, topic_secondary, mistake_reason, source)
                    VALUES (:raw_question, :user_answer, :correct_answer, :explanation, :qtype, :topic_primary, :topic_secondary, :mistake_reason, :source)
                """), {
                    "raw_question": raw_question,
                    "user_answer": user_answer,
                    "correct_answer": correct_answer,
                    "explanation": explanation,
                    "qtype": qtype,
                    "topic_primary": topic_primary,
                    "topic_secondary": topic_secondary,
                    "mistake_reason": mistake_reason,
                    "source": "USER_PASTED",
                })
            st.success("✅ Question saved!")

# ---------------------------
# Page: Practice QBank (AI)
# ---------------------------
elif page == "Practice QBank":
    st.header("🧪 Practice QBank (AI)")

    tcol1, tcol2, tcol3 = st.columns([1,1,1])
    with tcol1:
        qb_topic = st.selectbox("Topic", list(TOPIC_SEEDS.keys()))
    with tcol2:
        qb_qtype = st.selectbox("Question type", ["Diagnosis", "Management"])
    with tcol3:
        qb_difficulty = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"])

    def generate_question(topic: str, qtype: str, difficulty: str):
        stem = ""
        choices = []
        correct = None
        explanation = ""
        if topic == "Gastroenterology" and qtype == "Management":
            stem = ("A 28-year-old woman has 8 months of intermittent crampy lower abdominal pain with loose stools 3–4 times/day. "
                    "Pain improves after defecation. No weight loss, no GI bleeding. Vitals normal; exam: mild lower quadrant tenderness. "
                    "What is the next best step?")
            choices = [("A", "Colonoscopy"), ("B", "Fecal fat quantification"), ("C", "CT abdomen with contrast"),
                       ("D", "No further testing necessary"), ("E", "Stool ova/parasites x3")]
            correct = "D"
            explanation = ("Chronic abdominal pain with altered bowel habits relieved by defecation suggests IBS without alarm features; "
                           "basic labs normal → no additional invasive testing needed.")
        elif topic == "Cardiology" and qtype == "Diagnosis":
            stem = ("A 65-year-old with hypertension presents with sudden severe tearing chest pain radiating to the back. "
                    "BP right arm 190/110, left 160/90. New diastolic murmur. What is the most likely diagnosis?")
            choices = [("A", "Acute pericarditis"), ("B", "Aortic dissection"), ("C", "Pulmonary embolism"), ("D", "STEMI"), ("E", "Tension pneumothorax")]
            correct = "B"
            explanation = ("Tearing chest pain to back + pulse/BP differential + diastolic murmur (AR) → classic for aortic dissection.")
        elif topic == "Pulmonology" and qtype == "Management":
            stem = ("A 22-year-old with asthma has wheeze and dyspnea after URI. RR 24, O2 sat 92% RA, speaks in phrases. What is the next best step?")
            choices = [("A", "Intubate immediately"), ("B", "High-dose inhaled SABA + ipratropium + systemic steroids"),
                       ("C", "Begin inhaled corticosteroid monotherapy"), ("D", "Order chest CT"), ("E", "Antibiotics for CAP")]
            correct = "B"
            explanation = ("Moderate acute exacerbation → SABA + anticholinergic + systemic steroids; no need for intubation without impending failure.")
        else:
            stem = (f"A clinical vignette in {topic} requiring {qtype.lower()} is presented. Choose the best option.")
            choices = [("A", "Option 1"), ("B", "Option 2"), ("C", "Option 3"), ("D", "Option 4")]
            correct = "A"
            explanation = ("Generic explanation placeholder. (We keep items de novo and NBME-style without using proprietary content.)")
        from random import shuffle
        shuffle(choices)
        return stem, choices, correct, explanation

    if "qb_current" not in st.session_state:
        st.session_state.qb_current = None
        st.session_state.qb_answer = None

    if st.button("Generate Question") or st.session_state.qb_current is None:
        stem, choices, correct, expl = generate_question(qb_topic, qb_qtype, qb_difficulty)
        st.session_state.qb_current = {"stem": stem, "choices": choices, "correct": correct, "explanation": expl, "topic": qb_topic, "qtype": qb_qtype}
        st.session_state.qb_answer = None

    cur = st.session_state.qb_current
    st.subheader("Question")
    st.write(cur["stem"])
    ans = st.radio("Your answer", [c[0] for c in cur["choices"]], index=0, horizontal=True, key="qb_ans_radio")

    if st.button("Check Answer"):
        st.session_state.qb_answer = ans
        is_correct = (ans == cur["correct"])
        if is_correct:
            st.success(f"Correct! {ans}")
        else:
            st.error(f"Incorrect. Correct answer: {cur['correct']}")
        st.info(cur["explanation"])
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO questions (raw_question, user_answer, correct_answer, explanation, qtype, topic_primary, topic_secondary, mistake_reason, source)
                VALUES (:raw_question, :user_answer, :correct_answer, :explanation, :qtype, :topic_primary, :topic_secondary, :mistake_reason, :source)
            """), {
                "raw_question": cur["stem"] + "\n\n" + "\n".join([f"{l}. {t}" for l, t in cur["choices"]]),
                "user_answer": ans,
                "correct_answer": cur["correct"],
                "explanation": cur["explanation"],
                "qtype": cur["qtype"],
                "topic_primary": cur["topic"],
                "topic_secondary": None,
                "mistake_reason": "",
                "source": "AI_QBANK",
            })
        st.caption("Saved to log as AI_QBANK ✅")

# ---------------------------
# Page: Dashboard
# ---------------------------
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
