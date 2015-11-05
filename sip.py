# !/usr/bin/env python
# -*- coding: utf-8 -*-

import i18n

import json
import ast
import time
import thread
from calendar import timegm
import sys
sys.path.append('./plugins')

import web  # the Web.py module. See webpy.org (Enables the Python SIP web interface)

import gv
import logging
from helpers import plugin_adjustment, prog_match, schedule_stations, log_run, stop_onrain, stop_station
from helpers import check_rain, jsave, station_names, get_rpi_revision, mkdir_p, to_relative_time
from urls import urls  # Provides access to URLs for UI pages
from gpio_pins import set_output
# do not call set output until plugins are loaded because it should NOT be called
# if gv.use_gpio_pins is False (which is set in relay board plugin.
# set_output()

def timing_loop():
    """ ***** Main timing algorithm. Runs in a separate thread.***** """
    last_min = 0
    last_day = 0
    last_master_station_running = 0
    master_turn_on = 0
    mton_delay = 0
    while True:  # infinite loop
        gv.nowt = time.localtime()   # Current time as time struct.  Updated once per second.
        gv.now = timegm(gv.nowt)   # Current time as timestamp based on local time from the Pi. Updated once per second.
        if gv.sd['en'] and not gv.sd['mm']:
            if gv.now / 60 != last_min:  # only check programs once a minute
                last_min = gv.now / 60
                cur_day = gv.now//86400
                if cur_day != last_day:
                    for i in range(gv.sd['nst']):
                        for j in range(len(gv.rs[i])-1,0,-1):
                            if gv.rs[i][j]['rs_stop_sec'] < gv.now:
                                gv.logger.critical('Left over data on gv.rs['+str(i)+']')
                                del gv.rs[i][j]
                    last_day = cur_day
                extra_adjustment = plugin_adjustment()
                for i, p in enumerate(gv.pd):  # get both index and prog item
                    # check if program time matches current time, is active, and has a duration
                    if prog_match(p) and p[gv.p_duration_sec]:
                        rs = [[0,0] for idx in range(gv.sd['nst'])] # program, duration indexed by station
                        # check each station for boards listed in program up to number of boards in Options
                        for b in range(gv.sd['nbrd']):
                            for s in range(8):
                                sid = b * 8 + s  # station index
                                if sid == gv.sd['mas']-1:
                                    continue  # skip if this is master station

				# station duration condionally scaled by "water level"
				duration_adj = 1.0 if (p[gv.p_flags]&2) == 2 or gv.sd['iw'][b] & (1<<s) else gv.sd['wl'] / 100 * extra_adjustment
                                duration = int(p[gv.p_duration_sec] * duration_adj)

                                if p[gv.p_station_mask_idx + b] & 1 << s:  # if this station is scheduled in this program
                                    rs[sid][0] = i + 1  # store program number
                                    rs[sid][1] = duration
                        schedule_stations(rs, p[gv.p_flags])

        if gv.sd['bsy']:
            with gv.rs_lock:
                program_running = False
                masid = gv.sd['mas']-1
                for sid in range(gv.sd['nst']):  # Check each station once a second
                    b = sid >> 3
                    s = sid % 8
                    prog_id = gv.rs[sid][len(gv.rs[sid])-1]['rs_program_id']
                    p = None if prog_id > len(gv.pd) else gv.pd[prog_id-1]
                    if gv.now >= gv.rs[sid][len(gv.rs[sid])-1]['rs_stop_sec'] and len(gv.rs[sid]) > 1:  # check if time is up
                        stop_station(sid, **{'stop_only_current':1})
                    elif gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec'] <= gv.now < gv.rs[sid][len(gv.rs[sid])-1]['rs_stop_sec']:
                        if (gv.sbits[b] & (1<<s)) == 0: # not yet displayed?
                            duration = gv.rs[sid][len(gv.rs[sid])-1]['rs_stop_sec'] - gv.now
                            gv.sbits[b] |= 1 << s  # Set display to on
                            gv.ps[sid][0] = prog_id
                            gv.ps[sid][1] = duration
                            if p is None or (p[gv.p_flags]&2) == 0:  # if not ban program
                                gv.logger.debug('turn on sid: ' + str(sid+1) + ' prog: ' + str(prog_id) + ' dur: ' + to_relative_time(duration))
                                gv.srvals[sid] = 1
                                set_output()
                            else: # already stopped what was running in schedule_stations
                                gv.logger.debug('turn on ban sid: ' + str(sid+1) + ' prog: ' + str(prog_id) + ' dur: ' + to_relative_time(duration))
                            if sid == gv.sd['mas']-1: # when we turn on master, start mton countdown
                                mton_delay = -gv.sd['mton']
                    elif gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec'] > gv.now and gv.ps[sid][0] == 0:
                        duration = gv.rs[sid][len(gv.rs[sid])-1]['rs_stop_sec'] - gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec']
                        gv.ps[sid][0] = prog_id
                        gv.ps[sid][1] = duration 
                        gv.logger.debug('future: ' + to_relative_time(gv.rs[sid][len(gv.rs[sid])-1]['rs_start_sec']) +
                                        ' turn on sid: ' + str(sid+1) + ' prog: ' + str(prog_id) + ' dur: ' + to_relative_time(duration))

                    if masid >= 0 and sid != masid and gv.srvals[sid] and gv.sd['mo'][b]&(1<<s):  # Master settings
                        last_master_station_running = gv.now
                    if len(gv.rs[sid]) > 1:  # if any station is scheduled or on
                        program_running = True

                # delays may have screwed up master scheduling.
                # If a station requiring master is running and master is not started, start it.
                if masid >= 0 and gv.srvals[masid] == 0:
                    if master_turn_on == 0 and last_master_station_running == gv.now:
                        master_turn_on = last_master_station_running + gv.sd['mton']
                    if master_turn_on != 0 and master_turn_on <= gv.now:
                        gv.logger.debug('turn on master without prescheduling mton: ' + str(master_turn_on))
                        gv.sbits[masid>>3] |= 1 << (masid%8)  # Set display to on
                        gv.ps[masid][0] = 98
                        gv.ps[masid][1] = 0
                        gv.srvals[masid] = 1
                        set_output()
                        master_turn_on = 0

                # If no station requiring master is running and master is not stopped, stop it.
                if masid >= 0 and gv.srvals[masid] == 1:
                    if mton_delay >= 0:
                        mton_delay -= 1
                    if (mton_delay < 0 and # allow for windown where master starts early
                        last_master_station_running < gv.now and
                        ((gv.sd['mtoff'] <= 0 and last_master_station_running >= gv.now + gv.sd['mtoff']) or \
                         (gv.sd['mtoff'] > 0 and last_master_station_running + gv.sd['mtoff'] <= gv.now))):
                        gv.logger.debug('turn off master without prescheduling')
                        stop_station(masid, **{'stop_active':1})
                        mton_delay = 0
                    else:
                        program_running = True # give time for master to shut down

                if program_running:
                    # todo check stop_onrain for ban programs
                    if gv.sd['urs'] and gv.sd['rs']:  # Stop stations if use rain sensor and rain detected.
                        stop_onrain()  # Clear schedule for stations that do not ignore rain.
                    for sid in range(len(gv.rs)):  # loop through program schedule (gv.ps)
                        if (gv.sbits[sid>>3] & (1<<(sid%8))) != 0:  # If station is on, decrement time remaining display
                            gv.ps[sid][1] -= 1

                if not program_running:
                    gv.sd['bsy'] = 0
