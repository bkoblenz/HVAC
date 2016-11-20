# !/usr/bin/env python

import datetime
from random import randint
from threading import Thread, RLock
import sys
import traceback
import shutil
import ast
import json
import time
import io
import re
import os
import math
import urllib
import urllib2
import subprocess
import errno
import pigpio
import text_email
import operator
import i2c
from i2c import i2c_read, i2c_write, i2c_structure_read

from blinker import signal
import web
import gv  # Get access to ospi's settings
from urls import urls  # Get access to ospi's URLs
from ospi import template_render
from webpages import ProtectedPage, process_page_request, load_and_save_remote
from helpers import mkdir_p, jsave, read_log, run_program, schedule_recurring_instances, get_remote_sensor_boards
from array import array

if 'ld' not in gv.plugin_data:
    urls.extend(['/lda',  'plugins.sensors.view_sensors',
                 '/ldmg', 'plugins.sensors.multigraph_sensors',
                 '/ldms', 'plugins.sensors.modify_sensor',
                 '/ldcs', 'plugins.sensors.change_sensor',
                 '/ldcst', 'plugins.sensors.change_sensor_type',
                 '/ldds', 'plugins.sensors.delete_sensor',
                 '/ldes', 'plugins.sensors.enable_sensor',
                 '/ldvl', 'plugins.sensors.view_log_sensor',
                 '/ldcl', 'plugins.sensors.clear_log_sensor',
                 '/ldssl', 'plugins.sensors.sensor_sample_log',
                 '/ldsel', 'plugins.sensors.sensor_event_log',
                 ])
    # Add this plugin to the home page plugins menu
    gv.plugin_menu.append(['Sensors', '/lda'])

def update_ptype(s):
    try:
        now = gv.now
        s['last_read_value'] = None
        s['last_read_state'] = 'success'
        s['last_sample_time'] = now
        s['last_sub_sample_time'] = now
        s['failure_sequence_count'] = 0

        if s['type'] in  ['None', 'Leak Detector', 'Dry Contact', 'Motion']:
            s['sub_samples'] = [None]  # Initialize one sample to average
        elif s['type'] in ['Temperature', 'Moisture']:
            s['sub_samples'] = [None for i in range(10)]  # Initialize a bunch of None for subsamples

        if s['remote_sensor'] == 0 and s['vsb_bd'] >= 0: # vsb sensor
            base = i2c.BASE + 0x30*s['vsb_pos']
            address = i2c.ADDRESS+s['vsb_bd']
            if s['vsb_bd'] not in gv.in_bootloader:
                # write ptype as appropriate sensor type
                i2c_write(address, 2+s['vsb_pos'], 0x0)
                time.sleep(1) # give time for vsb to deallocate resounces
                if s['type'] == 'Leak Detector':
                    i2c_write(address, 2+s['vsb_pos'], 0x1)
                    i2c_write(address, base+3, 1) # make reads of pulse counter destructive
                elif s['type'] == 'Temperature':
                    i2c_write(address, 2+s['vsb_pos'], 0x2)
                elif s['type'] == 'Dry Contact':
                    i2c_write(address, 2+s['vsb_pos'], 0x3)
                elif s['type'] == 'Motion':
                    i2c_write(address, 2+s['vsb_pos'], 0x3)
                elif s['type'] == 'Moisture':
                    i2c_write(address, 2+s['vsb_pos'], 0x5)

                v = -1
                for i in range(10):
                    time.sleep(0.05) # wait 50 ms (up to a total of 500ms) for fw to initialize
                    try:
                        v = i2c_read(address, base)
                        if v != 0:
                            break
                    except:
                        v = -1
                gv.logger.info('update_ptype address: ' + hex(address) + ' offset: ' + str(2+s['vsb_pos']) + ' value: ' + str(v))
            else:
                gv.logger.info('did not update_ptype address: ' + hex(address) + ' offset: ' + str(2+s['vsb_pos']) + ' value: ' + str(v) + ' because in bootloader version: ' + hex(version))
    except:
        pass

def update_sub_samples(s, value):
    s['sub_samples'].append(value)
    s['sub_samples'].pop(0)

# track last value and display on view_sensors page
proto_sens = {'name':'','remote_sensor':0, 'type':'None','enabled':1,'normal_trigger':1,'lge':1,'lgs':1,'te':1,'sample_rate':300, 'last_sample_time':0, 'last_sub_sample_time':0, 'last_read_state':'success', 'last_read_value': None, 'failure_sequence_count':0, 'sub_samples':[None]}
def load_sensors():
    """Load the flow meter data from file."""

    try:
        with open('./data/sensors.json', 'r') as f:
            gv.plugin_data['ld'] = json.load(f)
        # add in any fields missing in data file
        change = False
        for key in proto_sens:
            for s in gv.plugin_data['ld']:
                if key not in s:
                    s[key] = proto_sens[key]
                    change = True
        if change:
            with open('./data/sensors.json', 'w') as f:
                json.dump(gv.plugin_data['ld'], f)

    except IOError:
        gv.plugin_data['ld'] = []
        with open('./data/sensors.json', 'w') as f:
            json.dump(gv.plugin_data['ld'], f)

path = os.path.join('.', 'data', 'sensors')
mkdir_p(path)
load_sensors()

last_change_time = [0]
last_srvals = [0]

#### change outputs when blinker signal received ####
def on_zone_change(arg): #  arg is just a necessary placeholder.
    """ Switch relays when core program signals a change in zone state."""

    global last_change_time, last_srvals

    with gv.rs_lock:
        if len(last_change_time) > len(gv.srvals):
            last_change_time = [last_change_time[i] for i in range(len(gv.srvals))]
            last_srvals = [gv.srvals[i] for i in range(len(gv.srvals))]
        elif len(last_change_time) < len(gv.srvals):
            new_last_change = [0]*len(gv.srvals)
            new_last_srvals = [0]*len(gv.srvals)
            for i in range(len(last_change_time)):
                new_last_change[i] = last_change_time[i]
                new_last_srvals[i] = last_srvals[i]
            last_change_time = new_last_change
            last_srvals = new_last_srvals
    
        now = gv.now
        for i in range(len(gv.srvals)):
            if gv.srvals[i] != last_srvals[i]:  # change?
                last_srvals[i] = gv.srvals[i]
                last_change_time[i] = now

