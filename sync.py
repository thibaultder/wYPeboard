# (C) 2014 by Dominik Jain (djain@gmx.net)
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import socket
import sys
import threading
import pickle
import wx
import asyncore
import time as t
import traceback
from whiteboard import Whiteboard
import objects

class DispatchingWhiteboard(Whiteboard):
	def __init__(self, title, dispatcher, isServer):
		Whiteboard.__init__(self, title)
		self.dispatcher = dispatcher
		self.isServer = isServer
		self.lastPing = t.time()
		self.Centre()
		self.Show()

	def onObjectCreationCompleted(self, object):
		self.dispatch(evt="addObject", args=(object.serialize(),))

	def _deserialize(self, s):
		return objects.deserialize(s, self.viewer)
	
	def addObject(self, object):
		super(DispatchingWhiteboard, self).addObject(self._deserialize(object))
	
	def dispatch(self, **d):
		self.dispatcher.dispatch(d)

	def handleNetworkEvent(self, d):
		exec("self.%s(*d['args'])" % d["evt"])
		
	def OnTimer(self, evt):
		Player.OnTimer(self, evt)
		# perform periodic ping from client to server
		if not self.isServer:
			if t.time() - self.lastPing > 1:
				self.lastPing = t.time()
				self.dispatch(ping = True)

class Dispatcher(asyncore.dispatcher):
	def __init__(self, sock=None):
		asyncore.dispatcher.__init__(self, sock=sock)
		self.terminator = "\r\n\r\n$end$\r\n\r\n"
		self.recvBuffer = ""
		self.sendBuffer = ""
	
	def sendPart(self):
		num_sent = 0
		num_sent = asyncore.dispatcher.send(self, self.sendBuffer[:1024])
		#print "sent %d of %d" % (num_sent, len(self.sendBuffer))
		self.sendBuffer = self.sendBuffer[num_sent:]
	
	def send(self, data):
		print "sending packet; size %d" % len(data)
		#print data
		self.sendBuffer += data + self.terminator
		self.initiate_send()
		
	def initiate_send(self):
		while len(self.sendBuffer) > 0:
			self.sendPart()

	def handle_read(self):
		d = self.recv(8192)
		if d == "": # connection closed from other end			
			return
		self.recvBuffer += d
		#print self.recvBuffer
		print "recvBuffer size: %d" % len(self.recvBuffer)
		while True:
			try:
				tpos = self.recvBuffer.index(self.terminator)
			except:
				break
			packet = self.recvBuffer[:tpos]
			print "received packet; size %d" % len(packet)
			print packet
			self.handle_packet(packet)
			self.recvBuffer = self.recvBuffer[tpos+len(self.terminator):]  
	
	def handle_packet(self, packet):
		''' handles a read packet '''
		sys.stderr('WARNING: unhandled packet; size %d' % len(packet))
	
class SyncServer(Dispatcher):
	def __init__(self, port):
		Dispatcher.__init__(self)
		# start listening for connections
		self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
		host = ""
		self.bind((host, port))
		self.connections = []
		self.listen(5)
		# create actual player
		self.whiteboard = DispatchingWhiteboard("wYPeboard server", self, True)		
	
	def handle_accept(self):		
		pair = self.accept()
		if pair is None:
			return
		print "incoming connection from %s" % str(pair[1])
		conn = DispatcherConnection(pair[0], self)
		self.connections.append(conn)
		#conn.sendData("hello %s" % str(pair[1]))

	def dispatch(self, d, exclude=None):
		print "dispatching %s to %d client(s)" % (str(d), len(self.connections) if exclude is None else len(self.connections)-1)
		for c in self.connections:
			if c != exclude:
				c.sendData(d)
	
	def removeConnection(self, conn):
		if not conn in self.connections:
			print "tried to remove non-present connection"
		self.connections.remove(conn)
		if len(self.connections) == 0:
			self.whiteboard.errorDialog("All client connections have been closed.")			

class DispatcherConnection(Dispatcher):
	def __init__(self, connection, server):
		Dispatcher.__init__(self, sock=connection)
		self.syncserver = server

	def handle_packet(self, packet):
		d = packet
		print "handling packet; size %d" % len(d)
		print d
		if d == "": # connection closed from other end			
			return
		d = pickle.loads(d)
		if type(d) == dict and "ping" in d: # ignore pings
			return
		print "received: %s " % d
		if type(d) == dict and "evt" in d:
			# forward event to other clients
			self.syncserver.dispatch(d, exclude=self)
			# handle in own player
			self.syncserver.whiteboard.handleNetworkEvent(d)	

	def remove(self):
		print "client connection dropped"
		self.syncserver.removeConnection(self)

	def handle_close(self):
		self.remove()
		self.close()

	def sendData(self, d):
		self.send(pickle.dumps(d))

class SyncClient(Dispatcher):	
	def __init__(self, server, port):
		Dispatcher.__init__(self)		
		self.serverAddress = (server, port)
		self.connectedToServer = self.connectingToServer = False
		self.connectToServer()
		# create actual player
		self.whiteboard = DispatchingWhiteboard("Sync'd VLC Client", self, False)

	def connectToServer(self):
		print "connecting to %s..." % str(self.serverAddress)
		self.connectingToServer = True
		self.create_socket(socket.AF_INET, socket.SOCK_STREAM)		
		self.connect(self.serverAddress)
	
	def handle_connect(self):
		print "connected to %s" % str(self.serverAddress)
		self.connectingToServer = False
		self.connectedToServer = True
		# immediately request current playback data
		#self.player.dispatch(evt="OnQueryPlayLoc", args=())

	def handle_packet(self, packet):
		d = packet
		if d == "": # server connection lost
			return
		d = pickle.loads(d)
		#print "received: %s " % d
		if type(d) == dict and "evt" in d:
			self.whiteboard.handleNetworkEvent(d)
	
	def handle_close(self):
		self.close()
		
	def readable(self):
		return True
	
	def writable(self):
		return True
		
	def close(self):
		print "connection closed"
		self.connectedToServer = False
		asyncore.dispatcher.close(self)
		if self.whiteboard.questionDialog("No connection. Reconnect?\nClick 'No' to quit.", "Reconnect?"):
			self.connectToServer()
		else:
			self.whiteboard.Close()
	
	def dispatch(self, d):
		if not self.connectedToServer:
			return
		if not (type(d) == dict and "ping" in d):
			print "sending %s" % str(d)
		self.send(pickle.dumps(d))

def spawnNetworkThread():
	networkThread = threading.Thread(target=lambda:asyncore.loop())
	networkThread.daemon = True
	networkThread.start()

def startServer(port):
	print "serving on port %d" % port
	server = SyncServer(port)
	spawnNetworkThread()
	return server
	
def startClient(server, port):
	print "connecting to %s:%d" % (server, port)
	client = SyncClient(server, port)
	spawnNetworkThread()
	return client

if __name__=='__main__':
	app = wx.PySimpleApp()
	
	argv = sys.argv[1:]
	file = None
	if len(argv) in (2, 3) and argv[0] == "serve":
		port = int(argv[1])
		startServer(port)
	elif len(argv) in (3, 4) and argv[0] == "connect":
		server = argv[1]
		port = int(argv[2])
		startClient(server, port)
	else:
		appName = "sync.py"
		print "\nwYPeboard\n\n"
		print "usage:"
		print "   server:  %s serve <port>" % appName
		print "   client:  %s connect <server> <port>" % appName
		sys.exit(1)

	app.MainLoop()
	