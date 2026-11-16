"""Identity of Argentina's source-reported national annual HIV notifications.

These constants describe a separate Ministry of Health dataset, never the BEN
cumulative snapshots. Source jurisdiction column names are counterintuitive:
``id_jurisdiccion`` carries ARG and ``jurisdiccion`` carries code 200.
"""

SOURCE_ID = "SRC_AR_MOH_HIV_ANNUAL"
SOURCE_SCOPE = "ar_moh_hiv_annual"
SOURCE_NAME = "Argentina Ministry of Health Annual HIV Notifications"
SOURCE_DISEASE_CODE = "hiv"
SOURCE_DISEASE_LABEL = "HIV annual notifications"
DEFINITION_VERSION = "ar_moh_hiv_annual_notifications_v1"
METRIC_TYPE = "case_notifications"
REPORTING_BASIS = "annual_hiv_notifications"
FREQUENCY = "annual"
DATASET_ID = "1f70534d-f5f0-41f7-bc9a-b9809c39a505"
CATALOGUE_ID = "ed365e37-bac7-580f-911e-17c90ef73324"
CSV_RESOURCE_ID = "747d2078-9d51-40d4-ae59-ced43225459f"
XLSX_RESOURCE_ID = "cdc2fdf3-9637-4fbc-9cf0-d05bdaa00e3f"
CATALOGUE_URL = f"https://datos.gob.ar/api/3/action/package_show?id={CATALOGUE_ID}"
DATASET_URL = f"https://datos.salud.gob.ar/dataset/{DATASET_ID}"
CSV_URL = f"{DATASET_URL}/resource/{CSV_RESOURCE_ID}/download/casos_prov_vih_2014-2023_1.csv"
XLSX_URL = f"{DATASET_URL}/resource/{XLSX_RESOURCE_ID}/download/casos_prov_vih_2014-2023_3.xlsx"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

# Preserve the source's explicit national/sex selection as provenance. This is
# a direct total; provincial and sex strata must never be summed into it.
NATIONAL_DIMENSIONS = {
    "dataset": "casos_prov_vih",
    "sex": "all",
    "source_sex_id": "3",
    "source_sex_label": "Ambos sexos",
    "source_jurisdiction_label": "ARG",
    "source_jurisdiction_code": "200",
    "date_basis": "calendar_year",
}