zones = signal('zone_change')
zones.connect(on_zone_change)

def random_sensor_value(low, high, low_t, low_p, high_t, high_p):
    """
    Return a floating value between low and high inclusive such that low_p percent of
    the time it will be between low and low_t (incl) and high_p percent of the time it
    will be between high_t and high.  It is assumed low <= low_t <= high_t <= high.
    """
    scale = 100.
    low_cross = int(low_p)
    high_cross = int(low_p) + int(high_p)
    r = randint(1,100)
    if r <= low_cross:
        return randint(int(low*scale), int(low_t*scale))/scale
    elif r <= high_cross:
        return randint(int(high_t*scale), int(high*scale))/scale
    elif high_t*scale-low_t*scale >= 2:
        return randint(int(low_t*scale)+1, int(high_t*scale)-1)/scale
    else:
        return int(low_t*scale)/scale
 
def scratch_register_verification(bd):
    try:
        v = i2c_read(i2c.ADDRESS+bd, 0xc) # write scratch register as keepalive
        if v != bd+16: # needs to match sip.py
            gv.logger.critical('Bad value for scratch register for board: ' + str(bd) + ' value: ' + str(v))
        else:
            gv.logger.critical('Scratch register value fine for board: ' + str(bd) + ' value: ' + str(v))
    except:
        gv.logger.critical('Failed to read scratch register for board: ' + str(bd))
        pass

################################################################################
# Main function loop:                                                          #
################################################################################

