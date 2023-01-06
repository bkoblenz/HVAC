# !/usr/bin/env python
# -*- coding: utf-8 -*-

# see http://electronics.ozonejunkie.com/2014/12/opening-up-the-usr-htw-wifi-temperature-humidity-sensor/    (10.10.100.254 default)
import socket
import math

import i18n
import json
import ast
import time
import thread
import threading
from calendar import timegm
import sys
sys.path.append('./plugins')

import web  # the Web.py module. See webpy.org (Enables the Python SIP web interface)

import gv
import logging
import logging.handlers
from helpers import *
import subprocess
import shutil
from urls import urls  # Provides access to URLs for UI pages
from gpio_pins import set_output
import i2c
from i2c import i2c_read, i2c_write

import urllib
try:
    from urllib2 import urlopen
except:
    from urllib.request import urlopen


b = 17.67 # see wikipedia
c = 243.5
def gamma(t,rh):
    return (b*t / (c+t)) + math.log(rh/100.0)

high_dewpoint = 0
def dewpoint(t,rh):
    global high_dewpoint

    g = gamma(t,rh)
    dp = c*g / (b-g)
    if dp > gv.sd['max_dewpoint']:
        if high_dewpoint % 100 == 0:
            log_event('DEWPOINT TOO HIGH ' + str(dp) + ' using: ' + str(gv.sd['max_dewpoint']))
        high_dewpoint += 1
        return gv.sd['max_dewpoint']
    else:
        high_dewpoint = 0
        return dp

def explore_sockets(ip):
    for i in range(10, 0xffff):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            s.connect((ip, i))
            print 'SUCCESS connect ', ip, i
            s.close()
            time.sleep(3)
        except:
            if i % 500 == 0:
                print 'failed connect ', ip, i
            pass

USR_TCP_PORT = 8899

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
def connect_socket():
    global s

    s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(30)
    for i in range(6):
        try:
            s.connect((gv.sd['USR_ip'], USR_TCP_PORT))
            break
        except:
#            log_event('Retry connect...')
            time.sleep(10)

connect_socket()

PACK_LEN = 11
bytes_data = [0] * PACK_LEN
def get_temp_hum():
    try:
        str_data = s.recv(PACK_LEN)
        hex_data = str_data.encode('hex')
        if len(str_data) == 0:  # disconnected by remote.  Will never return data in the future
            raise ValueError, 'No Data'

        for n in range(0,PACK_LEN): #convert to array of bytes
            lower = 2*n
            upper = lower + 2
            bytes_data[n] = int(hex_data[lower:upper],16)

        humid =  (((bytes_data[6])<<8)+(bytes_data[7]))/10.0
        temp =  (((((bytes_data[8])&0x7F)<<8)+(bytes_data[9]))/10.0)
    
        if int(bytes_data[8]) & 0x80: #invert temp if sign bit is set
            temp = -1.0* temp
    
#        checksum = (uint(sum(bytes_data[0:10])) & 0xFF)+1
        checksum = 0
        for i in range(PACK_LEN-1):
            checksum += bytes_data[i]

        checksum &= 0xFF
        checksum += 1
 
        if checksum == bytes_data[10]:
            return (temp, humid)
        raise ValueError,'Invalid Checksum'

    except:
        connect_socket() # reestablish connection
        raise

dew = gv.sd['max_dewpoint']
failed_dewpoint_read = 0
def dewpoint_loop():
    global dew, failed_dewpoint_read

    log_event('enter dewpoint loop')

    while True:
        try:
            time.sleep(60)
            if not gv.sd['USR_ip']:
                continue
            (dew_temp, dew_hum) = get_temp_hum()
            print 'dew_temp: ', dew_temp, ' dew_hum: ', dew_hum
            failed_dewpoint_read = 0
            dew = dewpoint(dew_temp, dew_hum)
            print 'dew: ', dew
        except Exception as ex:
            try:
                if failed_dewpoint_read > 5: # a few failures before panicing
                    dew = gv.sd['max_dewpoint']
                failed_dewpoint_read += 1
                if failed_dewpoint_read < 10:
                    if failed_dewpoint_read % 10 == 2: # first exception should get cleared by reconnect and is normal
                        log_event('cant read dewpoint.  Exception: ' + str(ex) + ' Failcount: ' + str(failed_dewpoint_read))
                elif failed_dewpoint_read == 10:
                    log_event('DEWPOINT SENSOR FAILURE')
                    gv.plugin_data['te']['tesender'].try_mail('Heating', 'DEWPOINT SENSOR FAILURE')
                elif failed_dewpoint_read % 10 == 0:
                    log_event('Ongoing dewpoint failure.  Failcount: ' + str(failed_dewpoint_read))
            except Exception as ex1:
                log_event('dewpoint sensor email send failed Unexpected exception: ' + str(ex1))

# vsb outputs
boiler_call = 0
circ_pump = 1
open_ret = 2
close_ret = 3
dry1 = 4
dry2 = 5
dry3 = 6
dry4 = 7

last_boiler_off = 0
last_boiler_on = 0
boiler_mode = 'none'
last_wakeup = int(time.time())
sustained_cold = last_wakeup
# If return from boiler should flow through buffer tank set following to true.  When large delta T, this seems worse
# because we do not get heatpump heating in background.  On the other hand, with low delta T, then this gives heatpump
# time to rest.
buffer_tank_isolated = True # default to we just ran boiler and do not want to fill buffer tank with hot water since heatpump might high pressure fault

