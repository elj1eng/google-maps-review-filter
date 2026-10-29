"""Live end-to-end checks against three fixed spots.

Each test runs the real CLI pipeline (scrape -> analyze -> JSON report)
and asserts the output is usable, so a markup change, a wrong place name,
or the 5-review sign-in wall fails loudly instead of silently degrading.

Needs a saved Google session (`gmaps-filter --login`): anonymous runs are
truncated to ~5 reviews. Tests skip without one, keeping CI offline;
set GMAPS_LIVE=1 to force them anyway.
"""

import json
import os

import pytest

from gmaps_review_filter.cli import EXIT_OK, run_once
from gmaps_review_filter.refresh_fixtures import LUGER_URL, MBS_URL
from gmaps_review_filter.scraper import resolve_auth_state

BRISNIE_URL = "https://maps.app.goo.gl/jQokksYrdN2s8DDC6"
LIVE_TARGET = 60
TRUST_LIMIT = 10

SPOTS = [
    pytest.param(BRISNIE_URL, "Brisnie Matcha", id="brisnie-matcha"),
    pytest.param(MBS_URL, "Marina Bay Sands Singapore", id="marina-bay-sands"),
    pytest.param(LUGER_URL, "Peter Luger Steak House", id="peter-luger"),
]


def _live_ready() -> bool:
    return os.environ.get("GMAPS_LIVE") == "1" or resolve_auth_state() is not None


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _live_ready(),
        reason="no saved Google session (run --login); set GMAPS_LIVE=1 to force",
    ),
]


@pytest.mark.parametrize("url,expected_name", SPOTS)
def test_fixed_spot_report(url, expected_name, capsys):
    code = run_once(
        url,
        target=LIVE_TARGET,
        trust_threshold=TRUST_LIMIT,
        as_json=True,
        verbose=False,
    )
    out, err = capsys.readouterr()

    assert code == EXIT_OK, f"{expected_name}: scrape failed\n{err}"
    data = json.loads(out)

    assert data["schema_version"] == 1
    assert data["source"]["url"] == url
    assert data["source"]["requested_target"] == LIVE_TARGET
    assert data["place"]["name"] == expected_name
    assert data["trust"]["threshold"] == TRUST_LIMIT
    total = data["place"]["total_reviews"]
    assert isinstance(total, int) and total > 0
    # fetch_html's stub guard already rejects short samples, so a
    # successful run must hold the full min(target, total) sample.
    assert data["sample"]["size"] == min(LIVE_TARGET, total)
    assert data["sample"]["classified"] <= data["sample"]["size"]
    assert data["sample"]["unrated"] >= 0
    assert (
        data["trust"]["filtered"] + data["trust"]["trusted"] <= data["sample"]["size"]
    )
    if data["trust"]["ratio"] is not None:
        assert 0.0 <= data["trust"]["ratio"] <= 1.0
    assert 0.0 <= data["ratings"]["all"] <= 5.0
    assert 0.0 <= data["ratings"]["trusted"] <= 5.0