class SensorsChecker(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.daemon = True
        self.status = {}
        self._sleep_time = 0
        self.start()

    def add_status(self, id, msg):
        if id in self.status:
            self.status[id] += '\n' + msg
        else:
            self.status[id] = msg

    def start_status(self, id, msg):
        if id in self.status:
            del self.status[id]
        self.add_status(id, msg)

    def update(self):
        self._sleep_time = 0

    def _sleep(self, secs):
        self._sleep_time = secs
        while self._sleep_time > 0:
            time.sleep(1)
            self._sleep_time -= 1

    def check_high_trigger(self, s, index, now):
        major_change = True
        status_update = True
        if index in s['last_high_report']:
            if (index not in s['last_low_report'] or s['last_low_report'][index] < s['last_high_report'][index]) and \
               (index not in s['last_good_report'] or s['last_good_report'][index] < s['last_high_report'][index]):
                major_change = False
            if not major_change and now - s['last_high_report'][index] < 3600:
                status_update = False
        return (major_change, status_update)

    def check_low_trigger(self, s, index, now):
        major_change = True
        status_update = True
        if index in s['last_low_report']:
            if (index not in s['last_high_report'] or s['last_high_report'][index] < s['last_low_report'][index]) and \
               (index not in s['last_good_report'] or s['last_good_report'][index] < s['last_low_report'][index]):
                major_change = False
            if not major_change and now - s['last_low_report'][index] < 3600:
                status_update = False
        return (major_change, status_update)

    def check_good_trigger(self, s, index, now):
        major_change = True
        status_update = True
        if index in s['last_good_report']:
            if (index not in s['last_low_report'] or s['last_low_report'][index] < s['last_good_report'][index]) and \
               (index not in s['last_high_report'] or s['last_high_report'][index] < s['last_good_report'][index]):
                major_change = False
            if not major_change and now - s['last_good_report'][index] < 3600:
                status_update = False
        elif index not in s['last_low_report'] and index not in s['last_high_report']:
            major_change = False # if no problem dont report on startup
        if major_change and not s['normal_trigger']:
            major_change = False
        return (major_change, status_update)

    def trigger_programs(self, s, index, now):
        try:
            if s['last_high_report'][index] == now:
                for tp in s['trigger_high_program']:
                    pid = map_program_to_pid(tp)
                    if pid != -1:
                        run_program(pid)
                        schedule_recurring_instances(pid, True)
        except:
            pass
        try:
            if s['last_low_report'][index] == now:
                for tp in s['trigger_low_program']:
                    pid = map_program_to_pid(tp)
                    if pid != -1:
                        run_program(pid)
                        schedule_recurring_instances(pid, True)
        except:
            pass

    def run(self):
        global last_change_time, last_srvals

        self._sleep(5) # let things catch up
        for s in gv.plugin_data['ld']:
            update_ptype(s)

        while True:
            try:
                self._sleep(1)
                now = gv.now

                if 'ld' not in gv.plugin_data:
                    self._sleep(5)
                    continue

                for s in gv.plugin_data['ld']:
                    if not s['enabled']:
                        s['last_read_value'] = None
                        s['failure_sequence_count'] = 0
                        continue

                    if s['vsb_bd'] in gv.in_bootloader: # ignore sensors if board is in bootloader
                        continue

                    major_sample = now - s['last_sample_time'] >= s['sample_rate']
                    minor_sample = now - s['last_sub_sample_time'] >= s['sample_rate']/float(len(s['sub_samples']))
                    if not major_sample and not minor_sample:
                        continue

                    if 'last_low_report' not in s:
                        # first time through.  Reset some info and try again
                        s['last_low_report'] = {}
                        s['last_good_report'] = {}
                        s['last_high_report'] = {}

                    jsave(gv.plugin_data['ld'], 'sensors') # save last good,low,high reports
                    reading = None
                    if s['type'] == 'Leak Detector':
                        if not s['remote_sensor'] and s['vsb_bd'] > -1:
                            try:
                                reading = i2c.read_pulse_counter(s['vsb_bd'], s['vsb_pos']) # read and reset counter
#                                if s['last_read_state'] == 'failure':
                                if s['failure_sequence_count'] > 1:
                                    subj = 'Sensor Read Success'
                                    body = 'Successfully read ' + s['type'] + ' sensor: ' + s['name']
                                    update_log(s, 'lge', now, body)
                                    if s['te']:
                                        text_email.checker.try_mail(subj, body)
                                    scratch_register_verification(s['vsb_bd'])
                                if s['failure_sequence_count'] >= 1:
                                    gv.logger.info('Single failure corrected for sensor ' + s['name'])
                                s['failure_sequence_count'] = 0
                                s['last_read_state'] = 'success'
                            except Exception as ex:
#                                if s['last_read_state'] == 'success' and s['te']:
                                if s['failure_sequence_count'] == 1:
                                    subj = 'Sensor Read Failure' if isinstance(ex, ValueError) else 'Valve Board Read Failure'
                                    body = 'Failed to read ' + s['type'] + ' sensor: ' + s['name']
                                    update_log(s, 'lge', now, subj+'; '+body)
                                    if s['te']:
                                        text_email.checker.try_mail(subj, body)
                                elif s['failure_sequence_count'] < 1:
                                    gv.logger.info('Single failure ignored for sensor ' + s['name'])
                                elif s['failure_senquence_count'] == 2 or s['failure_senquence_count']%100 == 0:
                                    gv.logger.info('Multi failure ignored for sensor ' + s['name'] + ' times: ' + str(s['failure_sequence_count']))
                                scratch_register_verification(s['vsb_bd'])
                                s['failure_sequence_count'] += 1
                                s['last_read_state'] = 'failure'

                        s['last_read_value'] = None
                        if reading != None:
                            try:
                                reading = int(float(reading)/(now-s['last_sample_time'])) # scale to per second
                            except:
                                gv.logger.critical('reading exception reading: ' + str(reading) + ' major: ' + str(major_sample) + ' now: ' + str(now) + ' s: ' + str(s))
                                reading = 0
                            # todo consider removing the check once fixed
                            if reading > 500000:
                                gv.logger.critical('Ignoring bad pulse counter for sensor ' + s['name'] + ' value: ' + str(reading) + ' hex: ' + hex(reading))
                                reading = None
                            else:
                                s['last_read_value'] = "{0:.2f}".format(reading)
                        save_last_sample_time = s['last_sample_time']
                        s['last_sample_time'] = now
                        s['last_sub_sample_time'] = now
                        update_sub_samples(s, reading)
                        stable = True
                        with gv.rs_lock:
                            operating_stations = [str(e) for e in gv.srvals]
                            operating_str = operating_stations_str(operating_stations)
                            # todo remove all ignored stations, but be careful with long sample times
                            for sid in range(len(last_change_time)):
                                if now - last_change_time[sid] < s['stabilization']:
                                    gv.logger.debug('sensor ' + s['name'] + ' skipping log due to sid: ' + str(sid+1) + ' changing within stabilization period.  Operating: ' + operating_str)
                                    stable = False
                                elif last_change_time[sid] > save_last_sample_time:
                                    gv.logger.debug('sensor ' + s['name'] + ' skipping log due to sid: ' + str(sid+1) + ' changing since last sample.   Operating: ' + operating_str)
                                    stable = False

                        sensitivity = s['sensitivity']/100.
                        if not stable or sensitivity == 1. or reading == None:
                            continue

                        mean = update_flow_records(s['name'], reading, ''.join(operating_stations))
                        s['trigger_low_threshold'] = mean*(1.-sensitivity)-0.1 # point in time value
                        s['trigger_high_threshold'] = mean*(1.+sensitivity)+0.1

                        if s['trigger_high_threshold'] < reading:
                            # if no operating stations likely leak
                            (major_change, status_update) = self.check_high_trigger(s, operating_str, now)
                            action = 'High Trigger' if major_change else 'High Value'
                            update_log(s, 'lgs', now, reading, action)
                            s['last_high_report'][operating_str] = now
                            if status_update:
                                self.start_status(s['name']+operating_str, 'Flow too high.  Meter: ' + s['name'] + '.')
                                if mean == 0:
                                    self.add_status(s['name']+operating_str, 'Expected 0 flow.  Got: ' + str(reading))
                                else:
                                    self.add_status(s['name']+operating_str, 'Flow was %.2f%% of what was expected' % (math.fabs((reading/mean)-1)*100))
                                self.add_status(s['name']+operating_str, 'Operating Stations: ' + operating_str)
                        elif s['trigger_low_threshold'] > reading:
                            # if no operating stations, probably no water
                            (major_change, status_update) = self.check_low_trigger(s, operating_str, now)
                            action = 'Low Trigger' if major_change else 'Low Value'
                            update_log(s, 'lgs', now, reading, action)
                            s['last_low_report'][operating_str] = now
                            if status_update:
                                self.start_status(s['name']+operating_str, 'Flow too low.  Meter: ' + s['name'] + '.')
                                self.add_status(s['name']+operating_str, 'Flow was %.2f%% of what was expected' % (math.fabs((reading/mean)-1)*100))
                                self.add_status(s['name']+operating_str, 'Operating Stations: ' + operating_str)
                        else:
                            (major_change, status_update) = self.check_good_trigger(s, operating_str, now)
                            action = 'Normal Trigger' if major_change else 'Normal Value'
                            update_log(s, 'lgs', now, reading, action)
                            s['last_good_report'][operating_str] = now
                            if status_update:
                                self.start_status(s['name']+operating_str, 'Flow appropriate.  Meter: ' + s['name'] + '.')
                                if mean == 0:
                                    self.add_status(s['name']+operating_str, 'Expected 0 flow.  Got: ' + str(reading))
                                else:
                                    self.add_status(s['name']+operating_str, 'Flow was within %.2f%% of expected' % (math.fabs((reading/mean)-1)*100))
                                self.add_status(s['name']+operating_str, 'Operating Stations: ' + operating_str)

                        if major_change:
                            self.trigger_programs(s, operating_str, now)
                            update_log(s, 'lge', now, self.status[s['name']+operating_str])
                            if s['te']:
                                text_email.checker.try_mail('Flow Change', self.status[s['name']+operating_str])
                            gv.logger.debug('flow change mean: ' + str(mean) + ' flow: ' + str(reading))

                    elif s['type'] in ['Dry Contact', 'Motion']:
                        changed_state = False
                        contact_info = [0 for i in range(4)]
                        if not s['remote_sensor'] and s['vsb_bd'] > -1:
                            try:
                                contact_info = i2c.read_dry_contact(s['vsb_bd'], s['vsb_pos']) # read and reset counter
                                reading = 1 - (contact_info[1]&1) # current value lsb (but we invert so open==0, closed==1
                                changed_state = (contact_info[1]&2) == 2
                                if s['last_read_state'] == 'failure':
                                    subj = 'Sensor Read Success'
                                    body = 'Successfully read ' + s['type'] + ' sensor: ' + s['name']
                                    update_log(s, 'lge', now, body)
                                    if s['te']:
                                        text_email.checker.try_mail(subj, body)
                                    scratch_register_verification(s['vsb_bd'])
                                s['failure_sequence_count'] = 0
                                s['last_read_state'] = 'success'
                            except Exception as ex:
                                if s['failure_sequence_count'] == 1:
                                    subj = 'Sensor Read Failure' if isinstance(ex, ValueError) else 'Valve Board Read Failure'
                                    body = 'Failed to read ' + s['type'] + ' sensor: ' + s['name']
                                    update_log(s, 'lge', now, subj+'; '+body)
                                    if s['te']:
                                        text_email.checker.try_mail(subj, body)
                                elif s['failure_sequence_count'] < 1:
                                    gv.logger.info('Single failure ignored for sensor ' + s['name'])
                                elif s['failure_senquence_count'] == 2 or s['failure_senquence_count']%100 == 0:
                                    gv.logger.info('Multi failure ignored for sensor ' + s['name'] + ' times: ' + str(s['failure_sequence_count']))
                                scratch_register_verification(s['vsb_bd'])
                                s['failure_sequence_count'] += 1
                                s['last_read_state'] = 'failure'
                        else:
                            #todo deal with contact for remote radios
                            pass

                        s['last_read_value'] = reading
                        save_last_sample_time = s['last_sample_time']
                        s['last_sample_time'] = now
                        s['last_sub_sample_time'] = now
                        update_sub_samples(s, reading)
                        if reading != None and changed_state:
                            if reading:
                                update_log(s, 'lgs', now, reading, 'Closed Trigger')
                                s['last_high_report'][s['name']] = now
                                self.start_status(s['name'], s['type'] + ' sensor: ' + s['name'] + ' closed and triggered.')
                            else:
                                update_log(s, 'lgs', now, reading, 'Open Trigger')
                                s['last_low_report'][s['name']] = now
                                self.start_status(s['name'], s['type'] + ' sensor: ' + s['name'] + ' open and triggered.')

                            self.trigger_programs(s, s['name'], now)
                            update_log(s, 'lge', now, self.status[s['name']])
                            if s['te']:
                                text_email.checker.try_mail(s['type'] + ' Change', self.status[s['name']])
                        elif reading != None:
                            update_log(s, 'lgs', now, reading)

                    elif s['type'] in ['Temperature', 'Moisture']:
                        #todo implement moisture sensor
#                        reading = random_sensor_value(-25, 50, s['trigger_low_threshold'], 1, s['trigger_high_threshold'], 98)
                        if s['type'] == 'Temperature':
                            if s['remote_sensor'] == 0 and s['vsb_bd'] > -1:
                                sensor_page = i2c.BASE + 0x30*s['vsb_pos']
                                address = i2c.ADDRESS + s['vsb_bd']

#                                i2c.read_temperature_state(s['vsb_bd'], s['vsb_pos'])
                                try:
                                    v = i2c_read(address, sensor_page+1)
                                    if v == 3:
                                        v = i2c_structure_read('h', address, sensor_page+2)[0]
                                        reading = v/10.
                                except:
                                    pass
                            elif s['remote_sensor'] == 1:
                                if s['vsb_bd'] in gv.remote_sensors: # remote radio name with data?
                                    sens_data = gv.remote_sensors[s['vsb_bd']]
                                    if sens_data['time'] >= s['last_sample_time']:
                                        if s['vsb_pos'] >= 0 and s['vsb_pos'] <= 1: # first two are analog
                                            try:
                                                adc_pos = sens_data['adc'+str(s['vsb_pos'])]
                                                adc2_reading = sens_data['adc2']
                                                # 0C + .1 degrees C for each millivolt of adc2-sensor
                                                reading = (adc_pos-adc2_reading)/1024. * 3300 * 0.1
                                            except:
                                                gv.logger.exception('Local radio analog read exception.  name: ' + s['name'] + ' bd: ' + s['vsb_bd'] + ' pos: ' + str(s['vsb_pos']))
                                                reading = None

                        s['last_sub_sample_time'] = now
                        update_sub_samples(s, reading)

                        if not major_sample:
                            continue

                        reading_list = [r for r in s['sub_samples'] if r != None]
                        if len(reading_list) == 0:
                            reading = None
                        else:
                            reading = sum(reading_list)/float(len(reading_list))

                        if reading == None:
                            if s['last_read_state'] == 'success':
                                subj = 'Sensor Read Failure'
                                body = 'Failed to read ' + s['type'] + ' sensor: ' + s['name']
                                update_log(s, 'lge', now, subj+'; '+body)
                                if s['te']:
                                    text_email.checker.try_mail(subj, body)
                            s['last_read_state'] = 'failure'
                        else:
                            if s['last_read_state'] == 'failure':
                                subj = 'Sensor Read Success'
                                body = 'Successfully read ' + s['type'] + ' sensor: ' + s['name']
                                update_log(s, 'lge', now, body)
                                if s['te']:
                                    text_email.checker.try_mail(subj, body)
                            s['last_read_state'] = 'success'

                        s['last_read_value'] = reading
                        s['last_sample_time'] = now
                        if reading == None:
                            continue

                        preading = reading*1.8 + 32 if s['type'] == 'Temperature' and gv.sd['tu'] == 'F' else reading
                        preading = "{0:.2f}".format(preading)
                        if reading > s['trigger_high_threshold']:
                            (major_change, status_update) = self.check_high_trigger(s, s['name'], now)
                            s['last_high_report'][s['name']] = now
                            action = 'High Trigger' if major_change else 'High Value'
                            update_log(s, 'lgs', now, reading, action) # wait for reading to be updated
                            if status_update:
                                self.start_status(s['name'], s['type'] + ' sensor: ' + s['name'] + ' triggered with reading ' + preading)
                        elif reading < s['trigger_low_threshold']:
                            (major_change, status_update) = self.check_low_trigger(s, s['name'], now)
                            s['last_low_report'][s['name']] = now
                            action = 'Low Trigger' if major_change else 'Low Value'
                            update_log(s, 'lgs', now, reading, action) # wait for reading to be updated
                            if status_update:
                                self.start_status(s['name'], s['type'] + ' sensor: ' + s['name'] + ' triggered with reading ' + preading)
                        else:
                            (major_change, status_update) = self.check_good_trigger(s, s['name'], now)
                            s['last_good_report'][s['name']] = now
                            action = 'Normal Trigger' if major_change else 'Normal Value'
                            update_log(s, 'lgs', now, reading, action) # wait for reading to be updated
                            if status_update:
                                self.start_status(s['name'], s['type'] + ' sensor: ' + s['name'] + ' back in normal range with reading ' + preading)

                        if major_change:
                            self.trigger_programs(s, s['name'], now)
                            update_log(s, 'lge', now, self.status[s['name']])
                            if s['te']:
                                text_email.checker.try_mail(s['type'] + ' Sensor Change', self.status[s['name']])
                    else:
                        gv.logger.critical('Unimplemented sensor: ' + s['name'] + ' type: ' + s['type'])

            except Exception as ex:
                gv.logger.exception('sensor loop exception: ' + str(ex))


checker = SensorsChecker()


################################################################################
# Web pages:                                                                   #
################################################################################

class view_sensors(ProtectedPage):
    """Load an html page for viewing all sensor parameters"""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        if process_page_request('view_sensors', qdict):
            return template_render.sensors(0, gv.plugin_data['ld'])
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Sensors&continuation=lda')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'sensors':1, 'ldi':-1})
                return template_render.sensors(subid, data['sensors'])
            except Exception as ex:
                gv.logger.info('view_sensors: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

temporary_sensor = proto_sens.copy()
class modify_sensor(ProtectedPage):
    """Load an html page for entering a sensor's parameters"""

    def GET(self):
        global temporary_sensor

        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        sensid = int(qdict['sensid'])
        vflags = int(qdict['vflags']) if 'vflags' in qdict else 0
        if process_page_request('modify_sensor', qdict):
            sensnames = [s['name'] for s in gv.plugin_data['ld']]
            if vflags == 2:
                sensdata = temporary_sensor
            else:
                sensdata = gv.plugin_data['ld'][sensid] if sensid >= 0 else proto_sens
                temporary_sensor = sensdata.copy()
            sensboards = i2c.get_vsb_boards().keys()
            remotesensboards = get_remote_sensor_boards()
            return template_render.modify_sensor(0, gv.sd['tu'], gv.pd, sensboards, remotesensboards, sensnames, sensid, sensdata)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Sensors&continuation=ldms')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'programs':1, 'sensors':1, 'ldi':-1})
                sensnames = [s['name'] for s in data['sensors']]
                if vflags == 2:
                    sensdata = temporary_sensor
                else:
                    sensdata = data['sensors'][sensid] if sensid >= 0 else proto_sens
                    temporary_sensor = sensdata.copy()
                return template_render.modify_sensor(subid, gv.sd['tu'], data['programs'], data['sensboards'], data['remotesensboards'], sensnames, sensid, sensdata)
            except Exception as ex:
                gv.logger.info('modify_sensor: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

class change_sensor(ProtectedPage):
    """Save user input to sensors.json file"""

    def GET(self):
        qdict = web.input(trigger_low_program=[], trigger_high_program=[])
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])

        for tpl in ['trigger_low_program', 'trigger_high_program']:
            if tpl in qdict and 'None' in qdict[tpl]:
                qdict[tpl].remove('None')

        for f in list(temporary_sensor.keys()): # for remote changes, grab temporary_sensor data to pass in qdict
            if f not in qdict and f not in ['last_sample_time', 'last_sub_sample_time', 'sub_samples', 'lgs', 'lge', 'te', 'normal_trigger', 'last_read_value', 'last_read_state', 'failure_sequence_count']: # dont hold onto old checkbox flags
                if type(temporary_sensor[f]) is str:
                    qdict[f] = temporary_sensor[f]
                elif type(temporary_sensor[f]) is int or type(temporary_sensor[f]) is float:
                    qdict[f] = str(temporary_sensor[f])
                elif type(temporary_sensor[f]) is not dict:
                    gv.logger.critical('change_sensor: Unexpected type in temporary_sensor for field ' + f + \
                                       str(type(temporary_sensor[f])))
                    qdict[f] = temporary_sensor[f]

        if process_page_request('change_sensor', qdict) or subid == 0:
            sensid = int(qdict['sensid'])
            if sensid >= len(gv.plugin_data['ld']):
                raise web.unauthorized()
            if sensid >= 0:
                old_sens = gv.plugin_data['ld'][sensid]
            else:
                old_sens = proto_sens.copy()

            new_sens = {}
            for f in ['last_read_value', 'last_read_state', 'failure_sequence_count', 'last_sample_time', 'last_sub_sample_time', 'sub_samples']:
                new_sens[f] = old_sens[f]

            try:
                new_name = qdict['name']
                if new_name == '':
                    new_name = 'Sensor'
            except:
                raise web.unauthorized()

            try:
                if qdict['type'] not in ['None', 'Dry Contact', 'Leak Detector', 'Temperature', 'Motion', 'Moisture']:
                    raise web.unauthorized()
            except:
                raise web.unauthorized()

            # Program names must be unique.  If this name is not unique add a number to the end until it is unique
            if new_name != old_sens['name']:
                try:
                    for i in range(len(gv.plugin_data['ld'])):
                        if i != sensid and gv.plugin_data['ld'][sensid]['name'] == new_name:
                            raise IOError, 'Duplicate Name'
                except IOError:
                    for i in range(len(gv.plugin_data['ld'])):
                        try:
                            try_name = new_name + '_' + str(i+1)
                            for j in range(len(gv.plugin_data['ld'])):
                                if j != sensid and gv.plugin_data['ld'][j]['name'] == try_name:
                                    raise IOError, 'Duplicate Name'
                            new_name = try_name
                            break
                        except IOError:
                            continue

            new_sens['type'] = qdict['type']
            try:
                new_sens['vsb_bd'] = int(qdict['vsb_bd'])
                new_sens['remote_sensor'] = 0
            except:
                new_sens['vsb_bd'] = qdict['vsb_bd']
                new_sens['remote_sensor'] = 1
            new_sens['vsb_pos'] = int(qdict['vsb_pos'])

            if new_sens['type'] != old_sens['type']: # delete any history if changing type
                if new_sens['type'] in ['None', 'Dry Contact', 'Leak Detector', 'Temperature', 'Motion', 'Moisture']:
                    update_ptype(new_sens)

                if old_sens['name']:
                    try:
                        old_path = os.path.join('.', 'data', 'sensors', old_sens['name'])
                        shutil.rmtree(old_path)
                    except:
                        pass

            new_sens['name'] = new_name
            new_path = os.path.join('.', 'data', 'sensors', new_name)
            if new_name != old_sens['name']:
                if old_sens['name']:
                    try:
                        old_path = os.path.join('.', 'data', 'sensors', old_sens['name'])
                        shutil.move(old_path, new_path)
                    except Exception as ex:
                        pass

            for f in ['enabled', 'sample_rate', 'lgs', 'lge', 'te', 'normal_trigger']:
                if f in qdict:
                    if qdict[f] == 'on':
                        qdict[f] = '1'
                    new_sens[f] = int(qdict[f])
                else:
                    new_sens[f] = 0

            if new_sens['type'] == 'Motion':
                new_sens['trigger_high_program'] = qdict['trigger_high_program']
                new_sens['trigger_high_threshold'] = 1
            elif new_sens['type'] == 'Leak Detector':
                new_sens['stabilization'] = int(qdict['stabilization'])
                new_sens['sensitivity'] = int(qdict['sensitivity'])
                if 'rln' in qdict:
                    path = os.path.join(new_path, 'fm')
                    try:
                        shutil.rmtree(path)
                    except:
                        pass

            if new_sens['type'] in ['Dry Contact', 'Leak Detector', 'Temperature', 'Moisture']:
                new_sens['trigger_low_program'] = qdict['trigger_low_program']
                new_sens['trigger_high_program'] = qdict['trigger_high_program']
                if new_sens['type'] != 'Leak Detector':
                    # keep temperature stored information in C
                    if new_sens['type'] == 'Temperature' and gv.sd['tu'] == 'F':
                        new_sens['trigger_low_threshold'] = (float(qdict['trigger_low_threshold'])-32)/1.8
                        new_sens['trigger_high_threshold'] = (float(qdict['trigger_high_threshold'])-32)/1.8
                    else:
                        new_sens['trigger_low_threshold'] = float(qdict['trigger_low_threshold'])
                        new_sens['trigger_high_threshold'] = float(qdict['trigger_high_threshold'])

            for lg in ['lgs', 'lge']:
                if not new_sens[lg]:
                    kind = 'slog' if lg == 'lgs' else 'evlog'
                    try:
                        shutil.move(os.path.join(new_path, 'logs', kind)+'.json', '/dev/null')
                    except:
                        pass

            with open('./data/sensors.json', 'w') as f:  # write the settings to file
                if sensid == -1:
                    sensid = len(gv.plugin_data['ld'])
                    gv.plugin_data['ld'].append({})
                gv.plugin_data['ld'][sensid] = new_sens
                json.dump(gv.plugin_data['ld'], f)

            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'ldcs', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_sensor: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/lda')

class change_sensor_type(ProtectedPage):
    """Change the type of a sensor when creating or modifying it.  We do this all on the local machine until it is saved."""

    def GET(self):
        global temporary_sensor

        qdict = web.input()

        # delete any old information that is type specific
        for f in ['trigger_low_threshold', 'trigger_high_threshold', 'trigger_low_program', 'trigger_high_program', \
                  'sensitivity', 'stabilization']:
            if f in temporary_sensor:
                del temporary_sensor[f]

        if 'name' in qdict:
            temporary_sensor['name'] = qdict['name']
        temporary_sensor['type'] = qdict['type']

        if temporary_sensor['type'] == 'Leak Detector':
            temporary_sensor['sensitivity'] = 10
            temporary_sensor['stabilization'] = 90

        if temporary_sensor['type'] in ['Dry Contact', 'Motion']:
            temporary_sensor['trigger_high_threshold'] = 1
            temporary_sensor['trigger_high_program'] = []
            if temporary_sensor['type'] == 'Dry Contact':
                temporary_sensor['trigger_low_threshold'] = 0
                temporary_sensor['trigger_low_program'] = []
        elif temporary_sensor['type'] in ['Leak Detector', 'Moisture', 'Temperature']:
            temporary_sensor['trigger_high_threshold'] = 0
            temporary_sensor['trigger_high_program'] = []
            temporary_sensor['trigger_low_threshold'] = 0
            temporary_sensor['trigger_low_program'] = []

        web.header('Content-Type', 'application/json')
        return json.dumps([])

class delete_sensor(ProtectedPage):
    """Delete one or all existing sensor(s)."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        sensid = int(qdict['sensid'])
        if process_page_request('delete_sensor', qdict) or subid == 0:
            if sensid == -1:
                for sens in gv.plugin_data['ld'][:]:
                    try:
                        shutil.rmtree(os.path.join('.', 'data', 'sensors', sens['name']))
                    except:
                        pass
                    del gv.plugin_data['ld'][0]
            else:
                try:
                    shutil.rmtree(os.path.join('.', 'data', 'sensors', gv.plugin_data['ld'][sensid]['name']))
                except:
                    pass
                del gv.plugin_data['ld'][sensid]

            jsave(gv.plugin_data['ld'], 'sensors')
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'ldds', 'substation', '0')
            except Exception as ex:
                gv.logger.info('delete_sensor: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/lda')

class enable_sensor(ProtectedPage):
    """Activate or deactivate an existing sensor(s)."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        sensid = int(qdict['sensid'])
        if process_page_request('enable_sensor', qdict) or subid == 0:
            gv.plugin_data['ld'][sensid]['enabled'] = int(qdict['enabled'])
            jsave(gv.plugin_data['ld'], 'sensors')
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'ldes', 'substation', '0')
            except Exception as ex:
                gv.logger.info('enable_sensor: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/lda')

