"""
AI API Connection Test - Consolidated

整合的AI API连接测试，包含所有提供商的测试
移动到ai目录，专注于API连接测试
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.core.config import get_config


class AIConnectionTester:
    """AI连接测试器"""
    
    def __init__(self):
        """初始化"""
        try:
            self.config = get_config()
            self.test_results = []
        except Exception as e:
            print(f"❌ 配置加载失败: {e}")
            exit(1)
    
    async def test_provider(self, provider_name: str, api_key: str, base_url: str, model: str):
        """测试单个AI提供商"""
        print(f"\n🔎 测试 {provider_name.upper()} API...")
        
        if not api_key or api_key.startswith('your_') or api_key == '':
            result = {
                'provider': provider_name,
                'status': 'skipped',
                'reason': 'API key not configured',
                'timestamp': datetime.now().isoformat()
            }
            print(f"   ⏭️  跳过: API密钥未配置")
            self.test_results.append(result)
            return False
        
        try:
            from openai import AsyncOpenAI
            
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url
            )
            
            start_time = datetime.now()
            
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": "测试成功"}
                ],
                max_tokens=50,
                temperature=0.1
            )
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            content = response.choices[0].message.content
            usage = response.usage
            
            result = {
                'provider': provider_name,
                'status': 'success',
                'model': model,
                'response': content[:100],
                'duration_seconds': duration,
                'tokens_used': usage.total_tokens if usage else 0,
                'timestamp': datetime.now().isoformat()
            }
            
            print(f"   ✅ 成功: {content[:50]}...")
            print(f"   ⏱️  {duration:.2f}秒, {usage.total_tokens if usage else 0} tokens")
            
            self.test_results.append(result)
            return True
            
        except Exception as e:
            result = {
                'provider': provider_name,
                'status': 'failed',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
            
            print(f"   ❌ 失败: {e}")
            self.test_results.append(result)
            return False
    
    async def test_all_providers(self):
        """测试所有配置的提供商"""
        print("🤖 开始测试所有AI提供商...")
        
        providers_to_test = [
            {
                'name': 'qianwen',
                'api_key': self.config.ai.qianwen_api_key,
                'base_url': self.config.ai.qianwen_base_url,
                'model': 'qwen-turbo'
            },
            {
                'name': 'glm',
                'api_key': self.config.ai.glm_api_key,
                'base_url': self.config.ai.glm_base_url,
                'model': 'glm-4'
            },
            {
                'name': 'openai',
                'api_key': self.config.ai.openai_api_key,
                'base_url': self.config.ai.openai_base_url,
                'model': 'gpt-3.5-turbo'
            }
        ]
        
        successful_tests = 0
        total_tests = len(providers_to_test)
        
        for provider in providers_to_test:
            success = await self.test_provider(
                provider['name'],
                provider['api_key'],
                provider['base_url'], 
                provider['model']
            )
            if success:
                successful_tests += 1
        
        return successful_tests, total_tests
    
    async def test_default_provider(self):
        """测试默认提供商"""
        print(f"\n🎯 测试默认提供商: {self.config.ai.default_provider}")
        
        provider = self.config.ai.default_provider
        model = self.config.ai.default_model
        
        if provider == 'qianwen':
            return await self.test_provider(
                'qianwen',
                self.config.ai.qianwen_api_key,
                self.config.ai.qianwen_base_url,
                model
            )
        elif provider == 'glm':
            return await self.test_provider(
                'glm', 
                self.config.ai.glm_api_key,
                self.config.ai.glm_base_url,
                model
            )
        elif provider == 'openai':
            return await self.test_provider(
                'openai',
                self.config.ai.openai_api_key, 
                self.config.ai.openai_base_url,
                model
            )
        else:
            print(f"❌ 不支持的默认提供商: {provider}")
            return False
    
    def generate_report(self):
        """生成测试报告"""
        print("\n" + "="*60)
        print("📊 AI API连接测试报告")
        print("="*60)
        
        success_count = len([r for r in self.test_results if r['status'] == 'success'])
        total_count = len(self.test_results)
        
        print(f"测试总数: {total_count}")
        print(f"成功数量: {success_count}")
        print(f"成功率: {success_count/total_count*100:.1f}%" if total_count > 0 else "成功率: 0%")
        
        print(f"\n详细结果:")
        for result in self.test_results:
            status_emoji = "✅" if result['status'] == 'success' else "❌" if result['status'] == 'failed' else "⏭️"
            print(f"  {status_emoji} {result['provider'].title()}: {result['status']}")
            
            if result['status'] == 'success':
                print(f"     模型: {result.get('model', 'N/A')}")
                print(f"     耗时: {result.get('duration_seconds', 0):.2f}s")
            elif result['status'] == 'failed':
                print(f"     错误: {result.get('error', 'Unknown error')[:50]}...")
        
        # 保存详细报告到文件
        report_file = Path(__file__).parent / "api_test_report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump({
                'test_timestamp': datetime.now().isoformat(),
                'summary': {
                    'total_tests': total_count,
                    'successful_tests': success_count,
                    'success_rate': success_count/total_count if total_count > 0 else 0
                },
                'test_results': self.test_results
            }, f, ensure_ascii=False, indent=2)
        
        print(f"\n💾 详细报告保存到: {report_file}")


async def main():
    """主函数"""
    print("=" * 60)
    print("🧪 AI API连接测试工具")
    print("=" * 60)
    
    tester = AIConnectionTester()
    
    # 测试默认提供商
    print("第一阶段: 测试默认提供商")
    default_success = await tester.test_default_provider()
    
    # 测试所有提供商
    print("\n第二阶段: 测试所有提供商")
    successful, total = await tester.test_all_providers()
    
    # 生成报告
    tester.generate_report()
    
    # 返回结果
    if default_success and successful > 0:
        print("\n🎉 测试完成，至少一个提供商可用!")
        exit(0)
    else:
        print(f"\n😞 测试失败，{successful}/{total} 个提供商可用")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())