def get_boiler_mode():
    return boiler_mode

def set_boiler_mode(md, remove=True):
    global boiler_mode, last_boiler_on, last_boiler_off, sustained_cold

    if remove:
        remove_action({'what':'set_boiler_mode', 'mode':'any'})
    if md == 'heating':
        gv.srvals[boiler_call] = 1
        set_output()
        last_boiler_on = gv.now
#        gv.logger.info('set_boiler_mode ' + md + ' last_boiler_on: ' + str(last_boiler_on))
    else:
        gv.srvals[boiler_call] = 0
        set_output()
        # leave buffer_tank_isolated supply temperature comes down to avoid faulting heatpump
        #if gv.sd['mode'] not in ['Boiler Only']: # put valve back to all buffer tank
            #gv.logger.info('set_boiler_mode not going to all buffer tank')
            #remove_action({'what':'set_valve_change'})
            #insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':100})
        last_boiler_off = gv.now
        sustained_cold = last_wakeup
#        gv.logger.info('set_boiler_mode ' + md + ' last_boiler_off: ' + str(last_boiler_off))
    boiler_mode = md
    log_event('set_boiler_mode: ' + md)

heatpump_setpoint_h = (122-32)/1.8
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
#        gv.srvals[dry4] = 1
#        set_output()
        time.sleep(.1) # make sure 4's state is set first
        gv.srvals[dry1] = 0
        gv.srvals[dry2] = 0
#        gv.srvals[dry3] = 1
        set_output()
        heatpump_mode = 'cooling'
        set_heatpump_pump_mode('on')
        last_heatpump_on = gv.now
    elif md == 'heating':
#        gv.srvals[dry4] = 1
#        set_output()
        time.sleep(.1) # make sure 4's state is set first
        gv.srvals[dry1] = 0
#        gv.srvals[dry2] = 1
        gv.srvals[dry3] = 0
        set_output()
        heatpump_mode = 'heating'
        set_heatpump_pump_mode('on')
        last_heatpump_on = gv.now
    else:
        gv.srvals[dry4] = 0
        set_output()
        time.sleep(.1) # make sure 4's state is set first
        gv.srvals[dry1] = 0
        gv.srvals[dry2] = 0
        gv.srvals[dry3] = 0
        set_output()
        heatpump_mode = 'none'
        insert_action(gv.now+2*60, {'what':'set_heatpump_pump_mode', 'mode':'off'})
        last_heatpump_off = gv.now
#    log_event('set_heatpump_mode: ' + md)

def set_heatpump_pump_mode(md, remove=True):
    if remove:
        remove_action({'what':'set_heatpump_pump_mode', 'mode':'any'})
    if md == 'on':
        pass # todo make this work
    else:
        pass # todo make this work
#    log_event('set_heatpump_pump_mode: ' + md)

actions = []
action_lock = threading.RLock()
def action_loop():
    while True:
        gv.nowt = time.localtime()   # Current time as time struct.  Updated once per second.
        gv.now = timegm(gv.nowt)   # Current time as timestamp based on local time from the Pi. Updated once per second.
        process_actions()
        time.sleep(2)

def insert_action(when, action):
    with action_lock:
        position = 0
        for a in actions:
            if a['time'] <= when:
                position += 1
                continue
            break
        actions.insert(position, {'time':when, 'action':action})

def remove_action(action):
    """ Remove any future action that corresponds to action"""

    with action_lock:
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

logged_internal_error = False
def process_actions():
    with action_lock:
        process_actions_work()

def process_actions_work():
    global logged_internal_error, buffer_tank_isolated

    if len(actions) > 10 and not logged_internal_error:
        try:
            gv.plugin_data['te']['tesender'].try_mail('Internal error', 'Action list likely too long len: ' + str(len(actions)))
        except:
            log_event('action list email send failed')
        log_event('Action list likely too long len: ' + str(len(actions)))
        for a in actions:
            log_event('action time: ' + str(a['time']) + ' what: ' + a['action']['what'])
        logged_internal_error = True

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
                elif action['what'] == 'set_valve_change':
                    amount = action['valve_change_percent']
                    amount = min(amount, 100)
                    amount = max(amount, -100)
                    if amount == 0: # stop valve movement
#                        log_event('stop valve')
                        gv.srvals[close_ret] = 0
                        gv.srvals[open_ret] = 0
                    elif amount < 0: # more return, less buffer tank
                        # assume 100 seconds to fully move valve, so each amount request is actually a second
                        insert_action(gv.now-int(amount), {'what':'set_valve_change', 'valve_change_percent':0})
                        gv.srvals[close_ret] = 0
                        gv.srvals[open_ret] = 1
                        log_event('more return water: ' + str(-int(amount)) + '%')
                        #gv.logger.info('setting buffer_tank_isolated')
                        buffer_tank_isolated = amount == -100
                    else: # less return, more buffer tank
                        insert_action(gv.now+int(amount), {'what':'set_valve_change', 'valve_change_percent':0})
                        gv.srvals[close_ret] = 1
                        gv.srvals[open_ret] = 0
                        log_event('more buffer tank water: ' + str(int(amount)) + '%')
                        #gv.logger.info('clearing buffer_tank_isolated')
                        buffer_tank_isolated = False
                    set_output()
            except Exception as ex:
                log_event('Unexpected action: ' + action['what'] + ' ex: ' + str(ex))
            del actions[i]
    
