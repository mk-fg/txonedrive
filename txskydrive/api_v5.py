#-*- coding: utf-8 -*-

import itertools as it, operator as op, functools as ft
from urllib import urlencode
from mimetypes import guess_type
import os, sys, io, re, types, json, logging

from OpenSSL import crypto
from zope.interface import implements

from twisted.web.iweb import IBodyProducer, UNKNOWN_LENGTH
from twisted.web.client import Agent, RedirectAgent,\
	HTTPConnectionPool, HTTP11ClientProtocol, ContentDecoderAgent,\
	GzipDecoder, FileBodyProducer
from twisted.web.http_headers import Headers
from twisted.web import http
from twisted.internet import defer, reactor, ssl, task, protocol

from skydrive.api_v5 import SkyDriveInteractionError,\
	ProtocolError, AuthenticationError, DoesNotExists
from skydrive import api_v5, conf

from twisted.python import log
for lvl in 'debug', 'info', ('warning', 'warn'), 'error', ('critical', 'fatal'):
	lvl, func = lvl if isinstance(lvl, tuple) else (lvl, lvl)
	assert not getattr(log, lvl, False)
	setattr(log, func, ft.partial( log.msg,
		logLevel=logging.getLevelName(lvl.upper()) ))



class DataReceiver(protocol.Protocol):

	def __init__(self, done):
		self.done, self.data = done, list()

	def dataReceived(self, chunk):
		self.data.append(chunk)

	def connectionLost(self, reason):
		# reason.getErrorMessage()
		self.done.callback(b''.join(self.data))


class MultipartDataSender(object):
	implements(IBodyProducer)

	#: Single read/write size
	chunk_size = 64 * 2**10 # 64 KiB

	def __init__(self, fields, boundary):
		self.fields, self.boundary = fields, boundary
		self.task = None

		## "Transfer-Encoding: chunked" doesn't work with SkyDrive,
		##  so calculate_length() must be called to replace it with some value
		self.length = UNKNOWN_LENGTH

	def calculate_length(self):
		d = self.send_form()
		d.addCallback(lambda length: setattr(self, 'length', length))
		return d

	@defer.inlineCallbacks
	def upload_file(self, src, dst):
		try:
			while True:
				chunk = src.read(self.chunk_size)
				if not chunk: break
				yield dst.write(chunk)
		finally: src.close()

	@defer.inlineCallbacks
	def send_form(self, dst=None):
		dry_run = not dst
		if dry_run: dst, dst_ext = io.BytesIO(), 0

		for name, data in self.fields.viewitems():
			dst.write(b'--{}\r\n'.format(self.boundary))

			if isinstance(data, tuple):
				fn, data = data
				ct = guess_type(fn)[0] or b'application/octet-stream'
				dst.write(
					b'Content-Disposition: form-data;'
					b' name="{}"; filename="{}"\r\n'.format(name, fn) )
			else:
				ct = b'text/plain'
				dst.write( b'Content-Disposition:'
					b' form-data; name="{}"\r\n'.format(name) )
			dst.write(b'Content-Type: {}\r\n\r\n'.format(ct))

			if isinstance(data, types.StringTypes): dst.write(data)
			elif not dry_run: yield self.upload_file(data, dst)
			else: dst_ext += os.fstat(data.fileno()).st_size
			dst.write(b'\r\n')

		dst.write(b'--{}--\r\n'.format(self.boundary))

		if dry_run: defer.returnValue(dst_ext + len(dst.getvalue()))
		else: self.task = None

	def startProducing(self, dst):
		if not self.task: self.task = self.send_form(dst)
		return self.task

	def resumeProducing(self):
		if not self.task: return
		self.task.unpause()

	def pauseProducing(self):
		if not self.task: return
		self.task.pause()

	def stopProducing(self):
		if not self.task: return
		self.task.cancel()
		self.task = None



class TLSContextFactory(ssl.CertificateOptions):

	isClient = 1

	def __init__(self, ca_certs_files):
		ca_certs = dict()

		for ca_certs_file in ( [ca_certs_files]
				if isinstance(ca_certs_files, types.StringTypes) else ca_certs_files ):
			with open(ca_certs_file) as ca_certs_file:
				ca_certs_file = ca_certs_file.read()
			for cert in re.findall( r'(-----BEGIN CERTIFICATE-----'
					r'.*?-----END CERTIFICATE-----)', ca_certs_file, re.DOTALL ):
				cert = crypto.load_certificate(crypto.FILETYPE_PEM, cert)
				ca_certs[cert.digest('sha1')] = cert

		super(TLSContextFactory, self).__init__(verify=True, caCerts=ca_certs.values())

	def getContext(self, hostname, port):
		return super(TLSContextFactory, self).getContext()


