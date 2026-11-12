# SPDX-License-Identifier: Apache-2.0
"""A ROS domain of this package's own, unless the caller chose one.

`colcon test` runs packages side by side. On one domain this bringup's view
would show the next package's fake turtle.
"""

import os

os.environ.setdefault("ROS_DOMAIN_ID", "73")
