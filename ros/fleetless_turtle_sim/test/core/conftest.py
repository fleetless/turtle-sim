# SPDX-License-Identifier: Apache-2.0
"""Hand-made worlds for the core's tests."""

import numpy as np
import pytest

from fleetless_turtle_sim.core import constants as c
from fleetless_turtle_sim.core.world import World


@pytest.fixture
def kinds():
    """An empty world: free everywhere, the pad where the garden has it (cells 3 to 14)."""
    cells = np.full((c.GRID_CELLS, c.GRID_CELLS), c.FREE, np.uint8)
    cells[3:15, 3:15] = c.PAD
    return cells


@pytest.fixture
def world(kinds):
    return World(kinds)