max_cooling_adjustments = 150
min_cooling_adjustments = -max_cooling_adjustments
cooling_adjust_per_degree = 2.5

thermostat_fails = {}
def read_sensor_value(name, logit=False):
    if 'ld' not in gv.plugin_data:
        return None
    for s in gv.plugin_data['ld']:
        if s['name'] == name:
            return s['last_read_value']

    zc = None
    if name == 'zone_call_thermostats':
        max_gap = 0
        fails = 0
        for i, d in enumerate(gv.sd['thermostats']):
            ip = d['ip']
            cmd = 'http://' + ip + '/tstat'
            try:
                data = json.loads(urlopen(cmd, timeout=5).read().decode('utf-8'))
                thermostat_fails[ip] = 0
                if logit:
                    gv.logger.info('tstat ' + ip + ': ' + str(data))
                # try to make thermostat match target config
                d['actual'] = data['temp']
                if 'name' not in d:
                    curl_cmd = ['/usr/bin/curl', 'http://'+ip+'/sys/name']
                    try:
                        upd_data = subprocess.check_output(curl_cmd, universal_newlines=True)
                        d['name'] = json.loads(upd_data)['name']
                    except Exception as ex:
                        upd_data = str(ex)
                    gv.logger.warning('missing name: ' + str(d) + ' Update: ' + upd_data)
                if data['tmode'] != d['mode']:
                    curl_cmd = ['/usr/bin/curl',
                                '-d', json.dumps({'tmode':d['mode']}), cmd]
                    try:
                        upd_data = subprocess.check_output(curl_cmd, universal_newlines=True)
                    except Exception as ex:
                        upd_data = str(ex)
                    gv.logger.warning('mode mismatch: ' + str(d) + ' got: ' + str(data) + ' Update: ' + upd_data)
                if d['mode'] == 1 and data['t_heat'] != d['temp']:
                    curl_cmd = ['/usr/bin/curl',
                                '-d', json.dumps({'tmode':d['mode'], 't_heat':d['temp'], 'hold':1}), cmd]
                    try:
                        upd_data = subprocess.check_output(curl_cmd, universal_newlines=True)
                    except Exception as ex:
                        upd_data = str(ex)
                    gv.logger.warning('temp(heat) mismatch: ' + str(d) + ' got: ' + str(data) + ' Update: ' + upd_data)
                elif d['mode'] == 2 and data['t_cool'] != d['temp']:
                    curl_cmd = ['/usr/bin/curl',
                                '-d', json.dumps({'tmode':d['mode'], 't_cool':d['temp'], 'hold':1}), cmd]
                    try:
                        upd_data = subprocess.check_output(curl_cmd, universal_newlines=True)
                    except Exception as ex:
                        upd_data = str(ex)
                    gv.logger.warning('temp(heat) mismatch: ' + str(d) + ' got: ' + str(data) + ' Update: ' + upd_data)
                if gv.sd['mode'] in ['Heatpump Cooling']:
                    if data['tmode'] in [2,3] and data['temp'] > data['t_cool']:
                        #zc = 1
                        zc = max(zc, min(1, int(data['tstate']))) # use thermostats notion of call fr cool (data['tstate'] == 2 for cooling)
                        local_gap = data['temp']-data['t_cool'] # degrees F
                        max_gap = max(max_gap, local_gap)
                    elif zc == None:
                        zc = 0
                elif gv.sd['mode'] in ['Boiler Only', 'Heatpump Only', 'Heatpump then Boiler']:
                    if data['tmode'] in [1,3] and data['temp'] < data['t_heat']: 
                        #zc = 1
                        zc = max(zc, int(data['tstate'])) # use thermostats notion of call fr heat (data['tstate'] == 1 for heating)
                        local_gap = data['t_heat']-data['temp'] # degrees F
                        max_gap = max(max_gap, local_gap)
                        gv.logger.info('ip: ' + ip + ' temp: ' + str(data['temp']) + ' target: ' + str(data['t_heat']) + ' gap: ' + str(max_gap) + ' localgap: ' + str(local_gap))
                    elif zc == None:
                        zc = 0
            except Exception as ex:
                gv.logger.warning('thermostat: ' + ip + ' exception: ' + str(ex))
                try:
                    if thermostat_fails[ip] > 5:
                        log_event('Thermostat failure ' + ip + ' ex: ' + str(ex))
                        thermostat_fails[ip] = 0
                        try:
                            gv.plugin_data['te']['tesender'].try_mail('Thermostat', 'Thermostat failure')
                        except:
                            pass
                except:
                    thermostat_fails[ip] = 0
                thermostat_fails[ip] += 1
                fails += 1

        if logit:
            gv.logger.info(name + ' zc: ' + str(zc) + ' max_gap: ' + str(max_gap))
        if zc == None:
            gv.logger.warning('read_sensor_value failed to get thermostat data from ANY thermostat: ' + str(gv.sd['thermostats']))
        return zc, max_gap, fails
    return zc

def read_temps():
    temps = []
    for t in ['supply_temp', 'return_temp']:
        ts = read_sensor_value(t)
        if ts == None:
            raise IOError, 'Cant read temperatures'
        else:
            temps.append(ts)
    return temps

