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
import re
from web.webapi import seeother
from blinker import signal
import i2c

import web
from web import form

import gv
from web.session import sha1
from operator import itemgetter
import urllib
import urllib2

try:
    from gpio_pins import GPIO, pin_rain_sense, pin_relay
    if gv.use_pigpio:
        import pigpio
        pi = pigpio.pi()
except ImportError:
    gv.logger.error('error importing GPIO pins into helpers')
    pass

try:
    import json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        gv.logger.error(_("Error: json module not found"))
        sys.exit()


##############################
#### Function Definitions ####

restarting = signal('restart') #: Signal to send on software restart
def report_restart():
    """
    Send blinker signal indicating system will restart.
    """
    restarting.send()

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
            stop_stations()
            if gv.use_pigpio:
                pass
            else:
                GPIO.cleanup()
        time.sleep(wait)
        try:
            gv.logger.info(_('Rebooting...'))
        except Exception:
            pass
        try: #power off usb
            subprocess.call(['hub-ctrl', '-h', '1',  '-p', '0'])
        except:
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
            stop_stations()
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


def restart(wait=1, block=False):
    """
    Restarts the software from a new thread.
    
    @type wait: int
    @param wait: length of time to wait before restarting
    @type block: bool
    @param block: If True, clear output and perform restart after wait.
        Set to True at start of thread (recursive).
    """
    if block:
        report_restart()
        from gpio_pins import set_output
        with gv.rs_lock:
            stop_stations()
            if gv.use_pigpio:
                pass
            else:
                GPIO.cleanup()
        time.sleep(wait)
        try:
            gv.logger.info(_('Restarting...'))
        except Exception:
            pass
        subprocess.Popen('service sip restart'.split())
    else:
        t = Thread(target=restart, args=(wait, True))
        t.start()

def usb_reset(kind=''):
    filter = kind
    if kind == 'camera':
        filter = '"Logitech, Inc."'
    elif kind == 'radio':
        filter = '"Future Technology"'
    try:
        bus =  subprocess.check_output('/usr/bin/lsusb | /bin/grep ' + filter + ' | /usr/bin/cut -c5-7', shell=True).strip()
        dev = subprocess.check_output('/usr/bin/lsusb | /bin/grep ' + filter + ' | /usr/bin/cut -c16-18', shell=True).strip()
        if bus != '' and dev != '':
            dev_path = '/dev/bus/usb/'+bus+'/'+dev
            try:
                subprocess.call(['../5124616/usbreset', dev_path])
            except:
                fd = os.open(dev_path, os.O_WRONLY)
                try:
                    fcntl.ioctl(fd, USBDEVFS_RESET, 0)
                finally:
                    os.close(fd)
            time.sleep(.5)
    except: # no usbreset
        pass

def update_radio_present():
    try:
        with open(gv.radio_dev, 'r') as sdf:
            gv.sd['radio_present'] = True
    except:
        gv.sd['radio_present'] = False

def propagate_to_substations(cmd, params=''):
    """Propagate urlcmd to each proxied substation and then slaves (other than ourselves)"""

    proxies = []
    slaves = []
    unreachable = []
    for i in range(1,len(gv.plugin_data['su']['subinfo'])):
        sub = gv.plugin_data['su']['subinfo'][i]
        if sub['status'] == 'ok':
            if sub['proxy'] != '':
                proxies.append(sub)
            elif sub['ip'] != get_ip():
                slaves.append(sub)
        else:
            gv.logger.info('propagate_to_substations.  unreachable: ' + sub['name'])
            unreachable.append(sub)
    
    # do all proxied slaves then slaves.  Otherwise gateway may be shut down
    propagate = proxies + slaves
    for sub in propagate:
        urlcmd = 'http://' + sub['ip']
        if 'port' in sub and sub['port'] != 80 and sub['port'] != 0:
            urlcmd += ":" + sub['port']
        if sub['proxy'] != '':
            urlcmd += ':9080/supri?proxyaddress='+sub['proxy'] + '&' + 'proxycommand=' + cmd
            if params != '':
                urlcmd += '&'
        else:
            urlcmd += '/'+cmd
            if params != '':
                urlcmd += '?'
        urlcmd += urllib.quote_plus(params)
        try:
            gv.logger.info('propagate_to_substations: ' + urlcmd)
            data = urllib2.urlopen(urlcmd, timeout=gv.url_timeout+2)
        except:
            pass # ignore results

def uptime():
    """
    Returns UpTime for RPi
    
    @rtype: String
    @return: Length of time System has been running.
    """
    string = 'Error 1: uptime'

    with open("/proc/uptime") as f:
        total_sec = float(f.read().split()[0])
        string = str(datetime.timedelta(seconds=total_sec)).split('.')[0]

    return string

last_ip_check_time = 0
external_ip_address = ''
def get_external_ip():
    """Return the externally visible IP address for this system."""

    global last_ip_check_time, external_ip_address
    try:
        if gv.now - last_ip_check_time > 5*60:
            last_ip_check_time = gv.now
            ip_info = subprocess.check_output(['/usr/bin/curl', '-ks', 'bot.whatismyipaddress.com'])
            if ip_info != '':
                external_ip_address = ip_info
        
#        ip_info = subprocess.check_output("curl -ks http://checkip.dyndns.org", shell=True)
        # look in <html><head><title>Current IP Check</title></head><body>Current IP Address: 63.142.218.154</body></html>
#        addr_str = "IP Address: "
#        addr_loc = ip_info.find(addr_str)
#        if addr_loc >= 0:
#            addr_end = ip_info.find("<", addr_loc)
#            return ip_info[addr_loc+len(addr_str):addr_end]
    except:
        pass
    return external_ip_address

def get_macid(net='wlan0'):
    """
    Returns the mac address of 'net'.
    """
    try:
        netinfo = subprocess.check_output(['ifconfig', net])
        hwstr = 'HWaddr '
        idx = netinfo.find(hwstr)
        if idx >= 0:
            start = idx+len(hwstr)
            return netinfo[start:start+17] # 5 colons plus 6 2character fields
    except:
        pass
    return "No MAC Address"

