"""
GlobalID V2 Data Exporter

数据导出器：将清洗整理好的数据导出为多种格式
"""
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import select

from src.core import get_database, get_logger
from src.domain import Country, Disease, DiseaseRecord

logger = get_logger(__name__)


class DataExporter:
    """
    数据导出器
    
    支持的格式：
    - CSV
    - Excel
    - JSON
    - Parquet
    """
    
    def __init__(self, output_dir: Optional[str] = None):
        """
        初始化导出器
        
        Args:
            output_dir: 输出目录
        """
        # self.db = get_database()  # Removed: Use context manager in methods
        self.output_dir = Path(output_dir or "exports")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"DataExporter initialized: {self.output_dir}")
    
    async def export_all(
        self,
        country_code: str,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        formats: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        导出所有数据
        
        Args:
            country_code: 国家代码
            period_start: 起始时间（None=全部）
            period_end: 结束时间（None=至今）
            formats: 导出格式列表（None=全部）
            
        Returns:
            格式 -> 文件路径的字典
        """
        logger.info(f"Exporting data for {country_code}")
        
        # 获取数据
        data = await self._fetch_data(country_code, period_start, period_end)
        
        if data.empty:
            logger.warning("No data to export")
            return {}
        
        # 默认格式
        if formats is None:
            formats = ['csv', 'excel', 'json']
        
        # 生成文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        period_suffix = ""
        if period_start and period_end:
            period_suffix = f"_{period_start.strftime('%Y%m%d')}_{period_end.strftime('%Y%m%d')}"
        
        filename_base = f"{country_code}_data{period_suffix}_{timestamp}"
        
        # 导出各种格式
        exported_files = {}
        
        if 'csv' in formats:
            filepath = await self.export_csv(data, filename_base)
            exported_files['csv'] = filepath
        
        if 'excel' in formats:
            filepath = await self.export_excel(data, filename_base)
            exported_files['excel'] = filepath
        
        if 'json' in formats:
            filepath = await self.export_json(data, filename_base)
            exported_files['json'] = filepath
        
        if 'parquet' in formats:
            filepath = await self.export_parquet(data, filename_base)
            exported_files['parquet'] = filepath
        
        logger.info(f"Exported {len(data)} records in {len(exported_files)} formats")
        return exported_files
    
    async def export_latest(
        self,
        country_code: str,
        formats: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        导出最新数据（latest.csv风格）
        
        Args:
            country_code: 国家代码
            formats: 导出格式列表
            
        Returns:
            格式 -> 文件路径的字典
        """
        logger.info(f"Exporting latest data for {country_code}")
        
        # 获取最新月份的数据
        data = await self._fetch_latest_data(country_code)
        
        if data.empty:
            logger.warning("No latest data to export")
            return {}
        
        # 默认格式
        if formats is None:
            formats = ['csv', 'excel']
        
        filename_base = f"{country_code}_latest"
        
        # 导出
        exported_files = {}
        
        if 'csv' in formats:
            filepath = await self.export_csv(data, filename_base)
            exported_files['csv'] = filepath
        
        if 'excel' in formats:
            filepath = await self.export_excel(data, filename_base)
            exported_files['excel'] = filepath
        
        if 'json' in formats:
            filepath = await self.export_json(data, filename_base)
            exported_files['json'] = filepath
        
        logger.info(f"Exported {len(data)} latest records")
        return exported_files
    
    async def export_monthly(
        self,
        country_code: str,
        year: int,
        month: int,
        formats: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        导出月度数据
        
        Args:
            country_code: 国家代码
            year: 年份
            month: 月份
            formats: 导出格式列表
            
        Returns:
            格式 -> 文件路径的字典
        """
        logger.info(f"Exporting monthly data: {year}-{month:02d}")
        
        # 设置时间范围
        from datetime import datetime
        from calendar import monthrange
        
        period_start = datetime(year, month, 1)
        last_day = monthrange(year, month)[1]
        period_end = datetime(year, month, last_day, 23, 59, 59)
        
        # 获取数据
        data = await self._fetch_data(country_code, period_start, period_end)
        
        if data.empty:
            logger.warning(f"No data for {year}-{month:02d}")
            return {}
        
        # 默认格式
        if formats is None:
            formats = ['csv', 'excel']
        
        # 文件名：CN_2025_June.csv
        month_name = period_start.strftime('%B')  # June, July, etc.
        filename_base = f"{country_code}_{year}_{month_name}"
        
        # 导出
        exported_files = {}
        
        if 'csv' in formats:
            filepath = await self.export_csv(data, filename_base)
            exported_files['csv'] = filepath
        
        if 'excel' in formats:
            filepath = await self.export_excel(data, filename_base)
            exported_files['excel'] = filepath
        
        logger.info(f"Exported {len(data)} records for {year}-{month:02d}")
        return exported_files
    
    async def _fetch_data(
        self,
        country_code: str,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """获取数据"""
        # 构建查询
        query = select(
            DiseaseRecord,
            Disease.name.label('disease_name'),
            Disease.category.label('disease_category'),
            Country.name.label('country_name'),
        ).join(Disease).join(Country).where(
            Country.code == country_code
        )
        
        if period_start:
            query = query.where(DiseaseRecord.time >= period_start)
        if period_end:
            query = query.where(DiseaseRecord.time <= period_end)
        
        query = query.order_by(DiseaseRecord.time.desc(), Disease.name)
        
        # 执行查询
        async with get_database() as db:
            result = await db.execute(query)
            rows = result.all()
        
        if not rows:
            return pd.DataFrame()
        
        # 转换为DataFrame
        data = []
        for row in rows:
            record = row.DiseaseRecord
            data.append({
                'Date': record.time.strftime('%Y-%m-%d'),
                'YearMonth': record.time.strftime('%Y %B'),
                'Disease': row.disease_name,
                'DiseaseCategory': row.disease_category,
                'Cases': record.cases,
                'Deaths': record.deaths,
                'Recoveries': record.recoveries,
                'IncidenceRate': record.incidence_rate,
                'MortalityRate': record.mortality_rate,
                'FatalityRate': (record.deaths / record.cases) if (record.cases and record.deaths) else 0.0,
                'Country': row.country_name,
                'DataQuality': record.data_quality,
                'ConfidenceScore': record.confidence_score,
                'Source': record.data_source,
                'SourceURL': record.metadata_.get("url") if record.metadata_ else None,
            })
        
        df = pd.DataFrame(data)
        return df
    
    async def _fetch_latest_data(self, country_code: str) -> pd.DataFrame:
        """获取最新月份的数据"""
        from sqlalchemy import func
        
        # 获取最新时间
        latest_time_query = select(func.max(DiseaseRecord.time)).join(Country).where(
            Country.code == country_code
        )
        async with get_database() as db:
            latest_time = await db.scalar(latest_time_query)
        
        if not latest_time:
            return pd.DataFrame()
        
        # 获取该月的数据
        from calendar import monthrange
        
        year = latest_time.year
        month = latest_time.month
        
        period_start = datetime(year, month, 1)
        last_day = monthrange(year, month)[1]
        period_end = datetime(year, month, last_day, 23, 59, 59)
        
        return await self._fetch_data(country_code, period_start, period_end)
    
    async def export_csv(self, data: pd.DataFrame, filename_base: str) -> str:
        """导出为CSV"""
        filepath = self.output_dir / f"{filename_base}.csv"
        data.to_csv(filepath, index=False, encoding='utf-8-sig')
        logger.debug(f"Exported CSV: {filepath}")
        return str(filepath)
    
    async def export_excel(self, data: pd.DataFrame, filename_base: str) -> str:
        """导出为Excel"""
        filepath = self.output_dir / f"{filename_base}.xlsx"
        
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            data.to_excel(writer, sheet_name='Data', index=False)
            
            # 格式化工作表
            worksheet = writer.sheets['Data']
            
            # 自动调整列宽
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
        
        logger.debug(f"Exported Excel: {filepath}")
        return str(filepath)
    
    async def export_json(self, data: pd.DataFrame, filename_base: str) -> str:
        """导出为JSON"""
        filepath = self.output_dir / f"{filename_base}.json"
        
        # 转换为记录格式
        data.to_json(filepath, orient='records', indent=2, force_ascii=False)
        
        logger.debug(f"Exported JSON: {filepath}")
        return str(filepath)
    
    async def export_parquet(self, data: pd.DataFrame, filename_base: str) -> str:
        """导出为Parquet（高效压缩格式）"""
        filepath = self.output_dir / f"{filename_base}.parquet"
        data.to_parquet(filepath, index=False, compression='snappy')
        logger.debug(f"Exported Parquet: {filepath}")
        return str(filepath)
    
    async def create_data_package(
        self,
        country_code: str,
        include_all: bool = True,
        include_latest: bool = True,
        include_monthly: bool = False,
    ) -> str:
        """
        创建数据包（ZIP）
        
        Args:
            country_code: 国家代码
            include_all: 包含全部数据
            include_latest: 包含最新数据
            include_monthly: 包含月度数据
            
        Returns:
            ZIP文件路径
        """
        import zipfile
        
        logger.info(f"Creating data package for {country_code}")
        
        # 创建临时目录
        temp_dir = self.output_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
        
        exported_files = []
        
        try:
            # 导出各种数据
            if include_all:
                files = await self.export_all(country_code, formats=['csv', 'excel', 'json'])
                exported_files.extend(files.values())
            
            if include_latest:
                files = await self.export_latest(country_code, formats=['csv', 'excel'])
                exported_files.extend(files.values())
            
            # 创建ZIP
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            zip_filepath = self.output_dir / f"{country_code}_data_package_{timestamp}.zip"
            
            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for filepath in exported_files:
                    if Path(filepath).exists():
                        zipf.write(filepath, Path(filepath).name)
                
                # 添加README
                readme_content = f"""# GlobalID Data Package

Country: {country_code}
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Files Included

"""
                for filepath in exported_files:
                    filename = Path(filepath).name
                    size = Path(filepath).stat().st_size / 1024  # KB
                    readme_content += f"- {filename} ({size:.1f} KB)\n"
                
                readme_content += """
## Data Dictionary

- Date: Record date (YYYY-MM-DD)
- YearMonth: Year and month (YYYY Month)
- Disease: Disease name
- DiseaseCategory: Disease category
- Cases: Number of cases
- Deaths: Number of deaths
- Recoveries: Number of recoveries
- IncidenceRate: Incidence rate
- MortalityRate: Mortality rate
- FatalityRate: Case fatality rate (%)
- Country: Country name
- DataQuality: Data quality level
- ConfidenceScore: Confidence score (0-1)
- Source: Data source
- SourceURL: Source URL

## Notes

- Missing values are represented as NaN or empty
- Data is cleaned and validated
- All dates are in UTC

Generated by GlobalID V2
"""
                
                zipf.writestr('README.txt', readme_content)
            
            logger.info(f"Data package created: {zip_filepath}")
            return str(zip_filepath)
        
        finally:
            # 清理临时文件
            for filepath in exported_files:
                try:
                    Path(filepath).unlink()
                except:
                    pass
