import streamlit as st
import pandas as pd
import sqlalchemy
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os

# --- Database Setup ---
DB_MODE = os.getenv("DB_MODE", "sqlite")  # default = SQLite
if DB_MODE == "postgres":
    DB_URL = os.getenv("DB_URL")  # full Supabase/Postgres URL
else:
    DB_URL = "sqlite:///questions.db"  # local SQLite fallback

engine = create_engine(DB_URL, echo=True)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

# --- DB Model ---
class QuestionLog(Base):
    __tablename__ = "question_logs"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    raw_question = Column(Text)
    choices = Column(Text)
    user_answer = Column(String(50))
    correct_answer = Column(String(50))
    explanation = Column(Text)
    reasoning = Column(Text)
    qtype = Column(String(50))
    primary_topic = Column(String(50))
    secondary_topic = Column(String(50))

def init_db():
    Base.metadata.create_all(engine)

# --- Simple AI Guessing Helpers ---
def guess_question_type(text: str) -> str:
    """Naive keyword-based question type classifier."""
    text = text.lower()
    if "most likely diagnosis" in text or "what is the diagnosis" in text:
        return "Diagnosis"
    elif "next step" in text or "management" in text or "treatment" in text:
        return "Management"
    elif "mechanism" in text or "pathophysiology" in text:
        return "Mechanism"
    elif "risk factor" in text or "predisposes" in text:
        return "Risk Factor"
    else:
        return "Other"

def guess_topics(text: str):
    """Naive keyword-based topic guesser (primary + secondary)."""
    text = text.lower()
    if "heart" in text or "cardiac" in text or "chest pain" in text:
        return "Cardiology", "Cardiac symptoms"
    elif "liver" in text or "hepatitis" in text:
        return "Gastroenterology", "Hepatology"
    elif "kidney" in text or "creatinine" in text:
        return "Nephrology", "Renal function"
    elif "pregnant" in text or "obstetric" in text:
        return "Obstetrics", "Pregnancy"
    elif "depression" in text or "psychiatry" in text:
        return "Psychiatry", "Mood disorders"
    else:
        return "General", "Uncategorized"

# --- Streamlit App ---
def main():
    st.title("🧠 Step2Hub — Logger")
    st.write("Log your NBME/Step2 questions and track weak points.")

    menu = ["Log Question", "Dashboard"]
    choice = st.sidebar.selectbox("Menu", menu)

    if choice == "Log Question":
        log_question()
    elif choice == "Dashboard":
        show_dashboard()

def log_question():
    st.header("Log a Question")

    raw_question = st.text_area("Paste the Question")
    choices = st.text_area("Paste the Choices (optional)")
    user_answer = st.text_input("Your Answer")
    correct_answer = st.text_input("Correct Answer")
    reasoning = st.text_area("Why you chose your answer / reasoning")
    explanation = st.text_area("Paste NBME Explanation")

    if st.button("Submit"):
        # Auto guess type & topics
        combined_text = (raw_question or "") + " " + (explanation or "")
        suggested_qtype = guess_question_type(combined_text)
        primary_topic, secondary_topic = guess_topics(combined_text)

        new_log = QuestionLog(
            raw_question=raw_question,
            choices=choices,
            user_answer=user_answer,
            correct_answer=correct_answer,
            reasoning=reasoning,
            explanation=explanation,
            qtype=suggested_qtype,
            primary_topic=primary_topic,
            secondary_topic=secondary_topic,
        )

        session = SessionLocal()
        session.add(new_log)
        session.commit()
        session.close()

        st.success("Question logged successfully!")
        st.info(f"Auto-detected Type: **{suggested_qtype}**, Topics: **{primary_topic} → {secondary_topic}**")

def show_dashboard():
    st.header("📊 Dashboard")
    session = SessionLocal()
    data = session.query(QuestionLog).all()
    session.close()

    if not data:
        st.write("No questions logged yet.")
        return

    df = pd.DataFrame([{
        "Timestamp": q.timestamp,
        "Type": q.qtype,
        "Primary Topic": q.primary_topic,
        "Secondary Topic": q.secondary_topic,
        "Your Answer": q.user_answer,
        "Correct": q.correct_answer,
        "Reasoning": q.reasoning,
    } for q in data])

    st.dataframe(df)

    # Summary counts
    st.subheader("Summary")
    type_counts = df["Type"].value_counts()
    topic_counts = df["Primary Topic"].value_counts()

    st.write("**By Question Type:**")
    st.bar_chart(type_counts)

    st.write("**By Primary Topic:**")
    st.bar_chart(topic_counts)

# --- Run ---
if __name__ == "__main__":
    init_db()
    main()
