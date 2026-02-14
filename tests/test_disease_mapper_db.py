"""
测试数据库版疾病映射器
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from src.data.normalizers.disease_mapper_db import DiseaseMapperDB
from src.core.logging import get_logger

logger = get_logger(__name__)


async def test_basic_mapping():
    """测试基本映射功能"""
    print("\n" + "="*60)
    print("🧪 测试1: 基本映射功能")
    print("="*60)
    
    mapper = DiseaseMapperDB('cn')
    
    test_cases = [
        ('新冠肺炎', 'COVID-19'),
        ('肺结核', 'Tuberculosis'),
        ('艾滋病', 'HIV/AIDS'),
        ('手足口病', 'Hand-Foot-Mouth Disease'),
        ('流行性感冒', 'Influenza'),
        ('麻疹', 'Measles'),
        ('鼠疫', 'Plague'),
    ]
    
    print("\n本地名称 → disease_id → 标准英文名:")
    all_pass = True
    
    for local_name, expected_name in test_cases:
        disease_id = await mapper.map_local_to_id(local_name)
        
        if disease_id:
            info = await mapper.get_standard_info(disease_id)
            if info:
                status = '✅' if info.standard_name_en == expected_name else '⚠️'
                if info.standard_name_en != expected_name:
                    all_pass = False
                print(f"  {status} {local_name:<15} → {disease_id} → {info.standard_name_en}")
            else:
                print(f"  ❌ {local_name:<15} → {disease_id} → 未找到标准信息")
                all_pass = False
        else:
            print(f"  ❌ {local_name:<15} → 未找到映射")
            all_pass = False
    
    print(f"\n{'✅ 测试通过' if all_pass else '❌ 测试失败'}\n")
    return all_pass


async def test_alias_mapping():
    """测试别名映射"""
    print("\n" + "="*60)
    print("🧪 测试2: 别名映射")
    print("="*60)
    
    mapper = DiseaseMapperDB('cn')
    
    alias_cases = [
        ('新冠', 'D004', 'COVID-19'),
        ('非典', 'D003', 'SARS'),
        ('HIV', 'D005', 'HIV/AIDS'),
    ]
    
    print("\n别名 → disease_id → 标准名:")
    all_pass = True
    
    for alias, expected_id, expected_name in alias_cases:
        disease_id = await mapper.map_local_to_id(alias)
        
        if disease_id:
            standard_name = await mapper.get_standard_name(disease_id, lang='en')
            status = '✅' if disease_id == expected_id else '⚠️'
            if disease_id != expected_id:
                all_pass = False
            print(f"  {status} {alias:<15} → {disease_id} → {standard_name}")
        else:
            print(f"  ❌ {alias:<15} → 未找到映射")
            all_pass = False
    
    print(f"\n{'✅ 测试通过' if all_pass else '❌ 测试失败'}\n")
    return all_pass


async def test_dataframe_mapping():
    """测试DataFrame批量映射"""
    print("\n" + "="*60)
    print("🧪 测试3: DataFrame批量映射")
    print("="*60)
    
    import pandas as pd
    
    mapper = DiseaseMapperDB('cn')
    
    # 创建测试数据
    df = pd.DataFrame({
        'disease_name': ['新冠肺炎', '肺结核', '艾滋病', '未知疾病X'],
        'cases': [100, 200, 50, 10]
    })
    
    print("\n原始数据:")
    print(df.to_string(index=False))
    
    # 映射
    result_df = await mapper.map_dataframe(
        df,
        disease_col='disease_name',
        add_id_col=True,
        add_standard_name=True
    )
    
    print("\n映射后数据:")
    print(result_df[['disease_name', 'disease_id', 'standard_name_en', 'cases']].to_string(index=False))
    
    # 验证
    mapped_count = result_df['disease_id'].notna().sum()
    unmapped_count = result_df['disease_id'].isna().sum()
    
    print(f"\n统计:")
    print(f"  已映射: {mapped_count}")
    print(f"  未映射: {unmapped_count}")
    
    all_pass = mapped_count == 3 and unmapped_count == 1
    print(f"\n{'✅ 测试通过' if all_pass else '❌ 测试失败'}\n")
    
    return all_pass


async def test_unknown_diseases():
    """测试未知疾病记录"""
    print("\n" + "="*60)
    print("🧪 测试4: 未知疾病学习")
    print("="*60)
    
    mapper = DiseaseMapperDB('cn')
    
    # 查询未知疾病建议
    suggestions = await mapper.get_unknown_diseases(limit=10)
    
    if suggestions:
        print(f"\n发现 {len(suggestions)} 个未知疾病建议:\n")
        for i, sug in enumerate(suggestions[:5], 1):
            print(f"  {i}. {sug['local_name']}")
            print(f"     出现次数: {sug['occurrence_count']}")
            if sug['suggested_standard_name']:
                print(f"     AI建议: {sug['suggested_standard_name']} (置信度: {sug['ai_confidence']:.2f})")
            print()
    else:
        print("\n✅ 暂无未知疾病建议\n")
    
    return True


async def test_statistics():
    """测试统计功能"""
    print("\n" + "="*60)
    print("🧪 测试5: 统计信息")
    print("="*60)
    
    mapper = DiseaseMapperDB('cn')
    
    stats = await mapper.get_statistics()
    
    print(f"\n疾病数据统计:")
    print(f"  国家: {stats['country_code'].upper()}")
    print(f"  标准疾病库: {stats['standard_diseases']} 种")
    print(f"  总映射数: {stats['total_mappings']} 条")
    print(f"    - 主名称: {stats['primary_mappings']} 条")
    print(f"    - 别名: {stats['alias_mappings']} 条")
    print(f"  待审核建议: {stats['pending_suggestions']} 条")
    
    all_pass = stats['standard_diseases'] > 0 and stats['total_mappings'] > 0
    print(f"\n{'✅ 测试通过' if all_pass else '❌ 测试失败'}\n")
    
    return all_pass


async def test_add_disease():
    """测试添加新疾病"""
    print("\n" + "="*60)
    print("🧪 测试6: 动态添加疾病")
    print("="*60)
    
    mapper = DiseaseMapperDB('cn')
    
    # 检查测试疾病是否已存在
    test_id = 'D999'
    existing = await mapper.get_standard_info(test_id)
    
    if existing:
        print(f"\n⏭️  测试疾病 {test_id} 已存在，跳过添加测试\n")
        return True
    
    try:
        # 添加测试疾病
        print(f"\n添加测试疾病: {test_id}")
        await mapper.add_disease(
            disease_id=test_id,
            standard_name_en='Test Disease',
            standard_name_zh='测试疾病',
            category='Viral',
            description='This is a test disease',
            created_by='test_script'
        )
        
        # 验证
        info = await mapper.get_standard_info(test_id)
        if info:
            print(f"  ✅ 疾病添加成功: {info.standard_name_en}")
            
            # 添加映射
            print(f"\n添加测试映射")
            await mapper.add_mapping(
                disease_id=test_id,
                local_name='测试疾病名',
                created_by='test_script'
            )
            
            # 验证映射
            mapped_id = await mapper.map_local_to_id('测试疾病名')
            if mapped_id == test_id:
                print(f"  ✅ 映射添加成功: 测试疾病名 → {mapped_id}")
            else:
                print(f"  ❌ 映射验证失败")
                return False
        else:
            print(f"  ❌ 疾病添加失败")
            return False
        
        print(f"\n✅ 测试通过\n")
        return True
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}\n")
        return False


async def main():
    """运行所有测试"""
    print("\n" + "="*70)
    print("🚀 GlobalID V2 - 数据库版疾病映射器测试")
    print("="*70)
    
    tests = [
        ("基本映射", test_basic_mapping),
        ("别名映射", test_alias_mapping),
        ("DataFrame批量映射", test_dataframe_mapping),
        ("统计信息", test_statistics),
        ("未知疾病学习", test_unknown_diseases),
        ("动态添加疾病", test_add_disease),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result))
        except Exception as e:
            logger.error(f"测试失败 {name}: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # 总结
    print("\n" + "="*70)
    print("📊 测试总结")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print()
    for name, result in results:
        status = '✅' if result else '❌'
        print(f"  {status} {name}")
    
    print(f"\n通过率: {passed}/{total} ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
    else:
        print(f"\n⚠️  {total - passed} 个测试失败")
    
    print("="*70 + "\n")
    
    return 0 if passed == total else 1


if __name__ == '__main__':
    exit_code = asyncio.run(main())
    exit(exit_code)
