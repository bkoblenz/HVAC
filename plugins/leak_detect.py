# !/usr/bin/env python
import datetime
from random import randint
from threading import Thread, RLock
import sys
import traceback
import shutil
import json
import time
import re
import os
import urllib
import urllib2
import errno
import pigpio
import text_email

from blinker import signal
import web
import gv  # Get access to ospi's settings
from urls import urls  # Get access to ospi's URLs
from ospi import template_render
from webpages import ProtectedPage
from helpers import mkdir_p, get_rpi_revision
from array import array

max_fm = 3

disable_leak_detect = False
if not gv.use_pigpio:
    print 'Error - leak_detect: Leak detection requires pigpio library installed'
    disable_leak_detect = True

if not disable_leak_detect:
    # Add a new url to open the data entry page.  DO THIS AFTER possible error exit in initialize_fm_pins()
    urls.extend(['/lda',  'plugins.leak_detect.settings',
                 '/ldj',  'plugins.leak_detect.settings_json',
                 '/ldu', 'plugins.leak_detect.update'])

    # Add this plugin to the home page plugins menu
    gv.plugin_menu.append(['Leak Detection', '/lda'])

def initialize_fm_pins():
    """Set input pins for flow meters."""

    global pi

    if disable_leak_detect:
        return

    for i in range(int(gv.plugin_data['ld']['count'])):
        if gv.plugin_data['ld']['data'][i]['enable'] == 'on':
            pin = gv.pin_map[int(gv.plugin_data['ld']['data'][i]['pin'])]
            mode = pi.get_mode(pin)
            if mode != 0:
                print 'Error - leak_detect: Possible pin conflict on physical pin: ', int(gv.plugin_data['ld']['data'][i]['pin'])
            pi.set_mode(pin, pigpio.INPUT)
            pi.set_pull_up_down(pin, pigpio.PUD_UP)

def load_fms():
    """Load the flow meter data from file."""

    try:
        with open('./data/leak_detect.json', 'r') as f:
            gv.plugin_data['ld'] = json.load(f)

    except IOError:
        gv.plugin_data['ld'] = {'count':'1', 'status':'',
                  'data':[{'name':'FM0'+str(i+1),'enable':'off','te':'off','sens':'10','mm':'1','ss':'30','pin':str(3+2*i)} for i in range(max_fm)]}
        with open('./data/leak_detect.json', 'w') as f:
            json.dump(gv.plugin_data['ld'], f)

    initialize_fm_pins()


last_change_time = [0]
last_srvals = [0]
pi = pigpio.pi()

load_fms()

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
    
        for i in range(len(gv.srvals)):
            if gv.srvals[i] != last_srvals[i]:  # change?
                last_srvals[i] = gv.srvals[i]
                last_change_time[i] = gv.now

if not disable_leak_detect:
    zones = signal('zone_change')
    zones.connect(on_zone_change)

################################################################################
# Main function loop:                                                          #
################################################################################

