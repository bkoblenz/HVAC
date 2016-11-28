# !/usr/bin/env python
# -*- coding: utf-8 -*-

import web
import json
import ast
import i18n
import gv
from helpers import get_ip, get_cpu_temp, usb_reset, update_radio_present, validate_fqdn
from webpages import WebPage, encrypt_name, decrypt_name, extra_timeout, get_ip_for_base, get_ip_to_base
import sys
import getopt
import logging
import logging.handlers
import urllib
import urllib2
import socket

import threading
import struct
import binascii
import serial
import math
import time
from calendar import timegm
import os
from fcntl import ioctl
import subprocess
from i2c import ZONE_STATE, SCRATCH

class WebPage(object):
    def __init__(self):
        self.logger = logging.getLogger('substation_proxy')
        gv.cputemp = get_cpu_temp()

class ProxyIn(WebPage):
    """Provide a gateway for the master to reach slaves on a local network behind this gateway."""

    def GET(self):
        qdict = web.input()
        try:
            addr = qdict['proxyaddress']
            hops = 1 + addr.count(';')
#            self.logger.debug('proxy_in: hops: ' + str(hops) + ' proxy: ' + addr)
            if hops > 1:
                semi_idx = addr.find(';')
                qdict['proxyaddress'] = addr[semi_idx+1:]
                addr = addr[:semi_idx]
                cmd = 'supri'
            else:
                del qdict['proxyaddress']
                cmd = qdict['proxycommand']
                del qdict['proxycommand']
            col_idx = addr.find(":")
            if col_idx == -1:
                port = 0
                base_addr = addr
            else:
                port = int(addr[col_idx+1:])
                base_addr = addr[:col_idx]
        except:
            raise web.unauthorized()

        urlcmd = 'http://' + addr
        urlcmd += '/' + cmd
        first_param = True
        for key,value in qdict.iteritems():
            urlcmd += '?' if first_param else '&'
            urlcmd += key + '='
            urlcmd += urllib.quote_plus(value)
            first_param = False

        ret_str = json.dumps({'unreachable':1})
        timeout_adder = extra_timeout(urlcmd)
        try:
#            self.logger.debug('proxy_in: attempt urlcmd: ' + urlcmd)
            datas = urllib2.urlopen(urlcmd, timeout=gv.url_timeout+timeout_adder)
#            self.logger.debug('proxy_in: urlcmd sucess')
            data = json.load(datas)
            ret_str = json.dumps(data)
        except Exception as ex:
            self.logger.exception('proxy_in: No response from slave: ' + addr + ' urlcmd: ' + urlcmd + ' ex: ' + str(ex))

        web.header('Content-Type', 'application/json')
        return ret_str

class ProxyOut(WebPage):
    """Provide a gateway for slaves to reach the master."""

    def GET(self):
        qdict = web.input()
        try:
            cmd = qdict['command']
            params = qdict['parameters']
            paramo = json.loads(params)
            slave_ip = paramo['ip']
            slave_port = int(paramo['port'])
            slave_addr = slave_ip if slave_port == 0 or slave_port == 80 else slave_ip+':'+str(slave_port)
            slave_name = paramo['name']
            slave_proxy = paramo['proxy']
        except:
            raise web.unauthorized()

        ret_str = json.dumps({'unreachable':1})
        paramo['port'] = 9080
        paramo['ip'] = get_ip_for_base()
        paramo['proxy'] = slave_addr if slave_proxy == '' else slave_addr + ';' + slave_proxy
#        self.logger.debug('proxy_out: ip: ' + paramo['ip'] + ' port: ' + str(paramo['port']) + ' proxy: ' + paramo['proxy'])
        try:
            if gv.sd['master_ip'] != '':
                urlcmd = 'http://' + get_ip_to_base()
                if gv.sd['master_port'] != 0 and gv.sd['master_port'] != 80:
                    urlcmd += ':' + str(gv.sd['master_port'])
                urlcmd += '/' + cmd + '?data=' + urllib.quote_plus(json.dumps(paramo))
            else:
                self.logger.critical('No master for proxy')
#            self.logger.debug('proxy_out: attempt urlcmd: ' + urlcmd)
            timeout_adder = extra_timeout(urlcmd)
            datas = urllib2.urlopen(urlcmd, timeout=gv.url_timeout+timeout_adder)
#            self.logger.debug('proxy_out: urlcmd success')
            data = json.load(datas)
            ret_str = json.dumps(data)
        except Exception as ex:
            self.logger.info('proxy_out: No response from master for slave: ' + slave_name + ' urlcmd: ' + urlcmd + ' ex: ' + str(ex))

        web.header('Content-Type', 'application/json')
        return ret_str

class Proxy(web.application):
    def __init__(self, *args, **kwargs):
        web.application.__init__(self, *args, **kwargs)
        self.logger = logging.getLogger('substation_proxy')
        gv.cputemp = get_cpu_temp()

    def run(self, port=9080, *middleware):
        func = self.wsgifunc(*middleware)
        return web.httpserver.runsimple(func, ('0.0.0.0', port))

    def GET(self):
        self.logger.debug('in proxy GET')
        return

######### Radio initialization ############
radio_spi_lock = threading.RLock()
radio_spi_cmds = {}
SPI_MAGIC = 245 # must be bigger than any chunksize that RxData may see
dmsg_len = 100
regs = {
    '0000': {'name':'DeviceMode','reg':0x0,'bank':0x0,'span':1,'val':'0'},
    '0001': {'name':'RF_DataRate','reg':0x1,'bank':0x0,'span':1,'val':'0'},
    '0002': {'name':'HopDuration','reg':0x2,'bank':0x0,'span':2,'val':'0'},
    '0004': {'name':'InitialParentNwkID','reg':0x4,'bank':0x0,'span':1,'val':'0'},
    '0015': {'name':'SleepMode','reg':0x15,'bank':0x0,'span':1,'val':'0'},
    '0016': {'name':'WakeResponseTime','reg':0x16,'bank':0x0,'span':1,'val':'0'},
    '0017': {'name':'WakeLinkTimeout','reg':0x17,'bank':0x0,'span':1,'val':'0'},
    '0018': {'name':'TxPower','reg':0x18,'bank':0x0,'span':1,'val':'0'},
    '001C': {'name':'UserTag','reg':0x1C,'bank':0x0,'span':16,'val':'0'},
    '0034': {'name':'TreeRoutingEn','reg':0x34,'bank':0x0,'span':1,'val':'0'},
    '0035': {'name':'BaseModeNetID','reg':0x35,'bank':0x0,'span':1,'val':'0'},
    '0037': {'name':'HeartbeatIntrvl','reg':0x37,'bank':0x0,'span':2,'val':'0'},
    '003A': {'name':'EnableRtAcks','reg':0x3a,'bank':0x0,'span':1,'val':'0'},
    '0101': {'name':'AccessMode','reg':0x1,'bank':0x1,'span':1,'val':'0'},
    '0102': {'name':'BaseSlotSize','reg':0x2,'bank':0x1,'span':1,'val':'0'},
    '0103': {'name':'LeasePeriod','reg':0x3,'bank':0x1,'span':1,'val':'0'},
    '0107': {'name':'CSMA_Predelay','reg':0x7,'bank':0x1,'span':1,'val':'0'},
    '0108': {'name':'CSMA_Backoff','reg':0x8,'bank':0x1,'span':1,'val':'0'},
    '010B': {'name':'CSMA_RemtSlotSize','reg':0xb,'bank':0x1,'span':1,'val':'0'},
    '0200': {'name':'MacAddress','reg':0x0,'bank':0x2,'span':3,'val':'0'},
    '0203': {'name':'CurrNwkAddr','reg':0x3,'bank':0x2,'span':1,'val':'0'},
    '0204': {'name':'CurrNwkID','reg':0x4,'bank':0x2,'span':1,'val':'0'},
    '0208': {'name':'RemoteSlotSize','reg':0x8,'bank':0x2,'span':1,'val':'0'},
    '0205': {'name':'CurrRF_DataRate','reg':0x5,'bank':0x2,'span':1,'val':'0'},
    '020D': {'name':'FirmwareVersion','reg':0xd,'bank':0x2,'span':1,'val':'0'},
    '020E': {'name':'FirmwareBuildNum','reg':0xe,'bank':0x2,'span':2,'val':'0'},
    '0214': {'name':'CurrTxPower','reg':0x14,'bank':0x2,'span':1,'val':'0'},
    '0300': {'name':'SerialRate','reg':0x0,'bank':0x3,'span':2,'val':'0'},
    '0302': {'name':'SerialParams','reg':0x2,'bank':0x3,'span':1,'val':'0'},
    '0303': {'name':'SerialControls','reg':0x3,'bank':0x3,'span':1,'val':'0'},
    '0304': {'name':'SPI_Mode','reg':0x4,'bank':0x3,'span':1,'val':'0'},
    '0305': {'name':'SPI_Divisor','reg':0x5,'bank':0x3,'span':1,'val':'0'},
    '0306': {'name':'SPI_Options','reg':0x6,'bank':0x3,'span':1,'val':'0'},
    '0307': {'name':'SPI_MasterCmdLen','reg':0x7,'bank':0x3,'span':1,'val':'0'},
    '0308': {'name':'SPI_MasterCmdStr','reg':0x8,'bank':0x3,'span':32,'val':'0'},
    '0400': {'name':'ProtocolMode','reg':0x0,'bank':0x4,'span':1,'val':'0'},
    '0506': {'name':'ADC0','reg':0x6,'bank':0x5,'span':2,'val':'0'},
    '0508': {'name':'ADC1','reg':0x8,'bank':0x5,'span':2,'val':'0'},
    '050A': {'name':'ADC2','reg':0xa,'bank':0x5,'span':2,'val':'0'},
    '0600': {'name':'GPIO_Dir','reg':0x0,'bank':0x6,'span':1,'val':'0'},
    '0601': {'name':'GPIO_Init','reg':0x1,'bank':0x6,'span':1,'val':'0'},
    '0604': {'name':'GPIO_SleepMode','reg':0x4,'bank':0x6,'span':1,'val':'0'},
    '0605': {'name':'GPIO_SleepDir','reg':0x5,'bank':0x6,'span':1,'val':'0'},
    '0606': {'name':'GPIO_SleepState','reg':0x6,'bank':0x6,'span':1,'val':'0'},
    '060B': {'name':'ADC_SampleIntvl','reg':0xb,'bank':0x6,'span':2,'val':'0'},
    '0619': {'name':'IO_ReportTrigger','reg':0x19,'bank':0x6,'span':1,'val':'0'},
    '061A': {'name':'IO_ReportInterval','reg':0x1a,'bank':0x6,'span':4,'val':'0'},
    '0800': {'name':'BaseNetworkId','reg':0x0,'bank':0x8,'span':1,'val':'0'},
    'FF0C': {'name':'SleepModeOverride','reg':0xc,'bank':0xff,'span':1,'val':'0'},
}
for i in range(2):
    curreg = struct.pack('< B', i+1)[0]
    regs['08'+binascii.hexlify(curreg)] = {'name':'ParentNetworkId'+str(i+1),'reg':i+1,'bank':0x8,'span':1,'val':'0'}

s = struct.Struct('< B B B B B B H')
values = (0xfb, 6, 0x04, 0x00, 0x03, 2, 0x0030) # set baud 9600
baud9600 = s.pack(*values)
values = (0xfb, 6, 0x04, 0x00, 0x03, 2, 0x0004) # set baud 115.2K
baud115200 = s.pack(*values)
values = (0xfb, 5, 0x04, 0xff, 0xff, 1, 0) # factor defaults
s = struct.Struct('< B B B B B B B')
factorydefaults = s.pack(*values)
values = (0xfb, 7, 0x00, 'DNTCFG') # enterprotocolmode,DNTCFG
s = struct.Struct('< B B B 6s')
dntcfg = s.pack(*values)
values = (0xfb, 2, 0x02, 1) # softwarereset,1
s = struct.Struct('< B B B B')
softwarereset = s.pack(*values)

xmit_delay = 1
file_name = ''
ping = False
quick_test = False
last_discover = ''

TUNSETIFF = 0x400454ca
IFF_TUN   = 0x0001
IFF_TAP   = 0x0002
TUNMODE = IFF_TUN
SIOCGIFMTU = 0x8921
SIOCSIFMTU = 0x8922

