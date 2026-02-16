"""
GlobalID V2 Writer Agent

Writer Agent: Responsible for writing report content
"""
from typing import Any, Dict, List, Optional

from src.core import get_logger
from .base import BaseAgent

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
        
        # Use v1.0-style section generation
        # Pass through any revision instructions via kwargs so retries can modify content
        if section_type == "introduction":
            content = await self._write_introduction(disease_name, language, **kwargs)
        elif section_type == "highlights":
            content = await self._write_highlights(analysis_data, disease_name, report_date, table_data_str, language, **kwargs)
        elif section_type == "cases_analysis":
            content = await self._write_cases_analysis(analysis_data, disease_name, table_data_str, language, **kwargs)
        elif section_type == "deaths_analysis":
            content = await self._write_deaths_analysis(analysis_data, disease_name, table_data_str, language, **kwargs)
        else:
            # Fallback to existing methods
            if section_type == "summary":
                content = await self._write_summary(analysis_data, style, language)
            elif section_type == "trend_analysis":
                content = await self._write_trend_analysis(analysis_data, style, language)
            elif section_type == "geographic_distribution":
                content = await self._write_geographic_distribution(analysis_data, style, language)
            elif section_type == "key_findings":
                content = await self._write_key_findings(analysis_data, style, language)
            elif section_type == "recommendations":
                content = await self._write_recommendations(analysis_data, style, language)
            else:
                content = await self._write_generic(section_type, analysis_data, style, language)
        
        result = {
            "section_type": section_type,
            "content": content,
            "style": style,
            "language": language,
            "word_count": len(content),
        }
        
        logger.info(f"Section '{section_type}' completed ({len(content)} chars)")
        return result
    
    async def _write_summary(
        self,
        analysis_data: Dict[str, Any],
        style: str,
        language: str,
        **kwargs
    ) -> str:
        """Write summary"""
        disease_name = analysis_data.get("disease_name", "Unknown Disease")
        stats = analysis_data.get("statistics", {})
        trends = analysis_data.get("trends", {})
        period = analysis_data.get("period", {})
        
        prompt = f"""Write a concise summary section for a disease surveillance report.

    Disease: {disease_name}
    Period: {period.get('start', '')} to {period.get('end', '')}

    Key metrics:
    - Total cases: {stats.get('total_cases', 'N/A')}
    - Average cases: {stats.get('avg_cases', 'N/A')}
    - Total deaths: {stats.get('total_deaths', 'N/A')}
    - Case fatality rate: {stats.get('fatality_rate', 'N/A')}%

    Trends:
    - Cases change rate: {trends.get('cases_change_rate', 'N/A')}%
    - Trend direction: {trends.get('cases_trend', 'N/A')}

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Length: 200-300 words
    - Structure: brief opening + key data highlights + trend summary
    - Language: {"Chinese" if language == "zh" else "English"}
    - Tone: objective, highlight main points"""
        
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
        **kwargs
    ) -> str:
        """Write trend analysis"""
        disease_name = analysis_data.get("disease_name", "Unknown Disease")
        trends = analysis_data.get("trends", {})
        anomalies = analysis_data.get("anomalies", [])
        insights = analysis_data.get("insights", "")
        
        prompt = f"""Write a trend analysis section for the disease.

    Disease: {disease_name}

    Trend data:
    {self._format_dict(trends)}

    Anomalies:
    {len(anomalies)} anomalies detected

    AI insights:
    {insights}

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Length: 300-500 words
    - Structure: overall trend + detailed analysis + anomaly discussion
    - Language: {"Chinese" if language == "zh" else "English"}
    - Use professional terminology while remaining readable"""
        
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
        **kwargs
    ) -> str:
        """Write key findings"""
        prompt = f"""List and explain the key findings from this disease surveillance.

    Analysis data:
    {self._format_analysis_data(analysis_data)}

    Requirements:
    - Writing style: {self._get_style_description(style)}
    - Format: numbered list (3-5 items)
    - Each item: title + short explanation (1-2 sentences)
    - Language: {"Chinese" if language == "zh" else "English"}
    - Emphasize importance and practical implications"""
        
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

        # System prompts are primarily loaded from external files; this method returns a concise English guideline.
        return f"{base} {extra}"
    
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
        return json.dumps(data, ensure_ascii=False, indent=2)

    # V1.0-style section writing methods
    async def _write_introduction(self, disease_name: str, language: str, **kwargs) -> str:
        """Write introduction section (90-100 words)"""
        prompt = f"Give a brief introduction to {disease_name or 'the disease'}, not including any analysis or commentary. Word limit: 90-100 words."

        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        response = await self.complete(
            prompt=prompt,
            system=self.system_prompt
        )

        return response.strip()
    
    async def _write_highlights(self, analysis_data: Dict, disease_name: str, 
                               report_date: str, table_data_str: str, language: str, **kwargs) -> str:
        """Write highlights section (100-110 words, 3-4 bullet points)"""
        data_context = table_data_str or str(analysis_data.get('insights', ''))

        prompt = f"""Analyze the provided data for {disease_name or 'the disease'} and provide a brief summary of key epidemiological trends and current disease situation as of {report_date or 'the current period'}.
    Format as 3-4 bullet points, each followed by <br/>.
    Word count: 100-110 words.
    Data: {data_context}"""

        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        response = await self.complete(
            prompt=prompt,
            system=self.system_prompt
        )

        return response.strip()
    
    async def _write_cases_analysis(self, analysis_data: Dict, disease_name: str, 
                                   table_data_str: str, language: str, **kwargs) -> str:
        """Write cases analysis section (2-3 paragraphs)"""
        data_context = table_data_str or str(analysis_data.get('insights', ''))

        prompt = f"""Provide deep cases analysis of reported data for {disease_name or 'the disease'}. 
    Write 2-3 flowing paragraphs without bullet points.
    Focus on case trends, patterns, and epidemiological insights.
    Data: {data_context}"""

        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        response = await self.complete(
            prompt=prompt,
            system=self.system_prompt
        )

        return response.strip()
    
    async def _write_deaths_analysis(self, analysis_data: Dict, disease_name: str, 
                                    table_data_str: str, language: str, **kwargs) -> str:
        """Write deaths analysis section (2-3 paragraphs)"""
        data_context = table_data_str or str(analysis_data.get('insights', ''))

        prompt = f"""Provide deep deaths analysis of reported data for {disease_name or 'the disease'}.
    Write 2-3 flowing paragraphs without bullet points.
    Focus on mortality patterns, case-fatality ratios, and death trends.
    Data: {data_context}"""

        rev = kwargs.get('revision_instructions')
        if rev:
            prompt += f"\n\nRevision instructions:\n{rev}"

        response = await self.complete(
            prompt=prompt,
            system=self.system_prompt
        )

        return response.strip()
