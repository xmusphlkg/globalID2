"""Incidence of a public total computed from several notification series."""

from collections.abc import Iterable


def aggregate_incidence(cases: int | float, components: Iterable[dict]) -> tuple[float | None, str]:
    """Compute a rate only when all components identify the same population."""
    populations = {(row.get("population"), row.get("population_source")) for row in components}
    if len(populations) == 1:
        population, source = populations.pop()
        if population and population > 0 and source:
            return cases * 100000 / population, f"{source.lower()}_computed"
    return None, "missing_population"