#                    gv.srvals = [0] * (gv.sd['nst'])
#                    set_output()
#                    gv.sbits = [0] * (gv.sd['nbrd'] + 1)
#                    for i in range(gv.sd['nst']):
#                        gv.ps[i] = [0, 0]
#                    for i in range(gv.sd['nst']):
#                        if len(gv.rs[i]) > 1:
#                            gv.debug.critical('left over rs data; sid: ' + str(i) + ' len(rs) ' +str(len(gv.rs[sid])))
#                            for j in range(len(gv.rs[i])-1, 0, -1):
#                                del gv.rs[i][j]
                    for sid in range(gv.sd['nst']):
                        b = sid >> 3
                        s = sid % 8
                        if gv.srvals[sid]:
                            gv.logger.critical('srval set with no program running sid: ' + str(sid+1))
                        if gv.sbits[b]&(1<<s):
                            gv.logger.critical('sbits set with no program running sid: ' + str(sid+1))
                        if gv.ps[sid][0]:
                            gv.logger.critical('ps[0] set with no program running sid: ' + str(sid+1))
                        if gv.ps[sid][1]:
                            gv.logger.critical('ps[1] set with no program running sid: ' + str(sid+1))

        if gv.sd['urs']:
            check_rain()  # in helpers.py

        if gv.sd['rd'] and gv.now >= gv.sd['rdst']:  # Check of rain delay time is up
            gv.sd['rd'] = 0
            gv.sd['rdst'] = 0  # Rain delay stop time
            jsave(gv.sd, 'sd')

        new_now = timegm(time.localtime())
        if new_now - gv.now == 0: # try to avoid drift
            time.sleep(1)
        #### End of timing loop ####


