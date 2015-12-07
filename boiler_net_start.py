# !/usr/bin/env python
# -*- coding: utf-8 -*-

import subprocess
import shutil
import time
import web
import json
import i18n
import ast
import gv
from helpers import network_up, network_exists, get_cpu_temp, jsave
import sys
import logging

# make /etc/network/interfaces broadcast Irricloud Setup as SSID on wlan0
def establish_broadcast_ip():
    try:
        outdata = []
        drop = 0
        with open('/etc/network/interfaces','r') as infile:
            iface = infile.readlines()
            for line in iface:
                if drop > 0:
                    drop -= 1
                    continue
                if 'iface wlan0 inet' in line:
                    if 'static' in line:
                        drop = 3
                    outdata.append('iface wlan0 inet static\n')
                    outdata.append('        address 10.0.0.1\n')
                    outdata.append('        netmask 255.255.255.0\n')
                    line = '        gateway 10.0.0.1\n'
                    drop += 1 # remove wpa-conf line so wpa_supplicant does not run in broadcast mode
                outdata.append(line)

        logger.debug('write /etc/network/interfaces with wlan0 as 10.0.0.1')
        with open('/etc/network/interfaces','w') as outfile:
           outfile.writelines(outdata)
        # the following is not understood why it is necessary
        subprocess.call(['ifconfig', 'wlan0', '10.0.0.1'])

    except Exception as ex:
        logger.exception('establish_broadcast_ip: Unexpected exception trying to create access point: ' + str(ex))
        raise ex

def reset_network():
    logger.debug('reset networks')
    try:
        rc = subprocess.call(['wpa_action', 'wlan0', 'stop'])
        time.sleep(1)
        rc = subprocess.call(['ifup', 'wlan0'])
        time.sleep(2)

    except Exception as ex:
        logger.exception('reset_network: Unexpected exception trying to reset network: ' + str(ex))
        raise ex

def collect_networks():
    try:
        wlan_info = subprocess.check_output(['iwlist', 'wlan0', 'scan'])
    except:
        wlan_info = ''
    wlan_list = wlan_info.split('\n')
    last_net = ''
    last_sec = ''
    last_qual = 0.0
    net_listd = {}
    for i in range(len(wlan_list)):
        entry = wlan_list[i].strip()
        if entry.startswith('ESSID:'):
            last_net = entry[len('ESSID:')+1:] # drop start quote
            last_net = last_net[0:len(last_net)-1] #drop end quote
        elif entry.startswith('IE: IEEE 802.11'):
            last_sec = entry[len('IE: IEEE 802.11')+2:] # drop i/
            if not last_sec.startswith('WPA2 Version 1') and not last_sec.startswith('WPA Version 1'):
                last_sec = ''
        elif entry.startswith('Quality='):
            qual = entry[len('Quality='):]
            sp_idx = qual.find(" ")
            qual = qual[:sp_idx]
            sl_idx = qual.find("/")
            try:
                last_qual = float(qual[:sl_idx])/float(qual[sl_idx+1:])
            except:
                pass
        elif entry.startswith('Cell '):
            if last_net != '' and last_sec != '': #only WPA and WPA2
                net_listd[last_net] = last_qual
            last_net = ''       
    if last_net != '' and last_sec != '': #only WPA and WPA2
        net_listd[last_net] = last_qual

    # sort by strength...strongest first
    net_list = [key for key, value in sorted(net_listd.iteritems(), key=lambda (k,v): (v,k), reverse=True)]
    return net_list

def create_visible_networks():
    """Find all broadcasting SSIDs and save in visible_networks file.  Return True if we got a good file"""

    network_good = False
    for do_resets in range(3):
        reset_network()
        net_list = []
        if network_exists('wlan0'):
            if network_up('wlan0'):
                network_good = True
                for get_nets in range(3):
                    try:
                        net_list = collect_networks()
                        if len(net_list) == 0:
                            time.sleep(3)
                            continue
                        else:
                            logger.warning('Found valid wifi connection.')
                        break
                    except:
                        time.sleep(3)
                        continue
        elif network_up('eth0'):
            network_good = True
            logger.warning('Found valid ethernet connection.')
            net_list = ['Irricloud valid eth0'] # see boiler_net_finish.py
        else:
            network_good = False
            self.logger.warning('found no valid network.  Retrying')
            time.sleep(3)
            continue

        if len(net_list) == 0:
            net_list.append('No Networks Found')
            network_good = False
        else:
            network_good = True
            break

    with open('data/visible_networks','w') as f:
        json.dump(net_list, f)

    return network_good

class StartConfig(object):
    def __init__(self, *args, **kwargs):
        self.logger = logging.getLogger('boiler_net_config')
        gv.cputemp = get_cpu_temp()

    def run(self, port=80, *middleware):
        # save /etc/network/interfaces and wpa_suplicant.conf
        shutil.copy('/etc/network/interfaces', '/etc/network/interfaces.save')
        shutil.copy('/etc/wpa_supplicant/wpa_supplicant.conf', '/etc/wpa_supplicant/wpa_supplicant.conf.save')
        self.logger.info('create visible_networks')
        create_visible_networks()
        self.logger.info('establish broadcast ip')
        establish_broadcast_ip()
#        self.logger.info('update boiler_net_finish')
#        subprocess.call(['update-rc.d', 'boiler_net_finish', 'defaults'])
#        self.logger.info('update boiler')
#        subprocess.call(['update-rc.d', 'boiler', 'remove'])
        self.logger.info('reboot.')
        subprocess.call(['reboot', '-h'])
        return

app = StartConfig()
logger = logging.getLogger('boiler_net_config')

if __name__ == "__main__":
    log_levels = { 'debug':logging.DEBUG,
        'info':logging.INFO,
        'warning':logging.WARNING,
        'error':logging.ERROR,
         'critical':logging.CRITICAL,
        }

    log_file = 'logs/boiler_net_config.out'
    fh = logging.FileHandler(log_file)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(logging.DEBUG) 

    if len(sys.argv) > 1:
        level_name = sys.argv[1]
        if level_name in log_levels:
            level = log_levels[level_name]
            logger.setLevel(level) 
        else:
            logger.critical('Bad parameter to boiler_net_config: ' + level_name)

    logger.critical('Starting')
    try:
        nows = time.strftime('%Y-%m-%d:%H:%M:%S', time.localtime())
        outdata = [nows + ' sip_net_start\n']
        with open('data/last_get','w') as f:
            f.writelines(outdata)
    except Exception as ex:
        logger.exception('could not write data/last_get: ' + str(ex))
    app.run()
