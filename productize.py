# !/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import subprocess
import shutil
import glob
import sys

if __name__ == "__main__":

    if len(sys.argv) != 2:
        print 'productize.py <version#>'
        os.exit(1)

    try:
        os.remove('/home/pi/.ssh/known_hosts')
    except:
        pass

    for dir in ['logs', 'data', 'sessions']:
        shutil.rmtree(dir)
        os.makedirs(dir)
    
    os.makedirs('data/sensors')
    os.makedirs('data/et_weather_level_history')
    version = sys.argv[1]
    with open('data/version', 'w') as f:
        f.write(version+'\n')

    data_contents = {'snames.json':'[]',
                     'snotes.json':'[]',
                     'programs.json':'[]',
                     'sensors.json':'[]',
                     'sd.json':'{"enable_upnp": 0, "en": 1, "seq": 1, "wl_et_weather": 100, "mton": 0, "teadr4": "", "ir": [], "etapi": "", "mas": 0, "iw": [], "upnp_refresh_rate": 15, "external_htp": 0, "snlen": 32, "tza": "US/Pacific", "tesmsprovider4": "AT&T", "teadr1": "", "etok": 0, "htp": 80, "ethistory": 1, "nst": 0, "tesmsnbr2": "", "rdst": 0, "loc": "98826", "nprogs": 0, "rs": 0, "tu": "F", "master_ip": "localhost", "rd": 0, "theme": "basic", "lr": 1000, "subnet_only_substations": 1, "tf": 1, "master": 1, "radio_zones": [], "substation_network": "Irricloud-Network", "tepoweron": 1, "tesmsnbr4": "", "sdt": 0, "lang": "en_US", "rsn": 0, "slave": 1, "bsy": 0, "lg": 1, "teipchange": 1, "teprogramrun": 0, "wl": 100, "etforecast": 1, "tesmsnbr3": "", "pwd": "b908168c9eeb928104f54a8ca1a4c6a9cd2bacd2", "ipas": 0, "tepassword": "", "light_ip": 1, "master_port": 0, "rst": 0, "password": "83d3ba2e9e4f8ae1be7166a3a8abf6ce9ffd0c1c", "tesmsprovider3": "AT&T", "tesmsnbr1": "", "tesmsprovider1": "AT&T", "tesmsprovider0": "AT&T", "radio_present": false, "external_proxy_port": 0, "teadr0": "", "tesmsnbr0": "", "name": "Irricloud-Sprinkler", "teadr3": "", "mm": 0, "etmin": 0, "mo": [], "rbt": 0, "show": [], "teuser": "", "mtoff": 0, "tesu": 1, "etbase": 7.0, "radiost": 0, "urs": 0, "teadr2": "", "tesmsprovider2": "AT&T", "salt": ">SNfKuR8eDF/vF.7PAU45@:<o*1~VPvs|3E=Wr+2mX>u*x\u007fZEu-v>GxrQq%7*5;w", "etmax": 200, "remote_support_port": 0}',
                    }

    for fname,contents in data_contents.iteritems():
        with open('data/'+fname, 'w') as f:
            f.write(contents)

    subprocess.call(['touch', 'data/factory_reset'])