def timing_loop():
    """ ***** Main timing algorithm. Runs in a separate thread.***** """
    global sustained_cold, last_wakeup

    last_min = 0
    supply_temp_readings = []
    return_temp_readings = []
#    last_mode = gv.sd['mode']
    last_mode = 'Invalid Mode' # force intialization
    last_temp_log = 0
    failed_temp_read = 0
    failed_cold_supply = 0
    last_dewpoint_adjust = 0

    # Log the image and all the vsb board fw
    try:
        with open('data/version', 'r') as f:
            image_version = f.read()
        gv.logger.info('Image version: ' + image_version)
    except:
        pass
    boards = i2c.get_vsb_boards()
    for board, version in boards.items():
        gv.logger.info('VSB Firmware for board: ' + str(board) + ' value: ' + hex(version))

    for delay in range(15):
        time.sleep(1) # wait for ip addressing to settle but keep updating time
        #gv.nowt = time.localtime()   # Current time as time struct.  Updated once per second.
        #gv.now = timegm(gv.nowt)   # Current time as timestamp based on local time from the Pi. Updated once per second.

    start_time = gv.now
    check_and_update_upnp()
    last_upnp_refresh = gv.now

    # one_way_cooling_adjustments tracks the direction of the last valve change and how many changes we have made
    # in that direction without going the other direction.  This stops us from constantly moving the valve when the
    # heatpump is off or we cannot achieve our target.
    one_way_cooling_adjustments = 0
    last_ave_supply_temp = None

    # force everything off

    set_output()
    zct = 0
    zc = 0
    last_zc = 0
    sleep_time = 15
    last_wakeup = int(time.time())
    sustained_cold = last_wakeup
    low_supply_count = 0

    while True:  # infinite loop
      try:
        time.sleep(max(0, sleep_time-(int(time.time())-last_wakeup)))
