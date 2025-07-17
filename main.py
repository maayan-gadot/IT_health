import os
import requests
import json
import glob
import time
from datetime import datetime, UTC, timedelta
import math
from collections import defaultdict
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

os.system('cls' if os.name == 'nt' else 'clear') 
nan_value = float('nan')

print('PMD zabbix: initialization')

ENABLE_CONTINUOUS_MODE = True  # Toggle continuous mode

CONFIG_FILE = "config.json"
PROPERTIES_FILE = "config.properties"

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def load_properties(filepath):
    props = {}
    with open(filepath, 'r') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                key, value = line.strip().split('=', 1)
                props[key.strip()] = value.strip()
    return props

def authenticate(headers):
    payload = {
        "jsonrpc": "2.0",
        "method": "user.login",
        "params": {
            "username": ZABBIX_USERNAME,
            "password": ZABBIX_PASSWORD
        },
        "id": 1
    }
    response = requests.post(ZABBIX_URL, json=payload, headers=headers)
    return response.json()['result']

def get_host_id(auth_token, host_name, headers):
    payload = {
        "jsonrpc": "2.0",
        "method": "host.get",
        "params": {
            "filter": {"host": [host_name]}
        },
        "auth": auth_token,
        "id": 2
    }
    response = requests.post(ZABBIX_URL, json=payload, headers=headers)
    data = response.json()
    return data['result'][0]['hostid'] if data['result'] else None

def get_metrics(auth_token, host_id, headers):
    payload = {
        "jsonrpc": "2.0",
        "method": "item.get",
        "params": {
            "output": ["itemid", "key_", "value_type"],
            "hostids": host_id,
            "sortfield": "name"
        },
        "auth": auth_token,
        "id": 3
    }
    response = requests.post(ZABBIX_URL, json=payload, headers=headers)
    return response.json()['result']

def get_history(auth_token, itemid, time_window, headers, history_type=0, start_time=None):
    time_to = int(time.time())
    time_from = start_time if start_time else (time_to - time_window)
    payload = {
        "jsonrpc": "2.0",
        "method": "history.get",
        "params": {
            "output": "extend",
            "history": history_type,
            "itemids": itemid,
            "sortfield": "clock",
            "sortorder": "DESC",
            "time_from": time_from,
            "time_till": time_to,
            "limit": 1000
        },
        "auth": auth_token,
        "id": 5
    }
    response = requests.post(ZABBIX_URL, json=payload, headers=headers)
    return response.json()['result']

def read_latest_cf_authorization_token(folder_path, prefix):
    matching_files = glob.glob(os.path.join(folder_path, f"{prefix}*"))
    if not matching_files:
        raise FileNotFoundError(f"No token files starting with '{prefix}' found in {folder_path}")
    latest_file = max(matching_files, key=os.path.getmtime)
    with open(latest_file, "r") as f:
        return f.read().strip()

def writeAPI(point: Point):
    try:
        line = point.to_line_protocol()
        response = requests.post(INFLUX_URL, params=params, headers=headers_influx, data=line)
        if not response.ok:
            print(f"❌ Failed to write to InfluxDB: {response.status_code} {response.text}")
    except Exception as e:
        print(f"❌ Exception during write:{e}")

def s_shape(val, min_th, max_th):
    try:
        norm = (val - min_th) / (max_th - min_th)
        return max(0.0, min(1.0, norm))
    except ZeroDivisionError:
        return 0.0

def z_shape(value, min_th, max_th):
    try:
        norm = (max_th - value) / (max_th - min_th)
        return max(0.0, min(1.0, norm))
    except ZeroDivisionError:
        return 0.0

def main_loop(start_from=None):
    print("[main_loop] Running in", "continuous" if ENABLE_CONTINUOUS_MODE else "historical", "mode")
    import score_cal
    score_cal.run(
        config=config,
        auth_token=auth_token,
        headers_zabbix=headers_zabbix,
        TIME_WINDOW=TIME_WINDOW,
        AVERAGING_WINDOW=AVERAGING_WINDOW,
        MOVING_AVG_STEP=MOVING_AVG_STEP,
        start_from=start_from,
        ENABLE_CONTINUOUS_MODE=ENABLE_CONTINUOUS_MODE
    )

if __name__ == "__main__":
    config = load_config()
    props = load_properties(PROPERTIES_FILE)

    INFLUX_MODE = props.get("influxdb.mode", "local")
    INFLUX_TOKEN = props.get("influxdb.token")
    INFLUX_ORG = props.get("influxdb.org")
    INFLUX_BUCKET = props.get("influxdb.bucket")
    INFLUX_FOLDER_PREFIX = props.get("influxdb.folder.prefix")
    ZABBIX_MODE = props.get("zabbix.mode", "local")
    ZABBIX_FOLDER_PREFIX = props.get("zabbix.folder.prefix")
    ZABBIX_USERNAME = props.get("zabbix.username")
    ZABBIX_PASSWORD = props.get("zabbix.password")
    CLOUDFALARE_PATH = props.get("cloudflare.token.path")

    if INFLUX_MODE == "cloud":
        INFLUX_URL = props.get("influxdb.cloud.url")
        token_folder_influx = os.path.expanduser(CLOUDFALARE_PATH)
        cf_token_influx = read_latest_cf_authorization_token(token_folder_influx, INFLUX_FOLDER_PREFIX)
        headers_influx = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain",
            "Cookie": f"CF_Authorization={cf_token_influx}"
        }
    else:
        INFLUX_URL = props.get("influxdb.local.url", "http://localhost:8086/api/v2/write")
        headers_influx = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain",
        }

    params = {
        "bucket": INFLUX_BUCKET,
        "org": INFLUX_ORG,
        "precision": "s"
    }

    if ZABBIX_MODE == "cloud":
        ZABBIX_URL = props.get("zabbix.cloud.url")
        token_folder_zabbix = os.path.expanduser(CLOUDFALARE_PATH)
        cf_token_zabbix = read_latest_cf_authorization_token(token_folder_influx, ZABBIX_FOLDER_PREFIX)
        headers_zabbix = {
            "Content-Type": "application/json",
            "Cookie": f"CF_Authorization={cf_token_zabbix}"
        }
    else:
        ZABBIX_URL = props.get("zabbix.local.url")
        headers_zabbix = {
            "Content-Type": "application/json"
        }

    auth_token = authenticate(headers_zabbix)
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    TIME_WINDOW = config.get("time_window", 30) * 60
    AVERAGING_WINDOW = config.get("averaging_window", 5) * 60
    MOVING_AVG_STEP = config.get("moving_avg_step", 2) * 60

    if ENABLE_CONTINUOUS_MODE:
        print("Continuous mode active: script will loop indefinitely.")
        start_time = int(time.time()) - MOVING_AVG_STEP
        while True:
            main_loop(start_from=start_time)
            start_time = int(time.time())
            time.sleep(MOVING_AVG_STEP)
    else:
        print("One-shot mode active: script will process historical data.")
        main_loop()

    client.close()
    print('Finished...')
