#!/usr/bin/env python3
# GCP counterpart of the AWS CloudResourcesReport for one project: urgent alert (risky instances), daily report and
# nightly cleanup (unattached disks; IPs, firewall rules, routes and subnets of gone clusters; base networks untouched). Mail via AWS SNS.
# main({"mode": "urgent"|"daily"|"cleanup", "dry_run": bool, "operating_mode": "actual_deletion", "no_writes": bool}); never stops VMs.

import json
import os
import re
from datetime import datetime, timezone, timedelta

import boto3
from google.cloud import compute_v1, container_v1, logging as gcp_logging

PROJECT = os.environ["GCP_PROJECT_ID"]
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
CLEANUP_ACTUAL = os.environ.get("CLEANUP_MODE") == "actual_deletion"  # report_only: the daily report must not announce deletions
DUE = "🗑️ deleted tonight" if CLEANUP_ACTUAL else "🗑️ due tonight (cleanup in report_only: not deleted)"
RISKY_MIN_VCPU = 32  # GCE machine types carry the vCPU count at the end of their name (n2-standard-32)
GPU_FAMILIES = ("a2-", "a3-", "a4-", "a4x-", "g2-", "g4-", "ct", "tpu")  # machine types with built-in accelerators (machine-types list, 2026-10-22)
INSTANCE_MAX_AGE = timedelta(hours=8)
CLUSTER_MAX_AGE = timedelta(days=1)
DISK_SINCE_LABEL = "cleanup-unattached-since"  # value: epoch seconds (label values cannot hold ":" or "+")
IP_SINCE_LABEL = "cleanup-unassociated-since"
CLUSTER_IP_SUFFIX = "-apiserver"  # <cluster>-apiserver: API server IP of a cluster; every other reservation belongs to the admins
KEEP_LABEL = "noautodelete"  # GCE label keys are lower case only
PVC_KEY = "kubernetes.io/created-for/pvc/name"  # PD CSI driver writes it into the disk description JSON
CLUSTER_LABEL = "goog-k8s-cluster-name"  # unverified: label GKE puts on its PD CSI disks
CLEANUP_MIN_AGE = timedelta(hours=23)  # 1 day, minus slack so the next nightly run qualifies
DOCS_URL = "https://github.com/Stratio/kind/blob/master/docs/gcp/cloud-resources-report.md"
RULE = "━" * 64


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def age(now, then):
    if not then:
        return "?"
    hours = (now - then).total_seconds() / 3600
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} d"


def last(url):
    return (url or "").rsplit("/", 1)[-1]


def region_of(location):
    return location.rsplit("-", 1)[0] if location.count("-") == 2 else location  # zone → region


def is_risky(machine_type, accelerators):
    m = re.search(r"-(\d+)(-|$)", machine_type)
    return bool(accelerators) or machine_type.startswith(GPU_FAMILIES) or bool(m and int(m.group(1)) >= RISKY_MIN_VCPU)


def kept(labels):
    return (labels or {}).get(KEEP_LABEL, "").lower() == "true"


def pvc_name(disk):
    try:
        return json.loads(disk.description or "{}").get(PVC_KEY, "")
    except ValueError:
        return ""


