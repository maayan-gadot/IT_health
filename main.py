
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

def get_history(auth_token, itemid, time_window, headers, history_type=0):
    time_to = int(time.time())
    time_from = time_to - time_window
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
        if response.ok:
            # print(f"✅ Successfully wrote to InfluxDB: {line}")
            pass
        else:
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

    targets = config["hosts"]
    host_score_data = defaultdict(lambda: {"total": 0.0, "weight": 0.0})
    grouped_by_host_name = defaultdict(list)
    for target in targets:
        grouped_by_host_name[target["host_name"]].append(target)

    # Determine global clock bounds across all hosts and metrics
    all_clocks = []
    for target in targets:
        host_id = get_host_id(auth_token, target["host"], headers_zabbix)
        if not host_id:
            continue
        items = get_metrics(auth_token, host_id, headers_zabbix)
        items_dict = {item['key_']: item for item in items}
        for metric in target["metrics"]:
            if not metric.get("enabled", True):
                continue
            item = items_dict.get(metric["key"])
            if not item:
                continue
            item.get
            history = get_history(auth_token, item['itemid'], TIME_WINDOW, headers_zabbix)
            clocks = [int(h['clock']) for h in history if 'clock' in h]
            all_clocks.extend(clocks)

    global_start = min(all_clocks) if all_clocks else int(time.time()) - TIME_WINDOW
    global_end = max(all_clocks) if all_clocks else int(time.time())
    shared_windows = list(range(global_start, global_end, MOVING_AVG_STEP))

 
    # Continue with metric and windowed processing, using shared_windows consistently...
    for host_name, host_targets in grouped_by_host_name.items():
        if not host_targets:
            continue
        print(host_name)
        first_host = host_targets[0]
        host_id = get_host_id(auth_token, first_host["host"], headers_zabbix)
        if not host_id:
            print("! Host not found: " + first_host["host"])
            continue

        simple_host = host_name.lower().replace(" ", "_")
        items = get_metrics(auth_token, host_id, headers_zabbix)
        items_dict = {item['key_']: item for item in items}

        histories = {}
        all_windows = defaultdict(lambda: {"scores": [], "weighted": [], "raw": []})

        for target in host_targets:
            target_metrics = target["metrics"]
            host_weight = target.get("host_weight", 1.0)

            for metric in target_metrics:
                metric_key = metric.get("key")
                if not metric.get("enabled", True) or metric_key not in items_dict:
                    continue
                
                # print(metric_key)

                item = items_dict[metric_key]
                value_type = int(item.get("value_type", 0))  

                history = sorted(get_history(auth_token, item['itemid'], TIME_WINDOW, headers_zabbix,history_type=value_type), key=lambda x: int(x['clock']))
                if not history:
                    print("! metric not found: " + item['key_'])
                    continue
                histories[metric_key] = history

        for metric_key, history in histories.items():
            for target in host_targets:
                metric = next((m for m in target['metrics'] if m['key'] == metric_key), None)
                if not metric:
                    continue

                function = metric.get("function", "z_shape")
                min_th = metric.get("min", nan_value)
                max_th = metric.get("max", nan_value)
                weight = metric.get("weight", 0)

                for h in history:
                    ts = int(h['clock'])
                    dt = datetime.fromtimestamp(ts, tz=UTC)
                    try:
                        value = float(h['value'])
                        if not math.isfinite(value):
                            continue
                        score = z_shape(value, min_th, max_th) if function == "z_shape" else s_shape(value, min_th, max_th)
                        weighted = score * weight
                        point = Point("raw_metrics").tag("target_host", simple_host).tag("host_id", host_id).field(metric_key, round(value, 4)).time(dt, WritePrecision.S)
                        writeAPI(point)
                        point = Point("score_metric").tag("target_host", simple_host).tag("host_id", host_id).field(metric_key, round(score, 4)).time(dt, WritePrecision.S)
                        writeAPI(point)
                        point = Point("weighted_score_metric").tag("target_host", simple_host).tag("host_id", host_id).field("weighted_score", round(weighted, 4)).time(dt, WritePrecision.S)
                        writeAPI(point)
                    except Exception:
                        print('Failed to send points')




                for win_start in shared_windows:
                    win_end = win_start + AVERAGING_WINDOW
                    window_data = [h for h in history if win_start <= int(h['clock']) < win_end and math.isfinite(float(h['value']))]
                    if not window_data:
                        continue

                    raw_vals = [float(h['value']) for h in window_data]
                    scores = [z_shape(v, min_th, max_th) if function == "z_shape" else s_shape(v, min_th, max_th) for v in raw_vals]
                    weighted_scores = [s * weight for s in scores]

                    avg_raw = sum(raw_vals) / len(raw_vals)
                    avg_score = sum(scores) / len(scores)
                    avg_weighted = sum(weighted_scores) / len(weighted_scores)

                    all_windows[win_end]["scores"].append(avg_score * weight)
                    all_windows[win_end]["weighted"].append(avg_weighted)
                    all_windows[win_end]["raw"].append(avg_raw)

                    avg_time = datetime.fromtimestamp(win_end, tz=UTC)

                    point = Point("raw_metrics_avg").tag("target_host", simple_host).tag("host_id", host_id).field(metric_key, round(avg_raw, 4)).time(avg_time, WritePrecision.S)
                    writeAPI(point)

                    point = Point("score_metric_avg").tag("target_host", simple_host).tag("host_id", host_id).field(metric_key, round(avg_score, 4)).time(avg_time, WritePrecision.S)
                    writeAPI(point)

                    point = Point("weighted_score_metric_avg").tag("target_host", simple_host).tag("host_id", host_id).field("weighted_score", round(avg_weighted, 4)).time(avg_time, WritePrecision.S)
                    writeAPI(point)

        for win_end, data in all_windows.items():
            if data["scores"]:
                avg_host_score = sum(data["scores"])
                weighted_host_score = avg_host_score * host_weight
                avg_time = datetime.fromtimestamp(win_end, tz=UTC)
                point = Point("host_score").tag("target_host", simple_host).tag("host_id", host_id).field("averaged_host_score", round(avg_host_score, 4)).time(avg_time, WritePrecision.S)
                writeAPI(point)
                host_score_data[win_end]["total"] += weighted_host_score
                host_score_data[win_end]["weight"] += host_weight

    for win_end, scores_dict in host_score_data.items():
        if scores_dict["weight"] > 0:
            system_avg = scores_dict["total"] / scores_dict["weight"]
        else:
            system_avg = 0.0
        avg_time = datetime.fromtimestamp(win_end, tz=UTC)
        point = Point("system_score").field("system_score", round(system_avg, 4)).time(avg_time, WritePrecision.S)
        writeAPI(point)

client.close()
print('Finished...')
