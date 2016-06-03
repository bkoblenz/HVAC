# -*- coding: utf-8 -*-


import i18n

import datetime
from threading import Thread
import os
import errno
import random
import sys
import time
import subprocess
import io
from web.webapi import seeother
from blinker import signal

import web
from web import form

import gv
from web.session import sha1
from operator import itemgetter

try:
    import json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        gv.logger.error(_("Error: json module not found"))
        sys.exit()


def reboot(wait=1, block=False):
    """
    Reboots the Raspberry Pi from a new thread.
    
    @type wait: int
    @param wait: length of time to wait before rebooting
    @type block: bool
    @param block: If True, clear output and perform reboot after wait.
        Set to True at start of thread (recursive).
    """
    if block:
        from gpio_pins import set_output
        with gv.rs_lock:
            if gv.use_pigpio:
                pass
            else:
                GPIO.cleanup()
        time.sleep(wait)
        try:
            gv.logger.info(_('Rebooting...'))
        except Exception:
            pass
        subprocess.Popen(['reboot'])
    else:
        t = Thread(target=reboot, args=(wait, True))
        t.start()


def poweroff(wait=1, block=False):
    """
    Powers off the Raspberry Pi from a new thread.
    
    @type wait: int or float
    @param wait: number of seconds to wait before rebooting
    @type block: bool
    @param block: If True, clear output and perform reboot after wait.
        Set to True at start of thread (recursive).
    """
    if block:
        from gpio_pins import set_output
        with gv.rs_lock:
            if gv.use_pigpio:
                pass
            else:
                GPIO.cleanup()
        time.sleep(wait)
        try:
            gv.logger.info(_('Powering off...'))
        except Exception:
            pass
        subprocess.Popen(['poweroff'])
    else:
        t = Thread(target=poweroff, args=(wait, True))
        t.start()


def get_ip(net=''):
    """
    Returns the IP address of 'net' if specified, otherwise 'wlan0', 'eth0', 'ppp0' whichever is found first.
    """
    try:
        arg = 'ip route list'
        p = subprocess.Popen(arg, shell=True, stdout=subprocess.PIPE)
        data,errdata = p.communicate()
        data = data.split('\n')
        list = ['wlan0', 'eth0', 'ppp0'] if net == '' else [net]
        for iface in list:
            for d in data:
                split_d = d.split()
                try:
                    idx = split_d.index(iface) # exception if not found
                    ipaddr = split_d[split_d.index('src') + 1]
                    return ipaddr
                except:
                    pass
        return "No IP Settings"
    except:
        return "No IP Settings"

def update_upnp(cur_ip, deletes=[],adds=[]):
    try:
        upnp_out = subprocess.check_output("upnpc -l ", shell=True)
        l = upnp_out.split('\n')
        last_dot = cur_ip.rfind(".")
        if last_dot == -1:
            raise ValueError('Bad IP Address')
        short_cur_ip = cur_ip[0:last_dot]
        router_try = short_cur_ip + '.1'
        router_ref = ''
        desc = 'desc: '
        for e in l:
            # If we find a router ending in .1 use it.  Otherwise if we find only
            # one upnp device, use it.  Otherwise give up.
            if desc in e:
                if router_try in e:
                    router_ref = '-u ' + e[e.find(desc)+len(desc):]
                    break
                elif router_try == '':
                    router_ref = '-u ' + e[e.find(desc)+len(desc):]
                else:
                    router_ref == 'Multiple'
        if router_ref == '' or router_ref == 'Multiple':
            raise ValueError('upnp Router Not Found: ' + upnp_out)
    except Exception as ex:
        gv.logger.info('Could not update upnp: ' + str(ex))
        return

    for port in deletes:
        try:
            upnp_out = subprocess.check_output("upnpc " + router_ref + " -d " + str(port) + " TCP", shell=True)
            gv.logger.info('upnp force deleted port : ' + str(port))
        except Exception as ex:
            gv.logger.info('upnp could not force delete port: ' + str(port) + ' Exception: ' + str(ex))

    for e in adds:
        internal_port = e[0]
        external_port = e[1]
        try:
            # create new one.  Dont delete existing as that will drop connections.
            #upnp_out = subprocess.check_output("upnpc " + router_ref + " -d " + str(external_port) + " TCP", shell=True)
            #gv.logger.info('upnp deleted port : ' + str(external_port))
            upnp_out = subprocess.check_output("upnpc " + router_ref + " -a " + cur_ip + ' ' + str(internal_port) + ' ' + str(external_port) + " TCP", shell=True)
            gv.logger.info('upnp added ip: ' + cur_ip + ' internal port: ' + str(internal_port) + ' external port: ' + str(external_port))
            upnp_out = subprocess.check_output("upnpc " + router_ref + " -l | grep TCP", shell=True)
            gv.logger.debug('upnp -l: ' + upnp_out)
        except Exception as ex:
            gv.logger.info('upnp could not add internal port: ' + str(internal_port) + ' for external port: ' + str(external_port) + ' Exception: ' + str(ex))

