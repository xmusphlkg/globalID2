"""
GlobalID V2 Disease Mapper

Internationalised disease-name mapper backed by CSV files.
- Uses the standard disease library (standard_diseases.csv) as the global reference.
- Supports per-country local-name mappings to standard disease_id values.
- Adapts to naming differences across countries and languages.
"""
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

import pandas as pd

from src.core import get_logger
from src.core.mapping_paths import resolve_mapping_file

logger = get_logger(__name__)


@dataclass
class StandardDisease:
    """Standard (global) disease record."""
    disease_id: str
    standard_name_en: str
    standard_name_zh: str
    category: str
    icd_10: str
    icd_11: str
    description: str


@dataclass
class LocalMapping:
    """Per-country local disease name mapping."""
    disease_id: str
    local_name: str
    local_code: str
    category: str
    aliases: List[str]


class DiseaseMapper:
    """
    Internationalised disease-name mapper (CSV-backed).

    Design:
    1. Standard disease library: a global, unique set of diseases (disease_id + standard_name_en).
    2. Per-country mapping table: local names → standard disease_id.
    3. Supports multiple languages and name variants, normalised to a single ID.

    Example::

        mapper = DiseaseMapper(country_code="cn")

        # Local name → standard disease
        disease_id = mapper.map_local_to_id("新冠肺炎")  # → "D004"
        standard_name = mapper.get_standard_name(disease_id)  # → "COVID-19"

        # Reverse lookup
        local_name = mapper.map_id_to_local("D004")  # → "新型冠状病毒感染"
    """
    
    def __init__(self, country_code: str = "cn"):
        """
        Initialise the mapper.

        Args:
            country_code: Country code (cn/us/uk/…), determines which
                          ``configs/mapping/<country_code>.csv`` is loaded.
        """
        self.country_code = country_code
        
        # File paths
        self.standard_file = Path("configs/standard_diseases.csv")
        self.mapping_file = resolve_mapping_file(Path("."), country_code)
        
        # Standard disease library (global)
        self.standard_diseases: Dict[str, StandardDisease] = {}

        # Per-country local mapping table
        self.local_mappings: Dict[str, LocalMapping] = {}

        # Fast-lookup indices
        self.local_to_id: Dict[str, str] = {}  # local name → disease_id
        self.id_to_local: Dict[str, str] = {}  # disease_id → primary local name

        # Unrecognised disease names (need manual review)
        self.unknown_diseases: Set[str] = set()
        
        # Load data
        self._load_standard_diseases()
        self._load_local_mappings()

    def get_standard_disease(self, disease_id: str) -> Optional[StandardDisease]:
        """Return the :class:`StandardDisease` for the given disease_id."""
        return self.standard_diseases.get(disease_id)

    def _load_standard_diseases(self):
        """Load the global standard disease library from CSV."""
        if not self.standard_file.exists():
            logger.error(f"[Normalizer] Standard disease file not found | path={self.standard_file}")
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
            
            logger.info(f"[Normalizer] Standard disease library loaded | count={len(self.standard_diseases)}")

        except Exception as e:
            logger.error(f"[Normalizer] Failed to load standard disease library | error={e}")
            import traceback
            traceback.print_exc()
    
    def _load_local_mappings(self):
        """Load the per-country local name mapping table from CSV."""
        if not self.mapping_file.exists():
            logger.error(f"[Normalizer] Country mapping file not found | path={self.mapping_file}")
            return
        
        try:
            df = pd.read_csv(self.mapping_file)
            
            for _, row in df.iterrows():
                disease_id = str(row['disease_id']).strip()
                local_name = str(row['local_name']).strip()
                
                # Parse aliases
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
                
                # Build lookup indices
                self.local_to_id[local_name] = disease_id
                self.id_to_local[disease_id] = local_name

                # Add alias mappings
                for alias in aliases:
                    self.local_to_id[alias] = disease_id
            
            logger.info(
                f"[Normalizer][{self.country_code.upper()}] Mappings loaded"
                f" | primary={len(self.local_mappings)} total={len(self.local_to_id)}"
            )

        except Exception as e:
            logger.error(f"[Normalizer] Failed to load country mapping | country={self.country_code} error={e}")
            import traceback
            traceback.print_exc()
    
    def map_local_to_id(self, local_name: str) -> Optional[str]:
        """
        Map a local disease name to a standard disease_id.

        Args:
            local_name: Local disease name (e.g. "新冠肺炎" or "COVID-19").

        Returns:
            Standard disease_id (e.g. "D004"), or None if not found.
        """
        local_name = local_name.strip()
        
        # Exact match
        if local_name in self.local_to_id:
            return self.local_to_id[local_name]

        # Fuzzy match: strip common prefixes/suffixes
        cleaned_name = self._clean_disease_name(local_name)
        if cleaned_name in self.local_to_id:
            return self.local_to_id[cleaned_name]
        
        # Record unrecognised disease name
        self.unknown_diseases.add(local_name)
        logger.warning(f"[Normalizer][{self.country_code}] Unknown disease | name={local_name!r}")
        return None
    
    def get_standard_name(self, disease_id: str, lang: str = "en") -> Optional[str]:
        """
        Return the standard name for a disease_id.

        Args:
            disease_id: Disease ID (e.g. "D004").
            lang:       Language ("en" or "zh").

        Returns:
            Standard name string, or None if not found.
        """
        if disease_id not in self.standard_diseases:
            return None
        
        disease = self.standard_diseases[disease_id]
        return disease.standard_name_en if lang == "en" else disease.standard_name_zh
    
    def map_id_to_local(self, disease_id: str) -> Optional[str]:
        """
        Map a standard disease_id to the primary local name.

        Args:
            disease_id: Disease ID (e.g. "D004").

        Returns:
            Primary local name, or None if not found.
        """
        return self.id_to_local.get(disease_id)
    
    def map_local_to_standard(self, local_name: str, lang: str = "en") -> Optional[str]:
        """
        Convenience: map a local name directly to a standard name.

        Args:
            local_name: Local disease name.
            lang:       Target language ("en" or "zh").

        Returns:
            Standard disease name, or None if not found.
        """
        disease_id = self.map_local_to_id(local_name)
        if not disease_id:
            return None
        
        return self.get_standard_name(disease_id, lang)
    
    def get_disease_info(self, disease_id: str) -> Optional[StandardDisease]:
        """
        Return the full :class:`StandardDisease` for a disease_id.

        Args:
            disease_id: Disease ID.

        Returns:
            :class:`StandardDisease` or None.
        """
        return self.standard_diseases.get(disease_id)
    
    def map_dataframe(
            self,
            df: pd.DataFrame,
            source_col: str,
            target_col: str = None,
            add_id_col: bool = True,
            add_standard_col: bool = True) -> pd.DataFrame:
        """
        Batch-map disease names in a DataFrame.

        Args:
            df:              Input DataFrame.
            source_col:      Column containing local disease names.
            target_col:      Column for the standard English name (default: "Diseases").
            add_id_col:      If True, add a ``disease_id`` column.
            add_standard_col: If True, add a standard English name column.

        Returns:
            DataFrame with mapping columns added.
        """
        if target_col is None:
            target_col = "Diseases"
        
        # Map to disease_id
        if add_id_col:
            df['disease_id'] = df[source_col].apply(
                lambda x: self.map_local_to_id(x) if pd.notna(x) and x else None
            )
        
        # Map to standard English name
        if add_standard_col:
            df[target_col] = df[source_col].apply(
                lambda x: self.map_local_to_standard(x, lang="en") if pd.notna(x) and x else None
            )
        
        return df
    
    def add_temporary_mapping(self, local_name: str, disease_id: str, aliases: List[str] = None):
        """
        Add a temporary in-memory mapping (not persisted to disk).

        Use this for newly discovered disease variants that need to be reviewed
        and eventually added to the standard mapping files.

        Args:
            local_name: Local disease name.
            disease_id: Standard disease ID.
            aliases:    Optional list of aliases.
        """
        self.local_to_id[local_name] = disease_id
        
        if aliases:
            for alias in aliases:
                self.local_to_id[alias] = disease_id
        
        logger.info(f"[Normalizer] Temporary mapping added | local={local_name!r} disease_id={disease_id}")
    
    def get_unknown_diseases(self) -> Set[str]:
        """Return a copy of the unrecognised disease name set."""
        return self.unknown_diseases.copy()

    def export_unknown_diseases(self, output_file: Path):
        """Export unrecognised disease names to a CSV file for manual review."""
        if not self.unknown_diseases:
            logger.info("[Normalizer] No unknown diseases to export")
            return
        
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"# Unrecognised disease names (country: {self.country_code})\n")
                f.write("# Add to: configs/mapping/{}.csv\n".format(self.country_code.lower()))
                f.write("disease_id,local_name,local_code,category,aliases,data_source,notes\n")
                for disease in sorted(self.unknown_diseases):
                    f.write(f",{disease},,,,pending_review\n")

            logger.info(f"[Normalizer] Unknown diseases exported | count={len(self.unknown_diseases)} file={output_file}")

        except Exception as e:
            logger.error(f"[Normalizer] Failed to export unknown diseases | error={e}")
    
    def get_statistics(self) -> Dict:
        """Return mapper statistics as a dictionary."""
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
        Strip common noise from a disease name for fuzzy matching.

        Args:
            name: Raw disease name.

        Returns:
            Cleaned name.
        """
        import re
        
        # Remove bracketed content
        name = re.sub(r"\([^)]*\)", "", name)
        name = re.sub(r"（[^）]*）", "", name)

        # Strip common Chinese disease suffixes
        suffixes = ["病", "症", "热"]
        for suffix in suffixes:
            if name.endswith(suffix) and len(name) > 2:
                name = name[:-len(suffix)]
        
        return name.strip()
