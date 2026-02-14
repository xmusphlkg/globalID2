"""
GlobalID V2 Disease Mapper

国际化疾病名称映射器
- 使用标准疾病库 (standard_diseases.csv) 作为全局唯一标准
- 支持多国家本地名称映射到标准disease_id
- 适配不同国家的疾病命名差异
"""
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

import pandas as pd

from src.core import get_logger

logger = get_logger(__name__)


@dataclass
class StandardDisease:
    """标准疾病信息"""
    disease_id: str
    standard_name_en: str
    standard_name_zh: str
    category: str
    icd_10: str
    icd_11: str
    description: str


@dataclass
class LocalMapping:
    """本地疾病映射信息"""
    disease_id: str
    local_name: str
    local_code: str
    category: str
    aliases: List[str]


class DiseaseMapper:
    """
    国际化疾病名称映射器
    
    设计理念：
    1. 标准疾病库：全局唯一的疾病标准（disease_id + standard_name_en）
    2. 国家映射表：各国本地名称 -> 标准disease_id
    3. 支持多语言、多变体，最终统一到标准ID
    
    使用示例：
        mapper = DiseaseMapper(country_code="cn")
        
        # 中国本地名称 -> 标准疾病
        disease_id = mapper.map_local_to_id("新冠肺炎")  # -> "D004"
        standard_name = mapper.get_standard_name(disease_id)  # -> "COVID-19"
        
        # 反向查询
        local_name = mapper.map_id_to_local("D004")  # -> "新型冠状病毒感染"
    """
    
    def __init__(self, country_code: str = "cn"):
        """
        初始化疾病映射器
        
        Args:
            country_code: 国家代码（cn/us/uk等），对应configs/{country_code}/目录
        """
        self.country_code = country_code
        
        # 文件路径
        self.standard_file = Path("configs/standard_diseases.csv")
        self.mapping_file = Path(f"configs/{country_code}/disease_mapping.csv")
        
        # 标准疾病库（全局）
        self.standard_diseases: Dict[str, StandardDisease] = {}
        
        # 本地映射表（国家特定）
        self.local_mappings: Dict[str, LocalMapping] = {}
        
        # 快速查找索引
        self.local_to_id: Dict[str, str] = {}  # 本地名称 -> disease_id
        self.id_to_local: Dict[str, str] = {}  # disease_id -> 本地官方名称
        
        # 未识别的疾病（需要人工审核）
        self.unknown_diseases: Set[str] = set()
        
        # 加载数据
        self._load_standard_diseases()
        self._load_local_mappings()

    def get_standard_disease(self, disease_id: str) -> Optional[StandardDisease]:
        """获取标准疾病信息"""
        return self.standard_diseases.get(disease_id)
    
    def _load_standard_diseases(self):
        """加载标准疾病库"""
        if not self.standard_file.exists():
            logger.error(f"标准疾病库文件不存在: {self.standard_file}")
            return
        
        try:
            df = pd.read_csv(self.standard_file)
            
            for _, row in df.iterrows():
                disease = StandardDisease(
                    disease_id=str(row['disease_id']).strip(),
                    standard_name_en=str(row['standard_name_en']).strip(),
                    standard_name_zh=str(row['standard_name_zh']).strip(),
                    category=str(row['category']).strip(),
                    icd_10=str(row['icd_10']).strip() if pd.notna(row['icd_10']) else "",
                    icd_11=str(row['icd_11']).strip() if pd.notna(row['icd_11']) else "",
                    description=str(row['description']).strip() if pd.notna(row['description']) else "",
                )
                self.standard_diseases[disease.disease_id] = disease
            
            logger.info(f"✅ 加载标准疾病库: {len(self.standard_diseases)} 条疾病")
            
        except Exception as e:
            logger.error(f"加载标准疾病库失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _load_local_mappings(self):
        """加载国家特定的本地映射表"""
        if not self.mapping_file.exists():
            logger.error(f"国家映射文件不存在: {self.mapping_file}")
            return
        
        try:
            df = pd.read_csv(self.mapping_file)
            
            for _, row in df.iterrows():
                disease_id = str(row['disease_id']).strip()
                local_name = str(row['local_name']).strip()
                
                # 解析别名
                aliases_str = str(row.get('aliases', ''))
                aliases = []
                if aliases_str and aliases_str != 'nan':
                    aliases = [a.strip() for a in aliases_str.split('|') if a.strip()]
                
                mapping = LocalMapping(
                    disease_id=disease_id,
                    local_name=local_name,
                    local_code=str(row['local_code']).strip(),
                    category=str(row['category']).strip(),
                    aliases=aliases,
                )
                
                self.local_mappings[local_name] = mapping
                
                # 建立索引
                self.local_to_id[local_name] = disease_id
                self.id_to_local[disease_id] = local_name
                
                # 添加别名映射
                for alias in aliases:
                    self.local_to_id[alias] = disease_id
            
            logger.info(f"✅ 加载国家映射 ({self.country_code.upper()}): {len(self.local_mappings)} 条主映射, "
                       f"总计 {len(self.local_to_id)} 个可识别名称（含别名）")
            
        except Exception as e:
            logger.error(f"加载国家映射失败: {e}")
            import traceback
            traceback.print_exc()
    
    def map_local_to_id(self, local_name: str) -> Optional[str]:
        """
        将本地疾病名称映射为标准disease_id
        
        Args:
            local_name: 本地疾病名称（如"新冠肺炎"、"COVID-19"）
            
        Returns:
            标准disease_id（如"D004"），未找到返回None
        """
        local_name = local_name.strip()
        
        # 精确匹配
        if local_name in self.local_to_id:
            return self.local_to_id[local_name]
        
        # 模糊匹配（移除常见前后缀）
        cleaned_name = self._clean_disease_name(local_name)
        if cleaned_name in self.local_to_id:
            return self.local_to_id[cleaned_name]
        
        # 记录未识别的疾病
        self.unknown_diseases.add(local_name)
        logger.warning(f"❌ 未找到本地疾病映射 ({self.country_code}): {local_name}")
        return None
    
    def get_standard_name(self, disease_id: str, lang: str = "en") -> Optional[str]:
        """
        获取标准疾病名称
        
        Args:
            disease_id: 疾病ID（如"D004"）
            lang: 语言（"en"或"zh"）
            
        Returns:
            标准名称，未找到返回None
        """
        if disease_id not in self.standard_diseases:
            return None
        
        disease = self.standard_diseases[disease_id]
        return disease.standard_name_en if lang == "en" else disease.standard_name_zh
    
    def map_id_to_local(self, disease_id: str) -> Optional[str]:
        """
        将标准disease_id映射为本地官方名称
        
        Args:
            disease_id: 疾病ID（如"D004"）
            
        Returns:
            本地官方名称，未找到返回None
        """
        return self.id_to_local.get(disease_id)
    
    def map_local_to_standard(self, local_name: str, lang: str = "en") -> Optional[str]:
        """
        本地名称 -> 标准名称（一步到位）
        
        Args:
            local_name: 本地疾病名称
            lang: 目标语言（"en"或"zh"）
            
        Returns:
            标准疾病名称，未找到返回None
        """
        disease_id = self.map_local_to_id(local_name)
        if not disease_id:
            return None
        
        return self.get_standard_name(disease_id, lang)
    
    def get_disease_info(self, disease_id: str) -> Optional[StandardDisease]:
        """
        获取完整的疾病信息
        
        Args:
            disease_id: 疾病ID
            
        Returns:
            StandardDisease对象，未找到返回None
        """
        return self.standard_diseases.get(disease_id)
    
    def map_dataframe(self, 
                     df: pd.DataFrame, 
                     source_col: str, 
                     target_col: str = None,
                     add_id_col: bool = True,
                     add_standard_col: bool = True) -> pd.DataFrame:
        """
        批量映射DataFrame中的疾病名称
        
        Args:
            df: 数据框
            source_col: 源列名（本地疾病名称）
            target_col: 目标列名（标准英文名），默认为"Diseases"
            add_id_col: 是否添加disease_id列
            add_standard_col: 是否添加标准英文名列
            
        Returns:
            映射后的数据框
        """
        if target_col is None:
            target_col = "Diseases"
        
        # 映射到disease_id
        if add_id_col:
            df['disease_id'] = df[source_col].apply(
                lambda x: self.map_local_to_id(x) if pd.notna(x) and x else None
            )
        
        # 映射到标准英文名
        if add_standard_col:
            df[target_col] = df[source_col].apply(
                lambda x: self.map_local_to_standard(x, lang="en") if pd.notna(x) and x else None
            )
        
        return df
    
    def add_temporary_mapping(self, local_name: str, disease_id: str, aliases: List[str] = None):
        """
        临时添加映射（仅在内存中，不持久化）
        
        用于处理新发现的疾病变体，需要后续更新标准文件。
        
        Args:
            local_name: 本地名称
            disease_id: 标准疾病ID
            aliases: 别名列表
        """
        self.local_to_id[local_name] = disease_id
        
        if aliases:
            for alias in aliases:
                self.local_to_id[alias] = disease_id
        
        logger.info(f"临时添加映射: {local_name} -> {disease_id}")
    
    def get_unknown_diseases(self) -> Set[str]:
        """获取未识别的疾病列表"""
        return self.unknown_diseases.copy()
    
    def export_unknown_diseases(self, output_file: Path):
        """导出未识别的疾病到文件（供人工审核）"""
        if not self.unknown_diseases:
            logger.info("没有未识别的疾病")
            return
        
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"# 未识别的疾病名称 (country: {self.country_code})\n")
                f.write("# 需要添加到映射文件: configs/{}/disease_mapping.csv\n".format(self.country_code))
                f.write("disease_id,local_name,local_code,category,aliases,data_source,notes\n")
                for disease in sorted(self.unknown_diseases):
                    f.write(f",{disease},,,,待审核\n")
            
            logger.info(f"导出 {len(self.unknown_diseases)} 个未识别疾病到: {output_file}")
            
        except Exception as e:
            logger.error(f"导出未识别疾病失败: {e}")
    
    def get_statistics(self) -> Dict:
        """获取映射统计信息"""
        return {
            "country_code": self.country_code,
            "standard_diseases_count": len(self.standard_diseases),
            "local_mappings_count": len(self.local_mappings),
            "total_recognizable_names": len(self.local_to_id),
            "unknown_diseases_count": len(self.unknown_diseases),
        }
    
    @staticmethod
    def _clean_disease_name(name: str) -> str:
        """
        清理疾病名称（移除常见前后缀）
        
        Args:
            name: 疾病名称
            
        Returns:
            清理后的名称
        """
        import re
        
        # 移除括号内容
        name = re.sub(r"\([^)]*\)", "", name)
        name = re.sub(r"（[^）]*）", "", name)
        
        # 移除常见后缀
        suffixes = ["病", "症", "热"]
        for suffix in suffixes:
            if name.endswith(suffix) and len(name) > 2:
                name = name[:-len(suffix)]
        
        return name.strip()
