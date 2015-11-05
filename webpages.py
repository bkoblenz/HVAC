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
            return template_render.login(my_signin)
        else:
            web.config._session.user = 'admin'
            report_login()
            raise web.seeother('/')


class logout(WebPage):
    def GET(self):
        web.config._session.user = 'anonymous'
        raise web.seeother('/')

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

def load_and_save_remote(qdict, save_list, cmd, first_param_name, first_param):
    try:
        subid = int(qdict['substation'])
    except:
        subid = 0
    sub = gv.plugin_data['su']['subinfo'][subid]
    urlcmd = 'http://' + sub['ip']
    if 'port' in sub and sub['port'] != 80 and sub['port'] != 0:
        urlcmd += ":" + sub['port']
    if sub['proxy'] != '':
        urlcmd += ':9080/supri?proxyaddress='+sub['proxy'] + '&' + 'proxycommand=' + cmd + '&'
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
        urlcmd += '&' + key + '=' + urllib.quote_plus(qdict[key])
    data = urllib2.urlopen(urlcmd)
    data = json.load(data)
    gv.plugin_data['su']['subdesc'][subid]['status'] = 'ok'
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

def extract_program(pid, pd):
    prog = []
    if pid != -1:
        mp = pd[pid][:]  # Modified program
        if mp[gv.p_day_mask] >= 128 and mp[gv.p_interval_day] > 1:  # If this is an interval program
            dse = gv.now//86400
            # Convert absolute to relative days remaining for display
            rel_rem = (((mp[gv.p_day_mask] - 128) + mp[gv.p_interval_day]) - (dse % mp[gv.p_interval_day])) % mp[gv.p_interval_day]
            mp[gv.p_day_mask] = rel_rem + 128  # Update from saved value.
        prog = str(mp).replace(' ', '')
    return prog

###########################
#### Class Definitions ####

class home(ProtectedPage):
    """Open Home page."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('home', qdict):
            return template_render.home(0, gv.snames, gv.sd, gv.ps, gv.pd, gv.lrun)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Status&continuation=home')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'snames'], 'susldr', 'data', {'sd':1, 'snames':1, 'ps':1, 'pd':1, 'lrun':1})
                return template_render.home(subid, data['snames'], data['sd'], data['ps'], data['pd'], data['lrun'])
            except Exception as ex:
                gv.logger.info('home: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            return template_render.home(0, gv.snames, gv.sd, gv.pd, gv.lrun)

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
            raise web.seeother('/')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cv', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_valuess: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
#           todo  raise web.seeother('/?substation='+qdict['substation'])
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

        for f in ['name']:
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
                    with open('/etc/timezone','w') as file:
                        file.write(qdict['o'+f]+'\n')
                    subprocess.call(['dpkg-reconfigure', '-f', 'non-interactive', 'tzdata'])
                    qdict['rbt'] = '1'  # force reboot with change
                gv.sd[f] = qdict['o'+f]

        for f in ['wl', 'lr']:
            if 'o'+f in qdict:
                gv.sd[f] = int(qdict['o'+f])

        for f in ['ipas', 'tf', 'urs', 'seq', 'rst', 'lg']:
            if 'o'+f in qdict and (qdict['o'+f] == 'on' or qdict['o'+f] == '1'):
                gv.sd[f] = 1
            else:
                gv.sd[f] = 0

        jsave(gv.sd, 'sd')
        report_option_change()

        if 'netconfig' in qdict and qdict['netconfig'] == '1':
            gv.logger.debug('netconfig')
            time.sleep(5)
            # exit will cause sip_monitor to start sip_net_config
            exit(0)

        # todo propagate shutdown
        if 'rshutdown' in qdict and qdict['rshutdown'] == '1':
            poweroff()

        if 'rbt' in qdict and qdict['rbt'] == '1':
            reboot()

        if 'rstrt' in qdict and qdict['rstrt'] == '1':
            raise web.seeother('/restart')
        raise web.seeother('/')

class view_stations(ProtectedPage):
    """Open a page to view and edit a run once program."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('view_stations', qdict):
            return template_render.stations(0, gv.snames, gv.sd)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Stations&continuation=vs')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'snames'], 'susldr', 'data', {'sd':1, 'snames':1})
                return template_render.stations(subid, data['snames'], data['sd'])
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
            for i in range(gv.sd['nbrd']):
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
            names = []
            for i in range(gv.sd['nst']):
                if 's' + str(i) in qdict:
                    names.append(qdict['s'+str(i)])
                else:
                    names.append('S'+"{:0>2d}".format(i+1))
            gv.snames = names
            jsave(names, 'snames')
            jsave(gv.sd, 'sd')
            report_station_names()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
            raise web.seeother('/')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cs', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_stations: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
