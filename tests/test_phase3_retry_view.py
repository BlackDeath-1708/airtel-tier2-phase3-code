import collections

from gui import phase3_data as p3
from phase3.checkpoint import unit_key
from phase3.parked import ParkedStatus
from tests.test_phase3_probe_wait import PairMissingClient

PAIR = ("Erode", 135801)
UNITS = [unit_key("10.0.0.1", "Delhi"), unit_key("10.0.0.2", "Delhi")]


def test_released_units_are_counted_for_their_pair_until_they_settle(tmp_path):
    # Arrange
    client = PairMissingClient(PAIR, back_at=float("inf"))
    parked = ParkedStatus(str(tmp_path))
    parked.released(PAIR, UNITS)

    # Act
    parked.snapshot({}, client, force=True)
    handed_back = p3.read_parked(str(tmp_path))

    # Assert
    assert handed_back["released_for_retry"] == 2
    assert handed_back["pairs"][0]["released_for_retry"] == 2
    assert handed_back["pairs"][0]["parked_units"] == 0

    # Act
    for unit in UNITS:
        parked.settled(unit)
    parked.snapshot({}, client, force=True)
    settled = p3.read_parked(str(tmp_path))

    # Assert
    assert settled["released_for_retry"] == 0
    assert settled["pairs"] == []


def test_a_pair_counts_how_many_times_it_was_checked_again(tmp_path):
    client = PairMissingClient(PAIR, back_at=float("inf"))
    parked = ParkedStatus(str(tmp_path))
    parked.missing(PAIR, 2)
    parked.recheck(PAIR, 2)
    parked.recheck(PAIR, 2)

    parked.snapshot({PAIR: collections.deque([{"ip": "10.0.0.1", "city": "Delhi"}])}, client, force=True)
    pair = p3.read_parked(str(tmp_path))["pairs"][0]

    assert pair["rechecks"] == 2
    assert pair["parked_units"] == 1


def test_parked_pairs_table_shows_the_released_and_recheck_counts():
    parked = {"pairs": [{
        "probe_city": "Erode", "asn": 135801, "parked_units": 0,
        "released_for_retry": 3, "rechecks": 4,
        "missing_since": None, "next_check_at": None, "gives_up_at": None,
    }]}

    frame = p3.parked_pairs_frame(parked, {135801: "C32 Broadband"})

    assert frame.iloc[0]["Released for retry"] == 3
    assert frame.iloc[0]["Rechecks"] == 4
