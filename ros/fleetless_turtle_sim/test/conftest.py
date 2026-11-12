# SPDX-License-Identifier: Apache-2.0
"""A ROS domain of this package's own, unless the caller chose one.

`colcon test` runs packages side by side. On one domain this package's tests
would hear the next package's nodes, and its turtle would take their
commands.
"""

import os

os.environ.setdefault("ROS_DOMAIN_ID", "71")
