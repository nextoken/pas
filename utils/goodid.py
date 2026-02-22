#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
Return good id:
e.g. 
input = 'cyun1 sam1 teoi2'
default output = 'cyun1sam1teoi2'
shortened good id = 'cyun1st'
"""
import sys, argparse

def main(argv):
    parser = argparse.ArgumentParser(
            description="Generate shortened good id for Wing Chun Glossary from jyutping")
    parser.add_argument("-s", "--short", action='store_true', help="Shortened Good ID")
    parser.add_argument("words", help='Words to be converted to Good ID')
    args = parser.parse_args()

    if args.short:
        print reduce(lambda x,y: x+y[0], args.words.split())
    else:
        print reduce(lambda x,y: x+y, args.words.split())

if __name__ == "__main__":
    main(sys.argv[1:])

