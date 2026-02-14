#!/usr/bin/env python3
"""
全面数据库数据检查

检查项目：
1. 数据完整性（外键、孤立记录）
2. 数据一致性（重复记录）
3. 数据质量（异常值、缺失值）
4. 时间序列完整性
5. 疾病映射准确性
"""
import asyncio
import sys
import os
from datetime import datetime

sys.path.append(os.getcwd())

from sqlalchemy import text
from src.core.database import get_db


class DataChecker:
    def __init__(self):
        self.issues = []
        self.warnings = []
        self.info = []

    def add_issue(self, category, message, severity='ERROR'):
        self.issues.append({'category': category, 'message': message, 'severity': severity})

    def add_warning(self, category, message):
        self.warnings.append({'category': category, 'message': message})

    def add_info(self, category, message):
        self.info.append({'category': category, 'message': message})

    async def check_all(self):
        """执行所有检查"""
        print("=" * 70)
        print("数据库数据质量检查")
        print("=" * 70)

        await self.check_basic_stats()
        await self.check_data_integrity()
        await self.check_duplicates()
        await self.check_data_quality()
        await self.check_time_series()
        await self.check_disease_mapping()
        await self.check_data_completeness()

        self.print_summary()

    async def check_basic_stats(self):
        print("\n[1] 基本统计")
        print("-" * 70)

        async with get_db() as db:
            # 配置表统计
            result = await db.execute(text("SELECT COUNT(*) FROM standard_diseases"))
            std_count = result.scalar() or 0
            print(f"  标准疾病: {std_count} 个")
            
            result = await db.execute(text("SELECT COUNT(*) FROM disease_mappings"))
            mapping_count = result.scalar() or 0
            print(f"  疾病映射: {mapping_count} 条")
            
            result = await db.execute(text("SELECT COUNT(*) FROM diseases"))
            disease_count = result.scalar() or 0
            print(f"  diseases表: {disease_count} 个")
            
            # 疾病记录统计
            result = await db.execute(text("SELECT COUNT(*) FROM disease_records"))
            total = result.scalar() or 0
            print(f"  疾病记录: {total:,} 条")
            self.add_info('stats', f'总记录数: {total:,}')

            result = await db.execute(text("""
                SELECT MIN(time), MAX(time), COUNT(DISTINCT DATE_TRUNC('month', time))
                FROM disease_records
            """))
            row = result.one()
            if row and row[0]:
                min_time, max_time, month_count = row
                print(f"  时间范围: {min_time.date()} 至 {max_time.date()}")
                print(f"  覆盖月份数: {month_count}")
                self.add_info('stats', f'时间范围: {min_time.date()} 至 {max_time.date()}')

            result = await db.execute(text("SELECT COUNT(DISTINCT disease_id) FROM disease_records"))
            record_disease_count = result.scalar() or 0
            print(f"  涉及疾病数: {record_disease_count}")
            
            # 数据源统计
            result = await db.execute(text("""
                SELECT data_source, COUNT(*) as cnt
                FROM disease_records
                GROUP BY data_source
                ORDER BY cnt DESC
            """))
            sources = result.fetchall()
            if sources:
                print(f"\n  数据源统计:")
                for source, cnt in sources:
                    print(f"    {source}: {cnt:,} 条")
            
            # Top 10 疾病记录数
            result = await db.execute(text("""
                SELECT d.name, COUNT(*) as count
                FROM disease_records dr
                JOIN diseases d ON dr.disease_id = d.id
                GROUP BY d.name
                ORDER BY count DESC
                LIMIT 10
            """))
            top_diseases = result.fetchall()
            if top_diseases:
                print(f"\n  Top 10 疾病记录数:")
                for name, count in top_diseases:
                    print(f"    {name}: {count:,} 条")

    async def check_data_integrity(self):
        print("\n[2] 数据完整性检查")
        print("-" * 70)

        async with get_db() as db:
            result = await db.execute(text("""
                SELECT COUNT(*) FROM disease_records dr
                WHERE NOT EXISTS (SELECT 1 FROM diseases d WHERE d.id = dr.disease_id)
            """))
            orphaned = result.scalar() or 0
            if orphaned > 0:
                print(f"  ❌ 孤立记录: {orphaned} 条")
                self.add_issue('integrity', f'发现 {orphaned} 条孤立记录（disease_id无效）', 'CRITICAL')
            else:
                print("  ✓ 无孤立记录")

            result = await db.execute(text("""
                SELECT 
                    COUNT(*) FILTER (WHERE disease_id IS NULL) as null_disease,
                    COUNT(*) FILTER (WHERE country_id IS NULL) as null_country,
                    COUNT(*) FILTER (WHERE time IS NULL) as null_time,
                    COUNT(*) FILTER (WHERE cases IS NULL) as null_cases
                FROM disease_records
            """))
            nulls = result.one()
            if any(nulls):
                print("  ❌ 发现NULL值:")
                if nulls[0]: print(f"     disease_id: {nulls[0]}")
                if nulls[1]: print(f"     country_id: {nulls[1]}")
                if nulls[2]: print(f"     time: {nulls[2]}")
                if nulls[3]: print(f"     cases: {nulls[3]}")
                self.add_issue('integrity', 'critical字段包含NULL值', 'CRITICAL')
            else:
                print("  ✓ 关键字段无NULL值")

    async def check_duplicates(self):
        print("\n[3] 重复记录检查")
        print("-" * 70)

        async with get_db() as db:
            result = await db.execute(text("""
                SELECT DATE_TRUNC('day', time)::date as day, disease_id, COUNT(*) as cnt
                FROM disease_records
                GROUP BY day, disease_id
                HAVING COUNT(*) > 1
            """))
            duplicates = result.fetchall()
            if duplicates:
                print(f"  ⚠️  发现 {len(duplicates)} 组重复记录:")
                for day, disease_id, cnt in duplicates[:10]:
                    print(f"     {day} | disease_id={disease_id} | count={cnt}")
                self.add_issue('duplicates', f'发现 {len(duplicates)} 组重复记录', 'ERROR')
            else:
                print("  ✓ 无重复记录")

    async def check_data_quality(self):
        print("\n[4] 数据质量检查")
        print("-" * 70)

        async with get_db() as db:
            result = await db.execute(text("SELECT COUNT(*) FROM disease_records WHERE cases < 0 OR deaths < 0"))
            negative = result.scalar() or 0
            if negative > 0:
                print(f"  ⚠️  负值记录: {negative} 条")
                self.add_warning('quality', f'{negative} 条记录包含负值')
            else:
                print("  ✓ 无负值")

            result = await db.execute(text("""
                SELECT time, d.name, dr.cases, dr.deaths
                FROM disease_records dr
                JOIN diseases d ON dr.disease_id = d.id
                WHERE dr.cases > 1000000 OR dr.deaths > 100000
                ORDER BY dr.cases DESC
                LIMIT 5
            """))
            large_values = result.fetchall()
            if large_values:
                print("  ⚠️  异常大的数值 (cases > 1M 或 deaths > 100K):")
                for time, name, cases, deaths in large_values:
                    print(f"     {time.date()} | {name}: cases={cases:,}, deaths={deaths:,}")
                self.add_warning('quality', f'发现 {len(large_values)} 条异常大的数值')
            else:
                print("  ✓ 无明显异常数值")

            result = await db.execute(text("""
                SELECT time, d.name, dr.cases, dr.deaths
                FROM disease_records dr
                JOIN diseases d ON dr.disease_id = d.id
                WHERE dr.deaths > dr.cases AND dr.cases > 0
                LIMIT 10
            """))
            deaths_exceed = result.fetchall()
            if deaths_exceed:
                print(f"  ⚠️  死亡数大于病例数: {len(deaths_exceed)} 条")
                for time, name, cases, deaths in deaths_exceed[:3]:
                    print(f"     {time.date()} | {name}: cases={cases}, deaths={deaths}")
                self.add_warning('quality', f'{len(deaths_exceed)} 条记录死亡数大于病例数')
            else:
                print("  ✓ 死亡数均小于等于病例数")

            result = await db.execute(text("""
                SELECT 
                    COUNT(*) FILTER (WHERE cases = 0) as zero_cases,
                    COUNT(*) FILTER (WHERE deaths = 0) as zero_deaths,
                    COUNT(*) as total
                FROM disease_records
            """))
            zero_cases, zero_deaths, total = result.one()
            total = total or 1
            print(f"\n  零值统计:")
            print(f"     cases=0: {zero_cases:,} ({zero_cases/total*100:.1f}%)")
            print(f"     deaths=0: {zero_deaths:,} ({zero_deaths/total*100:.1f}%)")

    async def check_time_series(self):
        print("\n[5] 时间序列完整性")
        print("-" * 70)

        async with get_db() as db:
            result = await db.execute(text("""
                SELECT DATE_TRUNC('month', time)::date as month, COUNT(*) as cnt
                FROM disease_records
                GROUP BY month
                ORDER BY month
            """))
            monthly_counts = list(result.fetchall())
            if monthly_counts:
                min_month = monthly_counts[0][0]
                max_month = monthly_counts[-1][0]
                print(f"  数据范围: {min_month} 至 {max_month}")
                current = min_month
                expected_months = []
                while current <= max_month:
                    expected_months.append(current)
                    if current.month == 12:
                        current = current.replace(year=current.year + 1, month=1)
                    else:
                        current = current.replace(month=current.month + 1)
                actual_months = {m[0] for m in monthly_counts}
                missing_months = [m for m in expected_months if m not in actual_months]
                if missing_months:
                    print(f"  ⚠️  缺失月份: {len(missing_months)} 个")
                    for month in missing_months[:5]:
                        print(f"     {month}")
                    if len(missing_months) > 5:
                        print(f"     ... 还有 {len(missing_months)-5} 个")
                    self.add_warning('time_series', f'缺失 {len(missing_months)} 个月份的数据')
                else:
                    print("  ✓ 时间序列连续")

    async def check_disease_mapping(self):
        print("\n[6] 疾病映射检查")
        print("-" * 70)

        async with get_db() as db:
            result = await db.execute(text("""
                SELECT COUNT(*) 
                FROM diseases d
                WHERE NOT EXISTS (
                    SELECT 1 FROM standard_diseases sd 
                    WHERE sd.standard_name_en = d.name OR sd.disease_id = d.name
                )
                AND NOT EXISTS (
                    SELECT 1 FROM disease_mappings dm
                    WHERE dm.local_name = d.name
                )
            """))
            unmatched_diseases = result.scalar() or 0

            if unmatched_diseases > 0:
                print(f"  ⚠️  {unmatched_diseases} 个疾病未在standard_diseases或disease_mappings中找到")
                result = await db.execute(text("""
                    SELECT d.name
                    FROM diseases d
                    WHERE NOT EXISTS (
                        SELECT 1 FROM standard_diseases sd 
                        WHERE sd.standard_name_en = d.name OR sd.disease_id = d.name
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM disease_mappings dm
                        WHERE dm.local_name = d.name
                    )
                    LIMIT 10
                """))
                unmatched = result.fetchall()
                print("     示例:")
                for (name,) in unmatched[:5]:
                    print(f"       - {name}")
                self.add_warning('mapping', f'{unmatched_diseases} 个疾病未在标准列表或映射表中')
            else:
                print("  ✓ 所有疾病均在标准列表或映射表中")

            result = await db.execute(text("""
                SELECT d.name, COUNT(*) as record_count
                FROM disease_records dr
                JOIN diseases d ON dr.disease_id = d.id
                WHERE NOT EXISTS (
                    SELECT 1 FROM standard_diseases sd
                    WHERE sd.standard_name_en = d.name OR sd.disease_id = d.name
                )
                AND NOT EXISTS (
                    SELECT 1 FROM disease_mappings dm
                    WHERE dm.local_name = d.name
                )
                GROUP BY d.name
                ORDER BY record_count DESC
                LIMIT 10
            """))
            unmapped_with_records = result.fetchall()
            if unmapped_with_records:
                print(f"\n  ⚠️  有数据记录但未映射的疾病:")
                for name, cnt in unmapped_with_records:
                    print(f"     {name}: {cnt} 条记录")
                self.add_warning('mapping', f'{len(unmapped_with_records)} 个疾病有记录但未标准化')

    async def check_data_completeness(self):
        print("\n[7] 数据完整性/完整性检查（基于频率）")
        print("-" * 70)

        async with get_db() as db:
            result = await db.execute(text("""
                SELECT COUNT(*) as total,
                       COUNT(*) FILTER (WHERE EXTRACT(day FROM time) = 1) as month_start_count,
                       COUNT(DISTINCT DATE_TRUNC('month', time)) as distinct_months
                FROM disease_records
            """))
            total, month_start_count, distinct_months = result.one()
            total = total or 0

            if total == 0:
                print("  ℹ️  无数据可检查")
                return

            prop_month_start = month_start_count / total if total else 0
            print(f"  数据总数: {total:,}, 月初日期占比: {prop_month_start:.2%}, 覆盖月份数: {distinct_months}")

            if prop_month_start >= 0.75 and distinct_months >= 3:
                print("  识别为月度数据，开始按疾病检查每月覆盖性...")

                result = await db.execute(text("""
                    SELECT dr.disease_id,
                           MIN(DATE_TRUNC('month', dr.time))::date as min_month,
                           MAX(DATE_TRUNC('month', dr.time))::date as max_month,
                           COUNT(DISTINCT DATE_TRUNC('month', dr.time)) as actual_months
                    FROM disease_records dr
                    GROUP BY dr.disease_id
                """))

                issues = []
                completeds = []
                for disease_id, min_month, max_month, actual_months in result.fetchall():
                    months_expected = (max_month.year - min_month.year) * 12 + (max_month.month - min_month.month) + 1
                    if actual_months < months_expected:
                        missing = months_expected - actual_months
                        issues.append((disease_id, min_month, max_month, actual_months, months_expected, missing))
                    else:
                        completeds.append((disease_id, min_month, max_month, actual_months, months_expected))

                name_map = {}
                ids_to_lookup = {str(i[0]) for i in issues} | {str(c[0]) for c in completeds}
                if ids_to_lookup:
                    id_list_sql = ','.join(ids_to_lookup)
                    name_result = await db.execute(text(f"""
                        SELECT d.id,
                               COALESCE(sd.standard_name_zh, sd.standard_name_en, d.name) as display_name
                        FROM diseases d
                        LEFT JOIN standard_diseases sd ON (sd.disease_id = d.name OR sd.standard_name_en = d.name)
                        WHERE d.id IN ({id_list_sql})
                    """))
                    for did, display_name in name_result.fetchall():
                        name_map[str(did)] = display_name

                if issues:
                    print(f"  ⚠️  有 {len(issues)} 个疾病存在缺失月份（未覆盖所有期望月份）")
                    for did, min_m, max_m, actual, expected, missing in issues:
                        name = name_map.get(str(did), f'id:{did}')
                        print(f"     {name} ({did}): {min_m} ~ {max_m}, 实际月份={actual}, 期望={expected}, 缺失={missing}")
                        
                        # 查询该疾病实际存在的月份
                        actual_months_result = await db.execute(text("""
                            SELECT DISTINCT DATE_TRUNC('month', time)::date AS month
                            FROM disease_records
                            WHERE disease_id = :disease_id
                            ORDER BY month
                        """), {"disease_id": did})
                        
                        actual_months_set = {row[0] for row in actual_months_result.fetchall()}
                        
                        # 在Python中生成期望的所有月份
                        from datetime import timedelta
                        expected_months = []
                        current = min_m
                        while current <= max_m:
                            expected_months.append(current)
                            # 移动到下个月
                            if current.month == 12:
                                current = current.replace(year=current.year + 1, month=1)
                            else:
                                current = current.replace(month=current.month + 1)
                        
                        # 找出缺失的月份
                        missing_months = [m for m in expected_months if m not in actual_months_set]
                        
                        if missing_months:
                            # 将连续的月份合并为范围
                            ranges = []
                            start = missing_months[0]
                            prev = missing_months[0]
                            
                            for i in range(1, len(missing_months)):
                                current = missing_months[i]
                                # 检查是否连续（计算月份差异）
                                months_diff = (current.year - prev.year) * 12 + (current.month - prev.month)
                                if months_diff > 1:
                                    # 不连续，结束当前范围
                                    if start == prev:
                                        ranges.append(f"{start.strftime('%Y-%m')}")
                                    else:
                                        ranges.append(f"{start.strftime('%Y-%m')}至{prev.strftime('%Y-%m')}")
                                    start = current
                                prev = current
                            
                            # 添加最后一个范围
                            if start == prev:
                                ranges.append(f"{start.strftime('%Y-%m')}")
                            else:
                                ranges.append(f"{start.strftime('%Y-%m')}至{prev.strftime('%Y-%m')}")
                            
                            print(f"        缺失月份: {', '.join(ranges)}")
                    
                    self.add_warning('completeness', f'{len(issues)} 个疾病在其时间范围内缺失月份')
                else:
                    print("  ✓ 未发现疾病缺失月份")

                if completeds:
                    print(f"\n  ✓ 有 {len(completeds)} 个疾病在其最小/最大月份范围内每月均有数据")
                    for did, min_m, max_m, actual, expected in completeds:
                        name = name_map.get(str(did), f'id:{did}')
                        print(f"     {name} ({did}): {min_m} ~ {max_m}, 月数={expected}")
            else:
                print("  识别到的数据不是典型月度频率，跳过按疾病逐月完整性检查")
                self.add_info('completeness', '数据频率非月度，已跳过逐疾病月度完整性检查')

    def print_summary(self):
        print("\n" + "=" * 70)
        print("检查结果摘要")
        print("=" * 70)

        critical_issues = [i for i in self.issues if i['severity'] == 'CRITICAL']
        error_issues = [i for i in self.issues if i['severity'] == 'ERROR']

        print(f"\n严重问题 (CRITICAL): {len(critical_issues)}")
        for issue in critical_issues:
            print(f"  ❌ [{issue['category']}] {issue['message']}")

        print(f"\n错误 (ERROR): {len(error_issues)}")
        for issue in error_issues:
            print(f"  ⚠️  [{issue['category']}] {issue['message']}")

        print(f"\n警告 (WARNING): {len(self.warnings)}")
        for warning in self.warnings[:10]:
            print(f"  ⚠️  [{warning['category']}] {warning['message']}")
        if len(self.warnings) > 10:
            print(f"  ... 还有 {len(self.warnings)-10} 个警告")

        print("\n" + "=" * 70)

        if critical_issues or error_issues:
            print("⚠️  发现严重问题，建议处理")
        elif self.warnings:
            print("✓ 未发现严重问题，但有一些警告需要注意")
        else:
            print("✅ 数据质量良好，未发现问题")

        print("=" * 70)


async def main():
    checker = DataChecker()
    await checker.check_all()


if __name__ == "__main__":
    asyncio.run(main())
