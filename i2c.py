# -*- coding: utf-8 -*-

import gv
import time
import pigpio
import subprocess
import logging
from threading import RLock
from array import array
import binascii

pi = pigpio.pi()
i2c_lock = RLock()

SDA = 2
SCL = 3
BAUD = 300000 # below 50000 run into trouble with deadman triggering.  At 200000 had trouble with bootloader but no more
BOOTLOAD_VERSION = 0x80

# Use pigpio bit banging i2c to get correct clock stretching.
# See http://abyz.co.uk/rpi/pigpio/python.html
# close if might be open already
def i2c_close():
    try:
        gv.logger.info('Closing i2c bus')
        with i2c_lock:
            pi.bb_i2c_close(SDA)
        time.sleep(.5)
    except:
        pass
i2c_close()

def i2c_open():
    gv.logger.info('Opening i2c bus')
    with i2c_lock:
        h = pi.bb_i2c_open(SDA, SCL, BAUD)
    if h != 0:
        gv.logger.critical('Cannot open i2c bus: ' + str(h))
i2c_open()

ADDRESS = 0x60
BASE = 0x40

VERSION = 0x0
ZONE_STATE = 0x6
ZONE_SET = 0x7
ZONE_CLEAR = 0x8
LED = 0x9
SCRATCH = 0xc
BOOTLOADER = 0x3f

def i2c_reset(address):
    # todo reset requires re-initialization of sensor ptype and other considerations
    gv.logger.critical('start resetting i2c address: ' + hex(address))
    with i2c_lock:
        i2c_write(address, BOOTLOADER, 0x66)
        time.sleep(4)
        i2c_close()
        i2c_open()
    gv.logger.info('finished resetting i2c address: ' + hex(address))

def i2c_write(address, reg, val, delay=.1):
    with i2c_lock:
        i2c_data = [4, address, 2, 7, 2, reg, val, 3, 0]
#        print 'i2c_write data: ', i2c_data
        (count, data) = pi.bb_i2c_zip(SDA, i2c_data)
        time.sleep(delay)
    if count < 0:  # -82 == write failure
#        print 'i2c_write failure count: ', count, ' addr: ', hex(address), ' reg: ', hex(reg), ' val: ', val
        raise IOError, 'i2c write failure'
#    print 'i2c_write count: ', count, ' addr: ', hex(address), ' reg: ', hex(reg), ' val: ', val

def i2c_read(address, reg):
    with i2c_lock:
        i2c_data = [4, address, 2, 7, 1, reg, 2, 6, 1, 3, 0]
#        print 'i2c_read data: ', i2c_data
        (count, data) = pi.bb_i2c_zip(SDA, i2c_data)
    if count < 0:  # -83 == read failure
#        print 'i2c_read failure count: ', count, ' addr: ', hex(address), ' reg: ', hex(reg)
        raise IOError, 'i2c read failure'
#    print 'i2c_read count: ', count, ' data[0]: ', data[0], ' addr: ', hex(address), ' reg: ', hex(reg)
    return data[0]

def i2c_read_block_data(address, reg, byte_count):
    with i2c_lock:
        i2c_data = [4, address, 2, 7, 1, reg, 2, 6, byte_count, 3, 0]
#        print 'i2c_read_block data: ', i2c_data
        (count, data) = pi.bb_i2c_zip(SDA, i2c_data)
#    print 'i2c_read_block_data count: ', count, ' addr: ', hex(address), ' reg: ', hex(reg), ' byte_count: ', byte_count
    if count < 0:  # -83 == read failure
        raise IOError, 'i2c read failure'
#    for i in range(count):
#        print 'i2c_read_block_data data[',i,']: ', data[i], hex(data[i])
    return data

def i2c_write_block_data(address, reg, wdata, delay=.1):
    """Write the list of bytes in data by writing them"""

    cmd = [4, address, 2, 7, 1+len(wdata), reg] # prepare for writing
    cmd += wdata
    cmd += [3, 0] # finish
    with i2c_lock:
#        print 'i2c_write_block_data cmd: ', cmd
        (count, data) = pi.bb_i2c_zip(SDA, cmd)
        time.sleep(delay)
    if count < 0:  # -82 == write failure
        raise IOError, 'i2c write failure'

def sign_extend(value, bits):
    sign_bit = 1 << (bits - 1)
    return (value & (sign_bit - 1)) - (value & sign_bit)

