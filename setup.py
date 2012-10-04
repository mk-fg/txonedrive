#!/usr/bin/env python

from setuptools import setup, find_packages
import os

pkg_root = os.path.dirname(__file__)

# Error-handling here is to allow package to be built w/o README included
try: readme = open(os.path.join(pkg_root, 'README.txt')).read()
except IOError: readme = ''

setup(

	name = 'txSkyDrive', # just trying to mimic tx* naming convention
	version = '12.10.3',
	author = 'Mike Kazantsev',
	author_email = 'mk.fraggod@gmail.com',
	license = 'WTFPL',
	keywords = 'skydrive twisted api oauth2 rest microsoft cloud live liveconnect',
	url = 'http://github.com/mk-fg/txskydrive',

	description = 'Twisted-based async interface'
		' for Microsoft LiveConnect SkyDrive REST API v5.0',
	long_description = readme,

	classifiers = [
		'Development Status :: 4 - Beta',
		'Environment :: Plugins',
		'Framework :: Twisted',
		'Intended Audience :: Developers',
		'Intended Audience :: Information Technology',
		'License :: OSI Approved',
		'Operating System :: OS Independent',
		'Programming Language :: Python',
		'Programming Language :: Python :: 2.7',
		'Programming Language :: Python :: 2 :: Only',
		'Topic :: Internet',
		'Topic :: System :: Archiving',
		'Topic :: System :: Filesystems' ],

	install_requires = ['Twisted', 'python-skydrive >= 12.10.4'],

	packages = find_packages(),
	include_package_data = True,
	package_data = {'': ['README.txt']},
	exclude_package_data = {'': ['README.*']} )
