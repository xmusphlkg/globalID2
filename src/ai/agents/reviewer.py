"""
GlobalID V2 Reviewer Agent

Reviewer Agent: Responsible for reviewing report quality and providing improvement suggestions
"""
from typing import Any, Dict, List, Optional

from src.core import get_logger
from .base import BaseAgent

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
            model=config.ai.default_model,  # Use default model from config
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
        logger.info(f"Reviewing {content_type} content ({len(content)} chars)")
        
        # 1. Quality scoring
        quality_score = await self._assess_quality(content, content_type)
        
        # 2. Fact checking (if original data provided)
        fact_check = {}
        if original_data:
            fact_check = await self._fact_check(content, original_data)
        
        # 3. Improvement suggestions
        suggestions = await self._generate_suggestions(content, content_type, quality_score)
        
        # 4. Overall assessment
        overall_assessment = await self._overall_assessment(
            content,
            quality_score,
            fact_check,
            suggestions,
        )
        
        result = {
            "approved": quality_score.get("overall", 0) >= self.reviewer_threshold,
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
        """Assess content quality"""
        prompt = f"""As a professional medical reviewer, assess the quality of the following {content_type} content.

Content:
{content}

Please score the following dimensions (0.0-1.0):
1. Accuracy: Content is accurate and not misleading
2. Completeness: Information is complete and comprehensive
3. Clarity: Expression is clear and understandable
4. Logic: Structure is reasonable and logically coherent
5. Professionalism: Professional and appropriate terminology

Return scores in JSON format:
{{
  "accuracy": 0.0-1.0,
  "completeness": 0.0-1.0,
  "clarity": 0.0-1.0,
  "logic": 0.0-1.0,
  "professionalism": 0.0-1.0,
  "overall": 0.0-1.0,
  "reasoning": "Brief explanation for the scores in English"
}}"""
        
        try:
            response = await self.complete(
                prompt=prompt,
                system="You are a strict academic reviewer, skilled at evaluating the quality of scientific reports. Please provide objective and fair scoring.",
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
                    "reasoning": "Parsing failed, using default scores",
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
                "reasoning": f"Assessment failed: {str(e)}",
            }
    
    async def _fact_check(
        self,
        content: str,
        original_data: Dict[str, Any],
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
        prompt = f"""Please fact-check if the following content is consistent with the original data.

Content:
{content[:1000]}  # Limit length

Original data summary:
{self._summarize_data(original_data)}

Please identify:
1. Obvious data errors or inconsistencies
2. Exaggerated or misleading statements
3. Missing important information

If no issues found, please respond with "No significant issues found"."""
        
        try:
            response = await self.complete(
                prompt=prompt,
                system="You are a meticulous fact-checker, focused on finding inconsistencies in data and statements.",
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
        """Generate improvement suggestions"""
        # If quality is high, fewer suggestions needed
        if quality_score.get("overall", 0) >= 0.9:
            return ["Content quality is excellent, no major revisions needed."]
        
        prompt = f"""As a senior editor, please provide improvement suggestions for the following {content_type} content.

Content:
{content}

Current quality score: {quality_score.get('overall', 0):.2f}

Please provide 3-5 specific improvement suggestions. Each suggestion should:
- Point out specific issues
- Explain why improvement is needed
- Provide improvement direction

Format: Use numbered list (1. 2. 3. ...)"""
        
        try:
            response = await self.complete(
                prompt=prompt,
                system="You are an experienced scientific editor skilled at providing constructive improvement suggestions.",
            )
            
            # Parse suggestions list
            import re
            suggestions = re.findall(r'\d+\.\s*(.+?)(?=\d+\.|$)', response, re.DOTALL)
            suggestions = [s.strip() for s in suggestions if s.strip()]

            return suggestions if suggestions else [response]
        
        except Exception as e:
            logger.error(f"Suggestion generation failed: {e}")
            return ["Suggestion generation failed, please review manually."]
    
    async def _overall_assessment(
        self,
        content: str,
        quality_score: Dict[str, float],
        fact_check: Dict[str, Any],
        suggestions: List[str],
    ) -> str:
        """Overall assessment"""
        overall = quality_score.get("overall", 0)

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
            assessment += f"\nNote: {len(fact_check['issues'])} potential factual issues identified."

        return assessment
    
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
        summary = json.dumps(data, ensure_ascii=False, indent=2)
        # Truncate if summary is too long
        if len(summary) > 1000:
            summary = summary[:1000] + "\n... (data truncated)"
        return summary
