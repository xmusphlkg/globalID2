"""
GlobalID V2 Analyst Agent

分析师 Agent：负责分析疾病数据，提取关键信息和趋势
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
    分析师 Agent
    
    职责：
    1. 分析疾病时间序列数据
    2. 识别趋势和异常
    3. 计算统计指标
    4. 生成数据洞察
    """
    
    def __init__(self):
        super().__init__(
            name="Analyst",
            temperature=0.3,  # 分析任务需要更低的温度（更确定性）
            max_tokens=2000,
        )
    
    async def process(
        self,
        data: pd.DataFrame,
        disease_name: str,
        period_start: datetime,
        period_end: datetime,
        **kwargs
    ) -> Dict[str, Any]:
        """
        分析疾病数据
        
        Args:
            data: 疾病数据DataFrame
            disease_name: 疾病名称
            period_start: 分析起始时间
            period_end: 分析结束时间
            **kwargs: 额外参数
            
        Returns:
            分析结果字典
        """
        logger.info(f"Analyzing disease '{disease_name}' from {period_start} to {period_end}")
        
        # 1. 计算统计指标
        stats = self._calculate_statistics(data)
        
        # 2. 识别趋势
        trends = self._identify_trends(data)
        
        # 3. 检测异常
        anomalies = self._detect_anomalies(data)
        
        # 4. 使用AI生成洞察
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
        """计算统计指标"""
        if data.empty:
            return {}
        
        stats = {}
        
        # 病例数统计
        if "cases" in data.columns:
            stats["total_cases"] = int(data["cases"].sum())
            stats["avg_cases"] = float(data["cases"].mean())
            stats["max_cases"] = int(data["cases"].max())
            stats["min_cases"] = int(data["cases"].min())
            stats["std_cases"] = float(data["cases"].std())
        
        # 死亡数统计
        if "deaths" in data.columns:
            stats["total_deaths"] = int(data["deaths"].sum())
            stats["avg_deaths"] = float(data["deaths"].mean())
            
            # 病死率
            if "cases" in data.columns and stats.get("total_cases", 0) > 0:
                stats["fatality_rate"] = round(
                    (stats["total_deaths"] / stats["total_cases"]) * 100, 2
                )
        
        # 发病率统计
        if "incidence_rate" in data.columns:
            stats["avg_incidence_rate"] = float(data["incidence_rate"].mean())
        
        return stats
    
    def _identify_trends(self, data: pd.DataFrame) -> Dict[str, Any]:
        """识别趋势"""
        if data.empty or "time" not in data.columns:
            return {}
        
        trends = {}
        
        # 确保数据按时间排序
        data = data.sort_values("time")
        
        # 计算病例增长趋势
        if "cases" in data.columns:
            cases = data["cases"].values
            if len(cases) >= 2:
                # 计算变化率
                change_rate = ((cases[-1] - cases[0]) / (cases[0] + 1)) * 100
                trends["cases_change_rate"] = round(change_rate, 2)
                
                # 判断趋势方向
                if change_rate > 10:
                    trends["cases_trend"] = "increasing"
                elif change_rate < -10:
                    trends["cases_trend"] = "decreasing"
                else:
                    trends["cases_trend"] = "stable"
                
                # 计算移动平均
                if len(cases) >= 4:
                    ma = pd.Series(cases).rolling(window=4).mean().values
                    trends["moving_average"] = ma[-1] if len(ma) > 0 else None
        
        return trends
    
    def _detect_anomalies(self, data: pd.DataFrame) -> List[Dict[str, Any]]:
        """检测异常值"""
        if data.empty:
            return []
        
        anomalies = []
        
        # 使用简单的统计方法检测异常（3-sigma规则）
        for column in ["cases", "deaths"]:
            if column not in data.columns:
                continue
            
            mean = data[column].mean()
            std = data[column].std()
            threshold = mean + 3 * std
            
            # 找出超过阈值的数据点
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
        """使用AI生成数据洞察"""
        # 构造提示词
        prompt = f"""作为一名流行病学数据分析师，请分析以下疾病数据并提供专业洞察。

疾病名称: {disease_name}
数据点数量: {len(data)}

统计数据:
{self._format_dict(stats)}

趋势分析:
{self._format_dict(trends)}

异常检测:
{"发现 " + str(len(anomalies)) + " 个异常值" if anomalies else "未发现明显异常"}

请提供：
1. 整体趋势评估（2-3句话）
2. 关键发现（3-5个要点）
3. 潜在风险提示（如果有）

要求：
- 使用简洁专业的语言
- 基于数据客观分析
- 突出重要信息"""
        
        try:
            insights = await self.complete(
                prompt=prompt,
                system="你是一位经验丰富的流行病学数据分析师，擅长从疾病监测数据中提取有价值的洞察。",
            )
            return insights
        except Exception as e:
            logger.error(f"Failed to generate insights: {e}")
            return "数据分析完成，但AI洞察生成失败。"
    
    def _assess_data_quality(self, data: pd.DataFrame) -> Dict[str, Any]:
        """评估数据质量"""
        if data.empty:
            return {"score": 0.0, "issues": ["数据为空"]}
        
        quality = {
            "score": 1.0,
            "issues": [],
            "completeness": 1.0,
            "consistency": 1.0,
        }
        
        # 检查缺失值
        missing_ratio = data.isnull().sum().sum() / (len(data) * len(data.columns))
        quality["completeness"] = 1.0 - missing_ratio
        
        if missing_ratio > 0.1:
            quality["issues"].append(f"缺失值比例较高 ({missing_ratio:.1%})")
            quality["score"] -= 0.2
        
        # 检查数据一致性
        if "cases" in data.columns and (data["cases"] < 0).any():
            quality["issues"].append("存在负值病例数")
            quality["consistency"] -= 0.3
            quality["score"] -= 0.3
        
        quality["score"] = max(0.0, quality["score"])
        
        return quality
    
    @staticmethod
    def _format_dict(d: Dict) -> str:
        """格式化字典为可读字符串"""
        if not d:
            return "无数据"
        return "\n".join([f"- {k}: {v}" for k, v in d.items()])
