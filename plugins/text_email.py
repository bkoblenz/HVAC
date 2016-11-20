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
from webpages import WebPage, ProtectedPage, message_base, validate_remote
from helpers import timestr, dtstring, read_log, get_external_ip
import urllib
import urllib2

from email import Encoders
import smtplib
from email.MIMEMultipart import MIMEMultipart
from email.MIMEBase import MIMEBase
from email.MIMEText import MIMEText

# Add a new url to open the data entry page.
if 'plugins.text_email' not in urls:
    urls.extend(['/tea', 'plugins.text_email.settings',
             '/tej', 'plugins.text_email.settings_json',
             '/tereq', 'plugins.text_email.email_request',
             '/teu', 'plugins.text_email.update'])

    # Add this plugin to the home page plugins menu
    gv.plugin_menu.append(['Text/Email settings', '/tea'])

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
        gv.plugin_data['te'] = {}
        pass

    return gv.plugin_data['te']


def email(subject, text, attach=None):
    """Send email with with attachments"""

#    recipients_list = [gv.plugin_data['te']['teadr'+str(i)] for i in range(5) if gv.plugin_data['te']['teadr'+str(i)]!='']
    recipients_list = [gv.sd['teadr'+str(i)] for i in range(5) if gv.sd['teadr'+str(i)]!='']
#    sms_recipients_list = [gv.plugin_data['te']['tesmsnbr'+str(i)] + '@' + sms_carrier_map[gv.plugin_data['te']['tesmsprovider'+str(i)]] \
#        for i in range(5) if gv.plugin_data['te']['tesmsnbr'+str(i)]!='']
    sms_recipients_list = [gv.sd['tesmsnbr'+str(i)] + '@' + sms_carrier_map[gv.sd['tesmsprovider'+str(i)]] \
        for i in range(5) if gv.sd['tesmsnbr'+str(i)]!='']
    if gv.sd['teuser'] != '' and gv.sd['tepassword'] != '':
        gmail_user = gv.sd['teuser']          # User name
        gmail_name = gv.sd['name']                          # SIP name
        gmail_pwd = gv.sd['tepassword']           # User password
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
            gv.logger.debug('mail0 recip: ' + recip)
            mailServer.sendmail(gmail_name, recip, msg.as_string())

        if len(recipients_list) > 0:
            recipients_str = ', '.join(recipients_list)
            msg['To'] = recipients_str
            gv.logger.debug('mail1 recip: ' + recipients_str)
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
# Main function loop:                                                          #
################################################################################

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

    def log_email(self, subject, body, status):
        start = time.gmtime(gv.now)
        if gv.sd['lg']:
            logline = '{' + time.strftime('"time":"%H:%M:%S","date":"%Y-%m-%d', start) + \
                      '", "subject":"' + subject + '", "body":"' + body + \
                      '", "status":"' + status + '"}'
            lines = []
            lines.append(logline + '\n')
            log = read_log('elog')
            for r in log:
                lines.append(json.dumps(r) + '\n')
            with open('./data/elog.json', 'w') as f:
                if gv.sd['lr']:
                    f.writelines(lines[:gv.sd['lr']])
                else:
                    f.writelines(lines)

    def try_mail(self, subject, body, attachment=None):
        try:
            if gv.sd['master']:
                email(subject, gv.sd['name'] + ': ' + body, attachment)
                self.start_status('Email was sent: ' + body)
                gv.logger.debug('email sent.  body: ' + body)
                self.log_email(subject, body, 'Sent')
            else:
                parameters = {'subject':subject, 'body':gv.sd['name'] + ': ' + body}
                data = message_base('tereq', parameters)
                if data['status'] == 0:
                    self.start_status('Email was sent: ' + body)
                    gv.logger.debug('email sent.  body: ' + body)
                    self.log_email(subject, body, 'Sent')
                else:
                    self.start_status('Email was not sent! ')
                    gv.logger.debug('email not sent.  status: ' + str(data['status']) + ' body: ' + body)
                    self.log_email(subject, body, 'Unsent')
        except Exception as err:
            self.start_status('Email was not sent! ' + str(err))
            gv.logger.exception('email not sent.  body: ' + body)
            self.log_email(subject, body, 'Unsent')

    def run(self):
        gv.plugin_data['te'] = get_email_options()  # load data from file
        gv.plugin_data['te']['tesender'] = self
        subject = "Report from Irricloud"  # Subject in email
        last_rain = 0
        was_running = False
        time.sleep(15)  # Sleep to let ip stuff settle
        self.start_status('Email plugin is started')

        if gv.sd['tepoweron']:
            body = 'System'
            try:
                ext_ip_addr = get_external_ip()
                if ext_ip_addr != '':
                    cur_ip = get_ip()
                    body += ' at Local IP: ' + cur_ip
                    if gv.sd['htp'] != 0 and gv.sd['htp'] != 80:
                        body += ':' + str(gv.sd['htp'])
                    body += ' External IP: ' + ext_ip_addr + ':' + str(gv.sd['external_htp'])
                gv.external_ip = ext_ip_addr
            except:
                pass
            body += ' was powered on.'
            self.try_mail(subject, body)