def i2c_structure_read(format, address, reg):
    byte_count = 0
    for c in format:
        if c == 'b' or c == 'B':
            byte_count += 1
        elif c == 'h' or c == 'H':
            byte_count += 2
        elif c == 'i' or c == 'I':
            byte_count += 4
        elif c == 'q' or c == 'Q':
            byte_count += 8
        elif c == ' ':
            continue
        else:
            raise ValueError('Unrecognized format')

    result = []
    try:
#        print 'attempt structure_read format: ' + format + ' address: ' + hex(address) + ' reg: ' + hex(reg) + ' byte_count: ' + str(byte_count)
        vs = i2c_read_block_data(address, reg, byte_count)
#        if byte_count == 14:
#            print ' len: ', byte_count, ' format: ', format
#            print 'structure bytes: ', ','.join([hex(i) for i in vs])
        for c in format:
            if c == 'b' or c == 'B':
                v = vs.pop(0)
                if c == 'b':
                    v = sign_extend(v, 8)
            elif c == 'h' or c == 'H':
                vss = [vs.pop(0) for i in range(2)]
                v = (vss[0]<<8) | vss[1]
                if c == 'h':
                    v = sign_extend(v, 16)
            elif c == 'i' or c == 'I':
                vss = [vs.pop(0) for i in range(4)]
                v = (vss[0]<<24) | (vss[1]<<16) | (vss[2]<<8) | vss[3]
                if c == 'i':
                    v = sign_extend(v, 32)
            elif c == 'q' or c == 'Q':
                vss = [vs.pop(0) for i in range(8)]
                v = (vss[0]<<56) | (vss[1]<<48) | (vss[2]<<40) | (vss[3]<<32) | \
                    (vss[4]<<24) | (vss[5]<<16) | (vss[6]<<8) | vss[7]
            result.append(v)
    except:
        pass
    return result

def i2c_structure_write(format, address, reg, data, delay=.1, byte_swap=False):
    # for now assume MSB written first
#    print 'sw size: ', len(data), binascii.hexlify(data), format, hex(address), hex(reg)
    w_data = []
    pos = 0
    for c in format:
        size = 0
        if c == 'b' or c == 'B':
            size = 1
        elif c == 'h' or c == 'H':
            size = 2
        elif c == 'i' or c == 'I':
            size = 4
        elif c == 'q' or c == 'Q':
            size = 8
        elif c == ' ':
            continue
        elif c == 'X': # skip a byte in data for alignment or whatever
            pos += 1
        else:
            raise ValueError('Unrecognized format')

        if (pos & ((1<<(size-1)) - 1)) != 0:
            logger.critical('i2c_structure_write_via_read requires aligned data.  Format: ' + format)
        for i in range(size):
            if byte_swap:
                w_data.append(data[pos+size-1-i])
            else:
                w_data.append(data[pos+i])
        pos += size

    i2c_write_block_data(address, reg, w_data, delay)

def get_vsb_boards():
    """Return dictionary of vsb board numbers and the version of fw running"""
    boards = {}
    for i in range(8):
        with i2c_lock:
            try:
                version = i2c_read(ADDRESS+i, VERSION)
                boards[i] = version
                if version >= BOOTLOAD_VERSION:
                    gv.in_bootloader[i] = True
                elif i in gv.in_bootloader:
                    del gv.in_bootloader[i]
            except:
                try:
                    del gv.in_bootloader[i]
                except:
                    pass
    return boards

def read_blank_check(address):
    bc = i2c_read(address, 9)
    time.sleep(1) # not sure why, but.....
    return bc

def read_pulse_counter(vsb_bd, vsb_pos):
    "Read the count of pulses clearing data"""

    address = ADDRESS+vsb_bd
    sensor_page = BASE + 0x30*vsb_pos
    pulses = 0
    try:
        pulses = i2c_structure_read('I', address, sensor_page+4)[0]
        return pulses
        pulses = i2c_structure_read('BBBBIHHH', address, sensor_page)
        out = 'pulse counter bd: ' + str(vsb_bd) + ' pos: ' + str(vsb_pos) + \
                       ' format: ' + str(pulses[0]) + ' config: ' + str(pulses[1]) + \
                       ' period: ' + str(pulses[2]) + ' clear: ' + str(pulses[3]) + \
                       ' count: ' + str(pulses[4]) + ' rate: ' + str(pulses[5]) + \
                       ' max: ' + str(pulses[6]) + ' min: ' + str(pulses[7])