def get_ip(net=''):
    """
    Returns the IP address of 'net' if specified, otherwise 'wlan0', 'eth0', 'dnttun0', 'vpntun0', 'ppp0' whichever is found first.
    """
    try:
        arg = ['/sbin/ip', 'route', 'list']
        p = subprocess.Popen(arg, stdout=subprocess.PIPE)
        data,errdata = p.communicate()
        data = data.split('\n')
        list = ['wlan0', 'eth0', gv.radio_iface, gv.vpn_iface, 'ppp0'] if net == '' else [net]
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

def split_ip(ip):
    """If this is a valid IP address, return the 4 octets (or all zeros if a problem)"""

    octets = []
    ip += '.'
    try:
        for i in range(4):
            dot_idx = ip.find('.')
            if dot_idx == -1:
                return ('0','0','0','0')
            octets.append(ip[0:dot_idx])
            ip = ip[dot_idx+1:]
    except:
        return ('0','0','0','0')
    return (octets[0], octets[1], octets[2], octets[3])

def blink_led(address, on_time=0, off_time=0):
    if address - 0x60 in gv.in_bootloader:
        return
    try:
        i2c.i2c_write(address, i2c.LED, 1)  # turn on led
        time.sleep(on_time)
        i2c.i2c_write(address, i2c.LED, 0)  # turn off led
        time.sleep(off_time)
    except:
        pass

def light_ip(ip):
    """Blink the LED to convey the IP address.  For common private networks, try to minimize what is blinked."""
    if not gv.sd['light_ip']:
        return
    try:
        address = i2c.ADDRESS + 0 # only first board
        if 0 in gv.in_bootloader:
            return

        # only display trailing octets of common ip addresses
        base_blinks = 4
        for ip_base in [['10.', '0.', '0.'], ['172.'], ['192.', '168.', '0.']]:
            found_base = False
            for octet in ip_base:
                oct_len = len(octet)
                if len(ip) > oct_len and ip[:oct_len] == octet:
                    found_base = True
                    ip = ip[oct_len:]
                else:
                    break
            if found_base: # any match?
                break
            base_blinks += 2

        if ip == 'No IP Settings':
            ip = '....'

        i2c.i2c_write(address, i2c.LED, 0)  # turn off led
        time.sleep(1)
        for i in range(base_blinks):
            blink_led(address, .1, .1)
        for c in ip:
            time.sleep(2)
            if c >= '1' and c <= '9':
                ic = int(c)
                for i in range(ic):
                    blink_led(address, .6, .6)
            elif c == '0':
                blink_led(address, .1, .1)
            elif c == '.':
                for i in range(3):
                    blink_led(address, .2, .2)
            else: # unexpected
                for i in range(10):
                    blink_led(address, .1, .1)
                pass
    except:
        pass

def upnp_externalip(desc_url):
    """Return the external ip address associated with desc_url.  If not found, return '0.0.0.0'"""
    upnp_desc_out = subprocess.check_output(['/usr/bin/upnpc', '-u', desc_url, '-l'])
    desc_l = upnp_desc_out.split('\n')
    eipa = 'ExternalIPAddress = '
    for eip in desc_l:
        if eipa in eip:
            return eip[eip.find(eipa)+len(eipa):]
    return '0.0.0.0'

def update_upnp(cur_ip, logger, deletes=[],adds=[]):
    try:
        last_dot = cur_ip.rfind(".")
        if last_dot == -1:
            raise ValueError('Bad IP Address')
        short_cur_ip = cur_ip[0:last_dot]
        router_try = short_cur_ip + '.1'
        router_try = 'rootDesc.xml'
        router_ref = ''
        desc = 'desc: '

        args = ['/usr/bin/timeout', '3', '/usr/bin/upnpc', '-l']
        upnp_out = subprocess.check_output(args)
        l = upnp_out.split('\n')
        for e in l:
            # If we find a router ending in .1 use it.  Otherwise if we find only
            # one upnp device, use it.  Otherwise give up.
            if desc in e:
                desc_url = e[e.find(desc)+len(desc):]
                eip = upnp_externalip(desc_url)
                if eip == '0.0.0.0':
                    print 'upnp found 0.0.0.0 external ip for router_ref: ' + desc_url
                elif router_try in e:
                    router_ref = '-u ' + desc_url
                    print 'upnp found external ip: ' + eip + ' for root router_ref: ' + desc_url
                    break
                elif router_ref == '':
                    router_ref = '-u ' + desc_url
                    print 'upnp found external ip: ' + eip + ' for router_ref: ' + desc_url
                else:
                    router_ref == 'Multiple'
        if router_ref == '':
            raise ValueError('upnp Router Not Found: ' + upnp_out)
        elif router_ref == 'Multiple':
            raise ValueError('upnp Router Multiple Found: ' + upnp_out)
    except Exception as ex:
        logger.info('Could not update upnp: ' + str(ex))
        return

    for port in deletes:
        try:
            upnp_out = subprocess.check_output('/usr/bin/upnpc ' + router_ref + ' -d ' + str(port) + ' TCP', shell=True)
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
            upnp_out = subprocess.check_output("/usr/bin/upnpc " + router_ref + " -a " + cur_ip + ' ' + str(internal_port) + ' ' + str(external_port) + " TCP", shell=True)
            gv.logger.info('upnp added ip: ' + cur_ip + ' internal port: ' + str(internal_port) + ' external port: ' + str(external_port))
#            upnp_out = subprocess.check_output("upnpc " + router_ref + " -l | grep TCP", shell=True)
#            gv.logger.debug('upnp -l: ' + upnp_out)
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
        if gv.sd['master'] and gv.sd['external_proxy_port'] != 0:
            adds.append([9081, gv.sd['external_proxy_port']])
        update_upnp(cur_ip, gv.logger, [], adds)

