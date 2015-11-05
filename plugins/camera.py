#!/usr/bin/env python

from blinker import signal
import web, json, time
import gv  # Get access to ospi's settings, gv = global variables
from urls import urls  # Get access to ospi's URLs
from ospi import template_render
from webpages import ProtectedPage
import picamera
from time import sleep
import os
import errno

params = {}

# Read in the parameters for this plugin from it's JSON file
def load_params():
    global params
    try:
        with open('./data/camera.json', 'r') as f:  # Read the settings from file
            params = json.load(f)
    except IOError: #  If file does not exist create file with defaults.
        params = {
            'zone_pic': 'off',
            'current_pic': 'off',
            'sleep_time': 5,
        }
        with open('./data/camera.json', 'w') as f:
            json.dump(params, f)
    return params

#### change outputs when blinker signal received ####
def on_zone_change(arg): #  arg is just a necessary placeholder.
    """Take a picture on zone change."""

    if params['zone_pic'] == 'off':
        return
    sleep(params['sleep_time']-2)
    take_picture('camera.jpg')

def take_picture(file):
    """ Take a picture and store in image_path/file"""

    with picamera.PiCamera() as camera:
        camera.start_preview()
        sleep(2)
        camera.capture(os.path.join(image_path,file))


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
    take_picture('camera.jpg')
    # Add a new url to open the data entry page if camera working
    if 'plugins.camera' not in urls:
        urls.extend(['/ca', 'plugins.camera.settings',
            '/caj', 'plugins.camera.settings_json',
            '/cau', 'plugins.camera.update',
            '/cap', 'plugins.camera.pic', 
            '/captz', 'plugins.camera.ptz']) 

        # Add this plugin to the home page plugins menu
        gv.plugin_menu.append(['Camera', '/ca'])

except:
    pass

zones = signal('zone_change')
zones.connect(on_zone_change)

################################################################################
# Web pages:                                                                   #
################################################################################

class settings(ProtectedPage):
    """Load an html page for entering relay board adjustments"""

    def GET(self):
        with open('./data/camera.json', 'r') as f:  # Read the settings from file
            params = json.load(f)
        return template_render.camera(params)


class settings_json(ProtectedPage):
    """Returns plugin settings in JSON format"""

    def GET(self):
        web.header('Access-Control-Allow-Origin', '*')
        web.header('Content-Type', 'application/json')
        return json.dumps(params)


class update(ProtectedPage):
    """Save user input to camera.json file"""

    def GET(self):
        qdict = web.input()
        changed = False
        checkboxes = ['zone_pic','current_pic']
        for c in checkboxes:
            if c not in qdict:
                qdict[c] = 'off'

        for c in checkboxes:
            if params[c] != qdict[c]:
                params[c] = qdict[c]
                changed = True

        intparams = ['sleep_time']
        for p in intparams:
            if params[p] != int(qdict[p]):
               params[p] = int(qdict[p])
               changed = True

        if changed:
           with open('./data/camera.json', 'w') as f:  # write the settings to file
              json.dump(params, f)

        raise web.seeother('/')

class pic(ProtectedPage):
    """Take a picture"""

    def GET(self):
        take_picture('camera.jpg')
        raise web.seeother('/ca')

class ptz(ProtectedPage):
    """Manage Pan-Tilt-Zoom for camera"""

    def GET(self):
        qdict = web.input()
        print 'ptz operation: ', qdict['param']
        raise web.seeother('/ca')