class view_log_sensor(ProtectedPage):
    """View Log"""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        sensid = int(qdict['sensid'])
        if process_page_request('view_log_sensor', qdict):
            sample_records = read_log('sensors/'+gv.plugin_data['ld'][sensid]['name']+'/logs/slog')
            event_records = read_log('sensors/'+gv.plugin_data['ld'][sensid]['name']+'/logs/evlog')
            sens = gv.plugin_data['ld'][sensid]
            return template_render.log_sensor(0, gv.sd, sensid, sens, sample_records, event_records)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Log&continuation=ldvl')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['slog', 'evlog'], 'susldr', 'data', {'sd':1, 'sensors':1, 'ldi':sensid, 'evlog':1, 'slog':1, 'end_date':'', 'days_before':0})
                if len(data['sensors']) != 0:
                    return template_render.log_sensor(subid, data['sd'], sensid, data['sensors'], data['slog'], data['evlog'])
                # likely coming from one substations sensor page to another substation without the sensor
                # Go to /lda without being caught by current try/exception
            except Exception as ex:
#                exc_type, exc_value, exc_traceback = sys.exc_info()
#                err_string = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
                gv.logger.info('view_log_sensor: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            raise web.seeother('/lda')

class clear_log_sensor(ProtectedPage):
    """Delete all log records"""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        sensid = int(qdict['sensid'])
        if process_page_request('clear_log_sensor', qdict) or subid == 0:
            with io.open('./data/sensors/'+ gv.plugin_data['ld'][sensid]['name'] + '/logs/' + qdict['kind'] + '.json', 'w') as f:
                f.write(u'')
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'ldcl', 'substation', '0')
            except Exception as ex:
                gv.logger.info('clear_log_sensor: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/ldvl?sensid='+str(sensid)+'&substation='+str(subid))