def adjust_gv_nbrd(onbrd, radio_station=False):
    """Modify gv.sd['nbrd'] and related fields based on the onbrd parameter"""

    if radio_station:
        bd_idx = (gv.sd['nst']+7)//8
    else:
        bd_idx = (gv.sd['nst']-gv.sd['radiost'])//8
    st_idx = 8*bd_idx
    incr = onbrd - bd_idx
    if incr > 0:  # Lengthen lists
        for i in range(incr):
            gv.sd['mo'].insert(bd_idx, 0)
            gv.sd['ir'].insert(bd_idx, 0)
            gv.sd['iw'].insert(bd_idx, 0)
            gv.sd['show'].insert(bd_idx, 255)
        for i in range(incr*8,0,-1):
            if not radio_station:
                gv.snames.insert(st_idx, "S"+"{:0>2d}".format(i+st_idx))
            else:
                gv.snames.insert(st_idx, "R"+"{:0>2d}".format(i+gv.sd['radiost']))
            gv.snotes.insert(st_idx, '')
        for i, p in enumerate(gv.pd):
            for j in range(incr):
                p.insert(bd_idx+j+gv.p_station_mask_idx, 0)
            gv.pd[i] = p
        with gv.rs_lock:
            for i in range(incr * 8):
                gv.srvals.insert(st_idx, 0)
                gv.ps.insert(st_idx, [0, 0])
                gv.rs.insert(st_idx, [gv.rs_generic.copy()])
            for i in range(incr):
                gv.sbits.insert(bd_idx, 0)
    elif incr < 0:  # Shorten lists
        gv.logger.critical('adjust_gv_nbrd with negative incr: ' + str(incr))
#        gv.sd['mo'] = gv.sd['mo'][:onbrd]
#        gv.sd['ir'] = gv.sd['ir'][:onbrd]
#        gv.sd['iw'] = gv.sd['iw'][:onbrd]
#        gv.sd['show'] = gv.sd['show'][:onbrd]
#        for i, p in enumerate(gv.pd):
#            p_name = p[-1]
#            p = p[0:len(p)+incr]
#            p[-1] = p_name
#            gv.pd[i] = p
#        newlen = onbrd*8
#        with gv.rs_lock:
#            gv.srvals = gv.srvals[:newlen]
#            gv.ps = gv.ps[:newlen]
#            gv.rs = gv.rs[:newlen]
#            gv.sbits = gv.sbits[:onbrd]
#        gv.snames = gv.snames[:newlen]
#        gv.snotes = gv.snotes[:newlen]
    jsave(gv.pd, 'programs')
    jsave(gv.snames, 'snames')
    jsave(gv.snotes, 'snotes')
    jsave(gv.sd, 'sd')

def reset_wlan0(logger):
    try:
        logger.info('reset_wlan0')
        rc = subprocess.call(['killall', 'wpa_supplicant'])
        logger.info('kill supplicant: ' + str(rc))
        time.sleep(2)
        try:
            rc = subprocess.call(['ifup', 'wlan0'])
            logger.info('ifup: ' + str(rc))
        except:
            pass

        matches = 0
        for i in range(24):
            iwout = subprocess.check_output(['iwconfig', 'wlan0'])
            if 'ESSID:"' in iwout:
                matches += 1
                if matches == 2: # does it stay on for two rounds?  bad key will match once
                    return True
            else:
                matches = 0
            time.sleep(5)
    except Exception as ex:
        logger.info('reset_wlan0 exception: ' + str(ex))
    logger.info('reset_wlan0 failed to restart')
    return False

def reset_networking(logger):
    # todo drop rest of routine if reset_wlan0 works
    if reset_wlan0(logger): # try using reset_wlan0 first
        return
    logger.info('reset_networking')
    try:
        logger.info('systemctl daemon-reload')
        rc = subprocess.call(['systemctl', 'daemon-reload'])
        logger.info('completed systemctl daemon-reload: ' + str(rc))
    except:
        logger.exception('Failed to systemctl daemon-reload')

    try:
        logger.info('wpa_action wlan0 stop')
        rc = subprocess.call(['wpa_action', 'wlan0', 'stop'])
        logger.info( 'wpa_action wlan0 stop return: ' + str(rc))
        rc = subprocess.call(['ifdown', 'wlan0'])
        logger.info( 'ifdown return: ' + str(rc))
        time.sleep(2)
        logger.info('ifup wlan0')
        rc = subprocess.call(['ifup', 'wlan0'])
        logger.info( 'ifup return: ' + str(rc))
        time.sleep(1)
        rc = subprocess.call(['wpa_action', 'wlan0', 'reload'])
        logger.info('wpa_action wlan0 reload return: ' + str(rc))
        time.sleep(1)
#        logger.info('service networking restart')
#        rc = subprocess.call(['service', 'networking', 'restart'])
#        logger.info('completed service networking restart: ' + str(rc))
    except:
        logger.exception('Failed to restart networking...rebooting')
        reboot()

def network_exists(net):
    try:
        netinfo = subprocess.check_output(['ifconfig', net])
    except:
        return False
    return True

def network_up_wpa(net, logger):
    """Return ip address of network if it is up.  Otherwise empty string"""

    try:
        netinfo = subprocess.check_output(['wpa_cli', '-i', net, 'status'])
    except:
        netinfo = ''
    netlist = netinfo.split()
    for l in netlist:
        if logger:
            logger.info('wpa: ' + l[:])
        if 'ip_address=' in l:
            if logger:
                logger.info(l[:])
            return l[len('ip_address='):]
    return ''

def network_up_ifconfig(net, logger):
    """Return ip address of network if it is up.  Otherwise empty string"""

    try:
        netinfo = subprocess.check_output(['ifconfig', net])
    except:
        netinfo = ''
    netlist = netinfo.split('\n')
    for l in netlist:
        if logger:
            logger.info('ifconfig: ' + l[:])
        if 'inet addr:' in l:
            l = l.strip()
            l = l[len('inet addr:'):]
            sp_idx = l.find(' ')
            if logger:
                logger.info('inet addr: ' + l[:sp_idx])
            return l[:sp_idx]

    return ''

def network_up(net, logger=False):
    attempts = 2
    while attempts > 0:
        attempts -= 1
        time.sleep(2)
        if network_up_ifconfig(net, logger) != '':
            return True
        if network_up_wpa(net, logger) != '':
            return True
    return False

