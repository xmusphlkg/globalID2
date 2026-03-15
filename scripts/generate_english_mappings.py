#!/usr/bin/env python3
"""
生成英文疾病映射CSV文件

从标准疾病数据库读取英文名称，生成英文映射CSV文件供导入使用
"""
import asyncio
import sys
from pathlib import Path
import csv

# 添加项目根目录到Python路径
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src.core.database import get_db
from src.core.logging import setup_logging, get_logger
from src.core.mapping_paths import expected_mapping_file, mapping_dir, resolve_mapping_file

logger = get_logger(__name__)


async def generate_english_mappings():
    """
    生成英文疾病映射CSV
    
    数据来源：
    1. 标准疾病库（standard_diseases表）：
       - standard_name_en → 作为主英文名称
       - standard_name_zh → 用于notes说明
       - category → 疾病分类
       - icd_10 → ICD-10编码
    
    2. 中文映射（configs/mapping/cn.csv）：
       - local_code → 如果是英文则作为别名
       - aliases → 提取其中的英文部分作为别名
    """
    
    logger.info("="*60)
    logger.info("开始生成英文疾病映射文件")
    logger.info("数据来源: 标准疾病库 + 中文映射的英文部分")
    logger.info("="*60)
    
    # 1. 从标准疾病库读取基础数据
    logger.info("步骤1: 从标准疾病库（standard_diseases）读取数据...")
    async with get_db() as db:
        result = await db.execute(text("""
            SELECT 
                disease_id,
                standard_name_en,
                standard_name_zh,
                category,
                icd_10
            FROM standard_diseases
            WHERE standard_name_en IS NOT NULL
            ORDER BY disease_id
        """))
        
        diseases = result.fetchall()
        
        if not diseases:
            logger.error("未找到标准疾病数据！")
            return False
        
        logger.info(f"  ✓ 从标准疾病库读取了 {len(diseases)} 条记录")
        logger.info(f"    - 包含字段: disease_id, standard_name_en, category, icd_10")
    
    # 2. 从中文映射读取英文别名来源
    logger.info("\n步骤2: 从中文映射（configs/mapping/cn.csv）读取英文别名...")
    cn_mapping_file = resolve_mapping_file(ROOT, "cn")
    cn_mappings = {}  # {disease_id: {'local_code': str, 'aliases': str}}
    
    if cn_mapping_file.exists():
        with open(cn_mapping_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                disease_id = row['disease_id']
                local_code = row.get('local_code', '').strip()
                aliases = row.get('aliases', '').strip()
                cn_mappings[disease_id] = {
                    'local_code': local_code,
                    'aliases': aliases
                }
        logger.info(f"  ✓ 从中文映射读取了 {len(cn_mappings)} 条记录")
        logger.info(f"    - 提取来源: local_code字段 + aliases字段中的英文部分")
    else:
        logger.warning(f"  ✗ 未找到中文映射文件: {cn_mapping_file}")
    
    # 3. 准备输出目录
    logger.info("\n步骤3: 准备输出目录...")
    output_dir = mapping_dir(ROOT)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = expected_mapping_file(ROOT, "en")
    logger.info(f"  ✓ 输出文件: {output_file}")
    
    # 4. 定义别名提取函数
    def is_english(text):
        """判断文本是否为英文（不包含中文字符）"""
        if not text:
            return False
        # 检查是否包含CJK字符（中文、日文、韩文）
        for char in text:
            code = ord(char)
            # CJK统一表意文字: 0x4E00-0x9FFF
            # CJK扩展: 0x3400-0x4DBF, 0x20000-0x2A6DF, 0x2A700-0x2B73F, etc
            if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
                return False
        return True
    
    def extract_english_aliases(disease_id, standard_name_en, cn_mapping):
        """从中文映射提取英文别名"""
        aliases_set = set()
        
        if not cn_mapping:
            return ''
        
        # 4.1 添加local_code（如果是英文且不同于标准名）
        local_code = cn_mapping.get('local_code', '').strip()
        if local_code and local_code != standard_name_en and is_english(local_code):
            aliases_set.add(local_code)
        
        # 4.2 从aliases字段提取英文部分
        aliases_str = cn_mapping.get('aliases', '').strip()
        if aliases_str:
            # 按|分割
            for alias in aliases_str.split('|'):
                alias = alias.strip()
                if alias and is_english(alias):
                    aliases_set.add(alias)
        
        # 返回使用|分隔的别名字符串
        return '|'.join(sorted(aliases_set)) if aliases_set else ''
    
    # 5. 生成并写入CSV文件
    logger.info("\n步骤4: 合并数据并生成英文映射CSV...")
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        
        # 写入表头
        writer.writerow([
            'disease_id',
            'local_name',
            'local_code',
            'category',
            'aliases',
            'notes',
            'data_source'
        ])
        
        # 写入数据
        alias_count = 0
        for disease in diseases:
            disease_id, name_en, name_zh, category, icd10_code = disease
            
            # 从中文映射提取英文别名
            cn_mapping = cn_mappings.get(disease_id)
            aliases = extract_english_aliases(disease_id, name_en, cn_mapping)
            if aliases:
                alias_count += 1
            
            # 写入记录（组合标准库数据 + 中文映射的英文部分）
            writer.writerow([
                disease_id,
                name_en,  # ← 来自 standard_diseases.standard_name_en
                icd10_code or '',  # ← 来自 standard_diseases.icd_10
                category or '',  # ← 来自 standard_diseases.category
                aliases,  # ← 来自 cn_mapping 的英文部分（local_code + aliases）
                f'From standard_diseases: {name_zh}',  # ← 来自 standard_diseases.standard_name_zh
                'Standard Database + CN Mapping'  # 数据来源说明
            ])
    
    logger.info(f"\n{'='*60}")
    logger.info("✅ 英文映射CSV生成完成！")
    logger.info(f"{'='*60}")
    logger.info(f"输出文件: {output_file}")
    logger.info(f"映射总数: {len(diseases)} 条")
    logger.info(f"有别名的疾病: {alias_count} 个")
    
    print("\n" + "="*80)
    print("📊 数据来源汇总:")
    print("="*80)
    print("【来自标准疾病库 standard_diseases】")
    print(f"  • disease_id       : 疾病统一标识")
    print(f"  • local_name       : 标准英文名称 (standard_name_en)")
    print(f"  • local_code       : ICD-10编码 (icd_10)")
    print(f"  • category         : 疾病分类")
    print(f"  • notes            : 中文名称参考 (standard_name_zh)")
    print()
    print("【来自中文映射 configs/mapping/cn.csv】")
    print(f"  • aliases          : 英文别名（从local_code和aliases字段提取）")
    print(f"    - 提取 local_code 中的英文代码（如 SARS-CoV, AIDS, TB）")
    print(f"    - 提取 aliases 中的纯英文部分（自动过滤中文）")
    print()
    print("="*80)
    print("📋 生成结果:")
    print(f"  ✓ 共生成 {len(diseases)} 条英文疾病映射")
    print(f"  ✓ 其中 {alias_count} 个疾病包含英文别名")
    print()
    print("📋 下一步操作:")
    print(f"  1. 检查生成的文件: {output_file}")
    print(f"  2. 运行以下命令导入数据库:")
    print(f"     ./venv/bin/python scripts/full_rebuild_database.py --yes")
    print()
    print("💡 维护提示:")
    print("  - 标准英文名称：维护 standard_diseases 表")
    print("  - 英文别名：维护 configs/mapping/cn.csv 的英文内容")
    print("="*80 + "\n")
    
    return True


async def main():
    """主函数"""
    setup_logging()
    
    try:
        success = await generate_english_mappings()
        return 0 if success else 1
    except Exception as e:
        logger.exception(f"生成英文映射失败: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
