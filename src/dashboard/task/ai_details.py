"""AI Task Details - Display AI conversation history and generation details"""
import streamlit as st
import json
from typing import List, Dict, Any, Optional
from datetime import datetime


def render_ai_conversation(conversation_history: List[Dict[str, Any]], section_title: str):
    """
    Render AI conversation history for a specific section/disease.
    
    Args:
        conversation_history: List of conversation entries
        section_title: Title/name of the section or disease
    """
    if not conversation_history:
        st.info("No AI conversation recorded")
        return
    
    st.markdown(f"### 🤖 AI Generation Details: {section_title}")
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    
    total_tokens = sum(entry.get("tokens", {}).get("total", 0) for entry in conversation_history)
    total_time = sum(entry.get("duration", 0) for entry in conversation_history)
    agents_used = set(entry.get("agent", "unknown") for entry in conversation_history)
    
    col1.metric("Total Steps", len(conversation_history))
    col2.metric("Total Tokens", f"{total_tokens:,}")
    col3.metric("Total Time", f"{total_time:.1f}s")
    col4.metric("Agents Used", len(agents_used))
    
    st.markdown("---")
    
    # Display each conversation entry
    for i, entry in enumerate(conversation_history, 1):
        agent = entry.get("agent", "Unknown")
        timestamp = entry.get("timestamp", "")
        model = entry.get("model", "Unknown")
        tokens = entry.get("tokens", {})
        duration = entry.get("duration", 0)
        
        # Agent icon mapping
        agent_icons = {
            "analyst": "📊",
            "writer": "✍️",
            "reviewer": "🔍"
        }
        icon = agent_icons.get(agent.lower(), "🤖")
        
        # Create expander for each conversation
        with st.expander(
            f"{icon} **Step {i}: {agent.title()}** | {model} | {duration:.1f}s | {tokens.get('total', 0)} tokens",
            expanded=False
        ):
            # Metadata
            meta_col1, meta_col2 = st.columns(2)
            
            with meta_col1:
                st.markdown("**Metadata**")
                st.json({
                    "timestamp": timestamp,
                    "agent": agent,
                    "model": model,
                    "provider": entry.get("provider", "Unknown"),
                    "temperature": entry.get("temperature", 0),
                    "max_tokens": entry.get("max_tokens", 0),
                })
            
            with meta_col2:
                st.markdown("**Token Usage**")
                if tokens:
                    st.json(tokens)
                else:
                    st.info("No token usage data")
            
            # System Prompt
            system_prompt = entry.get("system_prompt")
            if system_prompt:
                st.markdown("**🎯 System Prompt**")
                with st.container(border=True):
                    st.text_area(
                        "System",
                        value=system_prompt,
                        height=100,
                        disabled=True,
                        label_visibility="collapsed",
                        key=f"system_{section_title}_{i}"
                    )
            
            # User Prompt
            prompt = entry.get("prompt", "")
            if prompt:
                st.markdown("**💬 User Prompt**")
                with st.container(border=True):
                    st.text_area(
                        "Prompt",
                        value=prompt,
                        height=150,
                        disabled=True,
                        label_visibility="collapsed",
                        key=f"prompt_{section_title}_{i}"
                    )
            
            # AI Response
            response = entry.get("response", "")
            if response:
                st.markdown("**🎨 AI Response**")
                with st.container(border=True):
                    st.markdown(response)


def render_quality_scores(quality_scores: Dict[str, Any]):
    """
    Render quality scores for a section.
    
    Args:
        quality_scores: Dictionary of quality metrics
    """
    if not quality_scores:
        return
    
    st.markdown("### ⭐ Quality Assessment")
    
    # Overall score
    overall = quality_scores.get("overall", 0)
    
    # Color based on score
    if overall >= 0.8:
        score_color = "🟢"
    elif overall >= 0.6:
        score_color = "🟡"
    else:
        score_color = "🔴"
    
    st.markdown(f"## {score_color} Overall Score: {overall:.1%}")
    
    # Detailed scores
    col1, col2, col3, col4 = st.columns(4)
    
    metrics = [
        ("Accuracy", "accuracy", col1),
        ("Completeness", "completeness", col2),
        ("Clarity", "clarity", col3),
        ("Relevance", "relevance", col4),
    ]
    
    for label, key, col in metrics:
        value = quality_scores.get(key, 0)
        col.metric(label, f"{value:.1%}")
    
    # Reviewer notes
    notes = quality_scores.get("reviewer_notes")
    if notes:
        st.markdown("**📝 Reviewer Notes**")
        st.info(notes)


def render_section_details_dialog(section_data: Dict[str, Any]):
    """
    Render complete section details in a dialog/modal style.
    
    Args:
        section_data: Complete section data including conversation history
    """
    section_title = section_data.get("title", "Unknown Section")
    
    st.markdown(f"# 📄 Section Details: {section_title}")
    st.markdown("---")
    
    # Tabs for different views
    tabs = st.tabs(["🤖 AI Conversation", "📊 Final Content", "⭐ Quality Scores", "📈 Data Sources"])
    
    # AI Conversation Tab
    with tabs[0]:
        conversation_history = section_data.get("ai_conversation", [])
        render_ai_conversation(conversation_history, section_title)
    
    # Final Content Tab
    with tabs[1]:
        content = section_data.get("content", "No content available")
        st.markdown("### Generated Content")
        st.markdown(content)
        
        # Metadata
        st.markdown("---")
        st.markdown("### 📋 Metadata")
        col1, col2 = st.columns(2)
        
        with col1:
            st.metric("Generation Time", f"{section_data.get('generation_time', 0):.1f}s")
            st.metric("Token Count", section_data.get("token_count", 0))
        
        with col2:
            st.metric("AI Model", section_data.get("ai_model", "Unknown"))
            st.metric("Section Order", section_data.get("section_order", "N/A"))
    
    # Quality Scores Tab
    with tabs[2]:
        quality_scores = section_data.get("quality_scores", {})
        render_quality_scores(quality_scores)
    
    # Data Sources Tab
    with tabs[3]:
        data_sources = section_data.get("data_sources", [])
        if data_sources:
            st.markdown("### 📚 Data Sources")
            for i, source in enumerate(data_sources, 1):
                st.json(source)
        else:
            st.info("No data sources recorded")
