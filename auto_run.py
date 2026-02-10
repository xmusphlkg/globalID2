import os
import subprocess
import sys
import asyncio

# Ensure project root is in path
sys.path.append(os.getcwd())

async def run_pipeline():
    print("==================================================")
    print("   GLOBAL ID V2 - AUTOMATED SETUP PIPELINE")
    print("==================================================")

    # Step 1: Init Database
    # This runs the Typer command which uses the fixed get_database context manager
    print("\n>>> [1/3] Initializing Database (Schema & Base Data)...")
    try:
        subprocess.run([sys.executable, "main.py", "init-database"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error initializing database: {e}")
        sys.exit(1)

    # Step 2: Migrate Data
    # We use the class directly to avoid the interactive prompt in "main.py migrate-data"
    print("\n>>> [2/3] Migrating Historical Data...")
    try:
        from scripts.migrate_data import DataMigration
        
        # Verify path exists
        data_path = "/home/likangguo/globalID/ID_CN/Data/AllData/CN"
        if not os.path.exists(data_path):
            print(f"CRITICAL ERROR: Data path not found: {data_path}")
            sys.exit(1)
            
        migrator = DataMigration(data_path)
        await migrator.setup()
        await migrator.migrate_all(skip_existing=True)
        await migrator.get_statistics()
        await migrator.close()
    except Exception as e:
        print(f"Error during migration: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Step 3: Export Data
    print("\n>>> [3/3] Exporting Data for Verification...")
    try:
        subprocess.run([sys.executable, "main.py", "export-data", "--country", "CN", "--period", "latest"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error exporting data: {e}")
        sys.exit(1)

    print("\n==================================================")
    print("   SUCCESS! System is ready.")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(run_pipeline())
