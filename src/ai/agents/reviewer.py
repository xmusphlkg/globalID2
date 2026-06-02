"""
GlobalID V2 Reviewer Agent

Reviewer Agent: Responsible for reviewing report quality and providing improvement suggestions
"""
from typing import Any, Dict, List, Optional

from src.core import get_logger
from .base import BaseAgent
from .prompt_loader import render_prompt_template

logger = get_logger(__name__)


class ReviewerAgent(BaseAgent):
    """
    Reviewer Agent
    
    Responsibilities:
    1. Review report content for accuracy
    2. Check text quality (grammar, logic, readability)
    3. Verify correctness of data references
    4. Provide improvement suggestions
    """
    
    def __init__(self):
        from src.core.config import get_config
        config = get_config()

        super().__init__(
            name="Reviewer",
            # No explicit model - BaseAgent uses config.ai.default_model and respects model_chain
            temperature=0.2,  # Reviewing tasks need low temperature (strict, objective)
            max_tokens=2000,
        )

        # Load reviewer-specific configuration
        # reviewer_threshold can be set via .env or configuration (default 0.7)
        self.reviewer_threshold = float(getattr(config.ai, "reviewer_threshold", 0.7))
        # max_retries preference for writer-review loop, fallback to ai.max_retries
        self.max_retries = int(getattr(config.ai, "max_retries", config.ai.max_retries))

        # Load system prompt
        self.system_prompt = self._load_system_prompt()
    
    def _load_system_prompt(self) -> str:
        """Load system prompt"""
        from pathlib import Path
        
        prompt_file = Path(__file__).parent.parent.parent.parent / "configs" / "prompts" / "reviewer_system_prompt.txt"
        
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except FileNotFoundError:
            logger.warning(f"System prompt file not found: {prompt_file}")
            return "You are a professional medical reviewer. Please review the provided content for accuracy, clarity, and completeness."
    
    async def process(
        self,
        content: str,
        content_type: str,
        original_data: Optional[Dict[str, Any]] = None,
        language: str = "en",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Review content
        
        Args:
            content: Content to be reviewed
            content_type: Content type (summary/analysis/report etc)
            original_data: Original data (for fact checking)
            **kwargs: Additional parameters
            
        Returns:
            Review results
        """
        logger.info(f"Reviewing {content_type} content ({len(content)} chars, language={language})")

        # Fast path: one-pass review (single LLM call) to reduce token consumption.
        one_pass_result = await self._review_once(
            content=content,
            content_type=content_type,
            original_data=original_data,
            language=language,
        )
        if one_pass_result is not None:
            logger.info(f"Review completed (one-pass): {'APPROVED' if one_pass_result['approved'] else 'NEEDS REVISION'}")
            return one_pass_result
        
        # Fallback path: legacy multi-step review if one-pass parsing fails.
        quality_score = await self._assess_quality(content, content_type, language)
        
        # 2. Fact checking (if original data provided)
        fact_check = {}
        if original_data:
            fact_check = await self._fact_check(content, original_data, language)
        
        # 3. Improvement suggestions
        suggestions = await self._generate_suggestions(content, content_type, quality_score, language)
        
        # 4. Overall assessment
        overall_assessment = await self._overall_assessment(
            content,
            quality_score,
            fact_check,
            suggestions,
            language,
        )
        
        result = {
            "approved": quality_score.get("overall", 0) >= self.reviewer_threshold,
            "quality_score": quality_score,
            "fact_check": fact_check,
            "suggestions": suggestions,
            "assessment": overall_assessment,
            "rewrite_instruction": "\n".join(suggestions[:3]) if suggestions else "",
        }
        
        logger.info(f"Review completed: {'APPROVED' if result['approved'] else 'NEEDS REVISION'}")
        return result

    async def _review_once(
        self,
        content: str,
        content_type: str,
        original_data: Optional[Dict[str, Any]],
        language: str = "en",
    ) -> Optional[Dict[str, Any]]:
        """One-pass unified review: quality + fact-check + suggestions in a single LLM call."""
        data_summary = self._summarize_data(original_data or {})
        if len(data_summary) > 2400:
            data_summary = data_summary[:2400] + "\n... (data truncated)"

        content_for_review = content if len(content) <= 2600 else content[:2600] + "\n... (content truncated)"

        prompt = render_prompt_template(
            "reviewer_one_pass_prompt.txt",
            {
                "content_type": content_type,
                "content_for_review": content_for_review,
                "data_summary": data_summary,
                "language": language,
            },
            default_template=(
                "You are a medical reviewer and fact-checker. Return JSON only.\n"
                "Language: {language}\n"
                "Content type: {content_type}\n"
                "Content:\n{content_for_review}\n"
                "Data summary:\n{data_summary}"
            ),
        )
        system_msg = self._language_guard(
            "You are a strict and verifiable medical reviewer. Output valid JSON only. All outputs must be logically sound but formatted per requested language.",
            language,
        )

        try:
            response = await self.complete(prompt=prompt, system=system_msg)

            import json
            import re

            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                logger.warning("One-pass review returned non-JSON output")
                return None

            parsed = json.loads(json_match.group(0))

            quality_score = parsed.get("quality_score")
            if not isinstance(quality_score, dict):
                quality_score = {}

            if not isinstance(quality_score.get("overall"), (int, float)):
                dims = [
                    quality_score.get("accuracy"),
                    quality_score.get("completeness"),
                    quality_score.get("clarity"),
                    quality_score.get("logic"),
                    quality_score.get("professionalism"),
                ]
                valid_dims = [float(v) for v in dims if isinstance(v, (int, float))]
                if valid_dims:
                    quality_score["overall"] = round(sum(valid_dims) / len(valid_dims), 3)
                else:
                    quality_score["overall"] = 0.5

            fact_issues = parsed.get("fact_issues")
            if not isinstance(fact_issues, list):
                fact_issues = []
            fact_issues = [str(item).strip() for item in fact_issues if str(item).strip()][:5]

            suggestions = parsed.get("suggestions")
            if not isinstance(suggestions, list):
                suggestions = []
            suggestions = [str(item).strip() for item in suggestions if str(item).strip()][:5]

            rewrite_instruction = parsed.get("rewrite_instruction")
            if not isinstance(rewrite_instruction, str) or not rewrite_instruction.strip():
                rewrite_instruction = "\n".join(suggestions[:3]) if suggestions else ""

            overall = float(quality_score.get("overall", 0.0))
            approved_raw = parsed.get("approved")
            approved = bool(approved_raw) if isinstance(approved_raw, bool) else overall >= self.reviewer_threshold

            fact_check = {
                "status": "checked",
                "issues": fact_issues,
                "ai_findings": parsed.get("fact_check_summary") or parsed.get("assessment") or "",
                "numbers_checked": 0,
            }

            return {
                "approved": approved,
                "quality_score": quality_score,
                "fact_check": fact_check,
                "suggestions": suggestions,
                "assessment": str(parsed.get("assessment") or ""),
                "rewrite_instruction": rewrite_instruction,
            }
        except Exception as e:
            logger.warning(f"One-pass review failed, fallback to legacy path: {e}")
            return None
    
    async def _assess_quality(
        self,
        content: str,
        content_type: str,
        language: str = "en",
    ) -> Dict[str, float]:
        """Assess content quality"""
        prompt = render_prompt_template(
            "reviewer_quality_prompt.txt",
            {
                "content_type": content_type,
                "content": content,
                "language": language,
            },
            default_template=(
                "Assess quality for {content_type}.\n"
                "Language: {language}\n"
                "Content:\n{content}\n"
                "Return JSON with accuracy, completeness, clarity, logic, professionalism, overall, reasoning."
            ),
        )
        system_msg = self._language_guard(
            "You are a strict academic reviewer, skilled at evaluating the quality of scientific reports. Please provide objective and fair scoring.",
            language,
        )
        
        try:
            response = await self.complete(
                prompt=prompt,
                system=system_msg,
            )
            
            # Parse JSON response
            import json
            import re
            
            # Extract JSON part
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
                    "reasoning": "解析失败，使用默认评分" if language == "zh" else "Parsing failed, using default scores",
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
                "reasoning": f"评分失败: {str(e)}" if language == "zh" else f"Assessment failed: {str(e)}",
            }
    
    async def _fact_check(
        self,
        content: str,
        original_data: Dict[str, Any],
        language: str = "en",
    ) -> Dict[str, Any]:
        """Fact check the content against the original data."""
        # Extract numbers from content
        import re
        numbers_in_content = re.findall(r'\d+(?:,\d+)*(?:\.\d+)?', content)
        
        # Extract reference numbers from original data
        reference_numbers = self._extract_numbers_from_data(original_data)
        
        # Simple matching check
        issues = []
        for num in numbers_in_content[:10]:  # only check the first 10 numbers
            num_clean = num.replace(',', '')
            try:
                num_float = float(num_clean)
                # Check if within 10% of any reference number
                if reference_numbers and not any(
                    abs(num_float - ref) / (ref + 1) < 0.1
                    for ref in reference_numbers
                ):
                    # This number may be inconsistent with the reference data
                    issues.append(f"Potential mismatch: {num}")
            except ValueError:
                continue
        
        # Use AI for semantic-level fact checking
        prompt = render_prompt_template(
            "reviewer_fact_check_prompt.txt",
            {
                "content": content[:1000],
                "data_summary": self._summarize_data(original_data),
                "language": language,
            },
            default_template=(
                "Fact-check consistency between content and source data.\n"
                "Language: {language}\n"
                "Content:\n{content}\n"
                "Data summary:\n{data_summary}"
            ),
        )
        system_msg = self._language_guard(
            "You are a meticulous fact-checker, focused on finding inconsistencies in data and statements.",
            language,
        )
        
        try:
            response = await self.complete(
                prompt=prompt,
                system=system_msg,
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
        language: str = "en",
    ) -> List[str]:
        """Generate improvement suggestions"""
        # If quality is high, fewer suggestions needed
        if quality_score.get("overall", 0) >= 0.9:
            return ["内容质量优秀，无需重大修改。"] if language == "zh" else ["Content quality is excellent, no major revisions needed."]

        prompt = render_prompt_template(
            "reviewer_suggestion_prompt.txt",
            {
                "content_type": content_type,
                "content": content,
                "quality_score": quality_score.get('overall', 0),
                "language": language,
            },
            default_template=(
                "Provide 3-5 actionable improvement suggestions.\n"
                "Content type: {content_type}\n"
                "Language: {language}\n"
                "Current overall score: {quality_score}\n"
                "Content:\n{content}"
            ),
        )
        system_msg = self._language_guard(
            "You are an experienced scientific editor skilled at providing constructive improvement suggestions.",
            language,
        )
        
        try:
            response = await self.complete(
                prompt=prompt,
                system=system_msg,
            )
            
            # Parse suggestions list
            import re
            suggestions = re.findall(r'\d+\.\s*(.+?)(?=\d+\.|$)', response, re.DOTALL)
            suggestions = [s.strip() for s in suggestions if s.strip()]

            return suggestions if suggestions else [response]
        
        except Exception as e:
            logger.error(f"Suggestion generation failed: {e}")
            return ["建议生成失败，请人工复核。"] if language == "zh" else ["Suggestion generation failed, please review manually."]
    
    async def _overall_assessment(
        self,
        content: str,
        quality_score: Dict[str, float],
        fact_check: Dict[str, Any],
        suggestions: List[str],
        language: str = "en",
    ) -> str:
        """Overall assessment"""
        overall = quality_score.get("overall", 0)

        if language == "zh":
            if overall >= 0.9:
                assessment = "优秀：内容质量高，可直接使用。"
            elif overall >= max(self.reviewer_threshold, 0.7):
                assessment = "良好：质量可接受，建议小幅修改。"
            elif overall >= 0.5:
                assessment = "一般：使用前需进行较大幅度修改。"
            else:
                assessment = "较差：内容质量不足，建议重写。"
        else:
            if overall >= 0.9:
                assessment = "Excellent: high-quality content, ready to use."
            elif overall >= max(self.reviewer_threshold, 0.7):
                assessment = "Good: acceptable quality; minor edits recommended."
            elif overall >= 0.5:
                assessment = "Fair: substantial improvements needed before use."
            else:
                assessment = "Poor: content quality insufficient; consider rewriting."

        # Append fact-check findings
        if fact_check.get("issues"):
            if language == "zh":
                assessment += f"\n注意：识别到 {len(fact_check['issues'])} 个潜在事实问题。"
            else:
                assessment += f"\nNote: {len(fact_check['issues'])} potential factual issues identified."

        return assessment
    
    @staticmethod
    def _language_guard(system_prompt: str, language: str) -> str:
        target = "Simplified Chinese" if language == "zh" else "English"
        opposite = "English prose" if language == "zh" else "Chinese prose"
        return (
            f"{system_prompt}\n\n"
            f"Language control: all descriptive text values must be strictly in {target}. "
            f"Do not mix in {opposite} except for official disease names, acronyms, units, JSON keys, and source titles."
        )

    @staticmethod
    def _extract_numbers_from_data(data: Dict[str, Any]) -> List[float]:
        """Extract numeric values from nested data structures."""
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
        """Summarize data as JSON string (truncated if long)."""
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
        summary = json.dumps(converted_data, ensure_ascii=False, indent=2)
        # Truncate if summary is too long (保留清洗后的表格等关键信息)
        if len(summary) > 3000:
            summary = summary[:3000] + "\n... (data truncated)"
        return summary
