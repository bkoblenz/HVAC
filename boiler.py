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
device_folder = glob.glob(base_dir + '28*')[0]
device_file = device_folder + '/w1_slave'

def read_temp_raw():
    f = open(device_file, 'r')
    lines = f.readlines()
    f.close()
    return lines

def read_temp():
    for i in range(5):
        lines = read_temp_raw()
        if lines[0].strip()[-3:] == 'YES':
            equals_pos = lines[1].find('t=')
            if equals_pos != -1:
                temp_string = lines[1][equals_pos+2:len(lines[1])]
                temp_c = float(temp_string) / 1000.0
                return temp_c
        time.sleep(0.2)
    # failed to get good reading
    log_event('cant read temperature from file: ' + device_file)
    return -1000

zone_call = [20, pigpio.PUD_UP] # phys 38
factory_reset = [21, pigpio.PUD_UP] # phys 40  ### RESERVED BY boiler_monitor.py
in_pins = [zone_call]

close_ret = [24, 1] # phys 18
open_ret = [25, 1] # phys 22
dry1 = [5, 1] # phys 29
dry2 = [6, 1] # phys 31
dry3 = [13, 1] # phys 33, default is this controls heatpump
dry4 = [19, 1] # phys 35, default is this controls heatpump
circ_pump = [26, 1] # phys 37
boiler_call = [16, 1] # phys 36
out_pins = [circ_pump, boiler_call, dry1, dry2, dry3, dry4, close_ret, open_ret]

heatpump_setpoint_h = (118-32)/1.8
heatpump_setpoint_c = (55-32)/1.8
last_heatpump_off = 0
last_heatpump_on = 0
last_boiler_off = 0
last_boiler_on = 0

def get_boiler_mode():
    if boiler_call[1] == 0:
         return 'heating'
    return 'none'

def set_boiler_mode(md):
    if md == 'heating':
        pi.write(boiler_call[0], 0)
        last_boiler_on = gv.now
    else:
        pi.write(boiler_call[0], 1)
        last_boiler_off = gv.now
    log_event('set_boiler_mode: ' + md)

heatpump_mode = 'none'

def get_heatpump_mode():
    return heatpump_mode
    if dry3[1] == 0 and dry4[1] == 0:
        return 'cooling'
    if dry2[1] == 0 and dry4[1] == 0:
        return 'heating'
    return 'none'

def set_heatpump_mode(md):
    """Turn on dry3,4 for cooling; dry 2,4 for heating"""
    global heatpump_mode, last_heatpump_off, last_heatpump_on

    if md == 'cooling':
#        pi.write(dry4[0], 0)
        time.sleep(.1) # make sure 4's state is set first
        pi.write(dry1[0], 1)
        pi.write(dry2[0], 1)
#        pi.write(dry3[0], 0)
        heatpump_mode = 'cooling'
        set_heatpump_pump_mode('on')
        last_heatpump_on = gv.now
    elif md == 'heating':
#        pi.write(dry4[0], 0)
        time.sleep(.1) # make sure 4's state is set first
        pi.write(dry1[0], 1)
#        pi.write(dry2[0], 0)
        pi.write(dry3[0], 1)
        heatpump_mode = 'heating'
        set_heatpump_pump_mode('on')
        last_heatpump_on = gv.now
    else:
        pi.write(dry4[0], 1)
        time.sleep(.1) # make sure 4's state is set first
        pi.write(dry1[0], 1)
        pi.write(dry2[0], 1)
        pi.write(dry3[0], 1)
        heatpump_mode = 'none'
        insert_action(gv.now+2*60, {'what':'set_heatpump_pump_mode', 'mode':'off'})
        last_heatpump_off = gv.now
    log_event('set_heatpump_mode: ' + md)

def set_heatpump_pump_mode(md):
    # maybe make sure no future actions to counteract this
    if md == 'on':
        pass # todo make this work
    else:
        pass # todo make this work
    log_event('set_heatpump_pump_mode: ' + md)