def check_and_update_upnp(cur_ip=''):
    """Update the remote mappings if appropriate"""

    if cur_ip == '':
        cur_ip = get_ip()
    if cur_ip != 'No IP Settings' and gv.sd['enable_upnp']:
        adds = []
        if gv.sd['external_htp'] != 0:
            if gv.sd['htp'] == 0:
                adds.append([80, gv.sd['external_htp']])
            else:
                adds.append([gv.sd['htp'], gv.sd['external_htp']])
        if gv.sd['remote_support_port'] != 0:
            adds.append([22, gv.sd['remote_support_port']])
        update_upnp(cur_ip, [], adds)

def network_exists(net):
    try:
        netinfo = subprocess.check_output(['ifconfig', net])
    except:
        return False
    return True

def network_up_wpa(net):
    """Return ip address of network if it is up.  Otherwise empty string"""

    try:
        netinfo = subprocess.check_output(['wpa_cli', '-i', net, 'status'])
    except:
        netinfo = ''
    netlist = netinfo.split()
    for l in netlist:
        if 'ip_address:' in l:
            return l[len('ip_address:'):]
    return ''

def network_up_ifconfig(net):
    """Return ip address of network if it is up.  Otherwise empty string"""

    try:
        netinfo = subprocess.check_output(['ifconfig', net])
    except:
        netinfo = ''
    netlist = netinfo.split('\n')
    for l in netlist:
        if 'inet addr:' in l:
            l = l.strip()
            l = l[len('inet addr:'):]
            sp_idx = l.find(' ')
            return l[:sp_idx]

    return ''

def network_up(net):
    attempts = 5
    while attempts > 0:
        attempts -= 1
        time.sleep(3)
        if network_up_ifconfig(net) != '':
            return True
        if network_up_wpa(net) != '':
            return True
    return False

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise

def to_relative_time(now):
    """
    Return a string that corresponds to the hour:min:sec of now relative to midnight
    """

    relative = now % (24*60*60)
    hour = relative // (60*60)
    relative = relative - hour*60*60
    min = relative // 60
    sec = relative - min*60
    return str(hour).zfill(2) + ':' + str(min).zfill(2) + ':' + str(sec).zfill(2)
    

def get_cpu_temp(unit=None):
    """
    Reads and returns the temperature of the CPU if available.
    If unit is F, temperature is returned as Fahrenheit otherwise Celsius.
    
    @type unit: character
    @param unit: F or C        
    @rtype:   string
    @return:  CPU temperature
    """

    try:
        if gv.platform == 'bo':
            res = os.popen('cat /sys/class/hwmon/hwmon0/device/temp1_input').readline()
            temp = str(int(float(res) / 1000))
        elif gv.platform == 'pi':
            command = "cat /sys/class/thermal/thermal_zone0/temp"
            output = subprocess.check_output(command.split())
            temp = str(int(float(output) / 1000))
        else:
            temp = str(0)

        if unit == 'F':
            return str(1.8 * float(temp) + 32)
#            return str(9.0 / 5.0 * float(temp) + 32)
        elif unit is not None:
            return str(float(temp))
        else:
            return temp
    except Exception:
        return '!!'


