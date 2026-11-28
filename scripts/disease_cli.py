"""
GlobalID V2 - 疾病管理命令行工具

快速添加、查询、管理疾病数据
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.normalizers.disease_mapper_db import DiseaseMapperDB
from src.core.database import get_db
from sqlalchemy import text


async def cmd_stats(country='cn'):
    """显示统计信息"""
    print(f"\n📊 疾病数据统计 ({country.upper()}):\n")
    
    mapper = DiseaseMapperDB(country)
    stats = await mapper.get_statistics()
    
    print(f"  标准疾病库: {stats['standard_diseases']} 种")
    print(f"  映射总数: {stats['total_mappings']} 条")
    print(f"    ├─ 主名称: {stats['primary_mappings']} 条")
    print(f"    └─ 别名: {stats['alias_mappings']} 条")
    print(f"  待审核建议: {stats['pending_suggestions']} 条\n")


async def cmd_search(query, country='cn'):
    """搜索疾病"""
    print(f"\n🔍 搜索疾病: '{query}'\n")
    
    async with get_db() as db:
        # 搜索标准疾病
        result = await db.execute(text("""
            SELECT disease_id, standard_name_en, standard_name_zh, category
            FROM standard_diseases
            WHERE is_active = true
              AND (
                  standard_name_en ILIKE :query
                  OR standard_name_zh ILIKE :query
                  OR disease_id ILIKE :query
              )
            LIMIT 10
        """), {"query": f"%{query}%"})
        
        rows = result.fetchall()
        
        if rows:
            print("标准疾病库:")
            for row in rows:
                print(f"  [{row[0]}] {row[1]} / {row[2]} ({row[3]})")
        else:
            print("  无匹配结果")
        
        # 搜索本地映射
        result = await db.execute(text("""
            SELECT dm.local_name, dm.disease_id, sd.standard_name_en
            FROM disease_mappings dm
            JOIN standard_diseases sd ON dm.disease_id = sd.disease_id
            WHERE dm.country_code = :country
              AND dm.is_active = true
              AND dm.local_name ILIKE :query
            LIMIT 10
        """), {"country": country, "query": f"%{query}%"})
        
        rows = result.fetchall()
        
        if rows:
            print(f"\n{country.upper()}本地映射:")
            for row in rows:
                print(f"  {row[0]} → [{row[1]}] {row[2]}")
        
        print()


async def cmd_suggestions(country='cn', limit=20):
    """查看待审核的疾病建议"""
    print(f"\n📋 待审核疾病建议 ({country.upper()}):\n")
    
    mapper = DiseaseMapperDB(country)
    suggestions = await mapper.get_unknown_diseases(limit=limit)
    
    if not suggestions:
        print("  ✅ 暂无待审核建议\n")
        return
    
    print(f"共 {len(suggestions)} 条建议:\n")
    
    for i, sug in enumerate(suggestions, 1):
        print(f"{i}. [{sug['id']}] {sug['local_name']}")
        print(f"   出现次数: {sug['occurrence_count']}")
        if sug['suggested_standard_name']:
            conf = sug['ai_confidence'] if sug['ai_confidence'] else 0
            print(f"   AI建议: {sug['suggested_standard_name']} (置信度: {conf:.2f})")
        print()


async def cmd_add_disease(
    disease_id,
    name_en,
    name_zh,
    category,
    icd_10=None,
    icd_11=None,
    description=None
):
    """添加新疾病"""
    print(f"\n➕ 添加新疾病: {disease_id}\n")
    
    mapper = DiseaseMapperDB('cn')
    
    try:
        await mapper.add_disease(
            disease_id=disease_id,
            standard_name_en=name_en,
            standard_name_zh=name_zh,
            category=category,
            icd_10=icd_10,
            icd_11=icd_11,
            description=description,
            created_by='cli',
            source='manual'
        )
        
        print("✅ 疾病添加成功!")
        print(f"   ID: {disease_id}")
        print(f"   英文名: {name_en}")
        print(f"   中文名: {name_zh}")
        print(f"   分类: {category}\n")
        
    except Exception as e:
        print(f"❌ 添加失败: {e}\n")


async def cmd_add_mapping(
    disease_id,
    local_name,
    country='cn',
    local_code='',
    is_alias=False
):
    """添加国家映射"""
    print(f"\n➕ 添加映射 ({country.upper()}): {local_name} → {disease_id}\n")
    
    mapper = DiseaseMapperDB(country)
    
    try:
        await mapper.add_mapping(
            disease_id=disease_id,
            local_name=local_name,
            local_code=local_code,
            is_primary=not is_alias,
            is_alias=is_alias,
            created_by='cli',
            source='manual'
        )
        
        print("✅ 映射添加成功!")
        print(f"   本地名: {local_name}")
        print(f"   疾病ID: {disease_id}")
        print(f"   类型: {'别名' if is_alias else '主名称'}\n")
        
    except Exception as e:
        print(f"❌ 添加失败: {e}\n")


async def cmd_approve_suggestion(suggestion_id, disease_id, create_mapping=True):
    """批准疾病建议"""
    print(f"\n✓ 批准建议 #{suggestion_id}\n")
    
    async with get_db() as db:
        # 获取建议详情
        result = await db.execute(text("""
            SELECT country_code, local_name
            FROM disease_learning_suggestions
            WHERE id = :id
        """), {"id": suggestion_id})
        row = result.fetchone()
        
        if not row:
            print(f"❌ 未找到建议 #{suggestion_id}\n")
            return
        
        country_code, local_name = row
        
        # 创建映射
        if create_mapping:
            mapper = DiseaseMapperDB(country_code)
            await mapper.add_mapping(
                disease_id=disease_id,
                local_name=local_name,
                source='ai_learned',
                created_by='cli'
            )
            print(f"✅ 映射已创建: {local_name} → {disease_id}")
        
        # 更新建议状态
        await db.execute(text("""
            UPDATE disease_learning_suggestions
            SET status = 'approved',
                final_disease_id = :disease_id,
                reviewed_by = 'cli',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {"id": suggestion_id, "disease_id": disease_id})
        await db.commit()
        
        print("✅ 建议已批准\n")


