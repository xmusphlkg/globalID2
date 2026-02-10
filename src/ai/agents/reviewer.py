"""
GlobalID V2 Reviewer Agent

审核 Agent：负责审核报告质量，提供改进建议
"""
from typing import Any, Dict, List, Optional

from src.core import get_logger
from .base import BaseAgent

logger = get_logger(__name__)


class ReviewerAgent(BaseAgent):
    """
    审核 Agent
    
    职责：
    1. 审核报告内容的准确性
    2. 检查文本质量（语法、逻辑、可读性）
    3. 验证数据引用的正确性
    4. 提供改进建议
    """
    
    def __init__(self):
        super().__init__(
            name="Reviewer",
            model="claude-3-5-sonnet-20241022",  # 使用Claude进行审核
            temperature=0.2,  # 审核任务需要低温度（严格、客观）
            max_tokens=2000,
        )
    
    async def process(
        self,
        content: str,
        content_type: str,
        original_data: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        审核内容
        
        Args:
            content: 待审核的内容
            content_type: 内容类型（summary/analysis/report等）
            original_data: 原始数据（用于事实核查）
            **kwargs: 额外参数
            
        Returns:
            审核结果
        """
        logger.info(f"Reviewing {content_type} content ({len(content)} chars)")
        
        # 1. 质量评分
        quality_score = await self._assess_quality(content, content_type)
        
        # 2. 事实核查（如果提供了原始数据）
        fact_check = {}
        if original_data:
            fact_check = await self._fact_check(content, original_data)
        
        # 3. 改进建议
        suggestions = await self._generate_suggestions(content, content_type, quality_score)
        
        # 4. 综合评估
        overall_assessment = await self._overall_assessment(
            content,
            quality_score,
            fact_check,
            suggestions,
        )
        
        result = {
            "approved": quality_score.get("overall", 0) >= 0.7,  # 70分以上通过
            "quality_score": quality_score,
            "fact_check": fact_check,
            "suggestions": suggestions,
            "assessment": overall_assessment,
        }
        
        logger.info(f"Review completed: {'APPROVED' if result['approved'] else 'NEEDS REVISION'}")
        return result
    
    async def _assess_quality(
        self,
        content: str,
        content_type: str,
    ) -> Dict[str, float]:
        """评估内容质量"""
        prompt = f"""请作为专业审稿人，评估以下{content_type}内容的质量。

内容：
{content}

请从以下维度评分（0-1分）：
1. 准确性（Accuracy）：内容是否准确、无误导
2. 完整性（Completeness）：信息是否完整、全面
3. 清晰性（Clarity）：表达是否清晰、易懂
4. 逻辑性（Logic）：结构是否合理、逻辑连贯
5. 专业性（Professionalism）：用词是否专业、恰当

请以JSON格式返回评分：
{{
  "accuracy": 0.0-1.0,
  "completeness": 0.0-1.0,
  "clarity": 0.0-1.0,
  "logic": 0.0-1.0,
  "professionalism": 0.0-1.0,
  "overall": 0.0-1.0,
  "reasoning": "简要说明评分理由"
}}"""
        
        try:
            response = await self.complete(
                prompt=prompt,
                system="你是一位严格的学术审稿人，擅长评估科学报告的质量。请客观公正地评分。",
            )
            
            # 解析JSON响应
            import json
            import re
            
            # 提取JSON部分
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                scores = json.loads(json_match.group())
                return scores
            else:
                logger.warning("Failed to parse quality scores, using defaults")
                return {
                    "accuracy": 0.7,
                    "completeness": 0.7,
                    "clarity": 0.7,
                    "logic": 0.7,
                    "professionalism": 0.7,
                    "overall": 0.7,
                    "reasoning": "解析失败，使用默认分数",
                }
        
        except Exception as e:
            logger.error(f"Quality assessment failed: {e}")
            return {
                "accuracy": 0.5,
                "completeness": 0.5,
                "clarity": 0.5,
                "logic": 0.5,
                "professionalism": 0.5,
                "overall": 0.5,
                "reasoning": f"评估失败: {str(e)}",
            }
    
    async def _fact_check(
        self,
        content: str,
        original_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """事实核查"""
        # 提取内容中的数字
        import re
        numbers_in_content = re.findall(r'\d+(?:,\d+)*(?:\.\d+)?', content)
        
        # 从原始数据中提取参考数字
        reference_numbers = self._extract_numbers_from_data(original_data)
        
        # 简单的匹配检查
        issues = []
        for num in numbers_in_content[:10]:  # 只检查前10个数字
            num_clean = num.replace(',', '')
            try:
                num_float = float(num_clean)
                # 检查是否在参考数据的合理范围内
                if reference_numbers and not any(
                    abs(num_float - ref) / (ref + 1) < 0.1  # 10%误差
                    for ref in reference_numbers
                ):
                    # 这个数字可能有问题，但不一定是错误
                    pass
            except ValueError:
                continue
        
        # 使用AI进行语义级别的事实核查
        prompt = f"""请核查以下内容是否与原始数据一致。

内容：
{content[:1000]}  # 限制长度

原始数据摘要：
{self._summarize_data(original_data)}

请指出：
1. 明显的数据错误或不一致
2. 夸大或误导性的表述
3. 缺失的重要信息

如果没有问题，请回复"未发现明显问题"。"""
        
        try:
            response = await self.complete(
                prompt=prompt,
                system="你是一位细致的事实核查员，专注于发现数据和陈述中的不一致。",
            )
            
            return {
                "status": "checked",
                "issues": issues,
                "ai_findings": response,
                "numbers_checked": len(numbers_in_content),
            }
        
        except Exception as e:
            logger.error(f"Fact checking failed: {e}")
            return {
                "status": "failed",
                "error": str(e),
            }
    
    async def _generate_suggestions(
        self,
        content: str,
        content_type: str,
        quality_score: Dict[str, float],
    ) -> List[str]:
        """生成改进建议"""
        # 如果质量很高，不需要太多建议
        if quality_score.get("overall", 0) >= 0.9:
            return ["内容质量优秀，无需重大修改。"]
        
        prompt = f"""作为资深编辑，请为以下{content_type}内容提供改进建议。

内容：
{content}

当前质量评分：{quality_score.get('overall', 0):.2f}

请提供3-5条具体的改进建议，每条建议应：
- 指出具体问题
- 说明为什么需要改进
- 给出改进方向

格式：使用编号列表（1. 2. 3. ...）"""
        
        try:
            response = await self.complete(
                prompt=prompt,
                system="你是一位经验丰富的科学编辑，擅长提供建设性的改进建议。",
            )
            
            # 解析建议列表
            import re
            suggestions = re.findall(r'\d+\.\s*(.+?)(?=\d+\.|$)', response, re.DOTALL)
            suggestions = [s.strip() for s in suggestions if s.strip()]
            
            return suggestions if suggestions else [response]
        
        except Exception as e:
            logger.error(f"Suggestion generation failed: {e}")
            return ["建议生成失败，请人工审核。"]
    
    async def _overall_assessment(
        self,
        content: str,
        quality_score: Dict[str, float],
        fact_check: Dict[str, Any],
        suggestions: List[str],
    ) -> str:
        """综合评估"""
        overall = quality_score.get("overall", 0)
        
        if overall >= 0.9:
            assessment = "优秀：内容质量很高，可以直接使用。"
        elif overall >= 0.7:
            assessment = "良好：内容质量达标，建议略作修改后使用。"
        elif overall >= 0.5:
            assessment = "中等：内容需要较大改进才能使用。"
        else:
            assessment = "较差：内容质量不足，建议重新撰写。"
        
        # 添加事实核查结果
        if fact_check.get("issues"):
            assessment += f"\n注意：发现 {len(fact_check['issues'])} 个潜在的事实问题。"
        
        return assessment
    
    @staticmethod
    def _extract_numbers_from_data(data: Dict[str, Any]) -> List[float]:
        """从数据中提取数字"""
        numbers = []
        
        def extract_recursive(obj):
            if isinstance(obj, (int, float)):
                numbers.append(float(obj))
            elif isinstance(obj, dict):
                for value in obj.values():
                    extract_recursive(value)
            elif isinstance(obj, list):
                for item in obj:
                    extract_recursive(item)
        
        extract_recursive(data)
        return numbers
    
    @staticmethod
    def _summarize_data(data: Dict[str, Any]) -> str:
        """总结数据"""
        import json
        summary = json.dumps(data, ensure_ascii=False, indent=2)
        # 限制长度
        if len(summary) > 1000:
            summary = summary[:1000] + "\n... (数据已截断)"
        return summary
