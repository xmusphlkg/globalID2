# Official anonymous DDS Tableau summary fixtures

Captured on 2026-11-15 from the public DDS2/1_2 dashboard using the regular
guest startSession/bootstrap and summary filters. No underlying-data API,
login or private export was used. Internal use only; reuse rights are unverified.

`single_week_flu_report.zip` contains the initial native inventory and source
controls for report-date/place, year 2569, week 46 and influenza. The explicit
national cards show 32,529 cases and 4 deaths; 4 cases have unknown location.

`sparse_and_zero_report.zip` retains the original response sequence through the
first nine disease categories, because Tableau dictionaries carry incremental
state. Hepatitis A has 14 cases and only 23 province/metric groups. Hepatitis E
has 3 cases while the basis display is absent. Anthrax explicitly reports zero
and has an absent basis display. Tests require selected disease/week/basis
tuples and exact filter identity/target links to both national cards.

Each ZIP manifest records every member hash and the captured selection plan.
Transient guest session identifiers are replaced with equal-length placeholders
in these test fixtures, preserving bootstrap framing. Production raw artifacts
retain the original response bytes and separate SHA256 provenance.
