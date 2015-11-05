# !/usr/bin/python
# -*- coding: utf-8 -*-


##############################
#### Revision information ####
import subprocess
from threading import RLock
import logging

logger = logging.getLogger('boiler')

# Settings Dictionary. A set of vars kept in memory and persisted in a file.
# Edit this default dictionary definition to add or remove "key": "value" pairs or change defaults.
# note old passwords stored in the "pwd" option will be lost - reverts to default password.
from calendar import timegm
import json
import time
import pigpio

from helpers import password_salt, password_hash

sd = {
    u"htp": 80,
    u"tza": 'US/Pacific',
    u"tf": 1,
    u"ipas": 0,
    u"tu": u"F",
    u"snlen": 32,
    u"name": u"Koblenz Boiler",
    u"salt": password_salt(),
    u"theme": u"basic",
    u"lang": u"en_US",
}

sd['password'] = password_hash('Irricloud', sd['salt'])

try:
    with open('./data/sd.json', 'r') as sdf:  # A config file
        sd_temp = json.load(sdf)
    added_key = False
    for key in sd:  # If file loaded, replce default values in sd with values from file
        if key in sd_temp:
            sd[key] = sd_temp[key]
        else:
            added_key = True
    if added_key: # force write
        raise IOError

except IOError:  # If file does not exist, it will be created using defaults.
    with open('./data/sd.json', 'w') as sdf:  # save file
        json.dump(sd, sdf)


nowt = time.localtime()
now = timegm(nowt)
tz_offset = int(time.time() - timegm(time.localtime())) # compatible with Javascript (negative tz shown as positive value)
