# !/usr/bin/env python
# -*- coding: utf-8 -*-

import subprocess
import os
import shutil
import time
import web
import json
import i18n
import ast
import gv
from helpers import network_up, network_exists, get_cpu_temp, jsave, get_ip, password_hash, update_upnp, validate_fqdn, update_hostname, reset_networking, light_ip, get_macid
import sys
import logging
import logging.handlers
import urllib
import urllib2
import re
from sip_net_start import collect_networks
import glob

urls = (
  '/', 'SubConfig',
  '/cn', 'NetConfig',
  '/su', 'SubConfig',
  '/cc', 'ConfigComplete',
)

error_msg = ''
been_through_subconfig = False

def stop_daemons():
    try:
        logger.info('stopping hostapd and dnsmasq')
        subprocess.call(['/etc/init.d/hostapd', 'stop'], stderr=subprocess.STDOUT)
        subprocess.call(['./dnsmasq', 'stop'], stderr=subprocess.STDOUT)

    except Exception as ex:
        logger.exception('stop_daemons: Unexpected exception trying to stop daemons: ' + str(ex))
        raise ex

def start_daemons():
    for attempts in range(2):
        try:
            logger.info('starting dnsmasq and hostapd')
            subprocess.call(['./dnsmasq', 'start'], stderr=subprocess.STDOUT)
            subprocess.call(['/etc/init.d/hostapd', 'start'], stderr=subprocess.STDOUT)
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

def regenerate_ssh_keys():
    try:
        files = glob.glob('/etc/ssh/ssh_host_*')
        if len(files) > 0:
            logger.info('regenerate_ssh_keys rm')
            rm_cmd = ['rm', '-f'] + files
            subprocess.call(rm_cmd)
        else:
            logger.info('regenerate_ssh_keys no files to rm')
        logger.info('regenerate_ssh_keys dpkg-reconfigure')
        subprocess.call(['dpkg-reconfigure', 'openssh-server'])
        logger.info('regenerate_ssh_keys complete')
    except:
        logger.exception('Could not regenerate ssh keys')

def using_eth0():
    # For now assume never valid eth0 and we are moving away from visible_networks and sip_net_start
#    try:
#        with open('data/visible_networks','r') as f:
#            net_list = json.load(f)
#        if 'Irricloud valid eth0' in net_list: # see sip_net_start.py
#            return True
#    except:
#        pass
    return False

class WebPage(object):
    def __init__(self):
        self.logger = logging.getLogger('sip_net_config')
        gv.cputemp = get_cpu_temp()

class SubConfig(WebPage):
    def GET(self):
        global error_msg, been_through_subconfig

        self.logger.debug('in Sub GET')
        save_last_get('Sub GET')
