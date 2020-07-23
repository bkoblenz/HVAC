# !/usr/bin/env python

# this plugin checks sha on github and updates SIP from github

import time
import subprocess
import sys
import traceback
import json
import web
import gv  # Get access to SIP's settings
from urls import urls  # Get access to SIP's URLsimport errno
from sip import template_render
from webpages import ProtectedPage, process_page_request, load_and_save_remote
from helpers import restart, reboot, propagate_to_substations
import urllib
import i2c
import glob
import binascii
import operator
from threading import Thread

# Add a new url to open the data entry page.
if 'plugins.system_update' not in urls:
    urls.extend(['/UPs', 'plugins.system_update.status_page',
             '/UPu', 'plugins.system_update.update_page'
             ])

    # Add this plugin to the home page plugins menu
    gv.plugin_menu.append(['System update', '/UPs'])


class StatusChecker():
    def __init__(self):

        self.status = {
            'ver_str': gv.ver_str,
            'ver_date': gv.ver_date,
            'status': '',
            'remote': 'None!',
            'main': gv.sd['main'],
            'can_update': False,
            'update_fw': ''}

        self._sleep_time = 0

    def add_status(self, msg, start=False):
        if self.status['status'] and not start:
            self.status['status'] += '\n' + msg
        else:
            self.status['status'] = msg

    def start_status(self, msg):
        self.add_status(msg, True)

    def update(self):
        self._sleep_time = 0

    def _sleep(self, secs):
        self._sleep_time = secs
        while self._sleep_time > 0:
            time.sleep(1)
            self._sleep_time -= 1

    def update_rev_data(self):
        """Returns the update revision data."""

        command = 'git remote update'
        subprocess.call(command.split())

        command = 'git config --get remote.origin.url'
        remote = subprocess.check_output(command.split()).strip()
        if remote:
            self.status['remote'] = remote

        command = 'git log -1 origin/main --format=%cd --date=short'
        new_date = subprocess.check_output(command.split()).strip()

        command = 'git rev-list origin/main --count --first-parent'
        new_revision = int(subprocess.check_output(command.split()))

        command = 'git log HEAD..origin/main --oneline'
        changes = '  ' + '\n  '.join(subprocess.check_output(command.split()).split('\n'))

        latest_fw = latest_firmware()
        self.status['update_fw'] = latest_fw if len(firmware_update_required(latest_fw)) != 0 else ''

        if new_revision == gv.revision and new_date == gv.ver_date:
            if self.status['update_fw'] == '':
                self.start_status(_('Up-to-date.'))
            else:
                self.start_status(_('Firmware update to version ' + self.status['update_fw'] + ' available.'))
            self.status['can_update'] = False
        elif new_revision > gv.revision:
            self.start_status(_('New version is available!'))
            self.add_status(_('Available revision')+': %d.%d.%d (%s)' % (gv.major_ver, gv.minor_ver, new_revision - gv.old_count, new_date))
            self.add_status(_('Changes')+':\n' + changes)
            self.status['can_update'] = True
            if self.status['update_fw'] != '':
                self.add_status(_('Firmware update to version ' + self.status['update_fw'] + ' available.'))
        else:
            self.start_status(_('Currently running revision')+': %d (%s)' % ((gv.revision - gv.old_count), gv.ver_date))
            self.add_status(_('Available revision')+': %d (%s)' % ((new_revision - gv.old_count), new_date))
            self.status['can_update'] = False
            if self.status['update_fw'] != '':
                self.add_status(_('Firmware update to version ' + self.status['update_fw'] + ' available.'))

    def run(self):

        try:
            self.status['status'] = ''

        except Exception:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            err_string = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            self.add_status(_('System update plug-in encountered error')+':\n' + err_string)

checker = StatusChecker()

################################################################################
# Helper functions:                                                            #
################################################################################

def latest_firmware():
    """Return version string for latest firwmare update avaible.  Otherwise emptry string"""
    # get all versions in ./firmware and determine the latest
    files = glob.glob('./firmware/vsb.*')
    files.sort()
    latest_fw_full = files[-1]
    return latest_fw_full[latest_fw_full.rfind('.')+1:]

def firmware_update_required(fw_ver):
    """Return a list of device addresses where fw_ver is a different rev than what is found on device address,
       and also that the device can be upgraded."""

    updates = []
    boards = i2c.get_vsb_boards()
    for board, version in boards.items():
        if version < 0x22: # no bootloader
            continue
        elif hex(version) == fw_ver: # latest
            continue
        updates.append(board)
    return updates

