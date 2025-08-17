import streamlit as st
import pandas as pd
import re
import os
from io import StringIO
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

def classify_topics(text_block: str):
    text_lower = (text_block or "").lower()
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

def guess_question_type(text_block: str):
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
st.caption("Log NBME-style questions with AI-assisted classification + your own AI QBank tab")

init_db()

st.sidebar.header("Navigation")
page = st.sidebar.radio("Go to", ["Log Question", "Practice QBank (AI)", "Dashboard"])

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
            "Primary topic", [None] + list(TOPIC_SEEDS.keys()),
            index=(list(TOPIC_SEEDS.keys()).index(suggested_primary) + 1 if suggested_primary else 0)
        )
        topic_secondary = st.selectbox(
            "Secondary topic", [None] + list(TOPIC_SEEDS.keys()),
            index=(list(TOPIC_SEEDS.keys()).index(suggested_secondary) + 1 if suggested_secondary else 0)
        )
        qtype = st.selectbox(
            "Question type", [None] + list(QTYPE_SEEDS.keys()),
            index=(list(QTYPE_SEEDS.keys()).index(suggested_qtype) + 1 if suggested_qtype else 0)
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
elif page == "Practice QBank (AI)":
    st.header("🧪 Practice QBank (AI)")

    # --- Controls ---
    colL, colR = st.columns([2,1])
    with colL:
        qb_topic_choice = st.selectbox("Topic", ["(Random)"] + list(TOPIC_SEEDS.keys()))
    with colR:
        if st.button("Generate New Question", use_container_width=True):
            st.session_state.pop("qb_current", None)
            st.session_state.pop("qb_answer", None)

    # Utility to pick a topic when (Random) is selected
    from random import choice as rnd_choice, shuffle

    def pick_topic(topic_choice: str) -> str:
        return rnd_choice(list(TOPIC_SEEDS.keys())) if topic_choice == "(Random)" else topic_choice

    # --- NBME-style de-novo question generator ---
    # Returns: stem (str), choices (list[(letter,text)]), correct_letter (str), explanation (str), rationales (dict letter->why wrong/right)
    def generate_nbme_style_question(topic: str):
        import random
        qtype = random.choice(["Diagnosis", "Management", "Workup", "Mechanism"])  # random like the real exam
        stem = ""
        bank = []
        correct = None
        explanation = ""
        rationales = {}

        # Handcrafted high-yield items (thorough explanations + distractor rationales)
        if topic == "Gastroenterology" and qtype in ("Management", "Diagnosis"):
            stem = (
                "A 28-year-old woman has 8 months of intermittent crampy lower abdominal pain with 3–4 loose stools/day. "
                "Pain improves after defecation. No weight loss, fever, or GI bleeding. Exam: mild LLQ tenderness. Basic labs normal. "
                "What is the next best step?"
            )
            bank = [
                ("A", "Colonoscopy"),
                ("B", "Fecal fat quantification"),
                ("C", "CT abdomen with contrast"),
                ("D", "No further testing necessary"),
                ("E", "Stool ova and parasites (×3)"),
            ]
            correct = "D"
            explanation = (
                "**IBS** is suggested by chronic abdominal pain related to defecation and altered bowel habits **without alarm features** (no weight loss, anemia, blood in stool, age <50) and with normal basic labs. "
                "For suspected IBS with diarrhea-predominance, rule out red flags and obtain minimal labs (CBC, celiac serology, inflammatory/infectious markers). **If unremarkable, no further invasive testing** is indicated."
            )
            rationales = {
                "A": "Colonoscopy is for alarm features or age-appropriate screening; not indicated here.",
                "B": "Fecal fat is for suspected malabsorption with steatorrhea/weight loss, which are absent.",
                "C": "CT abdomen is for intra-abdominal pathology or red flags; not first-line in classic IBS.",
                "D": "Correct — classic IBS without alarms after minimal evaluation.",
                "E": "Stool O&P is for exposure/travel/epidemiologic risk; not suggested in this vignette.",
            }
        elif topic == "Cardiology" and qtype in ("Diagnosis",):
            stem = (
                "A 65-year-old man with hypertension develops sudden severe tearing chest pain radiating to the back. "
                "Right arm BP 190/110; left 160/90. Early diastolic murmur at the left sternal border. Most likely diagnosis?"
            )
            bank = [("A", "Acute pericarditis"), ("B", "Aortic dissection"), ("C", "Pulmonary embolism"), ("D", "ST-elevation myocardial infarction"), ("E", "Tension pneumothorax")]
            correct = "B"
            explanation = (
                "Tearing pain to the back + **pulse/BP differential** and a new **diastolic murmur** (aortic regurgitation) are classic for **aortic dissection**."
            )
            rationales = {
                "A": "Pericarditis is pleuritic, positional, and typically follows a viral prodrome; no BP differential.",
                "B": "Correct — dissection produces AR and limb BP differences.",
                "C": "PE causes pleuritic pain and hypoxia; tearing pain and BP differential are not typical.",
                "D": "STEMI pain is pressure-like with ischemic ECG; no tearing pain or BP asymmetry.",
                "E": "Tension pneumothorax has hypotension, JVD, and absent breath sounds, not a diastolic murmur.",
            }
        elif topic == "Pulmonology" and qtype in ("Workup", "Management"):
            stem = (
                "A 48-year-old postoperative patient develops sudden pleuritic chest pain and dyspnea. HR 112, RR 24, SpO2 94% on room air. Hemodynamically stable. "
                "What is the most appropriate next diagnostic test?"
            )
            bank = [("A", "D-dimer"), ("B", "CT pulmonary angiography"), ("C", "Ventilation–perfusion scan"), ("D", "Transthoracic echocardiography"), ("E", "Serial troponins")]
            correct = "B"
            explanation = (
                "Moderate/high suspicion for **PE** in a **stable** patient → **CT pulmonary angiography** is the test of choice. D-dimer is for **low** suspicion only; V/Q is alternative when CTPA is contraindicated."
            )
            rationales = {
                "A": "D-dimer is useful to *rule out* PE in low-risk patients; not appropriate here.",
                "B": "Correct — first-line diagnostic test for PE in stable patients without contraindications.",
                "C": "V/Q scan is for patients who cannot receive contrast or have severe renal dysfunction.",
                "D": "Echo evaluates right heart strain but doesn’t confirm PE in stable cases.",
                "E": "Troponins assess myocardial injury, not PE diagnosis primarily.",
            }
        elif topic == "Endocrinology" and qtype in ("Management",):
            stem = (
                "A 24-year-old with type 1 diabetes presents with polyuria, abdominal pain, Kussmaul respirations, and glucose 520 mg/dL. "
                "Which is the most appropriate **initial** management step?"
            )
            bank = [("A", "IV insulin bolus"), ("B", "IV isotonic saline"), ("C", "IV sodium bicarbonate"), ("D", "Subcutaneous insulin"), ("E", "Broad-spectrum antibiotics")]
            correct = "B"
            explanation = (
                "**DKA** management prioritizes **aggressive isotonic fluids first** to restore perfusion, then **IV insulin** (with careful potassium management). Bicarbonate is rarely indicated."
            )
            rationales = {
                "A": "Insulin is essential but **after** initial fluid resuscitation.",
                "B": "Correct — fluids first in DKA.",
                "C": "Bicarbonate is reserved for severe acidosis with hemodynamic compromise; may worsen outcomes otherwise.",
                "D": "SubQ insulin absorption is unreliable in DKA.",
                "E": "No infection signs provided; treat if indicated, but not first step here.",
            }
        else:
            # Generic fallback (kept de-novo and NBME-like)
            import random
            dx_or_mgmt = random.choice(["diagnosis", "management", "workup", "mechanism"])
            stem = (f"A clinical vignette in {topic} requiring {dx_or_mgmt} is presented. Choose the best option.")
            bank = [("A", "Option 1"), ("B", "Option 2"), ("C", "Option 3"), ("D", "Option 4"), ("E", "Option 5")]
            correct = "A"
            explanation = ("NBME-like de-novo item. Focus on clinical reasoning with guideline-consistent choices.")
            rationales = {ltr: ("Correct." if ltr=="A" else "Less appropriate than the best answer given the vignette cues.") for ltr,_ in bank}

        shuffle(bank)
        # Rebuild rationales to current letters after shuffle
        new_rats = {}
        for ltr, _ in bank:
            new_rats[ltr] = rationales.get(ltr, "Less appropriate than the best answer.")
        return stem, bank, correct, explanation, new_rats

    # --- One-question-at-a-time state ---
    if "qb_current" not in st.session_state:
        st.session_state.qb_current = None
        st.session_state.qb_answer = None

    # Generate if none exists
    if st.session_state.qb_current is None:
        topic = pick_topic(qb_topic_choice)
        stem, choices, correct, expl, rats = generate_nbme_style_question(topic)
        st.session_state.qb_current = {
            "topic": topic,
            "stem": stem,
            "choices": choices,  # list of (letter, text)
            "correct": correct,
            "explanation": expl,
            "rationales": rats,
        }
        st.session_state.qb_answer = None

    cur = st.session_state.qb_current

    st.subheader(f"Topic: {cur['topic']}")
    st.write(cur["stem"])
    # Show answer choices clearly
    for letter, text_opt in cur["choices"]:
        st.markdown(f"- **{letter}.** {text_opt}")

    sel = st.radio("Your answer", [ltr for ltr, _ in cur["choices"]], horizontal=True, key="qb_single_ans")

    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("Check Answer", use_container_width=True):
            st.session_state.qb_answer = sel
            is_correct = (sel == cur["correct"]) if cur["correct"] in [ltr for ltr, _ in cur["choices"]] else False
            if is_correct:
                st.success(f"Correct! {sel}")
            else:
                st.error(f"Incorrect. Correct answer: {cur['correct']}")
            st.markdown("**Explanation**")
            st.info(cur["explanation"])
            st.markdown("**Why the other options are wrong**")
            for ltr, _ in cur["choices"]:
                if ltr == cur["correct"]:
                    continue
                st.write(f"**{ltr}.** {cur['rationales'].get(ltr, 'Less appropriate than the best answer.')}")

            # Auto-log to DB as AI_QBANK (FIXED newlines)
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO questions (raw_question, user_answer, correct_answer, explanation, qtype, topic_primary, topic_secondary, mistake_reason, source)
                    VALUES (:raw_question, :user_answer, :correct_answer, :explanation, :qtype, :topic_primary, :topic_secondary, :mistake_reason, :source)
                """), {
                    "raw_question": cur["stem"] + "\n\n" + "\n".join([f"{l}. {t}" for l, t in cur["choices"]]),
                    "user_answer": sel,
                    "correct_answer": cur["correct"],
                    "explanation": cur["explanation"],
                    "qtype": "Mixed",  # randomized types like exam
                    "topic_primary": cur["topic"],
                    "topic_secondary": None,
                    "mistake_reason": "",
                    "source": "AI_QBANK",
                })
            st.caption("Saved to log as AI_QBANK ✅")

    with c2:
        if st.button("Another Question", use_container_width=True):
            # Generate a new single question (respect user's topic selection / random)
            topic = pick_topic(qb_topic_choice)
            stem, choices, correct, expl, rats = generate_nbme_style_question(topic)
            st.session_state.qb_current = {
                "topic": topic,
                "stem": stem,
                "choices": choices,
                "correct": correct,
                "explanation": expl,
                "rationales": rats,
            }
            st.session_state.qb_answer = None

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
        # Compute correctness dynamically (safe if blanks)
        df["is_correct"] = (df["user_answer"].fillna("").str.strip().str.upper()
                            == df["correct_answer"].fillna("").str.strip().str.upper())

        # --- Filters ---
        colf1, colf2, colf3 = st.columns([1,1,2])
        with colf1:
            source_opt = st.selectbox("Source", ["(all)", "USER_PASTED", "AI_QBANK"])
        with colf2:
            topic_opt = st.selectbox("Primary Topic", ["(all)"] + sorted([t for t in df["topic_primary"].dropna().unique()]))
        with colf3:
            qtype_opt = st.selectbox("Question Type", ["(all)"] + sorted([q for q in df["qtype"].dropna().unique()]))

        mask = pd.Series(True, index=df.index)
        if source_opt != "(all)":
            mask &= (df["source"] == source_opt)
        if topic_opt != "(all)":
            mask &= (df["topic_primary"] == topic_opt)
        if qtype_opt != "(all)":
            mask &= (df["qtype"] == qtype_opt)

        fdf = df.loc[mask].copy()

        # Overview
        st.subheader("Overview")
        cA, cB, cC = st.columns(3)
        with cA:
            st.metric("Total (filtered)", len(fdf))
        with cB:
            st.metric("Accuracy", f"{(100*fdf['is_correct'].mean() if len(fdf)>0 else 0):.0f}%")
        with cC:
            st.metric("Total (all)", len(df))

        col1, col2 = st.columns(2)
        with col1:
            st.write("**By Primary Topic**")
            st.bar_chart(fdf["topic_primary"].value_counts())
        with col2:
            st.write("**By Question Type**")
            st.bar_chart(fdf["qtype"].value_counts())

        st.subheader("Detailed Table")
        st.dataframe(fdf, use_container_width=True)

        # --- Download CSV of filtered view ---
        csv = fdf.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Download filtered CSV",
            data=csv,
            file_name="step2hub_filtered.csv",
            mime="text/csv",
        )
