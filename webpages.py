# -*- coding: utf-8 -*-

import os
import re
import time
import datetime
import web
import io
import ast

import gv
from helpers import *
from gpio_pins import set_output
from sip import template_render
from blinker import signal
import subprocess
import pytz
import urllib
import urllib2
from Crypto.Cipher import AES
import binascii

loggedin = signal('loggedin')
def report_login():
    loggedin.send()

value_change = signal('value_change')
def report_value_change():
    value_change.send()

option_change = signal('option_change')
def report_option_change():
    option_change.send()

station_names = signal('station_names')
def report_station_names():
    station_names.send()

program_change = signal('program_change')
def report_program_change():
    program_change.send()

program_deleted = signal('program_deleted')
def report_program_deleted():
    program_deleted.send()

program_toggled = signal('program_toggled')
def report_program_toggle():
    program_toggled.send()

### Web pages ######################

class WebPage(object):
    def __init__(self):
        gv.cputemp = get_cpu_temp()


class ProtectedPage(WebPage):
    def __init__(self):
        check_login(True)
        WebPage.__init__(self)

class login(WebPage):
    """Login page"""

    def GET(self):
        return template_render.login(signin_form())

    def POST(self):
        my_signin = signin_form()
        if not my_signin.validates():
            gv.logger.info('login failed')
            return template_render.login(my_signin)
        else:
            gv.logger.info('login succeeded')
            web.config._session.user = 'admin'
            report_login()
            raise web.seeother('/')

class logout(ProtectedPage):
    def GET(self):
        web.config._session.user = 'anonymous'
        raise web.seeother('/')

class shutdown_all(ProtectedPage):
    def GET(self):
        propagate_to_substations('shutdown_all')
        poweroff()
        raise web.seeother('/')

class reboot_all(ProtectedPage):
    def GET(self):
        propagate_to_substations('reboot_all')
        reboot()
        raise web.seeother('/reboot')

class sw_restart(ProtectedPage):
    """Restart system."""

    def GET(self):
        restart(1)
        return template_render.restarting()

class sw_unreachable(ProtectedPage):
    """Failed to reach slave."""

    def GET(self):
        return template_render.unreachable()

###########################
#### Class Definition Support ####

def encrypt_name(name):
    # encrypt name (make sure it is multiple of 16bytes)
    encryption_suite = AES.new(gv.substation_network_hash, AES.MODE_CBC, 'This is a 16B iv')
    pad = len(name) % 16
    if pad > 0:
        for i in range(16-pad):
            name += ' '
    enc_name = binascii.hexlify(encryption_suite.encrypt(name))
    return enc_name

def decrypt_name(enc_name):
    decryption_suite = AES.new(gv.substation_network_hash, AES.MODE_CBC, 'This is a 16B iv')
    sec_str = str(binascii.unhexlify(enc_name))
    plain_name = decryption_suite.decrypt(sec_str)
    return plain_name

def get_ip_for_base():
    base_ip = get_ip_to_base()
    if '10.1.128.' in base_ip:
        ten,one,base,s1 = split_ip(base_ip)
        base_ip = '10.1.0.' + s1
    gv.logger.debug('get_ip_for_base base_ip: ' + base_ip)
    return base_ip

def get_ip_to_base():
    base_ip = '127.0.0.1'
    if gv.sd['master_ip']:
        if gv.sd['master_ip'].upper() != 'LOCALHOST':
            if gv.sd['slave'] and not gv.sd['master']:
                base_ip = get_ip(gv.vpn_iface)
                ten,base,s0,s1 = split_ip(base_ip)
                base_ip = '10.1.128.' + s1
            else:
                base_ip = gv.sd['master_ip']
    gv.logger.debug('get_ip_to_base base_ip: ' + base_ip)
    return base_ip

def message_base(cmd, parameters={}):
    """Send cmd to base possibly via proxy and return object"""

    enc_name = encrypt_name(gv.sd['name'])
    radio_ip = get_ip(gv.radio_iface)
    ten,base,s0,s1 = split_ip(radio_ip)
    gv.logger.debug('message_base radio_ip: ' + radio_ip)
    if (s0 != '254' or s1 != '1') and ten == '10' and int(base) > 1:
        urlcmd = 'http://10.' + base + '.254.1:9080'
        info = {'ip': radio_ip, 'port':gv.sd['htp'], 'name':gv.sd['name'], 'proxy':'', 'security':enc_name}
        info.update(parameters)
        urlcmd += '/supro?command='+cmd+'&parameters=' + urllib.quote_plus(json.dumps(info))
    elif gv.sd['master_ip']:
        wifi_ip = get_ip_to_base()
        wifi_port = gv.sd['htp']
        if wifi_ip == '127.0.0.1':
            urlcmd = 'http://localhost'
            if gv.sd['htp'] != 0 and gv.sd['htp'] != 80:
                urlcmd += ':' + str(gv.sd['htp'])
        else:
            urlcmd = 'http://' + wifi_ip
            if gv.sd['master_port'] != 0 and gv.sd['master_port'] != 80:
                urlcmd += ':' + str(gv.sd['master_port'])
        info = {'ip': get_ip_for_base(), 'port':wifi_port, 'name':gv.sd['name'], 'proxy':'', 'security':enc_name}
        info.update(parameters)
        urlcmd += '/'+cmd+'?data=' + urllib.quote_plus(json.dumps(info))
    else:
        gv.logger.critical('No master for message_base.  radio_ip: ' + radio_ip + ' cmd: ' + cmd)
    try:
        gv.logger.debug('message_base trying command: ' + urlcmd)
        datas = urllib2.urlopen(urlcmd, timeout=gv.url_timeout+8)
    except urllib2.URLError:
        if cmd == 'suslj':
            gv.logger.info('suslj failed')
        gv.logger.info('timeout response.  urlcmd: ' + urlcmd)
        raise IOError, 'UnreachableMaster'
    except Exception as ex:
        gv.logger.info('Unexpected urllib2 error.  Exception ' + str(ex) + ' urlcmd: ' + urlcmd)
        raise IOError, 'UnexpectedResponse'

    data = json.load(datas)
    return data

def validate_remote(ddict):
    "raise unauthorized if not message from valid remote.  Otherwise return quietly"""

    for p in ['name', 'ip', 'port', 'proxy', 'security']:
        if p not in ddict:
            raise web.unauthorized()

    remote = web.ctx.env['REMOTE_ADDR']
    if remote != ddict['ip'] and remote != '127.0.0.1' and '10.1.128.' not in remote:
        gv.logger.critical('validate_remote passed in ip: ' + ddict['ip'] + ' does not match remote: ' + remote)
        raise web.unauthorized()

    try:
        plain_name = decrypt_name(ddict['security'])
    except Exception as ex:
        gv.logger.info('validate_remote security string not decrypted ex: ' + str(ex))
        raise web.unauthorized()
        
    if len(ddict['name']) == 0 or plain_name[:len(ddict['name'])] != ddict['name']:
        gv.logger.critical('validate_remote failed security test: ' + ddict['security'] + ' ip: ' + ddict['ip'] + \
                           ' remote: ' + remote + ' name: ' + ddict['name'])
        raise web.unauthorized()

