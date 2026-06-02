"""
GlobalID V2 Writer Agent

Writer Agent: Responsible for writing report content
"""
from typing import Any, Dict, List, Optional

from src.core import get_logger
from .base import BaseAgent
from .prompt_loader import render_prompt_template

logger = get_logger(__name__)


class WriterAgent(BaseAgent):
    """
    Writer Agent
    
    Responsibilities:
    1. Write report sections based on analysis results
    2. Generate text in different styles (formal, popular, technical, etc.)
    3. Ensure content is well-structured and logically coherent
    """
    
    def __init__(self):
        super().__init__(
            name="Writer",
            temperature=0.7,  # Writing tasks need moderate temperature (balance creativity and accuracy)
            max_tokens=3000,
        )
        
        # Load system prompt
        self.system_prompt = self._load_system_prompt()
    
    def _load_system_prompt(self) -> str:
        """Load system prompt"""
        from pathlib import Path
        
        prompt_file = Path(__file__).parent.parent.parent.parent / "configs" / "prompts" / "writer_system_prompt.txt"
        
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except FileNotFoundError:
            logger.warning(f"System prompt file not found: {prompt_file}")
            return "You are a professional medical writer. Write clear, accurate, and informative content based on the provided analysis."
    
    async def process(
        self,
        section_type: str,
        analysis_data: Dict[str, Any],
        style: str = "formal",
        language: str = "en",
        disease_name: str = None,
        report_date: str = None,
        table_data_str: str = None,
        raw_sources: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Write report section following v1.0 structure
        
        Args:
            section_type: Section type (introduction/highlights/cases_analysis/deaths_analysis)
            analysis_data: Analysis data
            style: Writing style (formal/popular/technical)
            language: Language (zh/en)
            disease_name: Name of the disease
            report_date: Current report date
            table_data_str: Formatted data for analysis
            **kwargs: Additional parameters
            
        Returns:
            Generated section content
        """
        logger.info(f"Writing section '{section_type}' in '{language}' with '{style}' style")

        # Prefer section-scoped context when provided by orchestrator.
        section_context = kwargs.pop("section_context", None)
        if isinstance(section_context, dict) and section_context:
            analysis_data = section_context

        raw_context = self._format_raw_sources(raw_sources)
        
        # Use v1.0-style section generation
        # Pass through any revision instructions via kwargs so retries can modify content
        if section_type == "introduction":
            content = await self._write_introduction(disease_name, language, raw_context=raw_context, **kwargs)
        elif section_type == "highlights":
            content = await self._write_highlights(analysis_data, disease_name, report_date, table_data_str, language, raw_context=raw_context, **kwargs)
        elif section_type == "cases_analysis":
            content = await self._write_cases_analysis(analysis_data, disease_name, table_data_str, language, raw_context=raw_context, **kwargs)
        elif section_type == "deaths_analysis":
            content = await self._write_deaths_analysis(analysis_data, disease_name, table_data_str, language, raw_context=raw_context, **kwargs)
        else:
            # Fallback to existing methods
            if section_type == "summary":
                content = await self._write_summary(analysis_data, style, language, raw_context=raw_context, **kwargs)
            elif section_type == "trend_analysis":
                content = await self._write_trend_analysis(analysis_data, style, language, raw_context=raw_context, **kwargs)
            elif section_type == "geographic_distribution":
                content = await self._write_geographic_distribution(analysis_data, style, language, raw_context=raw_context, **kwargs)
            elif section_type == "key_findings":
                content = await self._write_key_findings(analysis_data, style, language, raw_context=raw_context, **kwargs)
            elif section_type == "recommendations":
                content = await self._write_recommendations(analysis_data, style, language, raw_context=raw_context, **kwargs)
            else:
                content = await self._write_generic(section_type, analysis_data, style, language, raw_context=raw_context, **kwargs)
        
        result = {
            "section_type": section_type,
            "content": content,
            "style": style,
            "language": language,
            "word_count": len(content.split()),
        }
        
        logger.info(f"Section '{section_type}' completed ({len(content)} chars)")
        return result
    
    async def _write_summary(
        self,
        analysis_data: Dict[str, Any],
        style: str,
        language: str,
        raw_context: str = "",
        **kwargs
    ) -> str:
        """Write summary"""
        disease_name = analysis_data.get("disease_name", "Unknown Disease")
        names_all = analysis_data.get("disease_names_all") or [disease_name]
        disease_names_str = ", ".join(names_all) if isinstance(names_all, (list, tuple)) else str(names_all)
        stats = analysis_data.get("statistics", {})
        trends = analysis_data.get("trends", {})
        period = analysis_data.get("period", {})
        table_str = kwargs.get("table_data_str") or analysis_data.get("formatted_table", "")

        prompt = f"""Write a concise summary section for a disease surveillance report.

    Disease (use this for display): {disease_name}
    All known names/aliases (for reference): {disease_names_str}
    Data period: {period.get('start', '')} to {period.get('end', '')}

    Key metrics:
    - Total cases: {stats.get('total_cases', 'N/A')}
    - Average cases: {stats.get('avg_cases', 'N/A')}
    - Total deaths: {stats.get('total_deaths', 'N/A')}
    - Case fatality rate: {stats.get('fatality_rate', 'N/A')}%

    Trends:
    - Cases change rate: {trends.get('cases_change_rate', 'N/A')}%
    - Trend direction: {trends.get('cases_trend', 'N/A')}"""

        if table_str:
            prompt += f"""

    Data table (cleaned, wide format by {analysis_data.get('data_frequency', 'monthly')} period):
    {table_str}"""
        prev_content = kwargs.get("previous_sections_content", "")
        if prev_content:
            prompt += f"""

    Previous sections (trend analysis, key findings, highlights) for context:
    {prev_content[:2000]}"""

        prompt += f"""

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Length: 200-300 words
    - Structure: brief opening + key data highlights + trend summary
    - Language: {"Chinese" if language == "zh" else "English"}
    - Tone: objective, highlight main points"""

        if raw_context:
            prompt += f"\n\nRecent web signals (latest crawler pages):\n{raw_context}"
        
        system_prompt = self._get_system_prompt(language, style)
        
        # Include any revision instructions if provided
        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        content = await self.complete(
            prompt=prompt,
            system=system_prompt,
        )
        
        return content
    
    async def _write_trend_analysis(
        self,
        analysis_data: Dict[str, Any],
        style: str,
        language: str,
        raw_context: str = "",
        **kwargs
    ) -> str:
        """Write trend analysis"""
        disease_name = analysis_data.get("disease_name", "Unknown Disease")
        names_all = analysis_data.get("disease_names_all") or [disease_name]
        disease_names_str = ", ".join(names_all) if isinstance(names_all, (list, tuple)) else str(names_all)
        trends = analysis_data.get("trends", {})
        anomalies = analysis_data.get("anomalies", [])
        insights = analysis_data.get("insights", "")
        table_str = kwargs.get("table_data_str") or analysis_data.get("formatted_table", "")

        prompt = f"""Write a trend analysis section for the disease.

    Disease (use for display): {disease_name}
    All known names: {disease_names_str}
    Data period: {analysis_data.get("period", {}).get("start", "")} to {analysis_data.get("period", {}).get("end", "")}

    Trend data:
    {self._format_dict(trends)}

    Anomalies:
    {len(anomalies)} anomalies detected

    AI insights:
    {insights}"""

        if table_str:
            prompt += f"""

    Data table (cleaned, wide format by {analysis_data.get('data_frequency', 'monthly')} period):
    {table_str}"""

        prompt += f"""

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Length: 300-500 words
    - Structure: overall trend + detailed analysis + anomaly discussion
    - Language: {"Chinese" if language == "zh" else "English"}
    - Use professional terminology while remaining readable"""

        if raw_context:
            prompt += f"\n\nRecent web signals (latest crawler pages):\n{raw_context}"
        
        system_prompt = self._get_system_prompt(language, style)
        
        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        content = await self.complete(
            prompt=prompt,
            system=system_prompt,
        )
        
        return content
    
    async def _write_geographic_distribution(
        self,
        analysis_data: Dict[str, Any],
        style: str,
        language: str,
        raw_context: str = "",
        **kwargs
    ) -> str:
        """Write geographic distribution analysis"""
        prompt = f"""Write a geographic distribution analysis section.

    Data:
    {self._format_analysis_data(analysis_data)}

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Length: 200-400 words
    - Focus: regional differences, high-incidence areas, transmission patterns
    - Language: {"Chinese" if language == "zh" else "English"}"""

        if raw_context:
            prompt += f"\n\nRecent web signals (latest crawler pages):\n{raw_context}"
        
        system_prompt = self._get_system_prompt(language, style)
        
        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        content = await self.complete(
            prompt=prompt,
            system=system_prompt,
        )
        
        return content
    
    async def _write_key_findings(
        self,
        analysis_data: Dict[str, Any],
        style: str,
        language: str,
        raw_context: str = "",
        **kwargs
    ) -> str:
        """Write key findings (based on trend analysis + data)"""
        disease_name = analysis_data.get("disease_name", "Unknown Disease")
        names_all = analysis_data.get("disease_names_all") or [disease_name]
        disease_names_str = ", ".join(names_all) if isinstance(names_all, (list, tuple)) else str(names_all)
        prev_content = kwargs.get("previous_sections_content", "")
        prompt = f"""List and explain the key findings from this disease surveillance.

    Disease: {disease_name} (all names: {disease_names_str})
    Data period: {analysis_data.get("period", {}).get("start", "")} to {analysis_data.get("period", {}).get("end", "")}"""
        if prev_content:
            prompt += f"""

    Previous sections (trend analysis) for context:
    {prev_content[:1500]}"""
        analysis_subset = {k: v for k, v in analysis_data.items() if k not in ("raw_sources",)}
        prompt += f"""

    Analysis data:
    {self._format_analysis_data(analysis_subset)}

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Format: numbered list (3-5 items)
    - Each item: title + short explanation (1-2 sentences)
    - Language: {"Chinese" if language == "zh" else "English"}
    - Emphasize importance and practical implications"""

        if raw_context:
            prompt += f"\n\nRecent web signals (latest crawler pages):\n{raw_context}"
        
        system_prompt = self._get_system_prompt(language, style)
        
        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        content = await self.complete(
            prompt=prompt,
            system=system_prompt,
        )
        
        return content
    
    async def _write_recommendations(
        self,
        analysis_data: Dict[str, Any],
        style: str,
        language: str,
        raw_context: str = "",
        **kwargs
    ) -> str:
        """Write recommendations"""
        trends = analysis_data.get("trends", {})
        anomalies = analysis_data.get("anomalies", [])
        
        prompt = f"""Provide professional recommendations based on the surveillance data.

    Current situation:
    - Trend: {trends.get('cases_trend', 'N/A')}
    - Anomalies: {"Anomalies detected" if anomalies else "No significant anomalies"}

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Format: categorized recommendations (surveillance, prevention, response)
    - Provide 2-3 actionable items per category
    - Language: {"Chinese" if language == "zh" else "English"}
    - Practical and aligned with public health practice"""

        if raw_context:
            prompt += f"\n\nRecent web signals (latest crawler pages):\n{raw_context}"
        
        system_prompt = self._get_system_prompt(language, style)
        
        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        content = await self.complete(
            prompt=prompt,
            system=system_prompt,
        )
        
        return content
    
    async def _write_generic(
        self,
        section_type: str,
        analysis_data: Dict[str, Any],
        style: str,
        language: str,
        raw_context: str = "",
        **kwargs
    ) -> str:
        """Generic write method"""
        prompt = f"""Write the report section: {section_type}

    Data:
    {self._format_analysis_data(analysis_data)}

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Language: {"Chinese" if language == "zh" else "English"}
    - Maintain professionalism and accuracy"""

        if raw_context:
            prompt += f"\n\nRecent web signals (latest crawler pages):\n{raw_context}"
        
        system_prompt = self._get_system_prompt(language, style)
        
        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        content = await self.complete(
            prompt=prompt,
            system=system_prompt,
        )
        
        return content
    
    @staticmethod
    def _get_style_description(style: str) -> str:
        """Get style description"""
        styles = {
            "formal": "Formal and academic, suitable for professional reports",
            "popular": "Accessible and easy to read for the general public",
            "technical": "Highly technical with precise terminology for experts",
        }
        return styles.get(style, "Formal and professional")
    
    @staticmethod
    def _get_system_prompt(language: str, style: str) -> str:
        """Get system prompt"""
        base = "You are an experienced public health report writer who excels at transforming complex epidemiological data into clear, accurate, and insightful text."

        if style == "popular":
            extra = "Write in an accessible style suitable for a general audience."
        elif style == "technical":
            extra = "Use technical language and precise terminology for expert readers."
        else:
            extra = "Use a formal, professional tone appropriate for official reports and academic communication."

        # Enforce target language explicitly to avoid language drift caused by mixed-language context.
        target_language = "Chinese" if language == "zh" else "English"
        return f"{base} {extra} Output must be strictly in {target_language}."
    
    @staticmethod
    def _format_dict(d: Dict) -> str:
        """Format dictionary"""
        if not d:
            return "No data"
        return "\n".join([f"- {k}: {v}" for k, v in d.items()])
    
    @staticmethod
    def _format_analysis_data(data: Dict[str, Any]) -> str:
        """Format analysis data"""
        import json
        import pandas as pd
        from datetime import datetime
        
        def convert_timestamps(obj):
            """Convert pandas Timestamp objects to strings recursively."""
            if isinstance(obj, pd.Timestamp):
                return obj.isoformat()
            elif isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, dict):
                return {key: convert_timestamps(value) for key, value in obj.items()}
            elif isinstance(obj, list):
                return [convert_timestamps(item) for item in obj]
            else:
                return obj
        
        # Convert timestamps before JSON serialization
        converted_data = convert_timestamps(data)
        return json.dumps(converted_data, ensure_ascii=False, indent=2)

    @staticmethod
    def _format_raw_sources(raw_sources: Optional[List[Dict[str, Any]]], max_entries: int = 2, max_chars: int = 280) -> str:
        """Format latest raw crawler pages into concise bullet text for prompting"""
        if not raw_sources:
            return ""

        lines = []
        for src in raw_sources[:max_entries]:
            snippet = (src.get('snippet') or src.get('text') or '')[:max_chars]
            meta = f"{src.get('title') or 'Untitled'} | {src.get('source') or 'web'} | {src.get('fetched_at', '')}"
            if src.get('url'):
                meta += f" | {src['url']}"
            lines.append(f"- {meta}\n  {snippet}")

        return "\n".join(lines)

    @staticmethod
    def _build_context_injection(raw_context: str, revision_instructions: Optional[str]) -> str:
        blocks: List[str] = []
        if raw_context:
            blocks.append(f"Recent web signals (latest crawler pages):\n{raw_context}")
        if revision_instructions:
            blocks.append(f"Revision instructions:\n{revision_instructions}")
        return "\n\n".join(blocks)

    # V1.0-style section writing methods
    async def _write_introduction(self, disease_name: str, language: str, raw_context: str = "", **kwargs) -> str:
        """Write introduction section (90-100 words)"""
        context_injection = self._build_context_injection(raw_context, kwargs.get('revision_instructions'))
        prompt = render_prompt_template(
            "writer_introduction_prompt.txt",
            {
                "disease_name": disease_name or "the disease",
                "language": "Chinese" if language == "zh" else "English",
                "context_injection": context_injection,
            },
            default_template=(
                "Give a brief introduction to {disease_name}, without analysis or commentary.\n"
                "Word limit: 90-100 words.\n"
                "Target output language: {language}\n{context_injection}"
            ),
        )

        response = await self.complete(
            prompt=prompt,
            system=self._get_system_prompt(language, "formal")
        )

        return response.strip()
    
    async def _write_highlights(self, analysis_data: Dict, disease_name: str, 
                               report_date: str, table_data_str: str, language: str, raw_context: str = "", **kwargs) -> str:
        """Write highlights section (based on trend + key_findings + data)"""
        names_all = analysis_data.get("disease_names_all") or [disease_name]
        disease_names_str = ", ".join(names_all) if isinstance(names_all, (list, tuple)) else str(names_all)
        prev_content = kwargs.get("previous_sections_content", "")
        data_context = table_data_str or str(analysis_data.get('insights', ''))

        previous_sections_injection = ""
        if prev_content:
            previous_sections_injection = (
                "Previous sections (trend analysis, key findings) for context:\n"
                f"{prev_content[:2000]}"
            )

        context_injection = self._build_context_injection(raw_context, kwargs.get('revision_instructions'))
        prompt = render_prompt_template(
            "writer_highlights_prompt.txt",
            {
                "disease_name": disease_name or "the disease",
                "disease_names_str": disease_names_str,
                "report_date": report_date or "the current period",
                "period_start": analysis_data.get("period", {}).get("start", ""),
                "period_end": analysis_data.get("period", {}).get("end", ""),
                "previous_sections_injection": previous_sections_injection,
                "data_context": data_context,
                "language": "Chinese" if language == "zh" else "English",
                "context_injection": context_injection,
            },
            default_template=(
                "Summarize key epidemiological highlights for {disease_name}.\n"
                "Data period: {period_start} to {period_end}\n"
                "{previous_sections_injection}\n"
                "Format as 3-4 bullets with <br/>, 100-110 words.\n"
                "Data: {data_context}\n"
                "Target output language: {language}\n"
                "{context_injection}"
            ),
        )

        response = await self.complete(
            prompt=prompt,
            system=self._get_system_prompt(language, "formal")
        )

        return response.strip()
    
    async def _write_cases_analysis(self, analysis_data: Dict, disease_name: str, 
                                   table_data_str: str, language: str, raw_context: str = "", **kwargs) -> str:
        """Write cases analysis section (2-3 paragraphs)"""
        data_context = table_data_str or str(analysis_data.get('insights', ''))

        context_injection = self._build_context_injection(raw_context, kwargs.get('revision_instructions'))
        prompt = render_prompt_template(
            "writer_cases_analysis_prompt.txt",
            {
                "disease_name": disease_name or "the disease",
                "data_context": data_context,
                "language": "Chinese" if language == "zh" else "English",
                "context_injection": context_injection,
            },
            default_template=(
                "Provide in-depth case analysis for {disease_name}.\n"
                "Write 2-3 flowing paragraphs, no bullets.\n"
                "Data: {data_context}\n"
                "Target output language: {language}\n"
                "{context_injection}"
            ),
        )

        response = await self.complete(
            prompt=prompt,
            system=self._get_system_prompt(language, "formal")
        )

        return response.strip()
    
    async def _write_deaths_analysis(self, analysis_data: Dict, disease_name: str, 
                                    table_data_str: str, language: str, raw_context: str = "", **kwargs) -> str:
        """Write deaths analysis section (2-3 paragraphs)"""
        data_context = table_data_str or str(analysis_data.get('insights', ''))

        context_injection = self._build_context_injection(raw_context, kwargs.get('revision_instructions'))
        prompt = render_prompt_template(
            "writer_deaths_analysis_prompt.txt",
            {
                "disease_name": disease_name or "the disease",
                "data_context": data_context,
                "language": "Chinese" if language == "zh" else "English",
                "context_injection": context_injection,
            },
            default_template=(
                "Provide in-depth deaths analysis for {disease_name}.\n"
                "Write 2-3 flowing paragraphs, no bullets.\n"
                "Data: {data_context}\n"
                "Target output language: {language}\n"
                "{context_injection}"
            ),
        )

        response = await self.complete(
            prompt=prompt,
            system=self._get_system_prompt(language, "formal")
        )

        return response.strip()
