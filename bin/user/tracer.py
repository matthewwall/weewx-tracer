#!/usr/bin/env python
# Copyright 2019 Matthew Wall
# Distributed under the terms of the GNU Public License (GPLv3)

"""
Driver for collecting data from Tracer MPPT solar charge controller.

tested with model 3210A

default serial settings: 115200-8-1-N

use sudo when running, otherwise you will get this exception:
  engine: Unable to load driver: 'NoneType' object has no attribute 'interCharTimeout'

pip install pymodbus

Credits

Thanks to lewismoten for publishing a pymodbus implementation:
  https://github.com/lewismoten/solar-log/tree/master/charge-controller
"""

# TODO: enable get/set time

from pymodbus.client.sync import ModbusSerialClient
from pymodbus.mei_message import ReadDeviceInformationRequest
from pymodbus.constants import DeviceInformation

import ctypes
import datetime
import syslog
import time

import weewx
import weewx.drivers
import weewx.units

DRIVER_NAME = 'Tracer'
DRIVER_VERSION = '0.2'


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


schema = [('dateTime',   'INTEGER NOT NULL UNIQUE PRIMARY KEY'),
          ('usUnits',    'INTEGER NOT NULL'),
          ('interval',   'INTEGER NOT NULL'),
          ('ambient_temperature',  'REAL'), # degree_C
          ('battery_temperature', 'REAL'), # degree_C
          ('battery_current', 'REAL'), # amp
          ('battery_voltage', 'REAL'), # volt
          ('consumed_energy', 'REAL'),  # watt-hour
          ('generated_energy', 'REAL'), # watt-hour
]

weewx.units.obs_group_dict['ambient_temperature'] = 'group_temperature'
weewx.units.obs_group_dict['battery_temperature'] = 'group_temperature'
weewx.units.obs_group_dict['battery_current'] = 'group_amp'
weewx.units.obs_group_dict['battery_current'] = 'group_volt'
weewx.units.obs_group_dict['consumed_energy'] = 'group_energy' # watt-hour
weewx.units.obs_group_dict['generated_energy'] = 'group_energy' # watt-hour
try:
        # weewx prior to 3.7.0.  for 3.7.0+ this goes in the weewx config file
        weewx.accum.extract_dict['grid_energy'] = weewx.accum.Accum.sum_extract
except AttributeError:
        pass


BATTERY_TYPES = {
    0: "user defined",
    1: "sealed",
    2: "gel",
    3: "flooded",
}
LOAD_CONTROLLING_MODES = {
    0: "manual control",
    1: "light on/off",
    2: "light on+ timer",
    3: "time control",
}
VOLTAGE_CODES = {
    0: "auto recognize",
    1: "12V",
    2: "24V",
}
LOAD_TIMING_CONTROL_SELECTION = {
    0: "using timer 1",
    1: "using timer 2",
}
MANAGEMENT_MODES = {
    0: "voltage compensation",
    1: "SOC",
}
YES_NO = {
    0: "no",
    1: "yes",
}
ON_OFF = {
    0: "off",
    1: "on",
}

def value32(lo, hi):
    return ctypes.c_int(lo + (hi << 16)).value / 100
def value16(value):
    return ctypes.c_short(value).value / 100
def value8(value):
    return [value >> 8, value & 0xFF]
def volts(x):
    return value16(x)
def watts(lo, hi):
    return value32(lo, hi)
def kwh(lo, hi):
    return value32(lo, hi)
def amps(lo, hi=None):
    if hi is not None:
        return value32(lo, hi)
    return value16(lo)
def amp_hours(x):
    return x
def milliohms(x):
    return value16(x)
def temperature(x):
    return value16(x) # degree C
def percent(x):
    return value16(x) * 100
def tons(lo, hi):
    return value32(lo, hi)
def coeff(x):
    return value16(x)
def battery_type(x):
    return BATTERY_TYPES.get(x, x)
def load_controlling_mode(x):
    return LOAD_CONTROLLING_MODES.get(x, x)
def voltage_code(x):
    return VOLTAGE_CODES.get(x, x)
def load_timing_control(x):
    return LOAD_TIMING_CONTROL_SELECTION.get(x, x)
def management_mode(x):
    return MANAGEMENT_MODES.get(x, x)
def to_time(second, minute, hour):
    return datetime.time(hour, minute, second)
def to_datetime(second_minute, hour_day, month_year):
    sm = value8(second_minute)
    hd = value8(hour_day)
    my = value8(month_year)
    return datetime.datetime(2000 + my[0], my[1], hd[0], hd[1], sm[0], sm[1])
def days(x):
    return x
def minutes(x):
    return x
def seconds(x):
    return x
def hour_minute(x):
    hm = value8(x)
    return "%s:%s" % (hm[0], hm[1])
def yes_no(x):
    return YES_NO.get(x, x)