def print_help():
    """打印帮助信息"""
    print("""
GlobalID 疾病管理工具

使用方法:
    python scripts/disease_cli.py <command> [options]

命令:

    stats [--country CN]
        显示疾病数据统计

    search <query> [--country CN]
        搜索疾病（支持中英文）

    suggestions [--country CN] [--limit 20]
        查看待审核的疾病建议

    add-disease <disease_id> <name_en> <name_zh> <category> [options]
        添加新疾病到标准库
        Options:
            --icd-10 <code>      ICD-10编码
            --icd-11 <code>      ICD-11编码
            --description <text> 疾病描述

    add-mapping <disease_id> <local_name> [options]
        添加国家映射
        Options:
            --country CN         国家代码
            --local-code <code>  本地疾病代码
            --alias              标记为别名

    approve <suggestion_id> <disease_id> [--no-mapping]
        批准疾病建议

示例:

    # 查看统计
    python scripts/disease_cli.py stats

    # 搜索疾病
    python scripts/disease_cli.py search "新冠"

    # 添加新疾病
    python scripts/disease_cli.py add-disease D142 \\
        "Mpox Variant 2026" "猴痘2026变种" Viral \\
        --icd-11 "1E71.1" \\
        --description "2026年新发猴痘变种"

    # 添加本地映射
    python scripts/disease_cli.py add-mapping D142 "猴痘新变种"

    # 批准建议
    python scripts/disease_cli.py approve 123 D142

    """)


async def main():
    """主函数"""
    if len(sys.argv) < 2:
        print_help()
        return 0
    
    command = sys.argv[1]
    
    try:
        if command == 'stats':
            country = sys.argv[2] if len(sys.argv) > 2 else 'cn'
            await cmd_stats(country)
        
        elif command == 'search':
            if len(sys.argv) < 3:
                print("❌ 缺少搜索关键词")
                return 1
            query = sys.argv[2]
            country = sys.argv[3] if len(sys.argv) > 3 else 'cn'
            await cmd_search(query, country)
        
        elif command == 'suggestions':
            country = 'cn'
            limit = 20
            i = 2
            while i < len(sys.argv):
                if sys.argv[i] == '--country':
                    country = sys.argv[i+1]
                    i += 2
                elif sys.argv[i] == '--limit':
                    limit = int(sys.argv[i+1])
                    i += 2
                else:
                    i += 1
            await cmd_suggestions(country, limit)
        
        elif command == 'add-disease':
            if len(sys.argv) < 6:
                print("❌ 参数不足")
                print("用法: add-disease <disease_id> <name_en> <name_zh> <category>")
                return 1
            
            disease_id = sys.argv[2]
            name_en = sys.argv[3]
            name_zh = sys.argv[4]
            category = sys.argv[5]
            
            # 解析可选参数
            kwargs = {}
            i = 6
            while i < len(sys.argv):
                if sys.argv[i] == '--icd-10' and i+1 < len(sys.argv):
                    kwargs['icd_10'] = sys.argv[i+1]
                    i += 2
                elif sys.argv[i] == '--icd-11' and i+1 < len(sys.argv):
                    kwargs['icd_11'] = sys.argv[i+1]
                    i += 2
                elif sys.argv[i] == '--description' and i+1 < len(sys.argv):
                    kwargs['description'] = sys.argv[i+1]
                    i += 2
                else:
                    i += 1
            
            await cmd_add_disease(disease_id, name_en, name_zh, category, **kwargs)
        
        elif command == 'add-mapping':
            if len(sys.argv) < 4:
                print("❌ 参数不足")
                print("用法: add-mapping <disease_id> <local_name>")
                return 1
            
            disease_id = sys.argv[2]
            local_name = sys.argv[3]
            
            # 解析可选参数
            country = 'cn'
            local_code = ''
            is_alias = False
            i = 4
            while i < len(sys.argv):
                if sys.argv[i] == '--country' and i+1 < len(sys.argv):
                    country = sys.argv[i+1]
                    i += 2
                elif sys.argv[i] == '--local-code' and i+1 < len(sys.argv):
                    local_code = sys.argv[i+1]
                    i += 2
                elif sys.argv[i] == '--alias':
                    is_alias = True
                    i += 1
                else:
                    i += 1
            
            await cmd_add_mapping(disease_id, local_name, country, local_code, is_alias)
        
        elif command == 'approve':
            if len(sys.argv) < 4:
                print("❌ 参数不足")
                print("用法: approve <suggestion_id> <disease_id>")
                return 1
            
            suggestion_id = int(sys.argv[2])
            disease_id = sys.argv[3]
            create_mapping = '--no-mapping' not in sys.argv
            
            await cmd_approve_suggestion(suggestion_id, disease_id, create_mapping)
        
        elif command in ['help', '--help', '-h']:
            print_help()
        
        else:
            print(f"❌ 未知命令: {command}")
            print("使用 'help' 查看帮助")
            return 1
        
        return 0
        
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
