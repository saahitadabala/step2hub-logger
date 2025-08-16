import streamlit as st
import random
import time
import datetime
import json

# --- Your existing imports and DB setup here ---

# ✅ List of topics
TOPICS = ["Cardiology", "Pulmonology", "Gastroenterology", "Nephrology", "Obstetrics & Gynecology", "Neurology", "Endocrinology", "Infectious Disease", "Psychiatry"]

# ✅ Example AI question generator
def generate_ai_question(topic=None):
    if topic is None or topic == "(Random)":
        topic = random.choice(TOPICS)
    # For now, just a sample pool. Expand as needed.
    pool = {
        "Gastroenterology": {
            "stem": "A 32-year-old woman presents with intermittent abdominal pain and alternating constipation and diarrhea. Workup is normal. What is the most likely diagnosis?",
            "choices": ["Celiac disease", "Irritable bowel syndrome", "Crohn disease", "Small intestinal bacterial overgrowth", "Colon cancer"],
            "answer": "Irritable bowel syndrome",
            "explanation": (
                "This patient meets Rome IV criteria for IBS: recurrent abdominal pain associated with bowel movements, "
                "change in stool frequency/consistency, and no red flag signs (no weight loss, anemia, or nocturnal symptoms). "
                "Celiac disease would typically show abnormal serologies, Crohn disease has inflammation, SIBO occurs with predisposition "
                "to stasis, and colon cancer presents with red flag features."
            )
        },
        "Cardiology": {
            "stem": "A 64-year-old man with long-standing hypertension presents with acute tearing chest pain radiating to the back. Blood pressure is unequal in both arms. What is the most likely diagnosis?",
            "choices": ["Acute myocardial infarction", "Aortic dissection", "Pulmonary embolism", "Stable angina", "Esophageal rupture"],
            "answer": "Aortic dissection",
            "explanation": (
                "The classic description of tearing chest pain radiating to the back, pulse/BP differential, and hypertension history is "
                "hallmark for aortic dissection. MI typically causes crushing chest pain without pulse differential; PE causes dyspnea and pleuritic pain."
            )
        }
    }
    return pool.get(topic, pool["Gastroenterology"])

# --- Streamlit UI ---

def practice_tab():
    st.header("🧠 Practice QBank (AI)")
    st.write("NBME-style questions, one at a time. Pick a topic or go random.")

    topic_choice = st.selectbox("Choose Topic", ["(Random)"] + TOPICS)
    if st.button("Generate New Question"):
        st.session_state["ai_question"] = generate_ai_question(None if topic_choice == "(Random)" else topic_choice)
        st.session_state["ai_answered"] = False

    if "ai_question" in st.session_state:
        q = st.session_state["ai_question"]
        st.markdown(f"**Q: {q['stem']}**")

        selected = st.radio("Choose an answer:", q["choices"], index=None)
        if st.button("Check Answer"):
            if not selected:
                st.warning("Select an answer first.")
            else:
                st.session_state["ai_answered"] = True
                st.session_state["ai_selected"] = selected

        if st.session_state.get("ai_answered"):
            correct = q["answer"]
            chosen = st.session_state["ai_selected"]
            if chosen == correct:
                st.success(f"✅ Correct! The answer is **{correct}**.")
            else:
                st.error(f"❌ Incorrect. You chose **{chosen}**. The correct answer is **{correct}**.")
            st.info(q["explanation"])

            # ✅ Log automatically
            log_entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "topic": topic_choice if topic_choice != "(Random)" else q.get("topic", "Unknown"),
                "raw_question": q["stem"],  # ✅ fixed: no stray + "
                "answer": chosen,
                "correct": chosen == correct,
                "source": "AI_QBANK"
            }
            try:
                with open("ai_logs.jsonl", "a") as f:
                    f.write(json.dumps(log_entry) + "\n")
            except Exception as e:
                st.warning(f"Logging failed: {e}")

# --- Main App ---
def main():
    tabs = st.tabs(["Logger", "Practice QBank (AI)"])
    with tabs[0]:
        st.write("📘 Your Step2Hub Logger")
        # your existing logger tab code here
    with tabs[1]:
        practice_tab()

if __name__ == "__main__":
    main()
