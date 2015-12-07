# !/usr/bin/env python
# -*- coding: utf-8 -*-

import web
import json
import ast
import i18n
import gv
from helpers import jsave, get_ip, get_cpu_temp
from webpages import WebPage
import sys
import getopt
import logging
import urllib
import urllib2

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

#todo fix email config under options

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
        timeout_adder = 4 if urlcmd.find('update_status') != -1 else 0 # boost for system update code
        try:
#            self.logger.debug('proxy_in: attempt urlcmd: ' + urlcmd)
            datas = urllib2.urlopen(urlcmd, timeout=1+2*hops+timeout_adder)
#            self.logger.debug('proxy_in: urlcmd sucess')
            data = json.load(datas)
            ret_str = json.dumps(data)
        except Exception as ex:
            self.logger.info('proxy_in: No response from slave: ' + addr + ' urlcmd: ' + urlcmd + ' ex: ' + str(ex))

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
            if cmd != 'suslj':
                raise web.unauthorized()
        except:
            raise web.unauthorized()

        ret_str = json.dumps({'unreachable':1})
        paramo['port'] = 9080
        paramo['ip'] = get_ip()
        paramo['proxy'] = slave_addr if slave_proxy == '' else slave_addr + ';' + slave_proxy
#        self.logger.debug('proxy_out: ip: ' + paramo['ip'] + ' port: ' + str(paramo['port']) + ' proxy: ' + paramo['proxy'])
        try:
            if gv.sd['master_ip'] != '':
                urlcmd = 'http://' + gv.sd['master_ip']
                if gv.sd['master_port'] != 0 and gv.sd['master_port'] != 80:
                    urlcmd += ':' + str(gv.sd['master_port'])
                urlcmd += '/suslj?data=' + urllib.quote_plus(json.dumps(paramo))
            elif gv.sd['gateway_ip'] != '':
                urlcmd = 'http://' + gv.sd['gateway_ip']
                urlcmd += ':9080'
                urlcmd += '/supro?command=suslj&parameters=' + urllib.quote_plus(json.dumps(paramo))
            else:
                self.logger.critical('No master or gateway for proxy')
