# !/usr/bin/env python
# -*- coding: utf-8 -*-

import subprocess
import os
import time
import sys
import getopt
import re

FNULL = open(os.devnull, 'w')


def usage():
    print './test_radio.py [--base|--remote|--router=<1..63>|--sleep=<name>]'
    sys.exit(1)

try:
    opts, args = getopt.getopt(sys.argv[1:],"brs:t:",["router=","base","remote","sleep="])
except getopt.GetoptError:
    usage()

rtype = ''
rrouting = 0
sleep_name = ''
for opt, arg in opts:
    if rtype != '':
        print 'only one of router,sleep,base,remote can be used'
        usage()
    if opt in ("-t","--router"):
        try:
            if int(arg) < 1 or int(arg) > 63:
                print 'router # must be between 1 and 63'
                usage()
            rtype = 'router'
            rrouting = int(arg)
        except:
            print 'router # must be between 1 and 63'
            usage()
    elif opt in ("-s","--sleep"):
        rtype = 'remote'
        sleep_name = arg
    elif opt in ("-r","--remote"):
        rtype = 'remote'
    elif opt in ("-b","--base"):
        rtype = 'base'

if rtype == '':
    rtype = 'base'

# get pid of substation_proxy.
# Tell sip_monitor to not restart it.
# Kill it, then setparameters, then allow sip_monitor to restart it
try:
    proxy_info = subprocess.check_output("ps auwx | grep -e substation_proxy", shell=True)
    l = proxy_info.split('\n')
    kill_list = ['kill', '-9']
    for e in l:
        if 'grep -e' not in e and e != '':
            word_list = re.sub('[^\w]', ' ', e).split()
            kill_list.append(word_list[1])

    subprocess.call(['touch', 'data/substation_proxy_pause'])
    if len(kill_list) > 2:
        subprocess.call(kill_list)
    cmd = ['python', 'substation_proxy.py', '--onetime', '--quick', '--power=2', '--type='+rtype]
    if rrouting != 0:
        cmd += ['--radio_routing='+str(rrouting)]
    if sleep_name != '':
        cmd += ['--radio_name='+sleep_name]

    passcount = 0
    for b in ['10000', '9600', '115200']:
        # remove any old test result
        try:
            subprocess.call(['rm', 'logs/qt.out'], stdout=FNULL, stderr=subprocess.STDOUT)
        except:
            pass
        bcmd = cmd + ['--baudrate='+b]
        print 'about to call ', bcmd
        subprocess.call(bcmd, stdout=FNULL, stderr=subprocess.STDOUT)
        try:
#            subprocess.call(['grep', 'MacAddress', 'logs/qt.out'], stdout=FNULL, stderr=subprocess.STDOUT)
            if 'MacAddress' in subprocess.check_output(['grep', 'MacAddress', 'logs/qt.out']):
                passcount += 1
                print b, 'pass'
            else:
                print b, 'fail'
        except:
            print b, 'fail'
            pass
    res = 'Pass' if passcount > 0 else 'Fail'
    print res
    subprocess.call(['rm', 'data/substation_proxy_pause'])
except Exception as ex:
    subprocess.call(['rm', 'data/substation_proxy_pause'])
    raise
