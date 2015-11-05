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

from blinker import signal
import web
import gv  # Get access to ospi's settings
from urls import urls  # Get access to ospi's URLs
from ospi import template_render
from webpages import ProtectedPage, WebPage, load_and_save_remote
from helpers import jsave, get_ip, read_log
from system_update import checker as updatechecker

disable_substations = False
# Add a new url to open the data entry page.  DO THIS AFTER possible error exit in initialize_fm_pins()
if 'plugins.substations' not in urls:
    if gv.sd['master']:
        urls.extend(['/suslv',  'plugins.substations.view_substations',
                     '/suslj',  'plugins.substations.join_master',
                     '/susle',  'plugins.substations.execute_substation'])
    if gv.sd['slave']:
        urls.extend(['/susldr',  'plugins.substations.slave_data_request'])

    if not gv.sd['slave'] and not gv.sd['master']:
        disable_substations = True

    # Add this plugin to the home page plugins menu
    #gv.plugin_menu.append(['Substations', '/sua'])

def load_substations():
    """Load the substation data from file."""

    subdesco = {'pd':[],'sd':{},'log':[], 'snames':[]}
    try:
        with open('./data/substations.json', 'r') as f:
            gv.plugin_data['su'] = json.load(f)
        gv.plugin_data['su']['subdesc'] = [subdesco.copy()] # hold position 0 vacant
        for i in range(1,len(gv.plugin_data['su']['subinfo'])):
            gv.plugin_data['su']['subdesc'].append(subdesco.copy())

    except IOError:
        subinfoo = {'subinfo':[{'name':'','ip':'localhost','port':gv.sd['htp'],'proxy':'','status':'unknown','last_join':0}]} # hold position 0 vacant
        gv.plugin_data['su'] = subinfoo.copy()
        gv.plugin_data['su']['subdesc'] = [subdesco.copy()] # hold position 0 vacant
        jsave(subinfoo, 'substations')

if not disable_substations:
    load_substations()

################################################################################
# Main function loop:                                                          #
################################################################################

class SubstationChecker(Thread):
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
        # it enabled, dont store in json file
        #gv.plugin_data['su']['status'] = '\n'.join(messages)
        gv.logger.debug(msg)

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
        if disable_substations:
            return        

        gv.logger.info('Substation plugin started')        

        while True:
            try:
                if gv.sd['slave']:
                    try:
                        ppp_ip = get_ip('ppp0')
                        # if ppp ip address that is not 10.0.10.1 then need to go through gateway at 10.0.10.1
                        if ppp_ip != 'No IP Settings' and ppp_ip != '10.0.10.1':
                            # todo add gateway port if needed
                            urlcmd = 'http://10.0.10.1:9080'
                            info = {'ip': ppp_ip, 'port':gv.sd['htp'], 'name':gv.sd['name'], 'proxy':''}
                            urlcmd += '/supro?command=suslj&parameters=' + urllib.quote_plus(json.dumps(info))
                        else:
                            urlcmd = 'http://' + gv.sd['master_ip']
                            if gv.sd['master_port'] != 0 and gv.sd['master_port'] != 80:
                                urlcmd += ':' + str(gv.sd['master_port'])
                            info = {'ip': get_ip(), 'port':gv.sd['htp'], 'name':gv.sd['name'], 'proxy':''}
                            urlcmd += '/suslj?data=' + urllib.quote_plus(json.dumps(info))
                        data = urllib2.urlopen(urlcmd)
                        data = json.load(data)
                        if 'unreachable' in data:
                            raise IOError, 'UnreachableMaster'
                        force_reboot = False
                        # update common data that has changed on the master
                        for grouping in data:
                            for key in data[grouping]:
                                if grouping == 'sd':
                                    if key in gv.sd:
                                        if gv.sd[key] != data['sd'][key]:
                                            gv.logger.info('Changing gv.sd[' + key + '] from ' + str(gv.sd[key]) + ' to ' + str(data['sd'][key]))
                                            gv.sd[key] = data['sd'][key]
                                            if key == 'tza':
                                                with open('/etc/timezone','w') as file:
                                                    file.write(qdict['o'+f]+'\n')
                                                subprocess.call(['dpkg-reconfigure', '-f', 'non-interactive', 'tzdata'])
                                                force_reboot = True
                                            elif key == 'loc' or key == 'lang':
                                                force_reboot = True
                                    else:
                                        gv.logger.info('Setting gv.sd[' + key + '] to ' + str(data['sd'][key]))
                                        gv.sd[key] = data['sd'][key]
                                elif grouping in gv.plugin_data:
                                    if key in gv.plugin_data[grouping]:
                                        if gv.plugin_data[grouping][key] != data[grouping][key]:
                                            gv.logger.info('Changing gv.plugin_data[' + grouping +'][' + key + '] from ' + str(gv.plugin_data[grouping][key]) + ' to ' + str(data[grouping][key]))
                                            gv.plugin_data[grouping][key] = data[grouping][key]
                                    else:
                                        gv.logger.info('Setting gv.plugin_data[' + grouping +'][' + key + '] to ' + str(data[grouping][key]))
                                        gv.plugin_data[grouping][key] = data[grouping][key]
                                elif grouping == 'other':
                                    if key == 'websession':
                                        web.config._session.user = data[grouping][key]
                            if grouping == 'sd':
                                jsave(gv.sd, 'sd')
                            else:
                                pass # todo deal with any plugin_data saving
                        if force_reboot:
                            reboot()

                    except Exception as ex:
                        gv.logger.info('No master response.  ip: ' + gv.sd['master_ip'] + \
                                       ' port: ' + str(gv.sd['master_port']) + \
                                       ' Exception: ' + str(ex))

            except Exception as ex:
                self.start_status('', 'Substation encountered error: ' + str(ex))

            self._sleep(60)

