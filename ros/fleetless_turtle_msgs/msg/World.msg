# SPDX-License-Identifier: Apache-2.0
# The kinds of all cells. Latched, and published again when a cell changes.
std_msgs/Header header      # frame_id "map"
uint32 width                # 256
uint32 height               # 256
float32 resolution          # 0.1
uint8 FREE=0
uint8 WALL=1
uint8 ROCK=2
uint8 WATER=3
uint8 PAD=4
uint8[] kinds               # row-major from cell (0, 0)
