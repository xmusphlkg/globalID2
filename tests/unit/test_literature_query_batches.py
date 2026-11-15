from src.literature.query_batches import QUERY_ARGUMENT_LIMIT, argument_batches


def test_argument_batches_stay_under_the_driver_limit():
    assert list(argument_batches([])) == []
    assert list(argument_batches(["a", "b", "c"], size=2)) == [["a", "b"], ["c"]]
    assert QUERY_ARGUMENT_LIMIT < 32767