def start_thread(name, func, *argv):
    thread = threading.Thread(target=func, args=argv)
    thread.setDaemon(True)
    thread.setName(name)
    thread.start()
    return thread

def fork_thread(func, argv):
    child_pid = os.fork()
    if child_pid == 0:
        func(*argv)
        sys.exit(0)
    return child_pid

MB = 1*1024*1024
class TunManager:
    def __init__(self, proxy):
        self.tun_file = {}
        self.tun_reader_thread = {}
        self.tun_iface = {}
        self.tun_radio_start_in_progress = False
        self.substation_proxy = proxy
        # 10.1.128.sn# and 10.1.0.sn# are used for wifi master slave comms.
        # 10.2.254.1 is master radio
        # 10.2.x.y where x!=254 is used for slave radios reaching master
        # 10.sn#.254.1 is used for slave base radio
        # 10.sn#.x.y where x!=254 is used for slave radios reaching slave at sn#
        self.substations = [str(i) for i in range(3,254)]
        self.substation_lock = threading.RLock()
        self.log_tun = False
        if gv.sd['master']:
            start_thread('receive_persistent_tunnel', self.receive_persistent_tunnel)
        start_thread('persistent tunnel', self.persistent_tunnel)

    def get_mtu(self, tun_index, s, caller):
        '''Use socket ioctl call to get MTU size'''
        ifname = self.tun_iface[tun_index]
        ifr = ifname + '\x00'*(32-len(ifname))
        try:
            ifs = ioctl(s, SIOCGIFMTU, ifr)
            mtu = struct.unpack('<H',ifs[16:18])[0]
        except Exception, s:
            logger.critical(caller+ ' socket ioctl get_mtu call failed: {0}'.format(s))
            raise

        logger.info(caller + ' get_mtu: mtu of {0} = {1}'.format(ifname, mtu))
        return mtu

    def set_mtu(self, tun_index, s, mtu, caller):
        '''Use socket ioctl call to set MTU size'''
        ifname = self.tun_iface[tun_index]
        ifr = struct.pack('<16sH', ifname, mtu) + '\x00'*14
        try:
            ifs = ioctl(s, SIOCSIFMTU, ifr)
            mtu = struct.unpack('<H',ifs[16:18])[0]
        except Exception, s:
            logger.critical(caller + ' socket ioctl set_mtu call failed: {0}'.format(s))
            raise

        logger.info(caller + ' set_mtu: mtu of {0} = {1}'.format(ifname, mtu))
        return mtu

    def stop_tun(self, tun_index):
        # kill off existing thread and tun file if necessary
        try:
            tun_file = self.tun_file[tun_index]
            if tun_file:
                logger.info('deleting tun_file tun_index: ' + tun_index)
                del self.tun_file[tun_index]
                try:
                    os.close(tun_file)
                except Exception as ex:
                    logger.exception('stop_tun tun_file close failed')

            tun_reader_thread = self.tun_reader_thread[tun_index]
            if tun_reader_thread:
                del self.tun_reader_thread[tun_index]
                # force thread that may be blocking on read to get something
                rc = subprocess.call(['ping', '-I', self.tun_iface[tun_index], '8.8.8.8', '-c', '1'])
                tun_reader_thread.join()

            del self.tun_iface[tun_index]

            if tun_index == 'radio':
                try:
                    subprocess.call(['/etc/init.d/bind9', 'stop']) # only have running when master radio is running
                    logger.info('stop_tun stop bind9')
                except:
                    logger.critical('stop_tun could not stop bind9')

        except Exception as ex: # nothing to stop
            logger.info('stop_tun exception: ' + str(ex))

    def socket_reader(self, s, tun_index):
        """ Read the socket for IP messages (the length in bytes [2-3])
            and when there is a full message, write it to the tun file.

            If the socket or tun_file have been closed, just exit as this thread should die.
        """

        tun_file = self.tun_file[tun_index]
        logger.debug('entering socket_reader')
        try:
            x = s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # overrides value (in seconds) shown by sysctl net.ipv4.tcp_keepalive_time
            s.setsockopt(socket.SOL_TCP, socket.TCP_KEEPIDLE, 60)
            # overrides value shown by sysctl net.ipv4.tcp_keepalive_probes
            s.setsockopt(socket.SOL_TCP, socket.TCP_KEEPCNT, 4)
            # overrides value shown by sysctl net.ipv4.tcp_keepalive_intvl
            s.setsockopt(socket.SOL_TCP, socket.TCP_KEEPINTVL, 15)
            s.settimeout(10.) # set timeout

            self.set_mtu(tun_index, s, 1440, 'socket_reader')
            msg = ''
            msg_len = 0
            while True:
                # use tcpdump -n -i iface for tracing
                try:
#                    new_msg = s.recv(MB/1024)
                    new_msg = s.recv(64) # keep small because of timing anomaly
                except socket.timeout:
                    print 'Socket timeout, loop and try recv() again'
                    continue
                except:
                    logger.exception('socket_reader other exception')
                    raise  # socket shut down
 
                if len(new_msg) == 0:
                    logger.info('socket_reader shut down...exiting')
                    break  # socket shut down

                msg += new_msg
                logger.debug('socket_reader more msg len: ' + str(len(new_msg)))
                if msg_len == 0 or len(msg) >= msg_len+4:
                    if len(msg) < 24:
                        #msg may actually come through socket at any size....keeep accumulating
                        continue
                    if msg_len == 0:
                        msg_len = struct.unpack('> H',msg[6:8])[0]
                    if len(msg) >= msg_len + 4:
                        os.write(tun_file, msg[:msg_len+4])
                        if self.log_tun:
                            logger.info('socket_reader write tun len: ' + str(msg_len+4) + ' from: ' + binascii.hexlify(msg[16:20]) + ' to: ' + binascii.hexlify(msg[20:24]) + ' data: ' + binascii.hexlify(msg[0:min(msg_len+4,dmsg_len)]))
                        msg = msg[msg_len+4:]
                        msg_len = 0

        except Exception as ex:
            logger.exception('socket_reader exception')
            pass

    def tun_file_reader(self, tun_index, s):
        """ Read the tun_file for data and write it to the socket.

            If the socket or tun_file have been closed, just exit as this thread should die.
        """
        tun_file = self.tun_file[tun_index]
        self.set_mtu(tun_index, s, 1440, 'tun_file_reader')
        logger.debug('entering tun_file_reader')
        try:
            while True:
                # use tcpdump -n -i iface for tracing
                data = os.read(tun_file, 5*MB)
                msg_len = len(data)
                if msg_len < 24:
                    logger.critical('tun_file_reader bad ip packet len: ' + str(msg_len) + ' data: ' + binascii.hexlify(data))
                    continue
                if self.log_tun:
                    logger.info('tun_file_reader good msg len: ' + str(msg_len) + ' from: ' + binascii.hexlify(data[16:20]) + ' to: ' + binascii.hexlify(data[20:24]) + ' data: ' + binascii.hexlify(data[0:min(msg_len,dmsg_len)]))
                s.send(data)
        except Exception as ex:
            logger.exception('tun_file_reader exception...closing s')
            try:
                s.close()
            except:
                logger.exception('tun_file_reader could not close s')
            pass # exit thread

    def pinger(self, substation, interval):
        """Do http://10.1.0.substation/ping every interval seconds"""

        urlcmd = 'http://10.1.0.' + str(substation) + '/ping'
        while True:
            try:
                data = urllib2.urlopen(urlcmd, timeout=10)
                try:
                    data = json.load(data)
                except ValueError:
                    logger.critical('pinger bad json: ' + str(ex))
            except Exception as ex:
                logger.critical('pinger exception: ' + str(ex))
            time.sleep(interval)

    def monitor_tunnel(self, s, addr):
        """New request from substation for identifier.  Return the identifier and set up the tunnels
           to support communication and addressing with the substation.

           If the socket is broken, clean up the tunnels and recover the assigned substation number for reassignment
        """
        logger.info('in monitor_tunnel from: ' + addr[0] + ':' + str(addr[1]))

        try:
            with self.substation_lock:
                substation = self.substations.pop(0)
        except:
            logger.exception('monitor_tunnel no available substation')
            return

        try:
            s.settimeout(10.) # set timeout in case attempt at flooding
            try:
                encmsg = s.recv(MB/1024)
            except:
                logger.exception('monitor_tunnel: failed to receive name')
                raise
            msg = decrypt_name(encmsg)
            connect_params = substation + ';' + str(gv.sd['htp'])
            logger.info('monitor_tunnel: decrypted name: ' + msg + ' tx connect_params: ' + connect_params)
            msg = msg.strip()
            nm = validate_fqdn(msg)
            if nm != 'Irricloud':
                s.send(connect_params)
            else:
                raise IOError, 'Invalid name'
        except:
            try:
                s.close()
            except:
                logger.exception('monitor_tunnel: could not close s')
            with self.substation_lock:
                self.substations.append(substation)
            logger.exception('monitor_tunnel invalid initialization.  Returning substation: ' + substation)
            return

        tun_index = 'm'+substation
        self.init_tun(tun_index)
        try:
            self.tun_reader_thread[tun_index] = start_thread('tun_reader'+substation, self.tun_file_reader, tun_index, s)
            #start_thread('pinger'+substation, self.pinger, substation, 10)
            self.socket_reader(s, tun_index)  # wont return until tunfile or socket closed
#            fork_thread(self.tun_file_reader, (tun_index, s))
#            sr_pid = fork_thread(self.socket_reader, (s, tun_index))
#            os.waitpid(sr_pid, 0)

        except Exception as ex:
            logger.exception('monitor_tunnel unexpected exception ' + tun_index)

        logger.info('monitor_tunnel stopping tun: ' + str(tun_index))
        self.stop_tun(tun_index)

        logger.info('monitor_tunnel returning substation: ' + substation)
        with self.substation_lock:
            self.substations.append(substation)

    def receive_persistent_tunnel(self):
        """Listen for up to 128 slave requests and assign substation numbers in range 2..254"""

        logger.debug('entering receive_persistent_tunnel')
        while True:
            try:
                # master radio gets 10.2.254.1
                self.substation_proxy.radio_interface.network_prefix = '10.2'

                # create a socket object
                serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                serversocket.bind(('', 9081))

                # queue up to 128 requests
                serversocket.listen(128)
                while True:
                    clientsocket,addr = serversocket.accept()
                    start_thread('monitor', self.monitor_tunnel, clientsocket, addr)
            except Exception as ex:
                # dont really expect to hit this and dont know that appropriate cleanup occurs
                logger.exception('receive_persistent_tunnel')
                time.sleep(5)

    def persistent_tunnel(self):
        logger.debug('entering persistent_tunnel')
        while True:
            #check regularly if we want the vpntun data logged
            try:
               with open('./data/log_tun', 'r') as lt:
                   self.log_tun = True
            except:
                self.log_tun = False
            try:
                if gv.sd['slave'] and gv.sd['master_ip'] and not gv.sd['master']:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    port = 9081 if gv.sd['external_proxy_port'] == 0 else gv.sd['external_proxy_port']
                    logger.info('persistent_tunnel trying connect to ' + gv.sd['master_ip'] + ':' + str(port))
                    s.connect((gv.sd['master_ip'], port))
                    enc_name = encrypt_name(gv.sd['name'])
                    s.send(enc_name)
                    connect_params = s.recv(MB/1024) # get substation and master port
                    logger.info('persistent_tunnel received paramaters: ' + connect_params)
                    semi = connect_params.find(';')
                    try:
                        substation = connect_params[:semi]
                        if int(substation) <= 2 or int(substation) >= 254:
                            raise ValueError, 'Invalid Substation'
                        master_port = int(connect_params[semi+1:])
                        if master_port <= 0 or master_port > 32767:
                            raise ValueError, 'Invalid port number'
                    except:
                        raise
                    logger.info('persistent_tunnel got substation: ' + substation + ' port: ' + str(master_port))
                    gv.sd['master_port'] = master_port
                    tun_index = 's'+substation
                    self.init_tun(tun_index)
                    # substation radio gets 10.substation.254.1
                    self.substation_proxy.radio_interface.network_prefix = '10.' + substation

                    try:
                        self.tun_reader_thread[tun_index] = start_thread('tun_reader'+substation, self.tun_file_reader, tun_index, s)
                        self.socket_reader(s, tun_index)  # wont return until tunfile or socket closed