actions = []
def insert_action(when, action):
    position = 0
    for a in actions:
        if a['time'] <= when:
            position += 1
            continue
        break
    actions.insert(position, {'time':when, 'action':action})

def process_actions():
    for i, a in enumerate(actions[:]):
        if a['time'] <= gv.now:
            action = a['action']
            try:
                if action['what'] == 'set_heatpump_mode':
                    set_heatpump_mode(action['mode'])
                elif action['what'] == 'set_heatpump_pump_mode':
                    set_heatpump_pump_mode(action['mode'])
                elif action['what'] == 'set_boiler_mode':
                    set_boiler_mode(action['mode'])
                elif action['what'] == 'valve_change':
                    amount = action['valve_change_percent']
                    amount = min(amount, 100)
                    amount = max(amount, -100)
                    if amount == 0: # stop valve movement
                        log_event('stop valve')
                        pi.write(close_ret[0], 1)
                        pi.write(open_ret[0], 1)
                    elif amount < 0: # more return, less buffer tank
                        # assume 100 seconds to fully move valve, so each amount request is actually a second
                        insert_action(gv.now-int(amount), {'what':'valve_change', 'valve_change_percent':0})
                        pi.write(close_ret[0], 1)
                        pi.write(open_ret[0], 0)
                        log_event('more return water: ' + str(-int(amount)) + '%')
                    else: # less return, more buffer tank
                        insert_action(gv.now+int(amount), {'what':'valve_change', 'valve_change_percent':0})
                        pi.write(close_ret[0], 1)
                        pi.write(open_ret[0], 0)
                        log_event('more buffer tank water: ' + str(int(amount)) + '%')
            except Exception as ex:
                log_event('Unexpected action: ' + action['what'] + ' ex: ' + str(ex))
            del actions[i]
        else:
            break
    

