# !/usr/bin/python
# -*- coding: utf-8 -*-


##############################
#### Revision information ####
import subprocess
from threading import RLock
import logging

major_ver = 3
minor_ver = 2
old_count = 275
logger = logging.getLogger('boiler')

try:
    revision = int(subprocess.check_output(['git', 'rev-list', '--count', '--first-parent', 'HEAD']))
    ver_str = '%d.%d.%d' % (major_ver, minor_ver, (revision - old_count))
except Exception:
    print _('Could not use git to determine version!')
    revision = 999
    ver_str = '%d.%d.%d' % (major_ver, minor_ver, revision)

try:
    ver_date = subprocess.check_output(['git', 'log', '-1', '--format=%cd', '--date=short']).strip()
except Exception:
    print _('Could not use git to determine date of last commit!')
    ver_date = '2015-01-09'

#####################
#### Global vars ####

# Settings Dictionary. A set of vars kept in memory and persisted in a file.
# Edit this default dictionary definition to add or remove "key": "value" pairs or change defaults.
# note old passwords stored in the "pwd" option will be lost - reverts to default password.
from calendar import timegm
import json
import time

platform = ''  # must be done before the following import because gpio_pins will try to set it
substation = ""
substation_index = 0

try:
    import pigpio
    use_pigpio = True
except ImportError:
    use_pigpio = False
    
# use_pigpio = False #  for tasting  

from helpers import password_salt, password_hash

sd = {
    u"en": 1,
    u"mode": 'None',
    u"seq": 1,
    u"ir": [0],
    u"iw": [0],
    u"rsn": 0,
    u"htp": 80,
    u"external_htp": 0,
    u"enable_upnp": 0,
    u"upnp_refresh_rate": 15,
    u"remote_support_port": 0,
    u"nst": 8,
    u"rdst": 0,
    u"loc": u"",
#    u"tz": 48,
    u"tza": 'US/Pacific',
    u"tf": 1,
    u"rs": 0,
    u"rd": 0,
    u"mton": 0,
    u"lr": 100,
    u"sdt": 0,
    u"mas": 0,
    u"wl": 100,
    u"wl_et_weather": 100,
    u"bsy": 0,
    u"lg": u"",
    u"urs": 0,
    u"nopts": 13,
    u"pwd": u"b908168c9eeb928104f54a8ca1a4c6a9cd2bacd2",
    u"password": u"",
    u"ipas": 0,
    u"rst": 1,
    u"mm": 0,
    u"mo": [0],
    u"rbt": 0,
    u"mtoff": 0,
    u"nprogs": 1,
    u"nbrd": 1,
    u"tu": u"F",
    u"snlen": 32,
    u"name": u"Koblenz Boiler",
    u"theme": u"basic",
    u"show": [255],
    u"salt": password_salt(),
    u"lang": u"en_US",
    u"master":0,
    u"slave":0,
    u"master_ip":u"",
    u"master_port":80,
    u"gateway_ip":u"",
    u"radio_power":3,
    u"substation_network": u"Irricloud Network",
    u"tepassword":u"",
    u"tepr":0,
    u"tesu":1,
    u"teuser":u"",
    u"etok":1,
    u"etbase":10,
    u"etmin":0,
    u"etmax":200,
    u"ethistory":1,
    u"etforecast":1,
    u"etapi":u"",
}

for i in range(2):
    sd['teadr'+str(i)] = ''
    sd['tesmsnbr'+str(i)] = ''
    sd['tesmsprovider'+str(i)] = ''

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

# changes below must be reflected in sip_net_finish.py
substation_network_hash = password_hash(sd['substation_network'], 'notarandomstring')[:16]

nowt = time.localtime()
now = timegm(nowt)
tz_offset = int(time.time() - timegm(time.localtime())) # compatible with Javascript (negative tz shown as positive value)
plugin_menu = []  # Empty list of lists for plugin links (e.g. ['name', 'URL'])

rs_generic = {'rs_start_sec':0, 'rs_stop_sec':0, 'rs_duration_sec':0, 'rs_program_id':0, 'rs_schedule_type':'active', 'rs_last_seq_sec':0, 'rs_banstop_stop_sec':0, 'rs_bandelay_stop_sec':0}
rs_lock = RLock()
with rs_lock:
    rs = []  # run schedule
    for j in range(sd['nst']):
        rs.append([rs_generic.copy()])
    ps = []  # Program schedule (used for UI display)
    for i in range(sd['nst']):
        ps.append([0, 0])
    srvals = [0] * (sd['nst'])  # Shift Register values

rovals = [0] * sd['nst']  # Run Once durations
#snames = station_names()  # Load station names from file
#pd = load_programs()  # Load program data from file
plugin_data = {}  # Empty dictionary to hold plugin based global data

sbits = [0] * (sd['nbrd'] + 1)  # Used to display stations that are on in UI

lrun = [0, 0, 0, 0]  # station index, program number, duration, end time (Used in UI)
scount = 0  # Station count, used in set station to track on stations with master association.
use_gpio_pins = True

# Array indexing to interpret programs....Yuck
p_flags = 0
p_day_mask = 1
p_interval_day = 2
p_start_time = 3
p_stop_time = 4
p_spread_min = 5
p_duration_sec = 6
p_station_mask_idx = 7 # and bytes beyond this too

options = []
options += \
    [_("System name"), "string", "name", _("Unique name of this Boiler system."), _("System")], \
    [_("Time zone"), "list", "tza", _("Example: US/Pacific."), _("System")], \
    [_("24-hour clock"), "boolean", "tf", _("Display times in 24 hour format (as opposed to AM/PM style.)"), _("System")], \
    [_("Mode"), "list", "mode", _("Heating or cooling mode."), _("System")],

if sd['enable_upnp']:
    options += [_("Remote support"), "int", "remote_support_port", _("Enable remote ssh access for support.  Substations get remote support ports starting at this one and increasing."), _("System")],

options += \
    [_("Disable security"), "boolean", "ipas", _("Allow anonymous users to access the system without a password."), _("Change Password")], \
    [_("Current password"), "password", "opw", _("Re-enter the current password."), _("Change Password")], \
    [_("New password"), "password", "npw", _("Enter a new password."), _("Change Password")], \
    [_("Confirm password"), "password", "cpw", _("Confirm the new password."), _("Change Password")], \
    [_("Enable logging"), "boolean", "lg", _("Log all events."), _("Logging")], \
    [_("Max log entries"), "int", "lr", _("Length of log to keep, 0=no limits."), _("Logging")], \
    [_("gmail username"), "string", "teuser", _("Username from which to send texts and emails."), _("Text/Email")], \
    [_("gmail password"), "password", "tepassword", _("Password associated with above username."), _("Text/Email")],