#            self.try_mail(subject, body, "data/log.json")

        while True:
            try:
                # send if rain detected
#                if gv.plugin_data['te']['terain'] != 'off':             # if terain send email is enable (on)
#                    if gv.sd['rs'] != last_rain:            # send email only 1x if  gv.sd rs change
#                        last_rain = gv.sd['rs']
#
#                        if gv.sd['rs'] and gv.sd['urs']:    # if rain sensed and use rain sensor
#                            body = 'System detected rain.'
#                            self.try_mail(subject, body)    # send email without attachments

                now = gv.now
                if now % 5 == 0 and gv.sd['master'] and \
                      'su' in gv.plugin_data and gv.sd['tesu']:  # if tesu, send email if substation availability changes
                    give_notice = []
                    if 'tesubstatus' not in gv.plugin_data['te']:
                        gv.plugin_data['te']['tesubstatus'] = [{}]
                    for i in range(1,len(gv.plugin_data['su']['subinfo'])):
                        if i >= len(gv.plugin_data['te']['tesubstatus']):
                            gv.plugin_data['te']['tesubstatus'].append({'oktime':now, 'notoktime':now, 'noticetype':'ok'})
                        status = gv.plugin_data['su']['subinfo'][i]['status']
                        # notify of ok quickly, but let drops take 5 mins
#                        print 'check: '  + str(i) + ' ' + gv.plugin_data['su']['subinfo'][i]['name'] + ' status: ' + status
                        if status == 'ok':
                            gv.plugin_data['te']['tesubstatus'][i]['oktime'] = now
                            td = now - gv.plugin_data['te']['tesubstatus'][i]['notoktime']
#                            print 'td: '  + str(td) + ' noticetype: ' + gv.plugin_data['te']['tesubstatus'][i]['noticetype']
                            if td > 60 and gv.plugin_data['te']['tesubstatus'][i]['noticetype'] != status:
                                give_notice.append([i,status])
                                gv.plugin_data['te']['tesubstatus'][i]['noticetype'] = status
                        else:
                            gv.plugin_data['te']['tesubstatus'][i]['notoktime'] = now
                            td = now - gv.plugin_data['te']['tesubstatus'][i]['oktime']
#                            print 'td: '  + str(td) + ' noticetype: ' + gv.plugin_data['te']['tesubstatus'][i]['noticetype']
                            if td > 300 and gv.plugin_data['te']['tesubstatus'][i]['noticetype'] != status:
                                give_notice.append([i,status])
                                gv.plugin_data['te']['tesubstatus'][i]['noticetype'] = status

                    if len(give_notice) > 0:
                        body = ''
                        for e in give_notice:
                            body += 'Substation ' + gv.plugin_data['su']['subinfo'][e[0]]['name'] + ' changed status to: ' + e[1]
                        self.try_mail(subject, body)

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

class email_request(WebPage): # does its own security checking
    """A slave is asking the master to send email on its behalf."""

    def GET(self):
        qdict = web.input()
        try:
            ddict = json.loads(qdict['data'])
        except:
            raise web.unauthorized()

        validate_remote(ddict) # may raise unauthorized

        subj = ''
        body = ''
        try:
            subj = ddict['subject']
            body = ddict['body']
            if gv.sd['master']:
                email(subj, body)
                ret_str = json.dumps({'status':0})
            else:
                gv.logger.error('mail not sent by slave body: ' + body)
                ret_str = json.dumps({'status':1})
        except:
            gv.logger.exception('could not send email. subject: ' + subj + ' body: ' + body)
            ret_str = json.dumps({'status':2})
        web.header('Content-Type', 'application/json')
        return ret_str
