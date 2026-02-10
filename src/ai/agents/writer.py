"""
GlobalID V2 Writer Agent

作家 Agent：负责撰写报告内容
"""
from typing import Any, Dict, List, Optional

from src.core import get_logger
from .base import BaseAgent

logger = get_logger(__name__)


class WriterAgent(BaseAgent):
    """
    作家 Agent
    
    职责：
    1. 根据分析结果撰写报告章节
    2. 生成不同风格的文本（正式、通俗、技术等）
    3. 确保内容结构清晰、逻辑连贯
    """
    
    def __init__(self):
        super().__init__(
            name="Writer",
            temperature=0.7,  # 写作任务需要适中的温度（平衡创造性和准确性）
            max_tokens=3000,
        )
    
    async def process(
        self,
        section_type: str,
        analysis_data: Dict[str, Any],
        style: str = "formal",
        language: str = "zh",
        **kwargs
    ) -> Dict[str, Any]:
        """
        撰写报告章节
        
        Args:
            section_type: 章节类型（summary/trend_analysis/geographic_distribution等）
            analysis_data: 分析数据
            style: 写作风格（formal/popular/technical）
            language: 语言（zh/en）
            **kwargs: 额外参数
            
        Returns:
            生成的章节内容
        """
        logger.info(f"Writing section '{section_type}' in '{language}' with '{style}' style")
        
        # 根据章节类型选择合适的撰写方法
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
    ) -> str:
        """撰写摘要"""
        disease_name = analysis_data.get("disease_name", "未知疾病")
        stats = analysis_data.get("statistics", {})
        trends = analysis_data.get("trends", {})
        period = analysis_data.get("period", {})
        
        prompt = f"""请撰写一份疾病监测报告的摘要章节。

疾病：{disease_name}
时间段：{period.get('start', '')} 至 {period.get('end', '')}

关键数据：
- 总病例数：{stats.get('total_cases', 'N/A')}
- 平均病例数：{stats.get('avg_cases', 'N/A')}
- 总死亡数：{stats.get('total_deaths', 'N/A')}
- 病死率：{stats.get('fatality_rate', 'N/A')}%

趋势：
- 病例变化率：{trends.get('cases_change_rate', 'N/A')}%
- 趋势方向：{trends.get('cases_trend', 'N/A')}

要求：
- 写作风格：{self._get_style_description(style)}
- 长度：200-300字
- 结构：开头概述 + 数据亮点 + 趋势总结
- 语言：{"中文" if language == "zh" else "英文"}
- 客观准确，重点突出"""
        
        system_prompt = self._get_system_prompt(language, style)
        
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
    ) -> str:
        """撰写趋势分析"""
        disease_name = analysis_data.get("disease_name", "未知疾病")
        trends = analysis_data.get("trends", {})
        anomalies = analysis_data.get("anomalies", [])
        insights = analysis_data.get("insights", "")
        
        prompt = f"""请撰写疾病趋势分析章节。

疾病：{disease_name}

趋势数据：
{self._format_dict(trends)}

异常情况：
{len(anomalies)} 个异常值被检测到

AI洞察：
{insights}

要求：
- 写作风格：{self._get_style_description(style)}
- 长度：300-500字
- 结构：整体趋势 + 具体分析 + 异常讨论
- 语言：{"中文" if language == "zh" else "英文"}
- 使用专业术语，但保持可读性"""
        
        system_prompt = self._get_system_prompt(language, style)
        
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
    ) -> str:
        """撰写地理分布分析"""
        prompt = f"""请撰写疾病地理分布分析章节。

数据：
{self._format_analysis_data(analysis_data)}

要求：
- 写作风格：{self._get_style_description(style)}
- 长度：200-400字
- 重点：区域差异、高发地区、传播模式
- 语言：{"中文" if language == "zh" else "英文"}"""
        
        system_prompt = self._get_system_prompt(language, style)
        
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
    ) -> str:
        """撰写关键发现"""
        prompt = f"""请列出并阐述本次疾病监测的关键发现。

分析数据：
{self._format_analysis_data(analysis_data)}

要求：
- 写作风格：{self._get_style_description(style)}
- 格式：编号列表（3-5条）
- 每条：标题 + 简短说明（1-2句）
- 语言：{"中文" if language == "zh" else "英文"}
- 突出重要性和实际意义"""
        
        system_prompt = self._get_system_prompt(language, style)
        
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
    ) -> str:
        """撰写建议"""
        trends = analysis_data.get("trends", {})
        anomalies = analysis_data.get("anomalies", [])
        
        prompt = f"""基于疾病监测数据，请提出专业建议。

当前态势：
- 趋势：{trends.get('cases_trend', 'N/A')}
- 异常：{"发现异常情况" if anomalies else "无明显异常"}

要求：
- 写作风格：{self._get_style_description(style)}
- 格式：分类建议（监测建议、预防建议、应对建议）
- 每类2-3条具体建议
- 语言：{"中文" if language == "zh" else "英文"}
- 实用可行，符合公共卫生实践"""
        
        system_prompt = self._get_system_prompt(language, style)
        
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
    ) -> str:
        """通用撰写方法"""
        prompt = f"""请撰写报告章节：{section_type}

数据：
{self._format_analysis_data(analysis_data)}

要求：
- 写作风格：{self._get_style_description(style)}
- 语言：{"中文" if language == "zh" else "英文"}
- 保持专业性和准确性"""
        
        system_prompt = self._get_system_prompt(language, style)
        
        content = await self.complete(
            prompt=prompt,
            system=system_prompt,
        )
        
        return content
    
    @staticmethod
    def _get_style_description(style: str) -> str:
        """获取风格描述"""
        styles = {
            "formal": "正式、学术性强，适合专业报告",
            "popular": "通俗易懂，适合公众阅读",
            "technical": "技术性强，包含专业术语，适合专家",
        }
        return styles.get(style, "正式专业")
    
    @staticmethod
    def _get_system_prompt(language: str, style: str) -> str:
        """获取系统提示词"""
        if language == "zh":
            base = "你是一位经验丰富的公共卫生报告撰写专家，擅长将复杂的流行病学数据转化为清晰、准确、有洞察力的文字。"
        else:
            base = "You are an experienced public health report writer who excels at transforming complex epidemiological data into clear, accurate, and insightful text."
        
        if style == "popular":
            extra = "你的写作风格通俗易懂，能让普通读者理解专业内容。"
        elif style == "technical":
            extra = "你的写作风格技术性强，面向专业人士，使用准确的专业术语。"
        else:
            extra = "你的写作风格正式专业，适合官方报告和学术交流。"
        
        return f"{base} {extra}"
    
    @staticmethod
    def _format_dict(d: Dict) -> str:
        """格式化字典"""
        if not d:
            return "无数据"
        return "\n".join([f"- {k}: {v}" for k, v in d.items()])
    
    @staticmethod
    def _format_analysis_data(data: Dict[str, Any]) -> str:
        """格式化分析数据"""
        import json
        return json.dumps(data, ensure_ascii=False, indent=2)