checker = SubstationChecker()

################################################################################
# Web pages:                                                                   #
################################################################################

class view_substations(ProtectedPage):
    """ Generate links to perform an operation on a substation."""

    def GET(self):
        qdict = web.input()
        if 'head' not in qdict or 'continuation' not in qdict:
            raise web.unauthorized()
        head = qdict['head']
        cont = qdict['continuation']
        # loop through substations for reachability
        for subid in range(len(gv.plugin_data['su']['subinfo'])):
            if subid == 0:
                continue
            try:
                qd = {'substation':subid}
                load_and_save_remote(qd, [], 'susldr', 'data', {})
            except Exception as ex:
                gv.logger.info('view_substations: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
        return template_render.substations(head, cont, gv.plugin_data['su']['subinfo'])

class execute_substation(ProtectedPage):
    """ Generate links to perform an operation on a slave."""

    def GET(self):
        qdict = web.input()
        cont = '' if 'continuation' not in qdict else qdict['continuation']
        subid = 0 if 'substation' not in qdict else qdict['substation']
        raise web.seeother('/'+cont+'?substation='+str(subid))

class join_master(ProtectedPage):
    """ Capture a slave that is part of the group and return common data"""

    def GET(self):
        qdict = web.input()
        try:
            ddict = json.loads(qdict['data'])
        except:
            raise web.unauthorized()

        for p in ['name', 'ip', 'port', 'proxy']:
            if p not in ddict:
                raise web.unauthorized()
        ddict['last_join'] = gv.now
        ddict['status'] = 'ok'

        found_slave = False
        for i,d in enumerate(gv.plugin_data['su']['subinfo']):
            if i == 0:
                continue
            if d['name'] == ddict['name']:
                if d['ip'] != ddict['ip'] or d['port'] != ddict['port'] or d['proxy'] != ddict['proxy']:
                    gv.logger.info('Substation changed address from: ' + \
                                   d['ip'] + ':' + str(d['port']) + ' to: ' + \
                                   ddict['ip'] + ':' + str(ddict['port']) + \
                                   '; proxy from: ' + d['proxy'] + ' to: ' + ddict['proxy'])
                    su_strip = gv.plugin_data['su'].copy()
                    for p in ['ip', 'port', 'proxy']:
                        su_strip['subinfo'][i][p] = ddict[p]
                    del su_strip['subdesc'] # dont save active data
                    jsave(su_strip, 'substations')

                for p in ['ip', 'port', 'proxy', 'status', 'last_join']:
                    gv.plugin_data['su']['subinfo'][i][p] = ddict[p]
                found_slave = True
                break

        if not found_slave:
            gv.plugin_data['su']['subinfo'].append(ddict)
            idx = len(gv.plugin_data['su']['subinfo'])
            gv.plugin_data['su']['subdesc'].append(gv.plugin_data['su']['subdesc'][0].copy())
            su_strip = gv.plugin_data['su'].copy()
            del su_strip['subdesc'] # dont save active data
            jsave(su_strip, 'substations')

        result = {'sd':{}, 'te':{}, 'other':{}}
        web.header('Content-Type', 'application/json')
        for entry in gv.options:
            if entry[2] in ['name', 'htp', 'nbrd', 'opw', 'npw', 'cpw']:
                continue
            result['sd'][entry[2]] = gv.sd[entry[2]]
        for f in ['tu', 'password', 'salt', 'wl_et_weather']:
            try:
                result['sd'][f] = gv.sd[f]
            except:
                pass # if not yet defined, leave it alone
        result['other']['websession'] = web.config._session.user # capture login and logout

        # todo propagate et and text/email, flowmeter stuff, camera (and other plugins?)
        return json.dumps(result)

class slave_data_request(ProtectedPage):
    """Provide data to the master as the response if this looks like the master."""

    def GET(self):
        qdict = web.input()
        try:
            ddict = json.loads(qdict['data'])
        except:
            raise web.unauthorized()

        info = {}
        if 'log' in ddict and ddict['log']:
            end = ddict['end_date']
            days = ddict['days_before']
            records = read_log(end, days)
            info['log'] = records

        if 'lrun' in ddict and ddict['lrun']:
            info['lrun'] = gv.lrun

        if 'pd' in ddict and ddict['pd']:
            info['pd'] = gv.pd

        if 'ps' in ddict and ddict['ps']:
            info['ps'] = gv.ps

        if 'sbits' in ddict and ddict['sbits']:
            info['sbits'] = gv.sbits

        if 'srvals' in ddict and ddict['srvals']:
            info['srvals'] = gv.srvals

        if 'sd' in ddict and ddict['sd']:
            # todo delete network and other sensitive info when valid
            sd = gv.sd.copy()
            del sd['substation_network']
            del sd['salt']
            del sd['substation_network_salt']
            del sd['password']
            del sd['substation_network_password']
            del sd['pwd']
            del sd['substation_network_pwd']
            info['sd'] = sd

        if 'snames' in ddict and ddict['snames']:
            info['snames'] = gv.snames

        if 'update_status' in ddict and ddict['update_status']:
            updatechecker.update_rev_data()
            info['update_status'] = updatechecker.status

        # todo check that valid master made request
        web.header('Content-Type', 'application/json')
        return json.dumps(info)

################################################################################
# Helper functions:                                                            #
################################################################################

