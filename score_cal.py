import os
import time
import math
from datetime import datetime, UTC
from collections import defaultdict
from influxdb_client import Point, WritePrecision


def run(config, auth_token, headers_zabbix, TIME_WINDOW, AVERAGING_WINDOW, MOVING_AVG_STEP, start_from, ENABLE_CONTINUOUS_MODE):
    from __main__ import get_host_id, get_metrics, get_history, z_shape, s_shape, writeAPI

    targets = config["hosts"]
    host_score_data = defaultdict(lambda: {"total": 0.0, "weight": 0.0})
    grouped_by_host_name = defaultdict(list)
    for target in targets:
        grouped_by_host_name[target["host_name"]].append(target)

    if ENABLE_CONTINUOUS_MODE:
        now = int(time.time())
        shared_windows = [start_from]
    else:
        # Calculate shared windows for historical mode
        all_clocks = []
        for target in targets:
            host_id = get_host_id(auth_token, target["host"], headers_zabbix)
            if not host_id:
                print("host not found")
            # print(target["host"])
            items = get_metrics(auth_token, host_id, headers_zabbix)
            items_dict = {item['key_']: item for item in items}
            for metric in target["metrics"]:
                if not metric.get("enabled", True):
                    continue
                item = items_dict.get(metric["key"])
                if not item:
                    print("metric not found")
                # print(item)
                history = get_history(auth_token, item['itemid'], TIME_WINDOW, headers_zabbix)
                clocks = [int(h['clock']) for h in history if 'clock' in h]
                all_clocks.extend(clocks)

        global_start = min(all_clocks) if all_clocks else int(time.time()) - TIME_WINDOW
        global_end = max(all_clocks) if all_clocks else int(time.time())
        shared_windows = list(range(global_start, global_end, MOVING_AVG_STEP))

    for host_name, host_targets in grouped_by_host_name.items():
        if not host_targets:
            print("no hosts found")

        first_host = host_targets[0]
        host_id = get_host_id(auth_token, first_host["host"], headers_zabbix)
        if not host_id:
            print("host not found")

        simple_host = host_name.lower().replace(" ", "_")
        items = get_metrics(auth_token, host_id, headers_zabbix)
        items_dict = {item['key_']: item for item in items}
        # print(items_dict)
        histories = {}
        all_windows = defaultdict(lambda: {"scores": [], "weighted": [], "raw": []})

        for target in host_targets:
            # print(target)
            target_metrics = target["metrics"]
            host_weight = target.get("host_weight", 1.0)

            print(target_metrics)

            for metric in target_metrics:
                metric_key = metric.get("key")
                print(metric_key)
                if not metric.get("enabled", True) or metric_key not in items_dict:
                    print("metric not found" + metric_key)

                item = items_dict[metric_key]
                value_type = int(item.get("value_type", 0))
                history = get_history(auth_token, item['itemid'], TIME_WINDOW, headers_zabbix, history_type=value_type, start_time=start_from)
                history = sorted(history, key=lambda x: int(x['clock']))
                if not history:
                    continue

                histories[metric_key] = history

        for metric_key, history in histories.items():
            for target in host_targets:
                print(target["host"])
                metric = next((m for m in target['metrics'] if m['key'] == metric_key), None)
                if not metric:
                    print("metric not found: " + metric["name"])

                print(metric["name"])
                function = metric.get("function", "z_shape")
                min_th = metric.get("min", float('nan'))
                max_th = metric.get("max", float('nan'))
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
                        writeAPI(Point("raw_metrics").tag("target_host", simple_host).tag("host_id", host_id).field(metric_key, round(value, 4)).time(dt, WritePrecision.S))
                        writeAPI(Point("score_metric").tag("target_host", simple_host).tag("host_id", host_id).field(metric_key, round(score, 4)).time(dt, WritePrecision.S))
                        writeAPI(Point("weighted_score_metric").tag("target_host", simple_host).tag("host_id", host_id).field("weighted_score", round(weighted, 4)).time(dt, WritePrecision.S))
                    except Exception:
                        print("Failed to send points")

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
                    writeAPI(Point("raw_metrics_avg").tag("target_host", simple_host).tag("host_id", host_id).field(metric_key, round(avg_raw, 4)).time(avg_time, WritePrecision.S))
                    writeAPI(Point("score_metric_avg").tag("target_host", simple_host).tag("host_id", host_id).field(metric_key, round(avg_score, 4)).time(avg_time, WritePrecision.S))
                    writeAPI(Point("weighted_score_metric_avg").tag("target_host", simple_host).tag("host_id", host_id).field("weighted_score", round(avg_weighted, 4)).time(avg_time, WritePrecision.S))

        for win_end, data in all_windows.items():
            if data["scores"]:
                avg_host_score = sum(data["scores"])
                weighted_host_score = avg_host_score * host_weight
                avg_time = datetime.fromtimestamp(win_end, tz=UTC)
                writeAPI(Point("host_score").tag("target_host", simple_host).tag("host_id", host_id).field("averaged_host_score", round(avg_host_score, 4)).time(avg_time, WritePrecision.S))
                host_score_data[win_end]["total"] += weighted_host_score
                host_score_data[win_end]["weight"] += host_weight

    for win_end, scores_dict in host_score_data.items():
        if scores_dict["weight"] > 0:
            system_avg = scores_dict["total"] / scores_dict["weight"]
        else:
            system_avg = 0.0
        avg_time = datetime.fromtimestamp(win_end, tz=UTC)
        writeAPI(Point("system_score").field("system_score", round(system_avg, 4)).time(avg_time, WritePrecision.S))
