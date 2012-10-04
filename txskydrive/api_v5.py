#-*- coding: utf-8 -*-

import itertools as it, operator as op, functools as ft
from urllib import urlencode
from mimetypes import guess_type
import os, sys, io, re, types, json, logging

from OpenSSL import crypto
from zope.interface import implements

from twisted.web.iweb import IBodyProducer, UNKNOWN_LENGTH
from twisted.web.client import Agent, RedirectAgent,\
	HTTPConnectionPool, ContentDecoderAgent, GzipDecoder,\
	FileBodyProducer
from twisted.web.http_headers import Headers
from twisted.web import http
from twisted.internet.protocol import Protocol
from twisted.internet import defer, reactor, ssl, task

from skydrive import api_v5

from twisted.python import log
for lvl in 'debug', 'info', ('warning', 'warn'), 'error', ('critical', 'fatal'):
	lvl, func = lvl if isinstance(lvl, tuple) else (lvl, lvl)
	assert not getattr(log, lvl, False)
	setattr(log, func, ft.partial( log.msg,
		logLevel=logging.getLevelName(lvl.upper()) ))



class DataReceiver(Protocol):

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
		self.length = UNKNOWN_LENGTH
		self.task = None

	@defer.inlineCallbacks
	def upload_file(self, src, dst):
		try:
			while True:
				chunk = src.read(self.chunk_size)
				if not chunk: break
				yield dst.write(chunk)
		finally: src.close()

	@defer.inlineCallbacks
	def send_form(self, dst):
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
			else: yield self.upload_file(data, dst)
			dst.write(b'\r\n')

		dst.write(b'--{}--\r\n'.format(self.boundary))

		self.task = None # done

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



class LCVolatileCredentials(object):
	def __init__(self):
		raise NotImplementedError()



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



class txSkyDrive(api_v5.SkyDriveAPIWrapper):
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
		super(txSkyDrive, self).__init__(*argz, **kwz)

		pool = HTTPConnectionPool(reactor, persistent=True)
		for k, v in self.request_pool_options.viewitems():
			getattr(pool, k) # to somewhat protect against typos
			setattr(pool, k, v)

		self.request_agent = ContentDecoderAgent(RedirectAgent(Agent(
			reactor, TLSContextFactory(self.ca_certs_files) )), [('gzip', GzipDecoder)])


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
			if code != http.OK: raise api_v5.ProtocolError('{} {}'.format(code, res.phrase))
			if code == http.NO_CONTENT: defer.returnValue(None)

			body = defer.Deferred()
			res.deliverBody(DataReceiver(body))
			body = yield body
			defer.returnValue(json.loads(body) if not raw else body)

		except api_v5.ProtocolError as err:
			raise raise_for.get(code, api_v5.ProtocolError)(err.message, code)


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
		kwz.setdefault('raise_for', dict())[401] = api_v5.AuthenticationError
		api_url = ft.partial( self._api_url,
			url, query, pass_access_token=not auth_header )
		try: res = yield self.request(api_url(), **kwz)
		except api_v5.AuthenticationError:
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



if __name__ == '__main__':
	logging.basicConfig(level=logging.DEBUG)
	log.PythonLoggingObserver().start()


	from skydrive.conf import ConfigMixin

	class txSkyDriveTest(txSkyDrive, ConfigMixin):
		# api_url_base = 'http://127.0.0.1:8080/test/'

		@ft.wraps(txSkyDrive.auth_get_token)
		def auth_get_token(self, *argz, **kwz):
			d = defer.maybeDeferred(super(
				txSkyDriveTest, self ).auth_get_token, *argz, **kwz)
			d.addCallback(lambda ret: [self.sync(), ret][1])
			return d

		def __del__(self): self.sync()


	@defer.inlineCallbacks
	def test():
		api = txSkyDriveTest.from_conf()

		# print (yield api.listdir())
		# print (yield api.put('test.jpg'))
		# print (yield api.put('test.jpg'))

		# from twisted.web._newclient import RequestGenerationFailed
		# try: res = yield api.put('test.jpg')
		# except Exception as err:
		# 	if isinstance(err, RequestGenerationFailed):
		# 		err[0][0].raiseException()
		# 	raise
		# print 'SUCCESS', res

		reactor.stop()

	test()
	reactor.run()
