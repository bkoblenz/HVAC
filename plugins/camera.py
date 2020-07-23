#!/usr/bin/env python
# -*- coding: utf-8 -*-

from blinker import signal
import web, json, time
import gv  # Get access to ospi's settings, gv = global variables
from urls import urls  # Get access to ospi's URLs
from ospi import template_render
from webpages import ProtectedPage, process_page_request, load_and_save_remote, jsave
from helpers import usb_reset
import picamera
from time import sleep
import os
import subprocess
import errno
import base64

# Read in the parameters for this plugin from it's JSON file
def load_params():
    global params
    try:
        with open('./data/camera.json', 'r') as f:  # Read the settings from file
            gv.plugin_data['ca'] = json.load(f)
    except IOError: #  If file does not exist create file with defaults.
        gv.plugin_data['ca'] = {
            'enable_camera': 'on',
            'zone_pic': 'off',
            'current_pic': 'off',
            'resolution': '320x240',
            'sleep_time': 5,
        }
        with open('./data/camera.json', 'w') as f:
            json.dump(gv.plugin_data['ca'], f)

#### change outputs when blinker signal received ####
def on_zone_change(arg): #  arg is just a necessary placeholder.
    """Take a picture on zone change."""

    if gv.plugin_data['ca']['zone_pic'] == 'off':
        return
    sleep(gv.plugin_data['ca']['sleep_time']-2)
    take_picture('camera.jpg', False)

def take_picture(file, update_json=True):
    """ Take a picture and store in image_path/file"""

    try:
        subprocess.call(['rm', './static/images/camera_temp.jpg'])
        with picamera.PiCamera() as camera:
            xpos = gv.plugin_data['ca']['resolution'].find('x')
            width = int(gv.plugin_data['ca']['resolution'][:xpos])
            height = int(gv.plugin_data['ca']['resolution'][xpos+1:])
            camera.resolution = (width, height)
            camera.start_preview()
            sleep(2)
            camera.capture(os.path.join(image_path, file))
    except:
        try:
            out_file = os.path.join(image_path, file)
            usb_reset('camera')
            cmd = ['fswebcam', '--no-banner', '-r', gv.plugin_data['ca']['resolution'], out_file]
            subprocess.call(cmd)
            with open('./static/images/camera.jpg', mode='rb') as file: # b is important -> binary
                pass
        except:
            if update_json:
                gv.plugin_data['ca']['enable_camera'] = 'missing'
                jsave(gv.plugin_data['ca'], 'camera')
            return
    if update_json and gv.plugin_data['ca']['enable_camera'] == 'missing':
        gv.plugin_data['ca']['enable_camera'] = 'on'
        jsave(gv.plugin_data['ca'], 'camera')


load_params()
image_path = os.path.join('.', 'static', 'images')

try:
    os.makedirs(image_path)
except OSError as exc:  # Python >2.5
    if exc.errno == errno.EEXIST and os.path.isdir(image_path):
        pass
    else:
        raise

try:
    # Add a new url to open the data entry page if camera working
    if 'plugins.camera' not in urls:
        urls.extend(['/ca', 'plugins.camera.view_camera',
            '/cau', 'plugins.camera.change_camera',
            '/cap', 'plugins.camera.pic', 
            '/captz', 'plugins.camera.ptz']) 

        # Add this plugin to the home page plugins menu
        gv.plugin_menu.append(['Camera', '/ca'])

    take_picture('camera.jpg')

except:
    pass

zones = signal('zone_change')
zones.connect(on_zone_change)

################################################################################
# Web pages:                                                                   #
################################################################################

class view_camera(ProtectedPage):
    """Load an html page for entering sensor parameters"""

    def GET(self):
        qdict = web.input()
        if 'substation' not in qdict:
            qdict['substation'] = str(gv.substation_index)
        subid = int(qdict['substation'])
        subprocess.call(['rm', 'static/images/camera_temp.jpg'])
        if process_page_request('view_camera', qdict):
            if gv.plugin_data['ca']['enable_camera'] == 'on':
                subprocess.call(['cp', 'static/images/camera.jpg', 'static/images/camera_temp.jpg'])
            return template_render.camera(0, gv.plugin_data['ca'])
        elif 'substation' not in qdict:
            raise web.seeother('/suslv?head=Cameras&continuation=ca')
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'susldr', 'data', {'camera':1, 'cai':1})
                if data['cai']:
                    try:
                        with open('static/images/camera_temp.jpg', mode='wb') as file: # b is important -> binary
                            file.write(base64.b64decode(data['cai']))
                    except Exception as ex:
                        gv.logger.critical('Could not write camera_temp.jpg.  ex: ' + str(ex))
                return template_render.camera(subid, data['camera'])
            except Exception as ex:
                gv.logger.exception('view_camera: No response from subordinate: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')

class change_camera(ProtectedPage):
    """Save user input to camera.json file"""
    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('change_camera', qdict) or subid == 0:
            checkboxes = ['enable_camera', 'zone_pic','current_pic']
            for c in checkboxes:
                if c != 'enable_camera' or gv.plugin_data['ca'][c] != 'missing':
                    if c not in qdict:
                        qdict[c] = 'off'
                else:
                    qdict[c] = 'missing' # dont change 'missing' attribute of enable_camera

            for c in checkboxes:
                if gv.plugin_data['ca'][c] != qdict[c]:
                    gv.plugin_data['ca'][c] = qdict[c]
                    changed = True

            intparams = ['sleep_time']
            for p in intparams:
                if gv.plugin_data['ca'][p] != int(qdict[p]):
                    gv.plugin_data['ca'][p] = int(qdict[p])
                    changed = True

            sparams = ['resolution']
            for p in sparams:
                if gv.plugin_data['ca'][p] != qdict[p]:
                    gv.plugin_data['ca'][p] = qdict[p]
                    take_picture('camera.jpg', False)
                    changed = True

            if changed:
                with open('./data/camera.json', 'w') as f:  # write the settings to file
                    json.dump(gv.plugin_data['ca'], f)
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cau', 'substation', '0')
            except Exception as ex:
                gv.logger.info('change_camera: No response from subordinate: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/ca')

class pic(ProtectedPage):
    """Take a picture"""

    def GET(self):
        qdict = web.input()
        subid = 0 if 'substation' not in qdict else int(qdict['substation'])
        if process_page_request('pic', qdict) or subid == 0:
            take_picture('camera.jpg', False)
            if 'substation' in qdict:
                web.header('Content-Type', 'application/json')
                return json.dumps([])
        else:
            try:
                subid, data = load_and_save_remote(qdict, [], 'cap', 'substation', '0')
            except Exception as ex:
                gv.logger.info('pic: No response from subordinate: ' +
                               gv.plugin_data['su']['subinfo'][subid]['name'] + ' Exception: ' + str(ex))
                gv.plugin_data['su']['subinfo'][subid]['status'] = 'unreachable'
                raise web.seeother('/unreachable')
        raise web.seeother('/ca')

class ptz(ProtectedPage):
    """Manage Pan-Tilt-Zoom for camera"""

    def GET(self):
        qdict = web.input()
        print 'ptz operation: ', qdict['param']
        raise web.seeother('/ca')
