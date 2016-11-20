# !/usr/bin/env python
# -*- coding: utf-8 -*-

import gv
from helpers import usb_reset
import sys
import getopt
import logging
import logging.handlers
import urllib
import urllib2

import threading
import struct
import binascii
import serial
import struct
import time
from calendar import timegm
import os
import subprocess

radio_spi_lock = threading.RLock()
radio_spi_cmds = {}
SPI_MAGIC = 245 # must be bigger than any chunksize that RxData may see

######### Radio initialization ############
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

last_discover = ''

SIOCGIFMTU = 0x8921
SIOCSIFMTU = 0x8922

def start_thread(name, func, *argv):
    thread = threading.Thread(target=func, args=argv)
    thread.setDaemon(True)
    thread.setName(name)
    thread.start()
    return thread

MB = 1*1024*1024
class SerialRadio:
    def __init__(self, proxy):
        self.substation_proxy = proxy
        self.network_prefix = '10.0'
        self.serialport = self.open_serial(115200)
        self.chunksize = 195
        self.command_q = []
        self.mac2addr = {}
        self.addr2stuff = {}
        self.alive = True
        self.cur_nwkid = 255
        self.cur_nwkaddr = 255
        self.cur_mac = '000000'
        self.command_lock = threading.RLock()
        self.msg_count = 0
        self.response_lock = threading.RLock()

    def open_serial(self, baud):
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

    def compute_radio_ip(self):
        if self.cur_nwkid == 0 and self.cur_nwkaddr  == 0:
            router = 254
            entry = 1
        else:
            router = self.cur_nwkid
            entry = self.cur_nwkaddr
        return self.network_prefix + '.' + str(router) +'.' + str(entry)

    def enqueue_command(self,fullcmd):
        with self.command_lock:
            cmds = struct.unpack('B B B', fullcmd[:3])
            self.command_q.append([cmds[2], fullcmd, 0])

    def rediscover(self, data):
        values = (0xfb, 4, 0x06)
        s = struct.Struct('< B B B')
        new_cmd = s.pack(*values) + data[:3]
        self.enqueue_command(new_cmd)

    def write(self, r, d, serial_write=False):
        if serial_write:
            cmd = struct.unpack('B B B', d[:3])
            self.serialport.write(d)   # may raise timeout if write fails
        if serial_write:
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
            if is_spi_msg: # dont include len for outgoing spi messages...they will never be "received" by me
                data = struct.pack('< B B B B B B', 0xfb, 4+chunk, 0x05, int(addr[4:],16), int(addr[2:4],16), int(addr[0:2],16))
            else:
                data = struct.pack('< B B B B B B B', 0xfb, 5+chunk, 0x05, int(addr[4:],16), int(addr[2:4],16), int(addr[0:2],16), chunk)
            for i in range(written,written+chunk):
                data += struct.pack('B', struct.unpack('B', msg[i:i+1])[0])