#                        fork_thread(self.tun_file_reader, (tun_index, s))
#                        sr_pid = fork_thread(self.socket_reader, (s, tun_index))
#                        os.waitpid(sr_pid, 0)
                        logger.info('persistent_tunnel returned from socket_reader.  stopping tun')
                        self.stop_tun(tun_index)
                    except Exception as ex:
                        logger.exception('persistent_tunnel unexpected exception tun_index: ' + tun_index)
                        pass
                    self.substation_proxy.radio_interface.network_prefix = '10.0' # reset
                    logger.info('persistent_socket closing s')
                    try:
                        s.close()
                    except Exception as ex:
                        if s != 0:
                            logger.exception('persistent_tunnel cannot close socket')
                time.sleep(5)
            except Exception as ex:
                # maybe master not up or connection dropped.
                logger.info('persistent_socket socket.error....closing s')
                try:
                    s.close()
                    s = 0
                except:
                    pass
                time.sleep(10)

    def init_radio_tun(self):
        logger.debug('entering init_radio_tun')
        self.stop_tun('radio') # stop radio tun
        radio = self.substation_proxy.radio_interface
        if radio and radio.network_prefix != '10.0': # 10.0 prefix means we have not yet established the substation number
            logger.info('entering init_radio_tun radio: ' + radio.network_prefix)
            # set up tun interface
            f = os.open("/dev/net/tun", os.O_RDWR)
            self.tun_file['radio'] = f
            ifs = ioctl(f, TUNSETIFF, struct.pack("16sH", "dnttun%d", TUNMODE))
            ifname = ifs[:16].strip("\x00")
            gv.radio_iface = ifname
            self.tun_iface['radio'] = ifname
            addr = 'FF' + binascii.hexlify(struct.pack('B',radio.cur_nwkid)[0]) + \
                          binascii.hexlify(struct.pack('B',radio.cur_nwkaddr)[0])
            radio.mac2addr[radio.cur_mac] = {'addr':addr}
            radio.addr2stuff[addr] = {'mac':radio.cur_mac, 'msg':'', 'responses':{}}
            radio_ip = radio.compute_radio_ip()
            ifconfig_params = ['ifconfig', ifname, radio_ip+'/16']
            rc = subprocess.call(ifconfig_params)
            logger.info(' '.join(ifconfig_params) + ' rc: ' + str(rc))
            if not gv.sd['master_ip']:
                # if no internet access, use base radio for routes we dont understand
                base_radio = radio.network_prefix + '.254.1'
                route_params = ['route', 'add', 'default', 'gw', base_radio]
                rc = subprocess.call(route_params)
                logger.info(' '.join(route_params) + ' rc: ' + str(rc))

                # make /etc/resolv.conf refer to the base radio which will be running bind9 which gives us dns
                # for this remote radio
                cmd = ['chattr', '-i', '/etc/resolv.conf']
                subprocess.call(cmd)
                try:
                    with open('/etc/resolv.conf', 'w') as rfile:
                        lines = ['# Generated by substation_proxy.py\n',
                                 '# ' + time.strftime('%Y-%m-%d %H:%M:%S', gv.nowt) + '\n',
                                 'nameserver ' + base_radio + '\n']
                        rfile.writelines(lines)
                except:
                    logger.exception('init_radio_tun could not write /etc/resolv.conf')
                # prevent anything from overwriting resolv.conf
                cmd = ['chattr', '+i', '/etc/resolv.conf']
                subprocess.call(cmd)
            else:
                # this is a base radio.  Set up nat, so remote radios can make accesses to the internet through the base
                try:
                    cmds = [['/etc/init.d/bind9', 'stop'],
                            ['/etc/init.d/bind9', 'start'], # seems to need restarting sometimes
                            ['sysctl', 'net/ipv4/ip_forward=1'],
                            ['iptables', '-F'],
                            ['iptables', '-t', 'nat', '-F'],
                            ['iptables', '-t', 'nat', '-A', 'POSTROUTING', '-o', 'wlan0', '-j', 'MASQUERADE'],
                            ['iptables', '-A', 'FORWARD', '-i', ifname, '-o', 'wlan0', '-m', 'state', '--state', 'RELATED,ESTABLISHED', '-j', 'ACCEPT'],
                            ['iptables', '-A', 'FORWARD', '-i', 'wlan0', '-o', ifname, '-j', 'ACCEPT'],
                           ]
                    for cmd in cmds:
                        try:
                            rc = subprocess.call(cmd)
                            logger.info(' '.join(cmd) + ' rc: ' + str(rc))
                        except:
                            logger.exception('FAILED: ' + ' '.join(cmd))
                except:
                    logger.exception('init_radio_tun could not initialize cmd: ' + ' '.join(cmd))

            self.tun_reader_thread['radio'] = start_thread('tun_radio__reader', self.tun_radio_reader)
            self.tun_radio_start_in_progress = False
        else:
            logger.info('no radio or radio network_prefix')


    def init_tun(self, tun_index):
        logger.debug('in init_tun.  tun_index: ' + tun_index)

        # set up tun interface
        f = os.open("/dev/net/tun", os.O_RDWR)
        self.tun_file[tun_index] = f

        ifs = ioctl(f, TUNSETIFF, struct.pack("16sH", "vpntun%d", TUNMODE))
        ifname = ifs[:16].strip("\x00")
        if tun_index[0] == 's':
            gv.vpn_iface = ifname # only used for slaves
        self.tun_iface[tun_index] = ifname
        logger.debug('init_tun ifname: ' + ifname)
        master_ip = '10.1.128.'+tun_index[1:] # dropping leading 's' or 'm'
        tun_ip_address = '10.1.0.'+tun_index[1:]
        if tun_index[0] == 'm':
            local_ip = master_ip
            remote_ip = tun_ip_address
        elif tun_index[0] == 's':
            local_ip = tun_ip_address
            remote_ip = master_ip
        else:
            logger.critical('Unexpected tun_index: ' + tun_index)
        ifconfig_params = ['ifconfig', ifname, local_ip, 'pointopoint', remote_ip]
        rc = subprocess.call(ifconfig_params)
        logger.info(' '.join(ifconfig_params) + ' rc: ' + str(rc))
        if tun_index[0] == 's':
            route_params = ['route', 'add', '-net', '10.0.0.0', 'netmask', '255.0.0.0', 'gw', master_ip]
            rc = subprocess.call(route_params)
            logger.info(' '.join(route_params) + ' rc: ' + str(rc))
        elif tun_index[0] == 'm': # route to remote radios from each radio base
            route_params = ['route', 'add', '-net', '10.' + tun_index[1:] + '.0.0', 'netmask', '255.255.0.0', ifname]
            rc = subprocess.call(route_params)
            logger.info(' '.join(route_params) + ' rc: ' + str(rc))

    def get_radio_addr(self, packed_ip_addr):
        """Take binary packed IP address and if it is not a radio address use base radio address."""
        first_octet = struct.unpack('B', packed_ip_addr[0])[0]
        second_octet = struct.unpack('B', packed_ip_addr[1])[0]
        if second_octet == 1 or first_octet != 10: # must not be radio address. Treat as nat to base radio
            logger.debug('get_radio_addr got ' + str(first_octet) + '.' + str(second_octet) + '.x.x address.')
            addr = 'FFFE01'
        else:
            addr = ('FF' + binascii.hexlify(packed_ip_addr[2]) + binascii.hexlify(packed_ip_addr[3])).upper()
        if addr == 'FFFE01': #base?
            addr = 'FF0000'
        return addr

    def tun_radio_reader(self):
        time.sleep(2)
        logger.info('entering tun_radio_reader')
        while self.tun_reader_thread['radio']:
            try:
                data = os.read(self.tun_file['radio'], 5*MB)
                msg_len = len(data)
                if msg_len < 24:
                    logger.critical('tun_radio_reader bad ip packet len: ' + str(msg_len) + ' data: ' + binascii.hexlify(data))
                    continue
                if self.log_tun:
                    logger.info('tun_radio_reader good msg len: ' + str(msg_len) + ' from: ' + binascii.hexlify(data[16:20]) + ' to: ' + binascii.hexlify(data[20:24]) + ' data: ' + binascii.hexlify(data[0:min(msg_len,dmsg_len)]))
                from_addr = self.get_radio_addr(data[16:20])
                to_addr = self.get_radio_addr(data[20:24])
                if from_addr not in self.substation_proxy.radio_interface.addr2stuff or \
                       self.substation_proxy.radio_interface.addr2stuff[from_addr]['mac'] == 'unknown':
                    logger.info('tun_radio_reader missing map for from_addr: ' + from_addr)
                    if from_addr == 'FF0000':  # the base doesnt have heartbeat....get base mac
                        e = regs['0200']
                        self.substation_proxy.radio_interface.pack_getregister(e['reg'], e['bank'], e['span'], from_addr)
                    continue
                else:
                    from_mac = self.substation_proxy.radio_interface.addr2stuff[from_addr]['mac']
                if to_addr not in self.substation_proxy.radio_interface.addr2stuff or \
                        self.substation_proxy.radio_interface.addr2stuff[to_addr]['mac'] == 'unknown':
                    logger.info('tun_radio_reader missing map for to_addr: ' + to_addr)
                    if to_addr == 'FF0000':  # the base doesnt have heartbeat....get base mac
                        e = regs['0200']
                        self.substation_proxy.radio_interface.pack_getregister(e['reg'], e['bank'], e['span'], to_addr)
                    continue
                else:
                    to_mac = self.substation_proxy.radio_interface.addr2stuff[to_addr]['mac']

                if from_mac != to_mac:
                    self.substation_proxy.radio_interface.remote_command_response('tun_radio_reader', 2, to_mac, from_mac, data)
                else:
                    logger.debug('Skipping reflexive remote_command_response mac: ' + to_mac)
            except:
                logger.exception('tun_radio_reader')
                del self.tun_reader_thread['radio']


class SerialRadio:
    def __init__(self, proxy, power=1, initial_config='', baud=115200, key='', radio_name='', radio_routing=255):
        self.substation_proxy = proxy
        self.network_prefix = '10.0'
        self.serialport = self.open_serial(baud)
        self.initial_configuration = initial_config
        self.power = power
        self.radio_routing = radio_routing
        self.chunksize = 195 # do not allow txmsg len to ever get to SPI_MAGIC and look like spi message
        for i in range(16-len(key)):
            key += '\0'
        self.key = key[0:16]
        for i in range(16-len(radio_name)):
            radio_name += '\0'
        self.radio_name = radio_name[0:16]
        self.command_q = []
        self.mac2addr = {}
        self.addr2stuff = {}
        self.spi_messages = {}
        self.spi_lock = threading.RLock()
        self.alive = True
        self.cur_nwkid = 255
        self.cur_nwkaddr = 255
        self.cur_mac = '000000'
        self.command_lock = threading.RLock()
        self.msg_count = 0
        self.response_lock = threading.RLock()
        self.memory_save_and_reset = False

    def open_serial(self, baud):
        ser = serial.Serial()
        ser.port     = '/dev/dnt900'
        ser.baudrate = baud
        ser.parity   = 'N'
        ser.rtscts   = True
        ser.dsrdtr   = True
        ser.timeout  = 5           # required so that the reader thread can exit
        ser.write_timeout  = 1     # required so that the writer can release queue lock

        try:
            ser.open()
        except serial.SerialException, e:
            logger.exception("Could not open serial port %s: %s\n" % (ser.portstr, e))
            return 0

        logger.info('serial ' + ser.name + ' open.  Baud: ' + str(ser.baudrate))
        return ser

    def compute_radio_ip(self):
        if self.cur_nwkid == 0 and self.cur_nwkaddr  == 0:
            router = 254
            entry = 1
        else:
            router = self.cur_nwkid
            entry = self.cur_nwkaddr
        return self.network_prefix + '.' + str(router) +'.' + str(entry)

    def enqueue_command(self, fullcmd, is_spi_msg=False):
        with self.command_lock:
            cmds = struct.unpack('B B B', fullcmd[:3])
            self.command_q.append([cmds[2], fullcmd, is_spi_msg])

    def rediscover(self, data):
        values = (0xfb, 4, 0x06)
        s = struct.Struct('< B B B')
        new_cmd = s.pack(*values) + data[:3]
        self.enqueue_command(new_cmd)

    def write(self, r, d, is_spi_msg):
        cmd = struct.unpack('B B B', d[:3])
        get_or_set = bool(cmd[2] == 0xa or cmd[2] == 0xb) # get/setremoteregister
        if get_or_set:
            reg = struct.unpack('B', d[6])[0]
            bank = struct.unpack('B', d[7])[0]
            addr = (binascii.hexlify(d[5]) + binascii.hexlify(d[4]) + binascii.hexlify(d[3])).upper()
            regs_idx = ("{0:0{1}x}".format(bank,2) + "{0:0{1}x}".format(reg,2)).upper()
            desc = regs[regs_idx]
            name = desc['name']
            if cmd[2] == 0xa: #getremotereg?
                logger.debug('issue get ' + name + ' to addr: ' + addr)
            else: # setremotereg!
                span = desc['span']
                if span == 1:
                    val = struct.unpack('B', d[9])[0]
                elif span == 2:
                    val = struct.unpack('H', d[9:11])[0]
                elif span == 4:
                    val = struct.unpack('I', d[9:13])[0]
                else:
                    val = 0
                logger.debug('issue set ' + name + ' to addr: ' + addr + ' to val: ' + hex(val))
        elif cmd[2] == 0x4: # setregister?
            reg = struct.unpack('B', d[3])[0]
            bank = struct.unpack('B', d[4])[0]
            if bank == 0xff and reg == 0xff:
                logger.info('issue set MemorySave')
                self.memory_save_and_reset = True # signal that we have issued this instruction
            
        self.serialport.write(d)   # may raise timeout if write fails