#            self.logger.debug('proxy_out: attempt urlcmd: ' + urlcmd)
            datas = urllib2.urlopen(urlcmd, timeout=5)
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
regs = {
    '0000': {'name':'DeviceMode','reg':0x0,'bank':0x0,'span':1,'val':'0'},
    '0001': {'name':'RF_DataRate','reg':0x1,'bank':0x0,'span':1,'val':'0'},
    '0018': {'name':'TxPower','reg':0x18,'bank':0x0,'span':1,'val':'0'},
    '0034': {'name':'TreeRoutingEn','reg':0x34,'bank':0x0,'span':1,'val':'0'},
    '0035': {'name':'BaseModeNetID','reg':0x35,'bank':0x0,'span':1,'val':'0'},
    '0037': {'name':'HeartbeatIntrvl','reg':0x37,'bank':0x0,'span':2,'val':'0'},
    '003A': {'name':'EnableRtAcks','reg':0x3a,'bank':0x0,'span':1,'val':'0'},
    '0101': {'name':'AccessMode','reg':0x1,'bank':0x1,'span':1,'val':'0'},
    '0102': {'name':'BaseSlotSize','reg':0x2,'bank':0x1,'span':1,'val':'0'},
    '0107': {'name':'CSMA_Predelay','reg':0x7,'bank':0x1,'span':1,'val':'0'},
    '0108': {'name':'CSMA_Backoff','reg':0x8,'bank':0x1,'span':1,'val':'0'},
    '010B': {'name':'CSMA_RemtSlotSize','reg':0xB,'bank':0x1,'span':1,'val':'0'},
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
    '0400': {'name':'ProtocolMode','reg':0x0,'bank':0x4,'span':1,'val':'0'},
    '0506': {'name':'ADC0','reg':0x6,'bank':0x5,'span':2,'val':'0'},
    '0508': {'name':'ADC1','reg':0x8,'bank':0x5,'span':2,'val':'0'},
    '050A': {'name':'ADC2','reg':0xa,'bank':0x5,'span':2,'val':'0'},
    '0606': {'name':'GPIO_SleepState','reg':0x6,'bank':0x6,'span':1,'val':'0'},
    '0800': {'name':'BaseNetworkId','reg':0x0,'bank':0x8,'span':1,'val':'0'}
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

TUNSETIFF = 0x400454ca
IFF_TUN   = 0x0001
IFF_TAP   = 0x0002
TUNMODE = IFF_TUN

chunksize = 100
msg24 = 'Xabcdefghijklmnopqrstuvwx' # start with X to separate from commands
msg249 = ''
for i in range(10):
    msg249 += msg24
msg249 += '012345678'
msg249 = msg249[:chunksize]

def open_serial(baud):
    ser = serial.Serial()
    ser.port     = '/dev/dnt900'
    ser.baudrate = baud
    ser.parity   = 'N'
    ser.rtscts   = True
    ser.dsrdtr   = True
    ser.timeout  = 3     # required so that the reader thread can exit
    ser.write_timeout  = 1     # required so that the writer can release queue lock

    try:
        ser.open()
    except serial.SerialException, e:
        logger.exception("Could not open serial port %s: %s\n" % (ser.portstr, e))
        return 0

    logger.info('serial ' + ser.name + ' open.  Baud: ' + str(ser.baudrate))
    return ser

class SerialRadio:
    def __init__(self, proxy, initial_config='', baud=115200, power=1, key=''):
        self.substation_proxy = proxy
        self.serial = open_serial(baud)
        self.initial_configuration = initial_config
        self.power = power
        self.key = key
        self.command_q = []
        self.mac2addr = {}
        self.addr2stuff = {}
        self.alive = True
        self.tun_thread = 0
        self.tun_start_in_progress = False
        self.cur_nwkid = 0
        self.cur_nwkaddr = 0
        self.cur_mac = '000000'
        self.command_lock = threading.RLock()
        self.msg_count = 0
        self.response_lock = threading.RLock()

    def compute_radio_ip(self):
        if self.cur_nwkid == 0 and self.cur_nwkaddr  == 0:
            router = 254
            entry = 1
        else:
            router = self.cur_nwkid
            entry = self.cur_nwkaddr
        return '10.1.' + str(router) +'.' + str(entry)

    def stop_tun(self):
        # kill off existing thread if necessary
        tun_reader_thread = self.tun_thread
        if self.tun_thread:
            self.tun_thread = 0
            # force thread that may be blocking on read to get something
            rc = subprocess.call(['ping', '-I', 'dnttun0', '8.8.8.8', '-c', '1'])
            tun_reader_thread.join()

    def init_tun(self):
        self.stop_tun()

        # set up tun interface
        f = os.open("/dev/net/tun", os.O_RDWR)
        self.tun_file = f
        ifs = ioctl(f, TUNSETIFF, struct.pack("16sH", "dnttun%d", TUNMODE))
        ifname = ifs[:16].strip("\x00")
        addr = 'FF' + binascii.hexlify(struct.pack('B',self.cur_nwkid)[0]) + \
                      binascii.hexlify(struct.pack('B',self.cur_nwkaddr)[0])
        self.mac2addr[self.cur_mac] = {'addr':addr}
        self.addr2stuff[addr] = {'mac':self.cur_mac, 'msg':'', 'responses':{}}
        radio_ip = self.compute_radio_ip()
        # Cannot have router numbered 254 so treat this as the base
        rc = subprocess.call(['ifconfig', ifname, radio_ip+'/16'])
        if rc:
            logger.info('ifconfig ' + ifname + 'failed rc: ' + str(rc))
        else:
            self.tun_thread = self.start_thread('tun_reader', self.tun_reader)
        self.tun_start_in_progress = False

    def enqueue_command(self,fullcmd):
        with self.command_lock:
            cmds = struct.unpack('B B B', fullcmd[:3])
            self.command_q.append([cmds[2], fullcmd, 0])

    def write(self, r, d, serial_write=False):
        if serial_write:
            self.serial.write(d)   # may raise timeout if write fails
#        if serial_write and (d == baud9600 or d == baud115200 or d == factorydefaults):
        if serial_write:
            if (d == baud9600 or d == baud115200):
                self.serial.flush() # ensure write is done
                rate = 115200 if d == baud115200 else 9600
                self.serial.baudrate = rate
                logger.info('setregister baudrate update: ' + str(rate))

    def start_thread(self, name, func):
        thread = threading.Thread(target=func)
        thread.setDaemon(True)
        thread.setName(name)
        thread.start()
        return thread       

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


    def transmit_msg(self, addr, msg):
        """ Send a message broken into chunksize portions. """

        msglen = len(msg)
        written = 0
#        logger.debug('xmit msg len: ' + str(msglen) + ' addr: ' + addr)
        while True:
            chunk = min(chunksize, msglen-written)
            data = struct.pack('< B B B B B B B', 0xfb, 5+chunk, 0x05, int(addr[4:],16), int(addr[2:4],16), int(addr[0:2],16), chunk)
            for i in range(written,written+chunk):
                data += struct.pack('B', struct.unpack('B', msg[i:i+1])[0])
            self.enqueue_command(data)
            written += chunk
            if chunk < chunksize:
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
            if type != 2:
                msgid = self.get_response_holder(tomac)
            else:
                msgid = 0xdeadbeef
            txmsg = self.build_transmit_msg(type, frommacraw[2], frommacraw[1], frommacraw[0], msgid, msg)
#            logger.debug('about to xmit msgid: ' + str(msgid))
            self.transmit_msg(addr, txmsg)
            progress += 1
            if type != 2:
                tries = 0
                while tries < 10:
                    with self.response_lock:
                        if self.addr2stuff[addr]['responses'][str(msgid)]['response_present']:
                            ret_str = self.addr2stuff[addr]['responses'][str(msgid)]['response']
#                            logger.debug('got xmit response: ' + str(msgid))
                            break
                    time.sleep(.5)
                    tries += 1
                if tries > 4:
                    logger.info('stopped looking for response addr: ' + addr + ' msgid: ' + str(msgid) + ' tries: ' + str(tries))
                progress += 1
                try:
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
            logger.info('discarding cmd0: ' + str(c))
        return cmd_type

    def rssi2str(self, rssi):
        if rssi == 0x7f:
            return ' No RSSI (no ack)'
        elif rssi == 0x7e:
            return ' No RSSI (routed pkt)'
        return ''

    def process_command(self, cmd_type, data):
        """Process cmd_type assuming enough data in data.  Return the amount of data that was consumed."""

        global msg249

        if cmd_type == 'None' or cmd_type == 'Flush Remaining':
            return 0
        elif cmd_type[0] == '1':
            if cmd_type[1] == '0': # enter protocol mode
                logger.debug('enterprotocolmode')
                return 0
            elif cmd_type[1] == '3': # get register
                if len(data) < 4:
                    return 0
                d = struct.unpack('B B B', data[:3])
                reg = data[0]
                bank = data[1]
                span = data[2]
                if len(data) < 3+d[2]:
                    return 0
                val = data[3:3+d[2]]
                bankreg = (binascii.hexlify(bank)+binascii.hexlify(reg)).upper()
                regname = 'Unknown' if bankreg not in regs else regs[bankreg]['name']
                logger.info('getregister ' + regname + ' val: ' + binascii.hexlify(val) + \
                             ' reg: ' + binascii.hexlify(reg) + ' bank: ' + binascii.hexlify(bank) + \
                             ' span: ' + binascii.hexlify(span))
                if bankreg == '0200':
                    self.cur_mac = (binascii.hexlify(val[2]) + binascii.hexlify(val[1]) + binascii.hexlify(val[0])).upper()
                elif bankreg == '0203':
                    self.cur_nwkaddr = struct.unpack('B',val)[0]
                elif bankreg == '0204': # make sure tun is running whenever we get nwkid
                    self.cur_nwkid = struct.unpack('B',val)[0]
                    if not self.tun_thread and self.cur_nwkid != 255 and self.cur_mac != '000000':
                        self.init_tun()
                    else:
                        logger.info('getregister nwkid not yet valid')
                        self.tun_start_in_progress = False
                elif bankreg == '0506':
                    resistor = 10000
                    v = struct.unpack('B B', val)
                    vali = v[1]*256 + v[0]
                    resistance = 1023./vali - 1
                    resistance = resistor / resistance
                    B = 3977 # vishay (2%) digikey #: 2381 640 64103-ND
                    thermistor_nominal = 10000
                    steinhart = resistance/thermistor_nominal
                    steinhart = math.log(steinhart)
                    steinhart /= B
                    steinhart += 1./(25+273.15) # nominal temp in kelvin
                    steinhart = 1./steinhart
                    steinhart -= 273.15
                    logger.info(regname + ' tempC: ' + str(steinhart) + ' tempF: ' + str(steinhart*1.8+32))
                    
                return 3+d[2]
            elif cmd_type[1] == '4': # set register
                logger.debug('setregister')
                return 1
            elif cmd_type[1] == '5': # TxData
                if len(data) < 5:
                    return 0
                d = struct.unpack('B B B B B', data[:5])
                status = data[0]
                addr = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                rssi = data[4]
                rssi_str = self.rssi2str(d[4])
                if d[0] == 0:
                    logger.debug('TxData addr: ' + addr + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
                else:
                    logger.info('ERROR TxData addr: ' + addr + ' status: ' + binascii.hexlify(status) + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
                return 5
            elif cmd_type[1] == '6': # discover
                if len(data) < 7:
                    return 0
                d = struct.unpack('B B B B B B B', data[:7])
                mac = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                addr = (binascii.hexlify(data[6]) + binascii.hexlify(data[5]) + binascii.hexlify(data[4])).upper()
                try:
                    if mac in self.mac2addr:
                        oldaddr = self.mac2addr[mac]['addr']
                        if oldaddr != addr:
                            logger.debug('deleting mapping for addr:  ' + addr + ' old: ' + oldaddr)
                            del self.addr2stuff[addr]
                            logger.info('changing map for ' + mac + ' to: ' + addr)
                    else:
                        pass
                        logger.info('adding map for ' + mac + ' to: ' + addr)
                except:
                    logger.info('creating map for ' + mac + ' to: ' + addr)
                self.mac2addr[mac] = {'addr': addr}
                if addr not in self.addr2stuff or self.addr2stuff[addr]['mac'] != mac:
                    self.addr2stuff[addr] = {'mac': mac, 'msg':'', 'responses':{}}
                logger.debug('discover')
                return 7
            elif cmd_type[1] == 'a' or cmd_type[1] == 'A': # getremotereg
                if len(data) < 4:
                    return 0
                if len(data) < 8:
                    d = struct.unpack('B B B B', data[:4])
                else:
                    d = struct.unpack('B B B B B B B B', data[:8])
                s = struct.Struct('B B B')
                status = d[0]
                addr = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                if status != 0:
                    logger.info('ERROR GetRemoteReg addr: ' + addr + ' status: ' + binascii.hexlify(data[0]))
                    return 4
                elif len(data) < 8:
                    return 0
                rssi = data[4]
                rssi_str = self.rssi2str(d[4])
                reg = data[5]
                bank = data[6]
                span = d[7]
                if len(data) < 8 + span:
                    return 0
                val = data[8:8+span]
                bankreg = (binascii.hexlify(bank)+binascii.hexlify(reg)).upper()
                regname = 'Unknown' if bankreg not in regs else regs[bankreg]['name']
                logger.info('GetRemoteReg ' + regname + ' val: ' + binascii.hexlify(val) + ' addr: ' + addr + \
                            ' rssi: ' + binascii.hexlify(rssi) + rssi_str + \
                            ' reg: ' + binascii.hexlify(reg) + ' bank: ' + binascii.hexlify(bank) + ' span: ' + str(span))
                base_mac = (binascii.hexlify(val[2]) + binascii.hexlify(val[1]) + binascii.hexlify(val[0])).upper()
                if addr == 'FF0000' and bankreg == '0200' and (addr not in self.addr2stuff or self.addr2stuff[addr]['mac'] != base_mac):
                    logger.info('GetRemoteReg updating FF0000 to mac: ' + base_mac)
                    self.mac2addr[base_mac] = {'addr':'FF0000'}
                    self.addr2stuff['FF0000'] = {'mac':base_mac,'msg':'','responses':{}}
                return 8 + span
        elif cmd_type == 'JoinRequest':
            if len(data) < 8: #MAC,Addr,DeviceMode,SleepMode
                return 0
            mac = (binascii.hexlify(data[2]) + binascii.hexlify(data[1]) + binascii.hexlify(data[0])).upper()
            addr = (binascii.hexlify(data[5]) + binascii.hexlify(data[4]) + binascii.hexlify(data[3])).upper()
            dev = data[6]
            sleep = data[7]
            logger.info('JoinRequest mac: ' + mac + ' addr: ' + addr + ' dev: ' + binascii.hexlify(dev) + ' sleep: ' + binascii.hexlify(sleep))
            return 8
        elif cmd_type == 'Announce':
            if len(data) < 1:
                return 0
            ann_status = struct.unpack('B', data[:1])[0]
            logger.debug('Got announce status: ' + str(ann_status))
            if ann_status == 0xA0:
                logger.info('Announce: Radio startup completed')
                return 1
            elif ann_status == 0xA2:
                if len(data) < 6:
                    return 0
                d = struct.unpack('B B B B B B', data[:6])
                mac = (binascii.hexlify(data[3]) + binascii.hexlify(data[2]) + binascii.hexlify(data[1])).upper()
                range = data[5] # data[4] reserved
                logger.info('base: child joined network mac: ' + mac + ' range: ' + binascii.hexlify(range))
                if not self.initial_configuration:
                    values = (0xfb, 4, 0x06)
                    s = struct.Struct('< B B B')
                    new_cmd = s.pack(*values) + data[1:4]
                    self.enqueue_command(new_cmd)
                return 6
            elif ann_status == 0xA3:
                if len(data) < 6:
                    return 0
                d = struct.unpack('B B B B B B', data[:6])
                nwkid = data[1]
                mac = (binascii.hexlify(data[4]) + binascii.hexlify(data[3]) + binascii.hexlify(data[2])).upper()
                range = data[5]
                logger.info('remote: joined network mac: ' + mac + ' range: ' + binascii.hexlify(range) + ' nwkid: ' + binascii.hexlify(nwkid))
                if not self.initial_configuration:
                    values = (0xfb, 4, 0x06)
                    s = struct.Struct('< B B B')
                    new_cmd = s.pack(*values) + data[2:5]
                    self.enqueue_command(new_cmd)
                return 6
            elif ann_status == 0xA4:
                if len(data) < 2:
                    return 0
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
                    return 0
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
                        logger.warning('could not fully remote remote mac: ' + mac + ' progress: ' + str(progress))
                return 4
            elif ann_status == 0xA8:
                if len(data) < 11:
                    return 0
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
                range = data[10]
                logger.debug('base: heartbeat rx mac: ' + mac + ' nwkaddr: ' + binascii.hexlify(nwkaddr) +\
                             ' nwkid: ' + binascii.hexlify(nwkid) + ' par nwkid: ' + binascii.hexlify(parentnwkid) +\
                             ' beaconrssi: ' + binascii.hexlify(beaconrssi) + beaconrssi_str + \
                             ' avgtxattempts: ' + binascii.hexlify(avgtxattempts) + ' parentrssi: ' + binascii.hexlify(parentrssi) +\
                             ' ' + parentrssi_str + ' range: ' + binascii.hexlify(range))

                if not self.initial_configuration:
                    error = False
                    if mac not in self.mac2addr:
                        logger.info('heartbeat without mac2addr mapping.  mac: ' + mac)
                        error = True
                    elif self.mac2addr[mac]['addr'] not in self.addr2stuff:
                        logger.info('heartbeat without addr2stuff mapping.  mac: ' + mac + ' addr: ' + self.mac2addr[mac]['addr'])
                        error = True
                    elif self.addr2stuff[self.mac2addr[mac]['addr']]['mac'] != mac:
                        logger.info('heartbeat with invalid addr mapping.  mac: ' + mac + ' addr: ' + self.mac2addr[mac]['addr'] +\
                                        ' addr2stuff[mac]: ' + self.addr2stuff[self.mac2addr[mac]['addr']]['mac'])
                        error = True

                    if error:
                        logger.info('ignoring heartbeat due to error...rediscover')
                        if not self.initial_configuration:
                            values = (0xfb, 4, 0x06)
                            s = struct.Struct('< B B B')
                            new_cmd = s.pack(*values) + data[1:4]
                            self.enqueue_command(new_cmd)
                    else:
                        pass
                return 11
            elif ann_status == 0xA9:
                if len(data) < 2:
                    return 0
                nwkid = data[1]
                logger.info('base: router heartbeat timeout nwkid: ' + binascii.hexlify(nwkid))
                return 2
            elif ann_status >= 0xE0 and ann_status <= 0xEE:
                logger.critical('announced error status: ' + binascii.hexlify(data[0]))
                return 1
            else:
                logger.critical('Unexpected announce command: ' + binascii.hexlify(data[0]))
        elif cmd_type == 'RxEvent':
            logger.debug('RxEvent')
            pass
        elif cmd_type == 'RxData': # byte after rssi indicates length of this message broken into chunksize byte chunks
            if len(data) < 5:
                return 0
#            logger.debug('RxData data: ' + binascii.hexlify(data))
            d = struct.unpack('B B B B B', data[:5])
            addr = (binascii.hexlify(data[2]) + binascii.hexlify(data[1]) + binascii.hexlify(data[0])).upper()
            rssi = data[3]
            rssi_str = self.rssi2str(d[3])
            remaining = d[4]
            if remaining > chunksize:
                logger.critical('RxData with bad remaining: ' + str(remaining) + ' addr: ' + addr)
            if remaining > len(data)+5: # all the data here?
                return 0
            if not self.initial_configuration and addr not in self.addr2stuff:
                logger.warning('created unannounced addr2stuff entry for addr: ' + addr)
                self.addr2stuff[addr] = {'mac':'unknown', 'msg':'', 'responses':{}}
            if remaining > 0:
                rxmsg = struct.unpack(str(remaining)+'s',data[5:5+remaining])[0]
#                logger.debug('adding to message for addr: ' + addr + ' msg: ' + binascii.hexlify(rxmsg))
                if not self.initial_configuration:
                    self.addr2stuff[addr]['msg'] += rxmsg
#            logger.debug('current remaining: ' + str(remaining) + ' msg: ' + binascii.hexlify(self.addr2stuff[addr]['msg']))
            if remaining < chunksize:
#                logger.debug('process message for addr ' + addr + ' msg: ' + self.addr2stuff[addr]['msg'])
                msg = self.addr2stuff[addr]['msg']
                if not self.initial_configuration:
                    self.addr2stuff[addr]['msg'] = ''
                    d = struct.unpack('< B', msg[0:1])
                    request = d[0]
                    if request == ord('X'):
                        msg = msg[1:]
                        logger.debug('rxdata X msg: ' + msg)
                    elif len(msg) < 8:
                        logger.warning('message too short for req,mac,msgid msg: ' + binascii.hexlify(msg))
                    else:
                        d = struct.unpack('< B B B B I', msg[0:8])
                        mac = (binascii.hexlify(msg[3]) + binascii.hexlify(msg[2]) + binascii.hexlify(msg[1])).upper()
                        msgid = d[4]
                        if request <= 2: #request or response or ip
                            msg = msg[8:]
                        else:
                            logger.warning('rxdata unkown message type: ' + msg)
                        small_msg = msg if len(msg) < 100 else msg[:100]
                        if request == 1:
                            try:
                                logger.debug('RxData request complete: ' + small_msg)
                                data = urllib2.urlopen(msg, timeout=2)
                                datao = json.load(data)
                                ret_str = json.dumps(datao)
                            except Exception as ex:
                                logger.debug('rxdata request: No response from slave: ' + addr + ' Exception: ' + str(ex))
                                ret_str = json.dumps({'unreachable':1})
#                            frommac = gv.radio['radio_mac'] # WARNING NO LONGER MAINTAINED
                            frommacraw = [int(frommac[0:2],16),int(frommac[2:4],16),int(frommac[4:],16)]
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
                                os.write(self.tun_file, msg)
                            except:
                                logger.info('rxdata response: could not write tunfile mac: ' + mac + ' dropping response msg: ' + binascii.hexlify(msg))
            logger.debug('RxData addr: ' + addr + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
            return 5+remaining
        else:
            logger.critical('Unexpected command type: ' + cmd_type)
        return 0

    def tun_reader(self):
        time.sleep(2)
        logger.info('entering tun_reader')
        while self.tun_thread:
            try:
                data = os.read(self.tun_file, 5*1024*1024)
                if len(data) < 24:
                    logger.critical('tun_reader bad ip packet len: ' + str(len(data)) + ' data: ' + binascii.hexlify(data))
                    continue
                if struct.unpack('B',data[16])[0] != 0xa or struct.unpack('B',data[17])[0] != 0x1:
                    logger.critical('tun_reader unexpected from ip address len: ' + str(len(data)) + ' data: ' + binascii.hexlify(data))
                    continue
                if struct.unpack('B',data[20])[0] != 0xa or struct.unpack('B',data[21])[0] != 0x1:
                    logger.critical('tun_reader unexpected to ip address len: ' + str(len(data)) + ' data: ' + binascii.hexlify(data))
                    continue

                logger.debug('tun_reader good msg len: ' + str(len(data)) + ' data: ' + binascii.hexlify(data))

                from_addr = ('FF' + binascii.hexlify(data[18]) + binascii.hexlify(data[19])).upper()
                to_addr = ('FF' + binascii.hexlify(data[22]) + binascii.hexlify(data[23])).upper()
                if from_addr == 'FFFE01':
                    from_addr = 'FF0000' #base
                if to_addr == 'FFFE01':
                    to_addr = 'FF0000' #base
                if from_addr not in self.addr2stuff or self.addr2stuff[from_addr]['mac'] == 'unknown':
                    logger.info('tun_reader missing map for from_addr: ' + from_addr)
                    continue
                else:
                    from_mac = self.addr2stuff[from_addr]['mac']
                if to_addr not in self.addr2stuff or self.addr2stuff[to_addr]['mac'] == 'unknown':
                    logger.info('tun_reader missing map for to_addr: ' + to_addr)
                    if to_addr == 'FF0000':  # the base doesnt have heartbeat....get base mac
                        e = regs['0200']
                        self.pack_getregister(e['reg'], e['bank'], e['span'], to_addr)
                    continue
                else:
                    to_mac = self.addr2stuff[to_addr]['mac']

                ret_str = self.remote_command_response('tun_reader', 2, to_mac, from_mac, data)
            except:
                self.tun_thread = 0
                logger.exception('tun_reader')


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
        skip_read = False
        while self.alive:
            try:
                if not skip_read or cmd_type == 'None' or len(data) == 0:
                    try:
                        data += self.serial.read(1)             # read one, blocking
                        n = self.serial.inWaiting()             # look if there is more
                        if n:
                            data += self.serial.read(n)   # and get as much as possible
                    except serial.SerialException:
                        logger.exception('serial read failure: ')
                        self.alive = False
                        time.sleep(10)
                        continue

                if found_fb and remaining_bytes == 0: # deal with situation where no data associated with cmd
                    if cmd_type != 'Flush Remaining':
                        self.process_command(cmd_type, data)
                    found_fb = False
                    remaining_bytes = -1
                    cmd_type = 'None'

                if data:
                    self.write('reader', data)
                    if found_fb and remaining_bytes > 0 and cmd_type == 'None':
                        dc = data[0]
                        data = data[1:]
                        d = struct.unpack('B', dc)[0]
                        cmd_type = self.check_command(d)
                        remaining_bytes -= 1

                    avail = min(len(data), remaining_bytes)
                    if found_fb and remaining_bytes >= 0 and avail >= remaining_bytes:
                        if cmd_type == 'Flush Remaining':
                            logger.debug('Flush remaining len: ' + str(remaining_bytes))
                            data = data[remaining_bytes:]
                        else:
                            used = self.process_command(cmd_type, data)
                            data = data[used:]
                        found_fb = False
                        remaining_bytes = -1
                        cmd_type = 'None'

                    if len(data) == 0:
                        continue

                    for dc in data:
                        if cmd_type != 'None':
                            skip_read = not skip_read
                            break
                        d = struct.unpack('B', dc)[0]
                        data = data[1:] # remove dc from data
                        if not found_fb:
                            if d == 0xfb:
                                found_fb = True
                                remaining_bytes = -1
                                cmd_type = 'None'
                            else:
                                pass
#                                logger.debug('discarding fb: ' + str(d))
                        elif remaining_bytes == -1:
                            remaining_bytes = d
                        elif cmd_type == 'None':
                            cmd_type = self.check_command(d)
                            remaining_bytes -= 1

            except:
                logger.exception('sip_radio reader: ')


    def pack_setregister(self, reg, bank, span, val, remote_addr='', execute=True):
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
        self.pack_setregister(0xff, 0xff, 1, 0) # restore factory defaults
        self.enqueue_command(baud115200)
        self.enqueue_command(dntcfg)
        if len(self.key) == 16:
            self.pack_setregister(0x5, 0x0, 16, self.key) # set security key
        if self.power != -1:
            self.pack_setregister(0x18, 0x0, 1, self.power) # set power level
        else:
            logger.info('onetime_configuration: power level unchanged')
        self.pack_setregister(0x34, 0x0, 1, 1) # set TreeRoutingEn register 1
        self.pack_setregister(0x37, 0x0, 2, 60) # set heartbeat interval 60s
        self.pack_setregister(0x3a, 0x0, 1, 1) # set enableRtAcks 1
        self.pack_setregister(0x1, 0x1, 1, 1) # set CSMA
        self.pack_setregister(0x2, 0x1, 1, max(chunksize+7, 202)) # set baseslotsize == chunksize+overhead
        self.pack_setregister(0xb, 0x1, 1, max(chunksize+7,202)) # set csma_remtslotsize same as baseslotsize until we separate chunksizes
        self.pack_setregister(0x6, 0x6, 1, 0xc0) # set GPIO_SleepState to 0xc0
        if self.initial_configuration.upper() == 'BASE':
            self.pack_setregister(0x0, 0x0, 1, 1)
        elif self.initial_configuration.upper() == 'REMOTE':
            self.pack_setregister(0x0, 0x0, 1, 0)
        else:
            logger.info('onetime_configuration: DeviceMode unchanged')
        self.pack_setregister(255, 255, 1, 2) # MemorySave and reset

    def writer(self):

        self.enqueue_command(dntcfg)

        if self.initial_configuration:
            self.onetime_configuration()  # results in hanging serial interface due to reset

        for k, e in regs.iteritems():
            self.pack_getregister(e['reg'], e['bank'], e['span'])

        time.sleep(3)
        last_sensor_read_time = timegm(time.localtime())
        while self.alive:
            with self.command_lock:
                if len(self.command_q) > 0: # possibly retry something or write it if never tried
                    try:
                        self.write('writer', self.command_q[0][1], True)
                        self.command_q.pop(0)
                    except SerialTimeoutException:
                        logger.info('Serial Timeout!!!!!')

                    # if tun not running, reading these registers will have the side effect
                    # of getting it running
                    if not self.tun_thread and not self.tun_start_in_progress:
                        self.tun_start_in_progress = True
                        for r in ['0200', '0203', '0204']:
                            e = regs[r]
                            self.pack_getregister(e['reg'], e['bank'], e['span'])

#            cur_time = timegm(time.localtime())
#            if cur_time - last_sensor_read_time > 90:
#                for r in ['0506', '0508', '050A']:
#                    e = regs[r]
#                    self.pack_getregister(e['reg'], e['bank'], e['span'])
#                last_sensor_read_time = cur_time


class SubstationProxy:
    def __init__(self):
        self.radio_interface = 0
        self.reader_thread = 0
        self.writer_thread = 0
        self.app_thread = 0

    def onetime_config(self, type, baud, power, key):
#        try:
#hangs            rc = subprocess.call("/etc/init.d/substation_proxy stop", shell=True, stderr=subprocess.STDOUT)
#            logger.debug('substation_proxy (init.d) stopped: ' + str(rc))
#        except:
#            logger.exception('onetime_config: could not stop substation_proxy')
        try:
            sr = SerialRadio(self, type, baud, power, key)
            self.radio_interface = sr
            self.reader_thread = sr.start_thread('reader', sr.reader)
            self.writer_thread = sr.start_thread('writer',sr.writer)
        except:
            logger.exception('onetime_config:')

    def create_proxy(self):
        logger.debug('create_proxy: start')
        try:
            if not self.radio_interface:
                sr = SerialRadio(self)
                if sr:
                    logger.debug('create_proxy: set radio_interface')
                else:
                    logger.debug('create_proxy: set radio_interface to 0')
                self.radio_interface = sr

            if self.radio_interface.serial:  # only create threads if viable serial interface
                if not self.reader_thread:
                    logger.debug('create_proxy: radio opened')
                    self.reader_thread = sr.start_thread('reader', sr.reader)
                if not self.writer_thread:
                    self.writer_thread = sr.start_thread('writer',sr.writer)
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
                self.app_thread = sr.start_thread('app',app.run)
                logger.debug('create_proxy: app started')
        except:
            logger.exception('create_proxy:')
        logger.debug('create_proxy: finish')

    def stop_proxy(self):
        logger.debug('stop_proxy: start')
        if self.radio_interface:
            try:
                self.radio_interface.stop_tun()
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
            if self.radio_interface.serial:
                logger.debug('stop_proxy: radio_interface close')
                self.radio_interface.serial.close()
            self.radio_interface = 0
        logger.debug('stop_proxy: finish')

logger = logging.getLogger('substation_proxy')
def usage():
    print 'substation_proxy --onetime_configuration --loglevel <debug|info|warning|error|critical> --baudrate <9600|115200> --power <0..5> --type <remote|base>'
    sys.exit(2)

if __name__ == "__main__":
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
    logger.addHandler(fh)

    baud = 115200
    logger.setLevel(logging.INFO)
    type = ''
    key = ''
    onetime_config = False
    power = -1
    try:
        opts, args = getopt.getopt(sys.argv[1:],"l:b:t:p:k:o",["loglevel=","baudrate=","onetime_configuration","type=","power=","key="])
    except getopt.GetoptError:
        usage()

    for opt, arg in opts:
        if opt in ("-l","--loglevel"):
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
            if len(key) != 16:
                print 'key must be 16 bytes'
                usage()
        elif opt in ("-p","--power"):
            try:
                power = int(arg)
            except:
                power = -1
            if power < 0 or power > 5:
                print 'only power levels 0..5 (1mW,10mW,63mW,.25W,.5W,1W).  Top 3 are limited bandwidth.'
                usage()
        elif opt in ("-o","--onetime_configuration"):
            onetime_config = True
        elif opt in ("-t","--type"):
            type = arg.upper()
            if type != 'BASE' and type != 'REMOTE':
                print 'only base,remote supported as types'
                usage()

    substation_proxy = SubstationProxy()
    if onetime_config:
        logger.critical('Radio onetime')
        substation_proxy.onetime_config(type, baud, power, key)
        time.sleep(15)  # give it time to write registers
        os._exit(0) # sip_monitor to restart
    else:
        try:
            info = subprocess.check_output("grep 10.0.0.1 /etc/network/interfaces", shell=True, stderr=subprocess.STDOUT)
            logger.critical('Starting boiler_net_finish')
            rc = subprocess.call("/etc/init.d/boiler_net_finish start", shell=True, stderr=subprocess.STDOUT)
        except: # have real network
            logger.critical('Starting boiler')
            rc = subprocess.call("/etc/init.d/boiler start", shell=True, stderr=subprocess.STDOUT)
        logger.critical('Starting boiler_monitor:')
        rc = subprocess.call("/etc/init.d/boiler_monitor start", shell=True, stderr=subprocess.STDOUT)
        logger.critical('Starting radio interface')
        while True:
            substation_proxy.create_proxy() # webserver and radio thread startup
            try:
                with open('/dev/dnt900', 'r') as sdf:  # A config file
                    pass
            except:
                time.sleep(60) # no radio...just spin slowly
                continue
            logger.debug('pre check for serial interface')
            while substation_proxy.radio_interface.serial and substation_proxy.radio_interface.alive:
                time.sleep(10)
            logger.critical('exiting substation_proxy')
            os._exit() # sip_monitor to restart