#        for key,value in web.ctx.env.iteritems():
#            self.logger.debug('sg key: ' + str(key) + ' value: ' + str(value))
        have_radio = False
        try:
            with open('/dev/dnt900','r') as f:
                pass # test for existence to skip radio config
            have_radio = True
        except:
            pass

        form_args = (web.form.Checkbox('Master Station', checked=gv.sd['master']==1), )
        form_args += (web.form.Checkbox('Substation', checked=gv.sd['slave']==1), )
        form_args += (web.form.Checkbox('Enable Remote Substations', checked=gv.sd['subnet_only_substations']==0), )
        if have_radio:
            form_args += (web.form.Checkbox('Radio Only Substation', checked=False), )
            form_args += (web.form.Textbox('Radio Only Substation Name'), )
        form_args += (web.form.Textbox('System Network Name', web.form.notnull, value=gv.sd['substation_network']), )
        form_args += (web.form.Textbox('Master IP', value=gv.sd['master_ip']), )

        if have_radio:
            form_args += (web.form.Textbox('Radio Power', web.form.notnull, web.form.Validator('Must be between 0..4 (inclusive)', lambda x:int(x)>=0 and int(x) <= 5), value=2), )
            form_args += (web.form.Textbox('Radio Network Name'), )
            form_args += (web.form.Textbox('Radio Router Number', web.form.Validator('Between 0 and 63.', lambda x:int(x)>=0 and int(x)<64), value="0"), )

        form = web.form.Form(*form_args)
        messages = []
        if error_msg != '':
            messages.append(error_msg)
            messages.append('')
            messages.append('')
        messages.append('Select if this is a master station or substation (or both).  Typical configurations have both boxes checked.')
        messages.append('')
        messages.append('Select "Enable Remote Substations" if you want a master station to have substations that are on a different subnet.  Both the master station and any substation that is on a different subnet should have this box checked.  Typical configurations leave this box unchecked.')
        messages.append('')
        messages.append('If a wifi enabled substation can directly reach the master station, then Master IP must be filled in and correspond to the network address that this substation will use to reach the master station.')
        messages.append('')
        messages.append('"System Network Name" should refer to a unique name for the entire system that will separate it from any neighboring systems.')
        messages.append('')
        if have_radio:
            messages.append('Non-wifi based radio substations should set "Master IP" as blank.')
            messages.append('Wifi based substations that use a radio to reach remote zones should still have "Master IP" pointing to the master station.')
            messages.append('')
            messages.append('Configuring a radio for remote sensor and/or valve control should check "Radio Only Substation".  Otherwise that field should not be checked.')
            messages.append('A radio for remote sensor and/or valve control should have a unique (between 1 and 12) character name in "Radio Only Substation Name".')
            messages.append('')
            messages.append('"Radio Power" should be a value between 0 and 4 (inclusive).  Larger values consume more power.')
            messages.append('')
            messages.append('"Radio Network Name" must be a unique name shared by all radios that need to communicate.  It is like a password (much like "System Network Name") allowing only those radios with the same "Radio Network Name" to interact.')
            messages.append('')
            messages.append('"Radio Router Number" should generally be 0.  If it is necessary for an intermediate radio to be in the chain between the base radio and a remote radio in the same "Radio Network Name" network, then this intermediate radio needs a unique non-zero "Radio Router Number".  These numbers must be unique within the communication domain, but there is no safety net if they are not.  Please be careful.')
            messages.append('')
            messages.append('Radio configuration can take over 1 minute so be patient while the next page loads.')
            messages.append('')
        return render.sip_net_config(messages, form)

    def POST(self):
        global error_msg, been_through_subconfig

        been_through_subconfig = False
        self.logger.debug('in Sub POST')
        save_last_get('Sub POST')
        error_msg = ''
        form = web.input()
        continuation = '/cn'
        try:
            gv.sd['master'] = 1 if 'Master Station' in form else 0
            gv.sd['slave'] = 1 if 'Substation' in form else 0
            gv.sd['subnet_only_substations'] = 0 if 'Enable Remote Substations' in form else 1
            if not gv.sd['master'] and not gv.sd['slave'] and 'Radio Only Substation' not in form:
                error_msg = 'At least one of "Master Substation" or "Substation" must be checked.'
                raise web.seeother('/su') # back to form

            for f in ['Master IP', 'Master Port', 'System Network Name', 'Radio Power', 'Radio Network Name', 'Radio Router Number', 'Radio Only Substation Name']:
                if f in form:
                    form[f] = form[f].strip()

            gv.sd['master_ip'] = form['Master IP']
            gv.sd['substation_network'] = form['System Network Name']
            if 'Radio Power' in form:
                try:
                    power = int(form['Radio Power'])
                except:
                    power = -1
                jsave(gv.sd, 'sd')
                actual_power = power & ~0x10  # disable the "hard power" bit
                if actual_power < 0 or actual_power > 5:
                    error_msg = 'Error:  "Radio Power" must be integer between 0 and 4 inclusive.'
                    raise web.seeother('/su') # back to form
