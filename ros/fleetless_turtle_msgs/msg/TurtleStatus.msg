# SPDX-License-Identifier: Apache-2.0
# What the turtle is doing, 10 times a second.
std_msgs/Header header      # frame_id "map"
int32 cell_x                # the turtle's cell, 0..255
int32 cell_y
string square               # "A1" .. "P16"
float32 heading_deg         # 0 = east, counter-clockwise, [0, 360)
float32 speed               # m/s along the heading, negative backwards
float32 turn_rate           # rad/s, counter-clockwise positive
string mode                 # idle | teleop | auto | docking | charging
bool crawling               # battery empty, speed limits reduced
bool on_charger
bool bumper
uint32 collisions           # since start or reset
float64 odometer_m          # distance driven since start or reset
bool trail                  # the trail is being recorded
