# SPDX-License-Identifier: Apache-2.0
"""Runs the turtle's launch file in the container.

(Not called launch.py: Python looks beside a script first, and `import
launch` would find this file.)

It does what `ros2 launch fleetless_turtle_bringup turtle.launch.py` does,
through the same API, with one difference: it listens for a stop before it
imports launch, which takes half a second. The `ros2` command would answer
a stop in that time with a stack trace, or not at all.

The entrypoint sends the stop as SIGUSR1. SIGINT stays ignored in this
process, as it was born (see entrypoint.sh), so that the nodes it starts
are born the same way: until a node has asked for the signal itself, an
interrupt passes it by instead of raising KeyboardInterrupt in the middle
of its imports. Launch stops a node with SIGINT first and, should that
have passed it by, with SIGTERM a second later, which is safe at every
moment: a process that has not asked for it yet simply ends, and one that
has takes it as the same request again.
"""

import os
import signal
import sys
import threading

# After this long launch follows its SIGINT with a SIGTERM. Its own default
# is five seconds, half of what `docker stop` waits. A node needs a tenth of
# a second after its start before it hears a SIGINT, and a stop that falls
# into that tenth should not cost five.
SIGTERM_AFTER_S = "1"


def main() -> int:
    stop = threading.Event()
    running = False

    def on_stop(*_) -> None:
        if not running:
            os._exit(0)  # nothing was started yet: leave at once
        stop.set()

    signal.signal(signal.SIGUSR1, on_stop)

    # The slow part.
    from ament_index_python.packages import get_package_share_directory
    from launch import LaunchDescription, LaunchService
    from launch.actions import IncludeLaunchDescription, OpaqueFunction, SetLaunchConfiguration
    from launch.launch_description_sources import PythonLaunchDescriptionSource

    running = True
    launch_file = os.path.join(
        get_package_share_directory("fleetless_turtle_bringup"), "launch", "turtle.launch.py"
    )

    def begin(context):
        if stop.is_set():
            return None  # asked to stop before a node was started: start none
        return [IncludeLaunchDescription(PythonLaunchDescriptionSource(launch_file))]

    # Not interactive: on a stop, launch tells its nodes itself and does not
    # count on a terminal having done so.
    service = LaunchService(noninteractive=True)
    service.include_launch_description(
        LaunchDescription(
            [
                SetLaunchConfiguration("sigterm_timeout", SIGTERM_AFTER_S),
                OpaqueFunction(function=begin),
            ]
        )
    )

    def pass_the_stop_on() -> None:
        stop.wait()
        # Does nothing while launch is not running yet. Then `begin`, which
        # runs first thing, finds the flag set.
        service.shutdown()

    threading.Thread(target=pass_the_stop_on, name="stop", daemon=True).start()
    return service.run()


if __name__ == "__main__":
    sys.exit(main())