#           todo  raise web.seeother('/?substation='+qdict['substation'])
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
            raise web.seeother('/vs')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'csm', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_station_master: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            raise web.seeother('/vs?substation=' + str(subid))

class change_station_board(ProtectedPage):
    """Save changes number of boards."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('change_station_board', qdict) or subid == 0:
            onbrd = int(qdict['onbrd'])
            incr = onbrd - gv.sd['nbrd']
            if incr > 0:  # Lengthen lists
                for i in range(incr):
                    gv.sd['mo'].append(0)
                    gv.sd['ir'].append(0)
                    gv.sd['iw'].append(0)
                    gv.sd['show'].append(255)
                ln = len(gv.snames)
                for i in range(incr*8):
                    gv.snames.append("S"+"{:0>2d}".format(i+1+ln))
                with gv.rs_lock:
                    for i in range(incr * 8):
                        gv.srvals.append(0)
                        gv.ps.append([0, 0])
                        gv.rs.append([gv.rs_generic.copy()]) 
                    for i in range(incr):
                        gv.sbits.append(0)
            elif incr < 0:  # Shorten lists
                gv.sd['mo'] = gv.sd['mo'][:onbrd]
                gv.sd['ir'] = gv.sd['ir'][:onbrd]
                gv.sd['iw'] = gv.sd['iw'][:onbrd]
                gv.sd['show'] = gv.sd['show'][:onbrd]
                newlen = onbrd*8
                with gv.rs_lock:
                    gv.srvals = gv.srvals[:newlen]
                    gv.ps = gv.ps[:newlen]
                    gv.rs = gv.rs[:newlen]
                    gv.sbits = gv.sbits[:onbrd]
                gv.snames = gv.snames[:newlen]
            jsave(gv.snames, 'snames')
            gv.sd['nbrd'] = onbrd
            gv.sd['nst'] = onbrd*8
            jsave(gv.sd, 'sd')
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
            raise web.seeother('/vs')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'csb', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_station_board: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            raise web.seeother('/vs?substation=' + str(subid))

class get_set_station(ProtectedPage):
    """Return a page containing a number representing the state of a station or all stations if 0 is entered as station number."""

    def GET(self):
        qdict = web.input()
        got_data = False
        if 'substation' in qdict and qdict['substation'] != 0:
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
            elif sid < sd['nbrd'] * 8:
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
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
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
                return
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
            schedule_stations(rs, 5 if gv.sd['seq'] else 1)
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
            raise web.seeother('/')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cr', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_runonce: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
#           todo  raise web.seeother('/?substation='+qdict['substation'])
            raise web.seeother('/')

class view_programs(ProtectedPage):
    """Open programs page."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('view_programs', qdict):
            return template_render.programs(0, gv.snames, gv.sd, gv.pd, gv.now)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Programs&continuation=vp')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'snames', 'pd'], 'susldr', 'data', {'sd':1, 'snames':1, 'pd':1})
                return template_render.programs(subid, data['snames'], data['sd'], data['pd'], gv.now)
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
        if process_page_request('modify_program', qdict) or subid == 0:
            prog = extract_program(pid, gv.pd)
            return template_render.modify(0, gv.snames, gv.sd, pid, prog)
        else:
            data = gv.plugin_data['su']['subdesc'][subid] # assume came from view programs
            prog = extract_program(pid, data['pd'])
            return template_render.modify(subid, data['snames'], data['sd'], pid, prog)

