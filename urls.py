# !/usr/bin/env python
# -*- coding: utf-8 -*-

 #### urls is used by web.py. When a GET request is received, the corresponding class is executed.
urls = [
    '/',  'webpages.view_log',
    '/vo', 'webpages.view_options',
    '/co', 'webpages.change_options',
    '/vl', 'webpages.view_log',
    '/cl', 'webpages.clear_log',
    '/wl', 'webpages.water_log',
    '/login',  'webpages.login',
    '/logout',  'webpages.logout',
]
