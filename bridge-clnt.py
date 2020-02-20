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

import requests
import urllib2
import socket
import select
import sys
import uuid
import threading
from signal import signal, SIGINT, SIGHUP, SIGTERM
from sys import exit

# GLOBALS
localport = -1
localhost = "127.0.0.1"
remoteport = -1
remotehost = None
conn_id = ""
connected = False
local_server_conn = None
remotebuf = None
localbuf = None
closing = False

args = sys.argv
args = args[1:]

def usage(err=False):
    sys.stderr.write( "Usage: bridge-clnt PORT PROXY_URL REMOTE_HOST REMOTE_PORT\n")
    exit(1)

if len(args) < 4: usage(True)
try:
    for idx, arg in enumerate(args):
        if arg == "-h":
            print(idx, arg)
            usage()
        elif idx == 0:
            print(idx, arg)
            localport = arg
        elif idx == 1:
            print(idx, arg)
            url = arg
        elif idx == 2:
            print(idx, arg)
            remotehost = arg
        elif idx == 3:
            print(idx, arg)
            remoteport = arg
        else:
            print(idx, arg)
            usage(True)
except:
    print("Error parsing arguments")
    usage(True)

try:
    localport = int(localport)
    remoteport = int(remoteport)
except:
    _, err, _ = sys.exc_info()
    sys.stderr.write( "Error: invalid port given (local: {}, remote: {})\n".format(localport,remoteport))
    usage(True)

