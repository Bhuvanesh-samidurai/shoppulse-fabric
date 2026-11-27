"""EventstreamSink: send WMS / carrier events to the Eventstream custom endpoints (owner: DE1, issue #27).

- The custom endpoint connection string is fetched at runtime with the running identity
  (Eventstream topology API) and kept in memory only: never printed, logged or written to disk.
- Event Hub protocol (azure-eventhub), JSON payloads, large batches per stream. Order is restored
  downstream from event_ts; duplicates are removed by event_id (idempotent replays).
- Payload fields = contracts/bronze/{wms_event,carrier_event}.yml minus _ingested_at/_source/_batch_id
  (nb_ingest_events adds those). Timestamps are ISO-8601 UTC strings, event_date is YYYY-MM-DD.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict

import requests

API = "https://api.fabric.microsoft.com/v1"
STREAMS = {"wms": "es-wms", "carrier": "es-carrier"}


def _json_default(v):
    if isinstance(v, dt.datetime):
        return v.isoformat(timespec="milliseconds")
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "isoformat"):  # pandas Timestamp
        return v.isoformat()
    if hasattr(v, "item"):  # numpy scalars
        return v.item()
    raise TypeError(type(v))


def encode(payload: dict) -> str:
    return json.dumps(payload, default=_json_default, separators=(",", ":"))


def endpoint_connection(workspace: str, eventstream: str, credential) -> tuple[str, str]:
    """(connection string, event hub name) of the eventstream's CustomEndpoint source. Kept in memory only."""
    h = {"Authorization": "Bearer " + credential.get_token("https://api.fabric.microsoft.com/.default").token}
    ws = next(w["id"] for w in requests.get(f"{API}/workspaces", headers=h, timeout=60).json()["value"] if w["displayName"] == workspace)
    es = next(e["id"] for e in requests.get(f"{API}/workspaces/{ws}/eventstreams", headers=h, timeout=60).json()["value"]
              if e["displayName"] == eventstream)
    topo = requests.get(f"{API}/workspaces/{ws}/eventstreams/{es}/topology", headers=h, timeout=60).json()
    src = next(s for s in topo["sources"] if s["type"] == "CustomEndpoint")
    conn = requests.get(f"{API}/workspaces/{ws}/eventstreams/{es}/sources/{src['id']}/connection", headers=h, timeout=60).json()
    return conn["accessKeys"]["primaryConnectionString"], conn["eventHubName"]


def resume_paused_destinations(workspace: str, credential, log=print) -> list[str]:
    """After a capacity pause, Eventstream sources come back Running but destinations stay Paused: events are
    accepted and retained (retentionTimeInDays) but not delivered. Resume them WhenLastStopped so the retained
    events are delivered (verified in #28: nothing lost across a pause)."""
    h = {"Authorization": "Bearer " + credential.get_token("https://api.fabric.microsoft.com/.default").token}
    ws = next(w["id"] for w in requests.get(f"{API}/workspaces", headers=h, timeout=60).json()["value"] if w["displayName"] == workspace)
    resumed = []
    for es in requests.get(f"{API}/workspaces/{ws}/eventstreams", headers=h, timeout=60).json()["value"]:
        topo = requests.get(f"{API}/workspaces/{ws}/eventstreams/{es['id']}/topology", headers=h, timeout=60).json()
        for d in topo.get("destinations", []):
            if d.get("status") == "Paused":
                r = requests.post(f"{API}/workspaces/{ws}/eventstreams/{es['id']}/destinations/{d['id']}/resume",
                                  headers=h, json={"startType": "WhenLastStopped"}, timeout=60)
                resumed.append(f"{es['displayName']}/{d['name']}={r.status_code}")
    if resumed:
        log(f"resumed paused Eventstream destinations: {', '.join(resumed)}")
    return resumed


class EventstreamSink:
    def __init__(self, workspace: str, credential):
        from azure.eventhub import EventHubProducerClient

        resume_paused_destinations(workspace, credential)
        self.producers = {}
        for kind, name in STREAMS.items():
            cs, hub = endpoint_connection(workspace, name, credential)
            self.producers[kind] = EventHubProducerClient.from_connection_string(cs, eventhub_name=hub)

    def send(self, events) -> dict[str, int]:
        """Send events (generator.replay.Event) in large batches per stream, in timestamp order.
        Ordering downstream relies on event_ts and event_id, not on Event Hub partitions."""
        from azure.eventhub import EventData

        by_stream: dict[str, list] = defaultdict(list)
        for e in events:
            by_stream[e.stream].append(e)
        sent = {k: 0 for k in STREAMS}
        for kind, evs in by_stream.items():
            producer = self.producers[kind]
            batch = producer.create_batch()
            for e in sorted(evs, key=lambda x: x.ts):
                data = EventData(encode(e.payload))
                try:
                    batch.add(data)
                except ValueError:  # batch full: send it and start a new one
                    producer.send_batch(batch)
                    batch = producer.create_batch()
                    batch.add(data)
                sent[kind] += 1
            if len(batch):
                producer.send_batch(batch)
        return sent

    def close(self):
        for p in self.producers.values():
            p.close()