#                if len(form['Radio Network Name']) < 1:
#                    error_msg = 'Error:  "Radio Network Name" must be non-empty.'
#                    raise web.seeother('/su') # back to form

                if len(form['Radio Network Name']) > 0:
                    radio_network_hash = password_hash(form['Radio Network Name'], 'notarandomstring')[:16]
                    cmd = ['python', 'substation_proxy.py', '--onetime', '--power='+str(power), '--key='+radio_network_hash]
                else:
                    cmd = ['python', 'substation_proxy.py', '--onetime', '--power='+str(power)]
                router_id = 255
                type = 'base' if gv.sd['master_ip'] else 'remote'
                self.logger.info('initial radio type: ' + type)
                if len(form['Radio Router Number']) > 0 and form['Radio Router Number'] != '0':
                    try:
                        router_id = int(form['Radio Router Number'])
                    except:
                        router_id = 254
                    if router_id < 1 or router_id > 63:
                        error_msg = 'Error:  "Radio Router Number" must be between 1 and 63 inclusive.'
                        raise web.seeother('/su') # back to form
                    cmd.append('--radio_routing='+str(router_id))
                    type = 'router'
                if 'Radio Only Substation' in form:
                    if len(form['Radio Only Substation Name']) > 12 or len(form['Radio Only Substation Name']) < 1:
                        error_msg = 'Error:  "Radio Only Substation Name" must be unique and between 1 and 12 characters.'
                        raise web.seeother('/su') # back to form
                    elif form['Radio Only Substation Name'] == 'localhost':
                        error_msg = 'Error:  "Radio Only Substation Name" must not be "localhost".'
                        raise web.seeother('/su') # back to form
                    cmd.append('--radio_name='+form['Radio Only Substation Name'])
                    type = 'remote' # radio only substations are never base radios

                cmd.append('--type='+type)
                # get pid of substation_proxy.
                # Tell sip_monitor to not restart it.
                # Kill it, then setparameters, then allow sip_monitor to restart it
                proxy_info = subprocess.check_output("/bin/ps auwx | /bin/grep -e substation_proxy", shell=True)
                l = proxy_info.split('\n')
                kill_list = ['kill', '-9']
                for e in l:
                    if 'grep -e' not in e and e != '':
                        word_list = re.sub('[^\w]', ' ', e).split()
                        kill_list.append(word_list[1])
                subprocess.call(['touch', 'data/substation_proxy_pause'])
                try:
                    if len(kill_list) > 2:
                        subprocess.call(kill_list)
                    self.logger.info('Executing cmd: ' + " ".join(cmd))
                    subprocess.call(cmd)
                    self.logger.info('removing substation_proxy_pause')
                    subprocess.call(['rm', 'data/substation_proxy_pause'])
                except Exception as ex:
                    self.logger.info('removing substation_proxy_pause ex: ' + str(ex))
                    subprocess.call(['rm', 'data/substation_proxy_pause'])
                    raise
                if 'Radio Only Substation' in form:
                    continuation = '/cc'
            else:
                jsave(gv.sd, 'sd')
        except Exception as ex:
            self.logger.info('finish ex: ' + str(ex))
            raise web.seeother('/su') # back to form

        if continuation != '/cc':
            regenerate_ssh_keys()
        self.logger.debug('Sub POST been_through True.  continuation: ' + continuation)
        been_through_subconfig = True
        raise web.seeother(continuation) # configure network parameters

class ConfigComplete(WebPage):
    def GET(self):
        form_args = ()
        form = web.form.Form(*form_args)

        messages = []
        if error_msg != '':
            messages.append(error_msg)
            messages.append('')
            messages.append('')
        messages.append('Configuration Complete.')
        messages.append('')
        messages.append('Press submit to configure another component.')
        messages.append('')
        return render.sip_net_config(messages, form)

    def POST(self):
        global error_msg, been_through_subconfig

        been_through_subconfig = False
        error_msg = ''
        raise web.seeother('/su') # do another configuration

class NetConfig(web.application):
    def __init__(self, *args, **kwargs):
        web.application.__init__(self, *args, **kwargs)
        self.logger = logging.getLogger('sip_net_config')
        gv.cputemp = get_cpu_temp()

    def run(self, port=80, *middleware):
        if not using_eth0():
            start_daemons()
        else:
            start_daemons() # do anyway for wireless config of ethernet port
        func = self.wsgifunc(*middleware)
        return web.httpserver.runsimple(func, ('0.0.0.0', port))

    def GET(self):
        if not been_through_subconfig:
            self.logger.info('tried Net GET....back to /su')
            raise web.seeother('/su')

        self.logger.debug('in Net GET')
        save_last_get('Net GET')

        messages = []
        if error_msg != '':
            messages.append(error_msg)
            messages.append('')
            messages.append('')

        messages.append('Select a unique "Station Name" with "Station Port" (typically 80).  The "Station Name" can contain letters and numbers and "-" characters.')
        messages.append('')
        form_args = ()
        form_args += (web.form.Textbox('Station Name', web.form.notnull, value=gv.sd['name']), )
        form_args += (web.form.Textbox('Station Port', web.form.Validator('Must not be 9080', lambda x:int(x)!=9080), value=gv.sd['htp']), )

        if gv.sd['master']:
            messages.append('If you would like to export the web interface to the outside world with automatic port forwarding via UPnP, then provide a non-zero "External Station Port" and check "Enable UPnP".')
            messages.append('The "UPnP Refresh Rate" is the number of minutes between updates of the UPnP mappings.  Setting this to 0 will update mappings only upon changes to IP addresses.')
            messages.append('')
            messages.append('If you would like to manually export the web interface to the outside world on a port other than 80, then provide a non-zero "External Station Port".')
            messages.append('')
            form_args += (web.form.Checkbox('Enable UPnP', checked=False), )
            form_args += (web.form.Textbox('UPnP Refresh Rate', value=gv.sd['upnp_refresh_rate']), )
            form_args += (web.form.Textbox('External Station Port', value="0"), )

        if not gv.sd['subnet_only_substations']:
            messages.append('"Remote Substation Access Port" will be used by substations on a different subnet than the master station to reach the master station.  The value used for the master station and the relevant substations must all be the same.  A value between 10000 and 20000 typically works well.')
            messages.append('')
            form_args += (web.form.Textbox('Remote Substation Access Port', value=gv.sd['external_proxy_port']), )

        if not using_eth0():
