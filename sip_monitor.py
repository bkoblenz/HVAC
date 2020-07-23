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
from helpers import network_up, reset_networking, light_ip, get_ip, blink_led, reboot
import logging
import logging.handlers
import i2c
import thread

def light_vsb_boards(delay=2):
    boards = i2c.get_vsb_boards()
    for board, version in boards.items():
        if board not in gv.in_bootloader:
            address = i2c.ADDRESS + board
            blink_led(address, delay, 0)

def ps_list(proc):
    """Return ps output for processes named proc"""

    ps_info = subprocess.check_output("/bin/ps auwx | /bin/grep -e " + proc, shell=True)
    l = ps_info.split('\n')
    new_l = []
    for e in l:  # remove all grep references
        if 'grep -e' not in e and e != '':
            new_l.append(e)
    return new_l

def check_factory_reset():
    """Check every five seconds to see if we need to do a factory reset"""

    while True:
        time.sleep(5)
        try:
            factory_reset = False
            i2c_reset = False
            try:
                # if factory_reset file is present or hw button is pushed.
                if pi.read(button_pin) == 0:
                    factory_reset = True
                    logger.warning('button factory reset')
                else:
                    try:
                        with open('data/factory_reset','r') as f:
                            factory_reset = True
                            logger.warning('file factory reset')
                        if factory_reset:
                            subprocess.call(['rm', 'data/factory_reset'])
                    except:
                        pass
            except Exception as ex:
                logger.warning('button read failure: ' + str(ex))

            print 'checking factory reset', factory_reset
            if factory_reset:
                # Factory reset request
                logger.warning('factory reset')
                light_vsb_boards()
                try:
                    type = ''
                    usbs = subprocess.check_output(['lsusb'])
                    usbl = usbs.split('\n')
                    for usb in usbl:
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
                logger.info('restoring orig /etc/network/interfaces and /etc/wpa_supplicant/wpa_supplicant.conf')
                shutil.copy2('/etc/network/interfaces.orig', '/etc/network/interfaces')
                shutil.copy2('/etc/wpa_supplicant/wpa_supplicant.conf.orig', '/etc/wpa_supplicant/wpa_supplicant.conf')
                shutil.copy2('/etc/dhcpcd.conf.orig', '/etc/dhcpcd.conf')
                logger.info('starting sip_net_start')
                rc = subprocess.call(['/usr/bin/python', '/home/pi/Irricloud/sip_net_start.py']) # leads to reboot
                logger.debug( 'sip_net_start rc: ' + str(rc))
                time.sleep(10)
            else:
                try:
                    with open('data/i2c_reset','r') as f:
                        i2c_reset = True
                        logger.warning('file i2c_reset')
                        if i2c_reset:
                            subprocess.call(['rm', 'data/i2c_reset'])
                except:
                    pass

                print 'checking i2c reset', i2c_reset
                if i2c_reset:
                    # Factory reset request
                    boards = i2c.get_vsb_boards()
                    for board, version in boards.items():
                        if board not in gv.in_bootloader:
                            logger.warning('i2c reset board: ' + str(board+1))
                            i2c.i2c_reset(i2c.ADDRESS+board)
                        else:
                            logger.critical('i2c reset aborted board: ' + str(board+1) + ' in bootloader version: ' + hex(version))
                    logger.info('reboot.')
                    reboot(0, True)

        except Exception as ex:
            logger.critical('check_factory_reset exception: ' + str(ex))

def program_pid(program):
    """Return the pid for program if it is running, otherwise return 0"""

    ps_program_list = ps_list(program)
    found_program = 0
    for entry in ps_program_list:
        if 'python '+program in entry or '/usr/sbin/'+program in entry:
            found_program = entry.split()[1]
    return found_program

