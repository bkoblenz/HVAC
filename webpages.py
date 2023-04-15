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
            gv.plugin_data['te']['tesender'].try_mail('Login', 'Failure')
            return template_render.login(my_signin)
        else:
            gv.logger.info('login succeeded')
            web.config._session.user = 'admin'
            gv.logged_in = True
            gv.plugin_data['te']['tesender'].try_mail('Login', 'Success')
            report_login()
            raise web.seeother('/')

class logout(ProtectedPage):
    def GET(self):
        gv.logger.info('logout')
        web.config._session.user = 'anonymous'
        gv.logged_in = False
        raise web.seeother('/')

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
    urlcmd = 'http://localhost/'+cmd + '?'
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
        gv.logger.info('load_and_save_remote unreachable urlcmd: ' + urlcmd)
        raise IOError, 'UnreachableRemote'
    except Exception as ex:
        gv.logger.exception('load_and_save_remote unexpected exception: ' + urlcmd)
        raise IOError, 'UnreachableRemote'

    try:
        data = json.load(data)
    except ValueError:
        print 'bad response urlcmd: ', urlcmd, ' data: ', data
        raise IOError, 'BadRemoteResponse'
    except Exception as ex:
        gv.logger.exception('load_and_save_remote unexpected json data: ')
        raise IOError, 'BadJSONResponse'

    return 0, data

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

        #gv.sd['boiler_supply_temp'] = float(qdict['oboiler_supply_temp'])
        gv.sd['cold_gap_temp'] = float(qdict['ocold_gap_temp'])
        gv.sd['cold_gap_time'] = int(qdict['ocold_gap_time'])
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
        for i, ip in enumerate(gv.sd['thermostats']):
            gv.sd['thermostats'][i] = {'ip': ip, 'mode':int(qdict['oip'+str(i)+'_mode']), 'temp':float(qdict['oip'+str(i)+'_temp'])}
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

        if 'rbt' in qdict and qdict['rbt'] != '0':
            reboot(3)
            raise web.seeother('/reboot')
        raise web.seeother('/')

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
                try:
                    return template_render.log(subid, data['snames'], data['sd'], data['wlog'], data['elog'])
                except:
                    gv.logger.exception('view_log prerender: ' + str(data))
                    gv.plugin_data['te']['tesender'].try_mail('Heating', 'Bad log')
                    return template_render.log(subid, data['snames'], data['sd'], [], []) # probably corrupt data
            except Exception as ex:
                gv.logger.info('view_log: Exception: ' + str(ex))
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
                gv.logger.info('clear_log: Exception: ' + str(ex))
                raise web.seeother('/unreachable')
        raise web.seeother('/vl')

class water_log(ProtectedPage):
    """Download water log to file"""

    def GET(self):
        # assume only reasonable way to get here is to have just done a view log, so
        # most recent saved log data is what we will dump.
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        filename = 'watering-log'
        records = read_log('wlog')
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
