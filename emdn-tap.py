#! /usr/bin/env python
#---------------------------------------------------------------------
# Copyright (C) Oliver 'kfsone' Smith 2014 <oliver@kfs.org>:
#  You are free to use, redistribute, or even print and eat a copy of
#  this software so long as you include this copyright notice.
#  I guarantee there is at least one bug neither of us knew about.
#---------------------------------------------------------------------
# TradeDangerous :: Elite Market Data Network :: EMDN Tap
#  Taps into the Elite Market Data Net to retrieve prices observed
#  by other players.
#  For more information on EMDN see Andreas' post:
#   http://forums.frontier.co.uk/showthread.php?t=23585

######################################################################
# imports

import argparse
import re
import sys
import time
import pathlib

from tradedb import TradeDB
from tradecalc import localedNo
from emdn.firehose import Firehose
from data import prices

######################################################################
# global variables: jeebus doesn't love me anymore.

warnOnly = False
warningFh = sys.stderr

blackMarketItems = frozenset([
    'battleweapons',
])


class OldPrice(object):
    __slots__ = ('payingCr', 'askingCr')
    def __init__(self, payingCr, askingCr):
        self.payingCr, self.askingCr = payingCr, askingCr


######################################################################
# process command line

def processCommandLine():
    """
        Process the command line with argparse.
    """

    global warnOnly, warningFh

    parser = argparse.ArgumentParser(
        description="""
            Connects to the Elite Market Data Network (EMDN) firehose
            and saves incoming price updates to the TradeDangerous database.
        """,
        epilog="""
            This tool is provided as an OPTIONAL alternative to manually
            updating the Elite Dangerous database (see trade.py update --help).
            EMDN is developed by another E:D fan, Andreas. For details see his forum post: http://forums.frontier.co.uk/showthread.php?t=23585.
        """
    )
    parser.add_argument('--firehose', '-u',  help='URI for the firehose. Default={}'.format(Firehose.defaultURI), default=None)
    parser.add_argument('--file',     '-f',  help='Filename for the firehose. Default=None.', default=None)
    parser.add_argument('--db',       '-d',  help='SQLite database to write to. Default={}'.format(TradeDB.defaultDB))
    parser.add_argument('--seconds',  '-s',  help='Maximum seconds to run for. Default=unlimited.', type=int, default=0)
    parser.add_argument('--minutes',  '-m',  help='Maximum minutes to run for. Default=unlimited.', type=int, default=0)
    parser.add_argument('--records',  '-r',  help='Maximum records to retrieve. Default=unlimited.', type=int, default=0)
    parser.add_argument('--verbose',  '-v',  help='Increase verboseness.', action='count', default=0)
    parser.add_argument('--no-writes',       help='Don\'t actually write to the database.', action='store_true', default=False, dest='noWrites')
    parser.add_argument('--warn',            help='Demote unrecognized items/stations to warnings.', action='store_true', default=False)
    parser.add_argument('--warn-to',         help='Same as --warn but specifies file to write to.', dest='warnTo')
    parser.add_argument('--commit',          help='Automatically commit after this many seconds, 0 disables. Default: 90', type=int, default=90)

    pargs = parser.parse_args()

    if pargs.firehose and pargs.file:
        print("--firehose and --file are mutually exclusive.")
        sys.exit(1)

    pargs.firehoseURI = pargs.firehose
    if pargs.file and (pargs.file[0] in [ '/', '.', '\\' ]):
        pargs.firehoseURI = re.sub(r'\\', '/', 'file://' + pargs.file)
    elif pargs.file:
        pargs.firehoseURI = 'file://./' + pargs.file

    pargs.duration = pargs.minutes * 60 + pargs.seconds

    print("* Fetching EMDN data from {} to {}.".format(
            pargs.firehoseURI or '['+Firehose.defaultURI+']',
            pargs.db or '['+TradeDB.defaultDB+']'
        ))
    print("* Automatic commits {}.".format(
            'every {} seconds'.format(pargs.commit) if pargs.commit else 'disabled'
        ))

    if pargs.warnTo:
        pargs.warn = True
        warningFh = open(pargs.warnTo, 'w', 1)
        print("# warnings from {}".format(__name__), file=warningFh)

    warnOnly = pargs.warn

    return pargs


######################################################################
# UI Order tracking: items that weren't in the DB need to have a
# useful UIOrder attached to them, this makes a best guess.

uiOrders = {}
oldPrices = {}

def getStationCat(stationID, catID):
    global uiOrders
    key = (stationID << 32) | catID
    try: result = uiOrders[key]
    except KeyError:
        result = uiOrders[key] = {}
    return result


