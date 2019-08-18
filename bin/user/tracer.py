#!/usr/bin/env python
# Copyright 2019 Matthew Wall
# Distributed under the terms of the GNU Public License (GPLv3)

"""
Driver for collecting data from Tracer MPPT solar charge controller.

tested with model 3210A

default serial settings: 115200-8-1-N

pip install minimalmodbus
"""

import minimalmodbus
import struct
import syslog
import time

import weewx
import weewx.drivers


DRIVER_NAME = 'Tracer'
DRIVER_VERSION = '0.1'


def logmsg(dst, msg):
    syslog.syslog(dst, 'Tracer: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logcrt(msg):
    logmsg(syslog.LOG_CRIT, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)


def loader(config_dict, _):
    return TracerDriver(**config_dict[DRIVER_NAME])

def confeditor_loader():
    return TracerConfEditor()


class TracerConfEditor(weewx.drivers.AbstractConfEditor):
    @property
    def default_stanza(self):
        return """
[Tracer]
    # This section is for Tracer MPPT charge controller

    # Which model of controller is this?
    model = EPEVER

    # RS485 (modbus) serial port
    port = /dev/ttyUSB0

    # How often to poll the device, in seconds
    poll_interval = 10

    # The driver to use
    driver = user.tracer
"""

    def prompt_for_settings(self):
        print "Specify the serial port on which the device is connected, for"
        print "example /dev/ttyUSB0 or /dev/ttyS0 or /dev/tty.usbserial"
        port = self._prompt('port', '/dev/ttyUSB0')
        return {'port': port}


class TracerDriver(weewx.drivers.AbstractDevice):
    # mapping from hardware names to database schema names
    DEFAULT_MAP = {
        'pressure': 'pressure',
    }

    def __init__(self, **stn_dict):
        loginf('driver version is %s' % DRIVER_VERSION)
        self.model = stn_dict.get('model', 'MS-120')
        loginf("model is %s" % self.model)
        port = stn_dict.get('port', Tracer.DEFAULT_PORT)
        loginf("port is %s" % port)
        address = int(stn_dict.get('address', Tracer.DEFAULT_ADDRESS))
        loginf("address is %s" % address)
        baud_rate = int(stn_dict.get('baud_rate', Tracer.DEFAULT_BAUD_RATE))
        timeout = int(stn_dict.get('timeout', Tracer.DEFAULT_TIMEOUT))
        self.poll_interval = int(stn_dict.get('poll_interval', 10))
        loginf("poll interval is %s" % self.poll_interval)
        self.sensor_map = dict(TracerDriver.DEFAULT_MAP)
        if 'sensor_map' in stn_dict:
            self.sensor_map.update(stn_dict['sensor_map'])
        loginf("sensor map: %s" % self.sensor_map)
        self.max_tries = int(stn_dict.get('max_tries', 3))
        self.retry_wait = int(stn_dict.get('retry_wait', 5))
        self.station = Tracer(port, address, baud_rate, timeout)

    @property
    def hardware_name(self):
        return self.model

    def closePort(self):
        self.station.serial.close()
        self.station = None

    def genLoopPackets(self):
        while True:
            data = self._get_with_retries('get_current')
            logdbg("raw data: %s" % data)
            pkt = dict()
            pkt['dateTime'] = int(time.time() + 0.5)
            pkt['usUnits'] = weewx.US
            for k in self.sensor_map:
                if self.sensor_map[k] in data:
                    pkt[k] = data[self.sensor_map[k]]
            yield pkt
            if self.poll_interval:
                time.sleep(self.poll_interval)

    def _get_with_retries(self, method):
        for n in range(self.max_tries):
            try:
                return getattr(self.station, method)()
            except (IOError, ValueError, TypeError) as e:
                loginf("failed attempt %s of %s: %s" %
                       (n + 1, self.max_tries, e))
                time.sleep(self.retry_wait)
        else:
            raise weewx.WeeWxIOError("%s: max tries %s exceeded" %
                                     (method, self.max_tries))


class Tracer(minimalmodbus.Instrument):
    DEFAULT_PORT = '/dev/ttyUSB0'
    DEFAULT_ADDRESS = 1
    DEFAULT_BAUD_RATE = 115200
    DEFAULT_TIMEOUT = 6.0 # seconds

    def __init__(self, port, address, baud_rate, timeout):
        minimalmodbus.BAUDRATE = baud_rate
        minimalmodbus.TIMEOUT = timeout
        minimalmodbus.Instrument.__init__(self, port, address)
        loginf("port: %s" % self.serial.port)
        loginf("serial settings: %s:%s:%s:%s" % (
            self.serial.baudrate, self.serial.bytesize,
            self.serial.parity, self.serial.stopbits))
        self.address = address

    def __enter__(self):
        return self

    def __exit__(self, _, value, traceback):
        pass

    @staticmethod
    def _to_signed(x, bits=16):
        # assumes two's complement enoding of signed integer
        if (x & (1 << (bits - 1))) != 0:
            x = x - (1 << bits)
        return x

    @staticmethod
    def _to_long(a, b):
        return (a << 16) + b

    @staticmethod
    def _to_float(a, b):
        f = struct.unpack('f', struct.pack('>HH', a, b))[0]
#        loginf("to_float: a=%04x b=%04x f=%s" % (a, b, f))
        return f

    @staticmethod
    def _to_calculated(x):
        x = Tracer._to_signed(x)
        if x == -9990:
            return None
        return x * 0.1

    def _read_registers(self, reg, cnt):
        return self.read_registers(reg, cnt)

    def _read_register(self, reg, places=0):
        return self.read_register(reg, places)

    def _read_long(self, reg):
        return self.read_long(reg)

    def get_current(self):
        data = dict()
        x = self._read_registers(0x3000, 100)
        data.update(Tracer._decode_power(x))
        return data


if __name__ == '__main__':
    import optparse

    usage = """%prog [options] [--debug] [--help]"""

    def main():
        syslog.openlog('wee_tracer', syslog.LOG_PID | syslog.LOG_CONS)
        parser = optparse.OptionParser(usage=usage)
        parser.add_option('--version', dest='version', action='store_true',
                          help='display driver version')
        parser.add_option('--debug', dest='debug', action='store_true',
                          help='display diagnostic information while running')
        parser.add_option('--port', dest='port', metavar='PORT',
                          help='serial port to which the station is connected',
                          default=Tracer.DEFAULT_PORT)
        parser.add_option('--address', dest='address', metavar='ADDRESS',
                          help='modbus slave address', type=int,
                          default=Tracer.DEFAULT_ADDRESS)
        parser.add_option('--baud-rate', dest='baud_rate', metavar='BAUD_RATE',
                          help='modbus slave baud rate', type=int,
                          default=Tracer.DEFAULT_BAUD_RATE)
        parser.add_option('--timeout', dest='timeout', metavar='TIMEOUT',
                          help='modbus timeout, in seconds', type=int,
                          default=Tracer.DEFAULT_TIMEOUT)
        (options, _) = parser.parse_args()

        if options.version:
            print "driver version %s" % DRIVER_VERSION
            exit(1)

        if options.debug is not None:
            syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_DEBUG))
        else:
            syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_INFO))

        if False:
            test_mmb(options.port, options.address, options.baud_rate,
                     options.timeout, options.debug)
        if False:
            test_mbtk(options.port, options.address, options.baud_rate,
                      options.timeout, options.debug)
        if True:
            test_dev(options.port, options.address, options.baud_rate,
                     options.timeout, options.debug)

    def test_dev(port, address, baud_rate, timeout, debug):
        station = Tracer(port, address, baud_rate, timeout)
        station.debug = debug
        data = station.get_current()
        print "current values: ", data

    def test_mmb(port, address, baud_rate, timeout, debug):
        print "\n\nminimalmodbus"
        import minimalmodbus
        minimalmodbus.BAUDRATE = baud_rate
        minimalmodbus.TIMEOUT = timeout
        instrument = minimalmodbus.Instrument(port, address)
        instrument.debug = debug
        print instrument.read_register(100, 1)
        print instrument.read_registers(100, 11)
        print instrument.read_register(200, 1)
        print instrument.read_registers(200, 92)

    def test_mbtk(port, address, baud_rate, timeout, debug):
        print "\n\nmodbus-tk"
        import modbus_tk
        import modbus_tk.defines as cst
        from modbus_tk import modbus_rtu
        import serial
        if debug:
            logger = modbus_tk.utils.create_logger("console")
        master = modbus_rtu.RtuMaster(
            serial.Serial(port=port, baudrate=baud_rate,
                          bytesize=8, parity='N', stopbits=1))
        master.set_timeout(timeout)
        if debug:
            master.set_verbose(True)
        print master.execute(address, cst.READ_HOLDING_REGISTERS, 100, 1)
        print master.execute(address, cst.READ_HOLDING_REGISTERS, 100, 11)
        print master.execute(address, cst.READ_HOLDING_REGISTERS, 200, 1)
        print master.execute(address, cst.READ_HOLDING_REGISTERS, 200, 92)

    main()
