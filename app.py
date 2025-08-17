import streamlit as st
import pandas as pd
import re
import os
from random import choice as rnd_choice, shuffle
from sqlalchemy import create_engine, text

# =========================
# Database Setup
# =========================
DB_MODE = os.getenv("DB_MODE", "sqlite")  # "sqlite" or "postgres"
if DB_MODE == "postgres":
    DB_URL = os.getenv("DB_URL")
    engine = create_engine(DB_URL, pool_pre_ping=True)
else:
    DB_URL = "sqlite:///questions.db"
    engine = create_engine(DB_URL)

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
        # best-effort migration for "source"
        try:
            conn.execute(text("ALTER TABLE questions ADD COLUMN source TEXT DEFAULT 'USER_PASTED'"))
        except Exception:
            pass

# =========================
# Classification helpers (used for Logger tab only)
# =========================
TOPIC_SEEDS = {
    "Cardiology": ["chest pain", "murmur", "mi", "st elevation", "troponin", "afib", "hypertension"],
    "Pulmonology": ["dyspnea", "wheezing", "asthma", "copd", "pneumonia", "hypoxemia"],
    "Gastroenterology": ["abdominal pain", "diarrhea", "constipation", "ibs", "bloating", "rectal bleeding"],
    "Nephrology": ["aki", "ckd", "proteinuria", "hematuria", "casts", "dialysis", "oliguria", "anuria", "edema"],
    "ObGyn": ["pregnant", "gestation", "lmp", "miscarriage", "postpartum", "contraception"],
    "Endocrinology": ["diabetes", "thyroid", "cortisol", "adrenal", "pituitary"],
    "Psychiatry": ["anxiety", "depression", "psychosis", "bipolar", "ptsd", "substance"],
    "Neurology": ["seizure", "stroke", "weakness", "ms", "parkinson", "neuropathy"],
    "Dermatology": ["rash", "lesion", "eczema", "psoriasis", "melanoma"],
    "HemeOnc": ["anemia", "lymphoma", "leukemia", "thrombocytopenia", "bleeding"],
}