def getItemUIOrder(stationID, catID, itemID):
    global uiOrders
    cat = getStationCat(stationID, catID)
    try: result = cat[itemID]
    except KeyError:
        lastOrder = max(cat.values()) if cat else 0
        result = cat[itemID] = lastOrder + 1
    return result


def getOldPrice(stationID, itemID):
    global oldPrices
    key = (stationID << 32) | itemID
    try: result = oldPrices[key]
    except KeyError:
        result = oldPrices[key] = OldPrice(0, 0)
    return result

def loadPriceData(db):
    """ Load current prices and UI order values """
    stmt = """
        SELECT  station_id, category_id, Price.item_id, ui_order, sell_to, buy_from
        FROM  Price INNER JOIN Item ON Price.item_id = Item.item_id
    """
    cur = db.execute(stmt)
    for (stationID, catID, itemID, uiOrder, payingCr, askingCr) in cur:
        getStationCat(stationID, catID)[itemID] = uiOrder
        oldPrice = getOldPrice(stationID, itemID)
        oldPrice.payingCr, oldPrice.askingCr = payingCr, askingCr


######################################################################

def warning(*args, **kwargs):
    """
       Write a message to the warning channel, and duplicate to
       stdout if verbosity > 1.
    """

    import datetime
    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    warning(now, *args, file=warningFh, **kwargs)
    if warning.verbose:
        print('#', *args, **kwargs)


warning.verbose = 0


######################################################################

def bleat(category, name, *args, **kwargs):
    """
        Throttled (once per category+name only) reporting
        of errors. Exit after first report if not warnOnly.
        Bleat: rhymes with 'tweet'.
    """
    bleatKey = '{}:{}'.format(category, name)

    if not bleatKey in bleat.bleated:
        warning(now, '[{} >> {}]'.format(category, name), *args, **kwargs)
        bleat.bleated.add(bleatKey)

    if not warnOnly:
        sys.exit(1)


bleat.bleated = set()


######################################################################
# Save data to the db

commitStmt = """
    INSERT OR REPLACE INTO Price (item_id, station_id, ui_order, sell_to, buy_from, modified, demand, demand_level, stock, stock_level)
    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
"""

def commit(tdb, db, recordsSinceLastCommit, pargs):
    """
        Write uncommitted records to the database and rebuild the prices file.

        If --no-writes was specified, does everything except the actual commit.
    """
    if not recordsSinceLastCommit:
        if pargs.verbose > 2:
            print("-> no records to commit.")
        return

    if pargs.verbose:
        print("-> Save {} updates".format(len(recordsSinceLastCommit)) + (" [disabled]" if pargs.noWrites else ""))
        if pargs.verbose > 3:
            print("\n".join(['#  {}'.format(str(items)) for items in recordsSinceLastCommit]))
    if not pargs.noWrites:
        # Save the records.
        db.executemany(commitStmt, recordsSinceLastCommit)
        db.commit()

        # Rebuild prices
    dbFilename = tdb.dbURI
    if pargs.verbose > 1:
        print("-> Rebuild prices file" + (" [disabled]" if pargs.noWrites else ""))

    if not pargs.noWrites:
        with tdb.pricesPath.open("w") as pricesFile:
            prices.dumpPrices(dbFilename, withModified=True, file=pricesFile)
        time.sleep(0.001)
        pathlib.Path(dbFilename).touch()


######################################################################