def next_cleanup_run(now):
    run = now.replace(hour=23, minute=0, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    while run.weekday() >= 5:  # cleanup runs MON-FRI
        run += timedelta(days=1)
    return run


def scan_instances(now):
    out = []
    for scope, page in compute_v1.InstancesClient().aggregated_list(project=PROJECT):
        for i in page.instances:
            mt, started = last(i.machine_type), ts(i.last_start_timestamp) or ts(i.creation_timestamp)  # running time, like EC2 LaunchTime
            out.append({"zone": last(scope), "name": i.name, "type": mt, "state": i.status, "age": age(now, started),
                        "old": bool(started) and now - started > INSTANCE_MAX_AGE, "risky": is_risky(mt, i.guest_accelerators),
                        "networks": {last(n.network) for n in i.network_interfaces}, "owner": i.labels.get("owner", "")})
    return out


def live_clusters():
    # a failing GKE list counts as "every region live", so PVC disks are kept
    try:
        clusters = container_v1.ClusterManagerClient(transport="rest").list_clusters(parent=f"projects/{PROJECT}/locations/-").clusters
    except Exception as e:
        print(f"GKE list failed, PVC disks kept: {e}")
        return None
    return clusters


def pvc_of_live_cluster(disk, location, clusters):
    if clusters is None:
        return True
    cluster = disk.labels.get(CLUSTER_LABEL, "")
    if cluster:
        return cluster in {c.name for c in clusters}
    return region_of(location) in {region_of(c.location) for c in clusters}  # unknown cluster: any live GKE in the region


def disks_by_scope():
    for scope, page in compute_v1.DisksClient().aggregated_list(project=PROJECT):
        for d in page.disks:
            yield last(scope), d


def addresses_by_scope():
    for scope, page in compute_v1.AddressesClient().aggregated_list(project=PROJECT):  # includes the "global" scope
        for a in page.addresses:
            yield last(scope), a


def loose_address(a):
    return a.address_type == "EXTERNAL" and a.status == "RESERVED"


def orphan_cluster_ip(a, clusters, instance_names):
    # only <cluster>-apiserver IPs whose cluster has no GKE cluster and no instance left (any state)
    if not a.name.endswith(CLUSTER_IP_SUFFIX) or clusters is None:
        return False
    cluster = a.name[: -len(CLUSTER_IP_SUFFIX)]
    return cluster not in {c.name for c in clusters} and not any(n.startswith(cluster + "-") for n in instance_names)


GKE_RES = re.compile(r"^gke-(.+)-[0-9a-f]{8}-")  # gke-<cluster>-<8 hex>-<suffix>: firewall rules and subnets GKE creates per cluster
CAPG_FW = re.compile(r"^allow-(.+)-(cluster|healthchecks)$")  # firewall rules CAPG creates per cluster


def cluster_leftovers(clusters):
    # firewall rules, routes and subnets a cluster created and left behind; base networks and their own rules never match
    if clusters is None:
        return []
    gke = {c.name for c in clusters}
    raw = [i for _, p in compute_v1.InstancesClient().aggregated_list(project=PROJECT) for i in p.instances]
    names, tags = [i.name for i in raw], {t for i in raw for t in i.tags.items}

    def gone(c):
        return c not in gke and not any(n.startswith(c + "-") for n in names)

    out = []
    for f in compute_v1.FirewallsClient().list(project=PROJECT):
        m, g = CAPG_FW.match(f.name), GKE_RES.match(f.name)
        cluster = m.group(1) if m else g.group(1) if g else ""
        lb_rule = f.name.startswith("k8s-") and bool(f.target_tags) and not tags & set(f.target_tags)  # its nodes are gone
        if (cluster and gone(cluster)) or (not cluster and lb_rule):
            out.append({"kind": "firewall", "loc": "global", "name": f.name, "cluster": cluster or "?", "created": ts(f.creation_timestamp)})
    for r in compute_v1.RoutesClient().list(project=PROJECT):
        if r.next_hop_instance and last(r.next_hop_instance) not in names:
            out.append({"kind": "route", "loc": "global", "name": r.name, "cluster": "?", "created": ts(r.creation_timestamp)})
    for scope, page in compute_v1.SubnetworksClient().aggregated_list(project=PROJECT):
        for s in page.subnetworks:
            g = GKE_RES.match(s.name)
            if g and g.group(1) not in gke:
                out.append({"kind": "subnet", "loc": last(scope), "name": s.name, "cluster": g.group(1), "created": ts(s.creation_timestamp)})
    return out


def delete_leftover(r):
    if r["kind"] == "firewall":
        compute_v1.FirewallsClient().delete(project=PROJECT, firewall=r["name"]).result()
    elif r["kind"] == "route":
        compute_v1.RoutesClient().delete(project=PROJECT, route=r["name"]).result()
    else:
        compute_v1.SubnetworksClient().delete(project=PROJECT, region=r["loc"], subnetwork=r["name"]).result()


def scan(now, next_run):
    instances = scan_instances(now)
    running_nets = {n for i in instances if i["state"] == "RUNNING" for n in i["networks"]}
    clusters = live_clusters()
    data = {"instances": instances, "clusters": [], "disks": [], "ips": [], "nat": [], "lb": [],
            "leftovers": [{**r, "tonight": next_run - r["created"] >= CLEANUP_MIN_AGE} for r in cluster_leftovers(clusters)]}
    for c in clusters or []:
        created = ts(c.create_time)
        data["clusters"].append({"location": c.location, "name": c.name, "nodes": c.current_node_count, "age": age(now, created),
                                 "old": bool(created) and now - created > CLUSTER_MAX_AGE, "status": c.status.name})
    for loc, d in disks_by_scope():
        if d.users:
            continue
        since, pvc = d.labels.get(DISK_SINCE_LABEL), pvc_name(d)
        pvc_live = bool(pvc) and pvc_of_live_cluster(d, loc, clusters)
        data["disks"].append({"loc": loc, "name": d.name, "gb": d.size_gb, "type": last(d.type_), "age": age(now, ts(d.creation_timestamp)),
                              "protected": kept(d.labels), "pvc_live": pvc_live,
                              "tonight": bool(since) and not kept(d.labels) and not pvc_live
                              and next_run - datetime.fromtimestamp(int(since), timezone.utc) >= CLEANUP_MIN_AGE})
    names = [i["name"] for i in instances]
    for loc, a in addresses_by_scope():
        if loose_address(a):
            orphan, since = orphan_cluster_ip(a, clusters, names), a.labels.get(IP_SINCE_LABEL)
            data["ips"].append({"loc": loc, "name": a.name, "ip": a.address, "orphan": orphan,
                                "tonight": orphan and bool(since) and next_run - datetime.fromtimestamp(int(since), timezone.utc) >= CLEANUP_MIN_AGE})
    for scope, page in compute_v1.RoutersClient().aggregated_list(project=PROJECT):
        for r in page.routers:
            for n in r.nats:
                data["nat"].append({"loc": last(scope), "name": f"{r.name}/{n.name}", "network": last(r.network),
                                    "idle": last(r.network) not in running_nets})
    rules = [(last(s), f) for s, p in compute_v1.ForwardingRulesClient().aggregated_list(project=PROJECT) for f in p.forwarding_rules]
    for loc, f in rules:
        data["lb"].append({"loc": loc, "name": f.name, "ip": f.I_p_address, "scheme": f.load_balancing_scheme,
                           "age": age(now, ts(f.creation_timestamp))})
    return data


def set_label(loc, kind, res, key, value, no_writes):
    if no_writes:
        return
    labels = dict(res.labels)
    if value is None:
        labels.pop(key, None)
    else:
        labels[key] = value
    fp = res.label_fingerprint
    if kind == "disk" and loc.count("-") == 2:
        compute_v1.DisksClient().set_labels(project=PROJECT, zone=loc, resource=res.name, zone_set_labels_request_resource=
                                            compute_v1.ZoneSetLabelsRequest(labels=labels, label_fingerprint=fp)).result()
    elif kind == "disk":
        compute_v1.RegionDisksClient().set_labels(project=PROJECT, region=loc, resource=res.name, region_set_labels_request_resource=
                                                  compute_v1.RegionSetLabelsRequest(labels=labels, label_fingerprint=fp)).result()
    elif loc == "global":
        compute_v1.GlobalAddressesClient().set_labels(project=PROJECT, resource=res.name, global_set_labels_request_resource=
                                                      compute_v1.GlobalSetLabelsRequest(labels=labels, label_fingerprint=fp)).result()
    else:
        compute_v1.AddressesClient().set_labels(project=PROJECT, region=loc, resource=res.name, region_set_labels_request_resource=
                                                compute_v1.RegionSetLabelsRequest(labels=labels, label_fingerprint=fp)).result()


def delete(loc, kind, name):
    if kind == "disk" and loc.count("-") == 2:
        compute_v1.DisksClient().delete(project=PROJECT, zone=loc, disk=name).result()
    elif kind == "disk":
        compute_v1.RegionDisksClient().delete(project=PROJECT, region=loc, disk=name).result()
    elif loc == "global":
        compute_v1.GlobalAddressesClient().delete(project=PROJECT, address=name).result()
    else:
        compute_v1.AddressesClient().delete(project=PROJECT, region=loc, address=name).result()


def cleanup(now, actual, no_writes):
    summary = {"time": now.isoformat(), "mode": "actual_deletion" if actual else "report_only",
               "deleted": [], "would_delete": [], "waiting": [], "pvc_kept": [], "protected": 0, "errors": []}
    clusters = live_clusters()
    for loc, d in disks_by_scope():
        if d.users:
            if DISK_SINCE_LABEL in d.labels:
                _safe(summary, loc, d.name, "disk", lambda: set_label(loc, "disk", d, DISK_SINCE_LABEL, None, no_writes))  # attached again: reset the clock
            continue
        if kept(d.labels):
            summary["protected"] += 1
            continue
        pvc = pvc_name(d)
        if pvc and pvc_of_live_cluster(d, loc, clusters):
            if DISK_SINCE_LABEL in d.labels:
                _safe(summary, loc, d.name, "disk", lambda: set_label(loc, "disk", d, DISK_SINCE_LABEL, None, no_writes))  # restart the clock once the cluster is gone
            summary["pvc_kept"].append({"kind": "disk", "loc": loc, "name": d.name, "pvc": pvc})
            continue
        _safe(summary, loc, d.name, "disk", lambda: _cleanup_one(summary, now, actual, no_writes, loc, "disk", d, DISK_SINCE_LABEL))
    names = [i["name"] for i in scan_instances(now)]
    for loc, a in addresses_by_scope():
        if not (loose_address(a) and orphan_cluster_ip(a, clusters, names)):
            if IP_SINCE_LABEL in a.labels:  # in use again or its cluster is back: reset the clock
                _safe(summary, loc, a.name, "address", lambda: set_label(loc, "address", a, IP_SINCE_LABEL, None, no_writes))
            continue
        _safe(summary, loc, a.name, "address", lambda: _cleanup_one(summary, now, actual, no_writes, loc, "address", a, IP_SINCE_LABEL))
    for r in cluster_leftovers(clusters):  # no labels on firewall rules/routes/subnets: the 1-day margin counts from creation
        entry = {"kind": r["kind"], "loc": r["loc"], "name": r["name"], "loose_h": int((now - r["created"]).total_seconds() // 3600)}
        if now - r["created"] < CLEANUP_MIN_AGE:
            summary["waiting"].append(entry)
        elif not actual or no_writes:
            summary["would_delete"].append(entry)
        else:
            print(f"Deleting {r['kind']} {r['loc']}/{r['name']}")
            _safe(summary, r["loc"], r["name"], r["kind"], lambda r=r, entry=entry: (delete_leftover(r), summary["deleted"].append(entry)))
    print("CLEANUP_SUMMARY " + json.dumps(summary))
    return summary


def _safe(summary, loc, name, kind, action):
    # one failing resource is recorded and the run goes on with the rest
    try:
        action()
    except Exception as e:
        summary["errors"].append({"kind": kind, "loc": loc, "name": name, "error": str(e).splitlines()[0][:300]})


def _cleanup_one(summary, now, actual, no_writes, loc, kind, res, label):
    if res.labels.get(label):
        since = datetime.fromtimestamp(int(res.labels[label]), timezone.utc)
    else:
        since = now
        set_label(loc, kind, res, label, str(int(now.timestamp())), no_writes)
    entry = {"kind": kind, "loc": loc, "name": res.name, "loose_h": int((now - since).total_seconds() // 3600)}
    if now - since < CLEANUP_MIN_AGE:
        summary["waiting"].append(entry)
    elif not actual:
        summary["would_delete"].append(entry)
    else:
        print(f"Deleting {kind} {loc}/{res.name}")
        try:
            delete(loc, kind, res.name)
            summary["deleted"].append(entry)
        except Exception as e:
            summary["errors"].append({**entry, "error": str(e).splitlines()[0][:300]})


def load_summary(now):
    # last CLEANUP_SUMMARY line of the cleanup run, read back from Cloud Logging (no extra storage)
    since = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    flt = f'textPayload:"CLEANUP_SUMMARY" AND timestamp>="{since}"'
    for e in gcp_logging.Client(project=PROJECT).list_entries(filter_=flt, order_by=gcp_logging.DESCENDING, max_results=1):
        return json.loads(str(e.payload).split("CLEANUP_SUMMARY ", 1)[1])
    return None


def section(icon, title, rows, note="", count=None):
    # SNS sends plain text only, so sections are marked with an emoji, upper-case title and a rule line
    n = len(rows) if count is None else count
    head = f"{icon}  {title}" + (f" ({n})" if n != "" else "") + (f"  ·  {note}" if note else "")
    return ["", head, RULE] + (rows or ["   ✅ none"])


def item(*fields):
    return "   • " + "  |  ".join(str(f) for f in fields if f != "")


def render_daily(now, data, last_cleanup):
    running = [i for i in data["instances"] if i["state"] == "RUNNING"]
    risky = [i for i in running if i["risky"]]
    old = [i for i in running if i["old"] and not i["risky"]]
    old_clusters = [c for c in data["clusters"] if c["old"]]
    idle_nat = [n for n in data["nat"] if n["idle"]]
    tonight = sum(d["tonight"] for d in data["disks"]) + sum(i["tonight"] for i in data["ips"]) + sum(r["tonight"] for r in data["leftovers"])
    stale = not last_cleanup or now - datetime.fromisoformat(last_cleanup["time"]) > timedelta(hours=30)
    failed = bool(last_cleanup and last_cleanup["errors"])

    def flag(n):
        return "🔴" if n else "✅"

    out = [f"☁️  CLOUDS · GCP {PROJECT} · DAILY RESOURCES REPORT",
           f"📅  {now:%Y-%m-%d %H:%M} UTC  ·  report only: nothing is stopped or deleted by this report",
           RULE,
           f"   {flag(risky)} Risky instances running (GPU/accelerator or >= {RISKY_MIN_VCPU} vCPU): {len(risky)}",
           f"   {flag(old)} Instances running for more than 8 h: {len(old)}",
           f"   {flag(old_clusters)} GKE clusters older than 1 day: {len(old_clusters)}",
           f"   {flag(idle_nat)} Cloud NAT in a network with no running instance: {len(idle_nat)}",
           f"   {'🗑️' if tonight else '✅'} {'Deleted by the cleanup tonight' if CLEANUP_ACTUAL else 'Due tonight (cleanup in report_only, nothing is deleted)'}: {tonight}",
           f"   {'❌' if failed else '✅'} Last nightly cleanup: {'with errors' if failed else 'no errors' if last_cleanup else 'no run recorded'}"]
    out += section("🔥", f"RISKY INSTANCES RUNNING (GPU/accelerator or >= {RISKY_MIN_VCPU} vCPU)",
                   [item(i["zone"], i["name"], i["type"], f"running {i['age']}", i["owner"]) for i in risky])
    out += section("🖥️", "INSTANCES RUNNING FOR MORE THAN 8 h",
                   [item(i["zone"], i["name"], i["type"], f"running {i['age']}", i["owner"]) for i in old])
    out += section("☸️", "GKE CLUSTERS",
                   [item(c["location"], c["name"], c["status"], f"{c['nodes']} nodes", f"created {c['age']} ago",
                         "⚠️ older than 1 day" if c["old"] else "") for c in data["clusters"]])
    out += section("🌐", "CLOUD NAT",
                   [item(n["loc"], n["name"], n["network"], "⚠️ network with no running instance" if n["idle"] else "in use")
                    for n in data["nat"]])
    out += section("⚖️", "FORWARDING RULES (load balancers)",
                   [item(f["loc"], f["name"], f["ip"], f["scheme"], f"created {f['age']} ago") for f in data["lb"]])
    out += section("📍", "RESERVED UNUSED EXTERNAL IPs",
                   [item(i["loc"], i["name"], i["ip"], (DUE if i["tonight"] else "⏳ waiting (its cluster is gone)") if i["orphan"]
                         else "🛡️ admins' reservation, never cleaned") for i in data["ips"]])
    out += section("🧩", "LEFTOVERS OF GONE CLUSTERS (firewall rules, routes, subnets)",
                   [item(r["kind"], r["loc"], r["name"], f"cluster {r['cluster']}", DUE if r["tonight"] else "⏳ waiting (created < 1 day ago)")
                    for r in data["leftovers"]])
    out += section("💽", "UNATTACHED DISKS",
                   [item(d["loc"], d["name"], d["type"], f"{d['gb']} GB", d["age"],
                         "🛡️ protected (noautodelete)" if d["protected"] else "🛡️ PVC of a live cluster" if d["pvc_live"]
                         else DUE if d["tonight"] else "⏳ waiting")
                    for d in data["disks"]])
    rows = []
    if last_cleanup:
        rows.append(item(f"run {last_cleanup['time'][:16]} UTC", f"mode {last_cleanup['mode']}", f"deleted {len(last_cleanup['deleted'])}",
                         f"errors {len(last_cleanup['errors'])}", f"waiting {len(last_cleanup['waiting'])}",
                         f"pvc kept {len(last_cleanup.get('pvc_kept', []))}", "⚠️ older than 30 h" if stale else ""))
        rows += [f"       ↳ 🗑️ deleted {e['kind']} {e['loc']}/{e['name']}" for e in last_cleanup["deleted"]]
        rows += [f"       ↳ ❌ error {e['kind']} {e['loc']}/{e['name']}: {e['error']}" for e in last_cleanup["errors"]]
    out += section("🧹", "NIGHTLY CLEANUP — last run", rows, count="")
    out += ["", "ℹ️  HOW TO ACT", RULE,
            "   • 🗑️ / ⏳ marks: the nightly cleanup (MON-FRI 23:00 UTC) deletes it if it is still loose; ⏳ = first seen, not yet due",
            "   • Keep a disk: label it noautodelete=true (or attach it again)",
            "   • PVC disks are kept while a GKE cluster lives in their region",
            "   • Reserved external IPs are never touched, except <cluster>-apiserver ones whose cluster no longer exists",
            "   • Firewall rules, routes and subnets a cluster created are deleted once the cluster is gone; base networks are never touched",
            "   • Nothing here stops instances or clusters: delete what you no longer need",
            f"   • Docs and code: {DOCS_URL}"]
    return "\n".join(out) + "\n"


def sns_client():
    region, role = TOPIC_ARN.split(":")[3], os.environ.get("AWS_ROLE_ARN")
    if not role:  # local runs: AWS profile / env credentials
        return boto3.client("sns", region_name=region)
    # Federation: the function service account's Google ID token is exchanged for 1-hour AWS credentials, no stored secret
    import google.auth.transport.requests
    from google.oauth2 import id_token
    token = id_token.fetch_id_token(google.auth.transport.requests.Request(), os.environ["AWS_TOKEN_AUDIENCE"])
    c = boto3.client("sts", region_name=region).assume_role_with_web_identity(
        RoleArn=role, RoleSessionName="gcp-cloud-report", WebIdentityToken=token)["Credentials"]
    return boto3.client("sns", region_name=region, aws_access_key_id=c["AccessKeyId"],
                        aws_secret_access_key=c["SecretAccessKey"], aws_session_token=c["SessionToken"])


def publish(subject, body, dry_run):
    print(body)
    if dry_run:
        return False
    resp = sns_client().publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body)
    print("published", resp["MessageId"])
    return True


def main(event):
    mode, dry_run, no_writes = event.get("mode", "daily"), bool(event.get("dry_run")), bool(event.get("no_writes"))
    now = datetime.now(timezone.utc)
    if mode == "cleanup":
        summary = cleanup(now, event.get("operating_mode") == "actual_deletion", no_writes)
        return {"mode": mode, "deleted": len(summary["deleted"]), "errors": len(summary["errors"])}
    if mode == "urgent":
        risky = [i for i in scan_instances(now) if i["risky"] and i["state"] == "RUNNING"]
        print(f"{len(risky)} risky instances, dry_run={dry_run}")
        if not risky:
            return {"mode": mode, "risky": 0, "published": False}
        body = "\n".join(
            [f"🚨  CLOUDS · GCP {PROJECT} · {len(risky)} RISKY INSTANCES RUNNING",
             f"📅  {now:%Y-%m-%d %H:%M} UTC  ·  GPU/accelerator or >= {RISKY_MIN_VCPU} vCPU", RULE]
            + [item(i["zone"], i["name"], i["type"], f"running {i['age']}", i["owner"]) for i in risky]
            + ["", "⚠️  Nothing is stopped automatically. This alert repeats every 4 hours while they run:",
               "   stop or delete them if nobody needs them.", f"   • Docs and code: {DOCS_URL}"]) + "\n"
        sent = publish(f"[Clouds][GCP][{PROJECT}][URGENT] {len(risky)} risky instances running", body, dry_run)
        return {"mode": mode, "risky": len(risky), "published": sent}
    body = render_daily(now, scan(now, next_cleanup_run(now)), load_summary(now))
    sent = publish(f"[Clouds][GCP][{PROJECT}][Report] daily resources {now:%Y-%m-%d}", body, dry_run)
    return {"mode": mode, "published": sent}
