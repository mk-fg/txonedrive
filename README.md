txSkyDrive
----------------------------------------

Twisted-based python async interface for [SkyDrive API (version
5.0)](http://msdn.microsoft.com/en-us/library/live/hh826521).

API is mostly the same as in
[python-skydrive](https://github.com/mk-fg/python-skydrive) module
(txSkyDriveAPI class maps to SkyDriveAPIWrapper, txSkyDrive to SkyDriveAPI) -
methods are re-used directly from classes there, so more info on these can be
found in that project.

Key difference from synchronous python-skydrive module is that all methods
return twisted Deferred objects as scheduled to run by event loop, allowing to
run multiple operations (like large file uploads) concurrently within one python
process.


Usage Example
----------------------------------------

Following script will print listing of the root SkyDrive folder, upload
"test.txt" file there, try to find it in updated folder listing and then remove
it.

	from twisted.internet import defer, reactor
	from txskydrive import txSkyDrivePersistent

	@defer.inlineCallbacks
	def do_stuff():
		api = txSkyDrivePersistent.from_conf()

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

Note that txSkyDriveAPIPersistent convenience class uses Microsoft LiveConnect
authentication data from "~/.lcrc" file, which must be created as described in
more detail [in python-skydrive
docs](https://github.com/mk-fg/python-skydrive#command-line-usage).


Installation
----------------------------------------

It's a regular package for Python 2.7 (not 3.X).

Using [pip](http://pip-installer.org/) is the best way:

	% pip install txskydrive

If you don't have it, use:

	% easy_install pip
	% pip install txskydrive

Alternatively ([see
also](http://www.pip-installer.org/en/latest/installing.html)):

	% curl https://raw.github.com/pypa/pip/master/contrib/get-pip.py | python
	% pip install txskydrive

Or, if you absolutely must:

	% easy_install txskydrive

But, you really shouldn't do that.

Current-git version can be installed like this:

	% pip install 'git+https://github.com/mk-fg/txskydrive.git#egg=txskydrive'

Note that to install stuff in system-wide PATH and site-packages, elevated
privileges are often required.
Use "install --user",
[~/.pydistutils.cfg](http://docs.python.org/install/index.html#distutils-configuration-files)
or [virtualenv](http://pypi.python.org/pypi/virtualenv) to do unprivileged
installs into custom paths.


### Requirements

* [Python 2.7 (not 3.X)](http://python.org/)

* [python-skydrive](https://github.com/mk-fg/python-skydrive)
