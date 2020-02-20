#!/usr/bin/python
# vim: noai:ts=4:sw=4
#
#  Copyright (C) 2020-2020 by Les Potter
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#  This work is derived from "bridge" written by Luiz Angelo Daros de Luca
#  whose copyright follows.
#
#  Copyright (C) 2010-2017 by Luiz Angelo Daros de Luca
#    luizluca@gmail.com
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#################
#
# Port Forward client/server over HTTP
#
#################

import sys
import socket
import select
from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler


# DEFAULTS
args = sys.argv
localport = 8080
location = "/bridge"


def usage(err=False):
    print("Usage:  bridge-srv [-h] [PORT [PATH]]")
    if not err:
        print('''
bridge-srv is the server side of an HTTP Tunnel.  It listens for a
connection on PORT (default 8080) and fields HTTP requests to implement
a proxy protocol using only HTTP methods.

The bridge-clnt program connects to PORT and requests a connection
to a particular server at a particular PORT.  bridge-srv establishes
a connection to the requested server on the requested port and then
proxies traffic between the client and the target server unchanged.

Example:
    python bridge-srv.py 80 /bridge

''')
    quit(1 if err else 0)

args = args[1:]
try:
    for idx, arg in enumerate(args):
        if "-h" == arg:
            usage()
        elif idx == 0:
            localport = int(arg)
        elif idx == 1:
            location = arg
        else:
            print("Invalid argument: {}".format(arg))
            usage(True)
except:
    print("Error parsing arguments")
    usage(True)

def set_keepalive_linux(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """Set TCP keepalive on an open socket.

    It activates after 1 second (after_idle_sec) of idleness,
    then sends a keepalive ping once every 3 seconds (interval_sec),
    and closes the connection after 5 failed ping (max_fails), or 15 seconds
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)

def run(server_class=HTTPServer, handler_class=BaseHTTPRequestHandler):
    server_address = ('', localport)
    httpd = server_class(server_address, handler_class)
    httpd.serve_forever()


class BridgeHTTPRequestHandler(BaseHTTPRequestHandler):
    connections = {}
    def get_socket(self):
        conn = None
        conn_id = None
        try:
            conn_id = self.path.split("/")[-1]
            conn = BridgeHTTPRequestHandler.connections.get(conn_id,None)
        except:
            pass
        return conn, conn_id

    def close_socket(self):
        conn, conn_id = self.get_socket()
        if conn:  conn.close()
        if conn_id:  del BridgeHTTPRequestHandler.connections[conn_id]
        return conn_id

    def do_POST(self):
        try:
            conn_id = self.path.split("/")[-1]
            clen = self.headers.get("Content-Length",None)
            clen = int(clen) if clen else 0
            data = self.rfile.read(clen)

            data = data.split(":")
            (remote_host, remote_port) = data if len(data) == 2 else ("","0")
            remote_port = int(remote_port)
            sys.stderr.write("[{conn_id}] Opening connection to {remote_host}:{remote_port} for {client}...\n".format(
                conn_id=conn_id, remote_host=remote_host, remote_port=remote_port, client=self.client_address))

            try:
                conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn.connect((remote_host, remote_port))
                set_keepalive_linux(conn)
                conn.setblocking(0)
                BridgeHTTPRequestHandler.connections[conn_id] = conn
                sys.stderr.write("[{conn_id}] Connected to {remote_host}:{remote_port} {conn}\n".format(
                    conn_id=conn_id, remote_host=remote_host, remote_port=remote_port, conn=conn))
                self.send_response(201)
                self.end_headers()
            except:
                _, err, _ = sys.exc_info()
                self.send_error(406)  # Not Acceptable
                sys.stderr.write("[{conn_id}] Connection failed: {err}\n".format(conn_id=conn_id, err=err))
        except:
            _, err, _ = sys.exc_info()
            self.send_response(406)  # Not Acceptable
            sys.stderr.write("[{conn_id}] Connection failed: {err}\n".format(
                conn_id=conn_id if conn_id else "unknown", err=err))

    def do_PUT(self):
        conn, conn_id = self.get_socket()
        if not conn: return
        try:

            clen = self.headers.get("Content-Length",None)
            clen = int(clen) if clen else 0
            data = ""
            while clen:
                buf = self.rfile.read(clen)
                data += buf
                clen -= len(buf)

            clen = len(data)
            while clen:
                timeout = 5
                ready_to_read, ready_to_write, in_error = select.select(
                            [],
                            [conn],
                            [conn],
                            timeout)
                if in_error: raise Exception("error in socket")
                if not ready_to_write: Exception("socket timeout")
                slen = conn.send(data)
                clen -= slen
                data = data[slen:]

            self.send_response(200)
            self.end_headers()
        except:
            _, err, _ = sys.exc_info()
            sys.stderr.write("[{conn_id}] Connection closed in remote destination (PUT) {err}\n".format(conn_id=conn_id, err=err))
            self.close_socket()
            self.send_error(410)

    def do_GET(self):
        conn, conn_id = self.get_socket()
        if not conn: return
        try:
            timeout = 0
            ready_to_read, ready_to_write, in_error = select.select(
                        [conn],
                        [],
                        [conn],
                        timeout)
            if in_error: raise Exception("error in socket")
            if not ready_to_read:
                self.send_response(204) # No Content
                self.end_headers()
                return
            data = conn.recv(64*1024)
            if not data: raise Exception("socket closed")
            self.send_head("", data)
            self.wfile.write(data)
        except socket.timeout:
            self.send_response(204) # No Content
            self.end_headers()
        except:
            _, err, _ = sys.exc_info()
            sys.stderr.write("[{conn_id}] GET error: {err}\n".format(conn_id=conn_id if conn_id else "unknown", err=err))
            self.close_socket()
            self.send_error(410)  # Gone

    def do_DELETE(self):
        conn_id = self.close_socket()
        sys.stderr.write("[{conn_id}] Connection closed in client. Trying to close it in remote destination\n".format(conn_id=conn_id))
        self.send_response(200)

    def send_head(self, ctype, content):
        self.send_response(200)
        if content:
            if ctype:
                self.send_header("Content-type", ctype)
            self.send_header("Content-Length", str(len(content)))
        self.end_headers()

    def log_message(self, format, *args):
        pass

run(handler_class = BridgeHTTPRequestHandler)
