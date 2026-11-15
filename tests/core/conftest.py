import datetime

import pytest

from emile.core import Kitchen

TODAY = datetime.date(2026, 11, 9)
NOW = datetime.datetime(2026, 11, 9, 18, 0, tzinfo=datetime.UTC)


@pytest.fixture
def kitchen() -> Kitchen:
    return Kitchen()
