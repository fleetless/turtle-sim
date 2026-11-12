# SPDX-License-Identifier: Apache-2.0
"""The ros2 daemon, started with the turtle.

`ros2 topic list`, `ros2 node list` and their kind do not look at the graph
themselves. They ask a daemon that has been listening to it. Where none
runs, the first question starts one and is meanwhile answered by a node
that has listened for half a second: half the graph. So this container has
the daemon running from the start, and whoever asks first

    docker exec -it turtle ros2 topic list

gets all of it. In the turtle's first seconds the daemon is still hearing the
nodes arrive; the `ros2` wrapper waits those out.

It is ros2cli's own daemon, served by ros2cli's own function. It is started
here and not by `ros2 daemon start` for one reason: that one leaves after
two hours without a question, and the next question would be a first one
again.

(Not called daemon.py: Python looks beside a script first, and whatever
imports a module of that name would find this file.)
"""

import os
import signal
import sys

# Longer than a container lives.
NEVER_S = 100 * 365 * 24 * 60 * 60


def main() -> int:
    # A stop ends it on the spot, at every moment: while Python still
    # imports ROS as well as later. It holds nothing worth a goodbye.
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: os._exit(0))

    from ros2cli.daemon import make_xmlrpc_server, serve

    try:
        server = make_xmlrpc_server()
    except OSError:
        return 0  # its port is taken: a daemon is there already
    with server:
        serve(server, timeout=NEVER_S)
    return 0


if __name__ == "__main__":
    sys.exit(main())