#        if get_or_set:
#          logger.info('serialport write returned')
        if (d == baud9600 or d == baud115200):
            self.serialport.flush() # ensure write is done
            rate = 115200 if d == baud115200 else 9600
            self.serialport.baudrate = rate
            logger.info('write baudrate update: ' + str(rate))

    def get_response_holder(self, radio_mac):
        try:
            addr = self.mac2addr[radio_mac]['addr']
            responses = self.addr2stuff[addr]['responses']
            with self.response_lock:
                self.msg_count += 1
                if str(self.msg_count) in responses:
                    logger.warning('duplicate response msgid: ' + str(self.msg_count) + ' mac:  ' + radio_mac + ' addr: ' + addr)
                    del responses[str(self.msg_count)]
                responses[str(self.msg_count)] = {'response':'','response_present':0}
                return self.msg_count
        except:
            pass
        return 0

    def build_transmit_msg(self, req, macraw0, macraw1, macraw2, msgid, msg):
        txmsg = struct.pack('< B B B B I', req, macraw0, macraw1, macraw2, msgid)
        for i in range(len(msg)):
            txmsg += struct.pack('B', struct.unpack('B',msg[i:i+1])[0])
        return txmsg


    def transmit_msg(self, addr, msg, is_spi_msg=False):
        """ Send a message broken into chunksize portions. """

        msglen = len(msg)
        written = 0
        logger.debug('xmit msg len: ' + str(msglen) + ' addr: ' + addr)
        while True:
            chunk = min(self.chunksize, msglen-written)
            if chunk == SPI_MAGIC:
                logger.critical('attempting to xmit message with magic spi length')
            if is_spi_msg:
                data = struct.pack('< B B B B B B', 0xfb, 4+chunk, 0x05, int(addr[4:],16), int(addr[2:4],16), int(addr[0:2],16))
            else:
                data = struct.pack('< B B B B B B B', 0xfb, 5+chunk, 0x05, int(addr[4:],16), int(addr[2:4],16), int(addr[0:2],16), chunk)
            for i in range(written,written+chunk):
                data += struct.pack('B', struct.unpack('B', msg[i:i+1])[0])
#            logger.critical('transmit_msg data: ' + binascii.hexlify(data))
            self.enqueue_command(data, is_spi_msg)
            written += chunk
            if chunk < self.chunksize:
                break

    def remote_command_response(self, pre, type, tomac, frommac, msg):
        """ Send a command described by msg to radio tomac (embedding frommac in msgheader) and return its response. """

        ret_str = json.dumps({'unreachable':1})
        try:
            progress = 0
            tomacraw = [int(tomac[0:2],16),int(tomac[2:4],16),int(tomac[4:],16)]
            frommacraw = [int(frommac[0:2],16),int(frommac[2:4],16),int(frommac[4:],16)]
            try:
                addr = self.mac2addr[tomac]['addr']
            except:
                logger.info(pre + ': message_response cannot transmit to radio: ' + tomac)
                discover = struct.pack('< B B B B B B', 0xfb, 4, 0x06, tomacraw[2], tomacraw[1], tomacraw[0])
                self.enqueue_command(discover)
                raise
            if type != 2 and type != 3:
                msgid = self.get_response_holder(tomac)
            else:
                msgid = 0xdeadbeef
            txmsg = self.build_transmit_msg(type, frommacraw[2], frommacraw[1], frommacraw[0], msgid, msg)
            logger.debug('about to xmit to addr: ' + addr + ' type: ' + str(type) + ' frommac: ' + frommac + ' tomac: ' + tomac + ' msgid: ' + str(msgid) + ' len: ' + str(len(msg)))
            self.transmit_msg(addr, txmsg)
            progress += 1
            if type != 2 and type != 3:
                tries = 0
                while tries < 10:
                    with self.response_lock:
                        if self.addr2stuff[addr]['responses'][str(msgid)]['response_present']:
                            ret_str = self.addr2stuff[addr]['responses'][str(msgid)]['response']
                            logger.debug('got xmit response: ' + str(msgid))
                            break
                    time.sleep(.5)
                    tries += 1
                if tries > 4:
                    logger.info(pre + ': stopped looking for response addr: ' + addr + ' msgid: ' + str(msgid) + ' tries: ' + str(tries))
                progress += 1
                try:
                    logger.debug('deleting response')
                    with self.response_lock:
                        del self.addr2stuff[addr]['responses'][str(msgid)]
                except:
                    logger.warning(pre + ': message_response could not delete responses msgid: ' + str(msgid))
        except:
            logger.debug(pre + ': message_response no response from mac: ' + tomac + ' progress: ' + str(progress))
        return ret_str

    def check_command(self, c):
        cmd_type = 'Flush Remaining'
        if (c >= 0x10 and c <= 0x16) or (c >= 0x1a and c <= 0x1b):
            cmd_type = binascii.hexlify(chr(c))
        elif c == 0x26:
            cmd_type = 'RxData'
        elif c == 0x27:
            cmd_type = 'Announce'
        elif c == 0x28:
            cmd_type = 'RxEvent'
        elif c == 0x2c:
            cmd_type = 'JoinRequest'
        else:
            logger.warning('discarding cmd0: ' + str(c))
        return cmd_type

    def rssi2str(self, rssi):
        if rssi == 0x7f:
            return ' No RSSI (no ack)'
        elif rssi == 0x7e:
            return ' No RSSI (routed pkt)'
        return ''

    def process_spi_messages(self, addr):
        logger.debug('enter process_spi_messages: 0x' + addr)
        while True:
            with self.spi_lock:
                if addr in self.spi_messages:
                    # data gets pushed too fast and multiple spi messages arrive at once which dest cant handle
