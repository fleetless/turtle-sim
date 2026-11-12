# SPDX-License-Identifier: Apache-2.0
"""render/constants.py says it holds every number of the two cameras.

This reads the renderers for one it does not hold. It cannot tell a half
that is arithmetic from a half that is a proportion; it does find a 0.42, a
13 and a 0.036, which is what stood in `top` before its numbers had names.
"""

import ast
import inspect

import pytest

from fleetless_turtle_cameras.render import constants as look
from fleetless_turtle_cameras.render import eye, floor, scene, top

# What a renderer may write itself: none and one, a half (a pixel's middle
# lies half a pixel in, a window half a picture around the turtle), two
# coordinates, three colour channels, the four ways a face looks and the
# four bits OpenCV shifts by, and the range of a byte.
ARITHMETIC = {0, 1, 2, 3, 4, 0.5, 255, 256}


def numbers_in(module) -> dict[float, int]:
    """Every number written in a module's source, each with one line it stands in."""
    found = {}
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            found.setdefault(node.value, node.lineno)
    return found


@pytest.mark.parametrize("module", [top, eye, floor, scene], ids=lambda module: module.__name__)
def test_a_renderer_carries_no_number_of_its_own(module):
    own = {
        value: f"line {line}"
        for value, line in numbers_in(module).items()
        if value not in ARITHMETIC
    }
    assert own == {}


def test_the_reading_finds_a_number_where_there_is_one():
    assert set(numbers_in(look)) >= {look.TOP_SIZE_PX, look.TOP_ARROW_TIP, look.EYE_RANGE_M}
    assert 0.42 in numbers_in(look) and 0.42 not in ARITHMETIC


def test_every_camera_number_has_a_sentence():
    """A constant without a word on what it is would be a number with a name and nothing more."""
    source = inspect.getsource(look)
    tree = ast.parse(source)
    statements = tree.body
    undocumented = []
    for index, statement in enumerate(statements):
        if not isinstance(statement, ast.Assign):
            continue
        # Its own docstring, or the one of the group it opens: the next
        # statements are assignments until a string follows.
        following = statements[index + 1 :]
        explained = False
        for later in following:
            if isinstance(later, ast.Expr) and isinstance(later.value, ast.Constant):
                explained = isinstance(later.value.value, str)
                break
            if not isinstance(later, ast.Assign):
                break
        name = statement.targets[0].id
        # The colours explain themselves by their names; the sizes do not.
        if not explained and not isinstance(statement.value, ast.Tuple):
            undocumented.append(name)
    assert undocumented == []