def on_off(x):
    return ON_OFF.get(x, x)


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
        'outTemp': 'ambient_temperature',
        'battery_temperature': 'battery_temperature',
        'battery_current': 'battery_current',
        'battery_voltage': 'battery_voltage',
        'consumed_energy': 'consumed_energey_total',
        'consumed_energy_today': 'consumed_energy_today',
        'generated_energy': 'generated_energy_total',
        'generated_energy_today': 'generated_energy_today',
    }

    def __init__(self, **stn_dict):
        loginf('driver version is %s' % DRIVER_VERSION)
        self.model = stn_dict.get('model', 'MS-120')
        loginf("model is %s" % self.model)
        port = stn_dict.get('port', Tracer.DEFAULT_PORT)
        loginf("port is %s" % port)
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
        self.station = Tracer(port, baud_rate, timeout)
        if not self.station.connect():
            raise weewx.WeeWxIOError("cannot connect to device")
        loginf('device info: %s' % self.station.get_info())

    @property
    def hardware_name(self):
        return self.model

    def closePort(self):
        self.station.close()
        self.station = None

    def genLoopPackets(self):
        while True:
            data = dict()
            data.update(self._get_with_retries('get_data'))
            logdbg("raw data: %s" % data)
            pkt = {
                'dateTime': int(time.time() + 0.5),
                'usUnits': weewx.METRIC,
            }
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


