"""
GlobalID V2 Analyst Agent

Analyst Agent: Responsible for analyzing disease data and extracting key information and trends
"""
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from src.core import get_logger
from .base import BaseAgent

logger = get_logger(__name__)


class AnalystAgent(BaseAgent):
    """
    Analyst Agent
    
    Responsibilities:
    1. Analyze disease time series data
    2. Identify trends and anomalies
    3. Calculate statistical indicators
    4. Generate data insights
    """
    
    def __init__(self):
        super().__init__(
            name="Analyst",
            temperature=0.3,  # Analysis tasks need lower temperature (more deterministic)
            max_tokens=2000,
        )
        
        # Load system prompt
        self.system_prompt = self._load_system_prompt()
    
    def _load_system_prompt(self) -> str:
        """Load system prompt"""
        from pathlib import Path
        
        prompt_file = Path(__file__).parent.parent.parent.parent / "configs" / "prompts" / "analyst_system_prompt.txt"
        
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except FileNotFoundError:
            logger.warning(f"System prompt file not found: {prompt_file}")
            return "You are a professional disease surveillance analyst. Analyze the provided data and identify patterns, trends, and insights."
    
    async def process(
        self,
        data: pd.DataFrame,
        disease_name: str,
        period_start: datetime,
        period_end: datetime,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Analyze disease data
        
        Args:
            data: Disease data DataFrame
            disease_name: Disease name
            period_start: Analysis start time
            period_end: Analysis end time
            **kwargs: Additional parameters
            
        Returns:
            Analysis result dictionary
        """
        logger.info(f"Analyzing disease '{disease_name}' from {period_start} to {period_end}")
        
        # 1. Calculate statistical indicators
        stats = self._calculate_statistics(data)
        
        # 2. Identify trends
        trends = self._identify_trends(data)
        
        # 3. Detect anomalies
        anomalies = self._detect_anomalies(data)
        
        # 4. Use AI to generate insights
        insights = await self._generate_insights(
            data=data,
            stats=stats,
            trends=trends,
            anomalies=anomalies,
            disease_name=disease_name,
        )
        
        result = {
            "disease_name": disease_name,
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
            "statistics": stats,
            "trends": trends,
            "anomalies": anomalies,
            "insights": insights,
            "data_quality": self._assess_data_quality(data),
        }
        
        logger.info(f"Analysis completed for '{disease_name}'")
        return result
    
    def _calculate_statistics(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Calculate statistical indicators"""
        if data.empty:
            return {}
        
        stats = {}
        
        # Case statistics
        if "case_count" in data.columns:
            stats["total_cases"] = int(data["case_count"].sum())
            stats["avg_cases"] = float(data["case_count"].mean())
            stats["max_cases"] = int(data["case_count"].max())
            stats["min_cases"] = int(data["case_count"].min())
            stats["std_cases"] = float(data["case_count"].std())
        
        # Death statistics
        if "death_count" in data.columns:
            stats["total_deaths"] = int(data["death_count"].sum())
            stats["avg_deaths"] = float(data["death_count"].mean())
            
            # Fatality rate
            if "case_count" in data.columns and stats.get("total_cases", 0) > 0:
                stats["fatality_rate"] = round(
                    (stats["total_deaths"] / stats["total_cases"]) * 100, 2
                )
        
        # Incidence statistics
        if "incidence_rate" in data.columns:
            stats["avg_incidence_rate"] = float(data["incidence_rate"].mean())
        
        return stats
    
    def _identify_trends(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Identify trends"""
        if data.empty:
            return {}
        
        # Check time column name
        time_col = None
        if "date" in data.columns:
            time_col = "date"
        elif "time" in data.columns:
            time_col = "time"
        
        if time_col is None:
            return {}
        
        trends = {}
        
        # Ensure data is sorted by time
        data = data.sort_values(time_col)
        
        # Calculate case growth trends
        if "case_count" in data.columns:
            cases = data["case_count"].values
            if len(cases) >= 2:
                # Calculate change rate
                change_rate = ((cases[-1] - cases[0]) / (cases[0] + 1)) * 100
                trends["cases_change_rate"] = round(change_rate, 2)
                
                # Determine trend direction
                if change_rate > 10:
                    trends["cases_trend"] = "increasing"
                elif change_rate < -10:
                    trends["cases_trend"] = "decreasing"
                else:
                    trends["cases_trend"] = "stable"
                
                # Calculate moving average
                if len(cases) >= 4:
                    ma = pd.Series(cases).rolling(window=4).mean().values
                    trends["moving_average"] = ma[-1] if len(ma) > 0 else None
        
        return trends
    
    def _detect_anomalies(self, data: pd.DataFrame) -> List[Dict[str, Any]]:
        """Detect anomalies"""
        if data.empty:
            return []
        
        anomalies = []
        
        # Use simple statistical method to detect anomalies (3-sigma rule)
        for column in ["cases", "deaths"]:
            if column not in data.columns:
                continue
            
            mean = data[column].mean()
            std = data[column].std()
            threshold = mean + 3 * std
            
            # Find data points exceeding threshold
            anomaly_data = data[data[column] > threshold]
            
            for _, row in anomaly_data.iterrows():
                anomalies.append({
                    "time": row["time"].isoformat() if "time" in row else None,
                    "metric": column,
                    "value": int(row[column]),
                    "threshold": round(threshold, 2),
                    "severity": "high" if row[column] > mean + 4 * std else "medium",
                })
        
        return anomalies
    
    async def _generate_insights(
        self,
        data: pd.DataFrame,
        stats: Dict,
        trends: Dict,
        anomalies: List,
        disease_name: str,
    ) -> str:
        """Use AI to generate data insights"""
        # Construct English prompt
        prompt = f"""As an epidemiologist, analyze the following disease surveillance data and provide professional insights.

Disease: {disease_name}
Data records: {len(data)}
Time period: {data['date'].min().strftime('%Y-%m') if 'date' in data.columns and len(data) > 0 else 'Unknown'} to {data['date'].max().strftime('%Y-%m') if 'date' in data.columns and len(data) > 0 else 'Unknown'}

Statistical Summary:
{self._format_dict(stats)}

Trend Analysis:
{self._format_dict(trends)}

Anomaly Detection:
{f"Detected {len(anomalies)} anomalies" if anomalies else "No significant anomalies detected"}

Provide:
1. Overall trend assessment (2-3 sentences)
2. Key findings (3-5 bullet points)
3. Public health implications (if any)

Requirements:
- Use concise professional epidemiological language
- Base analysis strictly on provided data
- Highlight important patterns and trends
- Write in English only"""
        
        try:
            insights = await self.complete(
                prompt=prompt,
                system=self.system_prompt
            )
            return insights
        except Exception as e:
            logger.error(f"Failed to generate insights: {e}")
            return "Data analysis completed, but AI insight generation failed."
    
    def _assess_data_quality(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Assess data quality"""
        if data.empty:
            return {"score": 0.0, "issues": ["Data is empty"]}
        
        quality = {
            "score": 1.0,
            "issues": [],
            "completeness": 1.0,
            "consistency": 1.0,
        }
        
        # Check missing values
        missing_ratio = data.isnull().sum().sum() / (len(data) * len(data.columns))
        quality["completeness"] = 1.0 - missing_ratio
        
        if missing_ratio > 0.1:
            quality["issues"].append(f"High missing value ratio ({missing_ratio:.1%})")
            quality["score"] -= 0.2
        
        # Check data consistency
        if "cases" in data.columns and (data["cases"] < 0).any():
            quality["issues"].append("Negative case counts found")
            quality["consistency"] -= 0.3
            quality["score"] -= 0.3
        
        quality["score"] = max(0.0, quality["score"])
        
        return quality
    
    @staticmethod
    def _format_dict(d: Dict) -> str:
        """Format dictionary to readable string"""
        if not d:
            return "No data"
        return "\n".join([f"- {k}: {v}" for k, v in d.items()])