class QuietHTTP11ClientFactory(protocol.Factory):
	noisy = False
	def __init__(self, quiescentCallback):
		self._quiescentCallback = quiescentCallback
	def buildProtocol(self, addr):
		return HTTP11ClientProtocol(self._quiescentCallback)

class QuietHTTPConnectionPool(HTTPConnectionPool):
	_factory = QuietHTTP11ClientFactory



class txSkyDriveAPI(api_v5.SkyDriveAPIWrapper):
	'SkyDrive API client.'

	#: Options to twisted.web.client.HTTPConnectionPool
	request_pool_options = dict(
		maxPersistentPerHost=10,
		cachedConnectionTimeout=600,
		retryAutomatically=True )

	#: Path string or list of strings
	ca_certs_files = b'/etc/ssl/certs/ca-certificates.crt'

	#: Dump HTTP request data in debug log (might contain all sorts of auth tokens!)
	debug_requests = False


	def __init__(self, *argz, **kwz):
		super(txSkyDriveAPI, self).__init__(*argz, **kwz)

		pool = QuietHTTPConnectionPool(reactor, persistent=True)
		for k, v in self.request_pool_options.viewitems():
			getattr(pool, k) # to somewhat protect against typos
			setattr(pool, k, v)

		self.request_agent = ContentDecoderAgent(RedirectAgent(Agent(
			reactor, TLSContextFactory(self.ca_certs_files), pool=pool )), [('gzip', GzipDecoder)])


	@defer.inlineCallbacks
	def request( self, url, method='get', data=None,
			files=None, raw=False, headers=dict(), raise_for=dict() ):
		if self.debug_requests:
			log.debug(( 'HTTP request: {} {} (h: {}, data: {}, files: {}),'
				' raw: {}' ).format(method, url[:100], headers, data, files, raw))
		method, body = method.lower(), None
		headers = dict() if not headers else headers.copy()
		headers.setdefault('User-Agent', 'txSkyDrive')

		if data is not None:
			if method == 'post':
				headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
				body = FileBodyProducer(io.BytesIO(urlencode(data)))
			else:
				headers = headers.copy()
				headers.setdefault('Content-Type', 'application/json')
				body = FileBodyProducer(io.BytesIO(urlencode(json.dumps(data))))

		if files is not None:
			boundary = os.urandom(16).encode('hex')
			headers.setdefault('Content-Type', 'multipart/form-data; boundary={}'.format(boundary))
			body = MultipartDataSender(files, boundary)
			yield body.calculate_length()

		if isinstance(url, unicode): url = url.encode('utf-8')
		if isinstance(method, unicode): method = method.encode('ascii')

		code = None
		try:
			res = yield self.request_agent.request(
				method.upper(), url,
				Headers(dict((k,[v]) for k,v in (headers or dict()).viewitems())), body )
			code = res.code
			if self.debug_requests:
				log.debug( 'HTTP request done ({} {}): {} {} {}'\
					.format(method, url[:100], code, res.phrase, res.version) )
			if code == http.NO_CONTENT: defer.returnValue(None)
			if code not in [http.OK, http.CREATED]:
				raise ProtocolError(code, res.phrase)

			body = defer.Deferred()
			res.deliverBody(DataReceiver(body))
			body = yield body
			defer.returnValue(json.loads(body) if not raw else body)

		except ProtocolError as err:
			raise raise_for.get(code, ProtocolError)(code, err.message)


	@defer.inlineCallbacks
	def __call__( self, url='me/skydrive', query=dict(),
			query_filter=True, auth_header=False,
			auto_refresh_token=True, **request_kwz ):
		'''Make an arbitrary call to LiveConnect API.
			Shouldn't be used directly under most circumstances.'''
		if query_filter:
			query = dict( (k,v) for k,v in
				query.viewitems() if v is not None )
		if auth_header:
			request_kwz.setdefault('headers', dict())\
				['Authorization'] = 'Bearer {}'.format(self.auth_access_token)
		kwz = request_kwz.copy()
		kwz.setdefault('raise_for', dict())[401] = AuthenticationError
		api_url = ft.partial( self._api_url,
			url, query, pass_access_token=not auth_header )
		try: res = yield self.request(api_url(), **kwz)
		except AuthenticationError:
			if not auto_refresh_token: raise
			yield self.auth_get_token()
			if auth_header: # update auth header with a new token
				request_kwz['headers']['Authorization']\
					= 'Bearer {}'.format(self.auth_access_token)
			res = yield self.request(api_url(), **request_kwz)
		defer.returnValue(res)


	@defer.inlineCallbacks
	def auth_get_token(self, check_scope=True):
		'Refresh or acquire access_token.'
		res = self.auth_access_data_raw = yield self._auth_token_request()
		defer.returnValue(self._auth_token_process(res, check_scope=check_scope))



