from src.ai.route_selection import route_weight


def test_weight_tracks_remaining_capacity():
    assert route_weight(4, 1) == 3
    assert route_weight(1, 4) == 0
    assert route_weight(0, 0) == 0


def test_weight_uses_smoothed_success_rate_squared():
    assert route_weight(4, 0, [True, False]) == 4 * (3 / 4) ** 2
    assert route_weight(4, 0, [False, False]) == 1


def test_weight_uses_only_last_fifty_results_even_for_iterators():
    assert route_weight(2, 0, iter([False] * 100 + [True] * 50)) == 2
    assert route_weight(2, 0, [True] * 100 + [False] * 50) == 2 * (2 / 52) ** 2
