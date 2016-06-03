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
from helpers import network_up, network_exists, get_cpu_temp, jsave, get_ip, update_upnp
import sys
import logging
import urllib
import urllib2

urls = (
  '/', 'NetConfig',
)

def stop_daemons():
    try:
        logger.debug('stopping hostapd and dnsmasq')
        subprocess.call("/etc/init.d/hostapd stop", shell=True, stderr=subprocess.STDOUT)
        subprocess.call("/etc/init.d/dnsmasq stop", shell=True, stderr=subprocess.STDOUT)

    except Exception as ex:
        logger.exception('stop_daemons: Unexpected exception trying to stop daemons: ' + str(ex))
        raise ex

def start_daemons():
    for attempts in range(3):
        try:
            logger.debug('starting hostapd and dnsmasq')
            subprocess.call("/etc/init.d/dnsmasq start", shell=True, stderr=subprocess.STDOUT)
            subprocess.call("/etc/init.d/hostapd start", shell=True, stderr=subprocess.STDOUT)
            return

        except Exception as ex:
            logger.exception('start_daemons: Unexpected exception trying to stop daemons: ' + str(ex))
            saved_ex = ex
    raise saved_ex

def save_last_get(message=''):
    try:
        nows = time.strftime('%Y-%m-%d:%H:%M:%S', time.localtime())
        outdata = [nows + ' ' + message + '\n']
        with open('data/last_get','w') as f:
           f.writelines(outdata)
    except Exception as ex:
        logger.exception('save_last_get: could not write data/last_get: ' + str(ex))

def using_eth0():
    try:
        with open('data/visible_networks','r') as f:
            net_list = json.load(f)
        if len(net_list) == 1 and net_list[0] == 'Irricloud valid eth0': # see boiler_net_start.py
            return True
    except:
        pass
    return False

class WebPage(object):
    def __init__(self):
        self.logger = logging.getLogger('boiler_net_config')
        gv.cputemp = get_cpu_temp()

class NetConfig(web.application):
    def __init__(self, *args, **kwargs):
        web.application.__init__(self, *args, **kwargs)
        self.logger = logging.getLogger('boiler_net_config')
        gv.cputemp = get_cpu_temp()

    def run(self, port=80, *middleware):
        if not using_eth0():
            start_daemons()
        func = self.wsgifunc(*middleware)
        return web.httpserver.runsimple(func, ('0.0.0.0', port))

    def GET(self):
        self.logger.debug('in Net GET')
        save_last_get('Net GET')

        form_args = ()
        form_args += (web.form.Textbox('System Name', web.form.notnull, value=gv.sd['name']), )
        form_args += (web.form.Textbox('System Port', web.form.notnull, value=gv.sd['htp']), )
        form_args += (web.form.Textbox('External System Port', value="0"), )
        form_args += (web.form.Checkbox('Enable UPnP', checked=False), )
        form_args += (web.form.Textbox('UPnP Refresh Rate', value=gv.sd['upnp_refresh_rate']), )

        if not using_eth0():
            with open('data/visible_networks','r') as f:
                net_list = json.load(f)
            self.logger.debug('visible networks: ' + ",".join(net_list))
            form_args += (web.form.Dropdown('SSID', net_list), )
            form_args += (web.form.Textbox('Hidden SSID'), )
            form_args += (web.form.Password('Password'), )

        form_args += (web.form.Checkbox('Use DHCP', checked=True), )
        form_args += (web.form.Textbox('Static IP'), )
        form_args += (web.form.Textbox('Netmask'), )
        form_args += (web.form.Textbox('Gateway'), )

        form = web.form.Form(*form_args)
        msg1 = 'Select network or enter hidden SSID and provide password.'
        msg1 += '    Only WPA and WPA2 (Personal) protocols supported.'
        msg2 = 'Use DHCP to automatically get IP address, or configure a static IP address.'
        return render.sip_net_config(msg1, msg2, form)

    def POST(self):
        self.logger.debug('in Net POST')
        save_last_get('Net POST')
        form = web.input()
        outdata = []

        net = 'eth0' if 'Use Hardwired eth0' in form else 'wlan0'
        # use hidden SSID if provided
        using_hidden_ssid = False
        if 'Hidden SSID' in form and form['Hidden SSID'].strip() != '':
            using_hidden_ssid = True
            form['SSID'] = form['Hidden SSID']
            self.logger.info('using Hidden SSID')

        use_eth0 = 'SSID' not in form

        if not use_eth0:
            form['SSID'] = form['SSID'].strip()
            form['Password'] = form['Password'].strip()
        else:
            form['SSID'] = 'using eth0'
        self.logger.debug('in Net POST: ' + form['SSID'])

        try:
            drop = 0
            with open('/etc/network/interfaces','r') as infile:
                iface = infile.readlines()
            for line in iface:
                did_iface_wlan0 = False
                if drop > 0:
                    drop -= 1
                    continue
                if 'iface ' + net + ' inet' in line:
                    if net == 'wlan0':
                        did_iface_wlan0 = True
                    if 'Use DHCP' in form:
                        self.logger.debug('Using dhcp: ' + form['SSID'])
                        if 'static' in line:
                            drop = 3
                            line = 'iface ' + net + ' inet manual\n'
                    else:
                        self.logger.debug('Using static ip: ' + form['Static IP'] + ' ' + form['Netmask'] + ' ' + form['Gateway'])
                        if 'static' in line:
                            drop = 3
                        outdata.append('iface ' + net + ' inet static\n')
                        outdata.append('        address ' + form['Static IP'] + '\n')
                        outdata.append('        netmask ' + form['Netmask'] + '\n')
                        line = '        gateway ' + form['Gateway'] +'\n'
                outdata.append(line)
                if did_iface_wlan0:
                    outdata.append('        wpa-conf /etc/wpa_supplicant/wpa_supplicant.conf\n')


        except Exception as ex:
            self.logger.exception('Unexpected exception trying to configure network: ' + str(ex))
            raise web.seeother('/cn')

        if len(outdata) == 0:
            self.logger.error('Expected data in /etc/network/interfaces')
            raise web.seeother('/cn')

        self.logger.debug('stopping daemons before updating network interfaces')