class txSkyDrive(txSkyDriveAPI):
	'More biased SkyDrive interface with some convenience methods.'

	@defer.inlineCallbacks
	def resolve_path( self, path,
			root_id='me/skydrive', objects=False ):
		'''Return id (or metadata) of an object, specified by chain
				(iterable or fs-style path string) of "name" attributes of it's ancestors,
				or raises DoesNotExists error.
			Requires a lot of calls to resolve each name in path, so use with care.
			root_id parameter allows to specify path
				 relative to some folder_id (default: me/skydrive).'''
		if path:
			if isinstance(path, types.StringTypes):
				if not path.startswith('me/skydrive'):
					path = filter(None, path.split(os.sep))
				else: root_id, path = path, None
			if path:
				try:
					for i, name in enumerate(path):
						root_id = dict(it.imap(
							op.itemgetter('name', 'id'), (yield self.listdir(root_id)) ))[name]
				except (KeyError, ProtocolError) as err:
					if isinstance(err, ProtocolError) and err.code != 404: raise
					raise DoesNotExists(root_id, path[i:])
		defer.returnValue(root_id if not objects else (yield self.info(root_id)))

	@defer.inlineCallbacks
	def listdir(self, folder_id='me/skydrive', type_filter=None, limit=None):
		'''Return a list of objects in the specified folder_id.
			limit is passed to the API, so might be used as optimization.
			type_filter can be set to type (str) or sequence
				of object types to return, post-api-call processing.'''
		lst = ( yield super(txSkyDrive, self)\
			.listdir(folder_id=folder_id, limit=limit) )['data']
		if type_filter:
			if isinstance(type_filter, types.StringTypes): type_filter = {type_filter}
			lst = list(obj for obj in lst if obj['type'] in type_filter)
		defer.returnValue(lst)

	@defer.inlineCallbacks
	def get_quota(self):
		'Return tuple of (bytes_available, bytes_quota).'
		defer.returnValue(
			op.itemgetter('available', 'quota')\
				((yield super(SkyDriveAPI, self).get_quota())) )

	@defer.inlineCallbacks
	def copy(self, obj_id, folder_id, move=False):
		'''Copy specified file (object) to a folder.
			Note that folders cannot be copied, this is API limitation.'''
		if folder_id.startswith('me/skydrive'):
			log.info("Special folder names (like 'me/skydrive') don't"
				" seem to work with copy/move operations, resolving it to id")
			folder_id = yield self.info(folder_id)['id']
		defer.returnValue((
			yield super(SkyDriveAPI, self).copy(obj_id, folder_id, move=move) ))

	@defer.inlineCallbacks
	def comments(self, obj_id):
		'Get a list of comments (message + metadata) for an object.'
		defer.returnValue(
			(yield super(SkyDriveAPI, self).comments(obj_id))['data'] )



class txSkyDrivePersistent(txSkyDrive, conf.ConfigMixin):

	@ft.wraps(txSkyDrive.auth_get_token)
	def auth_get_token(self, *argz, **kwz):
		d = defer.maybeDeferred(super(
			txSkyDrivePersistent, self ).auth_get_token, *argz, **kwz)
		d.addCallback(lambda ret: [self.sync(), ret][1])
		return d

	def __del__(self): self.sync()


class txSkyDrivePluggableSync(txSkyDrive):

	config_update_keys = ['auth_access_token', 'auth_refresh_token']

	#: Should be set on init or overidden in subclass
	config_update_callback = None

	def __init__(self, *argz, **kwz):
		super(txSkyDrivePluggableSync, self).__init__(*argz, **kwz)
		if not self.config_update_callback:
			raise TypeError('config_update_callback must be set')

	def sync(self):
		if not self.config_update_callback:
			raise TypeError('config_update_callback must be set')
		self.config_update_callback(**dict(
			(k, getattr(self, k)) for k in self.config_update_keys ))

	@ft.wraps(txSkyDrive.auth_get_token)
	def auth_get_token(self, *argz, **kwz):
		d = defer.maybeDeferred(super(
			txSkyDrivePluggableSync, self ).auth_get_token, *argz, **kwz)
		d.addCallback(lambda ret: [self.sync(), ret][1])
		return d

	def __del__(self): self.sync()




if __name__ == '__main__':
	logging.basicConfig(level=logging.DEBUG)
	log.PythonLoggingObserver().start()

	@defer.inlineCallbacks
	def test():
		api = txSkyDriveAPIPersistent.from_conf()

		print map(op.itemgetter('name'), (yield api.listdir()))

		try: file_id = yield api.resolve_path('README.md')
		except DoesNotExists: print 'File not found'
		else: api.delete(file_id)

		from twisted.web._newclient import RequestGenerationFailed, ResponseFailed
		try: res = yield api.put('README.md')
		except (RequestGenerationFailed, ResponseFailed) as err:
			err[0][0].raiseException()
		print 'Uploaded:', res

		reactor.stop()

	test()
	reactor.run()
