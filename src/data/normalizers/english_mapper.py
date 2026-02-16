"""
GlobalID V2 Multi-Language Disease Mapper

支持多国多语言的疾病名称映射系统
"""
from typing import Optional, Dict, List, Tuple
from sqlalchemy import text, func
from enum import Enum
import re
from difflib import SequenceMatcher

from .disease_mapper_db import DiseaseMapperDB
from src.core.database import get_db
from src.core.logging import get_logger

logger = get_logger(__name__)


class MultiLanguageDiseaseMapper(DiseaseMapperDB):
    """多国多语言疾病映射器基类"""
    
    def __init__(self, country_code: str, language_code: str):
        """
        初始化多语言映射器
        
        Args:
            country_code: 国家代码 (如 CN, US, UK)
            language_code: 语言代码 (如 en, zh, es, fr)
        """
        self.country_code = country_code
        self.language_code = language_code
        
        # 使用 {country}_{language} 格式作为数据库存储键
        db_key = f"{country_code}_{language_code.upper()}"
        super().__init__(country_code=db_key)
        
        logger.info(f"Multi-language mapper initialized: {country_code} ({language_code})")


class EnglishDiseaseMapper(MultiLanguageDiseaseMapper):
    """
    英文疾病映射器 - 从数据库读取映射（无硬编码）
    
    所有映射数据从CSV文件导入后存储在数据库中。
    使用 generate_english_mappings.py 生成CSV，
    使用 full_rebuild_database.py 导入到数据库。
    """
    
    def __init__(self, country_code: str = "CN"):
        # 英文映射器使用英语
        super().__init__(country_code=country_code, language_code="en")
        logger.info(f"English disease mapper initialized for {country_code} (loading from database)")
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """计算两个字符串的相似度"""
        return SequenceMatcher(None, text1.lower().strip(), text2.lower().strip()).ratio()
    
    def _is_valid_match(self, input_name: str, candidate_name: str, threshold: float = 0.85) -> bool:
        """
        验证是否为有效匹配，避免错误的模糊匹配
        
        Args:
            input_name: 输入的疾病名称
            candidate_name: 候选匹配名称
            threshold: 相似度阈值
        """
        input_clean = re.sub(r'[^a-zA-Z\s]', '', input_name.lower().strip())
        candidate_clean = re.sub(r'[^a-zA-Z\s]', '', candidate_name.lower().strip())
        
        # 完全匹配
        if input_clean == candidate_clean:
            return True
            
        # 计算相似度
        similarity = self._calculate_similarity(input_clean, candidate_clean)
        
        # 对于短词，要求更高的匹配度
        if len(input_clean) <= 10:
            threshold = 0.90
        
        # 避免部分匹配错误：如 "Hepatitis" 不应匹配到 "Hepatitis A"
        # 如果输入词是候选词的一部分，但候选词明显更具体，则不匹配
        input_words = set(input_clean.split())
        candidate_words = set(candidate_clean.split())
        
        # 如果输入词完全包含在候选词中，但候选词有额外的特定标识符
        if (input_words.issubset(candidate_words) and 
            len(candidate_words) > len(input_words)):
            
            # 检查是否有特定的类型标识符（如A, B, C等）
            extra_words = candidate_words - input_words
            specific_indicators = {'a', 'b', 'c', 'd', 'e', 'type', '1', '2', '3', 'acute', 'chronic'}
            
            if extra_words.intersection(specific_indicators):
                logger.debug(f"Rejected specific match: '{input_name}' -> '{candidate_name}' (too specific)")
                return False
        
        return similarity >= threshold
    
    async def fuzzy_match_english(self, disease_name: str) -> Optional[str]:
        """
        改进的模糊匹配英文疾病名称
        """
        try:
            async with get_db() as db:
                # 获取所有英文映射进行智能匹配
                result = await db.execute(text("""
                    SELECT dm.disease_id, dm.local_name, dm.confidence_score,
                           sd.standard_name_en, dm.priority
                    FROM disease_mappings dm
                    JOIN standard_diseases sd ON dm.disease_id = sd.disease_id
                    WHERE dm.country_code LIKE :country_pattern
                      AND dm.is_active = true
                    ORDER BY dm.priority DESC, dm.confidence_score DESC
                """), {
                    "country_pattern": f"%_EN"  # 匹配所有 *_EN 格式的英文映射
                })
                
                candidates = result.fetchall()
                valid_matches = []
                
                # 为每个候选项计算匹配度
                for candidate in candidates:
                    disease_id, local_name, confidence, standard_name, priority = candidate
                    
                    # 检查与本地名称的匹配
                    if self._is_valid_match(disease_name, local_name):
                        similarity = self._calculate_similarity(disease_name, local_name)
                        valid_matches.append((disease_id, local_name, similarity, priority, 'local'))
                    
                    # 检查与标准名称的匹配
                    if self._is_valid_match(disease_name, standard_name):
                        similarity = self._calculate_similarity(disease_name, standard_name)
                        valid_matches.append((disease_id, standard_name, similarity, priority, 'standard'))
                
                if valid_matches:
                    # 按相似度和优先级排序
                    valid_matches.sort(key=lambda x: (x[2], x[3]), reverse=True)
                    best_match = valid_matches[0]
                    
                    logger.info(f"Smart matched '{disease_name}' to '{best_match[1]}' ({best_match[0]}, similarity: {best_match[2]:.2f})")
                    return best_match[0]
                
                logger.debug(f"No valid fuzzy match found for '{disease_name}'")
                return None
                
        except Exception as e:
            logger.error(f"Fuzzy matching failed: {e}")
            return None
    
    async def map_local_to_id(self, local_name: str) -> Optional[str]:
        """
        重写映射方法，增加英文特定的处理逻辑
        """
        # 先尝试标准映射
        result = await super().map_local_to_id(local_name)
        if result:
            return result
        
        # 如果标准映射失败，尝试模糊匹配
        return await self.fuzzy_match_english(local_name)
    
    async def get_statistics(self) -> Dict[str, int]:
        """获取英文映射器统计信息"""
        stats = await super().get_statistics()
        stats['mapper_type'] = 'English'
        return stats