def set_keepalive_linux(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """set tcp keepalive on an open socket.

    It activates after 1 second (after_idle_sec) of idleness,
    then sends a keepalive ping once every 3 seconds (interval_sec),
    and closes the connection after 5 failed ping (max_fails), or 15 seconds
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)

def closeService():
    global local_server_conn
    if local_server_conn:
        local_server_conn.close()
    local_server_conn = None

def waitforServiceRequest():
    global localhost, localport, remotehost, remoteport, conn_id, connected, local_server_conn

    sys.stderr.write( "Opening local port {localport}\n".format(localport=localport))
    local_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    local_server.bind((localhost, localport))
    sys.stderr.write( "Waiting for local connection to port {localport}\n".format(localport=localport))
    local_server.listen(1)
    local_server_conn, _ = local_server.accept()
    # Keep connection alive
    set_keepalive_linux(local_server_conn)
    # Make socket non-blocking
    local_server_conn.setblocking(0)
    #unique Connection IDentifier
    conn_id = str(uuid.uuid4())
    connected = False

# Disconnect the connection
def disconnect():
    global connected, conn_id
    if connected:
        requests.delete(url + "/" + conn_id)
    connected = False
    conn_id = None

# Open the connection
def open_connection():
    global connected
    sys.stderr.write( "Opening connection over HTTP bridge for {remotehost}:{remoteport} ({conn_id})\n".format(
                remotehost=remotehost, remoteport=remoteport, conn_id=conn_id))
    rsp = requests.post(url + "/" + conn_id, data="{remotehost}:{remoteport}".format(
                remotehost=remotehost,remoteport=remoteport))
    if rsp.status_code == requests.status_codes.codes.CREATED:
        connected=True
    else:
        sys.stderr.write("The bridge failed to connect to the remote location.\n")
        connected=False
    # Not connected, nothing more to do
    if not connected:
        exit(1)

def main_loop():
    global connected, closing, remotebuf, local_server_conn
    timeout = 3
    while connected or remotebuf:
        if not local_server_conn:
            sys.stderr.write( "Local Request has closed.\n")
            break
        try:
            rlist = [local_server_conn] if not closing else []
            wlist = [local_server_conn]
            xlist = [local_server_conn]

            ready_to_read, ready_to_write, in_error = select.select(
                    rlist, wlist, xlist, timeout)

            if in_error: raise Exception("socket error")
            if ready_to_read:
                local_read_remote_write()
            if ready_to_write:
                local_write_remote_read()
        except select.error:
            _, err, _ = sys.exc_info()
            sys.stderr.write("(main loop) Select Excption: {}\n".format(err))
            disconnect()
            break
        except:
            _, err, _ = sys.exc_info()
            sys.stderr.write("(main loop) Exception: {}\n".format(err))
            disconnect()
            break

def local_write_remote_read():
    global connected, remotebuf, local_server_conn, closing
    while True:
        # First, try to empty the buffer
        try:
            if remotebuf:
                # Non-blocking write to Service
                slen = local_server_conn.send(remotebuf)
                if slen:
                    # Adjust for actual amount sent
                    remotebuf = remotebuf[slen:]
                    # Try sending again
                    continue

                # Nothing transferred, not ready, so return
                return
            # Nothing available to send, so fall through and top up the buffer
            if closing:
                closeService()
                disconnect()
                return
        except socket.timeout:
            # NOT READY to send, Service is not sinking the data
            return
        except:
            # Some other exception, try to clean up
            _, err, _ = sys.exc_info()
            sys.stderr.write( "(lw/rr) Exception sending to local service: {}\n".format(err))
            disconnect()
            return

        try:
            # We get here if there is nothing to send
            if remotebuf:  raise Exception("(lw/rr) Programming error, shouldn't happen")

            # Get more data from remote service, should return right away if nothing is available
            rsp = requests.get(url + "/" + conn_id, timeout=3)
            if rsp.status_code == requests.status_codes.codes.OK:
                # Detect unexpected condition
                if not rsp.content: raise Exception("(lw/rr) Returned OK but no data")
                # Got data
                remotebuf = rsp.content if not remotebuf else remotebuf + rsp.content
                # Retry write to local service
                continue
            elif rsp.status_code == requests.status_codes.codes.NO_CONTENT:
                # Nothing available
                return
            elif rsp.status_code == requests.status_codes.codes.GONE:
                sys.stderr.write( "(lw/rr) Proxy lost connection to remote\n")
                disconnect()
                return
            elif rsp.status_code == requests.status_codes.codes.NOT_FOUND:
                sys.stderr.write( "(lw/rr) Proxy connection not recognized by Proxy\n")
                disconnect()
                # Continue, there may be remaining data to send to the local service
            else:
                raise Exception("response exception")
        except socket.timeout:
            # HTTP Communications timeout, allow retry
            return
        except:
            _, err, _ = sys.exc_info()
            err = "(lw/rr) Connection to bridge failed. {}\n".format(err)
            raise Exception(err)

def local_read_remote_write():
    global connected, localbuf, closing, local_server_conn
    while connected and not closing:
        try:
            if localbuf:
                rsp = requests.put(url + "/" + conn_id, localbuf)
                if rsp.status_code == requests.status_codes.codes.OK:
                    # Buffer sent, clear it
                    localbuf = None
                    return
                elif rsp.status_code == requests.status_codes.codes.GONE:
                    sys.stderr.write( "(lr/rw) Proxy detected remote connection closed\n")
                    disconnect()
                    return
                else:
                    sys.stderr.write( "(lr/rw) Proxy Error: {}\n".format(rsp.status_code))
                    disconnect()
                    return
        except:
            _, err, _ = sys.exc_info()
            err = "(lr/rw) Connection to bridge failed. {}\n".format(err)
            raise Exception(err)

        try:
            # Non-blocking read
            tmpbuf = local_server_conn.recv(64*1024)
            if not tmpbuf:
                # Socket is closed or closing
                closing = True
                # Fall through to flush any remaining
            else:
                # Got Data
                localbuf = tmpbuf if not localbuf else localbuf + tmpbuf
        except socket.timeout:
            return
        except:
            _, err, _ = sys.exc_info()
            err = "(lr/rw) Exception: {}\n".format(err)
            sys.stderr.write(err)
            disconnect()
            return


def handler(signal_received, frame):
    # Handle any cleanup here
    sys.stderr.write('SIGINT or CTRL-C detected. Exiting gracefully\n')
    disconnect()
    closeService()
    exit(0)


if __name__ == "__main__":
    signal(SIGINT, handler)
    signal(SIGHUP, handler)
    signal(SIGTERM, handler)
    try:
        waitforServiceRequest()
        open_connection()
        main_loop()
    except:
        _, err, _ = sys.exc_info()
        err = "(lr/rw) Exception: {}\n".format(err)
        sys.stderr.write(err)
        disconnect()
        closeService()