def main():
    records = 0

    pargs = processCommandLine()
    warning.verbose = pargs.verbose

    # Open the local TradeDangerous database
    dbFilename = pargs.db or TradeDB.defaultDB
    tdb = TradeDB(dbFilename=dbFilename, debug=1 if pargs.verbose else 0)
    db = tdb.getDB()

    loadPriceData(db)

    # Open a connection to the firehose.
    firehose = Firehose(pargs.firehoseURI, debug=pargs.verbose)

    if pargs.verbose: print("* Capture starting.")

    now = time.time()
    lastCommit, duration = now, pargs.duration
    recordsSinceLastCommit = []
    if duration:
        endOfRun = now + duration
    else:
        endOfRun = None

    def consumeRecord(rec):
        nonlocal pargs, records, recordsSinceLastCommit, tdb

        if rec.payingCr == 0 and rec.askingCr == 0:
            if pargs.verbose > 2:
                print("# Ignoring 0/0 entry for {} @ {}/{}".format(rec.item, rec.system, rec.station))
        if rec.payingCr < 0 or rec.askingCr < 0 \
        or rec.stock < 0 or rec.stockLevel < 0 \
        or rec.demand < 0 or rec.demandLevel < 0:
            bleat("item", '{}@{}/{}'.format(rec.item, rec.system, rec.station), "Invalid (negative) value in price/stock fields")
            return

        records += 1

        if pargs.verbose and (records % 1000 == 0):
            print("# At {} captured {} records.".format(rec.timestamp, records))

        if pargs.verbose > 1:
            paying = localedNo(rec.payingCr)+'cr' if rec.payingCr else '    -    '
            asking = localedNo(rec.askingCr)+'cr' if rec.askingCr else '    -    '
            desc = '{} @ {}/{}'.format(rec.item, rec.system, rec.station)
            extra = " | {:>6}L{} {:>6}L{}".format(rec.demand, rec.demandLevel, rec.stock, rec.stockLevel) if pargs.verbose > 2 else ""
            print("{} {:.<65} {:>9} {:>9}{}".format(rec.timestamp, desc, paying, asking, extra))
 
        # As of Beta 1.04, if you are carrying an item that the station doesn't handle
        # the UI shows a fake entry with the prices from the station you bought the
        # item from.

        if rec.demandLevel == 0 and rec.stockLevel == 0:
            if pargs.verbose > 2:
                warning("Ignoring no-demand entry for {} @ {}/{}".format(rec.item, rec.system, rec.station))
            return

        # Find the item in the price database to get its data and make sure
        # it matches the category we expect to see it listed in.
        try:
            item = tdb.lookupItem(rec.item)
            if TradeDB.normalizedStr(item.category.dbname) != TradeDB.normalizedStr(rec.category):
                bleat("item", rec.item, "\aCATEGORY MISMATCH: {}/{} => item: {}/{} aka {}".format(rec.category, rec.item, item.category.dbname, item.dbname, item.altname or 'None'))
                return
        except LookupError:
            if not rec.item in blackMarketItems:
                bleat("item", rec.item, "UNRECOGNIZED ITEM:", rec.item)
            return

        # Lookup the station.
        try: system = tdb.lookupSystem(rec.system)
        except LookupError:
            bleat("system", rec.system, "UNRECOGNIZED SYSTEM:", rec.system)
            return

        try: station = tdb.lookupStation(rec.station, system=system)
        except LookupError:
            bleat("station", rec.station, "UNRECOGNIZED STATION:", rec.system, rec.station)
            return

        oldPrice = getOldPrice(station.ID, item.ID)
        if oldPrice and oldPrice.payingCr and oldPrice.askingCr and pargs.verbose:
            desc = '{} @ {}/{}'.format(rec.item, rec.system, rec.station)
            payingDiff, askingDiff = rec.payingCr - oldPrice.payingCr, rec.askingCr - oldPrice.askingCr
            if payingDiff != 0 and askingDiff != 0:
                payingChange = "{}{}cr".format('+' if payingDiff > 0 else '-', localedNo(abs(payingDiff))) if payingDiff != 0 else '    -    '
                askingChange = "{}{}cr".format('+' if askingDiff > 0 else '-', localedNo(abs(askingDiff))) if askingDiff != 0 else '    -    '
                print("{:<19} {:.<65} {:>9} {:>9}".format(
                    '' if pargs.verbose > 1 else rec.timestamp,
                    '' if pargs.verbose > 1 else desc,
                    payingChange, askingChange
                ))
                oldPrice.payingCr, oldPrice.askingCr = rec.payingCr, rec.askingCr

        uiOrder = getItemUIOrder(station.ID, item.category.ID, item.ID)
        recordsSinceLastCommit.append([ item.ID, station.ID, uiOrder, rec.payingCr, rec.askingCr, rec.demand, rec.demandLevel, rec.stock, rec.stockLevel ])


    try:
        now = time.time()
        while True:
            nextCommit = lastCommit + pargs.commit
            timeLeft = cutoffTime - now if duration else nextCommit - now
            timeout = min(timeLeft, nextCommit - now) or None
            for rec in firehose.drink(records=pargs.records, timeout=timeout):
                consumeRecord(rec)
            if pargs.verbose > 2:
                print("- tick")
            lastCommit = now = time.time()
            commit(tdb, db, recordsSinceLastCommit, pargs)
            recordsSinceLastCommit = []
            if endOfRun and now >= endOfRun:
                break
    except KeyboardInterrupt:
        print("Ctrl-C pressed, stopping.")

    if pargs.verbose:
        print("Captured {} records total.".format(records))

    commit(tdb, db, recordsSinceLastCommit, pargs)


if __name__ == "__main__":
    main()
