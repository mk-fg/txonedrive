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
from twisted.python import log

from skydrive import api_v5



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
	chunk_size = 512 * 2**10 # 512 KiB

	def __init__(self, fields):
		self.fields = fields
		self.length = UNKNOWN_LENGTH
		self.task = False

	def upload_file(src, dst):
		def _writeloop():
			try:
				while True:
					chunk = src.read(self.chunk_size)
					if not chunk: break
					dst.write(chunk)
					yield
			finally: src.close()
		return task.cooperate(_writeloop()).whenDone()

	@defer.inlineCallbacks
	def send_form(self, dst):
		for name, data in self.fields.viewitems():
			dst.write(b'--{}\r\n'.format(boundary))

			if isinstance(data, tuple):
				(fn, data), ct = data, guess_type(fn)[0] or b'application/octet-stream'
				dst.write(
					b'Content-Disposition: form-data;'
					b' name="{}"; filename="{}"\r\n'.format(name, fn) )
			else:
				ct = b'text/plain'
				dst.write( b'Content-Disposition:'
					b' form-data; name="{}"\r\n'.format(name) )
			dst.write(b'Content-Type: {}\r\n\r\n'.format(ct))

			if isinstance(data, type.StringTypes): dst.write(data)
			else: yield self.upload_file(data, dst)
			dst.write(b'\r\n')

		dst.write(b'--{}--\r\n'.format(boundary))

	def startProducing(self, dst):
		if not self.task: self.task = self.send_form()
		return self.task

	def resumeProducing(self):
		self.task.unpause()

	def pauseProducing(self):
		self.task.pause()

	def stopProducing(self):
		self.task.cancel()
		self.task = False



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


	def __init__(self, *argz, **kwz):
		super(txSkyDrive, self).__init__(*argz, **kwz)

		pool = HTTPConnectionPool(reactor)
		for k, v in self.request_pool_options.viewitems():
			getattr(pool, k) # to somewhat protect against typos
			setattr(pool, k, v)

		self.request_agent = ContentDecoderAgent(RedirectAgent(Agent(
			reactor, TLSContextFactory(self.ca_certs_files) )), [('gzip', GzipDecoder)])


	@defer.inlineCallbacks
	def request( self, url, method='get', data=None,
			files=None, raw=False, headers=dict(), raise_for=dict() ):
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
			body = MultipartDataSender(files)

		if isinstance(url, unicode): url = url.encode('utf-8')

		code = None
		try:
			res = yield self.request_agent.request(
				method.upper(), url,
				Headers(dict((k,[v]) for k,v in (headers or dict()).viewitems())), body )
			code = res.code
			if code != http.OK: raise api_v5.ProtocolError('{} {}'.format(code, res.phrase))
			if code == http.NO_CONTENT: return

			body = defer.Deferred()
			res.deliverBody(DataReceiver(body))
			body = yield body
			defer.returnValue(json.loads(body) if not raw else body)

		except api_v5.ProtocolError as err:
			raise raise_for.get(code, api_v5.ProtocolError)(err.message, code)


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
		def sync(self): return

	@defer.inlineCallbacks
	def test():
		api = yield txSkyDriveTest.from_conf()
		print (yield api.listdir())
		reactor.stop()

	test()
	reactor.run()
