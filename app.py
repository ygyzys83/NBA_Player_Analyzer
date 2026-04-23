import os
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from nba_api.stats.static import players
from nba_api.stats.endpoints import playercareerstats, playerdashboardbygeneralsplits, commonplayerinfo
import google.generativeai as genai
from langchain_openai import ChatOpenAI

# 1. Setup Environment
load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# 2. Define Multi-Model Agents
# Worker Agent (Cloud-based for research tasks)
worker_model = genai.GenerativeModel('gemini-2.5-flash-lite')

# Organizer Agent (Local Ollama via LangChain)
organizer_llm = ChatOpenAI(
    model="qwen3.6",
    base_url="http://localhost:11434/v1",
    api_key="ollama"  # Required placeholder
)

# 3. Data Retrieval Functions
def get_player_data(full_name):
    player_dict = players.get_players()
    player = [p for p in player_dict if p['full_name'].lower() == full_name.lower()]
    if not player:
        return None
    player_id = player[0]['id']

    # Career Stats
    career = playercareerstats.PlayerCareerStats(player_id=player_id)
    df = career.get_data_frames()[0]

    # Physical Profile & Info
    # This endpoint provides HEIGHT and WEIGHT as seen in official documentation
    info = commonplayerinfo.CommonPlayerInfo(player_id=player_id)
    info_df = info.get_data_frames()[0]
    physical_profile = {
        "height": info_df['HEIGHT'].values[0],
        "weight": info_df['WEIGHT'].values[0],
        "position": info_df['POSITION'].values[0]
    }

    # Advanced Stats
    adv = playerdashboardbygeneralsplits.PlayerDashboardByGeneralSplits(player_id=player_id)
    adv_df = adv.get_data_frames()[0]

    return {"id": player_id, "stats": df, "advanced": adv_df, "profile": physical_profile}

# 4. Multi-Agent Logic
def run_comparison_research(player_name, player_data):
    # Prepare data for the researcher
    stats_summary = player_data['stats'].tail(3).to_string()
    profile = player_data['profile']

    # 1. Worker Agent (Gemini 2.5 Flash-Lite): The Researcher
    # This model uses its internet access to find the 5 player comps.
    worker_prompt = f"""
    Perform deep research on NBA player: {player_name}.

    PHYSICAL PROFILE: Height: {profile['height']}, Weight: {profile['weight']}, Position: {profile['position']}
    RECENT STATS:
    {stats_summary}

    YOUR TASKS:
    1. Analyze play-style and tendencies (e.g., shot selection, defensive role, ball-handling).
    2. Identify 3-5 specific strengths and 3-5 weaknesses.
    3. Use your internet search capabilities to find 5 current or historical NBA players who are the 
       closest "comparisons" to {player_name}. Consider stats, physicals, and style.

    Output this as raw, detailed research notes for an organizer to review.
    """

    # Run the worker research
    raw_research_notes = worker_model.generate_content(worker_prompt).text

    # 2. Organizer Agent (Local Qwen 3.6): The Editor
    # Qwen 3.6 will now structure the worker's notes into a comprehensive memo.
    organizer_prompt = f"""
    You are the Lead Basketball Operations Organizer. You have received raw research on {player_name}:

    --- RAW RESEARCH DATA ---
    {raw_research_notes}
    --- END RAW DATA ---

    TASK: Compile this research into a professional and comprehensive scouting memo.
    The memo must:
    - Detail {player_name}'s play-style and tendencies.
    - List the 5 closest comparisons found in the research.
    - For EACH comparison, provide specific reasons (physical, statistical, or stylistic) why they match.
    - Use clean Markdown (headers, bolding, bullet points) for a Streamlit app.
    """

    final_memo = organizer_llm.invoke(organizer_prompt).content
    return final_memo

st.title("🏀 NBA Player Comp Engine")
player_input = st.text_input("Enter NBA Player Name (e.g., Victor Wembanyama)")

if st.button("Analyze Player"):
    with st.spinner("Fetching NBA Data & Running Agents..."):
        data = get_player_data(player_input)
        if data:
            st.subheader(f"Stats for {player_input}")
            st.dataframe(data['stats'].tail())  # Show recent seasons
            report = run_comparison_research(player_input, data)
            st.markdown("### 📊 Agent Comparison Report")
            st.write(report)
        else:
            st.error("Player not found. Please check the spelling.")