#!/usr/bin/env python3

import logging
import json
import glob
import signal
import sys
import time

BLACKLIST = []
PROFILES = {}
SENSORS = []
CONFIG = {}
HWMON_DIR = "/sys/class/hwmon"
SMC_PATH = ""
CFG_PATH = "/etc/mbp-fanctl.conf"


class SmcObject:
    def __init__(self, smc_path, id):
        self.id = id
        self.smc_path = smc_path

    def read_attribute(self, attr_name):
        with open("{}/{}{}_{}".format(self.smc_path,
                                      self.prefix, self.id, attr_name)) as fd:
            ret = fd.read().strip()
        return ret

    def write_attribute(self, attr_name, value):
        with open("{}/{}{}_{}".format(self.smc_path, self.prefix,
                                      self.id, attr_name), "w") as fd:
            fd.write("{}".format(value))


class Fan(SmcObject):

    def __init__(self, smc_path, id):
        super().__init__(smc_path, id)
        self.prefix = "fan"
        self.max_speed = 7500
        self.min_speed = 1000

    def get_current_speed(self):
        return int(self.read_attribute("input"))

    def set_speed_target(self, normalized):
        speed_window = self.max_speed - self.min_speed
        target_speed = int(speed_window * normalized + self.min_speed)
        curr_speed = self.get_current_speed()
        if target_speed < self.min_speed:
            target_speed = self.min_speed
        elif target_speed > self.max_speed:
            target_speed = self.max_speed
        print("Setting fan{} to speed {} from speed {}".format(self.id,
                                                               target_speed,
                                                               curr_speed))
        self.set_speed(target_speed)

    def set_speed(self, speed):
        return self.write_attribute("output", int(speed))

    def set_manual(self):
        return self.write_attribute("manual", 1)

    def set_automatic(self):
        return self.write_attribute("manual", 0)


class TempSensor(SmcObject):
    def __init__(self, smc_path, id):
        super().__init__(smc_path, id)
        self.prefix = "temp"
        self.name = self.read_attribute("label")

    def get_temp(self):
        return int(self.read_attribute("input"))/1000


def find_smc_path():
    candidates = glob.glob(HWMON_DIR + "/hwmon*/device/name")
    for candidate in candidates:
        with open(candidate, "r") as fd:
            data = fd.read().strip()
            if data == "applesmc":
                return '/'.join(candidate.split('/')[:-1])
    return None


def get_sensors(smc_path):
    candidates = glob.glob(smc_path + "/temp*_label")
    sensors = []
    for candidate in candidates:
        id = candidate.split('/')[-1].split('_')[0][4:]
        new_sensor = TempSensor(smc_path, id)
        temp = new_sensor.get_temp()
        if new_sensor.name in BLACKLIST:
            print("Ignoring sensor {} because of blacklist".format(
                new_sensor.name))
        else:
            sensors.append(new_sensor)
    return sensors


def get_fans(smc_path):
    candidates = glob.glob(smc_path + "/fan*_max")
    fan = []
    for candidate in candidates:
        id = candidate.split('/')[-1].split('_')[0][3:]
        fan.append(Fan(smc_path, id))
    return fan


def receiveSignal(signalNumber, frame):
    global FANS
    print("Caught signal, settings fans to automatic and exiting")
    for fan in FANS:
        fan.set_automatic()
    sys.exit(signalNumber)


def setup_signals():
    for sig in signal.valid_signals():
        try:
            signal.signal(sig, receiveSignal)
        except Exception:
            pass


def normalize_value(value, floor, ceiling):
    window = ceiling - floor
    normalized = ((value - floor) / window)
    return max(min(1.0, normalized), 0.0)


def get_profile_normalized_value(profile_name):
    global PROFILES, SENSORS
    floor = PROFILES[profile_name]['floor']
    ceiling = PROFILES[profile_name]['ceiling']
    if profile_name == 'AVG':
        temp_sum = 0
        sensor_num = 0
        for sensor in SENSORS:
            sensor_temp = sensor.get_temp()
            if sensor_temp >= CONFIG['min_temp']:
                temp_sum = temp_sum + sensor_temp
                sensor_num = sensor_num + 1
        if sensor_num != 0:
            temp = temp_sum / sensor_num
    else:
        for sensor in SENSORS:
            if sensor.name == profile_name:
                temp = sensor.get_temp()
    return (normalize_value(temp, floor, ceiling), temp)


def load_config():
    global CONFIG, CFG_PATH, PROFILES, BLACKLIST
    with open(CFG_PATH) as fd:
        cfg = json.load(fd)
    PROFILES = cfg.pop('profiles')
    print("Loaded {} profiles {}".format(len(PROFILES), list(PROFILES.keys())))
    BLACKLIST = cfg.pop('blacklist')
    print("Blacklist has {} entr{} : {}".format(len(BLACKLIST),
                                                ('y' if len(BLACKLIST)
                                                    else 'ies'),
                                                BLACKLIST))
    CONFIG = cfg
    for entry in CONFIG.keys():
        print("Config entry {} has value {}".format(entry, CONFIG[entry]))


def main():
    load_config()
    global SMC_PATH, SENSORS, FANS, CONFIG
    SMC_PATH = find_smc_path()
    if SMC_PATH is None:
        print("Unable to find applesmc, is it loaded ?")
        sys.exit(1)
    print("Found applesmc at {}".format(SMC_PATH))
    FANS = get_fans(SMC_PATH)
    if len(FANS) == 0:
        print("Unable fo find any fans")
        sys.exit(1)
    print("Found {} fans".format(len(FANS)))
    SENSORS = get_sensors(SMC_PATH)
    if len(SENSORS) == 0:
        print("Unable to find any sensors")
        sys.exit(1)
    print("Found {} sensors".format(len(SENSORS)))
    setup_signals()
    for fan in FANS:
        fan.set_manual()
    old_setting = 0.0
    while True:
        new_setting = 0
        temp = 0
        cur_profile = ""
        for profile_name in PROFILES.keys():
            (tmp, tmp_temp) = get_profile_normalized_value(profile_name)
            if tmp > new_setting:
                temp = tmp_temp
                new_setting = tmp
                new_profile = profile_name
        delta = abs(old_setting - new_setting)
        if delta >= CONFIG['min_delta']:
            print("Acting upon profile {} with a normalized value of {}"
                  "(real value: {})".format(new_profile, new_setting, temp))
            for fan in FANS:
                fan.set_speed_target(new_setting)
            old_setting = new_setting

        time.sleep(CONFIG['loop_sleep_time'])


if __name__ == "__main__":
    main()
