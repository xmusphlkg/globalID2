import asyncio
import os
import sys
import glob
import pandas as pd
from sqlalchemy import select, text
from datetime import datetime

# Ensure project root is in python path
sys.path.append(os.getcwd())

from src.core.database import get_db as get_database, get_engine, Base
from src.domain.country import Country
from src.domain.disease import Disease
from src.domain.disease_record import DiseaseRecord

async def force_init_db():
    print("[1] Initializing Database Schema...")
    engine = get_engine()
    async with engine.begin() as conn:
        # Create tables
        await conn.run_sync(Base.metadata.create_all)
    print("    Schema created/verified.")

async def migrate():
    await force_init_db()
    
    print("[2] Reading and Processing Data...")
    
    # Path Configuration
    data_dir = "/home/likangguo/globalID/ID_CN/Data/AllData/CN"
    csv_files = glob.glob(os.path.join(data_dir, "*.csv"))
    
    if not csv_files:
        print(f"Error: No CSV files found in {data_dir}")
        return

    # Read and Concatenate
    dfs = []
    print(f"    Found {len(csv_files)} files. Reading...")
    for f in csv_files:
        try:
            df = pd.read_csv(f)
            dfs.append(df)
        except Exception as e:
            print(f"    Warning: Failed to read {f}: {e}")

    if not dfs:
        print("    No data loaded.")
        return

    full_df = pd.concat(dfs, ignore_index=True)
    initial_count = len(full_df)
    
    # Simple deduplication based on key columns
    # We treat Date + Diseases + Province as unique identity for a record
    full_df.drop_duplicates(subset=['Date', 'Diseases', 'Province'], inplace=True)
    final_count = len(full_df)
    
    print(f"    Loaded {initial_count} rows. After deduplication: {final_count} rows.")

    async with get_database() as db:
        print("[3] Setting up Country (CN)...")
        # Ensure China exists
        stmt = select(Country).where(Country.code == "CN")
        result = await db.execute(stmt)
        country = result.scalar_one_or_none()
        
        if not country:
            country = Country(
                code="CN",
                name="China",
                name_en="China",
                language="zh",
                timezone="Asia/Shanghai",
                crawler_config={"sources": ["cdc_weekly"]},
                data_source_url="http://weekly.chinacdc.cn"
            )
            db.add(country)
            await db.commit()
            print("    Created Country: China")
        else:
            print("    Country China already exists.")
            
        country_id = country.id

        print("[4] Syncing Diseases...")
        # Get all unique diseases from dataframe
        unique_diseases = full_df[['Diseases', 'DiseasesCN']].drop_duplicates()
        
        # Load existing diseases map
        stmt = select(Disease)
        result = await db.execute(stmt)
        existing_diseases = {d.name: d for d in result.scalars().all()}
        
        disease_map = {} # Name -> ID
        
        new_diseases_count = 0
        for _, row in unique_diseases.iterrows():
            d_name = row['Diseases']
            d_cn = row['DiseasesCN']
            
            if pd.isna(d_name):
                continue

            if d_name in existing_diseases:
                disease_map[d_name] = existing_diseases[d_name].id
            else:
                # Create new disease
                new_disease = Disease(
                    name=d_name,
                    name_en=d_name,
                    category="Uncategorized", # Default
                    aliases=[d_cn] if pd.notna(d_cn) else [],
                    metadata_={"name_cn": d_cn} if pd.notna(d_cn) else {}
                )
                db.add(new_disease)
                # We flush to get ID
                await db.flush() 
                existing_diseases[d_name] = new_disease
                disease_map[d_name] = new_disease.id
                new_diseases_count += 1
        
        if new_diseases_count > 0:
            await db.commit()
            print(f"    Added {new_diseases_count} new diseases.")
        else:
            print("    No new diseases to add.")

        print("[5] Inserting Records (Batching)...")
        
        # Prepare records
        records_to_insert = []
        
        # For performance, we might want to check existing records to avoid unique constraint errors 
        # but pure deduplication in python helps. ORM upsert is slow.
        # We will try a robust insert or just assume clean slate/deduplicated input.
        # Since we want to update if exists, merging is better but slower.
        # For bulk history, checking existence is good.
        
        # Let's get existing sets of (time, disease_id, region) to skip
        # Wait, table has (time, disease_id, country_id) as PK.
        # If region is part of key logic, the model PK definition might be insufficient if multiple regions exist for same time/disease.
        # Model: primary_key=(time, disease_id, country_id). 
        # This implies ONLY ONE record per country per disease per time.
        # So Provincial data CANNOT be stored if primary key is just that. 
        # CHECK definition: region is NOT in PK.
        # If 'Province' varies, we can't store province breakdowns with current PK!
        # Unless we only import 'China' rows (National level).
        # Let's check the CSV content again.
        
        # Sample: Province=China.
        # If there are rows for other provinces, they will clash on PK if time/disease/country is same.
        # I will check if there are non-China rows in the DF.
        
        province_counts = full_df['Province'].value_counts()
        print(f"    Province distribution: \n{province_counts.head()}")
        
        # Strategy: Only import National data ("China" or "National" or "全国") 
        # OR Update model PK. 
        # Given "GlobalID" likely tracks National level for now, I will filter for Country-level rows.
        # The sample shows 'Province'='China', 'ProvinceCN'='全国'.
        
        national_df = full_df[full_df['Province'].isin(['China', 'National', '全国'])]
        print(f"    Filtered for National data: {len(national_df)} rows (from {len(full_df)})")
        
        row_count = 0
        batch_size = 1000
        
        for _, row in national_df.iterrows():
            d_name = row['Diseases']
            if d_name not in disease_map:
                continue
                
            cases = int(row['Cases']) if pd.notna(row['Cases']) and row['Cases'] != -10 else 0
            deaths = int(row['Deaths']) if pd.notna(row['Deaths']) and row['Deaths'] != -10 else 0
            
            try:
                rec_time = pd.to_datetime(row['Date'])
            except:
                continue
                
            # Create record object
            # We use merge to upsert (handle existing)
            record = DiseaseRecord(
                time=rec_time,
                country_id=country_id,
                disease_id=disease_map[d_name],
                cases=cases,
                deaths=deaths,
                incidence_rate=float(row['Incidence']) if pd.notna(row['Incidence']) and row['Incidence'] != -10 else None,
                mortality_rate=float(row['Mortality']) if pd.notna(row['Mortality']) and row['Mortality'] != -10 else None,
                data_source=str(row['Source']) if pd.notna(row['Source']) else "CSV Archive",
                region=None, # National
                metadata_={"url": str(row['URL'])} if pd.notna(row['URL']) else {}
            )
            
            # Use merge to upsert
            await db.merge(record)
            
            row_count += 1
            if row_count % batch_size == 0:
                await db.commit()
                print(f"    Processed {row_count} records...", end='\r')
        
        await db.commit()
        print(f"\n    Finished. Total processed: {row_count}")

if __name__ == "__main__":
    asyncio.run(migrate())