class change_program(ProtectedPage):
    """Add a program or modify an existing one."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('modify_program', qdict) or subid == 0:
            pnum = int(qdict['pid']) + 1  # program number
            cp = json.loads(qdict['v'])
            if pnum > 0 and pnum <= len(gv.pd) and 'vflags' not in qdict: # something that does not understand our flags.  Keep them unchanged.
                cp[gv.p_flags] = cp[gv.p_flags]&1
                old_flags = gv.pd[pnum-1][gv.p_flags]
                new_flags = (gv.pd[pnum-1][gv.p_flags]&(~1)) | cp[gv.p_flags]
                cp[gv.p_flags] = new_flags
                if new_flags != old_flags:
                    gv.logger.debug('Program ' + str(pnum) + ' changed flags from ' + str(old_flags) + ' to ' + str(new_flags))
            if cp[gv.p_flags]&1 == 0: # if disabled and program is running or currently delayed
                with gv.rs_lock:
                    for i in range(len(gv.ps)):
                        if gv.ps[i][0] == pnum:
                            gv.ps[i] = [0, 0]
                            if gv.srvals[i]:
                                gv.srvals[i] = 0
                                set_output()
                    for i in range(len(gv.rs)):
                        for j in range(len(gv.rs[i])-1,0,-1):
                            if gv.rs[i][j]['rs_program_id'] == pnum:
                                del gv.rs[i][j]

            if cp[gv.p_day_mask] >= 128 and cp[gv.p_interval_day] > 1:
                dse = gv.now//86400
                ref = dse + cp[gv.p_day_mask] - 128
                cp[gv.p_day_mask] = (ref % cp[gv.p_interval_day]) + 128
            if qdict['pid'] == '-1':  # add new program
                gv.pd.append(cp)
            else:
                gv.pd[int(qdict['pid'])] = cp  # replace program
            jsave(gv.pd, 'programs')
            gv.sd['nprogs'] = len(gv.pd)
            report_program_change()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
            raise web.seeother('/vp')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cp', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_program: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            raise web.seeother('/vp?substation='+str(subid))

class delete_program(ProtectedPage):
    """Delete one or all existing program(s)."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('delete_program', qdict) or subid == 0:
            if qdict['pid'] == '-1':
                del gv.pd[:]
            else:
                pnum = int(qdict['pid'])
                del gv.pd[pnum]
            jsave(gv.pd, 'programs')
            gv.sd['nprogs'] = len(gv.pd)
            report_program_deleted()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
            raise web.seeother('/vp')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'dp', 'substation', '0')
            except Exception as ex:
                gv.logger.info('delete_program: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            raise web.seeother('/vp?substation='+str(subid))

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
            raise web.seeother('/vp')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'ep', 'substation', '0')
            except Exception as ex:
                gv.logger.info('enable_program: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            raise web.seeother('/vp?substation='+str(subid))

class view_log(ProtectedPage):
    """View Log"""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('view_log', qdict):
            records = read_log()
            return template_render.log(0, gv.snames, gv.sd, records)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Log&continuation=vl')
        else:
            try:
                subid, data = load_and_save_remote(qdict, ['sd', 'snames', 'log'], 'susldr', 'data', {'sd':1, 'snames':1, 'log':1, 'end_date':'', 'days_before':0})
                return template_render.log(subid, data['snames'], data['sd'], data['log'])
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
            with io.open('./data/log.json', 'w') as f:
                f.write(u'')
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
            raise web.seeother('/vl')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cl', 'substation', '0')
            except Exception as ex:
                gv.logger.info('clear_log: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
            raise web.seeother('/vl?substation='+str(subid))

class water_log(ProtectedPage):
    """Download water log to file"""

    def GET(self):
        # assume only reasonable way to get here is to have just done a view log, so
        # most recent saved log data is what we will dump.
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('water_log', qdict) or subid == 0:
            filename = 'log'
            records = read_log()
        else:
            filename = 'log-' + gv.plugin_data['su']['subinfo'][subid]['name']
            records = gv.plugin_data['su']['subdesc'][subid]['log']
        filename += '.csv'

        data = _("Date, Start Time, Zone, Duration, Program") + "\n"
        for r in records:
            event = ast.literal_eval(json.dumps(r))
            data += event["date"] + ", " + event["start"] + ", " + str(event["station"]+1) + ", " + event[
                "duration"] + ", " + event["program"] + "\n"

        web.header('Content-Type', 'text/csv')
        web.header('Content-Disposition', 'attachment; filename="'+filename+'"')
        return data

class run_now(ProtectedPage):
    """Run a scheduled program now. This will override any running programs."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('run_now', qdict) or subid == 0:
            pid = int(qdict['pid'])
            p = gv.pd[int(qdict['pid'])]  # program data
            stop = 1 if 'stop' not in qdict else json.loads(qdict['stop'])
            if stop:
                stop_stations()
            rs = [[0,0] for i in range(gv.sd['nst'])] # program, duration indexed by station
            extra_adjustment = plugin_adjustment()
            for b in range(gv.sd['nbrd']):  # check each station
                for s in range(8):
                    sid = b * 8 + s  # station index
                    if sid + 1 == gv.sd['mas']:  # skip if this is master valve
                        continue
                    if p[gv.p_station_mask_idx + b] & 1 << s:  # if this station is scheduled in this program
                        rs[sid][0] = pid + 1  # store program number in schedule
                        rs[sid][1] = p[6]
                        if (p[gv.p_flags]&2) == 0 and not gv.sd['iw'][b] & 1 << s: # not ban program
                            rs[sid][1] = int(rs[sid][1] * gv.sd['wl'] / 100 * extra_adjustment)
            schedule_stations(rs, p[gv.p_flags])
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
            raise web.seeother('/')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'rp', 'substation', '0')
            except Exception as ex:
                gv.logger.info('run_now: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
#           todo  raise web.seeother('/?substation='+qdict['substation'])
            raise web.seeother('/')

class toggle_temp(ProtectedPage):
    """Change units of Raspi's CPU temperature display on home page."""

    def GET(self):
        qdict = web.input()
        if qdict['tunit'] == "C":
            gv.sd['tu'] = "F"
        else:
            gv.sd['tu'] = "C"
        jsave(gv.sd, 'sd')
        raise web.seeother('/')


class api_status(ProtectedPage):
    """Simple Status API"""

    def GET(self):
        qdict = web.input()
        got_data = False
        if 'su' in gv.plugin_data and 'substation' in qdict and qdict['substation'] != 0:
            try:
                # dont save log data
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'sd':1, 'ps':1, 'pd':1, 'sbits':1})
                sd = data['sd']
                ps = data['ps']
                pd = data['pd']
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
        for bid in range(0, sd['nbrd']):
            for s in range(0, 8):
                sid = bid * 8 + s
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
                            if rem > 65536:
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
        thedate = qdict['date']
        del qdict['date']
        # date parameter filters the log values returned; "yyyy-mm-dd" format
        theday = datetime.date(*map(int, thedate.split('-')))
        thedaystr = theday.strftime('%Y-%m-%d')
        prevday = theday - datetime.timedelta(days=1)
        prevdate = prevday.strftime('%Y-%m-%d')
        got_data = False
        if 'su' in gv.plugin_data and 'substation' in qdict and qdict['substation'] != 0:
            try:
                # dont save log data
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'log':1, 'end_date':thedaystr, 'days_before':1})
                records = data['log']
                got_data = True
            except Exception as ex:
                gv.logger.info('api_log: No response from slave: ' +
                               gv.plugin_data['su']['subinfo'][int(qdict['substation'])]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][int(qdict['substation'])]['status'] = 'unreachable'

        if not got_data:
            records = read_log(thedaystr, 1)

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
