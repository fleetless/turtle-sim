# SPDX-License-Identifier: Apache-2.0
# The image behind `./run-tests.sh lint` and `./run-tests.sh unit`: plain
# Python, no ROS. numpy and Python match what Ubuntu 24.04 gives the real
# image; everything is pinned, so a red run is ours.
FROM python:3.12-slim
RUN pip install --no-cache-dir --disable-pip-version-check \
      numpy==1.26.4 \
      opencv-python-headless==4.10.0.84 \
      pyyaml==6.0.2 \
      pytest==8.3.4 \
      ruff==0.8.6 \
      shellcheck-py==0.10.0.1 \
      actionlint-py==1.7.7.23