#        gv.logger.info('wake cold: ' + str(sustained_cold))
        last_wakeup = int(time.time())
        # perform once per minute processing
        if gv.now // 60 != last_min:  # only check programs once a minute
            gv.logger.info('timing_loop last_zc: ' + str(last_zc) + ' coldgap: ' + str(last_wakeup-sustained_cold))
            boiler_supply_crossover_c = gv.sd['boiler_supply_temp'] if gv.sd['tu'] == 'C' else (gv.sd['boiler_supply_temp']-32)/1.8
            last_min = gv.now // 60
            zct, max_gap, fails = read_sensor_value('zone_call_thermostats', last_min % 5 == 0)
            if zct or (fails > 0 and last_zc): # keep on with what we had if we could not access some thermostats
                if fails == 0 and max_gap <= gv.sd['cold_gap_temp']:
                    sustained_cold = last_wakeup # reset as we are close enough
                if not zct:
                    gv.logger.info('missed zct fails: ' + str(fails) + ' last_zc: ' + str(last_zc) + ' forcing zct on')
                    zct = 1 
                #gv.logger.info('max_gap: ' + str(max_gap) + ' for: ' + str(last_wakeup-sustained_cold) + ' seconds')
                tzc = read_sensor_value('zone_call')
                if not tzc: # small gap (.5F) may lead to no call for heat from thermostat (so zone pump will not run), so ignore implied call for heat
                    if True:
                        gv.logger.warning('Zone_call_thermostat set and does not match zone_call sensor...using ON gap: ' + "{0:.2f}".format(max_gap))
                    #if False:
                    #    gv.logger.warning('Zone_call_thermostat set and does not match zone_call sensor...NOT using zone_call_thermostat gap: ' + "{0:.2f}".format(max_gap))
                    #    zct = False # let sustained cold persist?
                    #else:
                    #    gv.logger.error('Zone_call_thermostat set and does not match zone_call sensor...using zone_call_thermostat gap: ' + "{0:.2f}".format(max_gap))
            elif fails == 0:
                sustained_cold = last_wakeup
            max_bd = -1
            boards = i2c.get_vsb_boards()
            for bd, version in boards.items():
                if bd not in gv.in_bootloader:
                    try:
                        max_bd = max(max_bd, bd)
                        v = i2c_read(i2c.ADDRESS+bd, 0xc) # verify scratch value is as expected.  Acts as a touch of the vsb too!
                        if v != bd+16:
                            gv.logger.critical('Main bad scratch value on board: ' + str(bd) + ' value: ' + str(v))
                            i2c_write(i2c.ADDRESS+bd, 0xc, bd+16) # write scratch register as keepalive
                    except:
                        gv.logger.critical('Cant access scratch register on board: ' + str(bd))
                        pass

                    # read deadman debug register ignoring all errors.
                    try:
                        v = i2c_read(i2c.ADDRESS+bd, 0xd)
                        if v != 0:
                             gv.logger.critical('Deadman register triggered on board: ' + str(bd) + ' value: ' + str(v))
                    except:
                        pass

            cur_bd = (gv.sd['nst']-gv.sd['radiost'])//8
            if max_bd+1 > cur_bd: # ensure at nbrd captures all attached VSMs
                gv.logger.info('Changing nbrd based on attached boards: ' + str(max_bd+1))
                adjust_gv_nbrd(max_bd+1)
                gv.sd['nst'] += 8*(max_bd+1-cur_bd)
                jsave(gv.sd, 'sd')

            cur_ip = get_ip()
            ext_ip_addr = get_external_ip()
            if gv.sd['master'] and (ext_ip_addr != gv.external_ip or cur_ip != gv.last_ip):
                gv.external_ip = ext_ip_addr
                try:
                    # send email if ip addressing changed
                    if gv.sd['teipchange'] and gv.plugin_data['te']['tesender']:
                        subject = "Report from Irricloud"
                        body = 'IP change.  Local IP: ' + cur_ip
                        if gv.sd['htp'] != 0 and gv.sd['htp'] != 80:
                            body += ':' + str(gv.sd['htp'])
                        body += ' External IP: ' + ext_ip_addr
                        if gv.sd['external_htp'] != 0:
                            body += ':' + str(gv.sd['external_htp'])
                        gv.plugin_data['te']['tesender'].try_mail(subject, body)
                except:
                    log_event('ip change email send failed')

            if cur_ip != gv.last_ip:
                gv.logger.info('IP changed from: ' + gv.last_ip + ' to: ' + cur_ip)
                gv.last_ip = cur_ip
                if cur_ip != "No IP Settings":
                    # find router, set up upnp port mapping
                    check_and_update_upnp(cur_ip)
                    last_upnp_refresh = gv.now

            if gv.sd['upnp_refresh_rate'] > 0 and \
                   (gv.now-last_upnp_refresh)//60 >= gv.sd['upnp_refresh_rate']:
                check_and_update_upnp(cur_ip)
                last_upnp_refresh = gv.now

        last_zc = zc
        zc = read_sensor_value('zone_call')
        #gv.logger.info('last_zc: ' + str(last_zc) + ' zc: ' + str(zc) + ' zct: ' + str(zct))
        if zct and not zc:
            gv.logger.info('timing_loop ignoring physical zone_call')
            zc = zct
        elif zct != None and not zct and zc:
            zc = zct
        if zc == None:
            zc = last_zc
            log_event('Failed to read zone_call')
        try:
            with open('ZONE_CALL', 'r') as f:
                gv.logger.info('ZONE_CALL zc: ' + str(zc))
                if not zc:
                    zc = 1
        except:
            pass
        boiler_md = get_boiler_mode()
        heatpump_md = get_heatpump_mode()
        if gv.sd['mode'] != last_mode: # turn everything off
            log_event('change mode from: ' + last_mode + ' to: ' + gv.sd['mode'] + '.  Turn off boiler, heatpump, circ_pump')
            if boiler_md != 'none':
                set_boiler_mode('none')
            if heatpump_md != 'none':
                set_heatpump_mode('none')
            gv.srvals[circ_pump] = 0
            set_output()
            last_zc = 0 # mark as was off
            zc = 0
            sustained_cold = last_wakeup
            last_mode = gv.sd['mode']
            remove_action({'what':'set_valve_change'})
            insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':-100})
            continue

        try:
            temps = read_temps()
            failed_temp_read = 0
            # rather than tracking serial # of thermistors, just assume higher readings are supply
            # and cooler readings are return (if heating) and vice versa if cooling
            min_temp = min(temps)
            max_temp = max(temps)
            if min_temp < 0 or max_temp < 0:
                log_event('Bad min/max temps.  min: ' + str(min_temp) + ' max: ' + str(max_temp))
            if gv.sd['mode'] == 'Heatpump Cooling':
                supply_temp_readings.append(min_temp)
                return_temp_readings.append(max_temp)
            else:
                supply_temp_readings.append(max_temp)
                return_temp_readings.append(min_temp)
        except:
            if gv.now - start_time > 120: # let things start up before capturing errors
                failed_temp_read += 1
                if failed_temp_read < 300:
                    if failed_temp_read % 10 == 1: # first exception should get cleared by reconnect and is normal
                        log_event('cant read temperatures.  Failcount: ' + str(failed_temp_read))
                elif failed_temp_read == 300:
                    log_event('TEMPERATURE SENSOR FAILURE')
                    try:
                        gv.plugin_data['te']['tesender'].try_mail('Heating', 'TEMPERATURE SENSOR FAILURE')
                    except:
                        log_event('temp sensor failure email send failed')
                elif failed_temp_read % 300 == 0:
                    log_event('Ongoing temp failure.  Failcount: ' + str(failed_temp_read))

        if len(supply_temp_readings) > 5:
            supply_temp_readings.pop(0)
        if len(return_temp_readings) > 5:
            return_temp_readings.pop(0)
        try:
            ave_supply_temp = sum(supply_temp_readings)/float(len(supply_temp_readings))
            if ave_supply_temp < 0:
                log_event('Bad ave_supply_temp: ' + str(ave_supply_temp))
        except ZeroDivisionError:
            ave_supply_temp = -1
        try:
            ave_return_temp = sum(return_temp_readings)/float(len(return_temp_readings))
            if ave_return_temp < 0:
                log_event('Bad ave_return_temp: ' + str(ave_return_temp))
        except ZeroDivisionError:
            ave_return_temp = -1

        gv.logger.info('bti: ' + str(buffer_tank_isolated) + ' st: ' + str(ave_supply_temp) + ' rt: ' + str(ave_return_temp) + ' bmd: ' + boiler_md)
        # if we have been ignoring buffer tank and supply temp is now low enough, open buffer tank
        if boiler_md != 'heating' and buffer_tank_isolated and 0 < ave_supply_temp < boiler_supply_crossover_c and gv.sd['mode'] not in  ['Boiler Only', 'Heatpump Cooling']:
            gv.logger.info('reopening buffer tank; supply temp: ' + str(ave_supply_temp)+'C')
            remove_action({'what':'set_valve_change'})
            insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':100}) # will reset buffer_tank_isolated
            sustained_cold = last_wakeup # start countdown to turning boiler back on

        if gv.now - last_temp_log >= 300:
            ast_c = ave_supply_temp
            ast_f = ast_c*1.8 + 32
            last_temp_log = gv.now
            art_c = ave_return_temp
            art_f = art_c*1.8 + 32
            dew_f = dew*1.8 + 32
            log_event('supply temp: ' + "{0:.2f}".format(ast_c) + 'C ' + "{0:.2f}".format(ast_f) + 'F' + '; ' + \
                      'return temp: ' + "{0:.2f}".format(art_c) + 'C ' + "{0:.2f}".format(art_f) + 'F' + '; ' + \
                      'cold: ' + str(last_wakeup-sustained_cold) + '; ' + \
                      'dewpoint: ' + "{0:.2f}".format(dew) + 'C ' + "{0:.2f}".format(dew_f) + 'F')

        #gv.logger.info('last_zc: ' + str(last_zc) + ' zc: ' + str(zc) + ' zct: ' + str(zct))
        if zc != last_zc: # change in zone call
            gv.logger.info('change in zone call; last_zc: ' + str(last_zc) + ' zc: ' + str(zc) + ' zct: ' + str(zct))
            sustained_cold = last_wakeup
            if gv.sd['mode'] == 'None':
                zc = last_zc # dont do anything in terms of moving water
            elif last_zc == 0: # was off, now on?
                supply_temp_readings = []
                return_temp_readings = []
                last_ave_supply_temp = None
                log_event('zone call on; enable circ pump')
                gv.srvals[circ_pump] = 1
                set_output()
                # for cooling or boiler operation start with only return water.  For heating, only buffer tank water
                if gv.sd['mode'] in ['Heatpump Cooling']:
                    remove_action({'what':'set_valve_change'})
                    insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':-100})
                    one_way_cooling_adjustments = 0
                else:
                    remove_action({'what':'set_valve_change'})
                    if gv.sd['mode'] in ['Boiler Only']:
                        insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':-100})
                    elif not buffer_tank_isolated: # only open buffer tank when supply temp is cool enough
                        insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':100})
                    #else:
                    #    gv.logger.info('change in zone call would have opened buffer tank')
 
                if gv.sd['mode'] == 'Boiler Only':
                    log_event('zone call on; enable boiler')
                    set_boiler_mode('heating')
            else: # was on, now off
                msg_start = 'zone call off; '
                gv.srvals[circ_pump] = 0
                set_output()
                if boiler_md == 'heating' and \
                        gv.sd['mode'] in ['Boiler Only', 'Heatpump then Boiler']:
                    msg_start += 'disable boiler; '
                    set_boiler_mode('none')
                if heatpump_md == 'heating' and \
                        gv.sd['mode'] in ['Heatpump then Boiler', 'Heatpump Only']:
                    msg_start += 'disable heatpump; '
                    set_heatpump_mode('none')
                if heatpump_md == 'cooling' and gv.sd['mode'] == 'Heatpump Cooling':
                    msg_start += 'disable heatpump; '
                    set_heatpump_mode('none')
                log_event(msg_start + 'supply: ' + "{0:.2f}".format(ave_supply_temp) + ' return: ' + "{0:.2f}".format(ave_return_temp))
        elif zc == 1: # still on?
            if len(supply_temp_readings) < 5 or len(return_temp_readings) < 5:
                continue
            if gv.sd['mode'] in ['Heatpump Only', 'Heatpump then Boiler']:
                if ave_supply_temp < heatpump_setpoint_h-8:
                    if heatpump_md == 'none' and gv.now-last_heatpump_off > 3*60:
#                        log_event('reenable heatpump; supply: ' + str(ave_supply_temp))
                        set_heatpump_mode('heating')
                if ave_supply_temp > heatpump_setpoint_h-4:
                    if heatpump_md == 'heating' and gv.now-last_heatpump_on > 3*60:
#                        log_event('disable heatpump; supply: ' + str(ave_supply_temp))
                        set_heatpump_mode('none')
            if gv.sd['mode'] in ['Heatpump then Boiler', 'Heatpump Only']:
#                if ave_supply_temp < heatpump_setpoint_h-13 or ave_return_temp < 32:
                switch_to_boiler = False
                if ave_supply_temp < boiler_supply_crossover_c:
                    # Typically takes 300-450 seconds from low point to reach ok, and once starts trending up stays trending uo
                    if low_supply_count % 150 == 0:
                        trend = 'Neutral'
                        if low_supply_count != 0:
                            if last_ave_supply_temp > ave_supply_temp:
                                trend = 'Decreasing'
                            elif last_ave_supply_temp < ave_supply_temp:
                                trend = 'Increasing'
                        last_ave_supply_temp = ave_supply_temp
                        log_event('low_supply: ' + str(low_supply_count) + ' supply: ' + "{0:.2f}".format(ave_supply_temp) + ' return: ' + "{0:.2f}".format(ave_return_temp) + ' trend: ' + trend)
                    low_supply_count += sleep_time
                    if low_supply_count > gv.sd['low_supply_time']*60: # try to hold off boiler if heatpump water getting warmer
                        #switch_to_boiler = trend != 'Increasing' and gv.sd['mode'] == 'Heatpump then Boiler'
                        switch_to_boiler = gv.sd['mode'] == 'Heatpump then Boiler'
                        if switch_to_boiler:
                            log_event('Heatpump hot water supply failure')
                            try:
                                gv.plugin_data['te']['tesender'].try_mail('Heating', 'Heatpump hot water supply failure')
                            except:
                                log_event('hot supply water failure email send failed')
                            low_supply_count = 0 # reset
                        switch_to_boiler = False # just rely on coldgap for now
                elif not buffer_tank_isolated: # only reset once we know we are looking at buffer tank water
                    low_supply_count = 0
                if last_wakeup - sustained_cold > gv.sd['cold_gap_time']*60:
                    switch_to_boiler = gv.sd['mode'] == 'Heatpump then Boiler'
                if switch_to_boiler and boiler_md == 'none' and gv.now-last_boiler_off > 2*60 and gv.now-last_heatpump_on > 3*60:
                    log_event('reenable boiler; supply: ' + "{0:.2f}".format(ave_supply_temp) + ' return: ' + "{0:.2f}".format(ave_return_temp) + ' coldgap: ' + str(last_wakeup-sustained_cold))
                    # Use only boiler for a while
                    remove_action({'what':'set_valve_change'})
                    insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':-100})
                    # try an hour to warm things up and allow defrost mode on hp to finish and rewarm tank
                    last_on_min_gap = (gv.now - last_boiler_on)//60