# 支持的国家和语言配置
SUPPORTED_COUNTRIES = {
    "CN": {"name": "中国", "primary_language": "zh", "supported_languages": ["zh", "en"]},
    "US": {"name": "美国", "primary_language": "en", "supported_languages": ["en", "es"]},
    "UK": {"name": "英国", "primary_language": "en", "supported_languages": ["en"]},
    "CA": {"name": "加拿大", "primary_language": "en", "supported_languages": ["en", "fr"]},
    "AU": {"name": "澳大利亚", "primary_language": "en", "supported_languages": ["en"]},
}

DATA_SOURCE_CONFIG = {
    # 英文数据源
    "cdc_weekly": {"language": "en", "country": "CN"},
    "cdc_us": {"language": "en", "country": "US"},
    "pubmed": {"language": "en", "country": "CN"},  # 国际期刊，默认中国视角
    "who": {"language": "en", "country": "CN"},
    
    # 中文数据源
    "nhc": {"language": "zh", "country": "CN"},
    "cdc_cn": {"language": "zh", "country": "CN"},
    
    # 其他语言数据源示例
    "cdc_es": {"language": "es", "country": "US"},  # 西班牙语CDC
    "sante_fr": {"language": "fr", "country": "CA"},  # 法语健康部门
}


# 工厂函数，根据国家、语言和数据源创建合适的映射器
async def create_disease_mapper(
    country_code: str = "CN", 
    language: str = None, 
    data_source: str = None
) -> DiseaseMapperDB:
    """
    根据国家、语言和数据源创建疾病映射器
    
    Args:
        country_code: 国家代码 ("CN", "US", "UK"等)
        language: 语言代码 ("en", "zh", "es", "fr")
        data_source: 数据源名称，用于智能选择映射器
    
    Returns:
        疾病映射器实例
    """
    # 根据数据源智能选择国家和语言
    if data_source and data_source in DATA_SOURCE_CONFIG:
        config = DATA_SOURCE_CONFIG[data_source]
        if not language:
            language = config["language"]
        if country_code == "CN" and "country" in config:  # 如果使用默认国家，则采用数据源建议
            country_code = config["country"]
    
    # 验证国家代码
    if country_code not in SUPPORTED_COUNTRIES:
        logger.warning(f"Unsupported country: {country_code}, using CN")
        country_code = "CN"
    
    # 确定语言
    if not language:
        language = SUPPORTED_COUNTRIES[country_code]["primary_language"]
    
    # 验证该国家是否支持该语言
    if language not in SUPPORTED_COUNTRIES[country_code]["supported_languages"]:
        logger.warning(f"Language {language} not supported in {country_code}, using primary language")
        language = SUPPORTED_COUNTRIES[country_code]["primary_language"]
    
    logger.info(f"Creating mapper for {country_code} ({language}) from source: {data_source or 'manual'}")
    
    if language.lower() in ["en", "english"]:
        # 英文映射器：从数据库读取映射（需先运行 full_rebuild_database.py 导入）
        # EnglishDiseaseMapper 会自动构造 {country_code}_EN 作为db_key
        mapper = EnglishDiseaseMapper(country_code=country_code)
        return mapper
    else:
        # 中文映射器：从数据库读取映射，直接使用country_code（如"CN"）
        return DiseaseMapperDB(country_code=country_code)


# 向后兼容的简化函数
async def create_language_mapper(language: str = "zh", data_source: str = None) -> DiseaseMapperDB:
    """
    向后兼容函数：根据语言选择映射器
    
    Args:
        language: 语言代码 ("en", "zh")
        data_source: 数据源名称
    
    Returns:
        疾病映射器实例
    """
    return await create_disease_mapper(
        country_code="CN",  # 默认中国
        language=language,
        data_source=data_source
    )