def timestr(t):
    """
    Convert duration in seconds to string in the form mm:ss.
      
    @type  t: int
    @param t: duration in seconds
    @rtype:   string
    @return:  duration as "mm:ss"   
    """
    return str((t / 60 >> 0) / 10 >> 0) + str((t / 60 >> 0) % 10) + ":" + str((t % 60 >> 0) / 10 >> 0) + str(
        (t % 60 >> 0) % 10)


def jsave(data, fname):
    """
    Save data to a json file.
    
    
    """
    with open('./data/' + fname + '.json', 'w') as f:
        json.dump(data, f)


def password_salt():
    """
    Generate random number for use as salt for password encryption
    
    @rtype: string
    @return: random value as 64 byte string.
    """
    return "".join(chr(random.randint(33, 127)) for _ in xrange(64))


def password_hash(password, salt):
    """
    Generate password hash using sha-1.
    
    @type: string
    @param param: password
    @type param: string
    @param: salt 
    """
    return sha1(password + salt).hexdigest()


########################
#### Login Handling ####

def check_login(redirect=False):
    """
    Check login.
    """
    qdict = web.input()

    try:
        if gv.sd['ipas'] == 1:
            return True

        if web.config._session.user == 'admin':
            return True
    except KeyError:
        pass

    if 'pw' in qdict:
        if gv.sd['password'] == password_hash(qdict['pw'], gv.sd['salt']):
            return True
        if redirect:
            raise web.unauthorized()
        return False

    if redirect:
        raise web.seeother('/login')
    return False


signin_form = form.Form(
    form.Password('password', description = _('Password') + ':'),
    validators=[
        form.Validator(
            _("Incorrect password, please try again"),
            lambda x: gv.sd['password'] == password_hash(x.password, gv.sd['salt'])
        )
    ]
)


def get_input(qdict, key, default=None, cast=None):
    """
    Checks data returned from a UI web page.
    
    
    """
    result = default
    if key in qdict:
        result = qdict[key]
        if cast is not None:
            result = cast(result)
    return result

def read_log(end_date='', days_before=0):
    """
    Take optional end date in struct time format and return the log records
    for that date and days_before more dates.
    """

    if end_date == '':
        start_date = 0
        end_date = time.strftime('%Y-%m-%d', gv.nowt)
    else:
        end_st_date = time.strptime(end_date, '%Y-%m-%d')
        end_dt_date = datetime.datetime(*end_st_date[:6])
        start_date = end_dt_date - datetime.timedelta(days=days_before)
        start_date = str(start_date)
        start_date = start_date[:start_date.find(' ')] # remove seconds

    result = []
    try:
        with io.open('./data/log.json') as logf:
            records = logf.readlines()
            count = 0
            for i in records:
                count += 1
                try:
                    rec = json.loads(i)
                except:
                    print 'log.json record: ', count, ' record: ' , i
                    continue
                rec_date = rec['date']
                if start_date != 0:
                    if rec_date < start_date:
                        break; # no need to process rest of file
                if end_date != '':
                    if rec_date > end_date:
                        continue # skip this record
                result.append(rec)
        return result
    except IOError:
        return result


def jsave(data, fname):
    """
    Save data to a json file.
    
    
    """
    with open('./data/' + fname + '.json', 'w') as f:
        json.dump(data, f)


def log_event(msg):
    """
    Add run data to json log file - most recent first.
    
    If a record limit is specified (gv.sd['lr']) the number of records is truncated.  
    """

    if gv.sd['lg']:
        logline = '{'+time.strftime('"time":"%H:%M:%S","date":"%Y-%m-%d"', time.gmtime(gv.now)) + ',"mode":"' + gv.sd['mode'] + '","message":"' + msg + '"}'
        gv.logger.info(logline)
        lines = []
        lines.append(logline + '\n')
        log = read_log()
        for r in log:
            lines.append(json.dumps(r) + '\n')
        with open('./data/log.json', 'w') as f:
            if gv.sd['lr']:
                f.writelines(lines[:gv.sd['lr']])
            else:
                f.writelines(lines)