def generate_binary_firmware(rev, kind='vsb'):
    """Process each line in the hex file generating a corresponding array indexed by address with
       the record content padded to an 8 instruction record.
    """
    f32 = 'ff3fff3fff3fff3fff3fff3fff3fff3f'
    bin_f32 = binascii.unhexlify(f32+f32+f32+f32)
    records = {}
    skip_count = 0
    skip_data = False
    with open('./firmware/' + kind + '.' + rev, 'r') as hex_f:
        lines = hex_f.readlines()
        for line in lines:
            if line[0] != ':':
                print 'skipping record: ', line
                continue
            if skip_count > 0:
                skip_count -= 1
                if skip_count == 0:
                    skip_data = False
                continue
            data_bytes = int(line[1:3], 16)
            addr = int(line[3:7], 16)
            type = int(line[7:9], 16)
            hex_data = line[9:9+2*data_bytes]
            checksum = int(line[9+2*data_bytes:], 16)
            while (len(hex_data) < 32):
                hex_data += 'ff'
            for i in range(0,32,4):
                bval = int(hex_data[i+2:i+4], 16) # msb is second instruction byte here
                bval &= 0x3f # mask off two high bits
                new_str = hex(bval)[2:]
                if len(new_str) < 2:
                    new_str = '0' + new_str
                hex_data = hex_data[0:i+2] + new_str + hex_data[i+4:]
            if type == 1: # end of processing
                break
            elif type == 4: # linear address
                if hex_data[0:4] == '0001': # writing control registers...ignore
                    skip_count = 1
                    skip_data = True
                    continue
                elif hex_data[0:4] != '0000':
                    gv.logger.critical('Unexpected type 4: ' + hex_data)
                    return []
            elif type != 0:
                gv.logger.critical('Unexpected hex type in binary file: ' + str(type))
                return []
            if skip_data:
                gv.logger.critical('Unexpected data record in binary file: ' + hex(addr))
                return []
            base_addr = addr & (~0 & 0xffffffc0)  # align to 64B boundary
            if base_addr not in records:
                records[base_addr] = bin_f32
            offset = addr - base_addr
            old_bin_data = records[base_addr]
            old_str_data = binascii.hexlify(old_bin_data)
            if old_str_data[2*offset:2*offset+32] != f32:
                gv.logger.critical('Multiple writes to address: ' + hex(addr) + ' old: 0x' + old_str_data[2*offset:2*offset+32])
                return []
            old_str_data = old_str_data[:2*offset] + hex_data + old_str_data[2*offset+32:]
            records[base_addr] = binascii.unhexlify(old_str_data)

    # delete all instructions that match the result of the zorch
    for k,v in records.items():
        if v == bin_f32:
            del records[k]
    return sorted(records.items(), key=operator.itemgetter(0))

def perform_update():

    try:
        gv.logger.info('perform_update: ' + str(checker.status))

        command = "git config core.filemode true"
        subprocess.call(command.split())

        command = "git checkout main"  # Make sure we are on the main branch
        output = subprocess.check_output(command.split())

        command = "git stash"  # stash any local changes
        output = subprocess.check_output(command.split())

        command = "git fetch"
        output = subprocess.check_output(command.split())

        command = "git merge -X theirs origin/main"
        output = subprocess.check_output(command.split())

        command = "rm sessions/*"
        subprocess.call(command, shell=True, stderr=subprocess.STDOUT)
    except Exception as ex:
        gv.logger.error('perform_update failed: ' + str(ex))

    # If we got new firmware, update that too
    latest_fw = latest_firmware()
    boards_to_update = firmware_update_required(latest_fw)
    if len(boards_to_update) == 0:
        reboot(5)
    binary_rec = generate_binary_firmware(latest_fw)
    if len(binary_rec) == 0:
        gv.logger.error('perform_update failed fw update: ' + latest_fw)
    else:
        for board in boards_to_update:
            gv.logger.info('update fw for board: ' + str(board) + ' to: ' + latest_fw)
            i2c.bootload(0x60+board, binary_rec)
    reboot(5)


################################################################################
# Web pages:                                                                   #
################################################################################

class status_page(ProtectedPage):
    """Load an html page with rev data."""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        if process_page_request('view_system_update', qdict):
            checker.update_rev_data()
            return template_render.system_update(0, checker.status)
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=' + urllib.quote_plus('System Update') + '&continuation=UPs')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'update_status':1})
                return template_render.system_update(subid, data['update_status'])
            except Exception as ex:
                gv.logger.info('view_system_update: No response from subordinate: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

class update_page(ProtectedPage):
    """Update from github and return text message from command line."""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('system_update', qdict) or subid == 0:
            updateall = 0 if 'updateall' not in qdict else int(qdict['updateall'])
            if updateall == 1:
                propagate_to_substations('UPu')
#            t = Thread(target=perform_update, args=()) # do it in background
#            t.start()
            perform_update()
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'UPu', 'substation', '0')
            except Exception as ex:
                try:
                    gv.logger.info('system_update: No response from subordinate: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                    gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                except Exception as ex1:
                    gv.logger.info('system_update: No response  Exception: ' + str(ex1))
                raise web.seeother('/unreachable')
        raise web.seeother('/reboot')
