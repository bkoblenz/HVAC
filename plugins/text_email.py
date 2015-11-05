# !/usr/bin/env python
# this plugins send email at google email

from threading import Thread
from random import randint
import json
import time
import os
import sys
import traceback

import web
import gv  # Get access to SIP's settings
from urls import urls  # Get access to SIP's URLs
from sip import template_render
from webpages import ProtectedPage
from helpers import timestr

from email import Encoders
import smtplib
from email.MIMEMultipart import MIMEMultipart
from email.MIMEBase import MIMEBase
from email.MIMEText import MIMEText

# Add a new url to open the data entry page.
if 'plugins.text_email' not in urls:
    urls.extend(['/tea', 'plugins.text_email.settings',
             '/tej', 'plugins.text_email.settings_json',
             '/teu', 'plugins.text_email.update'])

    # Add this plugin to the home page plugins menu
    gv.plugin_menu.append(['Text/Email settings', '/tea'])

################################################################################
# Main function loop:                                                          #
################################################################################

def dtstring(start=None):
    if start == None:
        start = time.localtime(time.time())
    if gv.sd['tu'] == 'F':
        t = time.strftime("%m/%d/%Y at %H:%M:%S", start)
    else:
        t = time.strftime("%d.%m.%Y at %H:%M:%S", start)
    return t

