# Ireland public release

On 2026-11-09 the site operator directed that the Ireland HPSC feeds be
published before written permission was obtained, with the status disclosed on
the website. This is an operator decision, not evidence that HPSC granted
permission. The source metadata retains `written_permission_required`; rights
for the reconstructed weekly archive have not been verified.

The country page and source cards identify the HPSC current weekly hub,
historical weekly archive, historical annual reports and ECDC annual baseline
separately. ECDC's own attribution remains attached to the ECDC source. The
sources keep their original weekly or annual granularity; missing archive weeks
are not zero. Legacy compatibility records remain excluded from public export,
and the source allow-list admits only the four named Ireland source systems.

The operator decision is recorded by `operator_authorized_public_release` in
the IE crawler configuration. Removing it makes collection refuse to use an
updater that declares public release enabled. Revoking public publication also
requires closing the country and per-source release gates and running the
normal publication-retirement workflow.