class SIPApp(web.application):
    """Allow program to select HTTP port."""

    def run(self, port=gv.sd['htp'], *middleware):  # get port number from options settings
        func = self.wsgifunc(*middleware)
        return web.httpserver.runsimple(func, ('0.0.0.0', port))


app = SIPApp(urls, globals())
#  disableShiftRegisterOutput()
web.config.debug = False  # Improves page load speed
if web.config.get('_session') is None:
    web.config._session = web.session.Session(app, web.session.DiskStore('sessions'),
                                              initializer={'user': 'anonymous'})
template_globals = {
    'gv': gv,
    'str': str,
    'eval': eval,
    'session': web.config._session,
    'json': json,
    'ast': ast,
    '_': _,
    'i18n': i18n,
    'app_path': lambda p: web.ctx.homepath + p,
    'web' : web,
}

template_render = web.template.render('templates', globals=template_globals, base='base')

if __name__ == '__main__':

    mkdir_p('logs')

    log_levels = { 'debug':logging.DEBUG,
        'info':logging.INFO,
        'warning':logging.WARNING,
        'error':logging.ERROR,
         'critical':logging.CRITICAL,
        }
    log_file = 'logs/sip.out'
    fh = logging.FileHandler(log_file)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    gv.logger.addHandler(fh)
    gv.logger.setLevel(logging.DEBUG) 

    if len(sys.argv) > 1:
        level_name = sys.argv[1]
        if level_name in log_levels:
            level = log_levels[level_name]
            gv.logger.setLevel(level) 
        else:
            gv.logger.critical('Bad parameter to sip: ' + level_name)

    gv.logger.critical('Starting')

    #########################################################
    #### Code to import all webpages and plugin webpages ####

    import plugins

    try:
        gv.logger.info(_('plugins loaded:'))
    except Exception:
        pass

    for name in plugins.__all__:
        gv.logger.info(name)

    gv.plugin_menu.sort(key=lambda entry: entry[0])

    # Ensure first three characters ('/' plus two characters of base name of each
    # plugin is unique.  This allows the gv.plugin_data dictionary to be indexed
    # by the two characters in the base name.
    plugin_map = {}
    for p in gv.plugin_menu:
        three_char = p[1][0:3]
        if three_char not in plugin_map:
            plugin_map[three_char] = p[0] + '; ' + p[1]
        else:
            gv.logger.error('ERROR - Plugin Conflict:' + p[0] + '; ' + p[1] + ' and ' + plugin_map[three_char])
            exit()

    #  Keep plugin manager at top of menu
    try:
        gv.plugin_menu.pop(gv.plugin_menu.index(['Manage Plugins', '/plugins']))
    except Exception:
        pass
    
    gv.logger.debug('Starting main thread')
    thread.start_new_thread(timing_loop, ())

    if gv.use_gpio_pins:
        set_output()    

    app.notfound = lambda: web.seeother('/')

    app.run()