#        print out
        gv.logger.debug(out)
        return pulses[4]

    except Exception as ex:
        pass
    raise ValueError('Cannot read pulse counter information')

def read_temperature_state(vsb_bd, vsb_pos):
    "Read and log all of the temperature state.  Return the temperature"""

    address = ADDRESS+vsb_bd
    sensor_page = BASE + 0x30*vsb_pos
    pulses = 0
    try:
        temps = i2c_structure_read('BBHHHQQ', address, sensor_page)
        out = 'temp state bd: ' + str(vsb_bd) + ' pos: ' + str(vsb_pos) + ' format: ' + str(temps[0]) + \
              ' valid: ' + str(temps[1]) + \
              ' tempc: ' + str(temps[2]) + ' max: ' + str(temps[3]) + \
              ' min: ' + str(temps[4]) + ' serial: ' + "{0:#0{1}x}".format(temps[6],18)
#        print out
        gv.logger.debug(out)
        return temps[2]

    except Exception as ex:
        pass
    raise ValueError('Cannot read temperature state information')

def read_dry_contact(vsb_bd, vsb_pos):
    "Read the state and count of dry contact clearing data"""

    address = ADDRESS+vsb_bd
    sensor_page = BASE + 0x30*vsb_pos
    try:
        dryv = i2c_structure_read('BBHH', address, sensor_page)
        out = 'dry state bd: ' + str(vsb_bd) + ' pos: ' + str(vsb_pos) + ' format: ' + str(dryv[0]) + \
              ' status: ' + str(dryv[1]) + \
              ' edgesf: ' + str(dryv[2]) + ' edgesr: ' + str(dryv[3])
#        print out
        gv.logger.debug(out)
        return dryv
    except:
        pass
    raise ValueError('Cannot read contact information')

def validate_line(address, addr, bin):
    """validate the instructions at addr against bin, possibly raising an exception"""

    fail_count = 0
    # assume pc_address has already been properly configured and we will autoincrement
    for i in range(4): # read as four separate chunks of 16B each encoding which chunk in register
        bin_read = i2c_structure_read('HHHHHHHH', address, (i<<4)|3)
#        print 'bin_readh', hex(addr+8*i), [hex(v) for v in bin_read]
        if len(bin_read) != 8:
            gv.logger.critical('bootload bin_read != 8: ' + str(len(bin_read)))
            raise ValueError('Bad flash read')
        for j in range(len(bin_read)):
            compare = binascii.hexlify(bin[16*i+2*j+1]) + binascii.hexlify(bin[16*i+2*j])
            bin_readh = hex(bin_read[j])[2:]
            while len(bin_readh) < 4:
                bin_readh = '0' + bin_readh
            if bin_readh == compare:
                continue
            print 'mismatch bin_read: ', hex(addr + 8*i+j), bin_readh, compare
            fail_count += 1
            gv.logger.critical('bootload mismatch at word: ' + hex(addr + 8*i+j) + ' got: 0x' + bin_readh + ' expected: 0x' + compare)
    return fail_count

def bootload(address, bin_data):
    """For device at 'address', load the sorted array (indexed by address) of binary data via i2c.
       Return False if failed to bootload.

       We first set base address for application to 0x400
       Erase all memory with a sequence of i2c_read(reg=4) (64B chunks with automatic increment of address
       Set application address to 0x440
       Write 64B chunks from file to internal buffer (MSB of each word first with lowest address first) and
       then write that to the current address (auto increments) via i2c_read(reg=5)
       Reset application address to 0x440
       Read and verify that all data written is correctly read
       Write seal code and crc to address 0x400 and jump to new application
    """
    with i2c_lock:
        version = i2c_read(address, VERSION)
        if version < BOOTLOAD_VERSION: # not yet in bootloader?
            gv.in_bootloader[0x60-address] = True
            i2c_write(address, BOOTLOADER, 0xdb)
            time.sleep(1) # give time to land

        version = i2c_read(address, VERSION)
        if version < BOOTLOAD_VERSION:
            try:
                del gv.in_bootloader[0x60-address]
            except:
                pass
            gv.logger.critical('bootload: not in bootloader.  Version: ' + hex(version))
            raise ValueError('Not in bootloader')

        gv.logger.info('in bootloader with version: ' + hex(version))
        for addr, bin in bin_data:
            addr = int(addr) >> 1 # addresses are byte addresses in binary....convert to inst address
            if addr == 0x400: # write after verification
                bin_0400 = bin
                break
        try:
            if bin_0400 != 0: # make sure it exists
                pass
        except:
            gv.logger.critical('bootload: missing start code and crc')
            raise ValueError('Image missing crc and seal code')

        fail = True
        for i in range(3):
            gv.logger.info('zorch try: ' + str(i+1))
            i2c_read(address, 8)
            time.sleep(2) # let erase complete
            blank = read_blank_check(address)
            if blank == 1:
                fail = False
                break

        if fail:
            gv.logger.critical('bootload: could not zorch')
            raise ValueError('Failed to erase image')

        # write flash
        gv.logger.info('write flash')
        pc_address_b = binascii.unhexlify('0440')
        i2c_structure_write('H', address, 1, pc_address_b)
        pc_address = 0x0440
