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
            return temps
        time.sleep(0.2)
    # failed to get good reading
    log_event('cant read temperatures')
    return temps

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

last_boiler_off = 0
last_boiler_on = 0
boiler_mode = 'none'
def get_boiler_mode():
    return boiler_mode

def set_boiler_mode(md, remove=True):
    global boiler_mode, last_boiler_on, last_boiler_off

    if remove:
        remove_action({'what':'set_boiler_mode', 'mode':'any'})
    if md == 'heating':
        pi.write(boiler_call[0], 0)
        last_boiler_on = gv.now
    else:
        pi.write(boiler_call[0], 1)
        last_boiler_off = gv.now
    boiler_mode = md
    log_event('set_boiler_mode: ' + md)

heatpump_setpoint_h = (118-32)/1.8
heatpump_setpoint_c = (55-32)/1.8
last_heatpump_off = 0
last_heatpump_on = 0
heatpump_mode = 'none'
def get_heatpump_mode():
    return heatpump_mode

def set_heatpump_mode(md, remove=True):
    """Turn on dry3,4 for cooling; dry 2,4 for heating"""
    global heatpump_mode, last_heatpump_off, last_heatpump_on

    if remove:
        remove_action({'what':'set_heatpump_mode', 'mode':'any'})
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

def set_heatpump_pump_mode(md, remove=True):
    if remove:
        remove_action({'what':'set_heatpump_pump_mode', 'mode':'any'})
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

def remove_action(action):
    """ Remove any future action that corresponds to action"""

    for i, a in enumerate(actions[:]):
        a = a['action']
        if a['what'] != action['what']:
            continue
        if a['what'] in ['set_heatpump_mode', 'set_heatpump_pump_mode', 'set_boiler_mode']:
            if action['mode'] != 'any' and a['mode'] != action['mode']:
                continue
            del actions[i]
        elif action['what'] == 'set_valve_change':
            del actions[i]
        else:
            log_event('remove_action: no understood action: ' + action['what'])

def process_actions():
    for i, a in enumerate(actions[:]):
        if a['time'] > gv.now: # actions are sorted in time
            break
        else:
            action = a['action']
            try:
                if action['what'] == 'set_heatpump_mode':
                    set_heatpump_mode(action['mode'], False)
                elif action['what'] == 'set_heatpump_pump_mode':
                    set_heatpump_pump_mode(action['mode'], False)
                elif action['what'] == 'set_boiler_mode':
                    set_boiler_mode(action['mode'], False)
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
    
def timing_loop():
    gv.nowt = time.localtime()
    gv.now = timegm(gv.nowt)
    log_event('enter timing loop')
    zc = 1
    iter = 0
    supply_temp_readings = []
    return_temp_readings = []
    last_mode = gv.sd['mode']

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
        if iter == 600:
            iter = 0
            log_event('supply temp: ' + str(ave_supply_temp) + 'C ' + str(ave_supply_temp*1.8+32) + 'F' + '; ' + \
                      'return temp: ' + str(ave_return_temp) + 'C ' + str(ave_return_temp*1.8+32) + 'F')
        else:
            iter += 1

        if zc != last_zc: # change in zone call
            if last_zc == 1: # was off, now on?
                supply_temp_readings = []
                return_temp_readings = []
                log_event('zone call on; enable circ pump')
                pi.write(circ_pump[0], 0)
                # for cooling or boiler operation start with only return water.  For heating, only buffer tank water
                remove_action({'what':'set_valve_change'})
                if gv.sd['mode'] in ['Heatpump Cooling' 'Boiler Only']:
                    insert_action(gv.now, {'what':'valve_change', 'valve_change_percent':-100})
                else:
                    insert_action(gv.now, {'what':'valve_change', 'valve_change_percent':100})

                if gv.sd['mode'] in ['Boiler Only', 'Boiler and Heatpump']:
                    log_event('zone call on; enable boiler')
                    set_boiler_mode('heating')
            else: # was on, now off
                log_event('zone call off; disable circ pump')
                pi.write(circ_pump[0], 1)
                if boiler_md == 'heating' and \
                        gv.sd['mode'] in ['Boiler Only', 'Boiler and Heatpump']:
                    log_event('zone call off; disable boiler')
                    set_boiler_mode('none')
                if heatpump_md == 'heating' and \
                        gv.sd['mode'] in ['Boiler and Heatpump', 'Heatpump then Boiler', 'Heatpump Only']:
                    log_event('zone call off; disable heatpump')
                    set_heatpump_mode('none')
                if heatpump_md == 'cooling' and gv.sd['mode'] == 'Heatpump Cooling':
                    log_event('zone call off; disable heatpump')
                    set_heatpump_mode('none')
        elif zc == 0: # still on?
            if len(supply_temp_readings) < 5 or len(return_temp_readings) < 5:
                continue
            if gv.sd['mode'] in ['Heatpump Only', 'Boiler and Heatpump', 'Heatpump then Boiler']:
                 if ave_supply_temp < heatpump_setpoint_h-4:
                     if heatpump_md == 'none' and gv.now-last_heatpump_off > 3*60:
                         set_heatpump_mode('heating')
                 if ave_supply_temp > heatpump_setpoint_h-1.5:
                     if heatpump_md == 'heating' and gv.now-last_heatpump_on > 3*60:
                         set_heatpump_mode('none')
            if gv.sd['mode'] == 'Heatpump then Boiler':
                if ave_supply_temp < heatpump_setpoint_h-5 or ave_return_temp < 33:
                    if boiler_md == 'none' and gv.now-last_boiler_off > 2*60 and \
                             gv.now-last_heatpump_on > 3*60:
                        log_event('reenable boiler')
                        # Use only boiler until things shut off
                        remove_action({'what':'set_valve_change'})
                        insert_action(gv.now, {'what':'valve_change', 'valve_change_percent':-100})
                        set_heatpump_mode('none')
                        set_boiler_mode('heating')
#                        insert_action(gv.now+45*60, {'what':'set_boiler_mode', 'mode':'none'})
            if gv.sd['mode'] == 'Heatpump Cooling':
                 # todo dewpoint, valve, hp control, bad temp readings
                 if ave_supply_temp < dewpoint:
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