class EmailSender(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.daemon = True
        self.start()
        self.status = ''
        self._sleep_time = 0

    def start_status(self, msg):
        self.status = ''
        self.add_status(msg)

    def add_status(self, msg):
        if self.status:
            self.status += '\n' + msg
        else:
            self.status = msg
        gv.plugin_data['te']['status'] = self.status
        gv.logger.debug(msg)

    def update(self):
        self._sleep_time = 0

    def _sleep(self, secs):
        self._sleep_time = secs
        while self._sleep_time > 0:
            time.sleep(1)
            self._sleep_time -= 1

    def try_mail(self, subject, text, attachment=None):
        try:
            email(subject, text, attachment)  # send email with attachment from
            self.start_status('Email was sent: ' + text)
        except Exception as err:
            self.start_status('Email was not sent! ' + str(err))

    def run(self):
        time.sleep(randint(3, 10))  # Sleep some time to prevent printing before startup information

        gv.plugin_data['te'] = get_email_options()  # load data from file
        subject = "Report from Irricloud"  # Subject in email
        last_rain = 0
        was_running = False

        self.start_status('Email plugin is started')

        if gv.plugin_data['te']['telog'] != 'off':          # if telog send email is enable (on)
            body = ('On ' + dtstring() + ': System was powered on.')
            self.try_mail(subject, body, "data/log.json")

        while True:
            try:
                # send if rain detected
                if gv.plugin_data['te']['terain'] != 'off':             # if terain send email is enable (on)
                    if gv.sd['rs'] != last_rain:            # send email only 1x if  gv.sd rs change
                        last_rain = gv.sd['rs']

                        if gv.sd['rs'] and gv.sd['urs']:    # if rain sensed and use rain sensor
                            body = ('On ' + dtstring() + ': System detected rain.')
                            self.try_mail(subject, body)    # send email without attachments

                # send if leak triggered
                if gv.plugin_data['te']['teleak'] != 'off' and \
                   'ld' in gv.plugin_data:
                    # TODO figure out to report leaks
                    pass

                if gv.plugin_data['te']['terun'] != 'off':              # if terun send email is enable (on)
                    # todo this does not look right
                    running = False
                    with gv.rs_lock:
                        for sid in range(gv.sd['nst']):          # Check each station once a second
                            if gv.srvals[sid]:  # if this station is on
                                running = True
                                was_running = True

                    if was_running and not running:
                        was_running = False
                        if gv.lrun[1] == 98:
                            pgr = 'Run-once'
                        elif gv.lrun[1] == 99:
                            pgr = 'Manual'
                        else:
                            pgr = str(gv.lrun[1])

                        dur = str(timestr(gv.lrun[2]))
                        start = time.gmtime(gv.now - gv.lrun[2])

                        body = 'On ' + dtstring() + ': System last run: ' + 'Station ' + str(gv.lrun[0]+1) + \
                               ', Program ' + pgr + \
                               ', Duration ' + dur + \
                               ', Start time ' + dtstring(start)

                        self.try_mail(subject, body)     # send email without attachment

                if gv.now % 5 == 0 and gv.sd['master'] and \
                      'su' in gv.plugin_data and gv.plugin_data['te']['tesub'] != 'off':  # if tesub, send email if substation availability changes
                    give_notice = []
                    if 'tesubstatus' not in gv.plugin_data['te']:
                        gv.plugin_data['te']['tesubstatus'] = [{}]
                    for i in range(1,len(gv.plugin_data['su']['subinfo'])):
                        if i >= len(gv.plugin_data['te']['tesubstatus']):
                            gv.plugin_data['te']['tesubstatus'].append({'oktime':gv.now, 'notoktime':gv.now, 'noticetime':gv.now})
                        status = gv.plugin_data['su']['subinfo'][i]['status']
                        if status == 'ok':
                            gv.plugin_data['te']['tesubstatus'][i]['oktime'] = gv.now
                            td = gv.now - gv.plugin_data['te']['tesubstatus'][i]['notoktime']
                            if td > 60 and gv.plugin_data['te']['tesubstatus'][i]['notoktime'] > gv.plugin_data['te']['tesubstatus'][i]['noticetime']:
                                give_notice.append([i,status])
                                gv.plugin_data['te']['tesubstatus'][i]['noticetime'] = gv.now
                        else:
                            gv.plugin_data['te']['tesubstatus'][i]['notoktime'] = gv.now
                            td = gv.now - gv.plugin_data['te']['tesubstatus'][i]['oktime']
                            if td > 60 and gv.plugin_data['te']['tesubstatus'][i]['oktime'] > gv.plugin_data['te']['tesubstatus'][i]['noticetime']:
                                give_notice.append([i,status])
                                gv.plugin_data['te']['tesubstatus'][i]['noticetime'] = gv.now

                    if len(give_notice) > 0:
                        body = ''
                        for e in give_notice:
                            body += 'Substation ' + gv.plugin_data['su']['subinfo'][e[0]]['name'] + ' changed status to: ' + e[1]

                        self.try_mail(subject, body)     # send email without attachment

                self._sleep(1)

            except Exception:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                err_string = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
                self.add_status('Text/Email plugin encountered error: ' + err_string)
                self._sleep(60)


checker = EmailSender()
gv.plugin_data['te'] = {
    'teusr': '',
    'tepwd': '',
    'teleak': 'off',
    'telog': 'off',
    'terain': 'off',
    'terun': 'off',
    'tesub': 'off',
    'status': checker.status
}

for i in range(5):
    gv.plugin_data['te']['teadr'+str(i)] = ''
    gv.plugin_data['te']['tesmsnbr'+str(i)] = ''
    gv.plugin_data['te']['tesmsprovider'+str(i)] = ''

sms_carrier_map = {
    'AT&T':'txt.att.net',
    'Cingular':'cingularme.com',
    'Cricket':'mmm.mycricket.com',
    'Nextel':'messaging.nextel.com',
    'Sprint':'messaging.sprintpcs.com',
    'T-Mobile':'tmomail.net',
    'TracFone':'txt.att.net',
    'U.S. Cellular':'email.uscc.net',
    'Verizon':'vtext.com',
    'Virgin':'vmobl.com'
}

################################################################################
# Helper functions:                                                            #
################################################################################


def get_email_options():
    """Returns the defaults data form file."""

    try:
        with open('./data/text_email.json', 'r') as f:  # Read the settings from file
            file_data = json.load(f)

        for key, value in file_data.iteritems():
            if key in gv.plugin_data['te'] and key != 'status':
                gv.plugin_data['te'][key] = value

    except Exception:
        pass

    return gv.plugin_data['te']


def email(subject, text, attach=None):
    """Send email with with attachments"""

    recipients_list = [gv.plugin_data['te']['teadr'+str(i)] for i in range(5) if gv.plugin_data['te']['teadr'+str(i)]!='']
    sms_recipients_list = [gv.plugin_data['te']['tesmsnbr'+str(i)] + '@' + sms_carrier_map[gv.plugin_data['te']['tesmsprovider'+str(i)]] \
        for i in range(5) if gv.plugin_data['te']['tesmsnbr'+str(i)]!='']
    if gv.plugin_data['te']['teusr'] != '' and gv.plugin_data['te']['tepwd'] != '':
        gmail_user = gv.plugin_data['te']['teusr']          # User name
        gmail_name = gv.sd['name']                          # SIP name
        gmail_pwd = gv.plugin_data['te']['tepwd']           # User password
        mailServer = smtplib.SMTP("smtp.gmail.com", 587)
        mailServer.ehlo()
        mailServer.starttls()
        mailServer.ehlo()
        mailServer.login(gmail_user, gmail_pwd)
        #--------------
        msg = MIMEMultipart()
        msg['From'] = gmail_name
        msg['Subject'] = subject
        msg.attach(MIMEText(text))

        for recip in sms_recipients_list: # can only do one text message at a time
            msg['To'] = recip
            mailServer.sendmail(gmail_name, recip, msg.as_string())

        if len(recipients_list) > 0:
            recipients_str = ', '.join(recipients_list)
            msg['To'] = recipients_str
            if attach is not None:              # If insert attachments
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(open(attach, 'rb').read())
                Encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(attach))
                msg.attach(part)
            mailServer.sendmail(gmail_name, recipients_list, msg.as_string())   # name + e-mail address in the From: field

        mailServer.quit()
    else:
        raise Exception('E-mail plug-in is not properly configured!')

################################################################################
# Web pages:                                                                   #
################################################################################

class settings(ProtectedPage):
    """Load an html page for entering text/email settings."""

    def GET(self):
        return template_render.text_email(get_email_options())


class settings_json(ProtectedPage):
    """Returns plugin settings in JSON format."""

    def GET(self):
        web.header('Access-Control-Allow-Origin', '*')
        web.header('Content-Type', 'application/json')
        return json.dumps(get_email_options())


class update(ProtectedPage):
    """Save user input to text_email.json file."""

    def GET(self):
        qdict = web.input()
        if 'telog' not in qdict:
            gv.plugin_data['te']['telog'] = 'off'
        if 'terain' not in qdict:
            gv.plugin_data['te']['terain'] = 'off'
        if 'terun' not in qdict:
            gv.plugin_data['te']['terun'] = 'off'
        if 'tesub' not in qdict:
            gv.plugin_data['te']['tesub'] = 'off'
        if 'teleak' not in qdict:
            gv.plugin_data['te']['teleak'] = 'off'
        for k,v in qdict.iteritems():
            gv.plugin_data['te'][k] = v
        with open('./data/text_email.json', 'w') as f:  # write the settings to file
            json.dump(gv.plugin_data['te'], f)
        raise web.seeother('/')