def get_rpi_revision():
    """
    Returns the hardware revision of the Raspberry Pi
    using the RPI_REVISION method from RPi.GPIO.
    """
    try:
        import RPi.GPIO as GPIO

        return GPIO.RPI_REVISION
    except ImportError:
        return 0

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise

def validate_fqdn(dn):
    """Return the valid domain name if it dn can be a fully qualified domain name (ie hostname)
      by trimming the extra space and changing intermediate spaces to dashes.  Otherwise return 'Irricloud'."""

    if dn.endswith('.'):
        dn = dn[:-1]
    dn = dn.strip()
    dn = '-'.join(dn.split())
    ldh_re = re.compile('^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$',
                        re.IGNORECASE)
    if all(ldh_re.match(x) for x in dn.split('.')) and len(dn) >= 1 and len(dn) <= 253:
        return dn
    return 'Irricloud'

def update_hostname(hn):
    gv.logger.info('gv changing name from ' + gv.sd['name'] + ' to: ' + hn)
    old_hostname = os.uname()[1]
    subprocess.call(['./hostname.sh', old_hostname, hn])

def update_tza(tz):
    with open('/etc/timezone','w') as file:
        file.write(tz+'\n')
    subprocess.call(['dpkg-reconfigure', '-f', 'non-interactive', 'tzdata'])

def check_rain():
    """
    Checks status of an installed rain sensor.
    
    Handles normally open and normally closed rain sensors
    
    Sets gv.sd['rs'] to 1 if rain is detected otherwise 0.
    """

    global pi
    try:
        if gv.sd['rst'] == 1:  # Rain sensor type normally open (default)
            if gv.use_pigpio:
                if not pi.read(pin_rain_sense):  # Rain detected
                    gv.sd['rs'] = 1
                else:
                    gv.sd['rs'] = 0
            else:
                if not GPIO.input(pin_rain_sense):  # Rain detected
                    gv.sd['rs'] = 1
                else:
                    gv.sd['rs'] = 0
        elif gv.sd['rst'] == 0:  # Rain sensor type normally closed
            if gv.use_pigpio:
                if pi.read(pin_rain_sense):  # Rain detected
                    gv.sd['rs'] = 1
                else:
                    gv.sd['rs'] = 0
            else:
                if GPIO.input(pin_rain_sense):  # Rain detected
                    gv.sd['rs'] = 1
                else:
                    gv.sd['rs'] = 0
    except NameError:
        pass

def get_remote_sensor_boards(since=310): # just over 5 min default
    """
    Return the set of remote sensor boards that have had updated information in the last since seconds.
    If since == 0, then return all boards.
    """

    rsb = []
    for name,data in gv.remote_sensors.iteritems():
        try:
            if since == 0 or gv.now-data['time'] < since:
                rsb.append(name)
        except:
            pass
    return rsb

def clear_mm():
    """
    Clear manual mode settings and stop any running zones.
    """
    from gpio_pins import set_output
    if gv.sd['mm']:
        with gv.rs_lock:
            stop_stations()
    return


def plugin_adjustment():
    """
    Sums irrigation time (water level) adjustments from multiple plugins.
    
    The adjustment value output from a plugin must be 
    a unique element in the gv.sd dictionary with a key starting with 'wl_'
    
    @rtype:   float
    @return:  Total irrigation time adjustments for all active plugins
    """
    duration_adjustments = [gv.sd[entry] for entry in gv.sd if entry.startswith('wl_')]
    result = reduce(lambda x, y: x * y / 100, duration_adjustments, 1.0)
    return result

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

def dtstring(start=None):
    if start == None:
        start = time.localtime(time.time())
    if gv.sd['tu'] == 'F':
        t = time.strftime("%m/%d/%Y at %H:%M:%S", start)
    else:
        t = time.strftime("%d.%m.%Y at %H:%M:%S", start)
    return t

def log_run():
    """
    Add run data to json log file - most recent first.
    
    If a record limit is specified (gv.sd['lr']) the number of records is truncated.  
    """

    if gv.lrun[1] == 98:
        pgr = _('Run-once')
        name = pgr
    elif gv.lrun[1] == 99:
        pgr = _('Manual')
        name = pgr
    else:
        pgr = str(gv.lrun[1])
        try:
            name = gv.pd[gv.lrun[1]-1][-1]
        except:
            name = 'Deleted Program'
    dur = str(timestr(gv.lrun[2]))
    start = time.gmtime(gv.now - gv.lrun[2])
    station = str(gv.lrun[0])

    if gv.sd['lg']:
        logline = '{"program":"' + pgr + '","programname":"' + name + '","station":' + station + ',"duration":"' + \
          timestr(gv.lrun[2]) + '","start":"' + time.strftime('%H:%M:%S","date":"%Y-%m-%d"', start) + '}'
        lines = []
        lines.append(logline + '\n')
        log = read_log()
        for r in log:
            lines.append(json.dumps(r) + '\n')
        with open('./data/wlog.json', 'w') as f:
            if gv.sd['lr']:
                f.writelines(lines[:gv.sd['lr']])
            else:
                f.writelines(lines)

    if gv.sd['teprogramrun'] and 'te' in gv.plugin_data and gv.plugin_data['te']['tesender']:
        station = gv.snames[int(station)]
        body = 'Station ' + station + ', Program ' + pgr + ', Duration ' + dur + ', Start time ' + dtstring(start)
        subject = "Report from Irricloud"
        gv.plugin_data['te']['tesender'].try_mail(subject, body)

