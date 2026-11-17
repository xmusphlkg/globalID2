# Malaysia MOH annual notified STD fixture

`std_state.parquet` is the complete, unmodified public release downloaded on
2026-11-15 from <https://storage.data.gov.my/healthcare/std_state.parquet>.
The [official catalogue](https://data.gov.my/data-catalogue/std_state) assigns
CC BY 4.0 and identifies the Ministry of Health Malaysia as the producer.
The 6,633-byte artifact has SHA-256
`6402d05d667883d4f53c479b3c7a05cbc077d0ab5cc1b442448ddd2e57bd307a`.

The file contains 640 rows (16 source geography labels including Malaysia),
five diseases and 2017–2024. Only the 40 explicit `Malaysia` rows are imported
as national facts. W.P. Putrajaya is included in W.P. Kuala Lumpur upstream;
there is no inferred independent Putrajaya series. The original `syphillis`
category spelling is preserved as the source disease code.


## WHO fixtures (2026-11-16)

`who_flunet_dictionary.json` is the full public WHO FluNet field dictionary
response. `who_flunet_sample.json` preserves four original Malaysia records:
the first records for each of NOTDEFINED/NONSENTINEL/SENTINEL, and the latest
2026-W43 sentinel record. The enclosing value collection is a fixture subset;
individual records were not modified. Original endpoints:

- https://xmart-api-public.who.int/FLUMART/VIW_FNT?$filter=COUNTRY_CODE%20eq%20%27MYS%27
- https://xmart-api-public.who.int/FLUMART/VIW_FLU_METADATA?$filter=contains(DatasetName,%20%27FluNet%27)

`who_gho_metadata.json` is the full reviewed-prefix indicator catalogue query
response. `who_gho_sample.json` preserves each available reviewed indicator's
two latest original MYS annual rows, including nulls and legacy cholera Value
strings whose NumericValue is null. These are WHO country-reported indicators;
modelled incidence and rates are excluded. Endpoints and reviewed definitions
are declared in `src/data/crawlers/my_who_annual.py`. These public fixtures are
retained for internal source contract verification; they do not enable public
release of these source series.