class sensor_sample_log(ProtectedPage):
    """Download sensor sample log to file"""

    def GET(self):
        # assume only reasonable way to get here is to have just done a view log, so
        # most recent saved log data is what we will dump.
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        sensid = int(qdict['sensid'])
        if process_page_request('sensor_sample_log', qdict) or subid == 0:
            filename = 'sensor-sample-log'
            filename += '-' + gv.plugin_data['ld'][sensid]['name'] + '.csv'
            sens = gv.plugin_data['ld'][sensid]
            records = read_log('sensors/'+gv.plugin_data['ld'][sensid]['name']+'/logs/slog')
        else:
            filename = 'sensor-sample-log-' + gv.plugin_data['su']['subinfo'][subid]['name']
            filename += '-' + gv.plugin_data['su']['subdesc'][subid]['ld']['name'] + '.csv'
            sens = gv.plugin_data['su']['subdesc'][subid]['ld']
            records = gv.plugin_data['su']['subdesc'][subid]['slog']

        data = _("Date, Time, Value") + "\n"
        for r in records:
            event = ast.literal_eval(json.dumps(r))
            value = float(event['value'])
            if sens['type'] == 'Temperature' and gv.sd['tu'] == 'F':
                value = value*1.8 +32
            value = "{0:.2f}".format(value)
            data += event["date"] + ", " + event["time"] + ", " + value + "\n"

        web.header('Content-Type', 'text/csv')
        web.header('Content-Disposition', 'attachment; filename="'+filename+'"')
        return data