def update_rs_order(sid):
    """
    Ensure the next action is the end of the list of gv.rs[sid] entries.

    We update the gv.ps time if it is set since we might be reordering items at
    the top of the stack.
    """

    # find the index of the entry that has the nearest start or stop time greater than now
    # bias stops before starts.  Any stop that is in the past goes to the top of the list.
    index = 0
    index_time = 0
    max_last_seq_sec = 0
    save_entry = {}
    for i in range(len(gv.rs[sid])):
        entry = gv.rs[sid][i]
        max_last_seq_sec = max(entry['rs_last_seq_sec'], max_last_seq_sec)
        if entry['rs_start_sec'] > gv.now:
            if index_time == 0 or entry['rs_start_sec']+.5 < index_time:
                index = i
                index_time = entry['rs_start_sec']+.5
                save_entry = entry
        if index_time == 0 or entry['rs_stop_sec'] < index_time:
            index = i
            index_time = entry['rs_stop_sec']
            save_entry = entry
    
    if index != 0 and index < len(gv.rs[sid])-1:
        gv.logger.debug('moving sid: ' + str(sid+1) + ' entry: ' + str(index) + ' start: ' + to_relative_time(save_entry['rs_start_sec']) + \
                        ' end: ' + to_relative_time(save_entry['rs_stop_sec']) + ' to end ')
        del gv.rs[sid][index]
        save_entry['rs_last_seq_sec'] = max_last_seq_sec
        gv.rs[sid].append(save_entry)

    if len(gv.rs[sid]) > 1:
        remaining = gv.rs[sid][len(gv.rs[sid])-1]['rs_stop_sec'] - max(gv.now, gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec'])
        if remaining != gv.ps[sid][1]:
            gv.logger.debug('change ps sid: ' + str(sid+1) + ' from: ' + to_relative_time(gv.ps[sid][1]) + ' to: ' + to_relative_time(remaining) + ' srval: ' + str(gv.srvals[sid]))
            gv.logger.debug('change ps sid: ' + str(sid+1) + ' cont start: ' + to_relative_time(gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec']) + \
                            ' stop: ' + to_relative_time(gv.rs[sid][len(gv.rs[sid])-1]['rs_stop_sec']))
            gv.ps[sid][1] = remaining
        else:
            gv.logger.debug('no change ps sid: ' + str(sid+1) + ' ps: '+ to_relative_time(remaining))


def prog_match(prog, day_only=False):
    """
    Test a program for current date and time match.
    """
    if prog[gv.p_flags]&1 == 0:
        return 0  # Skip if program is not enabled
    devday = gv.now // 86400  # Check day match
    lt = gv.nowt
    if (prog[gv.p_day_mask] >= 128) and (prog[gv.p_interval_day] > 1):  # Interval program
        if (devday % prog[gv.p_interval_day]) != (prog[gv.p_day_mask] - 128):
            return 0
    else:  # Weekday program
        if not (prog[gv.p_day_mask]-128) & (1<<lt[6]):
            return 0
        if prog[gv.p_day_mask] >= 128 and prog[gv.p_interval_day] == 0:  # even days
            if lt[2] % 2 != 0:
                return 0
        if prog[gv.p_day_mask] >= 128 and prog[gv.p_interval_day] == 1:  # Odd days
            if lt[2] == 31 or (lt[1] == 2 and lt[2] == 29):
                return 0
            elif lt[2] % 2 != 1:
                return 0
    if day_only: # only check for day match?
        return 1

    this_minute = (lt[3] * 60) + lt[4]  # Check time match
    if this_minute < prog[gv.p_start_time] or this_minute >= prog[gv.p_stop_time]:
        return 0
    if prog[gv.p_spread_min] == 0:
        return 0
    elif this_minute == prog[gv.p_start_time]:
        return 1
    return 0

def sequential_station_running():
    """ Return max time (at least now) of any station is running under a sequential invocation."""

    last_seq_run = gv.now
    with gv.rs_lock:
        for sid in range(gv.sd['nst']):
            for j in range(len(gv.rs[sid])-1,0,-1):
                last_seq_run = max(last_seq_run, gv.rs[sid][j]['rs_last_seq_sec'])
    return last_seq_run


def ban_sec(sid):
    """ Return max of this station's ban stop and ban delay."""

    last_ban_stop = 0
    last_ban_delay = 0
    with gv.rs_lock:
        for j in range(len(gv.rs[sid])-1,0,-1):
            last_ban_stop = max(last_ban_stop, gv.rs[sid][j]['rs_banstop_stop_sec'])
            last_ban_delay = max(last_ban_delay, gv.rs[sid][j]['rs_bandelay_stop_sec'])
    return (last_ban_stop, last_ban_delay)

def update_rs(sched_type, sid, start, stop, prog_id):
    """Either update the last entry by extending start and stop times, or create new entry and fill in data."""

    start = int(start)
    stop = int(stop)
    if stop < start:
        gv.logger.debug('update_rs: adjusting stop to be at least start ' + str(sid+1))
        stop = start

    if stop-start >= 86400: # indefinite program?  Ensure there is only 1
        for i in range(len(gv.rs[sid])-1,0,-1):
             if gv.rs[sid][i]['rs_stop_sec'] - gv.rs[sid][i]['rs_start_sec'] >= 86400:
                 return -1

    for i in range(len(gv.rs[sid])-1,-1,-1):
        if gv.rs[sid][i]['rs_schedule_type'] != sched_type or gv.rs[sid][i]['rs_start_sec'] == 0 or \
           start > gv.rs[sid][i]['rs_stop_sec'] or stop < gv.rs[sid][i]['rs_start_sec']:
            continue
        else:
            break

    if i == 0:
        gv.logger.debug('update_rs new entry sid: ' + str(sid+1) + ' prog: ' + str(prog_id) + \
                        ' start: ' + to_relative_time(start) + ' stop: ' + to_relative_time(stop))
        gv.rs[sid].append(gv.rs_generic.copy())
        i = len(gv.rs[sid])-1
        gv.rs[sid][i]['rs_start_sec'] = start
        gv.rs[sid][i]['rs_stop_sec'] = stop
        gv.rs[sid][i]['rs_program_id'] = prog_id
    else:
        gv.logger.debug('update_rs found existing entry sid: ' + str(sid+1) + ' i: ' + str(i) + ' prog: ' + str(prog_id) + \
                        ' start: ' + to_relative_time(start) + ' stop: ' + to_relative_time(stop))

    if start < gv.rs[sid][i]['rs_start_sec']:
        gv.logger.debug('update_rs start_sec decrease sid: ' + str(sid+1) + ' start from: ' + \
                            to_relative_time(gv.rs[sid][i]['rs_start_sec']) + ' to: ' + to_relative_time(start))
        gv.rs[sid][i]['rs_start_sec'] = int(start)
        gv.logger.debug('update_rs start_sec sid: ' + str(sid+1) + ' cont stop: ' + to_relative_time(gv.rs[sid][i]['rs_stop_sec']) + \
                        ' newstop: ' + to_relative_time(stop))
    if stop > gv.rs[sid][i]['rs_stop_sec']:
        gv.logger.debug('update_rs stop_sec increase sid: ' + str(sid+1) + ' stop from: ' + \
                        to_relative_time(gv.rs[sid][i]['rs_stop_sec']) + ' to: ' + to_relative_time(stop))
        gv.logger.debug('update_rs stop_sec sid: ' + str(sid+1) + ' cont start: ' + to_relative_time(gv.rs[sid][i]['rs_start_sec']) + \
                        ' newstart: ' + to_relative_time(start))
        gv.rs[sid][i]['rs_stop_sec'] = stop
    return i

def schedule_recurring_instances(pid, all_iters=False):
    """Capture all future times the program recurs in gv.recur and keep it sorted"""

    p = gv.pd[pid]
    if (p[gv.p_flags]&1) == 0: # disabled?
        return
    if p[gv.p_spread_min] <= 0:
        return
    try_time = p[gv.p_start_time]
    appended = False
    now_min = (gv.now % 86400) // 60
    if all_iters:
        xtra_iters = (p[gv.p_stop_time]-1-p[gv.p_start_time])//p[gv.p_spread_min]
        for i in range(xtra_iters):
            start = (i+1)*p[gv.p_spread_min]+now_min
            if start < 86400//60: # dont recur into next day
                gv.recur.append([start, pid])
                gv.logger.info('sched xtra iterations program: ' + p[-1] + ' time: ' + to_relative_time(start*60))
                appended = True
    elif now_min >= try_time: # if not all iterations and we are not yet at start time, do nothing
        while try_time < p[gv.p_stop_time]:
            if try_time > now_min and try_time < 86400/60:
                gv.recur.append([try_time, pid])
                gv.logger.info('sched_recur instance program: ' + p[-1] + ' time: ' + to_relative_time(try_time*60))
                appended = True
            try_time += p[gv.p_spread_min]
    if appended:
        gv.recur.sort(key=itemgetter(0))

def run_program(pid, ignore_disable=False):
    """Schedule the stations to run the program with 'pid'"""

    p = gv.pd[pid]
    if not ignore_disable and (p[gv.p_flags]&1) == 0: # disabled?
        return
    dur = p[gv.p_duration_sec]
    now_min = (gv.now % 86400) // 60
    rs = [[0,0] for i in range(gv.sd['nst'])] # program, duration indexed by station
    extra_adjustment = plugin_adjustment()
    for sid in range(gv.sd['nst']):  # check each station
        bid = sid // 8
        s = sid % 8
        if sid + 1 == gv.sd['mas']:  # skip if this is master valve
            continue
        if p[gv.p_station_mask_idx + bid] & 1 << s:  # if this station is scheduled in this program
            rs[sid][0] = pid + 1  # store program number in schedule
            rs[sid][1] = dur
            if (p[gv.p_flags]&2) == 0 and not gv.sd['iw'][bid] & 1 << s: # not ban program
                rs[sid][1] = int(rs[sid][1] * gv.sd['wl'] / 100 * extra_adjustment)
            if (p[gv.p_flags]&32) == 32:
                rs[sid][1] = -1 # use -1 for indefinite program
    schedule_stations(rs, p[gv.p_flags])


def schedule_stations(rsin, flags=5):
    """
    Schedule stations/valves/zones to run.  rsin is an array of program, duration indexed by station.

    This routine is expected to add or extend runtimes of stations that are already running and adjust the
    master as needed.

    'seq' mode is just used to schedule the stations in this list.  If we are here we have already decided
    that it is ok for these stations to run concurrently with what is already running.

    flags are the bits of the program's p_flags.
    """

    if gv.sd['rd'] or (gv.sd['urs'] and gv.sd['rs']):  # If rain delay or rain detected by sensor
        rain = True
    else:
        rain = False
    seq = (flags&4) == 0
    ban = (flags&2) == 2
    masid = gv.sd['mas'] - 1
    accumulate_time = gv.now if not seq else sequential_station_running()

    gv.logger.debug('schedule_stations start.  ban: ' + str(ban) + ' seq: ' + str(seq) + ' flags: ' + str(flags))

    # build up an ordered list of stations to run.
    station_list = []
    for sid in range(len(rsin)):
        b = sid >> 3
        s = sid % 8
        if sid == masid or rsin[sid][1] == 0:
            continue
        try_time = accumulate_time
        if not ban:
            if rain and not gv.sd['ir'][b] & (1<<s):  # if no rain or station ignores rain
                gv.logger.debug('skip scheduling sid: ' + str(sid+1) + ' due to rain')
                continue
            (ban_stop_sec, ban_delay_sec) = ban_sec(sid)
            if ban_stop_sec > try_time:
                gv.logger.debug('skip scheduling sid: ' + str(sid+1) + ' due to banstop')
                continue
            if ban_delay_sec > try_time:
                if ban_delay_sec//86400 == try_time//86400: # same day?
                    try_time = ban_delay_sec
                else:
                    gv.logger.debug('skip scheduling sid: ' + str(sid+1) + ' due to bandelay to next day')
                    continue # skip delay into next day
        station_list.append([try_time, sid])

    station_list.sort(key=itemgetter(0))
    first_master = -1
    last_master = first_master
    if not ban and masid >= 0:
        for tries in station_list:
            sid = tries[1]
            b = sid >> 3
            s = sid % 8
            if gv.sd['mo'][b] & (1 << (s - (s / 8) * 80)):
                if first_master == -1:
                    first_master = sid
                last_master = sid

    with gv.rs_lock:
        for tries in station_list:
            sid = tries[1]
            b = sid >> 3
            s = sid % 8
            accumulate_time = max(accumulate_time, tries[0])
            if first_master == sid:
                if gv.sd['mton'] < 0:
                    if accumulate_time - gv.now < -gv.sd['mton']:
                        accumulate_time += -gv.sd['mton'] - (accumulate_time - gv.now)
                master_start = accumulate_time + gv.sd['mton']
                master_stop = master_start + gv.sd['mtoff']
                update_rs('active', masid, master_start, master_stop, rsin[sid][0])
                update_rs_order(masid)

            if seq:  # sequential mode, stations run one after another
                idx = update_rs('active', sid, accumulate_time, accumulate_time+rsin[sid][1], rsin[sid][0])
                accumulate_time += rsin[sid][1]  # add duration
                if idx >= 0:
                    gv.rs[sid][idx]['rs_last_seq_sec'] = max(gv.rs[sid][idx]['rs_last_seq_sec'], gv.rs[sid][idx]['rs_stop_sec'])
                if last_master == sid:
                    master_stop = accumulate_time+gv.sd['mtoff']
                    update_rs('active', masid, master_start, master_stop, rsin[sid][0])
                    update_rs_order(masid)
                accumulate_time += gv.sd['sdt']  # add station delay
            else:  # concurrent mode, stations allowed to run in parallel
                if not ban:
                    # make indefinite program have at least 2 days until stop.  At the end of each day,
                    # sip.py will extend the stop time
                    stop_time = accumulate_time
                    stop_time += 2*86400 if rsin[sid][1] == -1 else rsin[sid][1]
                    update_rs('active', sid, accumulate_time, stop_time, rsin[sid][0])
                    if masid >= 0 and gv.sd['mo'][b] & (1<<(s - (s / 8) * 80)):  # Master settings
                        master_stop = stop_time + gv.sd['mtoff']
                        update_rs('active', masid, master_start, master_stop, rsin[sid][0])
                        update_rs_order(masid)
                elif (flags&8) == 8:
                    gv.logger.debug('ban with stop prog: ' + str(rsin[sid][0]) + ' dur: ' + to_relative_time(rsin[sid][1]) + ' sid: ' + str(sid+1))
                    stop_station(sid, **{'stop_active':1})
                    idx = update_rs('banstop', sid, accumulate_time, accumulate_time+rsin[sid][1], rsin[sid][0])
                    if idx >= 0:
                        gv.rs[sid][idx]['rs_banstop_stop_sec'] = max(accumulate_time+rsin[sid][1], gv.rs[sid][idx]['rs_banstop_stop_sec'])
                elif (flags&16) == 16:
                    delay_until = int(accumulate_time+rsin[sid][1])
                    gv.logger.debug('ban with delay until: ' + to_relative_time(delay_until) + ' prog: ' + str(rsin[sid][0]) + ' dur: ' + to_relative_time(rsin[sid][1]) + ' sid: ' + str(sid+1))
                    stop_station(sid, **{'delay_active':delay_until})
                    idx = update_rs('bandelay', sid, accumulate_time, delay_until, rsin[sid][0])
                    if idx >= 0:
                        gv.rs[sid][idx]['rs_bandelay_stop_sec'] = max(delay_until, gv.rs[sid][idx]['rs_bandelay_stop_sec'])
            update_rs_order(sid)
    gv.sd['bsy'] = 1
    gv.logger.debug('schedule_stations end')
    return


def stop_onrain():
    """
    Stop stations that do not ignore rain.
    """

    from gpio_pins import set_output
    do_set_output = False
    with gv.rs_lock:
        for sid in range(gv.sd['nst']):
            bid = sid // 8
            s = sid % 8
            if gv.sd['ir'][bid] & (1<<s):  # if station ignores rain...
                continue
            else:
                if stop_station(sid, **{'no_set_output':1, 'stop_active':1}):
                    do_set_output = True

        if do_set_output:
            set_output()

    return

def stop_station(sid, **kwargs):
    """
    Stop the single station sid.  Return true if station was active.
    """
    from gpio_pins import set_output
    b = sid >> 3
    s = sid % 8
    with gv.rs_lock:
        if (gv.sbits[b]&(1<<s)) != 0:
            gv.sbits[b] &= ~(1<<s)  # Clears stopped stations from display
        was_active = gv.srvals[sid]
        if was_active:
            gv.srvals[sid] = 0
            if 'no_set_output' not in kwargs:
                set_output()
            if 'delay_active' in kwargs:
                gv.logger.info('delay_station sid: ' + str(sid+1))
            else:
                gv.logger.info('stop_station sid: ' + str(sid+1))

        if len(gv.rs[sid]) == 1: # nothing here?
            return False

        gv.logger.debug('stop_station ps_reset: change ps sid: ' + str(sid+1) + ' from: ' + to_relative_time(gv.ps[sid][1]) + ' to: 0')
        gv.ps[sid] = [0, 0] # might be future program being deleted

        if gv.sd['mas']-1 != sid:
            if gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec'] < gv.now:  # fill out log
                gv.lrun[0] = sid
                gv.lrun[1] = gv.rs[sid][len(gv.rs[sid])-1]['rs_program_id']
                gv.lrun[2] = int(gv.now - gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec'])
                gv.lrun[3] = gv.now
                log_run()
            else:
                gv.logger.info('stop_station: clearing delayed program: ' + str(sid+1))

        if 'delay_active' in kwargs:
            for i in range(len(gv.rs[sid])-1,0,-1):
                prog_id = gv.rs[sid][i]['rs_program_id']
                p = None if prog_id > len(gv.pd) else gv.pd[prog_id-1]
                if gv.rs[sid][i]['rs_schedule_type'] == 'active':
                    if p == None or (p[gv.p_flags]&2) == 0:  # not a ban
                        remaining = gv.rs[sid][i]['rs_stop_sec'] - int(max(gv.now, gv.rs[sid][i]['rs_start_sec']))
                        if gv.rs[sid][i]['rs_start_sec'] >= kwargs['delay_active']: # already after delay? leave alone
                            gv.logger.info('station ' + str(sid+1) + ' already starting after delay...leave')
                            continue;
                        new_start = kwargs['delay_active']
                        del gv.rs[sid][i]
                        if p != None and (p[gv.p_flags]&4) == 0:
                            new_start = max(new_start, sequential_station_running())
                            if new_start//86400 == gv.now//86400: # do not delay to new day
                                idx = update_rs('active', sid, new_start, new_start+remaining, prog_id)
                                if idx >= 0:
                                    gv.rs[sid][idx]['rs_last_seq_sec'] = max(gv.rs[sid][idx]['rs_last_seq_sec'], gv.rs[sid][idx]['rs_stop_sec'])
                                    gv.logger.info('delayed sid: ' + str(sid+1) + ' for: ' + str(new_start-gv.now) + \
                                                   ' new start: ' + to_relative_time(new_start) + ' dur: ' + to_relative_time(remaining))
                            else:
                                gv.logger.info('skipping seq delay to next day sid: ' + str(sid+1))
                        elif new_start//86400 == gv.now//86400: # do not delay to new day
                            update_rs('active', sid, new_start, new_start+remaining, prog_id)
                        else:
                            gv.logger.info('skipping delay to next day sid: ' + str(sid+1))
        elif 'stop_only_current' in kwargs:
            del gv.rs[sid][len(gv.rs[sid])-1]
        else:
            for i in range(len(gv.rs[sid])-1,0,-1):
                if 'stop_all' in kwargs:
                    del gv.rs[sid][i]
                elif gv.rs[sid][i]['rs_schedule_type'] == 'active' and 'stop_active' in kwargs:
                    del gv.rs[sid][i]
                else:
                    gv.logger.info('stop_station: not ready to stop bans that are not current')

        update_rs_order(sid)
        gv.logger.debug('stop_station new top sid: ' + str(sid+1) + \
                        ' start: ' + to_relative_time(gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec']) + \
                        ' stop: '  + to_relative_time(gv.rs[sid][len(gv.rs[sid])-1]['rs_stop_sec']))
        if was_active:
            return True

    return False

def stop_stations():
    """
    Stop all running stations, clear schedules.
    """
    from gpio_pins import set_output
    do_set_output = False
    with gv.rs_lock:
        for sid in range(gv.sd['nst']):
            if stop_station(sid, **{'no_set_output':1, 'stop_all':1}):
                do_set_output = True

        gv.sd['bsy'] = 0
        if do_set_output:
            set_output()
    return

def read_log(name='wlog', end_date='', days_before=0):
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
        with io.open('./data/'+name+'.json') as logf:
            lines = logf.readlines()
            records = []
            cur_line = ''
            # deal with newlines in middle of json stuff.  Ignore bug if our line ends with non-json }
            for l in lines:
                cur_line += l
                if l[len(l)-2] == '}': # ignore newline at -1
                    records.append(cur_line)
                    cur_line = ''

            count = 0
            for i in records:
                count += 1
                try:
                    rec = json.loads(i)
                except:
#                    print 'wlog.json record: ', count, ' record: ' , i
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


def station_names():
    """
    Load station names from /data/snames.json file if it exists
    otherwise create file with defaults.
    
    Return station names as a list.
    
    """
    try:
        with open('./data/snames.json', 'r') as snf:
            return json.load(snf)
    except IOError:
        stations = []
        for i in range(gv.sd['nst']):
            stations.append("S"+"{:0>2d}".format(i+1))
        jsave(stations, 'snames')
        return stations

def station_notes():
    """
    Load station notes from /data/snotes.json file if it exists
    otherwise create file with defaults.
    
    Return station notes as a list.
    
    """
    try:
        with open('./data/snotes.json', 'r') as snf:
            return json.load(snf)
    except IOError:
        snotes = [''] * gv.sd['nst']
        jsave(snotes, 'snotes')
        return snotes

def load_programs():
    """
    Load program data into memory from /data/programs.json file if it exists.
    otherwise create an empty programs data list (gv.pd).
    
    """
    try:
        with open('./data/programs.json', 'r') as pf:
            gv.pd = json.load(pf)
    except IOError:
        gv.pd = []  # A config file -- return default and create file if not found.
        with open('./data/programs.json', 'w') as pf:
            json.dump(gv.pd, pf)
    return gv.pd


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

        remote = web.ctx.env['REMOTE_ADDR']
        (ten,base,s0,s1) = split_ip(remote)
        if gv.sd['slave']:
            if remote == gv.sd['master_ip'] or remote == '127.0.0.1' or '10.1.128.' in remote:
                gv.logger.debug('check_login for slave success from master_ip: ' + gv.sd['master_ip'])
                return True
            elif ten == '10' and s0 == '254' and s1 == '1' and int(base) > 1: # base radio
                gv.logger.debug('check_login for slave success from proxy: ' + remote)
                return True

        if gv.sd['master']:
            try:
                for i in range(1, len(gv.plugin_data['su']['subinfo'])):
                    sub = gv.plugin_data['su']['subinfo'][i]
                    if remote == sub['ip']:
                        gv.logger.debug('check login for master success from slave ip: ' + sub['ip'] + ' name: ' + sub['name'])
                        return True

            except Exception as ex:
                gv.logger.info('check_login master exception remote: ' + remote + ' ex: ' + str(ex))

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
    else:
        gv.logger.info('check login failed from remote: ' + remote + ' for system: ' + gv.sd['name'])

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

def log_event(msg):
    """
    Add run data to json log file - most recent first.
    
    If a record limit is specified (gv.sd['lr']) the number of records is truncated.  
    """

    print msg
    if gv.sd['lg']:
        logline = '{'+time.strftime('"time":"%H:%M:%S","date":"%Y-%m-%d"', time.gmtime(gv.now)) + ',"mode":"' + gv.sd['mode'] + '","message":"' + msg + '"}'
        gv.logger.info(logline)
        lines = []
        lines.append(logline + '\n')
        log = read_log()
        for r in log:
            lines.append(json.dumps(r) + '\n')
        with open('./data/wlog.json', 'w') as f:
            if gv.sd['lr']:
                f.writelines(lines[:gv.sd['lr']])
            else:
                f.writelines(lines)
