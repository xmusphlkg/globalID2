"""
AI Report Generation Tests - Simplified

简化的AI报告生成测试，整合了所有报告相关功能测试
使用真实数据库数据（10年百日咳数据）进行测试
"""
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from unittest.mock import Mock, patch, AsyncMock

import pandas as pd

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    from src.core import get_config, get_database, get_logger, init_app
    from src.domain import Country, Disease, DiseaseRecord, ReportType
    from src.ai.agents import AnalystAgent, WriterAgent, ReviewerAgent
    from src.generation import ReportGenerator
    from sqlalchemy import select, func
    logger = get_logger(__name__)
    HAS_DEPENDENCIES = True
except ImportError as e:
    print(f"Warning: Could not import project modules: {e}")
    print("Running in standalone mode...")
    
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    HAS_DEPENDENCIES = False


class ReportGenerationTest:
    """报告生成测试类 - 整合版本"""
    
    def __init__(self):
        """初始化测试"""
        if not HAS_DEPENDENCIES:
            raise RuntimeError("Required dependencies not available")
            
        self.config = get_config()
        # Allow test to override reviewer threshold and retries via config for reproducibility
        try:
            self.config.ai.reviewer_threshold = 0.8
            self.config.ai.max_retries = 5
        except Exception:
            pass
        self.test_data_dir = Path(__file__).parent / "fixtures" / "ai_test_data"
        self.test_data_dir.mkdir(parents=True, exist_ok=True)
        
    async def find_pertussis_disease(self) -> Optional[Disease]:
        """查找百日咳疾病"""
        async with get_database() as db:
            query = select(Disease).where(
                Disease.name.ilike('%百日咳%') |
                Disease.name.ilike('%pertussis%') |
                Disease.name_en.ilike('%pertussis%')
            )
            
            result = await db.execute(query)
            disease = result.scalars().first()
            
            if disease:
                logger.info(f"Found pertussis disease: {disease.name} (ID: {disease.id})")
            return disease
    
    async def fetch_disease_data(self, years_back: int = 10) -> pd.DataFrame:
        """获取疾病数据（指定年数）- 假设数据存在"""
        try:
            disease = await self.find_pertussis_disease()
            if not disease:
                raise RuntimeError("Pertussis disease not found in database")
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=years_back * 365)
            
            async with get_database() as db:
                # 获取指定时间范围内的所有数据（不限制国家）
                query = select(
                    DiseaseRecord.time,
                    DiseaseRecord.cases,
                    DiseaseRecord.deaths,
                    DiseaseRecord.country_id,
                    DiseaseRecord.data_source,
                    Disease.name.label('disease_name'),
                    Disease.name_en.label('disease_name_en'),
                    Country.name.label('country_name'),
                    Country.code.label('country_code'),
                    Country.name_en.label('country_name_en')
                ).join(
                    Disease
                ).join(
                    Country
                ).where(
                    DiseaseRecord.disease_id == disease.id,
                    DiseaseRecord.time >= start_date,
                    DiseaseRecord.time <= end_date
                ).order_by(DiseaseRecord.time.desc()).limit(100)  # 最多100条记录
                
                result = await db.execute(query)
                rows = result.all()
                
                if not rows:
                    # 获取所有可用数据（最新50条）
                    query = select(
                        DiseaseRecord.time,
                        DiseaseRecord.cases,
                        DiseaseRecord.deaths,
                        DiseaseRecord.country_id,
                        DiseaseRecord.data_source,
                        Disease.name.label('disease_name'),
                        Disease.name_en.label('disease_name_en'),
                        Country.name.label('country_name'),
                        Country.code.label('country_code'),
                        Country.name_en.label('country_name_en')
                    ).join(
                        Disease
                    ).join(
                        Country
                    ).where(
                        DiseaseRecord.disease_id == disease.id
                    ).order_by(DiseaseRecord.time.desc()).limit(50)
                    
                    result = await db.execute(query)
                    rows = result.all()
                
                # 转换为DataFrame with enhanced context
                data = pd.DataFrame([{
                    'date': row.time,
                    'disease_name': row.disease_name,
                    'disease_name_en': row.disease_name_en or 'Pertussis',
                    'case_count': row.cases or 0,
                    'death_count': row.deaths or 0,
                    'country_id': row.country_id,
                    'country_name': row.country_name,
                    'country_code': row.country_code,
                    'country_name_en': row.country_name_en,
                    'source': row.data_source or 'Database'
                } for row in rows])
                
                logger.info(f"Fetched {len(data)} disease records")
                return data
                
        except Exception as e:
            logger.error(f"Failed to fetch disease data: {e}")
            raise
    
    async def test_analyst_agent(self, data: pd.DataFrame) -> Dict:
        """测试分析Agent"""
        print("📊 测试 AnalystAgent...")
        
        try:
            analyst = AnalystAgent()
            
            # Enhanced analysis task with more context
            disease_name = data['disease_name'].iloc[0] if len(data) > 0 else 'Unknown'
            disease_name_en = data['disease_name_en'].iloc[0] if len(data) > 0 else 'Unknown'
            countries = data['country_name_en'].unique().tolist() if len(data) > 0 else ['Unknown']
            date_range = (data['date'].min(), data['date'].max()) if len(data) > 0 else (None, None)
            
            logger.info(f"Testing analyst with {disease_name_en} in {countries}, {len(data)} records")
            
            result = await analyst.process(
                data=data,
                disease_name=disease_name_en,
                period_start=date_range[0] or datetime.now() - timedelta(days=365),
                period_end=date_range[1] or datetime.now(),
                geographical_scope=countries,
                data_sources=data['source'].unique().tolist() if len(data) > 0 else ['Database']
            )
            
            print(f"   ✅ 分析完成 - 找到 {len(result.get('patterns', []))} 个模式")
            return result
            
        except Exception as e:
            print(f"   ❌ 分析失败: {e}")
            logger.exception("Analyst test failed")
            raise
    
    async def test_writer_agent_with_retry(self, analysis_data: Dict, disease_name: str = None, 
                                          table_data_str: str = None, max_retries: int = 2) -> Dict:
        """Test WriterAgent with reviewer feedback and retry mechanism"""
        print(f"📝 Testing WriterAgent (4-section structured report, up to {max_retries} retries)...")
        
        sections = ['introduction', 'highlights', 'cases_analysis', 'deaths_analysis']
        report_sections = {}
        report_date = datetime.now().strftime('%Y-%m-%d')
        
        writer = WriterAgent()
        reviewer = ReviewerAgent()
        # Use reviewer-configured threshold and retries when available
        configured_threshold = getattr(reviewer, 'reviewer_threshold', 0.8)
        configured_max_retries = getattr(reviewer, 'max_retries', max_retries)
        max_retries = configured_max_retries
        
        for section_type in sections:
            print(f"   🔤 生成 {section_type} 部分...")
            
            retry_count = 0
            section_approved = False
            
            while retry_count <= max_retries and not section_approved:
                try:
                    # Generate section content. If we have revision instructions from a previous review, pass them.
                    revision_instructions = None
                    if retry_count > 0 and 'suggestions' in locals():
                        revision_instructions = '; '.join(suggestions)

                    result = await writer.process(
                        section_type=section_type,
                        analysis_data=analysis_data,
                        language='en',
                        style='formal',
                        disease_name=disease_name or 'Pertussis',
                        report_date=report_date,
                        table_data_str=table_data_str,
                        revision_instructions=revision_instructions,
                    )
                    
                    content = result.get('content', '')
                    
                    # Review the content
                    review_result = await reviewer.process(
                        content=content,
                        content_type=section_type
                    )
                    
                    overall_score = review_result.get('quality_score', {}).get('overall', 0)
                    approved = review_result.get('approved', False)
                    
                    if approved or overall_score >= configured_threshold:
                        # Content approved
                        report_sections[section_type] = {
                            'content': content,
                            'word_count': result.get('word_count', 0),
                            'length': len(content),
                            'score': overall_score,
                            'attempts': retry_count + 1
                        }
                        section_approved = True
                        print(f"   ✅ {section_type}: {len(content)} chars, score {overall_score:.2f}, passed at attempt {retry_count + 1}")
                    else:
                        # Content needs revision
                        retry_count += 1
                        suggestions = review_result.get('suggestions', [])
                        print(f"   🗒️ {section_type}: score {overall_score:.2f}, retry {retry_count}...")
                        print(f"      Suggestions: {len(suggestions)} items")
                        
                        if retry_count > max_retries:
                            # Max retries reached, use content anyway
                            report_sections[section_type] = {
                                'content': content,
                                'word_count': result.get('word_count', 0),
                                'length': len(content),
                                'score': overall_score,
                                'attempts': retry_count,
                                'final_status': 'max_retries_reached'
                            }
                            print(f"   ⚠️ {section_type}: reached max retries, using current version")
                            section_approved = True
                except Exception as e:
                    print(f"   ❌ {section_type} generation failed: {e}")
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
        
        # Combine all sections
        combined_content = f"""# {disease_name or 'Pertussis'} Surveillance Report

## Introduction
{report_sections['introduction']['content']}

## Highlights
{report_sections['highlights']['content']}

## Cases Analysis
{report_sections['cases_analysis']['content']}

## Deaths Analysis
{report_sections['deaths_analysis']['content']}"""
        
        total_length = len(combined_content)
        total_attempts = sum(section.get('attempts', 1) for section in report_sections.values())
        
        print(f"   ✅ 完整报告生成 - 总计 {total_length} 字符, {total_attempts} 次生成尝试")
        
        return {
            'content': combined_content,
            'sections': report_sections,
            'total_length': total_length,
            'section_count': len(sections),
            'total_attempts': total_attempts
        }
    
    async def test_reviewer_agent(self, content: str) -> Dict:
        """测试审核Agent"""
        print("🔍 测试 ReviewerAgent...")
        
        try:
            reviewer = ReviewerAgent()
            
            result = await reviewer.process(
                content=content,
                content_type='report'
            )
            
            score = result.get('quality_score', {}).get('overall', 0)
            print(f"   ✅ 审核完成 - 评分: {score:.2f}/1.0")
            return result
            
        except Exception as e:
            print(f"   ❌ 审核失败: {e}")
            logger.exception("Reviewer test failed")
            raise
    
    async def test_complete_pipeline(self) -> Dict[str, Any]:
        """测试完整的报告生成流水线"""
        print("🔄 测试完整AI报告生成流水线...")
        
        test_results = {
            'pipeline_test': True,
            'start_time': datetime.now().isoformat(),
            'stages': {}
        }
        
        try:
            # 1. 数据获取
            print("\n第一阶段: 数据获取")
            data = await self.fetch_disease_data(years_back=10)
            
            test_results['stages']['data_fetch'] = {
                'status': 'success',
                'records_count': len(data),
                'date_range': {
                    'start': data['date'].min().isoformat() if len(data) > 0 else None,
                    'end': data['date'].max().isoformat() if len(data) > 0 else None
                },
                'diseases': data['disease_name'].unique().tolist()
            }
            
            print(f"   ✅ 获取了 {len(data)} 条记录")
            
            # 保存数据样本
            sample_file = self.test_data_dir / f"disease_data_sample_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            data.to_csv(sample_file, index=False)
            print(f"   💾 数据样本保存到: {sample_file}")
            
            # 2. AI分析
            print("\n第二阶段: AI数据分析")
            analysis_result = await self.test_analyst_agent(data)
            
            test_results['stages']['analysis'] = {
                'status': 'success',
                'patterns_found': len(analysis_result.get('patterns', [])),
                'insights_count': len(analysis_result.get('insights', []))
            }
            
            # 3. 报告写作 (四部分结构化)
            print("\n第三阶段: 结构化报告写作")
            
            # 准备数据字符串
            disease_name = data['disease_name_en'].iloc[0] if len(data) > 0 else 'Pertussis'
            table_data_str = data.to_string(index=False, max_rows=10) if len(data) > 0 else "No data available"
            
            writing_result = await self.test_writer_agent_with_retry(
                analysis_result, 
                disease_name=disease_name, 
                table_data_str=table_data_str
            )
            
            content = writing_result.get('content', '')
            test_results['stages']['writing'] = {
                'status': 'success',
                'content_length': len(content),
                'section_count': writing_result.get('section_count', 0),
                'sections': list(writing_result.get('sections', {}).keys()),
                'content_preview': content[:200] + '...' if len(content) > 200 else content
            }
            
            # 4. 内容审核
            print("\n第四阶段: 内容审核")
            review_result = await self.test_reviewer_agent(content)
            
            test_results['stages']['review'] = {
                'status': 'success',
                'score': review_result.get('quality_score', {}).get('overall', 0),
                'approved': review_result.get('approved', False),
                'suggestions_count': len(review_result.get('suggestions', []))
            }
            
            # 5. 生成最终报告
            final_report = {
                'report_id': f"ai_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                'generated_at': datetime.now().isoformat(),
                'data_summary': test_results['stages']['data_fetch'],
                'analysis_results': analysis_result,
                'report_content': content,
                'review_results': review_result
            }
            
            # 保存完整报告 (JSON)
            report_file = self.test_data_dir / f"complete_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(final_report, f, ensure_ascii=False, indent=2, default=str)
            
            # 生成 Markdown 报告
            markdown_file = self.test_data_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            self._generate_markdown_report(final_report, markdown_file)
            
            test_results['final_report_file'] = str(report_file)
            test_results['markdown_report_file'] = str(markdown_file)
            test_results['status'] = 'success'
            
            print(f"\n✅ 完整流水线测试成功!")
            print(f"📄 JSON报告保存到: {report_file}")
            print(f"📋 Markdown报告保存到: {markdown_file}")
            
            return test_results
            
        except Exception as e:
            test_results['status'] = 'failed'
            test_results['error'] = str(e)
            logger.exception("Pipeline test failed")
            return test_results
    
    def print_summary(self, results: Dict[str, Any]):
        """打印测试摘要"""
        print("\n" + "="*60)
        print("📋 AI报告生成测试摘要")
        print("="*60)
        
        if results.get('status') == 'success':
            print("🎉 整体状态: 成功")
        else:
            print("❌ 整体状态: 失败")
            if 'error' in results:
                print(f"   错误: {results['error']}")
        
        if 'stages' in results:
            print(f"\n阶段结果:")
            for stage_name, stage_result in results['stages'].items():
                status_emoji = "✅" if stage_result.get('status') == 'success' else "❌"
                print(f"  {status_emoji} {stage_name}: {stage_result.get('status')}")
                
                if stage_name == 'data_fetch' and stage_result.get('status') == 'success':
                    print(f"      记录数: {stage_result.get('records_count')}")
                    print(f"      疾病: {', '.join(stage_result.get('diseases', []))}")
                elif stage_name == 'analysis' and stage_result.get('status') == 'success':
                    print(f"      模式数: {stage_result.get('patterns_found')}")
                    print(f"      洞察数: {stage_result.get('insights_count')}")
                elif stage_name == 'writing' and stage_result.get('status') == 'success':
                    print(f"      内容长度: {stage_result.get('content_length')} 字符")
                    section_count = stage_result.get('section_count', 0)
                    sections = stage_result.get('sections', [])
                    print(f"      部分数: {section_count} (包含: {', '.join(sections)})")
                elif stage_name == 'review' and stage_result.get('status') == 'success':
                    print(f"      评分: {stage_result.get('score'):.2f}/1.0")
                    print(f"      状态: {'✅ 通过' if stage_result.get('approved', False) else '🗒️ 待修改'}")
        
        if 'final_report_file' in results:
            print(f"\n📄 JSON报告: {results['final_report_file']}")
        if 'markdown_report_file' in results:
            print(f"📋 Markdown报告: {results['markdown_report_file']}")

    def _generate_markdown_report(self, report_data: Dict[str, Any], output_file: Path):
        """生成 Markdown 格式的报告"""
        try:
            # 提取报告内容
            content = report_data.get('report_content', '')
            analysis_results = report_data.get('analysis_results', {})
            review_results = report_data.get('review_results', {})
            data_summary = report_data.get('data_summary', {})
            
            # 生成 Markdown 内容
            markdown_content = f"""# AI Disease Surveillance Report

**Report ID**: {report_data.get('report_id', 'Unknown')}
**Generated**: {report_data.get('generated_at', 'Unknown')}
**Disease**: {analysis_results.get('disease_name', 'Unknown')}
**Period**: {analysis_results.get('period', {}).get('start', 'Unknown')} - {analysis_results.get('period', {}).get('end', 'Unknown')}
**Data Records**: {data_summary.get('records_count', 0)}

---

{content}

---

## Analysis Summary

### Statistical Overview
- **Total Records**: {data_summary.get('records_count', 0)}
- **Time Range**: {data_summary.get('date_range', {}).get('start', 'N/A')} to {data_summary.get('date_range', {}).get('end', 'N/A')}
- **Diseases Covered**: {', '.join(data_summary.get('diseases', []))}

### Quality Assessment
- **Overall Score**: {review_results.get('quality_score', {}).get('overall', 'N/A'):.2f}/1.0
- **Accuracy**: {review_results.get('quality_score', {}).get('accuracy', 'N/A'):.2f}
- **Completeness**: {review_results.get('quality_score', {}).get('completeness', 'N/A'):.2f}
- **Clarity**: {review_results.get('quality_score', {}).get('clarity', 'N/A'):.2f}
- **Professional Standards**: {review_results.get('quality_score', {}).get('professionalism', 'N/A'):.2f}
- **Report Status**: {'✅ Approved' if review_results.get('approved', False) else '❌ Needs Revision'}

### Review Assessment
{review_results.get('assessment', 'No assessment available')}

### Expert Recommendations
{chr(10).join([f"- {suggestion}" for suggestion in review_results.get('suggestions', ['No specific suggestions provided'])])}

---

**Generated by GlobalID v2.0 AI Report Generation System**
"""
            
            # 写入 Markdown 文件
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
                
            print(f"   📋 Markdown报告生成成功: {len(markdown_content)} 字符")
            
        except Exception as e:
            logger.error(f"Failed to generate Markdown report: {e}")
            print(f"   ❌ Markdown报告生成失败: {e}")


async def main():
    """主函数"""
    print("=" * 60)
    print("🧪 AI报告生成测试 (10年百日咳数据)")
    print("=" * 60)
    
    try:
        # 初始化应用 - 异步调用
        await init_app()
        
        # 创建测试实例
        test = ReportGenerationTest()
        
        # 运行完整测试
        results = await test.test_complete_pipeline()
        
        # 打印摘要
        test.print_summary(results)
        
        # 返回结果
        if results.get('status') == 'success':
            print("\n🎉 所有AI报告生成测试通过!")
            exit(0)
        else:
            print(f"\n😞 AI报告生成测试失败")
            exit(1)
            
    except Exception as e:
        print(f"\n❌ 测试过程中出现错误: {e}")
        logger.exception("Main test failed")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())