#        print 'write_address: ' + hex(pc_address)
        for addr, bin in bin_data:
            addr = int(addr) >> 1 # addresses are byte addresses in binary....convert to inst address
            if addr == 0x400: # write after verification
                continue
#            print 'addr: ' + hex(addr) + ' bin: 0x' + binascii.hexlify(bin)
            if pc_address != addr:
                h_addr = "{0:0{1}x}".format(addr,4) # 0 fill four byte hex without leading 0x
                pc_address_b = binascii.unhexlify(h_addr)
                i2c_structure_write('H', address, 1, pc_address_b)
                pc_address = int(h_addr, 16)
#                print 'write_address: ' + hex(pc_address)
            for i in range(4): # write as four separate chunks of 16B each encoding which chunk in register
                i2c_structure_write('HHHHHHHH', address, (i<<4)|2, bin[16*i:16*i+16], 0, True)
            i2c_read(address, 5)
            pc_address += 32

        # validate flash
        fails = 0
        gv.logger.info('validate flash')
        pc_address_b = binascii.unhexlify('0440')
        i2c_structure_write('H', address, 1, pc_address_b)
        pc_address = 0x440
#        print 'write_address: ' + hex(pc_address)
        for addr, bin in bin_data:
            addr = int(addr) >> 1 # addresses are byte addresses in binary....convert to inst address
            if addr == 0x400: # not yet written
                continue
#            print 'addr: ' + hex(addr) + ' bin: 0x' +binascii.hexlify(bin)
            if pc_address != addr:
                h_addr = "{0:0{1}x}".format(addr,4) # 0 fill four byte hex without leading 0x
                pc_address_b = binascii.unhexlify(h_addr)
                i2c_structure_write('H', address, 1, pc_address_b)
                pc_address = int(h_addr, 16)
#                print 'write_address: ' + hex(pc_address)
            fails += validate_line(address, pc_address, bin)
            pc_address += 32

        if fails != 0:
            gv.logger.critical('bootload: failed to validate image fails: ' + str(fails))
            raise ValueError('bootloader failed to validate image')

        # write crc, seal code and signal that we are ok
        fail = True
        for tries in range(3):
            gv.logger.info('write crc, seal code try: ' + str(tries+1))
#            print 'addr: ' + hex(0x400) + ' bin: 0x' + binascii.hexlify(bin_0400)
            pc_address_b = binascii.unhexlify('0400')
            i2c_structure_write('H', address, 1, pc_address_b)
#            print 'write_address: 0x400'
            for i in range(4): # write as four separate chunks of 16B each encoding which chunk in register
                i2c_structure_write('HHHHHHHH', address, (i<<4)|2, bin_0400[16*i:16*i+16], 0, True)
            i2c_read(address, 5)
            try:
                pc_address_b = binascii.unhexlify('0400') # undo auto increment
                i2c_structure_write('H', address, 1, pc_address_b)
#                print 'write_address: 0x400'
                if validate_line(address, 0x400, bin_0400) == 0:
                    fail = False
                    break
            except:
                pass

        if fail:
            gv.logger.critical('bootload: could not finalize image')
            raise ValueError('bootloader failed to finalize image')

        # jump to application
        gv.logger.info('jump to application ')
        i2c_read(address, 6)
        try:
            del gv.in_bootloader[0x60-address]
        except:
            pass