class LeakDetectChecker(Thread):
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
        messages = [v for k,v in self.status.iteritems()]
        gv.plugin_data['ld']['status'] = '\n'.join(messages)
        print msg

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

    def run(self):
        global last_change_time, last_srvals, pi, max_fm

        if disable_leak_detect:
            return

        last_low_report = [{}]*max_fm # last reporting of low flow index by flowmeter id (and stations operating)
        last_good_report = [{}]*max_fm
        last_high_report = [{}]*max_fm
        while True:
            try:
                options = gv.plugin_data['ld']
                wind_cb = []
                for fmid in range(len(gv.plugin_data['ld']['data'])):
                    wind_cb.append(pi.callback(gv.pin_map[int(gv.plugin_data['ld']['data'][fmid]['pin'])],
                                               pigpio.FALLING_EDGE))

                self._sleep(15) # collect a sample

                count = [0 if gv.plugin_data['ld']['data'][i]['enable'] == 'off' else wind_cb[i].tally() for i in range(len(wind_cb))]

                for fmid in range(int(options['count'])):
                    if gv.plugin_data['ld']['data'][fmid]['enable'] == 'off':
                        continue

                    stable = True
                    with gv.rs_lock:
                        operating_stations = [str(e) for e in gv.srvals]
                        # remove all ignored stations
                        stab_time = int(options['data'][fmid]['mm'])*60+int(options['data'][fmid]['ss'])
                        for sid in range(len(last_change_time)):
                            if gv.now - last_change_time[sid] < stab_time:
                                stable = False

                    sensitivity = float(options['data'][fmid]['sens'])/100.
                    if not stable or sensitivity == 1.:
                        continue

                    flow = count[fmid]
                    mean = update_flow_records(fmid, flow, ''.join(operating_stations))
                    operating_str = operating_stations_str(operating_stations)
                    do_status_update = True
                    major_flow_change = True

                    if mean*(1.+sensitivity) < flow:
                        # if no operating stations likely leak
                        if operating_str in last_high_report[fmid]: # only update status once per hour
                            if (operating_str not in last_low_report[fmid] or last_low_report[fmid][operating_str] < last_high_report[fmid][operating_str]) and \
                               (operating_str not in last_good_report[fmid] or last_good_report[fmid][operating_str] < last_high_report[fmid][operating_str]):
                                major_flow_change = False
                            if not major_flow_change and gv.now - last_high_report[fmid][operating_str] < 3600:
                                do_status_update = False
                        last_high_report[fmid][operating_str] = gv.now
                        if do_status_update:
                            self.start_status(operating_str, 'Flow too high.  Meter: '+str(fmid+1)+' (' +
                                              gv.plugin_data['ld']['data'][fmid]['name']+')')
                            if mean == 0:
                                self.add_status(operating_str, 'Expected 0 flow.  Got: ' + str(flow))
                            else:
                                self.add_status(operating_str, 'Flow was %.2f%% of expected' % (flow/mean))
                            self.add_status(operating_str, 'Operating Stations: ' + operating_str)
                    elif mean*(1. - sensitivity) > flow:
                        # if no operating stations, probably no water
                        if operating_str in last_low_report[fmid]: # only update status once per hour
                            if (operating_str not in last_high_report[fmid] or last_high_report[fmid][operating_str] < last_low_report[fmid][operating_str]) and \
                               (operating_str not in last_good_report[fmid] or last_good_report[fmid][operating_str] < last_low_report[fmid][operating_str]):
                                major_flow_change = False
                            if not major_flow_change and gv.now - last_low_report[fmid][operating_str] < 3600:
                                do_status_update = False
                        last_low_report[fmid][operating_str] = gv.now
                        if do_status_update:
                            self.start_status(operating_str, 'Flow too low.  Meter: '+str(fmid+1)+' (' +
                                              gv.plugin_data['ld']['data'][fmid]['name']+')')
                            self.add_status(operating_str, 'Flow was %.2f%% of expected' % (flow/mean))
                            self.add_status(operating_str, 'Operating Stations: ' + operating_str)
                    else:
                        if operating_str in last_good_report[fmid]: # only update status once per hour
                            if (operating_str not in last_low_report[fmid] or last_low_report[fmid][operating_str] < last_good_report[fmid][operating_str]) and \
                               (operating_str not in last_high_report[fmid] or last_high_report[fmid][operating_str] < last_good_report[fmid][operating_str]):
                                major_flow_change = False
                            if not major_flow_change and gv.now - last_good_report[fmid][operating_str] < 3600:
                                do_status_update = False
                        elif operating_str not in last_low_report[fmid] and operating_str not in last_high_report[fmid]:
                            major_flow_change = False # if no problem dont report on startup
                        last_good_report[fmid][operating_str] = gv.now
                        if do_status_update:
                            self.start_status(operating_str, 'Flow appropriate.  Meter: '+str(fmid+1)+' (' +
                                              gv.plugin_data['ld']['data'][fmid]['name']+')')
                            if mean == 0:
                                self.add_status(operating_str, 'Expected 0 flow.  Got: ' + str(flow))
                            else:
                                self.add_status(operating_str, 'Flow was %.2f%% of expected' % (flow/mean))
                            self.add_status(operating_str, 'Operating Stations: ' + operating_str)

                    if major_flow_change and gv.plugin_data['ld']['data'][fmid]['te'] == 'on':
                        text_email.checker.try_mail('Flow Change', self.status[operating_str])

            except Exception:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                err_string = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
                self.start_status('No Key', 'Leak Detection encountered error:\n' + err_string)


checker = LeakDetectChecker()


################################################################################
# Web pages:                                                                   #
################################################################################

class settings(ProtectedPage):
    """Load an html page for entering leak detection parameters"""

    def GET(self):
        return template_render.leak_detect(gv.plugin_data['ld'])


class settings_json(ProtectedPage):
    """Returns plugin settings in JSON format"""

    def GET(self):
        web.header('Access-Control-Allow-Origin', '*')
        web.header('Content-Type', 'application/json')
        dump_data = gv.plugin_data['ld']
        return json.dumps(dump_data)


class update(ProtectedPage):
    """Save user input to leak_detect.json file"""
    def GET(self):
        global max_fm

        qdict = web.input()
        newdict = gv.plugin_data['ld']
        change = False
        count_change = False
        if qdict['count'] != newdict['count']:
            change = True
            newdict['count'] = qdict['count']
            count_change = True

        for i in range(max_fm):
            if 'rln'+str(i) in qdict:
                path = os.path.join('.', 'data', 'leak_detect', 'fm'+str(i))
                try:
                    shutil.rmtree(path)
                except:
                    pass

        pin_change = False
        for i in range(max_fm):
            for f in ['name', 'enable', 'te', 'sens', 'pin', 'mm', 'ss']:
                if f+str(i) in qdict:
                    if qdict[f+str(i)] != newdict['data'][i][f]:
                        change = True
                        newdict['data'][i][f] = qdict[f+str(i)]
                        if f == 'pin':
                            pin_change = True
                elif f == 'enable':
                    if newdict['data'][i][f] == 'on':
                        change = True
                        newdict['data'][i][f] = 'off'
                else:
                    # old data is left and will come back (unenabled) if count is raised
                    pass

        if change:
            with open('./data/leak_detect.json', 'w') as f:  # write the settings to file
                newdict['status'] = gv.plugin_data['ld']['status'] # get any recent changes....still a small race
                gv.plugin_data['ld'] = newdict
                json.dump(newdict, f)

        if pin_change:
            initialize_fm_pins()

        checker.update()
        raise web.seeother('/')


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

def update_flow_records(fmid, flow, stations):
    """Add flow to the list of entries if the data set is not already full.  Return the mean"""

    path = os.path.join('.', 'data', 'leak_detect', 'fm'+str(fmid), stations)
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

    if file_entries < 30:
        a.append(flow)
        with open(path, 'wb') as f:
	    a.tofile(f)

    return float(sum(a))/len(a)