#                    gv.logger.info('last_on_min_gap: ' + str(last_on_min_gap) + ' now: ' + str(gv.now) + ' on: ' + str(last_boiler_on))
                    extra_min = 0 if last_on_min_gap >= 4*60 else 15 if last_on_min_gap >= 3*60 else 30
                    set_heatpump_mode('none')
                    set_boiler_mode('heating')
                    sustained_cold = last_wakeup
                    # try leaving boiler on until we come to temp
                    #insert_action(gv.now+(extra_min+40)*60, {'what':'set_boiler_mode', 'mode':'none'}) # used to be 59
                    insert_action(gv.now+(extra_min+40)*60, {'what':'set_boiler_mode', 'mode':'none'}) # used to be 59
            if gv.sd['mode'] == 'Heatpump Cooling' and gv.now-last_dewpoint_adjust >= 60:
                 dewpoint_margin = 1.
                 target = max(dew+dewpoint_margin, 6.)
                 adjust = 0
                 if ave_supply_temp >= 19:
                    failed_cold_supply += 1
                    if failed_cold_supply == 300:
                        log_event('COLD SUPPLY WATER FAILURE')
                        try:
                            gv.plugin_data['te']['tesender'].try_mail('Cooling', 'COLD SUPPLY WATER FAILURE')
                        except:
                            log_event('cold supply water failure email send failed')
                    elif failed_cold_supply % 300 == 0:
                        log_event('Ongoing cold supply water failure.  Failcount: ' + str(failed_cold_supply))
                 else:
                    failed_cold_supply = 0
                 if ave_supply_temp <= dew+dewpoint_margin and one_way_cooling_adjustments > min_cooling_adjustments:
                     remove_action({'what':'set_valve_change'})
                     insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':-100})
                     msg = 'Close valve; avoid condensation'
                     log_event(msg + ': ' + "{0:.2f}".format(dew) + 'C target: ' + "{0:.2f}".format(target) + 'C supply: ' + "{0:.2f}".format(ave_supply_temp) + 'C')
                     last_dewpoint_adjust = gv.now
                     last_ave_supply_temp = None
                     one_way_cooling_adjustments = min_cooling_adjustments
                 elif target < ave_supply_temp - .1:
                     remove_action({'what':'set_valve_change'})
                     adjust = 0
                     if one_way_cooling_adjustments < 0:
                         one_way_cooling_adjustments = 0
                         adjust += 2
                     adjust += cooling_adjust_per_degree * (ave_supply_temp - target)
                     if last_ave_supply_temp != None and last_ave_supply_temp - ave_supply_temp > 0 and \
                            gv.now-last_dewpoint_adjust <= 180: # already going down?  Be patient
                         new_adjust = adjust - 2*cooling_adjust_per_degree * (last_ave_supply_temp - ave_supply_temp)
                         msg = 'already going down'
                         gv.logger.debug(msg + ': ' + \
                                         ' adjust: ' + "{0:.2f}".format(adjust) + \
                                         ' new_adjust: ' + "{0:.2f}".format(new_adjust))
                         adjust = max(0, new_adjust)
                     adjust = int(round(adjust))
                     msg = 'Ignoring request for more buffer tank water'
                     if adjust > 0 and one_way_cooling_adjustments < max_cooling_adjustments:
                         insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':adjust})
                         last_dewpoint_adjust = gv.now
                         last_ave_supply_temp = ave_supply_temp
                         msg = 'More buffer tank water'
                 elif target > ave_supply_temp + .1:
                     remove_action({'what':'set_valve_change'})
                     adjust = 0
                     if one_way_cooling_adjustments > 0:
                         one_way_cooling_adjustments = 0
                         adjust -= 2
                     adjust += cooling_adjust_per_degree * (ave_supply_temp - target)
                     if last_ave_supply_temp != None and last_ave_supply_temp - ave_supply_temp < 0 and \
                            gv.now-last_dewpoint_adjust <= 180: # already going up?  Be patient
                         new_adjust = adjust - 2*cooling_adjust_per_degree * (last_ave_supply_temp - ave_supply_temp)
                         msg = 'already going up'
                         gv.logger.debug(msg + ': ' + \
                                         ' adjust: ' + "{0:.2f}".format(adjust) + \
                                         ' new_adjust: ' + "{0:.2f}".format(new_adjust))
                         adjust = min(0, new_adjust)
                     adjust = int(round(adjust))
                     msg = 'Ignoring request for more return water'
                     if adjust < 0 and one_way_cooling_adjustments > min_cooling_adjustments:
                         insert_action(gv.now, {'what':'set_valve_change', 'valve_change_percent':adjust})
                         last_dewpoint_adjust = gv.now
                         last_ave_supply_temp = ave_supply_temp
                         msg = 'More return water'
                 else:
                     msg = 'Not changing valve'
                 gv.logger.debug(msg + ': ' + str(one_way_cooling_adjustments) + \
                                 ' dew: ' + "{0:.2f}".format(dew) + 'C' + \
                                 ' target: ' + "{0:.2f}".format(target) + 'C' + \
                                 ' supply: ' + "{0:.2f}".format(ave_supply_temp) + 'C')
                 if (adjust > 0 and one_way_cooling_adjustments < max_cooling_adjustments) or \
                        (adjust < 0 and one_way_cooling_adjustments > min_cooling_adjustments):
                     one_way_cooling_adjustments += adjust

      except:
          gv.logger.exception('BUG')
        #### End of timing loop ####