#                    while len(self.spi_messages[addr]['messages']) > 0:
#                        try:
#                            msg = self.spi_messages[addr]['messages'].pop(0)
#                            logger.debug('queuing spi message for addr: ' + addr)
#                            self.transmit_msg(addr, msg, True)
#                        except Exception as ex:
#                            logger.critical('process_spi_messages ex: ' + str(ex))
                    messages_count = len(self.spi_messages[addr]['messages'])
                    if messages_count == 0:
                        break
                    elif messages_count == self.spi_messages[addr]['spi_count']:
                        try:
                            msg = self.spi_messages[addr]['messages'].pop(0)
                            logger.debug('queuing spi message for addr: ' + addr + ' with count: ' + str(self.spi_messages[addr]['spi_count']))
                            self.transmit_msg(addr, msg, True)
                        except Exception as ex:
                            logger.critical('process_spi_messages ex: ' + str(ex))
                else:
                    break
        logger.debug('exit process_spi_messages: 0x' + addr)

    def process_command(self, cmd_type, data):
        """Process cmd_type assuming enough data in data.  Return the amount of data that was consumed if the
           command was successful.  Otherwise return -1."""

        global msg249, last_discover

        if cmd_type == 'None' or cmd_type == 'Flush Remaining':
            return -1
        elif cmd_type[0] == '1':
            if cmd_type[1] == '0': # enter protocol mode
                logger.debug('enterprotocolmode')
                return 0
            elif cmd_type[1] == '2': # firmware upgrade
                logger.info('softwarereset')
                return 0
            elif cmd_type[1] == '3': # get register
                if len(data) < 4:
                    return -1
                d = struct.unpack('B B B', data[:3])
                reg = data[0]
                bank = data[1]
                span = data[2]
                if len(data) < 3+d[2]:
                    return -1
                val = data[3:3+d[2]]
                bankreg = (binascii.hexlify(bank)+binascii.hexlify(reg)).upper()
                regname = 'Unknown' if bankreg not in regs else regs[bankreg]['name']
                if bankreg != '0506' and bankreg != '0508' and bankreg != '050A' and bankreg != '0208':
                    # dont log ADC stuff and only RemoteSlotSize when changing chunksize
                    logger.info('getregister ' + regname + ' val: ' + binascii.hexlify(val) + \
                             ' reg: ' + binascii.hexlify(reg) + ' bank: ' + binascii.hexlify(bank) + \
                             ' span: ' + binascii.hexlify(span))
                if bankreg == '0200':
                    self.cur_mac = (binascii.hexlify(val[2]) + binascii.hexlify(val[1]) + binascii.hexlify(val[0])).upper()
                elif bankreg == '0203':
                    self.cur_nwkaddr = struct.unpack('B',val)[0]
                elif bankreg == '0204':
                    self.cur_nwkid = struct.unpack('B',val)[0]
                    if self.cur_nwkid != 255 and self.cur_mac != '000000' and self.substation_proxy.tun_manager:
                        self.substation_proxy.tun_manager.init_radio_tun()
                    else:
                        logger.info('getregister nwkid not yet valid')
                        if self.substation_proxy.tun_manager:
                            self.substation_proxy.tun_manager.tun_radio_start_in_progress = False
                elif bankreg == '0208':
                    new_chunk = struct.unpack('B',val)[0]
                    if new_chunk != self.chunksize+7:
                        logger.critical('getregister new chunksize: ' + str(new_chunk-7) + ' old: ' + str(self.chunksize))
                        self.chunksize = max(25,new_chunk-7)
                        if self.chunksize == SPI_MAGIC:
                            self.chunksize -= 1 # do not allow to look like spi message
                elif bankreg == '0506' or bankreg == '0508' or bankreg == '050A':
                    v = '0x' + binascii.hexlify(val[1]) + binascii.hexlify(val[0])
                    adc_idx = int('0x'+bankreg[3], 0)/2 - 3
                    sensor_data_str = '&adc'+str(adc_idx)+'='+v
                    urlcmd = 'http://localhost'
                    if gv.sd['htp'] != 0 and gv.sd['htp'] != 80:
                        urlcmd += ':'+str(gv.sd['htp'])
                    urlcmd += '/surrsd?name=localhost' + sensor_data_str
                    try:
                        urllib2.urlopen(urlcmd, timeout=1) # no response expected
                    except Exception as ex:
                        logger.error('getregister: analog register read got urlcmd exception: ' + str(ex) + ' urlcmd: ' + urlcmd)
                    
                return 3+d[2]
            elif cmd_type[1] == '4': # set register
                logger.debug('setregister')
                return 0
            elif cmd_type[1] == '5' or cmd_type[1] == 'b' or cmd_type[1] == 'B': # TxData or SetRemoteRegister
                if len(data) < 5:
                    return -1
                ct = 'TxData' if cmd_type[1] == '5' else 'setremoteregister'
                d = struct.unpack('B B B B B', data[:5])
                status = data[0]
                addr = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                rssi = data[4]
                rssi_str = self.rssi2str(d[4])
                if d[0] == 0:
                    logger.debug(ct + ' success for addr 0x: ' + addr + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
                else:
                    logger.warning(ct + ' failure for addr: 0x' + addr + ' status: ' + binascii.hexlify(status) + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
                    with self.spi_lock:
                        if addr in self.spi_messages:
                            logger.warning('deleting spi messages for addr: ' + addr + ' count: ' + str(self.spi_messages[addr]['spi_count']))
                            del self.spi_messages[addr]
                return 5
            elif cmd_type[1] == '6': # discover
                if len(data) < 7:
                    return -1
                d = struct.unpack('B B B B B B B', data[:7])
                mac = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                addr = (binascii.hexlify(data[6]) + binascii.hexlify(data[5]) + binascii.hexlify(data[4])).upper()
                if addr == 'FFFFFF':
                    logger.critical('discover addr came back FFFFFF.  Get mac from remote at: ' + mac)
                    addr = mac
                # do necessarily initialization in getremotereg
                e = regs['0200']
                self.pack_getregister(e['reg'], e['bank'], e['span'], addr)
                print 'setting last_discover to addr: ' + addr
                last_discover = addr
                logger.debug('discover')
                return 7
            elif cmd_type[1] == 'a' or cmd_type[1] == 'A': # getremotereg
                if len(data) < 4:
                    return -1
                if len(data) < 8:
                    d = struct.unpack('B B B B', data[:4])
                else:
                    d = struct.unpack('B B B B B B B B', data[:8])
                s = struct.Struct('B B B')
                status = d[0]
                addr = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                if status != 0:
                    logger.warning('GetRemoteReg addr: ' + addr + ' status: ' + binascii.hexlify(data[0]))
                    return 4
                elif len(data) < 8:
                    return -1
                rssi = data[4]
                rssi_str = self.rssi2str(d[4])
                reg = data[5]
                bank = data[6]
                span = d[7]
                if len(data) < 8 + span:
                    return -1
                val = data[8:8+span]
                bankreg = (binascii.hexlify(bank)+binascii.hexlify(reg)).upper()
                regname = 'Unknown' if bankreg not in regs else regs[bankreg]['name']
                logger.info('GetRemoteReg ' + regname + ' val: ' + binascii.hexlify(val) + ' addr: ' + addr + \
                            ' rssi: ' + binascii.hexlify(rssi) + rssi_str + \
                            ' reg: ' + binascii.hexlify(reg) + ' bank: ' + binascii.hexlify(bank) + ' span: ' + str(span))
                try:
                    base_mac = (binascii.hexlify(val[2]) + binascii.hexlify(val[1]) + binascii.hexlify(val[0])).upper()
                except:
                    base_mac = 'FFFFFF'
                if bankreg == '0200' and base_mac != 'FFFFFF': # get remote mac?
                    try:
                        if self.mac2addr[base_mac]['addr'] != addr or self.addr2stuff[addr]['mac'] != base_mac or \
                               'usertag' not in self.addr2stuff[addr]:
                            raise ValueError, 'Missing mapping'
                    except:
                        logger.info('GetRemoteReg updating addr: ' + addr + ' to mac: ' + base_mac)
                        self.mac2addr[base_mac] = {'addr':addr}
                        self.addr2stuff[addr] = {'mac':base_mac,'msg':'','responses':{}}
                        e = regs['001C']
                        self.pack_getregister(e['reg'], e['bank'], e['span'], addr)
                elif bankreg == '001C':
                    valls = struct.unpack('16s', val)[0]
                    vals = ''
                    for c in valls:
                        if c == '\0':
                            break
                        vals += c
                    try:
                        self.addr2stuff[addr]['usertag'] = vals
                    except: # if not yet defined get the mac from the remote and try again
                        e = regs['0200']
                        self.pack_getregister(e['reg'], e['bank'], e['span'], addr)
                return 8 + span
        elif cmd_type == 'JoinRequest':
            if len(data) < 8: #MAC,Addr,DeviceMode,SleepMode
                return -1
            mac = (binascii.hexlify(data[2]) + binascii.hexlify(data[1]) + binascii.hexlify(data[0])).upper()
            addr = (binascii.hexlify(data[5]) + binascii.hexlify(data[4]) + binascii.hexlify(data[3])).upper()
            dev = data[6]
            sleep = data[7]
            logger.info('JoinRequest mac: ' + mac + ' addr: ' + addr + ' dev: ' + binascii.hexlify(dev) + ' sleep: ' + binascii.hexlify(sleep))
            return 8
        elif cmd_type == 'Announce':
            if len(data) < 1:
                return -1
            ann_status = struct.unpack('B', data[:1])[0]
            logger.debug('Got announce status: ' + str(ann_status))
            if ann_status == 0xA0:
                logger.info('Announce: Radio startup completed')
                return 1
            elif ann_status == 0xA2:
                if len(data) < 6:
                    return -1
                d = struct.unpack('B B B B B B', data[:6])
                mac = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                rnge = data[5] # data[4] reserved
                logger.info('base: child joined network mac: ' + mac + ' rnge: ' + binascii.hexlify(rnge))
                self.rediscover(data[1:4])
                return 6
            elif ann_status == 0xA3:
                if len(data) < 6:
                    return -1
                d = struct.unpack('B B B B B B', data[:6])
                nwkid = data[1]
                mac = (binascii.hexlify(data[4]) + binascii.hexlify(data[3]) + binascii.hexlify(data[2])).upper()
                rnge = data[5]
                logger.info('remote: joined network mac: ' + mac + ' rnge: ' + binascii.hexlify(rnge) + ' nwkid: ' + binascii.hexlify(nwkid))
                if not self.initial_configuration:
                    self.rediscover(data[2:5])
                return 6
            elif ann_status == 0xA4:
                if len(data) < 2:
                    return -1
                nwkid = data[1]
                logger.info('remote: exited network base unreachable nwkid: ' + binascii.hexlify(nwkid))
                addr = 'FF' + binascii.hexlify(nwkid).upper() + '00'
                if addr in self.addr2stuff:
                    try:
                        progress = 0
                        mac = self.addr2stuff[addr]['mac']
                        logger.debug('remove addr2stuff for addr: ' + addr)
                        progress = 1
                        del self.addr2stuff[addr]
                        progress = 2
                        logger.debug('remove mac2addr for mac: ' + mac)
                        del self.mac2addr[mac]
                    except:
                        logger.warning('could not fully remove addr: ' + addr + ' progress: ' + str(progress))
                else:
                    logger.info('did not find addr: ' + addr + ' in addr2stuff')
                return 2
            elif ann_status == 0xA5:
                logger.info('Announce: Base Rebooted')
                return 1
            elif ann_status == 0xA7:
                if len(data) < 4:
                    return -1
                mac = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                logger.info('base: remote has left network mac: ' + mac)
                if mac in self.mac2addr:
                    try:
                        progress = 0
                        addr = self.mac2addr[mac]['addr']
                        progress = 1
                        if addr in self.addr2stuff:
                            logger.debug('remove addr2stuff for addr: ' + addr)
                            del self.addr2stuff[addr]
                        logger.debug('remove mac2addr for mac: ' + mac)
                        progress = 2
                        del self.mac2addr[mac]
                    except:
                        logger.warning('could not fully remove remote mac: ' + mac + ' progress: ' + str(progress))
                return 4
            elif ann_status == 0xA8:
                if len(data) < 11:
                    return -1
                d = struct.unpack('B B B B B B B B B B B', data[:11])
                mac = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                addr = ('FF' + binascii.hexlify(data[6]) + binascii.hexlify(data[4])).upper()
                nwkaddr = data[4]
                nwkid = data[5]
                parentnwkid = data[6]
                beaconrssi = data[7]
                beaconrssi_str = self.rssi2str(d[7])
                avgtxattempts = data[8]
                parentrssi = data[9]
                parentrssi_str = self.rssi2str(d[9])
                rnge = data[10]
                logger.debug('base: heartbeat rx mac: ' + mac + ' nwkaddr: ' + binascii.hexlify(nwkaddr) +\
                             ' nwkid: ' + binascii.hexlify(nwkid) + ' par nwkid: ' + binascii.hexlify(parentnwkid) +\
                             ' beaconrssi: ' + binascii.hexlify(beaconrssi) + beaconrssi_str + \
                             ' avgtxattempts: ' + binascii.hexlify(avgtxattempts) + ' parentrssi: ' + binascii.hexlify(parentrssi) +\
                             ' ' + parentrssi_str + ' rnge: ' + binascii.hexlify(rnge))

                if not self.initial_configuration:
                    error = False
                    if mac not in self.mac2addr:
#                        logger.info('heartbeat without mac2addr mapping.  mac: ' + mac)
                        error = True
                    elif self.mac2addr[mac]['addr'] not in self.addr2stuff:
#                        logger.info('heartbeat without addr2stuff mapping.  mac: ' + mac + ' addr: ' + self.mac2addr[mac]['addr'])
                        error = True
                    elif self.addr2stuff[self.mac2addr[mac]['addr']]['mac'] != mac:
#                        logger.info('heartbeat with invalid addr mapping.  mac: ' + mac + ' addr: ' + self.mac2addr[mac]['addr'] +\
#                                        ' addr2stuff[mac]: ' + self.addr2stuff[self.mac2addr[mac]['addr']]['mac'])
                        error = True

                    if error:
                        logger.info('ignoring heartbeat due to error...rediscover')
                        self.rediscover(data[1:4])
                    else:
                        pass
                return 11
            elif ann_status == 0xA9:
                if len(data) < 2:
                    return -1
                nwkid = data[1]
                logger.info('base: router heartbeat timeout nwkid: ' + binascii.hexlify(nwkid))
                return 2
            elif ann_status >= 0xE0 and ann_status <= 0xEE:
                logger.critical('announced error status: ' + binascii.hexlify(data[0]))
                return 1
            else:
                logger.critical('Unexpected announce command: ' + binascii.hexlify(data[0]))
        elif cmd_type == 'RxEvent':
            if len(data) < 21: # Addr, RSSI, reg, bank, span, and 14B from bank 5
                return -1
            d = struct.unpack('B B B B B B B', data[:7])
            addr = (binascii.hexlify(data[2]) + binascii.hexlify(data[1]) + binascii.hexlify(data[0])).upper()
            rssi = data[3]
            rssi_str = self.rssi2str(d[3])
            reg = data[4]
            bank = data[5]
            span = data[6]
            bank5data = data[7:22]
            sensor_data_str = ''
            for i in range(6):
                sensor_data_str += '&gpio'+str(i)+'=0x'+binascii.hexlify(bank5data[i])
            for i in range(3):
                v = '0x' + binascii.hexlify(bank5data[6+2*i+1]) + binascii.hexlify(bank5data[6+2*i])
                sensor_data_str += '&adc'+str(i)+'='+v
            v = '0x' + binascii.hexlify(bank5data[13]) + binascii.hexlify(bank5data[12])
            sensor_data_str += '&eventflags='+v
#            print 'sensor_data: ' + sensor_data_str
            try:
                utag = self.addr2stuff[addr]['usertag']
            except:
                utag = ''
            logger.debug('rxevent sensor_data: ' + sensor_data_str + ' utag: ' + utag)
            if addr in self.addr2stuff and utag != '':
                remote_zone = utag in gv.remote_zones
                if remote_zone:
                    e = regs['FF0C']
                    self.pack_setregister(e['reg'], e['bank'], e['span'], 1, addr) # override sleep
                   
                    with self.spi_lock:
                        try:
                            if addr in self.spi_messages:
                                spi_count = len(self.spi_messages[addr]['messages'])
                                if spi_count != 0:
                                    logger.warning('remaining spi messages from previous RxEvent: ' + str(spi_count))
                                    del self.spi_messages[addr]
                                time.sleep(.1) # give some threads time to quit....not guaranteed
                        except:
                            pass
                        self.spi_messages[addr] = {}
                        self.spi_messages[addr]['messages'] = []
                        self.spi_messages[addr]['spi_count'] = 0

                urlcmd = 'http://localhost'
                if gv.sd['htp'] != 0 and gv.sd['htp'] != 80:
                    urlcmd += ':'+str(gv.sd['htp'])
                urlcmd += '/surrsd?name=' + urllib.quote_plus(utag) + sensor_data_str
                try:
                    rz = urllib2.urlopen(urlcmd, timeout=1)
                    gv.remote_zones = json.load(rz) # update remote zone names
                except Exception as ex:
                    logger.error('RxEvent: got urlcmd exception: ' + str(ex) + ' urlcmd: ' + urlcmd)

                if remote_zone:
                    extra_spi = 0
                    for i in range(extra_spi):
                        shift_amt = i % 8
                        spi_cmd = pack_gateway_command('g', 0, 0x0, SCRATCH, 1, int_to_hex(1<<shift_amt), 0)
                        self.spi_messages[addr]['messages'].append(spi_cmd)
                        self.spi_messages[addr]['spi_count'] += 1
                        spi_cmd = pack_gateway_command('g', 0, 0x0, SCRATCH, 1, '', 0)
                        self.spi_messages[addr]['messages'].append(spi_cmd)
                        self.spi_messages[addr]['spi_count'] += 1
                
                    # get the zones for remote 'utag'
                    urlcmd = 'http://localhost'
                    if gv.sd['htp'] != 0 and gv.sd['htp'] != 80:
                        urlcmd += ':'+str(gv.sd['htp'])
                    urlcmd += '/surzd?name=' + urllib.quote_plus(utag)
                    try:
                        data = urllib2.urlopen(urlcmd, timeout=gv.url_timeout+2)
                        datao = json.load(data)
                        zone_mask = datao['zones']
                        logger.debug('rxevent zone_mask: ' + hex(zone_mask))
                        with self.spi_lock:
                            try:
                                if zone_mask != -1:
                                    # override sleep done in rxdata using pstate
                                    spi_cmd = pack_gateway_command('g', 1, 0x0, ZONE_STATE, 0, int_to_hex(zone_mask), 0)
                                    if extra_spi > 0:
                                        self.spi_messages[addr]['messages'].append(spi_cmd)
                                        self.spi_messages[addr]['spi_count'] += 1
                                        start_thread('spi_msg', self.process_spi_messages, addr)
                                    else:
                                        # todo if enabled check spi_count error messages and disable
                                        logger.debug('zone spi message for addr: ' + addr)
                                        self.spi_messages[addr]['spi_count'] += 1
                                        self.transmit_msg(addr, spi_cmd, True)
                                else:
                                    e = regs['FF0C']
                                    self.pack_setregister(e['reg'], e['bank'], e['span'], 2, addr) # cancel override sleep
                            except:
                                pass
                    except Exception as ex:
                        e = regs['FF0C']
                        self.pack_setregister(e['reg'], e['bank'], e['span'], 2, addr) # cancel override sleep
                        logger.error('RxEvent: got zone urlcmd exception: ' + str(ex) + ' urlcmd: ' + urlcmd)
            else:
                logger.info('RxEvent with unmapped addr: ' + addr + ' Get mac.')
                # do necessarily initialization in getremotereg
		e = regs['0200']
		self.pack_getregister(e['reg'],e['bank'],e['span'],addr)
		print 'setting last_discover(RxEvent) to addr:' + addr
		last_discover=addr
            logger.debug('RxEvent')
            return 21
        elif cmd_type == 'RxData': # byte after rssi indicates length of this message broken into chunksize byte chunks
#            print 'enter RxData', len(data), binascii.hexlify(data)
            if len(data) < 5:
                return -1
#            logger.debug('RxData data: ' + binascii.hexlify(data))
            d = struct.unpack('B B B B B', data[:5])
            addr = (binascii.hexlify(data[2]) + binascii.hexlify(data[1]) + binascii.hexlify(data[0])).upper()
            rssi = data[3]
            rssi_str = self.rssi2str(d[3])
            remaining = d[4]
            logger.debug('rxdata for addr: 0x' + addr + ' remaining: ' + hex(remaining))
            is_spi_msg = bool(remaining == SPI_MAGIC)
            if is_spi_msg:
                logger.debug('got spi loopback spilen: ' + str(len(data)-4)) # dont count addr and rssi
                remaining = 31 # 32B message but SPI_MAGIC is also "remaining"
            if remaining > self.chunksize:
                logger.critical('RxData with bad remaining: ' + str(remaining) + ' addr: ' + addr)
            if remaining+5 > len(data): # all the data here?
                return -1
            if not self.initial_configuration and addr not in self.addr2stuff:
                logger.warning('created unannounced addr2stuff entry for addr: ' + addr)
                self.addr2stuff[addr] = {'mac':'unknown', 'msg':'', 'responses':{}}
            adjust = -1 if is_spi_msg else 0
            remaining += 1 if is_spi_msg else 0 # get back to 32B content and include SPI_MAGIC
            if remaining > 0:
                rxmsg = struct.unpack(str(remaining)+'s',data[5+adjust:5+adjust+remaining])[0]
#                logger.debug('adding to message for addr: ' + addr + ' msg: ' + binascii.hexlify(rxmsg))
                if not self.initial_configuration:
                    self.addr2stuff[addr]['msg'] += rxmsg
#            logger.debug('current remaining: ' + str(remaining) + ' msg: ' + binascii.hexlify(self.addr2stuff[addr]['msg']))
            if remaining < self.chunksize:
#                logger.debug('process message for addr ' + addr + ' msg: ' + self.addr2stuff[addr]['msg'])
                try:
                    msg = self.addr2stuff[addr]['msg']
                except:
                    msg = '' # probably a remote sending 0 len msg and 'msg' field never got initialized
                if not self.initial_configuration:
                    self.addr2stuff[addr]['msg'] = ''
                    if len(msg) == 0:
                        logger.error('RxData unexpected 0 length message from addr: ' + addr)
                        return 5+remaining+adjust

                    d = struct.unpack('< B', msg[0:1])
                    request = d[0]
                    if is_spi_msg: # SPI message
                        with self.spi_lock:
                            try:
                                self.spi_messages[addr]['spi_count'] -= 1
                            except:
                                pass

                        out = format_gateway_response(msg)
                        logger.debug(out)
                        pstate = struct.unpack('B', msg[6])[0]
                        if (pstate & 1) == 1:
                            e = regs['FF0C']
                            self.pack_setregister(e['reg'], e['bank'], e['span'], 2, addr) # cancel sleep override
                    elif request == ord('X'):
                        msg = msg[1:]
                        logger.debug('rxdata X msg: ' + msg)
                    elif len(msg) < 8:
                        logger.warning('message too short for req,mac,msgid msg: ' + binascii.hexlify(msg))
                    else:
                        d = struct.unpack('< B B B B I', msg[0:8])
                        mac = (binascii.hexlify(msg[3]) + binascii.hexlify(msg[2]) + binascii.hexlify(msg[1])).upper()
                        msgid = d[4]
                        if request <= 3: #request or response or ip
                            msg = msg[8:]
                        else:
                            logger.warning('rxdata unknown message type: ' + msg)
                        small_msg = msg if len(msg) < 100 else msg[:100]
                        if request == 1:
                            try:
                                logger.debug('RxData request complete: ' + small_msg)
                                data = urllib2.urlopen(msg, timeout=gv.url_timeout+2)
                                datao = json.load(data)
                                ret_str = json.dumps(datao)
                            except Exception as ex:
                                logger.debug('rxdata request: No response from slave: ' + addr + ' Exception: ' + str(ex))
                                ret_str = json.dumps({'unreachable':1})
                            frommacraw = [int(mac[0:2],16),int(mac[2:4],16),int(mac[4:],16)]
                            txmsg = self.build_transmit_msg(0, frommacraw[2], frommacraw[1], frommacraw[0], d[4], ret_str)
                            try:
                                addr = self.mac2addr[mac]['addr']
                                self.transmit_msg(addr, txmsg)
                            except:
                                logger.info('rxdata request: No mac 2 addr mapping mac: ' + mac + ' dropping msg: ' + binascii.hexlify(txmsg))
                        elif request == 0: #response
                            try:
                                logger.debug('RxData response complete: ' + small_msg)
                                addr = self.mac2addr[mac]['addr']
                                responses = self.addr2stuff[addr]['responses']
                                with self.response_lock:
                                    if str(msgid) in responses:
                                        responses[str(msgid)]['response'] = msg
                                        responses[str(msgid)]['response_present'] = 1
                                    else:
                                        pass
                                        logger.debug('rxdata response: missing response holder for message mac: ' + mac + ' msgid: ' + str(msgid) + ' dropping response msg: ' + msg)
                            except:
                                logger.info('rxdata response: No mac 2 addr or responses configured mac: ' + mac + ' dropping response msg: ' + msg)
                        elif request == 2: #basic ip
                            try:
                                logger.debug('RxData ip complete: ' + binascii.hexlify(small_msg))
                                os.write(self.substation_proxy.tun_manager.tun_file['radio'], msg)
                                if self.substation_proxy.tun_manager.log_tun:
                                    msg_len = len(msg)
                                    logger.info('rxdata write tun len: ' + str(msg_len) + ' from: ' + binascii.hexlify(msg[16:20]) + ' to: ' + binascii.hexlify(msg[20:24]) + ' data: ' + binascii.hexlify(msg[0:min(msg_len,dmsg_len)]))
                            except Exception as ex:
                                logger.info('rxdata response: could not write tunfile mac: ' + mac + ' ex: ' + str(ex) + ' dropping response msg: ' + binascii.hexlify(msg))
                        elif request == 3: # receiving radio_prefix
                            logger.debug('RxData network prefix received: ' + msg)
                            if msg != self.substation_proxy.radio_interface.network_prefix:
                                radio_ip = self.compute_radio_ip()
                                if '.255.255' not in radio_ip:  # if radio not yet set up, wait for next update
                                    logger.info('RxData network prefix changed to: ' + msg)
                                    self.network_prefix = msg
                                    # update the run_reader whenever the nwkid may have changed.
                                    tm = self.substation_proxy.tun_manager
                                    tm.init_radio_tun()
                                    tm.tun_radio_start_in_progress = False
                                else:
                                    logger.info('RxData network prefix not ready to be updated')
#            logger.debug('RxData addr: ' + addr + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
            return 5+remaining+adjust
        else:
            logger.critical('Unexpected command type: ' + cmd_type)
        return -1

    # Every command starts with 0xfb and follows with a byte length.  Following the length, there is a variable
    # amount of data associated with the command.  If we do not understand the command we discard remaining data.
    # Commands 0x26 (RxData),0x27(Announce),0x28(RxEvent), and 0x2c(JoinRequest) appear asynchronously.  Replies have a 0x1 in the upper
    # nibble with the command found in the lower nibble.
    def reader(self):
        """loop forever"""

        remaining_bytes = -1
        found_fb = False
        cmd_type = 'None'
        data = ''
        while self.alive:
            try:
#                logger.info('blocking read')
                old_data_len = len(data)
                data += self.serialport.read(1)             # read one, blocking
                n = self.serialport.inWaiting()             # look if there is more
                if n:
                    data += self.serialport.read(n)   # and get as much as possible
#                if len(data) > old_data_len:
#                    logger.info('read ' + str(1+n) + ' bytes from serial port.  Unprocessed aggregate data len: ' + str(len(data)) + ' 0x' + binascii.hexlify(data[0:36]))
#                else:
#                    logger.info('no serial data')
#                print 'data chars read: ' + str(n+1) + ' data: ' + binascii.hexlify(data)
#            except serial.SerialException:
            except: # failure to open serial port also should exit
                logger.exception('serial read failure: ')
                self.alive = False
                time.sleep(10)
                continue

            try:
                used = 0
                while used >= 0 and self.alive: # as long as we have full commands to processes
                    used = -1 # make it look like need more serial data unless we process a full command
                    for dc in data:
                        if cmd_type != 'None':
                            break
                        d = struct.unpack('B', dc)[0]
                        data = data[1:] # remove dc from data
                        if not found_fb:
                            if d == 0xfb:
                                found_fb = True
                                remaining_bytes = -1
                                cmd_type = 'None'
                            else:
#                                print 'discarding char: ' + str(d)
                                logger.warning('discarding char: ' + str(d))
                        elif remaining_bytes == -1:
                            remaining_bytes = d
                        elif cmd_type == 'None':
                            cmd_type = self.check_command(d)
                            remaining_bytes -= 1

                    avail = min(len(data), remaining_bytes)
                    if found_fb and remaining_bytes >= 0 and avail >= remaining_bytes: # deal with situation where no data associated with cmd
                        if cmd_type == 'Flush Remaining':
                            logger.warning('Flush remaining len: ' + str(remaining_bytes))
#                            print 'Flush remaining len: ' + str(remaining_bytes)
                            used = remaining_bytes
                        else:
#                            print 'try processed cmd: ' + cmd_type + ' remaining bytes: ' + str(remaining_bytes) + ' data: ' + binascii.hexlify(data)
                            used = self.process_command(cmd_type, data)

                    if used >= 0:
                        data = data[used:]
#                        print 'fb processed cmd: ' + cmd_type + ' used: ' + str(used) + ' remaining data: ' + binascii.hexlify(data)
#                        logger.info('fbprocessed ' + cmd_type + ' used: ' + str(used) + ' data: 0x' + binascii.hexlify(data))
                        found_fb = False
                        remaining_bytes = -1
                        cmd_type = 'None'

            except:
                logger.exception('sip_radio reader: ')


    def pack_setregister(self, reg, bank, span, val, remote_addr='', execute=True):
#        print 'psr: ', reg, bank, span
        if span == 1:
            vals = 'B'
        elif span == 2:
            vals = 'H'
        elif span == 4:
            vals = 'I'
        else:
            vals = str(span)+'s'
        if remote_addr == '':
            values = (0xfb, 4+span, 0x04, reg, bank, span, val)
            s = struct.Struct('< B B B B B B ' + vals)
        else:
            addrraw = [int(remote_addr[0:2],16),int(remote_addr[2:4],16),int(remote_addr[4:],16)]
            values = (0xfb, 7+span, 0x0B, addrraw[2], addrraw[1], addrraw[0], reg, bank, span, val)
            s = struct.Struct('< B B B B B B B B B ' + vals)
        data = s.pack(*values)
#        print 'cmd: ', binascii.hexlify(data)
        if execute:
            self.enqueue_command(data)
        return data

    def pack_getregister(self, reg, bank, span, remote_addr='', execute=True):
        if remote_addr == '':
            values = (0xfb, 4, 0x03, reg, bank, span)
            s = struct.Struct('< B B B B B B')
        else:
            addrraw = [int(remote_addr[0:2],16),int(remote_addr[2:4],16),int(remote_addr[4:],16)]
            values = (0xfb, 7, 0x0A, addrraw[2], addrraw[1], addrraw[0], reg, bank, span)
            s = struct.Struct('< B B B B B B B B B')
        data = s.pack(*values)
        if execute:
            self.enqueue_command(data)
        return data

    def onetime_configuration(self):
        if self.initial_configuration.upper() == 'UPGRADE': # firmware upgrade and return
            logger.info('onetime_configuration: DeviceMode UPGRADE')
            self.enqueue_command(softwarereset)
            return

        self.pack_setregister(0xff, 0xff, 1, 0) # restore factory defaults
        time.sleep(1)
        self.enqueue_command(baud115200)
        self.enqueue_command(dntcfg)
        if self.initial_configuration.upper() == 'BASE':
            logger.info('onetime_configuration: DeviceMode BASE')
            self.pack_setregister(0x0, 0x0, 1, 1)
            # only the base has bank1 registers set.
            self.pack_setregister(0x1, 0x1, 1, 1) # set CSMA
            self.pack_setregister(0x3, 0x1, 1, 0) # LeasePeriod must be disabled in sleep mode (other 5 is fine)
            slot_size = 202  # MUST be low enough to never allow txdata message len to be SPI_MAGIC and look like spi message
#            slot_size = 213 # 214 seems to result in baseslotsize==50
            self.pack_setregister(0x2, 0x1, 1, slot_size) # set baseslotsize
            self.pack_setregister(0xb, 0x1, 1, slot_size) # set csma_remtslotsize same as baseslotsize until we separate chunksizes
#            self.pack_setregister(0x8, 0x2, 1, slot_size) # set remotelotsize
        elif self.initial_configuration.upper() == 'REMOTE':
            logger.info('onetime_configuration: DeviceMode REMOTE')
            self.pack_setregister(0x0, 0x0, 1, 0)
        elif self.initial_configuration.upper() == 'ROUTER':
            logger.info('onetime_configuration: DeviceMode ROUTER')
            self.pack_setregister(0x0, 0x0, 1, 0)
            self.pack_setregister(0x35, 0x0, 1, self.radio_routing) # set BaseModeNetID when in REMOTE mode
            time.sleep(3)
            self.pack_setregister(0x0, 0x0, 1, 3) # then set router mode
        else:
            logger.info('onetime_configuration: DeviceMode unchanged')
        self.pack_setregister(0x1, 0x0, 1, 1) # set datarate=0=>500kb/s, 1=>200kb 2=>115.2, 3=>38.4
        # setting hop duration to 20ms resulted in small RemoteSlotSize.  Might need to bump further for distance
        self.pack_setregister(0x2, 0x0, 2, 0x220) # set hopduration=27ms per Ryan (slotsize/hopduration relates to datarate)
        self.pack_setregister(0x0, 0x6, 1, 0x38) # set GPIO_Dir to gpio3,4,5 output
        self.pack_setregister(0x1, 0x6, 1, 0x18) # set GPIO_Init to gpio3,4 high others low
        self.pack_setregister(0x5, 0x0, 16, self.key) # set security key
        self.pack_setregister(0x1c, 0x0, 16, self.radio_name)
        if self.radio_name != '\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0':
            if self.radio_routing == 255: # cant sleep if routing
                logger.info('onetime_configuration: setting sleepmde and wakeresponsetime')
                self.pack_setregister(0x15, 0x0, 1, 2) # set SleepMode on after reset
                self.pack_setregister(0x16, 0x0, 1, 90) # WakeResponseTime 900msec (750ms sometimes missed)
                self.pack_setregister(0x4, 0x6, 1, 0x38) # set GPIO_SleepMode to respect sleepstates gpio3,4,5
                self.pack_setregister(0x5, 0x6, 1, 0x38) # set GPIO_SleepDir to gpio3,4,5 output
                self.pack_setregister(0x6, 0x6, 1, 0xc8) # set GPIO_SleepState to ignore dtd, host_cts, gpio3 high; gpio4,5 low

            logger.info('onetime_configuration: setting sampleintvl, reportinterval and reportrigger')
            self.pack_setregister(0x37, 0x0, 2, 0) # disable heartbeat
            self.pack_setregister(0x19, 0x6, 1, 0x10) # set IO_ReportTrigger to interval timer
            self.pack_setregister(0x1A, 0x6, 4, 60*100) # set IO_ReportInterval to 1 mins (and sample adc)
            self.pack_setregister(0x4, 0x3, 1, 2) # set SPI_mode to master
            self.pack_setregister(0x5, 0x3, 1, 10) # set SPI_Divisor at 80.6kb  !!!! Cannot be set less than 10
            self.pack_setregister(0x6, 0x3, 1, 2) # set SPI_Options: CPHA = 1 Clock idle High
            spi_root_cmd = 'SC0123456789abcdef9012345678901'
            spi_root_cmd = '' # dont have a standard spi command for now
            if spi_root_cmd != '':
                remainder_cmd = ''
                for i in range(31-len(spi_root_cmd)):
                    remainder_cmd += '\0'
                values = (len(spi_root_cmd), spi_root_cmd, remainder_cmd)
                s = struct.Struct('< B '+str(len(spi_root_cmd))+'s '+str(len(remainder_cmd))+'s')
                spi_cmd = s.pack(*values)
                self.pack_setregister(0x8, 0x3, 32, spi_cmd) # SPI_MasterCmdStr
                self.pack_setregister(0x7, 0x3, 1, len(spi_root_cmd)+1) # SPI_MasterCmdStr cmd + 1byte at beginning for len
        else: # no heartbeat on sleeping remotes
            self.pack_setregister(0x37, 0x0, 2, 60) # set heartbeat interval 60s
        if self.power != -1:
            self.pack_setregister(0x18, 0x0, 1, self.power) # set power level
        else:
            logger.info('onetime_configuration: power level unchanged')
        self.pack_setregister(0x34, 0x0, 1, 1) # set TreeRoutingEn register 1
        self.pack_setregister(0x3a, 0x0, 1, 1) # set enableRtAcks 1
        self.pack_setregister(0x0, 0x4, 1, 1) # set ProtocolMode
        # sleeping remotes should ignore host_rts
        if self.radio_name != '\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0' and self.radio_routing == 255:
            self.pack_setregister(0x3, 0x3, 1, 5) # set SerialControls Base DCD and Sleep/DTR enable

    def onetime_configuration_save_and_reset(self):
        self.pack_setregister(0xff, 0xff, 1, 2) # MemorySave and reset

    def writer(self):

        self.enqueue_command(dntcfg)

        if self.initial_configuration:
            self.onetime_configuration()

        ping_buffer = ''
        if ping:
            self.pack_setregister(0x18, 0x0, 1, self.power) # set power level temporarily
            e = regs['0018']
            self.pack_getregister(e['reg'], e['bank'], e['span'])
            with open(file_name, 'r') as f:
                ping_buffer = f.read()
            self.transmit_msg('FF0000', '') # prime the xmit pump
        elif quick_test:
            e = regs['0200']
            self.pack_getregister(e['reg'], e['bank'], e['span'])
        else:
            for k, e in regs.iteritems():
                self.pack_getregister(e['reg'], e['bank'], e['span'])

        time.sleep(1)
        last_network_prefix_update_time = 0
        last_iface_update_time = 0
        last_radio_sensor_read_time = 0
        save_and_reset = False
        while self.alive:
            with self.command_lock:
                if len(self.command_q) > 0: # possibly retry something or write it if never tried
                    try:
                        cq0 = self.command_q[0][1]
                        is_spi_msg = self.command_q[0][2]
                        self.write('writer', cq0, is_spi_msg)
                        self.command_q.pop(0)
                        if save_and_reset and len(self.command_q) == 0:
                            while not self.memory_save_and_reset: # wait until issued then exit
                                time.sleep(1)
                            time.sleep(2)
                            self.memory_save_and_reset = False
                            self.alive = False
                            continue
                        elif ping:
#                            print 'cmd: ' + binascii.hexlify(cq0)[4:6]
                            if binascii.hexlify(cq0)[4:6] == '05' and \
                                   len(binascii.hexlify(cq0))/2 - 7 < self.chunksize: #end of txdata?
                                time.sleep(xmit_delay)
                                if last_discover != '':
                                    print 'transmit -- addr: ' + last_discover + ' buflen: ' + str(len(ping_buffer))
                                    self.transmit_msg(last_discover, ping_buffer)
                                else:
                                    print 'transmit -- but no remote radio yet identified'
                                    self.transmit_msg('FF0000', '') # reprime pump of txdata
                    except serial.SerialTimeoutException:
                        logger.info('Serial Timeout!!!!!')
                    except:
                        logger.exception('Unexpected Serial Write Exception.')

                    # if tun not running, reading these registers will have the side effect
                    # of getting it running
                    tm = self.substation_proxy.tun_manager
                    if tm and 'radio' not in tm.tun_reader_thread and not tm.tun_radio_start_in_progress:
                        tm.tun_radio_start_in_progress = True
                        for r in ['0200', '0203', '0204']:
                            e = regs[r]
                            self.pack_getregister(e['reg'], e['bank'], e['span'])
                elif self.initial_configuration:
                    if self.initial_configuration.upper() != 'UPGRADE':
                        self.initial_configuration = False
                        self.onetime_configuration_save_and_reset()
                        save_and_reset = True
                        continue
                    else: # sent firwmare upgrade command....exit substation_proxy
                        time.sleep(1)
                        self.alive = False

            cur_time = timegm(time.localtime())
            # read local radio sensors.  Also update RemoteSlotSize if needed
            if cur_time - last_radio_sensor_read_time > 60:
                last_radio_sensor_read_time = cur_time
                radio_ip = self.compute_radio_ip()
                for r in ['0208', '0506', '0508', '050A']:
                    e = regs[r]
                    self.pack_getregister(e['reg'], e['bank'], e['span'])

            # update radio_iface and tun_iface for sip.
            if gv.sd['slave'] and cur_time - last_iface_update_time > 90:
                last_iface_update_time = cur_time
                urlcmd = 'http://localhost'
                if gv.sd['htp'] != 0 and gv.sd['htp'] != 80:
                    urlcmd += ':'+str(gv.sd['htp'])
                urlcmd += '/suiface?radio=' + gv.radio_iface + '&vpn=' + gv.vpn_iface
                try:
                    urllib2.urlopen(urlcmd, timeout=gv.url_timeout+2)
                except:
                    print 'failed to update sip iface'
                    pass

            # if we are a base radio, then propagate our network prefix to all other radios that we reach
            if cur_time - last_network_prefix_update_time > 90:
                radio_ip = self.compute_radio_ip()
                if '254.1' not in radio_ip:
                    continue
                # for an unknown reason bind9 sometimes need to be restarted.
                try:
                    subprocess.check_output(['/etc/init.d/bind9', 'status'])
                except Exception as ex:
                    logger.warning('restarting bind9 on base radio')
                    try:
                        subprocess.call(['/etc/init.d/bind9', 'start'])
                    except Exception as ex:
                        logger.critical('could not restart bind9: ' + str(ex))
                last_network_prefix_update_time = cur_time
                for to_mac in self.mac2addr:
                    if to_mac == self.cur_mac:
                        continue
                    try:
                        logger.debug('propagate network_prefix: ' + self.network_prefix)
                        addr = self.mac2addr[to_mac]['addr']
                        if 'usertag' not in self.addr2stuff[addr] or len(self.addr2stuff[addr]['usertag']) == 0:
                            # only propagate to slaves that have a pi attached
                            logger.debug('propagate network_prefix to addr: ' + addr)
                            self.remote_command_response('propagate network_prefix', 3, to_mac, self.cur_mac, self.network_prefix)
                        else:
                            logger.debug('found non-empty usertag')
                    except:
                        pass


def format_gateway_response(data):
    "return deconstructed 32B response from gateway"""

    out = ''
    v = struct.unpack('<B',data[0])[0]
    if v != SPI_MAGIC:
       out += 'Missing '
    out += 'SPI_MAGIC '
    out += 'version: ' + hex(struct.unpack('<B',data[1])[0]) + ' '
    mode = struct.unpack('<B',data[2])[0]
    gateway_map = {}
    gateway_map[0xa5] = {'token':'i2cread',
                           'cmd':['addr','reg','rlen','pstate'],
                           'pad':8,'payload':16,'result':1}
    gateway_map[0xaa] = {'token':'i2cwrite',
                           'cmd':['addr','reg','wlen','pstate'],
                           'pad':24,'payload':0,'result':1}
    gateway_map[0x55] = {'token':'gateway',
                           'cmd':['reg','wlen','rlen','pstate'],
                           'pad':12,'payload':12,'result':1}
    gateway_map[0xbb] = {'token':'bootloader',
                           'cmd':['cmd','wlen','rlen','pstate'],
                           'pad':8,'payload':16,'result':1}
    try:
        desc = gateway_map[mode]
        out += 'token: ' + desc['token'] + ' '
        for i,name in enumerate(desc['cmd']):
            out += name + ': ' + hex(struct.unpack('<B',data[3+i])[0]) + ' '
        if desc['payload'] > 0 and 'rlen' in desc['cmd']:
            payload_base = 7+desc['pad']
            payload_len = struct.unpack('<B',data[3+2])[0]
            out += 'payload: 0x' + binascii.hexlify(data[payload_base:payload_base+payload_len]) + ' '
        res = struct.unpack('<B',data[31])[0]
        out += 'result: '
        out += 'pass ' if (res&1)==0 else 'fail  '
        out += 'i2c_no_response ' if (res&2)==2 else ''
        out += 'i2c_timeout ' if (res&4)==4 else ''
        out += 'zones: '
        out += 'on ' if (res&16)==16 else 'off '
        out += 'on ' if (res&32)==32 else 'off '
        out += 'on ' if (res&64)==64 else 'off '
    except:
        out += 'Bad command: ' + hex(mode) + ' '
        out += 'data: 0x' + binascii.hexlify(data)
    return out

def int_to_hex(v):
    """return two character string of hex characters corresponding to value v"""
    return "{0:0{1}x}".format(v,2)

def pack_gateway_command(mode, pstate, address, reg, rlen, payload, bcmd):
    """Based on mode, generate a 32B binary radio command"""

    cmd = ''
    pad = 'ff'
    try: # strip leading 0x if present
        if payload[0:2] == '0x':
            payload = payload[2:]
    except:
        pass
    mode_map = {'r':'a5','w':'aa','g':'55','b':'bb'}
    cmd += mode_map[mode]
    if mode == 'r':
        cmd += int_to_hex(address)
        cmd += int_to_hex(reg)
        cmd += int_to_hex(rlen)
        cmd += int_to_hex(pstate)
        for i in range(27):
            cmd += pad
    elif mode == 'w':
        cmd += int_to_hex(address)
        cmd += int_to_hex(reg)
        cmd += int_to_hex(len(payload)/2)
        cmd += int_to_hex(pstate)
        cmd += payload
        for i in range(16+11-(len(payload)/2)):
            cmd += pad
    elif mode == 'g':
        cmd += int_to_hex(reg)
        cmd += int_to_hex(len(payload)/2)
        cmd += int_to_hex(rlen)
        cmd += int_to_hex(pstate)
        cmd += payload
        for i in range(12+15-(len(payload)/2)):
            cmd += pad
    elif mode == 'b':
        cmd += int_to_hex(bcmd)
        cmd += int_to_hex(len(payload)/2)
        cmd += int_to_hex(rlen)
        cmd += int_to_hex(pstate)
        cmd += payload
        for i in range(16+11-(len(payload)/2)):
            cmd += pad
    else:
        raise ValueError, 'Invalid spi mode'
    return binascii.unhexlify(cmd)

class SubstationProxy:
    def __init__(self):
        self.radio_interface = 0
        self.tun_manager = 0
        self.reader_thread = 0
        self.writer_thread = 0
        self.app_thread = 0

    def ping(self, power):
        try:
            sr = SerialRadio(self, power)
            self.radio_interface = sr
            self.reader_thread = start_thread('reader', sr.reader)
            self.writer_thread = start_thread('writer',sr.writer)
        except:
            logger.exception('ping:')


    def onetime_config(self, type, baud, power, key, radio_name, radio_routing):
        try:
            sr = SerialRadio(self, power, type, baud, key, radio_name, radio_routing)
            self.radio_interface = sr
            self.reader_thread = start_thread('reader', sr.reader)
            self.writer_thread = start_thread('writer',sr.writer)
        except:
            logger.exception('onetime_config:')

    def create_proxy(self):
        try:
            sr = self.radio_interface
            if not sr:
                sr = SerialRadio(self)
                if sr:
                    logger.debug('create_proxy: set radio_interface')
                else:
                    logger.debug('create_proxy: set radio_interface to 0')
                self.radio_interface = sr

            tm = self.tun_manager
            if not tm:
                tm = TunManager(self)
                logger.debug('create_proxy: create tun_manager')
                self.tun_manager = tm

            if sr and sr.serialport:  # only create threads if viable serial interface
                if not self.reader_thread:
                    logger.debug('create_proxy: radio opened')
                    self.reader_thread = start_thread('reader', sr.reader)
                if not self.writer_thread:
                    self.writer_thread = start_thread('writer',sr.writer)
                    logger.debug('create_proxy: writer started')

            if not self.app_thread:
                urls = ('/', 'Proxy', '/supri',  'ProxyIn', '/supro',  'ProxyOut',)
                app = Proxy(urls, globals())
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

                logger.debug('create_proxy: pre render')
                render = web.template.render('templates/', globals=template_globals, base='base')
                self.app_thread = start_thread('app',app.run)
                logger.debug('create_proxy: app started')
        except:
            logger.exception('create_proxy:')

    def stop_proxy(self):
        logger.debug('stop_proxy: start')
        if self.radio_interface:
            try:
                self.radio_interface.stop_tun('radio')
                logger.debug('stop_proxy: stopped tun')
            except:
                logger.exception('stop_proxy: failed to stop tun')
            self.radio_interface.alive = False

        if self.reader_thread:
            try:
                logger.debug('stop_proxy: pre reader join')
                self.reader_thread.join()
                self.reader_thread = 0
                logger.debug('stop_proxy: stopped reader')
            except:
                logger.exception('stop_proxy: failed to stop reader')
        if self.writer_thread:
            try:
                logger.debug('stop_proxy: pre writer join')
                self.writer_thread.join()
                self.writer_thread = 0
                logger.debug('stop_proxy: stopped writer')
            except:
                logger.exception('stop_proxy: failed to stop writer')
        
        # cannot stop app thread so dont recreate if already exists.
        if self.radio_interface:
            if self.radio_interface.serialport:
                logger.debug('stop_proxy: radio_interface close')
                self.radio_interface.serialport.close()
            self.radio_interface = 0
        logger.debug('stop_proxy: finish')

logger = logging.getLogger('substation_proxy')
def usage():
    # substation_proxy --onetime --type=upgrade   # in order to do firmware upgrade using minicom
    print 'substation_proxy [--onetime [--quick|[--ping --file filename --xmit_delay seconds]|--type=upgrade]] [--log_level <debug|info|warning|error|critical>] [--baudrate <9600|115200>] [--power <0..5>] --type <remote|router|base>'
    sys.exit(2)

if __name__ == "__main__":

    log_levels = { 'debug':logging.DEBUG,
        'info':logging.INFO,
        'warning':logging.WARNING,
        'error':logging.ERROR,
         'critical':logging.CRITICAL,
        }

    baud = 115200
    type = ''
    key = ''
    radio_name = ''
    radio_routing = 255
    onetime_config = False
    power = -1

    try:
        opts, args = getopt.getopt(sys.argv[1:],"l:b:t:p:k:r:n:x:f:ogq",["log_level=","baudrate=","onetime","quick","type=","power=","key=","radio_name=","radio_routing=","file=","xmit_delay=","ping"])
    except getopt.GetoptError:
        usage()

    for opt, arg in opts:
        if opt in ("-l","--log_level"):
            if arg in log_levels:
                logger.setLevel(log_levels[arg])
            else:
                print 'only debug,info,warning,error,critical supported as logging levels'
                usage()
        elif opt in ("-b","--baudrate"):
            try:
                baud = int(arg)
            except:
                baud = 0
            if baud != 115200 and baud != 9600:
                print 'only 115200 and 9600 supported as baud rates'
                usage()
        elif opt in ("-k","--key"):
            key = arg
        elif opt in ("-r","--radio_name"):
            radio_name = arg
        elif opt in ("-n","--radio_routing"):
            try:
                radio_routing = int(arg)
                if (radio_routing < 1 or radio_routing > 63) and radio_routing != 255:
                    print 'radio_routing 1..63 or 255.'
                    usage()
            except:
                print 'radio_routing 1..63 or 255.'
                usage()
        elif opt in ("-p","--power"):
            try:
                power = int(arg)
            except:
                power = -1
            if power < 0 or power > 5:
                print 'only power levels 0..5 (1mW,10mW,63mW,.25W,.5W,1W).  Top 3 are limited bandwidth.'
                usage()
        elif opt in ("-x","--xmit_delay"):
            try:
                xmit_delay = int(arg)
            except:
                xmit_delay = -1
            if xmit_delay < 0:
                print 'only non-negative seconds of delay between transmissions.'
                usage()
        elif opt in ("-o","--onetime"):
            onetime_config = True
        elif opt in ("-g","--ping"):
            ping = True
            if quick_test:
                print 'Cannot have ping and quick together.'
                usage()
        elif opt in ("-q","--quick"):
            quick_test = True
            if ping:
                print 'Cannot have ping and quick together.'
                usage()
        elif opt in ("-t","--type"):
            type = arg.upper()
            if type != 'BASE' and type != 'REMOTE' and type != 'ROUTER' and type != 'UPGRADE':
                print 'only base,remote,router, and upgrade supported as types'
                usage()
        elif opt in ("-f","--file"):
            file_name = arg

    log_file = 'logs/irricloud.out' if not quick_test else 'logs/qt.out'
    fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5*gv.MB, backupCount=5)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)

    usb_reset('radio')
    substation_proxy = SubstationProxy()
    if ping or quick_test or onetime_config:
        logger.critical('Radio onetime')
        if onetime_config or quick_test:
            substation_proxy.onetime_config(type, baud, power, key, radio_name, radio_routing)
        elif ping:
            substation_proxy.ping(power)
        print 'wait for writer'
        while substation_proxy.writer_thread.isAlive():
            time.sleep(1)
        substation_proxy.writer_thread.join()
        print 'wait for reader'
        while substation_proxy.reader_thread.isAlive():
            time.sleep(1)
        substation_proxy.reader_thread.join()
        os._exit(0) # sip_monitor to restart
    else:
        logger.critical('Starting radio interface')
        while True:
            substation_proxy.create_proxy() # webserver and radio thread startup
            update_radio_present()
            if gv.sd['radio_present'] and substation_proxy.radio_interface and \
               substation_proxy.radio_interface.serialport and substation_proxy.radio_interface.alive:
                time.sleep(10)
            elif not gv.sd['radio_present'] and (substation_proxy.radio_interface == 0 or substation_proxy.radio_interface.serialport == 0):
                time.sleep(10)
            else:
                break
        logger.critical('exiting substation_proxy.  radio_present: '+str(gv.sd['radio_present']))
        os._exit(0) # sip_monitor to restart
