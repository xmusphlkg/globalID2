#!/usr/bin/env python3
"""
重新导入历史数据到 disease_records 表

该脚本从 data/processed/history_merged.csv 读取历史数据，
使用 disease_mappings 表进行疾病名称映射，然后导入到 disease_records 表。
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
import subprocess
from src.core.database import get_db
from src.core.logging import get_logger
import json

logger = get_logger(__name__)


async def main():
    logger.info("=" * 70)
    logger.info("开始重新导入历史数据")
    logger.info("=" * 70)
    
    # 读取历史数据
    csv_file = ROOT / "data/processed/history_merged.csv"
    if not csv_file.exists():
        logger.error(f"历史数据文件不存在: {csv_file}")
        return
    
    logger.info(f"读取历史数据: {csv_file}")
    df = pd.read_csv(csv_file)
    logger.info(f"✓ 读取 {len(df)} 条原始记录")
    
    # 显示列名
    logger.info(f"CSV 列: {list(df.columns)}")
    
    async with get_db() as db:
        # 1. 获取中国的 country_id
        result = await db.execute(text("SELECT id FROM countries WHERE code = 'CN'"))
        country_row = result.fetchone()
        if not country_row:
            logger.error("未找到中国的 country_id")
            return
        country_id = country_row[0]
        logger.info(f"✓ 中国 country_id = {country_id}")
        # 提示是否需要先从 CSV 同步标准库或映射
        try:
            sync_standard = input("是否需要从 CSV 重新同步标准疾病库 `configs/standard_diseases.csv`? (yes/no): ")
        except Exception:
            sync_standard = 'no'

        try:
            sync_mappings = input("是否需要从 CSV 重新同步国家映射 `configs/cn/disease_mapping.csv`? (yes/no): ")
        except Exception:
            sync_mappings = 'no'

        if (sync_standard or sync_mappings) and (sync_standard.lower() in ('yes', 'y') or sync_mappings.lower() in ('yes', 'y')):
            logger.info("开始从 CSV 同步标准库与映射（调用 scripts/refresh_disease_mappings.py --yes）...")
            try:
                subprocess.check_call([sys.executable, str(ROOT / 'scripts' / 'refresh_disease_mappings.py'), '--yes'])
                logger.info("✓ CSV 同步完成")
            except Exception as e:
                logger.error(f"CSV 同步失败: {e}")
                return
        
        # 2. 获取所有疾病映射（别名已经作为独立的 local_name 行存在）
        result = await db.execute(text("""
            SELECT dm.local_name, dm.disease_id, sd.standard_name_en, sd.standard_name_zh
            FROM disease_mappings dm
            JOIN standard_diseases sd ON dm.disease_id = sd.disease_id
            WHERE dm.country_code = 'CN' AND dm.is_active = true
        """))
        
        # 使用归一化（去空格小写）键提高匹配容错性
        def _norm(s):
            try:
                return s.strip().lower()
            except Exception:
                return None

        mapping_dict = {}
        for row in result:
            local_name = row[0]
            disease_id = row[1]
            
            # 添加名称（包括别名，因为它们都作为 local_name存储）
            normalized = _norm(local_name)
            if normalized:
                mapping_dict[normalized] = disease_id
        
        logger.info(f"✓ 加载 {len(mapping_dict)} 个疾病映射（含别名）")
        
        # 3. 获取 diseases 表中的 ID 映射 (disease_id code -> db id)
        result = await db.execute(text("SELECT id, name FROM diseases"))
        disease_code_to_id = {row[1]: row[0] for row in result}
        logger.info(f"✓ 加载 {len(disease_code_to_id)} 个疾病 ID 映射")
        
        # 4. 清空现有数据（可选）
        user_input = input("\n是否清空现有的 disease_records 数据？(yes/no): ")
        if user_input.lower() == 'yes':
            await db.execute(text("DELETE FROM disease_records"))
            await db.commit()
            logger.info("✓ 已清空 disease_records 表")
        
        # 5. 处理和导入数据
        logger.info("\n开始导入数据...")
        
        # 确定 CSV 中的列名（可能是中英文混合）
        date_col = None
        for col in ['Date', 'date', 'time', 'Time', 'YearMonthDay']:
            if col in df.columns:
                date_col = col
                break
        
        disease_cn_col = None
        for col in ['DiseasesCN', 'disease_cn', 'disease_name_cn', '疾病名称', '病名']:
            if col in df.columns:
                disease_cn_col = col
                break
        
        disease_en_col = None
        for col in ['Diseases', 'disease_en', 'disease_name_en', 'Disease']:
            if col in df.columns:
                disease_en_col = col
                break
        
        cases_col = None
        for col in ['Cases', 'cases', 'case', '发病数']:
            if col in df.columns:
                cases_col = col
                break
        
        deaths_col = None
        for col in ['Deaths', 'deaths', 'death', '死亡数']:
            if col in df.columns:
                deaths_col = col
                break
        
        if not all([date_col, disease_cn_col, cases_col, deaths_col]):
            logger.error(f"缺少必要列: date={date_col}, disease_cn={disease_cn_col}, cases={cases_col}, deaths={deaths_col}")
            return
        
        logger.info(f"使用列映射: date={date_col}, disease_cn={disease_cn_col}, disease_en={disease_en_col}, cases={cases_col}, deaths={deaths_col}")
        
        inserted = 0
        skipped = 0
        errors = 0
        error_diseases = set()
        
        for idx, row in df.iterrows():
            try:
                # 提取基本字段
                date_str = str(row[date_col])
                disease_cn = str(row[disease_cn_col]) if pd.notna(row[disease_cn_col]) else None
                disease_en = str(row[disease_en_col]) if disease_en_col and pd.notna(row[disease_en_col]) else None
                cases = int(row[cases_col]) if pd.notna(row[cases_col]) and str(row[cases_col]) not in ['', '-10', 'nan'] else 0
                deaths = int(row[deaths_col]) if pd.notna(row[deaths_col]) and str(row[deaths_col]) not in ['', '-10', 'nan'] else 0
                
                # 提取额外字段
                # Incidence 和 Mortality (-10 表示无数据)
                incidence = None
                if 'Incidence' in df.columns and pd.notna(row['Incidence']):
                    val = float(row['Incidence'])
                    incidence = val if val >= 0 else None
                
                mortality = None
                if 'Mortality' in df.columns and pd.notna(row['Mortality']):
                    val = float(row['Mortality'])
                    mortality = val if val >= 0 else None
                
                # 省份/地区信息（优先使用中文）
                region = None
                if 'ProvinceCN' in df.columns and pd.notna(row['ProvinceCN']) and str(row['ProvinceCN']) != '全国':
                    region = str(row['ProvinceCN'])
                elif 'Province' in df.columns and pd.notna(row['Province']) and str(row['Province']) != 'China':
                    region = str(row['Province'])
                
                # 真实的数据来源
                data_source = 'Historical Data Import'  # 默认值
                if 'Source' in df.columns and pd.notna(row['Source']):
                    data_source = str(row['Source'])
                
                # 跳过无效数据
                if not disease_cn or disease_cn == 'nan':
                    skipped += 1
                    continue
                
                # 查找疾病映射：优先尝试英文名，其次中文名（都使用归一化键）
                disease_code = None
                if disease_en:
                    disease_code = mapping_dict.get(_norm(disease_en))
                if not disease_code:
                    disease_code = mapping_dict.get(_norm(disease_cn))
                
                if not disease_code:
                    if disease_cn not in error_diseases:
                        error_diseases.add(disease_cn)
                    skipped += 1
                    continue
                
                # 获取 disease 数据库 ID
                db_disease_id = disease_code_to_id.get(disease_code)
                if not db_disease_id:
                    if disease_code not in error_diseases:
                        error_diseases.add(f"{disease_cn} -> {disease_code} (未找到DB ID)")
                    skipped += 1
                    continue
                
                # 解析日期
                try:
                    if '/' in date_str:
                        date_obj = datetime.strptime(date_str, '%Y/%m/%d')
                    else:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                except:
                    skipped += 1
                    continue
                
                # 插入数据
                try:
                    # Prepare metadata 包含引用信息
                    source_file = None
                    if '__source_file' in df.columns:
                        sf = row.get('__source_file')
                        if pd.notna(sf):
                            source_file = str(sf)
                    
                    doi = None
                    if 'DOI' in df.columns and pd.notna(row['DOI']):
                        doi = str(row['DOI'])
                    
                    url = None
                    if 'URL' in df.columns and pd.notna(row['URL']):
                        url = str(row['URL'])
                    
                    adcode = None
                    if 'ADCode' in df.columns and pd.notna(row['ADCode']):
                        adcode = str(int(row['ADCode']))

                    metadata_obj = {
                        'source_csv': str(csv_file.name),
                        'source_file': source_file,
                        'row_index': int(idx),
                        'doi': doi,
                        'url': url,
                        'adcode': adcode
                    }

                    raw_obj = None
                    try:
                        raw_obj = {k: (None if pd.isna(v) else v) for k, v in row.items()}
                    except Exception:
                        raw_obj = None

                    await db.execute(text("""
                        INSERT INTO disease_records 
                        (time, disease_id, country_id, cases, deaths, 
                         incidence_rate, mortality_rate, region, data_source,
                         new_cases, new_deaths, recoveries, active_cases, new_recoveries, 
                         metadata, raw_data)
                        VALUES 
                        (:time, :disease_id, :country_id, :cases, :deaths, 
                         :incidence_rate, :mortality_rate, :region, :data_source,
                         0, 0, 0, 0, 0, 
                         :metadata_json, :raw_json)
                        ON CONFLICT (time, disease_id, country_id) DO UPDATE
                        SET cases = EXCLUDED.cases, 
                            deaths = EXCLUDED.deaths, 
                            incidence_rate = EXCLUDED.incidence_rate,
                            mortality_rate = EXCLUDED.mortality_rate,
                            region = EXCLUDED.region,
                            data_source = EXCLUDED.data_source, 
                            metadata = EXCLUDED.metadata, 
                            raw_data = EXCLUDED.raw_data
                    """), {
                        'time': date_obj,
                        'disease_id': db_disease_id,
                        'country_id': country_id,
                        'cases': max(0, cases),
                        'deaths': max(0, deaths),
                        'incidence_rate': incidence,
                        'mortality_rate': mortality,
                        'region': region,
                        'data_source': data_source,
                        'metadata_json': json.dumps(metadata_obj),
                        'raw_json': json.dumps(raw_obj) if raw_obj is not None else None
                    })
                    
                    inserted += 1
                    
                    if inserted % 1000 == 0:
                        await db.commit()
                        logger.info(f"  已处理 {idx + 1}/{len(df)} 条，插入 {inserted} 条")
                        
                except Exception as insert_error:
                    # 遇到插入错误时回滚事务并继续
                    await db.rollback()
                    errors += 1
                    if errors <= 10:  # 只记录前10个错误详情
                        logger.error(f"插入第 {idx} 行失败: {insert_error}")
                        logger.error(f"  数据: date={date_str}, disease={disease_cn}, cases={cases}, deaths={deaths}")
                    continue
                    
            except Exception as e:
                logger.error(f"处理第 {idx} 行时出错: {e}")
                await db.rollback()
                errors += 1
                continue
        
        # 最后提交
        await db.commit()
        
        logger.info(f"\n" + "=" * 70)
        logger.info(f"✅ 导入完成！")
        logger.info(f"  - 成功插入: {inserted} 条")
        logger.info(f"  - 跳过: {skipped} 条")
        logger.info(f"  - 错误: {errors} 条")
        
        if error_diseases:
            logger.warning(f"\n⚠️  有 {len(error_diseases)} 个疾病未找到映射:")
            for disease in sorted(error_diseases)[:20]:
                logger.warning(f"    - {disease}")
            if len(error_diseases) > 20:
                logger.warning(f"    ... 还有 {len(error_diseases) - 20} 个")
        
        # 验证导入结果
        result = await db.execute(text("""
            SELECT d.name, sd.standard_name_zh, COUNT(*) as cnt
            FROM disease_records r
            JOIN diseases d ON r.disease_id = d.id
            LEFT JOIN standard_diseases sd ON d.name = sd.disease_id
            WHERE r.country_id = :country_id AND d.name IN ('D024', 'D006', 'D012')
            GROUP BY d.name, sd.standard_name_zh
            ORDER BY cnt DESC
        """), {'country_id': country_id})
        
        logger.info(f"\n验证痢疾相关疾病:")
        for row in result:
            logger.info(f"  - {row[0]} ({row[1]}): {row[2]} 条记录")


if __name__ == "__main__":
    asyncio.run(main())
