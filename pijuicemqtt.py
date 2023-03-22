"""
Get PiJuice UPS hat information and publish to MQTT for consumption by eg Node-RED and Home Assistant
"""

import argparse
import platform
import signal
import sys
import threading
from json import dumps
import socket

import paho.mqtt.client as mqtt
import yaml
from pijuice import PiJuice
from pijuice import __version__ as library_version


parser = argparse.ArgumentParser(description="PiJuice to MQTT")
parser.add_argument(
    "-c",
    "--config",
    default="config.yaml",
    help="Configuration yaml file, defaults to `config.yaml`",
    dest="config_file",
)
args = parser.parse_args()

pijuice = PiJuice(1, 0x14)  # Instantiate PiJuice interface object
timer_thread = None


def load_config(config_file):
    """Load the configuration from config yaml file and use it to override the defaults."""
    with open(config_file, "r") as f:
        config_override = yaml.safe_load(f)

    default_config = {
        "mqtt": {
            "broker": "127.0.0.1",
            "port": 1883,
            "username": None,
            "password": None,
            "topic_prefix": "pijuice/$HOSTNAME",
        },
        "homeassistant": {
            "topic": "homeassistant",
            "sensor": True,
        },
        "publish_period": 30,
        "hostname": platform.node(),
    }

    config = {**default_config, **config_override}
    return config


def mqtt_on_connect(client, userdata, flags, rc):
    """Renew subscriptions and set Last Will message when connect to broker."""
    # Set up Last Will, and then set services' status to 'online'
    client.will_set(
        base_topic,
        payload="offline",
        qos=1,
        retain=True,
    )
    client.publish(
        base_topic,
        payload="online",
        qos=1,
        retain=True,
    )

    # Home Assistant MQTT autoconfig
    if config["homeassistant"]["sensor"]:
        print("Publishing Home Assistant MQTT autoconfig")
        # Payload that is common to both autoconfig messages
        battery_capacity = pijuice.config.GetBatteryProfile()["data"]["capacity"]
        firmware_version = pijuice.config.GetFirmwareVersion()["data"]["version"]
        base_payload = {
            "availability_topic": base_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "state_topic": f"{base_topic}/status",
            "json_attributes_topic": f"{base_topic}/status",
            "device": {
                "identifiers": [f"pijuice-{hostname}"],
                "name": f"{hostname} PiJuice",
                "sw_version": f"Library {library_version}, Firmware {firmware_version}",
                "model": f"PiJuice {battery_capacity} mAh",
                "manufacturer": "PiSupply",
            },
        }

        if "expire_after" in config["homeassistant"]:
            base_payload["expire_after"] = int(config["homeassistant"]["expire_after"])

        # Battery charge percentage
        payload = {
            "name": f"{hostname} PiJuice Battery",
            "unique_id": f"pijuice-{hostname}-batteryCharge",
            "value_template": "{{ value_json.batteryCharge }}",
            "device_class": "battery",
            "unit_of_measurement": "%",
        }
        client.publish(
            f"{config['homeassistant']['topic']}/sensor/pijuice-{hostname}/batteryCharge/config",
            dumps({**base_payload, **payload}),
            qos=1,
            retain=True,
        )

        # Power/No Power binary sensor
        payload = {
            "name": f"{hostname} PiJuice PowerInput5vIo",
            "unique_id": f"pijuice-{hostname}-powerInput5vIo",
            "value_template": "{{ value_json.powerInput5vIo }}",
            "payload_off": "NOT_PRESENT",
            "payload_on": "PRESENT",
            "device_class": "power",
        }
        client.publish(
            f"{config['homeassistant']['topic']}/binary_sensor/pijuice-{hostname}/powerInput5vIo/config",
            dumps({**base_payload, **payload}),
            qos=1,
            retain=True,
        )

        # Battery Temperature sensor
        payload = {
            "name": f"{hostname} PiJuice BatteryTemperature",
            "unique_id": f"pijuice-{hostname}-batteryTemperature",
            "value_template": "{{ value_json.batteryTemperature }}",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "enabled_by_default": False,
            "entity_category": "diagnostic",
        }
        client.publish(
            f"{config['homeassistant']['topic']}/sensor/pijuice-{hostname}/batteryTemperature/config",
            dumps({**base_payload, **payload}),
            qos=1,
            retain=True,
        )

        # Battery Status sensor
        payload = {
            "name": f"{hostname} PiJuice BatteryStatus",
            "unique_id": f"pijuice-{hostname}-batteryStatus",
            "value_template": "{{ value_json.batteryStatus }}",
            "enabled_by_default": False,
            "entity_category": "diagnostic",
        }
        client.publish(
            f"{config['homeassistant']['topic']}/sensor/pijuice-{hostname}/batteryStatus/config",
            dumps({**base_payload, **payload}),
            qos=1,
            retain=True,
        )

def on_exit(signum, frame):
    """
    Update MQTT services' status to `offline` and stop the timer thread.

    Called when program exit is received.
    """
    print("Exiting...")
    client.publish(
        base_topic,
        payload="offline",
        qos=1,
        retain=True,
    )
    timer_thread.cancel()
    timer_thread.join()
    sys.exit(0)


def publish_pijuice():
    """
    Publish PiJuice UPS Hat information every `publish_period` seconds.

    See https://github.com/PiSupply/PiJuice/tree/master/Software#i2c-command-api
    """
    global timer_thread
    timer_thread = threading.Timer(config["publish_period"], publish_pijuice)
    timer_thread.start()

    try:

        if "publish_online_status" in config and config["publish_online_status"]:
            client.publish(
                base_topic,
                payload="online",
                qos=1,
                retain=True,
            )

        status = pijuice.status.GetStatus()["data"]
        pijuice_status = {
            "battery_charge": pijuice.status.GetChargeLevel()["data"],
            "battery_voltage": pijuice.status.GetBatteryVoltage()["data"] / 1000,
            "battery_current": pijuice.status.GetBatteryCurrent()["data"] / 1000,
            "battery_temperature": pijuice.status.GetBatteryTemperature()["data"],
            "battery_status": status["battery"],
            "power_input": status["powerInput"],
            "power_input_5vio": status["powerInput5vIo"],
            "io_voltage": pijuice.status.GetIoVoltage()["data"] / 1000,
            "io_vurrent": pijuice.status.GetIoCurrent()["data"] / 1000,
        }
        client.publish(
            f"{base_topic}/status",
            dumps(pijuice_status),
        )
    except KeyError:
        print("Could not read PiJuice data, skipping")


config = load_config(args.config_file)

# get device host name - used in mqtt topic
hostname = socket.gethostname()
base_topic = config["mqtt"]["topic_prefix"]
base_topic = base_topic.replace("$HOSTNAME", hostname)

if __name__ == "__main__":
    client = mqtt.Client()
    client.on_connect = mqtt_on_connect
    client.username_pw_set(config["mqtt"]["username"], config["mqtt"]["password"])
    client.connect(config["mqtt"]["broker"], config["mqtt"]["port"], 60)
    print("PiJuice connected to MQTT broker")

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    publish_pijuice()
    client.loop_forever()
