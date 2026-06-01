import os
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from nba_api.stats.static import players
from nba_api.stats.endpoints import playercareerstats, playerdashboardbygeneralsplits, commonplayerinfo
from google import genai
import re

# Setup Environment
load_dotenv()


def get_secret(key):
    value = os.getenv(key)
    if value:
        return value
    if key in st.secrets:
        return st.secrets[key]
    return None


# Retrieve Keys
GOOGLE_API_KEY = get_secret("GOOGLE_API_KEY")
APP_PASSWORD   = get_secret("APP_PASSWORD")

# Configure Gemini client
client = genai.Client(api_key=GOOGLE_API_KEY) if GOOGLE_API_KEY else None
MODEL  = "gemini-3.1-flash-lite"


# ── Password protection ───────────────────────────────────────────────────────
def check_password():
    """Returns True if the user has the correct password."""
    if st.session_state.get("password_correct"):
        return True

    with st.form("password_form"):
        st.text_input("Enter App Password to Unlock", type="password", key="password")
        submitted = st.form_submit_button("Unlock App")

    if submitted:
        if st.session_state["password"] == APP_PASSWORD:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("😕 Incorrect password. Access denied.")

    return False


if not check_password():
    st.stop()


# ── Session state ─────────────────────────────────────────────────────────────
if 'player_data' not in st.session_state:
    st.session_state.player_data = None
if 'analysis_report' not in st.session_state:
    st.session_state.analysis_report = None
if 'comp_stats' not in st.session_state:
    st.session_state.comp_stats = []


# ── Data retrieval ────────────────────────────────────────────────────────────
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
        "name":         player[0]['full_name'],
        "id":           p_id,
        "career_stats": df_career,
        "advanced_stats": df_adv,
        "profile": {
            "Height":     info_df['HEIGHT'].values[0],
            "Weight":     info_df['WEIGHT'].values[0],
            "Position":   info_df['POSITION'].values[0],
            "Draft Year": info_df['DRAFT_YEAR'].values[0]
        }
    }


# ── Multi-agent research ──────────────────────────────────────────────────────
def call_gemini(prompt: str, system: str = None) -> str:
    """Single helper for all Gemini calls in this app."""
    if not client:
        return "Error: GOOGLE_API_KEY not configured."
    from google.genai import types
    config = types.GenerateContentConfig(
        temperature=0.4,
        system_instruction=system,
    ) if system else types.GenerateContentConfig(temperature=0.4)
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=config
    )
    return response.text.strip()


def run_comparison_research(player_name, player_data):
    stats_summary = player_data['career_stats'].tail(5).to_string()
    profile       = player_data['profile']

    # ── Worker: research + comp identification ────────────────────────────────
    worker_prompt = f"""
Research NBA player: {player_name}.
Physicals: {profile}
Recent Stats (last 5 seasons):
{stats_summary}

TASKS:
1. Analyze play-style and tendencies.
2. List 3-5 key strengths and weaknesses.
3. Identify 5 specific current or historical NBA players as comparisons.

CRITICAL: At the end of your response, list the 5 comparison names clearly
on a new line prefixed with "NAMES:" separated by commas.
Example: NAMES: Michael Jordan, Kobe Bryant, Tracy McGrady, Paul George, Jimmy Butler
"""
    raw_research = call_gemini(worker_prompt)

    # ── Organizer: format into professional memo ──────────────────────────────
    organizer_system = """You are a Lead NBA Scout writing professional scouting memos
for front office executives. Your writing is concise, analytical, and formatted clearly
with sections and bullet points. Never use filler phrases."""

    organizer_prompt = f"""Format this raw scouting research into a professional
executive memo for {player_name}. Use clear sections: Overview, Strengths,
Weaknesses, and Comparable Players.

RAW RESEARCH:
{raw_research}"""

    final_memo = call_gemini(organizer_prompt, system=organizer_system)

    # ── Extract comp names ────────────────────────────────────────────────────
    name_match = re.search(r"NAMES:\s*(.*)", raw_research)
    comp_names = [n.strip() for n in name_match.group(1).split(",")] if name_match else []

    return final_memo, comp_names


# ── Streamlit UI ──────────────────────────────────────────────────────────────
st.title("🏀 NBA Advanced Scout & Comp Engine")

player_input = st.text_input("Search NBA Player Name")

if st.button("Pull Player Stats"):
    with st.spinner("Accessing NBA Database..."):
        data = get_player_data(player_input)
        if data:
            st.session_state.player_data       = data
            st.session_state.analysis_report   = None
            st.session_state.comp_stats        = []
        else:
            st.error("Player not found. Check spelling and use full name (e.g. 'LeBron James').")

if st.session_state.player_data:
    p = st.session_state.player_data
    st.header(f"Results for {p['name']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Height",   p['profile']['Height'])
    col2.metric("Weight",   p['profile']['Weight'])
    col3.metric("Position", p['profile']['Position'])

    st.subheader("Career Stats")
    st.dataframe(p['career_stats'])

    st.subheader("Advanced Stats (Current Season/Splits)")
    st.dataframe(p['advanced_stats'])

    if st.button("Analyze Player & Find Comps"):
        with st.status("🔍 Agent Pipeline Active...", expanded=True) as status:
            try:
                st.write("🤖 Worker Agent (Gemini): Researching tendencies...")
                memo, comp_names = run_comparison_research(p['name'], p)
                st.session_state.analysis_report = memo

                st.write("📊 NBA API: Fetching comparison player stats...")
                found_comp_stats = []
                for name in comp_names[:5]:
                    st.write(f"  👉 Pulling: {name}")
                    comp_data = get_player_data(name)
                    if comp_data:
                        found_comp_stats.append(comp_data)
                st.session_state.comp_stats = found_comp_stats
                status.update(label="✅ Analysis Complete!", state="complete", expanded=False)

            except Exception as e:
                status.update(label="❌ Error", state="error", expanded=True)
                st.error(f"Analysis failed: {str(e)}")

if st.session_state.comp_stats:
    st.divider()
    st.header("🔍 Comparison Player Data")
    for c_data in st.session_state.comp_stats:
        with st.expander(f"Stats for: {c_data['name']}"):
            st.write(
                f"**Physicals:** {c_data['profile']['Height']} | "
                f"{c_data['profile']['Weight']} | {c_data['profile']['Position']}"
            )
            st.dataframe(c_data['career_stats'].tail(5))

if st.session_state.analysis_report:
    st.divider()
    st.subheader("📊 Executive Scouting Memo")
    st.markdown(st.session_state.analysis_report)