def timing_loop():
    gv.nowt = time.localtime()
    gv.now = timegm(gv.nowt)
    log_event('enter timing loop')
    zc = 1
    t = 0
    temp_readings = []
    last_mode = gv.sd['mode']
    # for cooling start with only return water.  For heating, only buffer tank water
    if last_mode == 'Heatpump Cooling':
        insert_action(gv.now, {'what':'valve_change', 'valve_change_percent':-100})
    else:
        insert_action(gv.now, {'what':'valve_change', 'valve_change_percent':100})

    while True:
        time.sleep(1)
        gv.nowt = time.localtime()
        gv.now = timegm(gv.nowt)
        process_actions()
        last_zc = zc
        zc = pi.read(zone_call[0])
        boiler_md = get_boiler_mode()
        heatpump_md = get_heatpump_mode()

        if gv.sd['mode'] != last_mode: # turn everything off
            log_event('change mode.  Turn off boiler, heatpump, circ_pump')
            if boiler_md != 'none':
                set_boiler_mode('none')
            if heatpump_md != 'none':
                set_heatpump_mode('none')
            pi.write(circ_pump[0], 1)
            last_zc = 1 # mark as was off
            last_mode = gv.sd['mode']            

        temp = read_temp()
        temp_readings.append(temp)
        if len(temp_readings) > 5:
            temp_readings.pop(0)
        ave_temp = sum(temp_readings)/float(len(temp_readings))
        if gv.now%180 == 0:
            log_event('ave temp: ' + str(ave_temp) + 'C ' + str(ave_temp*1.8+32) + 'F')

        if zc != last_zc: # change in zone call
            if last_zc == 1: # was off, now on?
                temp_readings = []
                log_event('zone call on; enable circ pump')
                pi.write(circ_pump[0], 0)
                if gv.sd['mode'] == 'Boiler Only' or gv.sd['mode'] == 'Boiler and Heatpump':
                    log_event('zone call on; enable boiler')
                    set_boiler_mode('heating')
            else: # was on, now off
                log_event('zone call off; disable circ pump')
                pi.write(circ_pump[0], 1)
                if gv.sd['mode'] == 'Boiler Only' or gv.sd['mode'] == 'Boiler and Heatpump':
                    log_event('zone call off; disable boiler')
                    set_boiler_mode('none')
                if heatpump_md == 'heating' and \
                        (gv.sd['mode'] == 'Boiler and Heatpump' or \
                         gv.sd['mode'] == 'Heatpump then Boiler' or \
                         gv.sd['mode'] == 'Heatpump Only'):
                    log_event('zone call off; disable heatpump')
                    set_heatpump_mode('none')
                if heatpump_md == 'cooling' and gv.sd['mode'] == 'Heatpump Cooling':
                    log_event('zone call off; disable heatpump')
                    set_heatpump_mode('none') # todo keep hp pump running 2 minutes
        elif zc == 0: # still on?
            if len(temp_readings) < 5:
                continue
            if gv.sd['mode'] == 'Heatpump Only' or gv.sd['mode'] == 'Boiler and Heatpump' or \
                   gv.sd['mode'] == 'Heatpump then Boiler':
                 if ave_temp < heatpump_setpoint_h-4:
                     if heatpump_md == 'none' and gv.now-last_heatpump_off > 3*60:
                         set_heatpump_mode('heating')
                 if ave_temp > heatpump_setpoint_h-1.5:
                     if heatpump_md == 'heating' and gv.now-last_heatpump_on > 3*60:
                         set_heatpump_mode('none')
            if gv.sd['mode'] == 'Heatpump then Boiler':
                 # todo think about turning off heatpump if too cold
                if ave_temp < heatpump_setpoint_h-5:
                    if boiler_md == 'none' and gv.now-last_boiler_off > 2*60 and \
                             gv.now-last_heatpump_on > 3*60:
                        log_event('reenable boiler')
                        set_boiler_mode('heating') # for 45 mins
                        insert_action(gv.now+45*60, {'what':'set_boiler_mode', 'mode':'none'})
            if gv.sd['mode'] == 'Heatpump Cooling':
                 # todo dewpoint, valve, hp control, bad temp readings
                 if ave_temp < dewpoint:
                     insert_action(gv.now, {'what':'valve_change', 'valve_change_percent':-100})


class BoilerApp(web.application):
    """Allow program to select HTTP port."""

    def run(self, port=gv.sd['htp'], *middleware):  # get port number from options settings
        func = self.wsgifunc(*middleware)
        return web.httpserver.runsimple(func, ('0.0.0.0', port))


app = BoilerApp(urls, globals())
web.config.debug = False  # Improves page load speed
if web.config.get('_session') is None:
    web.config._session = web.session.Session(app, web.session.DiskStore('sessions'),
                                              initializer={'user': 'anonymous'})
template_globals = {
    'gv': gv,
    'str': str,
    'eval': eval,
    'session': web.config._session,
    'json': json,
    'ast': ast,
    '_': _,
    'i18n': i18n,
    'app_path': lambda p: web.ctx.homepath + p,
    'web' : web,
}

template_render = web.template.render('templates', globals=template_globals, base='base')
logger = logging.getLogger('boiler')

if __name__ == '__main__':

    log_levels = { 'debug':logging.DEBUG,
        'info':logging.INFO,
        'warning':logging.WARNING,
        'error':logging.ERROR,
        'critical':logging.CRITICAL,
        }

    log_file = 'logs/boiler.out'
    fh = logging.FileHandler(log_file)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(logging.DEBUG) 
    logger.info('Starting...')

    for pin_e in in_pins:
        pin = pin_e[0]
        pi.set_mode(pin, pigpio.INPUT)
        pi.set_pull_up_down(pin, pin_e[1])

    for pin_e in out_pins:
        pin = pin_e[0]
        pi.set_mode(pin, pigpio.OUTPUT)
        pi.write(pin, pin_e[1])

    gv.logger.debug('Starting main thread')
    thread.start_new_thread(timing_loop, ())

    app.notfound = lambda: web.seeother('/')
    app.run()

