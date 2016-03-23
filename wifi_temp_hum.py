# !/usr/bin/env python
# -*- coding: utf-8 -*-

# see http://electronics.ozonejunkie.com/2014/12/opening-up-the-usr-htw-wifi-temperature-humidity-sensor/    (10.10.100.254 default)
import socket
import math
import time

TCP_ADDR = '192.168.1.107'
TCP_PORT = 8899

PACK_LEN = 11

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(30)
while True:
    try:
        s.connect((TCP_ADDR, TCP_PORT))
        break
    except:
        print 'Retry connect....'
        time.sleep(10)

bytes_data = [0] * PACK_LEN

b = 17.67 # see wikipedia
c = 243.5
def gamma(t,rh):
    return (b*t / (c+t)) + math.log(rh/100.0)

def dewpoint(t,rh):
    g = gamma(t,rh)
    return c*g / (b-g)

def get_temp_hum():
    try:
        str_data = s.recv(PACK_LEN)
        hex_data = str_data.encode('hex')
    
        for n in range(0,PACK_LEN): #convert to array of bytes
            lower = 2*n
            upper = lower + 2
            bytes_data[n] = int(hex_data[lower:upper],16)

        humid =  (((bytes_data[6])<<8)+(bytes_data[7]))/10.0
        temp =  (((((bytes_data[8])&0x7F)<<8)+(bytes_data[9]))/10.0)
    
        if int(bytes_data[8]) & 0x80: #invert temp if sign bit is set
            temp = -1.0* temp
    
#        checksum = (uint(sum(bytes_data[0:10])) & 0xFF)+1
        checksum = 0
        for i in range(PACK_LEN-1):
            checksum += bytes_data[i]

        checksum &= 0xFF
        checksum += 1
 
        if checksum == bytes_data[10]:
            return (temp, humid)
        raise ValueError,'Invalid Checksum'

    except:
        raise

while True:
    try:
        (temp, humid) = get_temp_hum()
        print "Valid!"
        print "Temp: " + str(temp)
        print "Hum: " + str(humid)
        print "Dewpoint: " + str(dewpoint(temp, humid))
    except Exception as ex:
        print "exception: ", ex
        print "Timed Out"
        time.sleep(30)

