# !/usr/bin/env python
# -*- coding: utf-8 -*-

import subprocess
import shutil
import time
from calendar import timegm
import pigpio
import sys
import getopt
import gv
from helpers import network_up
import logging

if __name__ == "__main__":

    def ps_list(proc):
        """Return ps output for processes named proc"""

        ps_info = subprocess.check_output("ps auwx | grep -e " + proc, shell=True)
        l = ps_info.split('\n')
        new_l = []
        for e in l:  # remove all grep references
            if 'grep -e' not in e and e != '':
                new_l.append(e)
        return new_l

    log_levels = { 'debug':logging.DEBUG,
            'info':logging.INFO,
            'warning':logging.WARNING,
            'error':logging.ERROR,
            'critical':logging.CRITICAL,
            }

    log_file = 'logs/boiler_monitor.out'
    level = logging.DEBUG
    fh = logging.FileHandler(log_file)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger = logging.getLogger('boiler_monitor')
    logger.addHandler(fh)
    logger.setLevel(level) 
    if len(sys.argv) > 1:
        level_name = sys.argv[1]
        if level_name in log_levels:
            level = log_levels[level_name]
            logger.setLevel(level) 
        else:
            logger.critical('Bad parameter to boiler_monitor: ' + level_name)

    logger.critical('Starting')

    # wait for other things to get started
    time.sleep(4)
    pi = pigpio.pi()
    button_pin = 21
    pi.set_mode(button_pin, pigpio.INPUT)
    pi.set_pull_up_down(button_pin, pigpio.PUD_UP)

    reset_skip = 0
    boiler_restart_skip = 1
    boiler_net_finish_skip = 1
    last_hostapd_reset = timegm(time.localtime())
    last_ntp_reset = 0 # force immediate reset

    while True:
        try:
            factory_reset = False
            try:
                # if factory_reset file is present or hw button is pushed.
                if pi.read(button_pin) == 0:
                    factory_reset = True
                else:
                    try:
                        with open('data/factory_reset','r') as f:
                            factory_reset = True
                        if factory_reset:
                            subprocess.call(['rm', 'data/factory_reset'])
                    except:
                        pass
            except Exception as ex:
                logger.warning('button read failure: ' + str(ex))

            if factory_reset:
                # Factory reset request
                logger.warning('factory reset')
                try:
                    type = ''
                    usbs = subprocess.check_output(['lsusb'])
                    usbl = usbs.split('\n')
                    for usb in usbl:
                        print usb
                        if 'Ralink Technology' in usb:
                            type = 'CanaKit' if type == '' else 'Unknown'
                        elif 'Realtek Semiconductor' in usb:
                           type = 'TP' if type == '' else 'Unknown'

                    if type != '' and type != 'Unknown':
                        for file in ['/etc/hostapd/hostapd.conf', '/usr/sbin/hostapd', '/etc/wpa_supplicant/functions.sh']:
                            rc = subprocess.call(['diff', file, file+'-'+type])
                            if rc == 0:
                                logger.debug(file + ' and ' + file+'-'+type + ' match')
                            else:
                                logger.critical('Rebinding ' + file + ' to match ' + file+'-'+type)
                                subprocess.call(['rm', file])
                                subprocess.call(['ln', '-s', file+'-'+type, file])
                    else:
                        logger.info('leaving hostapd and wpa_supplicant unchanged.  type: ' + type)
                         
                except Exception as ex:
                    logger.exception('Could not validate/change hostapd and wpa_supplicant usage: ' + str(ex))
                logger.info('removing boiler and restoring orig /etc/network/interfaces and /etc/wpa_supplicant/wpa_supplicant.conf')
