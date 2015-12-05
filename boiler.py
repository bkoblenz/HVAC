#!/usr/bin/env python

# boiler.py
#

import time
from calendar import timegm
import pigpio
import json
import ast
import i18n
import thread
import logging
import gv
from helpers import *
import web

urls = [
#    '/',  'webpages.home',
    '/',  'webpages.view_options',
    '/vo', 'webpages.view_options',
    '/co', 'webpages.change_options',
    '/vl', 'webpages.view_log',
    '/cl', 'webpages.clear_log',
    '/wl', 'webpages.boiler_log',
    '/login',  'webpages.login',
    '/logout',  'webpages.logout',
]

pi = pigpio.pi()
zone_call = [20, pigpio.PUD_UP] # phys 38
factory_reset = [21, pigpio.PUD_UP] # phys 40  ### RESERVED BY boiler_monitor.py
in_pins = [zone_call]

dry1 = [5, 1] # phys 29
dry2 = [6, 1] # phys 31
dry3 = [13, 1] # phys 33, default is this controls heatpump
dry4 = [19, 1] # phys 35, default is this controls heatpump
circ_pump = [26, 1] # phys 37
boiler_call = [16, 1] # phys 36
out_pins = [circ_pump, boiler_call, dry1, dry2, dry3, dry4]

def timing_loop():
    last_circ_pump = circ_pump[1]
    t = 0
    while True:
        gv.now = timegm(time.localtime())
        t += 1
        if t >= 10:
            t -= 10
        for pin_e in in_pins:
            pin = pin_e[0]
            if pin != zone_call[0] and t != 1:
                continue
            v = pi.read(pin)
            if pin == zone_call[0] and v != last_circ_pump: # zone_call==0 --> last_circ_pump = 0
                last_circ_pump = v
                pi.write(circ_pump[0], last_circ_pump)
                operation = 'disabling' if last_circ_pump else 'enabling'
                if gv.sd['mode'] in ['Boiler Only', 'Boiler and Heatpump', 'Heatpump Only']:
                    if gv.sd['mode'] in ['Boiler Only', 'Boiler and Heatpump']:
                        pi.write(boiler_call[0], last_circ_pump)
                        msg = operation + ' ciculation pump and boiler'
                        logger.debug(msg)
                        log_event(msg)
                    if gv.sd['mode'] in ['Boiler and Heatpump', 'Heatpump Only']:
                        if False:
                            pi.write(dry2[0], last_circ_pump)
                            pi.write(dry4[0], last_circ_pump)
                elif gv.sd['mode'] == 'Heatpump Cooling':
                    pi.write(dry3[0], last_circ_pump)
                    pi.write(dry4[0], last_circ_pump)
        time.sleep(5)


class BoilerApp(web.application):
    """Allow program to select HTTP port."""

    def run(self, port=gv.sd['htp'], *middleware):  # get port number from options settings
        func = self.wsgifunc(*middleware)
        return web.httpserver.runsimple(func, ('0.0.0.0', port))


app = BoilerApp(urls, globals())
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
logger = logging.getLogger('boiler')

if __name__ == '__main__':

    log_levels = { 'debug':logging.DEBUG,
        'info':logging.INFO,
        'warning':logging.WARNING,
        'error':logging.ERROR,
        'critical':logging.CRITICAL,
        }

    log_file = 'logs/boiler.out'
    fh = logging.FileHandler(log_file)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(logging.DEBUG) 
    logger.info('Starting...')

    for pin_e in in_pins:
        pin = pin_e[0]
        pi.set_mode(pin, pigpio.INPUT)
        pi.set_pull_up_down(pin, pin_e[1])

    for pin_e in out_pins:
        pin = pin_e[0]
        pi.set_mode(pin, pigpio.OUTPUT)
        pi.write(pin, pin_e[1])

    gv.logger.debug('Starting main thread')
    thread.start_new_thread(timing_loop, ())

    app.notfound = lambda: web.seeother('/')
    app.run()

