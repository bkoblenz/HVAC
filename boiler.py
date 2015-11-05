#!/usr/bin/env python

# boiler.py
#

import time
import pigpio
import logging

pi = pigpio.pi()
zone_call = [20, pigpio.PUD_UP] # phys 38
factory_reset = [21, pigpio.PUD_UP] # phys 40
in_pins = [factory_reset, zone_call]

dry1 = [5, 0] # phys 29
dry2 = [6, 1] # phys 31
dry3 = [13, 1] # phys 33
dry4 = [19, 1] # phys 35
circ_pump = [26, 0] # phys 37
out_pins = [circ_pump, dry1, dry2, dry3, dry4]

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
        print 'initializing input pin: ', pin
        pi.set_mode(pin, pigpio.INPUT)
        pi.set_pull_up_down(pin, pin_e[1])

    for pin_e in out_pins:
        pin = pin_e[0]
        print 'initializing output pin: ', pin
        pi.set_mode(pin, pigpio.OUTPUT)
        pi.write(pin, pin_e[1])

    while True:
        for pin_e in in_pins:
            pin = pin_e[0]
            v = pi.read(pin)
            print 'pin: ', pin, ' value: ', v
#            if v == 0:
#                logger.debug('some zone operating')
#            else:
#                logger.debug('no zone operating')
        time.sleep(5)