#                subprocess.call(['update-rc.d', 'boiler', 'remove'])
                shutil.copy('/etc/network/interfaces.orig', '/etc/network/interfaces')
                shutil.copy('/etc/wpa_supplicant/wpa_supplicant.conf.orig', '/etc/wpa_supplicant/wpa_supplicant.conf')
                logger.info('starting boiler_net_start')
                rc = subprocess.call(['/usr/bin/python', '/home/pi/HVAC/boiler_net_start.py']) # leads to reboot
                logger.debug( 'boiler_net_start rc: ' + str(rc))
                time.sleep(10)

            nowt = time.localtime()
            now = timegm(nowt)
            if now - last_ntp_reset > 24*60*60:  # seems to be working, but reset once per day
                ps_ntp_list = ps_list('/usr/sbin/ntpd')
                if len(ps_ntp_list) > 0:
                    try:
                        subprocess.call("/etc/init.d/openntpd stop", shell=True, stderr=subprocess.STDOUT)
                    except Exception as ex:
                        logger.critical('could not stop openntpd: ' + str(ex))
                else:
                    try:
                        last_ntp_reset = now # mark now to wait another day even if fail
                        subprocess.call("/etc/init.d/openntpd start", shell=True, stderr=subprocess.STDOUT)
                        logger.info('restarted openntpd')
                    except Exception as ex:
                        logger.critical('could not restart openntpd: ' + str(ex))

            ps_boiler_list = ps_list('boiler.py')
            ps_conf_list = ps_list('boiler_net_finish.py')
            found_boiler = 0
            for entry in ps_boiler_list:
                if '/usr/bin/python' in entry:
                    boiler_restart_skip = 1 # reset delay once boiler is running
                    found_boiler = entry.split()[1]
            found_net_conf = 0
            for entry in ps_conf_list:
                if '/usr/bin/python' in entry:
                    found_net_conf = entry.split()[1]

            if found_boiler == 0:
                boiler_net_finish_skip = 1
                if found_net_conf == 0:
                    # in case boiler is restarting and we had bad timing, give it two tries
                    if boiler_restart_skip == 0:
                        logger.info('starting boiler_net_start')
                        rc = subprocess.call(['/usr/bin/python', '/home/pi/HVAC/boiler_net_start.py']) # leads to reboot
                        logger.debug( 'boiler_net_start rc: ' + str(rc))
                        boiler_restart_skip = 10
                    else:
                        boiler_restart_skip -= 1
                else:
                    try:
                        nows = time.strftime('%Y-%m-%d:%H:%M:%S', nowt)
                        with open('data/last_get','r') as infile:
                            iface = infile.readlines()
                        sp_idx = iface[0].find(" ")
                        gettime = iface[0][:sp_idx].strip()
                        last_msg = iface[0][sp_idx+1:].strip()
                        if now-last_hostapd_reset > 5*60:
                            try:
                                logger.debug('reset ap: now: ' + nows + ' get: ' + gettime + ' msg: ' + last_msg + ' diff: ' + str(now-last_hostapd_reset))
                                rc = subprocess.call("/etc/init.d/hostapd stop", shell=True, stderr=subprocess.STDOUT)
                                logger.debug('hostapd stopped: ' + str(rc))
                                rc = subprocess.call(['wpa_action', 'wlan0', 'stop'])
                                logger.debug('wpa_action stopped: ' + str(rc))
                                rc = subprocess.call(['ifconfig', 'wlan0', '10.0.0.1'])
                                logger.debug('ifconfig 10.0.0.1: ' + str(rc))
                                rc = subprocess.call(['ifup', 'wlan0'])
                                logger.debug('ifup: ' + str(rc))
                            except Exception as ex:
                                logger.exception('reset_network: Unexpected exception trying to reset network: ' + str(ex))
                            subprocess.call("/etc/init.d/hostapd start", shell=True, stderr=subprocess.STDOUT)
                            last_hostapd_reset = now
                    except Exception as ex:
                        logger.exception('could not read data/last_get: ' + str(ex))

            elif found_net_conf != 0:
                logger.critical('unexpected boiler_net_finish running with boiler')
                if boiler_net_finish_skip == 0:
                    logger.info('boiler_net_finish rebooting')
                    subprocess.call(['reboot', '-h'])
                else:
                    boiler_net_finish_skip -= 1
#                logger.info('removing boiler_net_finish')
#                subprocess.call(['update-rc.d', 'boiler_net_finish', 'remove'])
                logger.info('stopping boiler_net_finish')
                subprocess.call("/etc/init.d/boiler_net_finish stop", shell=True, stderr=subprocess.STDOUT)
                logger.info('stopped boiler_net_finish')
            else:
                # make sure network is up as best as possible
#                logger.debug('sip running....check network, then do nothing')
                attempt_reset = not network_up('wlan0') and not network_up('eth0')
                if attempt_reset and reset_skip == 0:
                    logger.warning('boiler_monitor: resetting network')
                    reset_skip = 2 # throw in some delay
                    try:
                        logger.debug('down/up')
                        subprocess.call(['wpa_action', 'wlan0', 'stop'])
                        subprocess.call(['ifdown', 'eth0'])
                        time.sleep(2)
                        subprocess.call(['ifup', 'wlan0'])
                        subprocess.call(['ifup', 'eth0'])
                    except:
                        logger.error('boiler_monitor: reset failed')
                        pass
                elif reset_skip > 0:
                    reset_skip -= 1

        except Exception as ex:
            logger.exception('in except' + str(ex))

        time.sleep(5)