def extra_timeout(urlcmd):
    timeout_adder = 0
    # if radio address (direct or proxied) bump up timeout
    if ('=10.' in urlcmd and '=10.1.' not in urlcmd) or \
       ('/10.' in urlcmd and '/10.1.' not in urlcmd) or \
       ('proxy%22%3A+%2210.' in urlcmd and 'proxy%22%3A+%2210.1.' not in urlcmd):
        timeout_adder += 20
    hops = 0 if '127.0.0.1' in urlcmd else 1
    if 'proxyaddress' in urlcmd:
        hops += urlcmd.count(';')
    timeout_adder += 4*hops

    # adjust timeout based on approximation to amount of log data
    log_count = 0
    for l in ['wlog', 'elog', 'slog', 'evlog']:
        log_count += 1 if l in urlcmd else 0
    if gv.sd['lr'] == 0:
        timeout_adder += log_count * 10
    elif gv.sd['lr'] > 0:
        timeout_adder += log_count * min(10, 1 + gv.sd['lr']//100)

    # sw update?
    timeout_adder += 20 if 'update_status' in urlcmd else 0

    # camera image?
    timeout_adder += 40 if 'cai' in urlcmd else 0
    timeout_adder += 40 if 'cap' in urlcmd else 0

    return timeout_adder

def load_and_save_remote(qdict, save_list, cmd, first_param_name, first_param):
    try:
        subid = int(qdict['substation'])
    except:
        subid = 0
    sub = gv.plugin_data['su']['subinfo'][subid]

    try:
        if sub['status'] != 'ok': # wait for next join if not ok
            gv.logger.debug('substation status not ok: ' + sub['name'])
            raise IOError, 'UntriedRemote'
    except IOError:
        raise
    except:
        gv.logger.debug('substation status does not exist: ' + sub['name'])
        raise

    urlcmd = 'http://' + sub['ip']
    if 'port' in sub and sub['port'] != 80 and sub['port'] != 0:
        urlcmd += ":" + str(sub['port'])
    if sub['proxy'] != '':
        urlcmd += '/supri?proxyaddress='+urllib.quote_plus(sub['proxy']) + '&' + 'proxycommand=' + cmd + '&'
    else:
        urlcmd += '/'+cmd + '?'
    urlcmd += first_param_name + '='
    if type(first_param) is str:
        urlcmd += urllib.quote_plus(first_param)
    else:
        o = json.dumps(first_param)
        urlcmd += urllib.quote_plus(o)
    for key in qdict:
        if key == 'substation':
            continue
        if type(qdict[key]) is not list:
            urlcmd += '&' + key + '=' + urllib.quote_plus(qdict[key])
        else:
            for elem in qdict[key]:
                urlcmd += '&' + key + '=' + urllib.quote_plus(elem)
    timeout_adder = extra_timeout(urlcmd)
    gv.logger.debug('load_and_save_remote urlcmd: ' + urlcmd + ' timeout: ' + str(gv.url_timeout+timeout_adder))
    try:
        data = urllib2.urlopen(urlcmd, timeout=gv.url_timeout+timeout_adder)
    except urllib2.URLError:
        sub['status'] = 'unreachable'
        gv.logger.info('load_and_save_remote unreachable urlcmd: ' + urlcmd)
        raise IOError, 'UnreachableRemote'
    except Exception as ex:
        sub['status'] = 'unreachable'
        gv.logger.exception('load_and_save_remote unexpected exception: ' + urlcmd)
        raise IOError, 'UnreachableRemote'

    try:
        data = json.load(data)
    except ValueError:
        print 'bad response urlcmd: ', urlcmd, ' data: ', data
        raise IOError, 'BadRemoteResponse'
    except Exception as ex:
        sub['status'] = 'unreachable'
        gv.logger.exception('load_and_save_remote unexpected json data: ')
        raise IOError, 'BadJSONResponse'

    if 'unreachable' in data and data['unreachable']:
        sub['status'] = 'unreachable'
        raise IOError, 'UnreachableRemote'
    else:
        sub['status'] = 'ok'
    for key in save_list:
        gv.plugin_data['su']['subdesc'][subid][key] = data[key]
    return subid, data

def process_page_request(routine, qdict):
    """ Return true and log if we directly process a request (as slave or non-forwarded master"""

    if not gv.sd['master'] or \
       ('substation' in qdict and int(qdict['substation']) == 0):
        more = 'empty' if 'substation' not in qdict else qdict['substation']
        gv.logger.debug(routine + ' - substation: ' + more)
        return True
    return False

temporary_program = ['no_name']
def extract_program(pid, pd, use_temporary_program):
    mp = ['name_to_drop']
    if use_temporary_program:
        mp = temporary_program
    elif pid != -1:
        mp = pd[pid][:]  # Modified program
    mp = mp[0:len(mp)-1] # drop program name
    if len(mp) > 0 and mp[gv.p_day_mask] >= 128 and mp[gv.p_interval_day] > 1:  # If this is an interval program
        dse = gv.now//86400
        # Convert absolute to relative days remaining for display
        rel_rem = (((mp[gv.p_day_mask] - 128) + mp[gv.p_interval_day]) - (dse % mp[gv.p_interval_day])) % mp[gv.p_interval_day]
        mp[gv.p_day_mask] = rel_rem + 128  # Update from saved value.
    prog = str(mp).replace(' ', '')
    return prog

###########################
#### Class Definitions ####

class ping(WebPage):
    """Just return empty list to show reachable."""

    def GET(self):
        qdict = web.input()
        web.header('Content-Type', 'application/json')
        return json.dumps([])

class home(ProtectedPage):
    """Open Home page."""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        if process_page_request('home', qdict):
            return template_render.home(0, gv.snames, gv.sd, gv.ps, gv.pd, gv.lrun)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Status&continuation=home')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'snames'], 'susldr', 'data', {'sd':1, 'snames':1, 'ps':1, 'programs':1, 'lrun':1})
                return template_render.home(subid, data['snames'], data['sd'], data['ps'], data['programs'], data['lrun'])
            except Exception as ex:
                gv.logger.info('home: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

class change_values(ProtectedPage):
    """Save controller values, return browser to home page."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('change_values', qdict) or subid == 0:
            if 'rsn' in qdict and qdict['rsn'] == '1':
                stop_stations()
                if 'substation' in qdict:
                    web.header('Content-Type', 'application/json')
                    return json.dumps([])
                else:
                    raise web.seeother('/')
            if 'en' in qdict and qdict['en'] == '':
                qdict['en'] = '1'  # default
            elif 'en' in qdict and qdict['en'] == '0':
                stop_stations()
            if 'mm' in qdict and qdict['mm'] == '0':
                clear_mm()
            if 'rd' in qdict and qdict['rd'] != '0' and qdict['rd'] != '':
                gv.sd['rd'] = int(float(qdict['rd']))
                gv.sd['rdst'] = int(gv.now + gv.sd['rd'] * 3600) # +1 adds a smidge just so after a round trip the display hasn't already counted down by a minute.
                stop_onrain()
            elif 'rd' in qdict and qdict['rd'] == '0':
                gv.sd['rdst'] = 0
            for key in qdict.keys():
                try:
                    gv.sd[key] = int(qdict[key])
                except Exception:
                    pass
            jsave(gv.sd, 'sd')
            report_value_change()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cv', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_values: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/')

class view_options(ProtectedPage):
    """Open the options page for viewing and editing."""

    def GET(self):
        qdict = web.input()
        errorCode = "none"
        if 'errorCode' in qdict:
            errorCode = qdict['errorCode']

        tzdict = {}
        for tz in pytz.common_timezones:
            tzdict[tz] = None
        return template_render.options(errorCode, tzdict)

class change_options(ProtectedPage):
    """Save changes to options made on the options page."""

    def GET(self):
        qdict = web.input()
        if 'opw' in qdict and qdict['opw'] != "":
            try:
                if password_hash(qdict['opw'], gv.sd['salt']) == gv.sd['password']:
                    if qdict['npw'] == "":
                        raise web.seeother('/vo?errorCode=pw_blank')
                    elif qdict['cpw'] != '' and qdict['cpw'] == qdict['npw']:
                        gv.sd['salt'] = password_salt()  # Make a new salt
                        gv.sd['password'] = password_hash(qdict['npw'], gv.sd['salt'])
                    else:
                        raise web.seeother('/vo?errorCode=pw_mismatch')
                else:
                    raise web.seeother('/vo?errorCode=pw_wrong')
            except KeyError:
                pass

        for f in ['tesmsprovider', 'tesmsnbr', 'teadr']:
            for i in range(5):
                if 'o'+f+str(i) in qdict:
                    gv.sd[f+str(i)] = qdict['o'+f+str(i)]
                else:
                    gv.sd[f+str(i)] = ""

        # dont require reentering password if user is unchanged and password field is empty
        if 'oteuser' in qdict and qdict['oteuser'] != '':
            if gv.sd['teuser'] != qdict['oteuser']:
                gv.sd['tepassword'] = ''
            gv.sd['teuser'] = qdict['oteuser']
            if 'tepassword' in qdict and qdict['tepassword'] != '':
                gv.sd['tepassword'] = qdict['tepassword']
        else:
            gv.sd['teuser'] = ''
            gv.sd['tepassword'] = ''

         # only change name on configuration
#        if 'oname' in qdict:
#            fqdn = validate_fqdn(qdict['oname'])
#            if fqdn != 'Irricloud' and fqdn != gv.sd['name'] and qdict['oname'] != gv.sd['name']: # change??
#                gv.sd['name'] = fqdn
#                update_hostname(fqdn)
#                qdict['rbt'] = '1'  # force reboot with change

        for f in ['etapi', 'mode']:
            if 'o'+f in qdict:
                gv.sd[f] = qdict['o'+f]

        for f in ['loc', 'lang']:
            if 'o'+f in qdict:
                if f not in gv.sd or gv.sd[f] != qdict['o'+f]:
                    qdict['rbt'] = '1'  # force reboot with change (was restart)
                gv.sd[f] = qdict['o'+f]

        for f in ['tza']:
            if 'o'+f in qdict:
                if f not in gv.sd or gv.sd[f] != qdict['o'+f]:
                    update_tza(qdict['o'+f])
                    qdict['rbt'] = '1'  # force reboot with change
                gv.sd[f] = qdict['o'+f]

        if gv.sd['enable_upnp']:
            cur_ip = get_ip()
            if gv.sd['remote_support_port'] != 0 and ('oremote_support_port' not in qdict or int(qdict['oremote_support_port']) != gv.sd['remote_support_port']):
                update_upnp(cur_ip, gv.logger, [gv.sd['remote_support_port']])
            if 'oremote_support_port' in qdict and int(qdict['oremote_support_port']) != 0:
                update_upnp(cur_ip, gv.logger, [], [[22, int(qdict['oremote_support_port'])]])
            if gv.sd['master'] and 'external_proxy_port' in qdict and int(qdict['external_proxy_port']) != 0:
                update_upnp(cur_ip, gv.logger, [], [[9081, int(qdict['external_proxy_port'])]])

        for f in ['wl', 'lr', 'etmin', 'etmax', 'ethistory', 'etforecast', 'remote_support_port']:
            if 'o'+f in qdict:
                gv.sd[f] = int(qdict['o'+f])

        for f in ['ipas', 'tf', 'urs', 'seq', 'rst', 'lg', 'etok', 'tepoweron', 'teprogramrun', 'teipchange', 'tesu']:
            if 'o'+f in qdict and (qdict['o'+f] == 'on' or qdict['o'+f] == '1'):
                gv.sd[f] = 1
            else:
                gv.sd[f] = 0

        gv.sd['boiler_supply_temp'] = float(qdict['oboiler_supply_temp'])
        gv.sd['USR_ip'] = qdict['oUSR_ip']
        gv.sd['max_dewpoint'] = float(qdict['omax_dewpoint'])
        try:
            gv.sd['thermostats'] = qdict['otherm_ips'].split(',')
        except:
            gv.sd['thermostats'] = []
        gv.sd['thermostats'] = [t.strip() for t in gv.sd['thermostats']]
        for therm_ip_idx in range(len(gv.sd['thermostats']), 0, -1):
            if not valid_ip(gv.sd['thermostats'][therm_ip_idx-1]):
                del gv.sd['thermostats'][therm_ip_idx-1]
        gv.sd['therm_ips'] = ', '.join(gv.sd['thermostats'])
        try:
            new_base = float(qdict['oetbase'])
            new_weather = gv.sd['wl_et_weather'] * float(gv.sd['etbase'])/new_base
            overall_scale = min(max(new_weather,float(gv.sd['etmin'])), float(gv.sd['etmax']))
            gv.sd['wl_et_weather'] = overall_scale if gv.sd['etok'] else 100
            gv.sd['etbase'] = new_base
        except:
            pass

        jsave(gv.sd, 'sd')
        report_option_change()

        if 'netconfig' in qdict and qdict['netconfig'] == '1':
            gv.logger.info('webpages netconfig')
            subprocess.call(['touch', './data/factory_reset'])

        if 'rshutdown' in qdict and qdict['rshutdown'] != '0':
            if qdict['rshutdown'] == '2':
                propagate_to_substations('shutdown_all')
            poweroff()

        if 'rbt' in qdict and qdict['rbt'] != '0':
            if qdict['rbt'] == '2':
                propagate_to_substations('reboot_all')
            reboot(3)
            raise web.seeother('/reboot')

        if 'rstrt' in qdict and qdict['rstrt'] == '1':
            raise web.seeother('/restart')
        raise web.seeother('/')

class view_stations(ProtectedPage):
    """Open a page to view and edit stations."""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        if process_page_request('view_stations', qdict):
            radioboards = get_remote_sensor_boards()
            if 'localhost' in radioboards: # dont allow local radios for valve control
                radioboards.remove('localhost')
            return template_render.stations(0, radioboards, gv.snames, gv.snotes, gv.sd)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Stations&continuation=vs')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'snames'], 'susldr', 'data', {'sd':1, 'sensors':1, 'snames':1, 'snotes':1})
                if 'localhost' in data['remotesensboards']: # dont allow local radios for valve control
                    data['remotesensboards'].remove('localhost')
                return template_render.stations(subid, data['remotesensboards'], data['snames'], data['snotes'], data['sd'])
            except Exception as ex:
                gv.logger.info('view_stations: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

class change_stations(ProtectedPage):
    """Save changes to station names, ignore rain and master associations."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('change_stations', qdict) or subid == 0:
            for f in ['mas', 'mton', 'mtoff', 'sdt']:
                if 'o'+f in qdict:
                    gv.sd[f] = int(qdict['o'+f])
            for i in range((gv.sd['nst']+7)//8):
                if 'm' + str(i) in qdict:
                    try:
                        gv.sd['mo'][i] = int(qdict['m' + str(i)])
                    except ValueError:
                        gv.sd['mo'][i] = 0
                if 'i' + str(i) in qdict:
                    try:
                        gv.sd['ir'][i] = int(qdict['i' + str(i)])
                    except ValueError:
                        gv.sd['ir'][i] = 0
                if 'w' + str(i) in qdict:
                    try:
                        gv.sd['iw'][i] = int(qdict['w' + str(i)])
                    except ValueError:
                        gv.sd['iw'][i] = 0
                if 'sh' + str(i) in qdict:
                    try:
                        gv.sd['show'][i] = int(qdict['sh' + str(i)])
                    except ValueError:
                        gv.sd['show'][i] = 255
                if 'd' + str(i) in qdict:
                    try:
                        gv.sd['show'][i] = ~int(qdict['d' + str(i)])&255
                    except ValueError:
                        gv.sd['show'][i] = 255
                if 'ss' + str(i) in qdict and qdict['ss'+str(i)] != '':
                    tostop = int(qdict['ss'+str(i)])
                    for j in range(8):
                        if tostop & (1<<j):
                            sid = 8*i + j
                            stop_station(sid, **{'stop_all':1})

            names = []
            notes = []
            for i in range((gv.sd['nst']+7)//8 * 8):
                rsid = i - (gv.sd['nst'] - gv.sd['radiost'])
                if rsid >= 0 and rsid < gv.sd['radiost']:
                    if 'radio_bd' + str(i) in qdict:
                        gv.sd['radio_zones'][rsid]['radio_bd'] = qdict['radio_bd'+str(i)]
                    if 'radio_zone_pos' + str(i) in qdict:
                        gv.sd['radio_zones'][rsid]['zone_pos'] = int(qdict['radio_zone_pos'+str(i)])
                if 's' + str(i) in qdict:
                    names.append(qdict['s'+str(i)])
                elif rsid < 0:
                    names.append('S'+"{:0>2d}".format(i+1))
                else:
                    names.append('R'+"{:0>2d}".format(rsid+1))
                if 'notes' + str(i) in qdict:
                    notes.append(qdict['notes' + str(i)])
                else:
                    notes.append("")

            gv.snames = names
            jsave(names, 'snames')
            gv.snotes = notes
            jsave(notes, 'snotes')
            jsave(gv.sd, 'sd')
            report_station_names()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cs', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_stations: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/')

class change_station_master(ProtectedPage):
    """Save changes to station master and reload stations page."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('change_station_master', qdict) or subid == 0:
            if 'omas' in qdict and gv.sd['mas'] != int(qdict['omas']):
                gv.sd['mas'] = int(qdict['omas'])
                jsave(gv.sd, 'sd')
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'csm', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_station_master: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/vs')

#class change_station_board(ProtectedPage):
#    """Save changes number of boards."""
#
#    def GET(self):
#        qdict = web.input()
#        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
#        if process_page_request('change_station_board', qdict) or subid == 0:
#            onbrd = int(qdict['onbrd'])
#            adjust_gv_nbrd(onbrd)
#            if 'substation' in qdict:
#                web.header('Content-Type', 'application/json')
#                return json.dumps([])
#        else:
#            try:
#                subid, data = load_and_save_remote(qdict, [], 'csb', 'substation', '0')
#            except Exception as ex:
#                gv.logger.info('change_station_board: No response from slave: ' +
#                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
#                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
#                raise web.seeother('/unreachable')
#        raise web.seeother('/vs')

class add_radio_station(ProtectedPage):
    """Bump up the count of radio stations"""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('add_radio_station', qdict) or subid == 0:
            if gv.sd['radiost'] % 8 == 0:
                adjust_gv_nbrd(gv.sd['nst']//8 + 1, True)
            else:
                enable_bit = gv.sd['radiost'] % 8
                index = gv.sd['nst'] // 8
                gv.sd['show'][index] |= 1<<enable_bit # turn on show bit for new radiost
            gv.sd['nst'] += 1
            gv.sd['radiost'] += 1
            gv.sd['radio_zones'].append({'radio_bd':'', 'zone_pos':-1})
            jsave(gv.sd, 'sd')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'ars', 'substation', '0')
            except Exception as ex:
                gv.logger.info('add_radio_station: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        web.header('Content-Type', 'application/json')
        return json.dumps([])

class get_set_station(ProtectedPage):
    """Return a page containing a number representing the state of a station or all stations if 0 is entered as station number."""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        got_data = False
        if subid != 0:
            try:
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'sd':1, 'srvals':1})
                sd = data['sd']
                srvals = data['srvals']
                got_data = True
            except Exception as ex:
                gv.logger.info('get_set_status: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][int(qdict['substation'])]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][int(qdict['substation'])]['status'] = 'unreachable'

        if not got_data:
            sd = gv.sd
            srvals = gv.srvals

        sid = get_input(qdict, 'sid', 0, int) - 1
        set_to = get_input(qdict, 'set_to', None, int)
        set_time = get_input(qdict, 'set_time', 0, int)

        if set_to is None:
            if sid < 0:
                status = '<!DOCTYPE html>\n'
                status += ''.join(str(x) for x in srvals)
                return status
            elif sid < sd['nst']:
                status = '<!DOCTYPE html>\n'
                status += str(srvals[sid])
                return status
            else:
                return _('Station ') + str(sid+1) + _(' not found.')
        elif sd['mm']:
            # todo consider supporting manual mode.
            raise Exception('Manual Mode not supported')
            with gv.rs_lock:
                if set_to:  # if status is
                    gv.rs[sid][gv.rs_start_sec] = gv.now  # set start time to current time
                    if set_time > 0:  # if an optional duration time is given
                        gv.rs[sid][gv.rs_stop_sec] = gv.rs[sid][gv.rs_start_sec] + set_time  # stop time = start time + duration
                    else:
                        gv.rs[sid][gv.rs_stop_sec] = float('inf')  # stop time = infinity ????
                    gv.rs[sid][gv.rs_program_id] = 99  # set program index
                    gv.sd['bsy'] = 1
                else:  # If status is off
                    gv.rs[sid][gv.rs_stop_sec] = gv.now
            time.sleep(1)
            raise web.seeother('/')
        else:
            return _('Manual mode not active.')

class view_runonce(ProtectedPage):
    """Open a page to view and edit a run once program."""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        if process_page_request('view_runonce', qdict):
            return template_render.runonce(0, gv.snames, gv.sd)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=' + urllib.quote_plus('Run Once') + '&continuation=vr')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'snames'], 'susldr', 'data', {'sd':1, 'snames':1})
                return template_render.runonce(subid, data['snames'], data['sd'])
            except Exception as ex:
                gv.logger.info('view_runonce: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

class change_runonce(ProtectedPage):
    """Start a Run Once program. This will override any running program."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('change_runonce', qdict) or subid == 0:
            if not gv.sd['en']:   # check operation status
                if 'substation' in qdict:
                    web.header('Content-Type', 'application/json')
                    return json.dumps([])
                else:
                    raise web.seeother('/')
            gv.rovals = json.loads(qdict['t'])
            gv.rovals.pop()
            stop = 1 if 'stop' not in qdict else json.loads(qdict['stop'])
            if stop:
                stop_stations()
            rs = [[0,0] for i in range(gv.sd['nst'])] # program, duration indexed by station
            for i, v in enumerate(gv.rovals):
                if v:  # if this element has a value
                    rs[i][0] = 98
                    rs[i][1] = v
            schedule_stations(rs, 1 if gv.sd['seq'] else 5)
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cr', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_runonce: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/')

class view_programs(ProtectedPage):
    """Open programs page."""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        if process_page_request('view_programs', qdict):
            return template_render.programs(0, gv.snames, gv.sd, gv.pd, gv.now)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Programs&continuation=vp')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'programs', 'snames'], 'susldr', 'data', {'sd':1, 'snames':1, 'programs':1})
                return template_render.programs(subid, data['snames'], data['sd'], data['programs'], gv.now)
            except Exception as ex:
                gv.logger.info('view_programs: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

class modify_program(ProtectedPage):
    """Open page to allow program modification."""

    def GET(self):
        qdict = web.input()
        pid = int(qdict['pid'])
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        vflags = int(qdict['vflags']) if 'vflags' in qdict else 0
        if process_page_request('modify_program', qdict) or subid == 0:
            prog = extract_program(pid, gv.pd, vflags==2)
            if 'vflags' in qdict and qdict['vflags'] == '2':
                prog_name = temporary_program[-1]
            elif pid == -1:
                prog_name = ''
            else:
                prog_name = gv.pd[pid][-1]
            return template_render.modify(0, gv.snames, gv.sd, pid, prog, prog_name)
        else:
            data = gv.plugin_data['su']['subdesc'][subid] # assume came from view programs
            prog = extract_program(pid, data['programs'], vflags==2)
            if 'vflags' in qdict and qdict['vflags'] == '2':
                prog_name = temporary_program[-1]
            elif pid == -1:
                prog_name = ''
            else:
                prog_name = data['programs'][pid][-1]
            return template_render.modify(subid, data['snames'], data['sd'], pid, prog, prog_name)

class change_program(ProtectedPage):
    """Add a program or modify an existing one.  pid==-1 means new program, pid==-2 means temporary program"""

    def GET(self):
        global temporary_program

        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        pid = int(qdict['pid'])
        vflags = int(qdict['vflags']) if 'vflags' in qdict else 0
        if process_page_request('change_program', qdict) or subid == 0:
            cp = json.loads(qdict['v'])
            if pid >= 0 and pid < len(gv.pd) and vflags == 0: # something that does not understand our flags.  Keep them unchanged.
                cp[gv.p_flags] = cp[gv.p_flags]&1
                old_flags = gv.pd[pid][gv.p_flags]
                new_flags = (gv.pd[pid][gv.p_flags]&(~1)) | cp[gv.p_flags]
                cp[gv.p_flags] = new_flags
                if new_flags != old_flags:
                    gv.logger.debug('Program ' + str(pid+1) + ' changed flags from ' + str(old_flags) + ' to ' + str(new_flags))
                cp_name = gv.pd[pid][-1]
            elif vflags != 2 and ('program_name' not in qdict or qdict['program_name'] == ''):
                cp_name = 'Program'
            else:
                cp_name = qdict['program_name']

            if vflags != 2:
                old_name = '' if pid == -1 else gv.pd[pid][-1]
                # Program names must be unique.  If this name is not unique add a number to the end until it is unique
                duplicate = False
                try:
                    for i in range(len(gv.pd)):
                        if i != pid and gv.pd[i][-1] == cp_name:
                             raise IOError, 'Duplicate Name'
                except IOError:
                    for i in range(len(gv.pd)):
                        try:
                            try_name = cp_name + '_' + str(i+1)
                            for j in range(len(gv.pd)):
                                if j != pid and gv.pd[j][-1] == try_name:
                                    raise IOError, 'Duplicate Name'
                            cp_name = try_name
                            break
                        except IOError:
                            continue
            cp.append(cp_name)

            if cp[gv.p_flags]&1 == 0: # if disabled and program is running or currently delayed
                with gv.rs_lock:
                    for i in range(len(gv.ps)):
                        if gv.ps[i][0] == pid+1:
                            gv.ps[i] = [0, 0]
                            if gv.srvals[i]:
                                gv.srvals[i] = 0
                                set_output()
                    for i in range(len(gv.rs)):
                        for j in range(len(gv.rs[i])-1,0,-1):
                            if gv.rs[i][j]['rs_program_id'] == pid+1:
                                del gv.rs[i][j]

            if cp[gv.p_day_mask] >= 128 and cp[gv.p_interval_day] > 1:
                dse = gv.now//86400
                ref = dse + cp[gv.p_day_mask] - 128
                cp[gv.p_day_mask] = (ref % cp[gv.p_interval_day]) + 128

            if vflags == 2:
                temporary_program = cp
            elif pid == -1:  # add new program
                gv.pd.append(cp)
                pid = len(gv.pd)-1
            else:
                gv.pd[pid] = cp  # replace program
                for i in range(len(gv.recur)-1, -1, -1):
                    e = gv.recur[i]
                    if e[1] == pid:
                        del gv.recur[i]
            if vflags != 2:
                if prog_match(cp, True):
                    schedule_recurring_instances(pid)
                jsave(gv.pd, 'programs')

                changed_sensor = False
                for sens in gv.plugin_data['ld']:
                    for pl in ['trigger_high_program', 'trigger_low_program']:
                        if pl in sens and old_name in sens[pl]:
                            sens[pl].remove(old_name)
                            sens[pl].append(cp[-1])
                            changed_sensor = True

                if changed_sensor:
                    jsave(gv.plugin_data['ld'], 'sensors')

                gv.sd['nprogs'] = len(gv.pd)
                report_program_change()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cp', 'substation', '0')
                if vflags == 2: # need to track what is above since temporary_program is maintained locally
                    cp = json.loads(qdict['v'])
                    cp.append(qdict['program_name'])
                    if cp[gv.p_day_mask] >= 128 and cp[gv.p_interval_day] > 1:
                        dse = gv.now//86400
                        ref = dse + cp[gv.p_day_mask] - 128
                        cp[gv.p_day_mask] = (ref % cp[gv.p_interval_day]) + 128
                    temporary_program = cp
            except Exception as ex:
                gv.logger.info('change_program: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        if vflags == 2:  # back to modifying temporary program
            raise web.seeother('/mp?vflags=2&substation='+str(subid)+'&pid='+str(pid))
        else:
            raise web.seeother('/vp')

class delete_program(ProtectedPage):
    """Delete one or all existing program(s)."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('delete_program', qdict) or subid == 0:
            changed_sensor = False
            if qdict['pid'] == '-1':
                del gv.pd[:]
                del gv.recur[:]
                for sens in gv.plugin_data['ld']:
                    for p in ['trigger_high_program', 'trigger_low_program']:
                        if p in sens:
                            sens[p] = []
                            changed_sensor = True
            else:
                pid = int(qdict['pid'])
                old_name = gv.pd[pid][-1]
                for sens in gv.plugin_data['ld']:
                    for pl in ['trigger_high_program', 'trigger_low_program']:
                        if pl in sens and old_name in sens[pl]:
                            sens[pl].remove(old_name)
                            changed_sensor = True
                for i in range(len(gv.recur),0,-1): # delete entries for current program
                    if gv.recur[i-1][1] == pid:
                        del gv.recur[i-1]
                for i,e in enumerate(gv.recur): # update pid of future recurring programs
                    if e[1] > pid:
                        gv.recur[i][1] -= 1
                del gv.pd[pid]
            jsave(gv.pd, 'programs')
            gv.sd['nprogs'] = len(gv.pd)
            if changed_sensor:
                jsave(gv.plugin_data['ld'], 'sensors')
            report_program_deleted()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'dp', 'substation', '0')
            except Exception as ex:
                gv.logger.info('delete_program: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/vp')

class enable_program(ProtectedPage):
    """Activate or deactivate an existing program(s)."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('delete_program', qdict) or subid == 0:
            if int(qdict['enable']):
                gv.pd[int(qdict['pid'])][gv.p_flags] |= 1
            else:
                gv.pd[int(qdict['pid'])][gv.p_flags] &= ~1
            jsave(gv.pd, 'programs')
            report_program_toggle()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'ep', 'substation', '0')
            except Exception as ex:
                gv.logger.info('enable_program: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/vp')

class view_log(ProtectedPage):
    """View Log"""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        if process_page_request('view_log', qdict):
            watering_records = read_log('wlog')
            email_records = read_log('elog')
            return template_render.log(0, gv.snames, gv.sd, watering_records, email_records)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Log&continuation=vl')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'snames', 'wlog', 'elog'], 'susldr', 'data', {'sd':1, 'snames':1, 'wlog':1, 'elog':1, 'end_date':'', 'days_before':0})
                return template_render.log(subid, data['snames'], data['sd'], data['wlog'], data['elog'])
            except Exception as ex:
                gv.logger.info('view_log: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

class clear_log(ProtectedPage):
    """Delete all log records"""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('clear_log', qdict) or subid == 0:
            if 'kind' not in qdict:
                qdict['kind'] = 'wlog'
            with io.open('./data/'+ qdict['kind'] + '.json', 'w') as f:
                f.write(u'')
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cl', 'substation', '0')
            except Exception as ex:
                gv.logger.info('clear_log: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/vl')

class truncate_log(ProtectedPage):
    """Shorten specified debugging log"""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        kind = qdict['kind']
        trunc_len = int(qdict['truncate'])
        if kind in ['slog', 'evlog']:
            sensid = int(qdict['sensid'])
        if process_page_request('truncate_log', qdict) or subid == 0:
            try:
                lines = []
                if kind in ['wlog', 'elog']:
                    log = read_log(kind)
                    for r in log:
                        lines.append(json.dumps(r) + '\n')
                    fname = './data/' + kind + '.json'
                elif kind in ['slog', 'evlog']:
                    logname = 'sensors/'+gv.plugin_data['ld'][sensid]['name']+'/logs/' + kind
                    log = read_log(logname)
                    for r in log:
                        lines.append(json.dumps(r) + '\n')
                    fname = './data/' + logname + '.json'
                else:
                    fname = './logs/' + kind + '.out'
                    with open(fname, 'r') as file:
                        lines = file.readlines()

                gv.logger.info('truncate_log fname: ' + fname + ' to  ' + str(trunc_len))
                
                keep_lines = []
                cur_len = 0
                if kind in ['wlog', 'elog', 'slog', 'evlog']:
                    # keep first set of records
                    for i in range(len(lines)):
                        if cur_len < trunc_len:
                            cur_len += len(lines[i])
                            keep_lines.append(lines[i])
                        else:
                            break
                else:
                    # keep last set of records
                    for i in range(len(lines),0,-1):
                        if cur_len < trunc_len:
                            cur_len += len(lines[i-1])
                            keep_lines.insert(0, lines[i-1])
                        else:
                            break

                with open(fname, 'w') as file:
                    file.writelines(keep_lines)

            except Exception as ex:
                gv.logger.error('truncate_log exception: ' + str(ex))
                pass

            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'tl', 'substation', '0')
            except Exception as ex:
                gv.logger.info('truncate_log: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        if kind in ['wlog', 'elog']:
            raise web.seeother('/vl')
        elif kind in ['slog', 'evlog']:
            raise web.seeother('/ldvl?sensid='+str(sensid))
        else:
            raise web.seeother('/')

class debug_log(ProtectedPage):
    """Download debugging log to file"""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        kind = qdict['kind']
        records = []
        if process_page_request('debug_log', qdict):
            try:
                with open('./logs/' + kind + '.out', 'r') as file:
                    records = file.readlines()
            except:
                pass
            filename = 'dlog-' + kind
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'dlog'+kind:1})
                records = data['dlog'+kind]
                filename = 'dlog-' + kind + '-' + gv.plugin_data['su']['subinfo'][subid]['name']
            except Exception as ex:
                gv.logger.info('debug_log: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

        filename += '.txt'
        data = ''
        for r in records:
            data += r
        web.header('Content-Type', 'text/html')
        web.header('Content-Disposition', 'attachment; filename="'+filename+'"')
        return data

class save_configuration(ProtectedPage):
    """Download configuration file in format for restoring"""

    def GET(self):
        qdict = web.input()
        datal = ['sd', 'programs', 'sensors', 'camera', 'snames', 'snotes']
        configd = {}
        for fname in datal:
            configd[fname] = 1
        records = {'Irricloud Master Station':{}}
        try:
            for fname in datal:
                with open('./data/' + fname + '.json', 'r') as file:
                    records['Irricloud Master Station'][fname] = json.load(file)
            records['Irricloud Master Station']['sd']['tepassword'] = '' # remove gmail password
            gv.logger.info('save_configuration saving master station ' + gv.sd['name'])
            for subid in range(1,len(gv.plugin_data['su']['subinfo'])):
                name = gv.plugin_data['su']['subinfo'][subid]['name']
                if name == gv.sd['name']:  # do not save ourselves as a substation
                    continue
                records[name] = {}
                qdict['substation'] = str(subid)
                subidx, data = load_and_save_remote(qdict, [], 'susldr', 'data', configd)
                gv.logger.info('save_configuration saving substation ' + name)
                for fname in datal:
                    records[name][fname] = data[fname]
        except Exception as ex:
            gv.logger.info('save_configuration failure: ' + str(ex))
            raise web.seeother('/unreachable')

        filename = 'config.json'
        data = json.dumps(records)
        web.header('Content-Type', 'text/html')
        web.header('Content-Disposition', 'attachment; filename="'+filename+'"')
        return data

class restore_configuration(ProtectedPage):
    """Restore configuration files and reboot"""

    def GET(self):
        qdict = web.input()
        return template_render.restore_config()

    def POST(self):
        qdict = web.input(myfile={})
        sub_name = '' if 'substationname' not in qdict else qdict['substationname']

#        web.debug(qdict['myfile'].filename) # This is the filename
#        web.debug(qdict['myfile'].value) # This is the file contents
        try:
            records = json.loads(qdict['myfile'].value)
        except Exception as ex:
            gv.logger.info('restore configuration file load failure.  Filename: ' + qdict['myfile'].filename + ' exception: ' + str(ex))
            raise web.unauthorized()

        # build map of names in configuration file to valid substation ids
        substation_map = {}
        unreachable_substations = []
        for sub in records:
            if sub == 'Irricloud Master Station':
                continue
            if sub_name == '' or (sub_name == sub and sub != gv.sd['name']):
                unreachable_substations.append(sub)
                for subid in range(1,len(gv.plugin_data['su']['subinfo'])):
                    if gv.plugin_data['su']['subinfo'][subid]['name'] == sub:
                        substation_map[sub] = subid

        unreachable = 0
        for sub in unreachable_substations:
            if sub not in substation_map:
                unreachable += 1
                gv.logger.info('restore configuration unreachable substation: ' + sub)
      
        if sub_name != '':
            if sub_name == gv.sd['name']:
                sub_name = 'Irricloud Master Station'
            elif sub_name not in substation_map:
                gv.logger.info('restore_configuration could not find sub_name: ' + sub_name + ' to restore')
                raise web.seeother('unreachable')
        elif unreachable != 0:
            gv.logger.info('restore_configuration Abandoning restoration due to ' + str(unreachable) + ' unreachable substations. ')
            raise web.seeother('unreachable')
            
        datal = ['sd', 'programs', 'sensors', 'camera', 'snames', 'snotes']
        for sub in records:
            if sub_name != '' and sub_name != sub: # only restore sub_name if present
                continue
            if sub == 'Irricloud Master Station':
                for fname in datal:
                    rm = records['Irricloud Master Station']
                    if fname == 'sd':
                        gv.sd = rm[fname]
                        update_hostname(gv.sd['name'])
                        update_tza(gv.sd['tza'])
                    elif fname == 'programs':
                        gv.pd = rm[fname]
                    elif fname == 'camera':
                        gv.plugin_data['ca'] = rm[fname]
                    elif fname == 'sensors':
                        gv.plugin_data['ld'] = rm[fname]
                    elif fname == 'snames':
                        gv.snames = rm[fname]
                    elif fname == 'snotes':
                        gv.snotes = rm[fname]
                    else:
                        gv.logger.critical('unexpected configuration file to restore: ' + fname)
                    jsave(rm[fname], fname)
                    gv.logger.info('restore_configuration restored master ' + fname)
                gv.logger.info('restore_configuration master restoration complete.')
            else:
                subid = substation_map[sub]
                configd = {}
                for fname in datal:
                    configd[fname] = records[sub][fname]

                qdict = {}
                qdict['substation'] = str(subid)
                try:
                    subidx, data = load_and_save_remote(qdict, [], 'susldr', 'data', configd)
                    for fname in datal:
                        if data[fname] != 1:
                            gv.logger.critical('restore_configuration failed to restore ' + fname + ' on substation ' + sub)
                    gv.logger.info('restore_configuration restored substation ' + sub)
                except Exception as ex:
                    gv.logger.critical('restore_configuration: No response from slave: ' + sub + ' Exception: ' + str(ex))
                    raise web.seeother('/unreachable')

        if sub_name == '' or sub_name == 'Irricloud Master Station':
            gv.logger.info('restore_configuration restoration complete...rebooting')
            reboot(3)
            raise web.seeother('/reboot')
        else:
            gv.logger.info('restore_configuration restoration complete...home page')
            raise web.seeother('/')

class water_log(ProtectedPage):
    """Download water log to file"""

    def GET(self):
        # assume only reasonable way to get here is to have just done a view log, so
        # most recent saved log data is what we will dump.
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('water_log', qdict) or subid == 0:
            filename = 'watering-log'
            records = read_log('wlog')
        else:
            filename = 'watering-log-' + gv.plugin_data['su']['subinfo'][subid]['name']
            records = gv.plugin_data['su']['subdesc'][subid]['wlog']
        filename += '.csv'

#        data = _("Date, Start Time, Zone, Duration, Program Number, Program Name") + "\n"
#        for r in records:
#            event = ast.literal_eval(json.dumps(r))
#            data += event["date"] + ", " + event["start"] + ", " + str(event["station"]+1) + ", " + \
#                    event["duration"] + ", " + event["program"] + ", " + event["programname"] + "\n"
        data = _("Time, Date, Mode, Message") + "\n"
        for r in records:
            event = ast.literal_eval(json.dumps(r))
            data += event["time"] + ", " + event["date"] + ", " + \
                    event["mode"] + ", " + event["message"] + "\n"

        web.header('Content-Type', 'text/csv')
        web.header('Content-Disposition', 'attachment; filename="'+filename+'"')
        return data

class email_log(ProtectedPage):
    """Download email log to file"""

    def GET(self):
        # assume only reasonable way to get here is to have just done a view log, so
        # most recent saved log data is what we will dump.
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('email_log', qdict) or subid == 0:
            filename = 'email-log'
            records = read_log('elog')
        else:
            filename = 'email-log-' + gv.plugin_data['su']['subinfo'][subid]['name']
            records = gv.plugin_data['su']['subdesc'][subid]['elog']
        filename += '.csv'

        data = _("Date, Time, Subject, Body") + "\n"
        for r in records:
            event = ast.literal_eval(json.dumps(r))
            data += event["date"] + ", " + event["time"] + ", " + event["subject"] + ", " + event["body"] + "\n"

        web.header('Content-Type', 'text/csv')
        web.header('Content-Disposition', 'attachment; filename="'+filename+'"')
        return data

class run_now(ProtectedPage):
    """Run a scheduled program now. This will override any running programs."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('run_now', qdict) or subid == 0:
            stop = 1 if 'stop' not in qdict else json.loads(qdict['stop'])
            if stop:
                stop_stations()
            pid = int(qdict['pid'])
            run_program(pid, True) # run even if disabled
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'rp', 'substation', '0')
            except Exception as ex:
                gv.logger.info('run_now: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/')

class toggle_temp(ProtectedPage):
    """Change units of Raspi's CPU temperature display on home page."""

    def GET(self):
        qdict = web.input()
        if qdict['tunit'] == "C":
            gv.sd['tu'] = "F"
            gv.sd['boiler_supply_temp'] = gv.sd['boiler_supply_temp']*1.8 + 32
        else:
            gv.sd['tu'] = "C"
            gv.sd['boiler_supply_temp'] = (gv.sd['boiler_supply_temp']-32)/1.8
        jsave(gv.sd, 'sd')
        raise web.seeother('/')


class api_status(ProtectedPage):
    """Simple Status API"""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        got_data = False
        if subid != 0:
            try:
                # dont save log data
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'sd':1, 'ps':1, 'programs':1, 'sbits':1})
                sd = data['sd']
                ps = data['ps']
                pd = data['programs']
                sbits = data['sbits']
                got_data = True
            except Exception as ex:
                gv.logger.info('api_status: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][int(qdict['substation'])]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][int(qdict['substation'])]['status'] = 'unreachable'

        if not got_data:
            sd = gv.sd
            ps = gv.ps
            pd = gv.pd
            sbits = gv.sbits

        statuslist = []
        for sid in range(0, sd['nst']):
            bid = sid // 8
            s = sid % 8
            if (sd['show'][bid] >> s) & 1 == 1:
                sn = sid + 1
                sbit = (sbits[bid] >> s) & 1
                irbit = (sd['ir'][bid] >> s) & 1
                status = {'station': sid, 'status': 'disabled', 'reason': '', 'master': 0, 'programName': '',
                          'remaining': 0}
                if sd['en'] == 1:
                    if sbit:
                        status['status'] = 'on'
                    if not irbit:
                        if sd['rd'] != 0:
                            status['reason'] = 'rain_delay'
                        if sd['urs'] != 0 and sd['rs'] != 0:
                            status['reason'] = 'rain_sensed'
                    if sn == sd['mas']:
                        status['master'] = 1
                        status['reason'] = 'master'
                    else:
                        rem = ps[sid][1]
                        if rem > 65536 and rem < 86400:
                            rem = 0

                        id_nr = ps[sid][0]
                        pname = 'P' + str(id_nr)
                        if id_nr == 255 or id_nr == 99:
                            pname = 'Manual Mode'
                        if id_nr == 254 or id_nr == 98:
                            pname = 'Run-once Program'

                        if sbit:
                            if id_nr <= len(pd) and (pd[id_nr-1][gv.p_flags]&2) == 2:
                                status['status'] = 'ban'
                            status['reason'] = 'program'
                            status['programName'] = pname
                            status['remaining'] = rem
                        else:
                            if ps[sid][0] == 0:
                                status['status'] = 'off'
                            else:
                                status['status'] = 'waiting'
                                status['reason'] = 'program'
                                status['programName'] = pname
                                status['remaining'] = rem
                else:
                    status['reason'] = 'system_off'
                statuslist.append(status)
                   
        web.header('Content-Type', 'application/json')
        return json.dumps(statuslist)


class api_log(ProtectedPage):
    """Simple Log API"""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        thedate = qdict['date']
        del qdict['date']
        # date parameter filters the log values returned; "yyyy-mm-dd" format
        theday = datetime.date(*map(int, thedate.split('-')))
        thedaystr = theday.strftime('%Y-%m-%d')
        prevday = theday - datetime.timedelta(days=1)
        prevdate = prevday.strftime('%Y-%m-%d')
        got_data = False
        if subid != 0:
            try:
                # dont save log data
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'wlog':1, 'end_date':thedaystr, 'days_before':1})
                records = data['wlog']
                got_data = True
            except Exception as ex:
                gv.logger.info('api_log: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][int(qdict['substation'])]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][int(qdict['substation'])]['status'] = 'unreachable'

        if not got_data:
            records = read_log('wlog', thedaystr, 1)

        data = []
        for event in records:
            # return any records starting on this date
            if event['date'] == thedate:
                data.append(event)
                # also return any records starting the day before and completing after midnight
            elif event['date'] == prevdate:
                if int(event['start'].split(":")[0]) * 60 + int(event['start'].split(":")[1]) + int(
                        event['duration'].split(":")[0]) > 24 * 60:
                    data.append(event)

        web.header('Content-Type', 'application/json')
        return json.dumps(data)
