#!/usr/bin/python
"""
    Rename selected files to be in no. order.
    e.g. 5_006_choumei_02.mp3, 5_006_choumei_03.mp3 -> 01.mp3, 02.mp3
"""

import sys, subprocess, os

files = sys.argv[1:]
path = os.path.dirname(os.path.abspath(files[0]))
ext = os.path.splitext(files[0])[-1]
log_file = os.path.join(path,'rename.log')

print files, path, ext
os.chdir(path)
with open(log_file, 'w') as log:
    for (counter,f) in enumerate(files,start=1):
        log.write("rename: %s -> %02d%s\n" % (f, counter,ext))
        os.rename(f,'%02d%s' % (counter,ext))
    

