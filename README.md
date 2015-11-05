Irricloud
====

An enhanced version of the Python Interval Program for OpenSprinkler Pi started by Dan Kimberling built on earlier work.
Some of this program is under the GNU GPL License and other (plugin) parts are Intellectual Property owned by Brian Koblenz.

-----------------------------------------------------------------
Irricloud Program<br/>

GNU GPL License (for all parts except leak detection)<br/>
August 2015

***********
August 29 2015
----------
(Brian)  
1. Add text/email plugin  
2. Add evapotranspiration plugin  
3. Add camera plugin  
4. Add leak detection plugin  
5. Remove proto and signalling example plugins  
6. Remove general access to new plugins to create a fixed environment  
7. Create dynamic monitoring and wireless access configuration  

***********
August 15 2015
----------
(Brian)  
1. Add gv.output_srvals and a gv.output_srvals_lock, so that threads can get a consistent state of stations currently running  
2. Add gv.plugin_data which is a dictionary (index by plugin webpage base) to hold data associated with a plugin    
3. Add gv.nowt to have a struct time of the current time


***********
August 9 2015
----------
(Brian)  
1. Enable master valve to be a station not on first board (templates/options.html)  
2. Make sure station 9 has default of S09 instead of S9 (webpages.py)  
3. Some minor indenting changes in static/scripts/schedule.js    
4. When dynamic water level adjustment is in effect, enable per zone ignoring of the adjustment    


***********
August 6 2015
----------
(Dan)  
1. Pushed file modifications for project rename to GitHub  
2. Renamed main Python file from ospi.py to sip.py  
3. Added ospi.py file as symlink to sip.py for backward compatibility  
4. Renamed related GitHub repositories to SIP  
5. Updated major version number to 3


***********
February 10 2015
----------
(Dan)  
1. Added Plugin Manager plugin  
2. Updated System Updater plugin  
3. Added help button linked to repository wiki  
4. Moved plugins to new repository  
5. Includes Spanish and French translations and related bug fixes

For additional history see https://github.com/Dan-in-CA/SIP

******************************************************
Full credit goes to Dan Kimberling and Ray's Hobby and Samer Albahra for their
incredible work.
******************************************************

For installation instructions see the wiki.