class SIPApp(web.application):
    """Allow program to select HTTP port."""

    def run(self, port=gv.sd['htp'], *middleware):  # get port number from options settings
        func = self.wsgifunc(*middleware)
        return web.httpserver.runsimple(func, ('0.0.0.0', port))


app = SIPApp(urls, globals())
#  disableShiftRegisterOutput()
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
    'subprocess': subprocess,
    'ast': ast,
    '_': _,
    'i18n': i18n,
    'app_path': lambda p: web.ctx.homepath + p,
    'web' : web,
}

template_render = web.template.render('templates', globals=template_globals, base='base')

if __name__ == '__main__':

    gv.nowt = time.localtime()   # Current time as time struct.  Updated once per second once timing loop starts.
    gv.now = timegm(gv.nowt)   # Current time as timestamp based on local time from the Pi. Updated once per second.
    mkdir_p('logs')

    log_levels = { 'debug':logging.DEBUG,
        'info':logging.INFO,
        'warning':logging.WARNING,
        'error':logging.ERROR,
         'critical':logging.CRITICAL,
        }
    log_file = 'logs/irricloud.out'
    fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5*gv.MB, backupCount=5)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    gv.logger.addHandler(fh)
    gv.logger.setLevel(logging.INFO) 

    if len(sys.argv) > 1:
        level_name = sys.argv[1]
        if level_name in log_levels:
            level = log_levels[level_name]
            gv.logger.setLevel(level) 
        else:
            gv.logger.critical('Bad parameter to sip: ' + level_name)

    gv.logger.critical('Starting')
    key_files = ['/etc/network/interfaces', '/etc/wpa_supplicant/wpa_supplicant.conf', '/etc/resolv.conf']
    for f in key_files:
        shutil.copy2(f, './logs/'+f[f.rfind('/')+1:])

    #########################################################
    #### Code to import all webpages and plugin webpages ####

    gv.logger.info('pre import plugins')
    import plugins
    gv.logger.info('post import plugins')

    try:
        gv.logger.info(_('plugins loaded:'))
    except Exception:
        pass

    for name in plugins.__all__:
        gv.logger.info(name)

    gv.plugin_menu.sort(key=lambda entry: entry[0])

    # Ensure first three characters ('/' plus two characters of base name of each
    # plugin is unique.  This allows the gv.plugin_data dictionary to be indexed
    # by the two characters in the base name.
    plugin_map = {}
    for p in gv.plugin_menu:
        three_char = p[1][0:3]
        if three_char not in plugin_map:
            plugin_map[three_char] = p[0] + '; ' + p[1]
        else:
            gv.logger.error('ERROR - Plugin Conflict:' + p[0] + '; ' + p[1] + ' and ' + plugin_map[three_char])
            exit()

    #  Keep plugin manager at top of menu
    try:
        gv.plugin_menu.pop(gv.plugin_menu.index(['Manage Plugins', '/plugins']))
    except Exception:
        pass
    
    gv.logger.info('Starting dewpoint thread')
    thread.start_new_thread(dewpoint_loop, ())

    gv.logger.info('Starting action thread')
    thread.start_new_thread(action_loop, ())

    gv.logger.info('Starting main thread')
    thread.start_new_thread(timing_loop, ())

    app.notfound = lambda: web.seeother('/')
    app.run()
