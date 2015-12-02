#!/usr/bin/env python

# boiler.py
#

import time
import pigpio
import logging

# masks
# heating with boiler and heatpump == 7
# heating with only boiler == 3
# heating with only heatpump == 5
# cooling with heatpump == 4
# nothing == 0
HEATING = 0x1
BOILER = 0x2
HEATPUMP = 0x4

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

    mode = HEATING+BOILER+HEATPUMP
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

    last_circ_pump = circ_pump[1]
    t = 0
    while True:
        t += 1
        if t >= 10:
            t -= 10
        for pin_e in in_pins:
            pin = pin_e[0]
            if pin != zone_call[0] and t != 1:
                continue
            v = pi.read(pin)
            print 'pin: ', pin, ' value: ', v, t
            if pin == zone_call[0] and v != last_circ_pump: # zone_call==0 --> last_circ_pump = 0
                last_circ_pump = v
                pi.write(circ_pump[0], last_circ_pump)
                if (mode & HEATING) == HEATING:
                    if (mode & BOILER) == BOILER:
                        pi.write(boiler_call[0], last_circ_pump)
                    if (mode & HEATPUMP) == HEATPUMP:
                        if False:
                            pi.write(dry2[0], last_circ_pump)
                            pi.write(dry4[0], last_circ_pump)
                elif (mode & HEATPUMP) == HEATPUMP:
                    pi.write(dry3[0], last_circ_pump)
                    pi.write(dry4[0], last_circ_pump)
                msg = 'disabling' if last_circ_pump else 'enabling'
                msg += ' ciculation pump and boiler'
                logger.debug(msg)
        time.sleep(5)