#            with open('data/visible_networks','r') as f:
#                net_list = json.load(f)
            net_list = collect_networks()
            self.logger.info('visible networks: ' + ",".join(net_list))

            form_args += (web.form.Dropdown('SSID', net_list), )
            form_args += (web.form.Textbox('Hidden SSID'), )
            form_args += (web.form.Password('Password'), )
            messages.append('Select network or enter hidden SSID and provide the associated password.  Only WPA and WPA2 (Personal) protocols are supported.')
            messages.append('')

        form_args += (web.form.Checkbox('Use DHCP', checked=True), )
        form_args += (web.form.Textbox('Static IP'), )
        form_args += (web.form.Textbox('Netmask'), )
        form_args += (web.form.Textbox('Gateway'), )
        form_args += (web.form.Textbox('DNS Nameservers'), )
#        form_args += (web.form.Checkbox('Do not validate network connection', checked=False), )

        messages.append('"Use DHCP" to automatically get IP address, or configure a static IP  with appropriate netmask and gateway addresses.')
        messages.append('The MAC address of this device is: ' + get_macid())
        messages.append('')
        messages.append('"DNS Nameservers" can be configured for complex network setups.  Typically this should be left blank.')
        messages.append('')
#        messages.append('If you are configuring for a network that is not currently accessible, check "Do not validate network connection".')
#        messages.append('')
        form = web.form.Form(*form_args)
        return render.sip_net_config(messages, form)

    def POST(self):
        global error_msg

        if not been_through_subconfig:
            self.logger.info('tried Net POST....back to /su')
            raise web.seeother('/su')

        self.logger.debug('in Net POST')
        save_last_get('Net POST')
        form = web.input()
        error_msg = ''
        outdata = []

        for f in ['Static IP', 'Netmask', 'Gateway', 'DNS Nameservers', 'SSID', 'Password', 'Hidden SSID', 'UPnP Refresh Rate',
                  'Station Name', 'Station Port', 'External Station Port', 'Remote Substation Access Port']:
            if f in form:
                form[f] = form[f].strip()

        # use hidden SSID if provided
        using_hidden_ssid = False
        if 'Hidden SSID' in form and form['Hidden SSID'] != '':
            using_hidden_ssid = True
            form['SSID'] = form['Hidden SSID']
            self.logger.info('using Hidden SSID')

        use_eth0 = 'SSID' not in form or form['SSID'] == 'ethernet'

        if not use_eth0:
            net = 'wlan0'
        else:
            form['SSID'] = 'using eth0'
            net = 'eth0'

        self.logger.debug('in Net POST: ' + form['SSID'])
        gv.sd['light_ip'] = 1
        try:
            if form['DNS Nameservers'][0:1] == 'X' and \
                   (len(form['DNS Nameservers']) == 1 or form['DNS Nameservers'][1:2] == ' '):
                gv.sd['light_ip'] = 0 # hack to disable blinking led on startup with leading X in nameservers
                form['DNS Nameservers'] = form['DNS Nameservers'][1:].strip()
        except:
            pass

        if 'Use DHCP' in form:
            for d in ['DNS Search', 'DNS Nameservers']:
                if d in form and form[d] != '':
                    error_msg = 'Error:  "' + d + '" must be blank with "Use DHCP".'
                    raise web.seeother('/cn') # back to form

        if 'Station Name' in form:
            sn = validate_fqdn(form['Station Name'])
            if sn == 'Irricloud': # error condition
                error_msg = 'Error:  "Station Name" must have only letter and numbers.'
                raise web.seeother('/cn') # back to form
            elif len(sn) > 50 or len(sn) < 1:
                error_msg = 'Error:  "Station Name" must be at most 50 characters.'
                raise web.seeother('/cn') # back to form
            form['Station Name'] = sn

        try:
            drop = 0
            with open('/etc/network/interfaces','r') as infile:
                iface = infile.readlines()
            for line in iface:
                if 'dns-nameservers' in line:
                    continue # delete any old dns stuff
                did_iface_wlan0 = False
                if drop > 0:
                    drop -= 1
                    continue
                if 'iface ' + net + ' inet' in line:
                    if net == 'wlan0':
                        did_iface_wlan0 = True
                    if 'Use DHCP' in form:
                        self.logger.info('Using dhcp: ' + form['SSID'])
                        if 'static' in line:
                            drop = 3
                            line = 'iface ' + net + ' inet manual\n'
                    else:
                        self.logger.info('Using static ip: ' + form['Static IP'] + ' ' + form['Netmask'] + ' ' + form['Gateway'])
                        if 'static' in line:
                            drop = 3
                        outdata.append('iface ' + net + ' inet static\n')
                        outdata.append('        address ' + form['Static IP'] + '\n')
                        outdata.append('        netmask ' + form['Netmask'] + '\n')
                        line = '        gateway ' + form['Gateway'] +'\n'
                        outdata.append(line)
                        if 'DNS Nameservers' in form and form['DNS Nameservers'] != '':
                            line = '        dns-nameservers ' + form['DNS Nameservers'] +'\n'
                        else: # use gateway
                            line = '        dns-nameservers ' + form['Gateway'] +'\n'
                elif 'iface ' + 'wlan0' + ' inet' in line: # using eth0 but need to clean up 10.0.0.1 entry
                    if 'static' in line:
                        drop = 3
                        line = 'iface ' + 'wlan0' + ' inet manual\n'
                        did_iface_wlan0 = True

                outdata.append(line)
                if did_iface_wlan0:
                    outdata.append('        wpa-conf /etc/wpa_supplicant/wpa_supplicant.conf\n')


        except Exception as ex:
            self.logger.exception('Unexpected exception trying to configure network: ' + str(ex))
            raise web.seeother('/cn')

        if len(outdata) == 0:
            self.logger.error('Expected data in /etc/network/interfaces')
            raise web.seeother('/cn')

        for portn in ['Station Port', 'External Station Port', 'Remote Substation Access Port', 'UPnP Refresh Rate']:
            if portn in form:
                try:
                    port = int(form[portn])
                except:
                    error_msg = 'Error:  "' + portn + '" must be integer.'
                    raise web.seeother('/cn') # back to form
                if port < 0 or port > 65535:
                    error_msg = 'Error:  "' + portn + '" must be between 0 and 65535.'
                    raise web.seeother('/cn') # back to form
                if portn == 'Station Port' and port == 9080: # specially reserved for proxy
                    error_msg = 'Error:  "' + portn + '" cannot be 9080.'
                    raise web.seeother('/cn') # back to form

        self.logger.info('stopping daemons before updating network interfaces')
        if not use_eth0:
            stop_daemons()
        else:
            stop_daemons()

        try:
            with open('/etc/network/interfaces','w') as outfile:
                outfile.writelines(outdata)

            # Do not let dhcpcd get a dhcp address (and dns info?) if we are using a static config
            if 'Use DHCP' in form:
                shutil.copy2('/etc/dhcpcd.conf.yeswlan0', '/etc/dhcpcd.conf')
            else:
                shutil.copy2('/etc/dhcpcd.conf.nowlan0', '/etc/dhcpcd.conf')

            if not use_eth0:
                wpa_supp_lines = ['ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n']
                wpa_supp_lines.append('update_config=1\n\n')
                if form['SSID'] != '' and form['Password'] != '':
                    # wpa_passphrase cannot handle blanks so quote strings
                    ssid = form['SSID'].replace('"', '\"')
                    passw = form['Password'].replace('"', '\"')
                    cmd = '/usr/bin/wpa_passphrase "' + ssid + '" "' + passw + '" > passphrase'
                    subprocess.call(cmd, shell=True)
                    with open('passphrase', 'r') as f:
                        pl = f.readlines()
                        for line in pl:
                            if '#psk=' in line: # drop cleartext password
                                continue
                            wpa_supp_lines.append(line)
                            if using_hidden_ssid and 'ssid' in line:
                                wpa_supp_lines.append('\tscan_ssid=1\n')
                    subprocess.call(['rm', 'passphrase'])
                else:
                    self.logger.warning('missing ssid or password')

                with open('/etc/wpa_supplicant/wpa_supplicant.conf', 'w') as outfile:
                    outfile.writelines(wpa_supp_lines)

                # check if network connection was made