class Tracer(ModbusSerialClient):
    CHARGE_CONTROLLER_UNIT = 1
    DEFAULT_PORT = '/dev/ttyUSB0'
    DEFAULT_BAUD_RATE = 115200
    DEFAULT_TIMEOUT = 1 # seconds

    def __init__(self, port, baud_rate, timeout):
        super(Tracer, self).__init__(method='rtu', port=port,
                                     baudrate=baud_rate, timeout=timeout)
        self.unit = Tracer.CHARGE_CONTROLLER_UNIT
        loginf("port: %s" % port)

    def __enter__(self):
        return self

    def __exit__(self, _, value, traceback):
        pass

    def get_info(self):
        basic = self.execute(ReadDeviceInformationRequest(
            DeviceInformation.Basic, unit=self.unit))
        regular = self.execute(ReadDeviceInformationRequest(
            DeviceInformation.Regular, unit=self.unit))
        return {
            'company': basic.information[0],
            'product': basic.information[1],
            'version': basic.information[2],
            'serial': regular.information[3],
        }

    def get_ratings(self):
        data = dict()
        r = self.read_input_registers(0x3000, 9, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x3000': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x3000': 'read failed'})
        else:
            data.update({
                'rated_input_voltage': volts(r.registers[0]),
                'rated_input_current': amps(r.registers[1]),
                'rated_input_power': watts(r.registers[2], r.registers[3]),
                'rated_output_voltage': volts(r.registers[4]),
                'rated_output_current': amps(r.registers[5]),
                'rated_output_power': watts(r.registers[6], r.registers[7]),
                'charging_mode': r.registers[8],
            })
        r = self.read_input_registers(0x300E, 1, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x300E': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x300E': 'read failed'})
        else:
            data.update({
                'rated_output_current_of_load': amps(r.registers[0]),
            })
        return data

    def get_statistics(self):
        data = dict()
        r = self.read_input_registers(0x3300, 31, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x3300': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x3300': 'read failed'})
        else:
            data.update({
                'input_voltage_today_max': volts(r.registers[0]),
                'input_voltage_today_min': volts(r.registers[1]),
                'battery_voltage_today_max': volts(r.registers[2]),
                'battery_voltage_today_min': volts(r.registers[3]),
                'consumed_energy_today': kwh(r.registers[4], r.registers[5]),
                'consumed_energy_month': kwh(r.registers[6], r.registers[7]),
                'consumed_energy_year': kwh(r.registers[8], r.registers[9]),
                'consumed_energy_total': kwh(r.registers[10], r.registers[11]),
                'generated_energy_today': kwh(r.registers[12], r.registers[13]),
                'generated_energy_month': kwh(r.registers[14], r.registers[15]),
                'generated_energy_year': kwh(r.registers[16], r.registers[17]),
                'generated_energy_total': kwh(r.registers[18], r.registers[19]),
                'co2_reduction': tons(r.registers[20], r.registers[21]),
                'battery_voltage': volts(r.registers[26]),
                'battery_current': amps(r.registers[27], r.registers[28]),
                'battery_temperature': temperature(r.registers[29]),
                'ambient_temperature': temperature(r.registers[30]),
            })
        return data

    def get_data(self):
        data = dict()
        r = self.read_input_registers(0x3100, 19, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x3100': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x3100': 'read failed'})
        else:
            data.update({
                'charge_input_voltage': volts(r.registers[0]),
                'charge_input_current': amps(r.registers[1]),
                'charge_input_power': watts(r.registers[2], r.registers[3]),
                'charge_output_voltage': volts(r.registers[4]),
                'charge_output_current': amps(r.registers[5]),
                'charge_output_power': watts(r.registers[6], r.registers[7]),
                'discharge_output_voltage': volts(r.registers[12]),
                'discharge_output_current': amps(r.registers[13]),
                'discharge_output_power': watts(r.registers[14], r.registers[15]),
                'battery_temperature': temperature(r.registers[16]),
                'equipment_temperature': temperature(r.registers[17]),
                'component_temperature': temperature(r.registers[18]),
            })
        r = self.read_input_registers(0x311A, 2, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x311A': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x311A': 'read failed'})
        else:
            data.update({
                'battery_soc': percent(r.registers[0]),
                'remote_battery_temperature': temperature(r.registers[1]),
            })
        r = self.read_input_registers(0x311D, 1, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x311D': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x311D': 'read failed'})
        else:
            data.update({
                'battery_rated_power': volts(r.registers[0]),
            })
        return data

    def get_status(self):
        data = dict()
        r = self.read_input_registers(0x3200, 3, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x3200': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x3200': 'read failed'})
        else:
            r0 = r.registers[0]
            r1 = r.registers[1]
            r2 = r.registers[2]
            data.update({
                'status_battery_voltage': r0 & 0x0007,
                'status_battery_temperature': (r0 >> 4) & 0x000f,
                'status_battery_resistance': (r0 >> 8) & 0x0001,
                'status_battery_rated_voltage': (r0 >> 15) & 0x0001,
                'status_charge_input_voltage': (r1 >> 14) & 0x0003,
                'status_charge_mosfet_short': (r1 >> 13) & 0x0001,
                'status_charge_charging_or_anti_reverse_mosfet_short': (r1 >> 12) & 0x0001,
                'status_charge_anti_reverse_mosfet_short': (r1 >> 11) & 0x0001,
                'status_charge_input_over_current': (r1 >> 10) & 0x0001,
                'status_charge_load_over_current': (r1 >> 9) & 0x0001,
                'status_charge_load_short': (r1 >> 8) & 0x0001,
                'status_charge_load_mosfet_short': (r1 >> 7) & 0x0001,
                'status_charge_pv_input_short': (r1 >> 4) & 0x0001,
                'status_charge_battery': (r1 >> 2) & 0x0003,
                'status_charge_fault': (r1 >> 1) & 0x0001,
                'status_charge_running': r1 & 0x0001,
                'status_discharge_input_voltage': (r2 >> 14) & 0x003,
                'status_discharge_output_power': (r2 >> 12) & 0x0003,
                'status_discharge_short_circuit': (r2 >> 11) & 0x0001,
                'status_discharge_unable_to_discharge': (r2 >> 10) & 0x0001,
                'status_discharge_unable_to_stop_discharge': (r2 >> 9) & 0x0001,
                'status_discharge_output_voltage_abnormal': (r2 >> 8) & 0x0001,
                'status_discharge_input_overpressure': (r2 >> 7) & 0x0001,
                'status_discharge_high_voltage_side_short': (r2 >> 6) & 0x0001,
                'status_discharge_boost_overpressure': (r2 >> 5) & 0x0001,
                'status_discharge_output_overpressure': (r2 >> 4) & 0x0001,
                'status_discharge_fault': (r2 >> 1) & 0x0001,
                'status_discharge_running': r2 & 0x001,
            })
        return data

    def get_coils(self):
        mapping = [
            (2, 1, ['manual_load_control', 0]),
            (3, 1, ['default_load_control', 0]),
            (5, 1, ['load_test_mode', 0]),
            (6, 1, ['force_loa', 0]),
        ]
        data = dict()
        for m in mapping:
            r = self.read_coils(m[0], m[1], unit=self.unit)
            if isinstance(r, Exception):
                data.update({'coil%s' % m[0]: "exception: %s" % r})
            elif r.function_code >= 0x80:
                data.update({'coil%s' % m[0]: 'read failed'})
            else:
                data.update({m[2][0]: r.bits[m[2][1]]})
        return data

    def get_settings(self):
        data = dict()

        # registers 0x9000 - 0x9014
        r = self.read_holding_registers(0x9000, 15, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x9000': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x9000': 'read failed'})
        else:
            data.update({
                'battery_type': battery_type(r.registers[0]),
                'battery_capacity': amp_hours(r.registers[1]),
                'temperature_compensation_coefficient': coeff(r.registers[2]),
                'high_voltage_disconnect': volts(r.registers[3]),
                'charging_limit_voltage': volts(r.registers[4]),
                'over_voltage_reconnect': volts(r.registers[5]),
                'equalization_voltage': volts(r.registers[6]),
                'boost_voltage': volts(r.registers[7]),
                'float_voltage': volts(r.registers[8]),
                'boost_reconnect_voltage': volts(r.registers[9]),
                'low_voltage_reconnect': volts(r.registers[10]),
                'under_voltage_recover': volts(r.registers[11]),
                'under_voltage_warning': volts(r.registers[12]),
                'low_voltage_disconnect': volts(r.registers[13]),
                'discharging_limit_voltage': volts(r.registers[14]),
            })

        # registers 0x9013 - 0x9021
        r = self.read_holding_registers(0x9013, 15, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x9013': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x9013': 'read failed'})
        else:
            data.update({
                'clock': to_datetime(r.registers[0], r.registers[1], r.registers[2]),
                'equalization_charging_cycle': days(r.registers[3]),
                'battery_temperature_warning_upper_limit': temperature(r.registers[4]),
                'battery_temperature_warning_lower_limit': temperature(r.registers[5]),
                'controller_inner_temperature_upper_limit': temperature(r.registers[6]),
                'controller_inner_temperature_upper_limit_recover': temperature(r.registers[7]),
                'power_component_temperature_upper_limit': temperature(r.registers[8]),
                'power_component_temperature_upper_limit_recover': temperature(r.registers[9]),
                'line_impedance': milliohms(r.registers[10]),
                'night_time_threshold_volt': volts(r.registers[11]),
                'light_signal_startup_delay_time': minutes(r.registers[12]),
                'day_time_threshold_volt': volts(r.registers[13]),
                'light_signal_turn_off_delay_time': minutes(r.registers[14]),
            })

        # registers 0x903D - 0x903F
        r = self.read_holding_registers(0x903D, 3, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x903D': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x903D': 'read failed'})
        else:
            data.update({
                'load_controlling_modes': load_controlling_mode(r.registers[0]),
                'working_time_length_1': hour_minute(r.registers[1]),
                'working_time_length_2': hour_minute(r.registers[2]),
            })

        # registers 0x9042 - 0x904D
        r = self.read_holding_registers(0x9042, 12, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x9042': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x9042': 'read failed'})
        else:
            data.update({
                'turn_on_timing_1': to_time(r.registers[0], r.registers[1], r.registers[2]),
                'turn_off_timing_1': to_time(r.registers[3], r.registers[4], r.registers[5]),
                'turn_on_timing_2': to_time(r.registers[6], r.registers[7], r.registers[8]),
                'turn_off_timing_2': to_time(r.registers[9], r.registers[10], r.registers[11]),
            })

        # registers 0x9063
        r = self.read_holding_registers(0x9063, 1, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x9063': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x9063': 'read failed'})
        else:
            data.update({
                'backlight_time': seconds(r.registers[0]),
            })

        # registers 0x9065
        r = self.read_holding_registers(0x9065, 1, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x9065': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x9065': 'read failed'})
        else:
            data.update({
                'length_of_night': hour_minute(r.registers[0]),
            })

        # registers 0x9067 - 0x906E
        r = self.read_holding_registers(0x9067, 8, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x9067': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x9067': 'read failed'})
        else:
            data.update({
                'battery_rated_voltage_code': voltage_code(r.registers[0]),
                'load_timing_control_selection': load_timing_control(r.registers[2]),
                'default_load_onoff_in_manual_mode': on_off(r.registers[3]),
                'equalize_duration': minutes(r.registers[4]),
                'boost_duration': minutes(r.registers[5]),
                'discharge_percentage': percent(r.registers[6]),
                'charging_percentage': percent(r.registers[7]),
            })

        # registers 0x9070
        r = self.read_holding_registers(0x9070, 1, unit=self.unit)
        if isinstance(r, Exception):
            data.update({'0x9070': "exception: %s" % r})
        elif r.function_code >= 0x80:
            data.update({'0x9070': 'read failed'})
        else:
            data.update({
                'management_modes': management_mode(r.registers[0]),
            })

        return data


if __name__ == '__main__':
    import optparse

    usage = """%prog [options] [--debug] [--help]"""

    def pretty_print(x, level=0):
        for k in sorted(x.keys()):
            print("%s%s=%s" % (' ' * level, k, x[k]))

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

        with Tracer(options.port, options.baud_rate, options.timeout) as station:
            print("device info:")
            data = station.get_info()
            pretty_print(data, 2)
            print("device ratings:")
            data = station.get_ratings()
            pretty_print(data, 2)
            print("device settings:")
            data = station.get_settings()
            pretty_print(data, 2)
            print("status:")
            data = station.get_status()
            pretty_print(data, 2)
            print("data:")
            data = station.get_data()
            pretty_print(data, 2)
            print("coils:")
            data = station.get_coils()
            pretty_print(data, 2)
            print("statistics:")
            data = station.get_statistics()
            pretty_print(data, 2)

    main()
