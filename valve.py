# !/usr/bin/env python
# -*- coding: utf-8 -*-

import time
from calendar import timegm
import pigpio
import json
import ast
import i18n
import thread
import logging
import gv
from helpers import *
import web
import os
import glob

pi = pigpio.pi()

close_ret = [24, 1] # phys 18
open_ret = [25, 1] # phys 22
out_pins = [close_ret, open_ret]

nowt = time.localtime()
now = timegm(nowt)
for pin_e in out_pins:
    pin = pin_e[0]
    pi.set_mode(pin, pigpio.OUTPUT)
    pi.write(pin, pin_e[1])


amount = int(sys.argv[1])
print 'amount: ', amount
amount = min(amount, 100)
amount = max(amount, -100)

while True:
    if amount == 0: # stop valve movement
        print 'stop valve'
        pi.write(close_ret[0], 1)
        pi.write(open_ret[0], 1)
        break
    elif amount < 0: # more return, less buffer tank
        # assume 100 seconds to fully move valve, so each amount request is actually a second
        print 'more return, less buffer tank'
        pi.write(close_ret[0], 1)
        pi.write(open_ret[0], 0)
        print 'sleep, amount: ', -amount
        time.sleep(-amount)
        amount = 0
    else: # less return, more buffer tank
        print 'more buffer tank, less return'
        pi.write(open_ret[0], 1)
        pi.write(close_ret[0], 0)
        print 'sleep, amount: ', amount
        time.sleep(amount)
        amount = 0