#                self.logger.debug('wpa_action stopping wlan0')
#                rc = subprocess.call(['wpa_action', 'wlan0', 'stop'])
#                self.logger.debug( 'wpa_action wlan0 stop return: ' + str(rc))
#                time.sleep(1)
#                self.logger.debug('ifup wlan0')
#                rc = subprocess.call(['ifup', 'wlan0'])
#                self.logger.debug( 'ifup return: ' + str(rc))
#                time.sleep(1)
#                rc = subprocess.call(['wpa_action', 'wlan0', 'reload'])
#                self.logger.debug( 'wpa_action wlan0 reload return: ' + str(rc))
#                time.sleep(1)
#                reset_networking()

            if True or 'Do not validate network connection' in form or network_up(net, self.logger):
                #successful network connection.   Finalize
                # copy the current versions to the save version
                cur_ip = get_ip(net)
                self.logger.info('success...cur_ip: ' + cur_ip)
                if gv.sd['enable_upnp']: # was enabled?  Then cleanup.  If network still points to 10.0.0.1 network cant cleanup!
                    deletes = []
                    if gv.sd['external_htp'] != 0:
                        deletes.append(gv.sd['external_htp'])
                    if gv.sd['remote_support_port'] != 0:
                        deletes.append(gv.sd['remote_support_port'])
                    if gv.sd['external_proxy_port'] != 0:
                        deletes.append(gv.sd['external_proxy_port'])
                    update_upnp(cur_ip, self.logger, deletes)
                gv.sd['enable_upnp'] = 1 if 'Enable UPnP' in form else 0
                if gv.sd['enable_upnp']:
                    if 'UPnP Refresh Rate' in form:
                        gv.sd['upnp_refresh_rate'] = int(form['UPnP Refresh Rate'])
                if 'Station Name' in form:
                    gv.sd['name'] = form['Station Name']
                    update_hostname(gv.sd['name'])
                if 'Station Port' in form:
                    gv.sd['htp'] = int(form['Station Port'])
                if gv.sd['master']:
                    gv.sd['master_ip'] = 'localhost' # update local master_port 
                    gv.sd['master_port'] == gv.sd['htp']
                else:
                    gv.sd['master_port'] = 0
                gv.sd['external_htp'] = int(form['External Station Port']) if 'External Station Port' in form else 0
                gv.sd['external_proxy_port'] = int(form['Remote Substation Access Port']) if 'Remote Substation Access Port' in form else 0
                jsave(gv.sd, 'sd')
                self.logger.info('success....copying back interfaces and wpa')
                try:
                    os.remove('/etc/resolv.conf')
                except:
                    pass
                shutil.copy('/etc/network/interfaces', '/etc/network/interfaces.save')
                shutil.copy('/etc/wpa_supplicant/wpa_supplicant.conf', '/etc/wpa_supplicant/wpa_supplicant.conf.save')
