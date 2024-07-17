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
import base64
import errno
import i2c

from blinker import signal
import web
import gv  # Get access to ospi's settings
from urls import urls  # Get access to ospi's URLs
from ospi import template_render
from webpages import ProtectedPage, WebPage, load_and_save_remote, message_base, validate_remote, get_ip_to_base
from helpers import jsave, get_ip, read_log, reboot, get_remote_sensor_boards, update_upnp, update_hostname, update_tza
import subprocess

disable_substations = False
# Add a new url to open the data entry page.  DO THIS AFTER possible error exit in initialize_fm_pins()
if 'plugins.substations' not in urls:
    if gv.sd['master']:
        urls.extend(['/suslv',  'plugins.substations.view_substations',
                     '/suslj',  'plugins.substations.join_master',
                     '/susde',  'plugins.substations.delete_substation',
                     '/suset',  'plugins.substations.set_substation',
                     '/susle',  'plugins.substations.execute_substation'])
    if gv.sd['slave']:
        urls.extend(['/susldr',  'plugins.substations.slave_data_request'])
        urls.extend(['/suiface',  'plugins.substations.slave_iface'])

    urls.extend(['/surrsd',  'plugins.substations.receive_remote_sensor_data'])
    urls.extend(['/surzd',  'plugins.substations.remote_zone_data'])

    if not gv.sd['slave'] and not gv.sd['master']:
        disable_substations = True

    # Add this plugin to the home page plugins menu
    #gv.plugin_menu.append(['Substations', '/sua'])

def load_substations():
    """Load the substation data from file."""

    subdesco = {'programs':[],'sd':{},'wlog':[], 'elog':[], 'slog':[], 'evlog':[], 'snames':[], 'snotes':[]}
    subinfoo = {'subinfo':[{'name':'','ip':'localhost','port':gv.sd['htp'],'proxy':'','status':'unknown','last_join':0}]} # hold position 0 vacant
    try:
        with open('./data/substations.json', 'r') as f:
            gv.plugin_data['su'] = subinfoo.copy()
#            gv.plugin_data['su'] = json.load(f)
        gv.plugin_data['su']['subdesc'] = [subdesco.copy()] # hold position 0 vacant
        for i in range(1,len(gv.plugin_data['su']['subinfo'])):
            gv.plugin_data['su']['subdesc'].append(subdesco.copy())

    except IOError:
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
        time.sleep(7) # let things wake up, but keep less than delay for sending email

        last_message_base = gv.now - 60
        while True:
            try:
                if gv.sd['slave'] and gv.now - last_message_base >= 60:
                    try:
                        last_message_base = gv.now
                        data = message_base('suslj')
                        if 'unreachable' in data:
                            raise IOError, 'UnreachableMaster'
                        force_reboot = False
                        # update common data that has changed on the master
                        for grouping in data:
                            if gv.sd['master']:
                                continue
                            for key in data[grouping]:
                                if grouping == 'sd':
                                    if key in gv.sd:
                                        if gv.sd[key] != data['sd'][key]:
                                            gv.logger.info('Changing gv.sd[' + key + '] from ' + str(gv.sd[key]) + ' to ' + str(data['sd'][key]))
                                            if key == 'remote_support_port' and gv.sd['enable_upnp']:
                                                gv.logger.critical('substation_run: Unexpected key of remote_support_port')
                                                if gv.sd[key] != 0: # delete old
                                                    update_upnp(get_ip(), [gv.sd[key]])
                                                if data['sd'][key] != 0:
                                                    update_upnp(get_ip(), [], [[22, data['sd'][key]]])
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
                                    elif key == 'datetime':
                                        try:
                                            pass