#        if not use_eth0:
#            stop_daemons()

        try:
            with open('/etc/network/interfaces','w') as outfile:
                outfile.writelines(outdata)

            if not use_eth0:
                wpa_supp_lines = ['ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n']
                wpa_supp_lines.append('update_config=1\n\n')
                if form['SSID'] != '' and form['Password'] != '':
                    # wpa_passphrase cannot handle blanks
                    cmd = 'wpa_passphrase ' + form['SSID'] + ' ' + form['Password'] + ' > passphrase'
                    subprocess.call(cmd, shell=True)
                    with open('passphrase', 'r') as f:
                        pl = f.readlines()
                        for line in pl:
                            wpa_supp_lines.append(line)
                            if using_hidden_ssid and 'ssid' in line:
                                wpa_supp_lines.append('\tscan_ssid=1\n')
                    subprocess.call(['rm', 'passphrase'])
                else:
                    self.logger.warning('missing ssid or password')

                with open('/etc/wpa_supplicant/wpa_supplicant.conf', 'w') as outfile:
                    outfile.writelines(wpa_supp_lines)

                # check if network connection was made
                self.logger.debug('wpa_action stopping wlan0')
                rc = subprocess.call(['wpa_action', 'wlan0', 'stop'])
                self.logger.debug( 'wpa_action return: ' + str(rc))
                time.sleep(1)
                self.logger.debug('ifup wlan0')
                rc = subprocess.call(['ifup', net])
                self.logger.debug( 'ifup return: ' + str(rc))
                time.sleep(2)

            if network_up(net):
                #successful network connection.   Finalize
                # copy the current versions to the save version
                if gv.sd['enable_upnp']: # was enabled?  Then cleanup
                    cur_ip = get_ip(net)
                    deletes = []
                    if gv.sd['external_htp'] != 0:
                        deletes.append(gv.sd['external_htp'])
                    if gv.sd['remote_support_port'] != 0:
                        deletes.append(gv.sd['remote_support_port'])
                    update_upnp(cur_ip, deletes)
                gv.sd['enable_upnp'] = 1 if 'Enable UPnP' in form else 0
                if gv.sd['enable_upnp']:
                    if 'UPnP Refresh Rate' in form:
                        gv.sd['upnp_refresh_rate'] = int(form['UPnP Refresh Rate'])

                if 'System Name' in form:
                    gv.sd['name'] = form['System Name']
                    jsave(gv.sd, 'sd')
                if 'System Port' in form:
                    gv.sd['htp'] = int(form['System Port'])
                    jsave(gv.sd, 'sd')
                if 'External System Port' in form:
                    gv.sd['external_htp'] = int(form['External System Port'])
                self.logger.info('success....copying back interfaces and wpa')
                shutil.copy('/etc/network/interfaces', '/etc/network/interfaces.save')
                shutil.copy('/etc/wpa_supplicant/wpa_supplicant.conf', '/etc/wpa_supplicant/wpa_supplicant.conf.save')
#                self.logger.info('success....enabling boiler')
#                subprocess.call(['update-rc.d', 'boiler', 'defaults'])
#                self.logger.info('disabling boiler_net_finish')
#                subprocess.call(['update-rc.d', 'boiler_net_finish', 'remove'])
                self.logger.info('rebooting')
                subprocess.call(['reboot', '-h'])
                exit(0)
            else:
                raise Exception('Network Inaccessible')

        except Exception as ex:
            self.logger.exception('failed: ' + str(ex))
            # restore saved /etc/network/interfaces and wpa_suplicant.conf
            self.logger.info('restore network files and exit.  Exception: ' + str(ex))
            shutil.move('/etc/network/interfaces.save', '/etc/network/interfaces')
            shutil.move('/etc/wpa_supplicant/wpa_supplicant.conf.save', '/etc/wpa_supplicant/wpa_supplicant.conf')
            exit(1)

        self.logger.error('boiler_net_config: should have exited above.  boiler_monitor will restart')
        raise web.seeother('/')

app = NetConfig(urls, globals())
app.notfound = lambda: web.seeother('/')

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

render = web.template.render('templates/', globals=template_globals, base='base')
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

    logger.critical('Starting boiler_net_finish')
    app.run()
