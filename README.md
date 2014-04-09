txOneDrive
----------------------------------------

Twisted-based python async interface for
[OneDrive API (version 5.0)](http://msdn.microsoft.com/en-us/library/live/hh826521)
(formerly known as SkyDrive).

API is mostly the same as in
[python-onedrive](https://github.com/mk-fg/python-onedrive) module
(txOneDriveAPI class maps to OneDriveAPIWrapper, txOneDrive to OneDriveAPI) -
methods are re-used directly from classes there, so more info on these can be
found in that project.

Key difference from synchronous python-onedrive module is that all methods
return twisted Deferred objects as scheduled to run by event loop, allowing to
run multiple operations (like large file uploads) concurrently within one python
process.

Service was called SkyDrive prior to 2014-02-19, when it got renamed to OneDrive.
This package similarly renamed from txskydrive to txonedrive.


Usage Example
----------------------------------------

Following script will print listing of the root OneDrive folder, upload
"test.txt" file there, try to find it in updated folder listing and then remove
it.

	from twisted.internet import defer, reactor
	from txonedrive import txOneDrivePersistent

	@defer.inlineCallbacks
	def do_stuff():
		api = txOneDrivePersistent.from_conf()

		# Print root directory ("me/skydrive") listing
		print (e['name'] for e in (yield api.listdir()))

		# Upload "test.txt" file from local current directory
		file_info = yield api.put('test.txt')

		# Find just-uploaded "test.txt" file by name
		file_id = yield api.resolve_path('test.txt')

		# Check that id matches uploaded file
		assert file_info['id'] == file_id

		# Remove the file
		yield api.delete(file_id)

	do_stuff().addBoth(lambda ignored: reactor.stop())
	reactor.run()

Note that txOneDriveAPIPersistent convenience class uses Microsoft LiveConnect
authentication data from "~/.lcrc" file, which must be created as described in
more detail [in python-onedrive
docs](https://github.com/mk-fg/python-onedrive#command-line-usage).


Installation
----------------------------------------

It's a regular package for Python 2.7 (not 3.X).

Using [pip](http://pip-installer.org/) is the best way:

	% pip install txonedrive

If you don't have it, use:

	% easy_install pip
	% pip install txonedrive

Alternatively ([see
also](http://www.pip-installer.org/en/latest/installing.html)):

	% curl https://raw.github.com/pypa/pip/master/contrib/get-pip.py | python
	% pip install txonedrive

Or, if you absolutely must:

	% easy_install txonedrive

But, you really shouldn't do that.

Current-git version can be installed like this:

	% pip install 'git+https://github.com/mk-fg/txonedrive.git#egg=txonedrive'

Note that to install stuff in system-wide PATH and site-packages, elevated
privileges are often required.
Use "install --user",
[~/.pydistutils.cfg](http://docs.python.org/install/index.html#distutils-configuration-files)
or [virtualenv](http://pypi.python.org/pypi/virtualenv) to do unprivileged
installs into custom paths.


### Requirements

* [Python 2.7 (not 3.X)](http://python.org)

* [Twisted](http://twistedmatrix.com) (core, web, at least 12.2.0)

* [python-onedrive](https://github.com/mk-fg/python-onedrive)
