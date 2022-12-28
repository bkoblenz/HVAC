# !/usr/bin/env python
# -*- coding: utf-8 -*-

import web
import json
import ast
import i18n
import gv
from helpers import get_ip, get_cpu_temp, usb_reset, validate_fqdn
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

class SubstationProxy:
    def __init__(self):
        self.radio_interface = 0
        self.tun_manager = 0
        self.reader_thread = 0
        self.writer_thread = 0
        self.app_thread = 0

    def create_proxy(self):
        try:
            tm = self.tun_manager
            if not tm:
                tm = TunManager(self)
                logger.debug('create_proxy: create tun_manager')
                self.tun_manager = tm

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
            if gv.sd['radio_present'] and substation_proxy.radio_interface and \
               substation_proxy.radio_interface.serialport and substation_proxy.radio_interface.alive:
                time.sleep(10)
            elif not gv.sd['radio_present'] and (substation_proxy.radio_interface == 0 or substation_proxy.radio_interface.serialport == 0):
                time.sleep(10)
            else:
                break
        logger.critical('exiting substation_proxy.  radio_present: '+str(gv.sd['radio_present']))
        os._exit(0) # sip_monitor to restart