class sensor_event_log(ProtectedPage):
    """Download sensor event log to file"""

    def GET(self):
        # assume only reasonable way to get here is to have just done a view log, so
        # most recent saved log data is what we will dump.
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        sensid = int(qdict['sensid'])
        if process_page_request('sensor_log', qdict) or subid == 0:
            filename = 'sensor-event-log'
            filename += '-' + gv.plugin_data['ld'][sensid]['name'] + '.csv'
            records = read_log('sensors/'+gv.plugin_data['ld'][sensid]['name']+'/logs/evlog')
        else:
            filename = 'sensor-event-log-' + gv.plugin_data['su']['subinfo'][subid]['name']
            filename += '-' + gv.plugin_data['su']['subdesc'][subid]['ld']['name'] + '.csv'
            records = gv.plugin_data['su']['subdesc'][subid]['evlog']

        data = _("Date, Time, Event") + "\n"
        for r in records:
            event = ast.literal_eval(json.dumps(r))
            data += event["date"] + ", " + event["time"] + ", " + event["event"] + "\n"

        web.header('Content-Type', 'text/csv')
        web.header('Content-Disposition', 'attachment; filename="'+filename+'"')
        return data

class multigraph_sensors(ProtectedPage):
    """Load an html page for entering a sensor's parameters"""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        senstype = qdict['senstype']
        sample_records = {}
        sensnames = []
        if process_page_request('multigraph_sensors', qdict):
            for sens in gv.plugin_data['ld']:
                if sens['type'] == senstype:
                    sensnames.append(sens['name'])
                    new_sample_records = read_log('sensors/'+sens['name']+'/logs/slog')
                    for r in new_sample_records:
                        dt = r['date'] + ' ' + r['time']
                        if dt not in sample_records:
                            sample_records[dt] = {"datetime":dt}
                        sample_records[dt][sens['name']] = r['value']
            sample_records = sorted(sample_records.items(), key=operator.itemgetter(0))
            sample_records = [s[1] for s in sample_records]
            return template_render.multigraph_sensors(0, gv.sd, senstype, sensnames, sample_records)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Log&continuation=ldvl')
        else:
            try:
                subid, sdata = load_and_save_remote(qdict, [], 'susldr', 'data', {'sensors':1, 'ldi':-1})
                sens_pos = -1
                for sens in sdata['sensors']:
                    sens_pos += 1
                    if sens['type'] == senstype:
                        sensnames.append(sens['name'])
                        subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'sd':1, 'sensors':1, 'ldi':sens_pos, 'slog':1, 'end_date':'', 'days_before':0})
                        for r in data['slog']:
                            dt = r['date'] + ' ' + r['time']
                            if dt not in sample_records:
                                sample_records[dt] = {"datetime":dt}
                            sample_records[dt][sens['name']] = r['value']

                # sort in order of datetime and then strip off initial datetime value
                sample_records = sorted(sample_records.items(), key=operator.itemgetter(0))
                sample_records = [s[1] for s in sample_records]
                if len(sdata['sensors']) != 0:
                    return template_render.multigraph_sensors(subid, data['sd'], senstype, sensnames, sample_records)
                # likely coming from one substations sensor page to another substation without the sensor
                # Go to /lda without being caught by current try/exception
            except Exception as ex:
                gv.logger.info('multigraph_sensors: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            raise web.seeother('/lda')


################################################################################
# Helper functions:                                                            #
################################################################################

def operating_stations_str(operating_stations):
    """ Create a comma separated list of operating stations or 'None'"""

    operating = ''
    if sum([int(operating_stations[i]) for i in range(len(operating_stations))]) == 0:
        operating = 'None'
    else:
        for i in range(len(operating_stations)):
            if operating_stations[i] == '1':
                operating += ','+str(i+1)
        operating = operating[1:]

    return operating

def update_flow_records(name, flow, stations):
    """Add flow to the list of entries if the data set is not already full.  Return the mean"""

    path = os.path.join('.', 'data', 'sensors', name, 'fm', stations)
    directory = os.path.dirname(path)
    mkdir_p(directory)
    a = array('L')
    try:
        file_entries = os.path.getsize(path)>>2
        with open(path, 'r') as f:
            a.fromfile(f, file_entries)
    except (OSError, IOError) as e:
        if e.errno == errno.ENOENT:
            file_entries = 0
        else:
            raise
    except Exception, e:
        raise

    if file_entries < 50:
        a.append(flow)
        with open(path, 'wb') as f:
	    a.tofile(f)

    return float(sum(a))/len(a)

def update_log(sens, lg, now, msg, action=''):
    """Based on the 'lg' type, update the log with 'msg'"""

    if not sens[lg]:
        return

    kind = 'slog' if lg == 'lgs' else 'evlog'
    nowg = time.gmtime(now)

    logline = '{' + time.strftime('"time":"%H:%M:%S","date":"%Y-%m-%d"', nowg)
    if kind == 'slog':
        if type(msg) == float:
            msg = "{0:.1f}".format(msg)
        logline += ', "value":"' + str(msg) + '"'
        if action:
            logline += ', "action":"' + action + '"'
    else:
        logline += ', "event":' + json.dumps(msg)
    logline += '}'
    lines = []
    lines.append(logline + '\n')
    log_dir = os.path.join('.', 'data', 'sensors', sens['name'], 'logs')
    mkdir_p(log_dir) # ensure dir and file exists after config restore
    log_ref = log_dir + '/' + kind
    subprocess.call(['touch', log_ref + '.json'])
    try:
        log = read_log(log_ref[7:]) # drop leading ./data/
        for r in log:
            lines.append(json.dumps(r) + '\n')
        with open(log_ref+'.json', 'w') as f:
            if gv.sd['lr']:
                f.writelines(lines[:gv.sd['lr']])
            else:
                f.writelines(lines)
    except Exception as ex:
        gv.logger.error('Could not update log file.  Name: ' + sens['name'] + ' type: ' + kind + \
                        ' exception: ' + str(ex))

def map_program_to_pid(name):
    """Return the program id for the program named 'name'.  Otherwise -1"""

    for i in range(len(gv.pd)):
        p = gv.pd[i]
        if p[-1] == name:
            return i
    return -1
