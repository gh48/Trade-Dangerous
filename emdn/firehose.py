#! /usr/bin/env python
#---------------------------------------------------------------------
# Copyright (C) Oliver 'kfsone' Smith 2014 <oliver@kfs.org>:
#  You are free to use, redistribute, or even print and eat a copy of
#  this software so long as you include this copyright notice.
#  I guarantee there is at least one bug neither of us knew about.
#---------------------------------------------------------------------
# Elite Market Data Net :: Modules :: Main Module

"""
    "Elite Market Data Net" (EMDN) is a ZeroMQ based service that
    provides a near-realtime feed of market scrapes from the Elite
    Dangerous universe. This feed is called the "firehose".

    emdn.ItemRecord class encapsulates a record as described by
    the EMDN network.

    emdn.Firehose class encapsulates a connection to retrieve
    ItemRecords in an iterative fashion.

    Example:

      from emdn.firehose import Firehose
      firehose = Firehose()
      # use firehose = Firehose(ctx=ctx) if you have your own zmq.Context

      # wait upto 10 seconds and retrieve upto 2 records:
      for itemRec in firehose.drink(records=2, timeout=10.0)
          pass

      # print everything else we receive
      for itemRec in firehose.drink():
          print(itemRec)
"""

try:
    import zmq
except ImportError:
    raise ImportError("This module requires the ZeroMQ library to be installed. The easiest way to obtain this is to type: pip install pyzmq") from None
try: from itemrecord import ItemRecord
except ImportError: from . itemrecord import ItemRecord

import zlib
import json
import time

class Firehose(object):
    """
        Encapsulates a connection to the Elite Market Data Network (EMDN)
        live feed of price updates.
        Also handles "file:///" URIs for local testing, e.g.
            hose = Firehose("file://./emdn.csv")
        reads from a local file called 'emdn.csv'.
    """

    defaultURI = 'tcp://firehose.elite-market-data.net:9500'

    def __init__(self, uri=None, ctx=None, debug=0):
        self.__uri = uri or Firehose.defaultURI
        self.debug = debug

        if self.__uri.find("file://") == 0:
            def _poll(timeout):
                return 1
            def _read(nonBlocking=False):
                result = self.__filehandle.readline().strip()
                if not result: raise EOFError()
                return result

            self.poll = _poll
            self.read = _read
            self.__filehandle = open(self.__uri[7:], 'r')
        else:
            def _poll(timeout):
                #### TODO: Use a ZMQ Poller so we can catch ctrl-c
                return self.__socket.poll(timeout)
            def _read(nonBlocking=False):
                flags = zmq.NOBLOCK if nonBlocking else 0
                compressed = self.__socket.recv(flags)
                uncompressed = zlib.decompress(compressed)
                jsonString = uncompressed.decode('utf-8')
                result = json.loads(jsonString)
                if not result: raise EOFError()
                return result

            # All ZMQ operations are done through a Context,
            # so use one we're given or create one for ourselves.
            self.__ctx = ctx or zmq.Context()

            # EMDN is using the pub/sub model, a bit overzealously,
            # so we need a subscriber socket subscribed to nothing.
            self.__socket = self.__ctx.socket(zmq.SUB)
            self.__socket.setsockopt(zmq.SUBSCRIBE, ''.encode())
            self.__socket.connect(self.__uri)
            self.poll = _poll
            self.read = _read


    def json_to_record(self, data):
        record = None
        try:
            dataType = data['type']
            if dataType == 'marketquote':
                try:
                    message = data['message']
                    try:
                        return ItemRecord(
                            askingCr=message['buyPrice'],
                            payingCr=message['sellPrice'],
                            demand=message['demand'],
                            demandLevel=message['demandLevel'],
                            stock=message['stationStock'],
                            stockLevel=message['stationStockLevel'],
                            category=message['categoryName'],
                            item=message['itemName'],
                            location=message['stationName'],
                            timestamp=message['timestamp']
                        )
                    except KeyError as e:
                        print("jsonData['message']:", message)
                        raise ValueError("json marketquote data is missing a required field: {}".format(e))
                except KeyError: # missing 'message' element
                    if self.debug > 2: print("# json data didn't contain a 'message'")
                    if self.debug > 3: print(data)
            else:   # not a marketquote
                if self.debug > 2: print("# ignoring '{}'".format(dataType))
        except KeyError: # missing 'type' field
            if self.debug > 2: print("# invalid data, did not contain a 'type' key")
            if self.debug > 3: print(data)

        return None


    def drink(self, records=None, timeout=None, burst=False):
        """
            Drink from the firehose, yielding the data we retrieve as ItemRecords.

            Keyword arguments:
            records -- maximum number of records to yield before exiting. Default, 0, is unlimited.
            timeout -- maximum total time to wait for data.
            burst   -- set True to stop after the first set of records are retrieved.

            e.g.
              drink(records=50, timeout=300)
                Reads until we have received 50 seconds or 300 seconds have passed.

              drink(records=50, timeout=10.5, burst=True)
                Reads until the first of:
                  50 records have been received,
                  10.5 seconds has elapsed,
                  the first burst of data has been drained.
        """

        maxPollDuration = timeout
        recordsRemaining = records or -1

        cutoffTime = time.clock() + timeout if timeout else None

        while recordsRemaining:
            if timeout:
                maxPollDuration = int(((cutoffTime - time.clock()) * 1000) + 0.9999)
                if maxPollDuration <= 0:
                    return
            pollResult = self.poll(timeout=maxPollDuration)
            if pollResult:
                while recordsRemaining:
                    try:
                        jsData = self.read(nonBlocking=True)
                        if self.debug > 3: print(jsData)
                    except EOFError:
                        return
                    except (zmq.error.Again, BlockingIOError, InterruptedError):
                        break

                    record = self.json_to_record(jsData)
                    if record:
                        yield record
                        recordsRemaining -= 1
                if burst:
                    return