QTYPE_SEEDS = {
    "Diagnosis": ["most likely diagnosis", "diagnosis", "dx"],
    "Management": ["next step", "management", "initial treatment", "therapy"],
    "Workup": ["initial diagnostic test", "best initial test", "next diagnostic step"],
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
            if seed in text_lower:
                if topic == "Nephrology":
                    if re.search(r"(proteinuria|hematuria|casts|aki|ckd|oliguria|anuria|dialysis|edema)", text_lower):
                        scores[topic] += 2
                else:
                    scores[topic] += 1

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

# =========================
# NBME-style templates (AI QBank)
#   - Each entry has one realistic vignette with A–E options.
#   - We randomize order and map to letters, preserving correct/rationales.
# =========================
TEMPLATES = [
    # Cardiology
    {
        "topic": "Cardiology", "qtype": "Diagnosis",
        "stem": ("A 65-year-old man with long-standing hypertension develops sudden severe tearing chest pain radiating "
                 "to the back. Blood pressure is higher in the right arm than the left. A new early diastolic murmur is "
                 "heard at the left sternal border. Which of the following is the most likely diagnosis?"),
        "options": ["Acute pericarditis", "Aortic dissection", "Pulmonary embolism",
                    "ST-elevation myocardial infarction", "Tension pneumothorax"],
        "correct": "Aortic dissection",
        "rationales": {
            "Acute pericarditis": "Typically pleuritic and positional chest pain after a viral prodrome; no pulse/BP differential.",
            "Aortic dissection": "Correct — tearing pain to the back with limb BP differential and new AR murmur.",
            "Pulmonary embolism": "Pleuritic pain and hypoxia are more typical; no AR murmur or limb BP difference.",
            "ST-elevation myocardial infarction": "Pressure-like pain with ischemic ECG; tearing pain and BP asymmetry suggest dissection.",
            "Tension pneumothorax": "Hypotension, JVD, and absent breath sounds dominate; murmur and BP differential not expected."
        },
        "explanation": ("A classic presentation of ascending aortic dissection includes tearing chest pain radiating to the back, "
                        "pulse/BP differential, and acute aortic regurgitation (early diastolic murmur).")
    },
    {
        "topic": "Cardiology", "qtype": "Management",
        "stem": ("A 72-year-old man has crushing chest pain at rest for 30 minutes with diaphoresis. "
                 "ECG shows 2-mm ST elevations in leads II, III, and aVF. What is the most appropriate next step in management?"),
        "options": ["Immediate aspirin and emergent PCI activation", "Schedule an outpatient stress test",
                    "Give sublingual nitroglycerin only and discharge",
                    "Order a transthoracic echocardiogram and observe",
                    "Start heparin and wait 24 hours before intervention"],
        "correct": "Immediate aspirin and emergent PCI activation",
        "rationales": {
            "Immediate aspirin and emergent PCI activation": "Correct — reperfusion is time critical for STEMI.",
            "Schedule an outpatient stress test": "Stress testing is contraindicated during active ischemia.",
            "Give sublingual nitroglycerin only and discharge": "Unsafe disposition; delays definitive reperfusion.",
            "Order a transthoracic echocardiogram and observe": "Echo should not delay reperfusion in a clear STEMI.",
            "Start heparin and wait 24 hours before intervention": "Anticoagulation alone is not definitive therapy for STEMI."
        },
        "explanation": ("Inferior STEMI requires immediate antiplatelet therapy and prompt reperfusion (PCI within the guideline window).")
    },
    {
        "topic": "Cardiology", "qtype": "Workup",
        "stem": ("A 58-year-old woman reports substernal chest pressure reliably provoked by brisk walking for 3 months. "
                 "Her resting ECG is normal. Which is the best initial diagnostic test?"),
        "options": ["Exercise treadmill ECG stress test", "CT coronary angiography",
                    "Cardiac MRI with gadolinium", "Serial troponins", "BNP level"],
        "correct": "Exercise treadmill ECG stress test",
        "rationales": {
            "Exercise treadmill ECG stress test": "Correct — first-line for stable angina with interpretable baseline ECG.",
            "CT coronary angiography": "Reasonable in some cases but not first-line for classic stable angina.",
            "Cardiac MRI with gadolinium": "Useful for tissue characterization; not the best initial test here.",
            "Serial troponins": "For suspected ACS; this is chronic stable symptoms.",
            "BNP level": "Assesses heart failure, not ischemia evaluation."
        },
        "explanation": ("Stable, exertional symptoms with normal resting ECG and ability to exercise → exercise treadmill ECG is the initial test.")
    },

    # Gastroenterology
    {
        "topic": "Gastroenterology", "qtype": "Management",
        "stem": ("A 28-year-old woman has 8 months of crampy lower abdominal pain with 3–4 loose stools/day. Pain improves after defecation. "
                 "There is no weight loss or GI bleeding. Basic labs (CBC, CRP) and celiac serology are normal. What is the next best step?"),
        "options": ["Colonoscopy", "Fecal fat quantification", "CT abdomen with contrast",
                    "No further testing necessary", "Stool ova and parasites (×3)"],
        "correct": "No further testing necessary",
        "rationales": {
            "Colonoscopy": "Reserved for alarm features or age-appropriate screening.",
            "Fecal fat quantification": "Used for suspected malabsorption with steatorrhea/weight loss.",
            "CT abdomen with contrast": "For intra-abdominal pathology or alarm features, not classic IBS.",
            "No further testing necessary": "Correct — classic IBS without alarms after minimal evaluation.",
            "Stool ova and parasites (×3)": "Used with travel/exposure risk; not suggested here."
        },
        "explanation": ("Chronic abdominal pain related to defecation with altered stool form/frequency without alarm features and with normal basic workup "
                        "is consistent with IBS; invasive testing is not indicated.")
    },

    # Pulmonology
    {
        "topic": "Pulmonology", "qtype": "Workup",
        "stem": ("A 48-year-old postoperative patient develops sudden pleuritic chest pain and dyspnea. HR 112/min, RR 24/min, SpO₂ 94% on room air. "
                 "He is hemodynamically stable. What is the most appropriate next diagnostic test?"),
        "options": ["D-dimer", "CT pulmonary angiography", "Ventilation–perfusion scan",
                    "Transthoracic echocardiography", "Serial troponins"],
        "correct": "CT pulmonary angiography",
        "rationales": {
            "D-dimer": "Useful to rule out PE in low-risk patients; not appropriate at moderate/high suspicion.",
            "CT pulmonary angiography": "Correct — first-line diagnostic test for stable patients when not contraindicated.",
            "Ventilation–perfusion scan": "Alternative when CTPA is contraindicated or renal function is poor.",
            "Transthoracic echocardiography": "Assesses right heart strain; does not confirm PE in a stable patient.",
            "Serial troponins": "Primarily evaluate myocardial injury."
        },
        "explanation": ("In a stable patient with moderate/high suspicion for PE, CTPA is preferred; V/Q is used when contrast cannot be given.")
    },

    # Endocrinology
    {
        "topic": "Endocrinology", "qtype": "Management",
        "stem": ("A 24-year-old with type 1 diabetes presents with abdominal pain, Kussmaul respirations, and glucose 520 mg/dL. "
                 "Which is the most appropriate initial management step?"),
        "options": ["IV insulin bolus", "IV isotonic saline", "IV sodium bicarbonate", "Subcutaneous insulin", "Broad-spectrum antibiotics"],
        "correct": "IV isotonic saline",
        "rationales": {
            "IV insulin bolus": "Insulin is essential but after initial fluid resuscitation.",
            "IV isotonic saline": "Correct — fluids first in DKA to restore perfusion.",
            "IV sodium bicarbonate": "Rarely indicated; may worsen outcomes if used indiscriminately.",
            "Subcutaneous insulin": "Absorption is unreliable during DKA.",
            "Broad-spectrum antibiotics": "Treat if infection suspected, but not the initial step here."
        },
        "explanation": ("DKA management prioritizes aggressive fluid resuscitation before insulin; potassium must be monitored closely.")
    },

    # Neurology
    {
        "topic": "Neurology", "qtype": "Workup",
        "stem": ("A 69-year-old develops sudden right-sided weakness and expressive aphasia 45 minutes ago. BP 168/94. "
                 "What is the best initial diagnostic test?"),
        "options": ["Non-contrast CT of the head", "MRI brain with diffusion", "CT angiography of head and neck",
                    "EEG", "Carotid duplex ultrasound"],
        "correct": "Non-contrast CT of the head",
        "rationales": {
            "Non-contrast CT of the head": "Correct — first to exclude intracranial hemorrhage before thrombolysis.",
            "MRI brain with diffusion": "Highly sensitive for ischemia but not first step in acute evaluation.",
            "CT angiography of head and neck": "Useful after hemorrhage is excluded to evaluate vessels.",
            "EEG": "Not part of initial stroke evaluation.",
            "Carotid duplex ultrasound": "Outpatient evaluation; not the first step in acute stroke."
        },
        "explanation": ("In suspected acute stroke, immediate non-contrast head CT is required to rule out hemorrhage and determine eligibility for reperfusion therapy.")
    },

    # ObGyn
    {
        "topic": "ObGyn", "qtype": "Workup",
        "stem": ("A 28-year-old woman with 6 weeks of amenorrhea presents with lower abdominal pain and light vaginal bleeding. "
                 "β-hCG is positive. What is the most appropriate next step in evaluation?"),
        "options": ["Transvaginal ultrasound", "Endometrial biopsy", "Methotrexate therapy now",
                    "Repeat β-hCG in 1 week only", "Dilation and curettage immediately"],
        "correct": "Transvaginal ultrasound",
        "rationales": {
            "Transvaginal ultrasound": "Correct — first-line to evaluate for intrauterine vs ectopic pregnancy.",
            "Endometrial biopsy": "Not indicated in early pregnancy evaluation for ectopic.",
            "Methotrexate therapy now": "Treatment is considered after diagnosis; not prior to imaging confirmation.",
            "Repeat β-hCG in 1 week only": "Delays diagnosis and risks rupture.",
            "Dilation and curettage immediately": "Not first-line and risks terminating a viable intrauterine pregnancy."
        },
        "explanation": ("In a pregnant patient with pain/bleeding, TVUS is the initial step to localize the pregnancy and assess for ectopic.")
    },

    # Nephrology
    {
        "topic": "Nephrology", "qtype": "Diagnosis",
        "stem": ("A 70-year-old man with vomiting and poor intake has BUN 48 mg/dL and creatinine 2.1 mg/dL. "
                 "Urine sodium is 8 mEq/L and FeNa is 0.5%. Which of the following is the most likely cause of his acute kidney injury?"),
        "options": ["Acute tubular necrosis", "Pre-renal azotemia", "Acute interstitial nephritis",
                    "Post-renal obstruction", "Rapidly progressive glomerulonephritis"],
        "correct": "Pre-renal azotemia",
        "rationales": {
            "Acute tubular necrosis": "Typically FeNa >2% with muddy brown casts.",
            "Pre-renal azotemia": "Correct — low urine sodium and FeNa <1% suggest hypoperfusion.",
            "Acute interstitial nephritis": "Associated with eosinophils, rash, and fever after new drugs.",
            "Post-renal obstruction": "Hydronephrosis on imaging; not supported by low FeNa.",
            "Rapidly progressive glomerulonephritis": "Hematuria/proteinuria with casts and systemic features."
        },
        "explanation": ("Low FeNa and low urine sodium indicate sodium avidity due to hypoperfusion → pre-renal azotemia.")
    },

    # Infectious Disease
    {
        "topic": "Infectious Disease", "qtype": "Management",
        "stem": ("A 22-year-old college student presents with fever, headache, neck stiffness, and petechial rash. "
                 "She is somnolent but arousable. What is the most appropriate next step in management?"),
        "options": ["Start IV ceftriaxone and vancomycin immediately", "Obtain LP first, then start antibiotics",
                    "Order brain MRI, then LP", "Begin steroids only", "Observe for 6 hours and repeat exam"],
        "correct": "Start IV ceftriaxone and vancomycin immediately",
        "rationales": {
            "Start IV ceftriaxone and vancomycin immediately": "Correct — do not delay empiric therapy in suspected bacterial meningitis.",
            "Obtain LP first, then start antibiotics": "Antibiotics should not be delayed; cultures can be drawn before LP.",
            "Order brain MRI, then LP": "Imaging only if focal deficits/seizure/immunocompromise — still should not delay antibiotics.",
            "Begin steroids only": "Dexamethasone can be added, but antibiotics are urgent.",
            "Observe for 6 hours and repeat exam": "Dangerous delay in a rapidly progressive infection."
        },
        "explanation": ("Suspected bacterial meningitis is a medical emergency; initiate empiric antibiotics immediately after blood cultures.")
    },

    # Heme/Onc
    {
        "topic": "HemeOnc", "qtype": "Diagnosis",
        "stem": ("Minutes after the start of a blood transfusion, a patient develops fever, flank pain, and dark urine. "
                 "Blood pressure drops and oozing is noted at IV sites. Which of the following is the most likely diagnosis?"),
        "options": ["Anaphylactic reaction", "Acute hemolytic transfusion reaction", "Febrile non-hemolytic transfusion reaction",
                    "TRALI", "Urticarial reaction"],
        "correct": "Acute hemolytic transfusion reaction",
        "rationales": {
            "Anaphylactic reaction": "Hypotension with wheeze/angioedema, especially in IgA deficiency; hemoglobinuria less typical.",
            "Acute hemolytic transfusion reaction": "Correct — ABO incompatibility causes fever, flank pain, hemoglobinuria, DIC, and shock.",
            "Febrile non-hemolytic transfusion reaction": "Fever and chills only; due to cytokines in donor plasma.",
            "TRALI": "Acute hypoxemia and pulmonary edema within 6 hours; not hemoglobinuria/DIC.",
            "Urticarial reaction": "Pruritus and hives without systemic instability."
        },
        "explanation": ("Acute hemolytic transfusion reactions are due to ABO incompatibility leading to intravascular hemolysis, DIC, and shock.")
    },

    # Dermatology
    {
        "topic": "Dermatology", "qtype": "Management",
        "stem": ("A 42-year-old woman has a 1.2-cm irregularly pigmented lesion with asymmetric borders and color variation on the calf. "
                 "What is the most appropriate next step in management?"),
        "options": ["Excisional biopsy with narrow margins", "Shave biopsy", "Topical imiquimod",
                    "Wide local excision to fascia", "Observation in 3 months"],
        "correct": "Excisional biopsy with narrow margins",
        "rationales": {
            "Excisional biopsy with narrow margins": "Correct — diagnostic approach for suspected melanoma.",
            "Shave biopsy": "May transect lesion and underestimate depth.",
            "Topical imiquimod": "Used for superficial lesions (e.g., BCC); not for suspected melanoma.",
            "Wide local excision to fascia": "Definitive treatment determined by Breslow depth after diagnostic biopsy.",
            "Observation in 3 months": "Delays diagnosis of potentially invasive melanoma."
        },
        "explanation": ("Suspicious melanocytic lesions require full-thickness excisional biopsy with narrow margins to assess Breslow depth.")
    },

    # Psychiatry
    {
        "topic": "Psychiatry", "qtype": "Diagnosis",
        "stem": ("A patient on sertraline develops agitation, tremor, hyperreflexia, mydriasis, and hyperthermia after adding linezolid. "
                 "What is the most likely diagnosis?"),
        "options": ["Neuroleptic malignant syndrome", "Serotonin syndrome", "Anticholinergic toxicity",
                    "Malignant hyperthermia", "Opioid overdose"],
        "correct": "Serotonin syndrome",
        "rationales": {
            "Neuroleptic malignant syndrome": "Rigidity and hyporeflexia with dopaminergic blockade; slower onset.",
            "Serotonin syndrome": "Correct — triad of mental-status change, autonomic instability, and neuromuscular hyperactivity.",
            "Anticholinergic toxicity": "Dry skin, mydriasis, urinary retention; not hyperreflexia/clonus.",
            "Malignant hyperthermia": "Occurs with inhaled anesthetics/succinylcholine intra-op; rigidity, hypercarbia.",
            "Opioid overdose": "Miosis and respiratory depression; not hyperreflexia."
        },
        "explanation": ("Serotonin excess from SSRI + MAOI-like agent causes agitation, hyperthermia, and hyperreflexia/clonus.")
    },
]

TOPICS = sorted(set(t["topic"] for t in TEMPLATES))  # used in QBank UI

def pick_template(topic_choice: str):
    """Pick a random template for a specific topic or any topic if '(Random)'."""
    pool = [t for t in TEMPLATES if topic_choice == "(Random)" or t["topic"] == topic_choice]
    return rnd_choice(pool)

def assemble_question_from_template(tpl: dict):
    """Shuffle options, map to letters, and compute correct letter + per-letter rationales."""
    opts = tpl["options"][:]
    shuffle(opts)
    letters = ["A", "B", "C", "D", "E"]
    # Ensure 5 options; if fewer, pad with plausible distractor placeholders (rare)
    while len(opts) < 5:
        opts.append(f"Option {len(opts)+1}")
    opts = opts[:5]
    choices = list(zip(letters, opts))

    correct_text = tpl["correct"]
    # find letter that matches the correct option text
    correct_letter = next((ltr for ltr, txt in choices if txt == correct_text), "A")

    # map rationales by letter
    rats = {ltr: tpl["rationales"].get(txt, "Less appropriate than the best answer in this vignette.")
            for ltr, txt in choices}

    return {
        "topic": tpl["topic"],
        "qtype": tpl["qtype"],
        "stem": tpl["stem"],
        "choices": choices,             # list of (letter, text)
        "correct": correct_letter,      # "A".."E"
        "explanation": tpl["explanation"],
        "rationales": rats,
    }

# =========================
# Streamlit App
# =========================
st.set_page_config(page_title="Step2Hub — Logger", page_icon="🧠", layout="wide")
st.title("🧠 Step2Hub — Logger")
st.caption("Log NBME-style questions with AI-assisted classification + your own AI QBank")

init_db()

st.sidebar.header("Navigation")
page = st.sidebar.radio("Go to", ["Log Question", "Practice QBank (AI)", "Dashboard"])

# --------- Log Question ---------
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
            "Primary topic", [None] + sorted(TOPIC_SEEDS.keys()),
            index=(sorted(TOPIC_SEEDS.keys()).index(suggested_primary) + 1 if suggested_primary in TOPIC_SEEDS else 0)
        )
        topic_secondary = st.selectbox(
            "Secondary topic", [None] + sorted(TOPIC_SEEDS.keys()),
            index=(sorted(TOPIC_SEEDS.keys()).index(suggested_secondary) + 1 if suggested_secondary in TOPIC_SEEDS else 0)
        )
        qtype = st.selectbox(
            "Question type", [None, "Diagnosis", "Management", "Workup", "Mechanism", "Prognosis", "Ethics"],
            index=([None, "Diagnosis", "Management", "Workup", "Mechanism", "Prognosis", "Ethics"].index(suggested_qtype)
                   if suggested_qtype in {"Diagnosis","Management","Workup","Mechanism","Prognosis","Ethics"} else 0)
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

# --------- Practice QBank (AI) ---------
elif page == "Practice QBank (AI)":
    st.header("🧪 Practice QBank (AI)")
    colL, colR = st.columns([2,1])
    with colL:
        qb_topic_choice = st.selectbox("Topic", ["(Random)"] + sorted(TOPICS))
    with colR:
        if st.button("Generate New Question", use_container_width=True):
            st.session_state.pop("qb_current", None)
            st.session_state.pop("qb_answer", None)

    # Initialize state
    if "qb_current" not in st.session_state:
        st.session_state.qb_current = None
        st.session_state.qb_answer = None

    # Generate question if none
    if st.session_state.qb_current is None:
        tpl = pick_template(qb_topic_choice)
        st.session_state.qb_current = assemble_question_from_template(tpl)
        st.session_state.qb_answer = None

    cur = st.session_state.qb_current

    st.subheader(f"Topic: {cur['topic']}  ·  Type: {cur['qtype']}")
    st.write(cur["stem"])
    for letter, text_opt in cur["choices"]:
        st.markdown(f"- **{letter}.** {text_opt}")

    sel = st.radio("Your answer", [ltr for ltr, _ in cur["choices"]], horizontal=True, key="qb_single_ans")

    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("Check Answer", use_container_width=True):
            st.session_state.qb_answer = sel
            is_correct = (sel == cur["correct"])
            if is_correct:
                st.success(f"✅ Correct! {sel}")
            else:
                st.error(f"❌ Incorrect. Correct answer: {cur['correct']}")
            st.markdown("**Explanation**")
            st.info(cur["explanation"])
            st.markdown("**Why the other options are wrong**")
            for ltr, _ in cur["choices"]:
                if ltr == cur["correct"]:
                    continue
                st.write(f"**{ltr}.** {cur['rationales'].get(ltr, 'Less appropriate than the best answer.')}")

            # Log to DB as AI_QBANK (with proper newlines)
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO questions (raw_question, user_answer, correct_answer, explanation, qtype, topic_primary, topic_secondary, mistake_reason, source)
                    VALUES (:raw_question, :user_answer, :correct_answer, :explanation, :qtype, :topic_primary, :topic_secondary, :mistake_reason, :source)
                """), {
                    "raw_question": cur["stem"] + "\n\n" + "\n".join([f"{l}. {t}" for l, t in cur["choices"]]),
                    "user_answer": sel,
                    "correct_answer": cur["correct"],
                    "explanation": cur["explanation"],
                    "qtype": cur["qtype"],
                    "topic_primary": cur["topic"],
                    "topic_secondary": None,
                    "mistake_reason": "",
                    "source": "AI_QBANK",
                })
            st.caption("Saved to log as AI_QBANK ✅")

    with c2:
        if st.button("Another Question", use_container_width=True):
            tpl = pick_template(qb_topic_choice)
            st.session_state.qb_current = assemble_question_from_template(tpl)
            st.session_state.qb_answer = None

# --------- Dashboard ---------
elif page == "Dashboard":
    st.header("📊 Dashboard")
    with engine.begin() as conn:
        df = pd.read_sql("SELECT * FROM questions ORDER BY created_at DESC", conn)

    if df.empty:
        st.info("No questions logged yet.")
    else:
        df["is_correct"] = (
            df["user_answer"].fillna("").str.strip().str.upper()
            == df["correct_answer"].fillna("").str.strip().str.upper()
        )

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

        csv = fdf.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Download filtered CSV",
            data=csv,
            file_name="step2hub_filtered.csv",
            mime="text/csv",
        )
