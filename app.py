import os
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from nba_api.stats.static import players
from nba_api.stats.endpoints import playercareerstats, playerdashboardbygeneralsplits, commonplayerinfo
import google.generativeai as genai
from langchain_openai import ChatOpenAI
from langchain_nvidia_ai_endpoints import ChatNVIDIA
import re

# GLOBAL SETTINGS - CHANGE THIS TO SWITCH ORGANIZER MODELS: "qwen3.6" or "glm-5.1"
MODEL_PROVIDER = "glm-5.1"

# Setup Environment
load_dotenv()

def get_secret(key):
    # 1. Try local environment variable (.env)
    value = os.getenv(key)
    if value:
        return value
    # 2. Try Streamlit Cloud secrets
    if key in st.secrets:
        return st.secrets[key]
    return None

# Retrieve Keys
GOOGLE_API_KEY = get_secret("GOOGLE_API_KEY")
NVIDIA_API_KEY = get_secret("NVIDIA_API_KEY")
APP_PASSWORD = get_secret("APP_PASSWORD")

# Configure Gemini
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    worker_model = genai.GenerativeModel('gemini-2.5-flash-lite')

# 1. Updated Password Protection Logic
def check_password():
    """Returns True if the user had the correct password."""
    def password_entered():
        # Compare against the APP_PASSWORD retrieved via our helper function
        if st.session_state["password"] == APP_PASSWORD:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Enter App Password to Unlock", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Enter App Password to Unlock", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorrect")
        return False
    else:
        return True


if not check_password():
    st.stop()

# Initialize Session State
if 'player_data' not in st.session_state:
    st.session_state.player_data = None
if 'analysis_report' not in st.session_state:
    st.session_state.analysis_report = None
if 'comp_stats' not in st.session_state:
    st.session_state.comp_stats = []


# --- Data Retrieval Function ---
def get_player_data(full_name):
    player_dict = players.get_players()
    player = [p for p in player_dict if p['full_name'].lower() == full_name.lower()]
    if not player:
        return None
    p_id = player[0]['id']

    career = playercareerstats.PlayerCareerStats(player_id=p_id)
    df_career = career.get_data_frames()[0]

    info = commonplayerinfo.CommonPlayerInfo(player_id=p_id)
    info_df = info.get_data_frames()[0]

    adv = playerdashboardbygeneralsplits.PlayerDashboardByGeneralSplits(player_id=p_id)
    df_adv = adv.get_data_frames()[0]

    return {
        "name": player[0]['full_name'],
        "id": p_id,
        "career_stats": df_career,
        "advanced_stats": df_adv,
        "profile": {
            "Height": info_df['HEIGHT'].values[0],
            "Weight": info_df['WEIGHT'].values[0],
            "Position": info_df['POSITION'].values[0],
            "Draft Year": info_df['DRAFT_YEAR'].values[0]
        }
    }


# --- Multi-Agent Research Function ---
def run_comparison_research(player_name, player_data):
    # LAZY INITIALIZATION: Only connect to the Organizer when the function runs
    if MODEL_PROVIDER == "glm-5.1":
        organizer_llm = ChatNVIDIA(
            model="z-ai/glm-5.1",
            nvidia_api_key=NVIDIA_API_KEY  # Change from os.getenv to the variable
        )
    else:
        organizer_llm = ChatOpenAI(
            model="qwen3.6",
            base_url="http://localhost:11434/v1",
            api_key="ollama"
        )

    stats_summary = player_data['career_stats'].tail(5).to_string()
    profile = player_data['profile']

    worker_prompt = f"""
    Research NBA player: {player_name}.
    Physicals: {profile}
    Recent Stats: {stats_summary}

    TASKS:
    1. Analyze play-style/tendencies.
    2. List 3-5 strengths/weaknesses.
    3. Identify 5 specific current or historical NBA players as "comparisons".

    CRITICAL: At the end of your response, list the 5 names clearly on a new line prefixed with "NAMES:" separated by commas.
    """

    raw_research = worker_model.generate_content(worker_prompt).text

    organizer_prompt = f"""
    You are a Lead Scout. Format this raw research into a professional memo for {player_name}:
    {raw_research}
    """

    final_memo = organizer_llm.invoke(organizer_prompt).content

    name_match = re.search(r"NAMES:\s*(.*)", raw_research)
    comp_names = [n.strip() for n in name_match.group(1).split(",")] if name_match else []

    return final_memo, comp_names


# --- Streamlit UI ---
st.title("🏀 NBA Advanced Scout & Comp Engine")
player_input = st.text_input("Search NBA Player Name")

if st.button("Pull Player Stats"):
    with st.spinner("Accessing NBA Database..."):
        data = get_player_data(player_input)
        if data:
            st.session_state.player_data = data
            st.session_state.analysis_report = None
            st.session_state.comp_stats = []
        else:
            st.error("Player not found.")

if st.session_state.player_data:
    p = st.session_state.player_data
    st.header(f"Results for {p['name']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Height", p['profile']['Height'])
    col2.metric("Weight", p['profile']['Weight'])
    col3.metric("Position", p['profile']['Position'])

    st.subheader("Career Stats")
    st.dataframe(p['career_stats'])
    st.subheader("Advanced Stats (Current Season/Splits)")
    st.dataframe(p['advanced_stats'])

    if st.button("Analyze Player & Find Comps"):
        with st.status("🔍 Agent Pipeline Active...", expanded=True) as status:
            try:
                st.write("🤖 Worker Agent: Researching tendencies...")
                memo, comp_names = run_comparison_research(p['name'], p)
                st.session_state.analysis_report = memo

                st.write("📊 NBA_API: Fetching comparison stats...")
                found_comp_stats = []
                for name in comp_names[:5]:
                    st.write(f" 👉 Pulling: {name}")
                    comp_data = get_player_data(name)
                    if comp_data:
                        found_comp_stats.append(comp_data)
                st.session_state.comp_stats = found_comp_stats
                status.update(label="✅ Analysis Complete!", state="complete", expanded=False)
            except Exception as e:
                status.update(label="❌ Provider Error", state="error", expanded=True)
                st.error(
                    f"The {MODEL_PROVIDER} provider is currently unavailable (502 Bad Gateway). Please switch MODEL_PROVIDER to 'qwen3.6' to use your local model instead.")

if st.session_state.comp_stats:
    st.divider()
    st.header("🔍 Comparison Player Data")
    for c_data in st.session_state.comp_stats:
        with st.expander(f"Stats for Comparison: {c_data['name']}"):
            st.write(
                f"**Physicals:** {c_data['profile']['Height']} | {c_data['profile']['Weight']} | {c_data['profile']['Position']}")
            st.dataframe(c_data['career_stats'].tail(5))

if st.session_state.analysis_report:
    st.subheader("📊 Executive Scouting Memo")
    st.markdown(st.session_state.analysis_report)