#                self.logger.info('success....enabling sip')
#                subprocess.call(['systemctl', 'enable', 'sip.service'])
##                subprocess.call(['update-rc.d', 'sip', 'enable'])
##                subprocess.call(['update-rc.d', 'sip', 'defaults'])
##                self.logger.info('disabling sip_net_finish')
#                subprocess.call(['systemctl', 'disable', 'sip_net_finish.service'])
##                subprocess.call(['update-rc.d', '-f', 'sip_net_finish', 'remove'])
#                light_ip(cur_ip)
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

        self.logger.error('sip_net_config: should have exited above.  sip_monitor will restart')
        raise web.seeother('/cn')

app = NetConfig(urls, globals())
app.notfound = lambda: web.seeother('/su')

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
logger = logging.getLogger('sip_net_config')

if __name__ == "__main__":
    log_levels = { 'debug':logging.DEBUG,
        'info':logging.INFO,
        'warning':logging.WARNING,
        'error':logging.ERROR,
         'critical':logging.CRITICAL,
        }
    log_file = 'logs/irricloud_net_config.out'
    fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=gv.MB, backupCount=5)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(logging.INFO) 

    if len(sys.argv) > 1:
        level_name = sys.argv[1]
        if level_name in log_levels:
            level = log_levels[level_name]
            logger.setLevel(level) 
        else:
            logger.critical('Bad parameter to sip_net_config: ' + level_name)

    logger.critical('Starting sip_net_finish')
    app.run()