#                                            subprocess.call(['date', '--set='+data[grouping][key]])
                                        except:
                                            gv.logger.exception('Could not set datetime to ' + data[grouping][key])
                            if grouping == 'sd':
                                jsave(gv.sd, 'sd')
                            elif grouping != 'other':
                                pass
                        if force_reboot:
                            reboot(5) # give a few seconds before reboot

                    except Exception as ex:
                        gv.logger.info('No master response.  ip: ' + get_ip_to_base() + \
                                       ' port: ' + str(gv.sd['master_port']) + \
                                       ' Exception: ' + str(ex))
                        try:
                            iwout = subprocess.check_output(['iwconfig', 'wlan0'])
                            lines = iwout.split('\n')
                            for l in lines:
                                if 'ESSID:' in l:
                                    gv.logger.info('slave iwconfig wlan0 ' + l[l.find('ESSID:'):])
                        except Exception as ex:
                            gv.logger.info('slave could not check iwconfig: ' + str(ex))

                if gv.sd['master']:
                    for subid in range(1,len(gv.plugin_data['su']['subinfo'])):
                        sub = gv.plugin_data['su']['subinfo'][subid]
                        try:
                            if sub['status'] != 'unreachable' and gv.now - sub['last_join'] >= 90: # if havent received join in a while reach out
                                qd = {'substation':subid}
                                load_and_save_remote(qd, [], 'susldr', 'data', {}) # touch a slave to ensure still alive
                        except Exception as ex:
                            gv.logger.info('substations reach out to slave: No response from slave: ' +
                                           sub['name'] + ' Exception: ' + str(ex))
                            sub['status'] = 'unreachable'
                            try:
                                iwout = subprocess.check_output(['iwconfig', 'wlan0'])
                                lines = iwout.split('\n')
                                for l in lines:
                                    if 'ESSID:' in l:
                                        gv.logger.info('master iwconfig wlan0 ' + l[l.find('ESSID:'):])
                            except Exception as ex:
                                gv.logger.info('master could not check iwconfig: ' + str(ex))

            except Exception as ex:
                self.start_status('', 'Substation encountered error: ' + str(ex))

            self._sleep(30)

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
        for subid in range(1,len(gv.plugin_data['su']['subinfo'])):
            try:
                qd = {'substation':subid}
                load_and_save_remote(qd, [], 'susldr', 'data', {})
            except Exception as ex:
                gv.logger.info('view_substations: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
        return template_render.substations(head, cont, gv.plugin_data['su']['subinfo'])

class delete_substation(ProtectedPage):
    """ Delete a substation, and regenerate links to perform an operation on a slave."""

    def GET(self):
        qdict = web.input()
        head = '' if 'head' not in qdict else qdict['head']
        cont = '' if 'continuation' not in qdict else qdict['continuation']
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if 0 < subid < len(gv.plugin_data['su']['subinfo']):
            gv.logger.info('delete_substation: Deleting substation: ' + gv.plugin_data['su']['subinfo'][subid]['name'])
            del gv.plugin_data['su']['subinfo'][subid]
            del gv.plugin_data['su']['subdesc'][subid]
            if subid == gv.substation_index:
                if len(gv.plugin_data['su']['subinfo']) > 1:
                    gv.substation = gv.plugin_data['su']['subinfo'][1]['name']
                    gv.substation_index = 1
                else:
                    gv.substation = ''
                    gv.substation_index = 0
            if 'te' in gv.plugin_data and 'tesubstatus' in gv.plugin_data['te']:
                del gv.plugin_data['te']['tesubstatus'][subid]
                jsave(gv.plugin_data['te'], 'text_email')
            su_strip = gv.plugin_data['su'].copy()
            del su_strip['subdesc'] # dont save active data
            jsave(su_strip, 'substations')
        raise web.seeother('/suslv?head='+head+'&continuation='+cont)

class set_substation(ProtectedPage):
    """ Set gv.substation for keying future operations based on that substation."""

    def GET(self):
        qdict = web.input()
        subname = '' if 'substationname' not in qdict else qdict['substationname']
        try:
            url = qdict['url']
            unreach = 'unreachable'
            if url[-len(unreach):] == unreach: # do not revisit the unreachable page when changing station
                url = ''
        except:
            url = ''
        found = False
        try:
            for i in range(1,len(gv.plugin_data['su']['subinfo'])):
                if gv.plugin_data['su']['subinfo'][i]['name'] == subname:                
                    gv.substation = subname
                    gv.substation_index = i
                    found = True
                    break
        except:
            pass

        if not found:
            gv.logger.info('set_substation failed to find match for: ' + subname)
            gv.substation = ''
            gv.substation_index = 0
        raise web.seeother(url)

class execute_substation(ProtectedPage):
    """ Generate links to perform an operation on a slave."""

    def GET(self):
        qdict = web.input()
        cont = '' if 'continuation' not in qdict else qdict['continuation']
        subid = 0 if 'substation' not in qdict else qdict['substation']
        raise web.seeother('/'+cont+'?substation='+str(subid))

# unprotected page that must do its own security check
class join_master(WebPage):
    """ Capture a slave that is part of the group and return common data"""

    def GET(self):
        qdict = web.input()
        try:
            ddict = json.loads(qdict['data'])
        except:
            raise web.unauthorized()

        validate_remote(ddict) # may raise unauthorized
        ddict['last_join'] = gv.now
        ddict['status'] = 'ok'

#        gv.logger.debug('joining ip: ' + ddict['ip'] + ' name: ' + ddict['name'] + ' proxy: ' + ddict['proxy'])
        found_slave = 0
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
                found_slave = i
                break

        if found_slave == 0:
            gv.logger.info('join_master adding substation: ' + ddict['name'] + ' at ip: ' + ddict['ip'] + ' proxy: ' + ddict['proxy'])
            gv.plugin_data['su']['subinfo'].append(ddict)
            found_slave = len(gv.plugin_data['su']['subinfo']) - 1
            if gv.substation == '':
                gv.substation = ddict['name']
                gv.substation_index = found_slave
            gv.plugin_data['su']['subdesc'].append(gv.plugin_data['su']['subdesc'][0].copy())
            su_strip = gv.plugin_data['su'].copy()
            del su_strip['subdesc'] # dont save active data
            jsave(su_strip, 'substations')

        result = {'sd':{}, 'other':{}}
        web.header('Content-Type', 'application/json')
        for entry in gv.options:
            f = entry[2]
            if f in ['name', 'htp', 'nbrd', 'opw', 'npw', 'cpw', 'external_htp', 'enable_upnp', 'remote_support_port']:
                continue
            if len(f) > 2 and (f[0:2] == 'te' or f[0:2] == 'et'): # filter out email and et stuff unless explicitly added below
                continue
            result['sd'][f] = gv.sd[f]
        for f in ['tu', 'password', 'salt', 'wl_et_weather', 'teprogramrun', 'teipchange', 'tepoweron']:
            result['sd'][f] = gv.sd[f]

        result['other']['websession'] = web.config._session.user # capture login and logout
        result['other']['datetime'] = time.strftime("%a %d %b %Y %H:%M:%S", time.localtime())

        return json.dumps(result)

class slave_data_request(ProtectedPage):
    """Provide data to the master as the response if this looks like the master."""

    def GET(self):
        qdict = web.input()
        try:
            ddict = json.loads(qdict['data'])
        except:
            raise web.unauthorized()

        force_reboot = False
        info = {}
        for l in ['wlog', 'elog', 'slog', 'evlog']:
            if l in ddict and ddict[l]:
                end = ddict['end_date']
                days = ddict['days_before']
                prefix = ''
                try:
                    if l in ['slog', 'evlog']:
                        prefix = 'sensors/' + gv.plugin_data['ld'][int(ddict['ldi'])]['name'] + '/logs/'
                except:
                    pass
                records = read_log(prefix+l, end, days)
                info[l] = records

        for dlog in ['dlogirricloud', 'dlogirricloud_monitor', 'dlogirricloud_net_config']:
            if dlog in ddict and ddict[dlog]:
                records = []
                try:
                    with open('./logs/' + dlog[4:] + '.out', 'r') as file:
                        records = file.readlines()
                except:
                    pass
                info[dlog] = []
                for r in records:
                    info[dlog].append(r)

        if 'lrun' in ddict and ddict['lrun']:
            info['lrun'] = gv.lrun

        if 'programs' in ddict:
            if ddict['programs'] == 1:
                info['programs'] = gv.pd
            else:
                gv.pd = ddict['programs']
                jsave(gv.pd, 'programs')
                info['programs'] = 1

        if 'ps' in ddict and ddict['ps']:
            info['ps'] = gv.ps

        if 'sbits' in ddict and ddict['sbits']:
            info['sbits'] = gv.sbits

        if 'srvals' in ddict and ddict['srvals']:
            info['srvals'] = gv.srvals

        if 'sensors' in ddict and 'ld' in gv.plugin_data:
            if ddict['sensors'] == 1:
                info['sensors'] = []
                try:
                    if 'ldi' not in ddict or int(ddict['ldi']) == -1:
                        info['sensors'] = gv.plugin_data['ld']
                    else:
                        info['sensors'] = gv.plugin_data['ld'][int(ddict['ldi'])]
                except:
                    pass
                info['sensboards'] = i2c.get_vsb_boards().keys()
                info['remotesensboards'] = get_remote_sensor_boards()
            else:
                try:
                    if 'ldi' not in ddict or int(ddict['ldi']) == -1:
                        gv.plugin_data['ld'] = ddict['sensors']
                    else:
                        gv.plugin_data['ld'][int(ddict['ldi'])] = ddict['sensors']
                except:
                    gv.plugin_data['ld'] = ddict['sensors']
                jsave(gv.plugin_data['ld'], 'sensors')
                info['sensors'] = 1

        if 'camera' in ddict and 'ca' in gv.plugin_data:
            if ddict['camera'] == 1:
                info['camera'] = gv.plugin_data['ca']
                if 'cai' in ddict and ddict['cai']:
                    info['cai'] = ''
                    if gv.plugin_data['ca']['enable_camera'] == 'on':
                        try:
                            with open('./static/images/camera.jpg', mode='rb') as file: # b is important -> binary
                                info['cai'] = base64.b64encode(file.read())
                        except:
                            pass
            else:
                gv.plugin_data['ca'] = ddict['camera']
                jsave(gv.plugin_data['ca'], 'camera')
                info['camera'] = 1

        if 'sd' in ddict:
            if ddict['sd'] == 1:
                sd = gv.sd.copy()
                del sd['substation_network']
                del sd['salt']
                del sd['password']
                del sd['pwd']
                del sd['enable_upnp'] # stuff from base configuration stays as was
                del sd['subnet_only_substations']
                del sd['external_proxy_port']
                kill_keys = []
                for k,v in sd.iteritems():
                    if len(k) > 2 and (k[0:2] == 'te' or k[0:2] == 'et'):
                        kill_keys.append(k)
                for k in kill_keys:
                    del sd[k] # dont send textemail or et_weather stuff
                info['sd'] = sd
            else:
                for field in ddict['sd']:
                    gv.sd[field] = ddict['sd'][field]
                update_hostname(gv.sd['name'])
                update_tza(gv.sd['tza'])
                jsave(gv.sd, 'sd')
                info['sd'] = 1
                force_reboot = True

        if 'snames' in ddict:
            if ddict['snames'] == 1:
                info['snames'] = gv.snames
            else:
                gv.snames = ddict['snames']
                jsave(gv.snames, 'snames')
                info['snames'] = 1

        if 'snotes' in ddict:
            if ddict['snotes'] == 1:
                info['snotes'] = gv.snotes
            else:
                gv.snotes = ddict['snotes']
                jsave(gv.snotes, 'snotes')
                info['snotes'] = 1

        web.header('Content-Type', 'application/json')
        ret_str = json.dumps(info)
        if force_reboot:
            reboot(5)  # give a few seconds to reply
        return ret_str

class slave_iface(WebPage):
    """Provide data to the master as the response if this looks like the master."""

    def GET(self):
        qdict = web.input()
        remote = web.ctx.env['REMOTE_ADDR']
#        print 'slave iface remote: ' + remote + ' qdict: ' + str(qdict)
        if remote != '127.0.0.1':
            gv.logger.info('slave_iface invalid remote: ' + remote)
            raise web.unauthorized()

        gv.radio_iface = qdict['radio']
        gv.vpn_iface = qdict['vpn']

class receive_remote_sensor_data(WebPage):
    """Save remote sensor data so that it can be accessed by plugins/sensors.py.
       Return the current list of radio names associated with zones"""

    def GET(self):
        qdict = web.input()

        #ensure request is from localhost and we trust it
        remote = web.ctx.env['REMOTE_ADDR']
        if remote != '127.0.0.1':
            gv.logger.info('receive_remote_sensor_data invalid remote: ' + remote)
            raise web.unauthorized()

        remote_sensor_name = ''
        try:
            remote_sensor_name = qdict['name']
            del qdict['name']
            if remote_sensor_name in gv.remote_sensors:
                sensor_data = gv.remote_sensors[remote_sensor_name]
            else:
                sensor_data = {}
            for key in qdict:
                sensor_data[key] = int(qdict[key], 0)
            sensor_data['time'] = gv.now
            gv.remote_sensors[remote_sensor_name] = sensor_data
        except:
            gv.logger.exception('receive_remote_sensor_data name: ' + remote_sensor_name + ' qdict: ' + str(qdict))

        zone_list = []
        for z in gv.remote_zones:
            zone_list.append(z)
        web.header('Content-Type', 'application/json')
        return json.dumps(zone_list)

class remote_zone_data(WebPage):
    """Send the zone data (or -1 if no zone data) for the specified remote radio."""

    def GET(self):
        qdict = web.input()

        #ensure request is from localhost and we trust it
        remote = web.ctx.env['REMOTE_ADDR']
        if remote != '127.0.0.1':
            gv.logger.info('remote_zone_data invalid remote: ' + remote)
            raise web.unauthorized()

        remote_zones = -1
        try:
            remote_sensor_name = qdict['name']
            remote_zones = gv.remote_zones[remote_sensor_name]
        except:
            pass
#            gv.logger.error('remote_zone_data name: ' + remote_sensor_name + ' qdict: ' + str(qdict))
        return json.dumps({'zones':remote_zones})


################################################################################
# Helper functions:                                                            #
################################################################################
