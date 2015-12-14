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

urls = [
#    '/',  'webpages.home',
    '/',  'webpages.view_options',
    '/vo', 'webpages.view_options',
    '/co', 'webpages.change_options',
    '/vl', 'webpages.view_log',
    '/cl', 'webpages.clear_log',
    '/wl', 'webpages.boiler_log',
    '/login',  'webpages.login',
    '/logout',  'webpages.logout',
]

pi = pigpio.pi()

# see http://projects.privateeyepi.com/home/temperature-sensor-project-using-ds18b20 for temp sensor reading
# gpio pin 23 is data point.  (See /boot/config.txt)
os.system('modprobe w1-gpio')
os.system('modprobe w1-therm')
base_dir = '/sys/bus/w1/devices/'
device_folders = glob.glob(base_dir + '28*')
device_files = []
for df in device_folders:
    device_files.append(df + '/w1_slave')

def read_temps_raw():
    lines = []
    try:
        for device_file in device_files:
            f = open(device_file, 'r')
            flines = f.readlines()
            f.close()
            lines.append(flines)
    except:
        for i in range(len(device_files)):
            lines.append([])
    return lines

def read_temps():
    temps = [-1000] * len(device_files)
    for i in range(5):
        lines = read_temps_raw()
        found_bad = False
        pos = 0
        for flines in lines:
            if temps[pos] != -1000:
                continue
            try:
                if flines[0].strip()[-3:] == 'YES':
                    equals_pos = flines[1].find('t=')
                    if equals_pos != -1:
                        temp_string = flines[1][equals_pos+2:len(flines[1])]
                        temp_c = float(temp_string) / 1000.0
                        temps[pos] = temp_c
                    else:
                        found_bad = True
                else:
                    found_bad = True
            except Exception as ex:
                found_bad = True
            pos += 1

        if not found_bad:
#            print 'temps: ', temps
            return temps
        time.sleep(0.2)

    print 'cant read temperatures'
    return temps

def timing_loop():

    iter = 0
    supply_temp_readings = []
    return_temp_readings = []
    while True:
        time.sleep(1)
        gv.nowt = time.localtime()
        gv.now = timegm(gv.nowt)
        temps = read_temps()
        # rather than tracking serial # of thermistors, just assume higher readings are supply
        # and cooler readings are return (if heating) and vice versa if cooling
        if gv.sd['mode'] == 'Heatpump Cooling':
            supply_temp_readings.append(min(temps))
            return_temp_readings.append(max(temps))
        else:
            supply_temp_readings.append(max(temps))
            return_temp_readings.append(min(temps))
        if len(supply_temp_readings) > 5:
            supply_temp_readings.pop(0)
        if len(return_temp_readings) > 5:
            return_temp_readings.pop(0)
        ave_supply_temp = sum(supply_temp_readings)/float(len(supply_temp_readings))
        ave_return_temp = sum(return_temp_readings)/float(len(return_temp_readings))
        if iter == 10:
            iter = 0
            print 'supply temp: ', ave_supply_temp, 'C ', ave_supply_temp*1.8+32, 'F; ', \
                  'return temp: ', ave_return_temp, 'C ', ave_return_temp*1.8+32, 'F'
        else:
            iter += 1

timing_loop()