if __name__ == "__main__":

    log_levels = { 'debug':logging.DEBUG,
            'info':logging.INFO,
            'warning':logging.WARNING,
            'error':logging.ERROR,
            'critical':logging.CRITICAL,
            }

    log_file = 'logs/irricloud_monitor.out'
    level = logging.DEBUG
    fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=gv.MB, backupCount=5)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger = logging.getLogger('sip_monitor')
    logger.addHandler(fh)
    gv.logger.addHandler(fh)
    logger.setLevel(level) 
    if len(sys.argv) > 1:
        level_name = sys.argv[1]
        if level_name in log_levels:
            level = log_levels[level_name]
            logger.setLevel(level) 
        else:
            logger.critical('Bad parameter to sip_monitor: ' + level_name)

    logger.critical('Starting')
    light_vsb_boards()
    time.sleep(15) # give time for network to come up
    light_ip(get_ip())
    pi = pigpio.pi()
    button_pin = 21
    pi.set_mode(button_pin, pigpio.INPUT)
    pi.set_pull_up_down(button_pin, pigpio.PUD_UP)

    reset_skip = 10
    sip_restart_skip = 2
    sip_net_finish_skip = 2
    last_net_reset = timegm(time.localtime())

    cmds = [['rm', 'data/substation_proxy_pause'],
            ['/etc/init.d/bind9', 'stop'], # only run with main radio
            ['update-rc.d', 'sip', 'remove'],
            ['update-rc.d', 'sip_net_finish', 'remove'],
            ['update-rc.d', 'sip_monitor', 'defaults'],
            ['sysctl', 'net.ipv4.tcp_thin_linear_timeouts=1'], # not proven necessary (or problematic)
            ['chattr', '-i', '/etc/resolv.conf'], # allow overwriting resolv.conf
           ]
    for cmd in cmds:
        try:
            subprocess.call(cmd)
            logger.info('executed ' + ' '.join(cmd))
        except:
            pass

    thread.start_new_thread(check_factory_reset, ())
    while True:
        try:
            try:
                found_proxy = program_pid('substation_proxy.py')
                if found_proxy == 0:
                    try:
                        with open('data/substation_proxy_pause','r') as f:
                            logger.info('substation_proxy paused')
                            pass # presence of file prevents proxy from restartng
                    except:
                        logger.info('starting substation_proxy')
                        subprocess.call(['/etc/init.d/substation_proxy', 'start'], stderr=subprocess.STDOUT)
            except:
                pass

            nowt = time.localtime()
            now = timegm(nowt)

#            if now - last_net_reset > 360:
#                last_net_reset = now
#                logger.info('do reset')
#                reset_networking(logger)

            found_sip = program_pid('sip.py')
            if found_sip != 0:
                sip_restart_skip = 2
            found_net_conf = program_pid('sip_net_finish.py')

            try:
                subprocess.check_output(['/bin/grep', '10.0.0.1', '/etc/network/interfaces'], stderr=subprocess.STDOUT)
                config_mode = True
            except:
                config_mode = False
            if config_mode and found_net_conf == 0:
                if found_sip != 0:
                    logger.critical('found sip running....kill it: ' + str(found_sip))
                    subprocess.call(['/bin/kill', '-9', str(found_sip)], stderr=subprocess.STDOUT)
                logger.info('starting sip_net_finish')
                rc = subprocess.call(['/etc/init.d/sip_net_finish', 'start'])
                logger.debug( 'sip_net_finish rc: ' + str(rc))
                time.sleep(15)
            elif not config_mode and found_sip == 0:
                if found_net_conf != 0:
                    logger.critical('found sip_net_finish running....kill it: ' + str(found_net_conf))
                    subprocess.call(['/bin/kill', '-9', str(found_net_conf)], stderr=subprocess.STDOUT)
                logger.info('starting sip')
                rc = subprocess.call(['/etc/init.d/sip', 'start'])
                logger.debug( 'sip rc: ' + str(rc))
                time.sleep(15)

            # kill any hostapd or dnsmasq processes hanging around
            if not config_mode:
                for cmd in ['hostapd', 'dnsmasq']:
                    try:
                        cmd_pid = program_pid(cmd)
                        if cmd_pid != 0:
                            logger.critical('found ' + cmd + ' running....kill it: ' + str(cmd_pid))
                            subprocess.call(['/bin/kill', '-9', str(cmd_pid)], stderr=subprocess.STDOUT)
                    except:
                        pass

            if not network_up('wlan0') and not network_up('eth0'):
                if reset_skip == 0:
                    logger.warning('sip_monitor: resetting network')
                    network_up('wlan0', logger) # save network info when resetting
                    reset_skip = 10 # throw in some delay
                    reset_networking(logger)
                elif reset_skip > 0:
                    reset_skip -= 1
            else:
                reset_skip = 10 # throw in some delay

        except Exception as ex:
            logger.exception('in except' + str(ex))

        time.sleep(5)