#            logger.critical('transmit_msg data: ' + binascii.hexlify(data))
            self.enqueue_command(data)
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
            logger.info('discarding cmd0: ' + str(c))
        return cmd_type

    def rssi2str(self, rssi):
        if rssi == 0x7f:
            return ' No RSSI (no ack)'
        elif rssi == 0x7e:
            return ' No RSSI (routed pkt)'
        return ''

    def execute_spi_commands(self, addr, utag):
        """do the next block of spi commands indexed by utag to the radio at address utag"""

        with radio_spi_lock:
            try:
                cmd_count = radio_spi_cmds[utag]['cmd_counts'][0]
                while cmd_count > 0: # process commands in block
                    msg = radio_spi_cmds[utag]['cmds'].pop(0)
                    if cmd_count == 1: # last message in block?  Adjust pstate to allow remote back to sleep
                        print 'changing pstate of last command to re-enable sleep'
                        pstate = struct.unpack('B', msg[4])[0]
                        pstate |= 1
                        print 'old command for ' + utag + ': 0x' + binascii.hexlify(msg)
                        msg = msg[0:4] + struct.pack('B', pstate) + msg[5:]
                    print 'processing command for ' + utag + ': 0x' + binascii.hexlify(msg)
                    self.transmit_msg(addr, msg, True)
                    cmd_count -= 1
                if cmd_count == 0: # remove count associated with block
                    print 'completed block processing for ' + utag
                    radio_spi_cmds[utag]['cmd_counts'].pop(0)
            except Exception as ex:
                print 'never seen spi commands for ' + utag + ' exception: ' + str(ex)

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
                elif bankreg == '0506' or bankreg == '0508' or bankreg == '050A':
                    v = '0x' + binascii.hexlify(val[1]) + binascii.hexlify(val[0])
                    adc_idx = int('0x'+bankreg[3], 0)/2 - 3
                    sensor_data_str = '&adc'+str(adc_idx)+'='+v
                    logger.info('getregister sensor_data: ' + sensor_data_str)
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
                    print 'TxData ' + addr + ' ' + rssi_str
                    logger.debug(ct + ' addr: ' + addr + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
                else:
                    logger.info('ERROR ' + ct + ' addr: ' + addr + ' status: ' + binascii.hexlify(status) + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
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
                    logger.info('ERROR GetRemoteReg addr: ' + addr + ' status: ' + binascii.hexlify(data[0]))
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
                    print 'usertag... ', vals
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
                    self.rediscover(data[1:4])
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
            logger.info('rxevent sensor_data: ' + sensor_data_str)
            try:
                utag = self.addr2stuff[addr]['usertag']
            except:
                utag = ''
            if addr in self.addr2stuff and utag != '':
                try:
                    cmd_count = radio_spi_cmds[utag]['cmd_counts'][0] # ok for it to be racy
                    if cmd_count > -1:
                        print 'override sleep addr: ' + addr
                        e = regs['FF0C']
                        self.pack_setregister(e['reg'], e['bank'], e['span'], 1, addr) # override sleep
                        print 'addr: ' + addr + ' utag:' + utag + ' radio_spi_cmds: ' + str(radio_spi_cmds)
                        self.execute_spi_commands(addr, utag)
                except Exception as ex:
                    print 'exception', ex
                    pass
            else:
                logger.info('RxEvent with unmapped addr: ' + addr + ' Rediscover.')
                self.rediscover(data[0:3])
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
            is_spi_msg = bool(remaining == SPI_MAGIC)
            if is_spi_msg:
                remaining = 32 # always 32B including SPI_MAGIC
            print 'RxData ' + addr + ' ' + rssi_str + ' remaining: ' + str(remaining) + ' spi: ' + str(is_spi_msg)
            if remaining > self.chunksize:
                logger.critical('RxData with bad remaining: ' + str(remaining) + ' addr: ' + addr)
            if remaining+5 > len(data): # all the data here?
                return -1
            if addr not in self.addr2stuff:
                logger.warning('created unannounced addr2stuff entry for addr: ' + addr)
                self.addr2stuff[addr] = {'mac':'unknown', 'msg':'', 'responses':{}}
            if remaining > 0:
                adjust = -1 if is_spi_msg else 0
                rxmsg = struct.unpack(str(remaining)+'s',data[5+adjust:5+adjust+remaining])[0]
#                logger.debug('adding to message for addr: ' + addr + ' msg: ' + binascii.hexlify(rxmsg))
                self.addr2stuff[addr]['msg'] += rxmsg
#            logger.debug('current remaining: ' + str(remaining) + ' msg: ' + binascii.hexlify(self.addr2stuff[addr]['msg']))
            if remaining < self.chunksize:
#                logger.debug('process message for addr ' + addr + ' msg: ' + self.addr2stuff[addr]['msg'])
                try:
                    msg = self.addr2stuff[addr]['msg']
                except:
                    msg = '' # probably a remote sending 0 len msg and 'msg' field never got initialized
                self.addr2stuff[addr]['msg'] = ''
                if len(msg) == 0:
                    logger.error('RxData unexpected 0 length message from addr: ' + addr)
                    return 5+remaining

                if is_spi_msg: # SPI message
                    print_gateway_response(msg)
                    pstate = struct.unpack('B', msg[6])[0]
                    if (pstate & 1) == 1:
                        print 'cancel override sleep addr: ' + addr
                        e = regs['FF0C']
                        self.pack_setregister(e['reg'], e['bank'], e['span'], 2, addr) # cancel sleep override
                else:
                    logger.error('RxData unexpected message not starting with "S": ' + msg)
#            logger.debug('RxData addr: ' + addr + ' rssi: ' + binascii.hexlify(rssi) + rssi_str)
            return 5+remaining
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
        skip_read = False
        while self.alive:
            try:
                if not skip_read or cmd_type == 'None' or len(data) == 0:
                    try:
                        data += self.serialport.read(1)             # read one, blocking
                        n = self.serialport.inWaiting()             # look if there is more
                        if n:
                            data += self.serialport.read(n)   # and get as much as possible
#                        print 'data chars read: ' + str(n+1) + ' data: ' + binascii.hexlify(data)
#                    except serial.SerialException:
                    except: # failure to open serial port also should exit
                        logger.exception('serial read failure: ')
                        self.alive = False
                        time.sleep(10)
                        continue

                if found_fb and remaining_bytes >= 0: # deal with situation where no data associated with cmd
                    used = 0
                    if cmd_type != 'Flush Remaining':
#                        print 'try processed cmd: ' + cmd_type + ' remaining bytes: ' + str(remaining_bytes) + ' data: ' + binascii.hexlify(data)
                        used = self.process_command(cmd_type, data)
                        if used > 0:
                            data = data[used:]
                    if used >= 0:
#                        print 'fb processed cmd: ' + cmd_type + ' used: ' + str(used) + ' remaining data: ' + binascii.hexlify(data)
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
#                            print 'Flush remaining len: ' + str(remaining_bytes)
                            used = remaining_bytes
                        else:
#                            print 'try1 processed cmd: ' + cmd_type + ' remaining bytes: ' + str(remaining_bytes) + ' data: ' + binascii.hexlify(data)
                            used = self.process_command(cmd_type, data)

                        if used >= 0:
                            data = data[used:]
#                            print 'fb1 processed cmd: ' + cmd_type + ' used: ' + str(used) + ' remaining data: ' + binascii.hexlify(data)
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
#                                print 'discarding char: ' + str(d)
#                                logger.debug('discarding char: ' + str(d))
                                pass
                        elif remaining_bytes == -1:
                            remaining_bytes = d
                        elif cmd_type == 'None':
                            cmd_type = self.check_command(d)
                            remaining_bytes -= 1

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

    def writer(self):

        self.enqueue_command(dntcfg)
        time.sleep(1)
        last_network_prefix_update_time = 0
        last_radio_sensor_read_time = 0
        while self.alive:
            with self.command_lock:
                if len(self.command_q) > 0: # possibly retry something or write it if never tried
                    try:
                        cq0 = self.command_q[0][1]
                        self.write('writer', cq0, True)
                        self.command_q.pop(0)
                    except serial.SerialTimeoutException:
                        logger.info('Serial Timeout!!!!!')
                    except:
                        logger.exception('Unexpected Serial Write Exception.')

            cur_time = timegm(time.localtime())
            # read local radio sensors.  Also update RemoteSlotSize if needed
            if cur_time - last_radio_sensor_read_time > 60:
                last_radio_sensor_read_time = cur_time
                radio_ip = self.compute_radio_ip()
                for r in ['0208', '0506', '0508', '050A']:
                    e = regs[r]
                    self.pack_getregister(e['reg'], e['bank'], e['span'])

            # if we are a base radio, then propagate our network prefix to all other radios that we reach
            if cur_time - last_network_prefix_update_time > 90:
                radio_ip = self.compute_radio_ip()
                if '254.1' not in radio_ip:
                    continue
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


class SubstationProxy:
    def __init__(self):
        self.radio_interface = 0
        self.reader_thread = 0
        self.writer_thread = 0

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

            if sr and sr.serialport:  # only create threads if viable serial interface
                if not self.reader_thread:
                    logger.debug('create_proxy: radio opened')
                    self.reader_thread = start_thread('reader', sr.reader)
                if not self.writer_thread:
                    self.writer_thread = start_thread('writer',sr.writer)
                    logger.debug('create_proxy: writer started')

        except:
            logger.exception('create_proxy:')

    def stop_proxy(self):
        logger.debug('stop_proxy: start')
        if self.radio_interface:
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

logger = logging.getLogger('radspi')
def usage():
    print 'python radspi.py [-r|-w|-g|-p] [--addr=<i2caddr>] [--offset=<i2coffset>] [--rlen=<readlen>] [--cmd=<bootloadercmd>] [--payload=<hexstring>]'
    sys.exit(2)

def int_to_hex(v):
    """return two character string of hex characters corresponding to value v"""
    return "{0:0{1}x}".format(v,2)

def print_gateway_response(data):
    "Print deconstructed 32B response from gateway"""

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
    print out

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
        print 'Invalid spi mode: ' + mode
        raise ValueError, 'Invalid spi mode'
    return binascii.unhexlify(cmd)

def ps_list(proc):
    """Return ps output for processes named proc"""

    ps_info = subprocess.check_output("ps auwx | grep " + proc, shell=True)
    l = ps_info.split('\n')
    new_l = []
    for e in l:  # remove all grep references
        if 'grep -e' not in e and e != '':
            new_l.append(e)
    return new_l

if __name__ == "__main__":

    log_file = 'logs/radspi.out'
    fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5*MB, backupCount=5)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)

    # kill any existing sip stuff that might interfere with radio operation.
    kill_list = ps_list('-e sip_monitor.py -e substation_proxy.py -e sip.py')
    for entry in kill_list:
        if '/usr/bin/python' in entry:
            pid = entry.split()[1]
            program = entry.split()[11]
            print 'killing ' + program
            subprocess.call("kill -9 " + str(pid), shell=True, stderr=subprocess.STDOUT)

    usb_reset('radio')
    logger.critical('radspi start')

    substation_proxy = SubstationProxy()
    substation_proxy.create_proxy()
    sr = substation_proxy.radio_interface

    last_radioname = ''
    last_radio_cmd_count = 0
    for line in sys.stdin:
        # remove comments and leading and trailing spaces
        hash = line.find('#')
        if hash != -1:
            line = line[0:hash]
        line = line.strip()
        if len(line) == 0:
            contine

        cmd = line.split(' ',1)[0]
        line = line[len(cmd):].strip()
        print cmd, line
        if cmd == 'sleep':
            time.sleep(float(line))
        elif cmd == 'wait': # wait until named sleeping radio is found
            found_utag = False
            while not found_utag:
                for k,v in sr.addr2stuff.items():
                    try:
                        if v['usertag'] == line:
                            found_utag = True
                            break
                    except:
                        pass
                if not found_utag:
                    print 'wait... ' + line
                    time.sleep(5)
        elif cmd == 'waitmac': # wait until radio with named macid is found
            while True:
                try:
                   if sr.addr2stuff[self.mac2addr[line]['addr']]['mac'] == line:
                        break
                except:
                    pass
                print 'waitmac... ' + line
                time.sleep(5)
        elif cmd == 'radio' or cmd == 'done' or cmd == 'execute':
            if last_radioname != '':
                with radio_spi_lock:
                    radio_spi_cmds[last_radioname]['cmd_counts'][-1] = last_radio_cmd_count
                    radio_spi_cmds[last_radioname]['cmd_counts'].append(-1) # maintain trailing -1
            if cmd == 'done':
                break
            elif cmd == 'execute':
                try:
                    addr = sr.mac2addr[line]['addr'] # execute command takes macid for radio to tickle
                    sr.execute_spi_commands(addr, line)
                except:
                    print 'could not find radio ' + line + ' to execute spi commands'
            else:
                last_radioname = line
                last_radio_cmd_count = 0
                if last_radioname != '':
                    if last_radioname not in radio_spi_cmds:
                        radio_spi_cmds[last_radioname] = {'cmd_counts':[-1],'cmds':[]}
        elif cmd == 'command':
            if last_radioname == '':
                print 'ignoring command due to no named radio block: ' + line
                continue
            payload = ''
            rlen = 0
            address = 0
            reg = 0
            pstate = 0
            mode = ''
            cmd = 0

            line = line.split()
            try:
                opts, args = getopt.getopt(line,"brwgp:a:o:n:c:",["payload=","addr=","reg=","offset=","rlen=","cmd="])
            except getopt.GetoptError:
                usage()

            for opt, arg in opts:
                if opt in ("-b","-r","-w","-g"):
                    if mode == '':
                        mode = opt[1:]
                    else:
                        print 'only one of -[rwgb] can be specified'
                        usage()
                elif opt in ("-p","--payload"):
                    arg_len = len(arg)
                    if arg_len < 2 or (arg_len & 1) == 1:
                        print 'payload must specify even number of hex characters: ' + arg
                        usage()
                    payload = arg
                elif opt in ("-a","--addr"):
                    try:
                        address = int(arg,0)
                    except:
                        print 'address must be valid int or hex number'
                        usage()
                elif opt in ("-o","--reg","--offset"):
                    try:
                        address = int(arg,0)
                    except:
                        print 'register offset must be valid int or hex number'
                        usage()
                elif opt in ("-n","--rlen"):
                    try:
                        rlen = int(arg,0)
                    except:
                        print 'rlen must be valid int or hex number'
                        usage()
                elif opt in ("-c","--cmd"):
                    try:
                        cmd = int(arg,0)
                    except:
                        print 'cmd must be valid int or hex number'
                        usage()

            bin_cmd = pack_gateway_command(mode, pstate, address, reg, rlen, payload, cmd)
            with radio_spi_lock:
                radio_spi_cmds[last_radioname]['cmds'].append(bin_cmd)
                last_radio_cmd_count += 1

    # make sure above blocks have been executed
    more_spi = True
    while more_spi:
        more_spi = False
        for k,v in radio_spi_cmds.items():
            if len(v['cmd_counts']) > 1:
                print 'waiting on... ' + k
                more_spi = True
                time.sleep(10)
                break
    os._exit(0)
