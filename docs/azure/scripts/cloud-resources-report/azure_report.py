#!/usr/bin/env python3
# Azure counterpart of the AWS CloudResourcesReport for one subscription: urgent alert (risky VMs), daily report and
# nightly cleanup (unattached disks, unassociated public IPs). Mail goes through the AWS SNS topic of the AWS report.
# main({"mode": "urgent"|"daily"|"cleanup", "dry_run": bool, "operating_mode": "actual_deletion"}); never stops VMs.

import json
import os
import re
from datetime import datetime, timezone, timedelta

import boto3
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource.resources import ResourceManagementClient

SUBSCRIPTION = os.environ["AZURE_SUBSCRIPTION_ID"]
SUB_NAME = os.environ.get("AZURE_SUBSCRIPTION_NAME", SUBSCRIPTION)
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
RISKY_MIN_VCPU = 32  # Azure sizes carry the vCPU count in their name (Standard_D32s_v5)
INSTANCE_MAX_AGE = timedelta(hours=8)
DISK_SINCE_TAG = "cleanup-unattached-since"  # Azure tag names cannot contain "/"
IP_SINCE_TAG = "cleanup-unassociated-since"
CLEANUP_MIN_AGE = timedelta(hours=23)  # 1 day, minus slack so the next nightly run qualifies
SUMMARY_CONTAINER, SUMMARY_BLOB = "cloud-cleanup", "last-summary.json"
DOCS_URL = "https://github.com/Stratio/kind/blob/master/docs/azure/cloud-resources-report.md"
RULE = "━" * 64

cred = DefaultAzureCredential()
compute = ComputeManagementClient(cred, SUBSCRIPTION)
network = NetworkManagementClient(cred, SUBSCRIPTION)
resources = ResourceManagementClient(cred, SUBSCRIPTION)


def val(x):
    return getattr(x, "value", x)  # SDK enums print as Class.NAME otherwise


def rg_of(resource_id):
    return resource_id.split("/")[4]


def age(now, then):
    if not then:
        return "?"
    hours = (now - then).total_seconds() / 3600
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} d"


def is_risky(size):
    name = size.removeprefix("Standard_").removeprefix("Basic_")
    m = re.match(r"([A-Z]+)(\d+)", name)
    return name.startswith("N") or bool(m and int(m.group(2)) >= RISKY_MIN_VCPU)  # N-series = GPU


def power_state(vm):
    statuses = (vm.instance_view.statuses if vm.instance_view else None) or []
    return next((s.code.split("/", 1)[1] for s in statuses if s.code.startswith("PowerState/")), "unknown")


def next_cleanup_run(now):
    run = now.replace(hour=23, minute=0, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    while run.weekday() >= 5:  # cleanup runs MON-FRI
        run += timedelta(days=1)
    return run


def kept(tags):
    return (tags or {}).get("NoAutoDelete", "").lower() == "true"


def set_tag(resource_id, key, value=None):
    op = "Merge" if value is not None else "Delete"
    resources.tags.begin_update_at_scope(resource_id, {"operation": op, "properties": {"tags": {key: value or ""}}}).result()


def scan_vms(now):
    vms = []
    for vm in compute.virtual_machines.list_all(status_only="true"):
        size = vm.hardware_profile.vm_size if vm.hardware_profile else "?"
        vms.append({"rg": rg_of(vm.id), "name": vm.name, "size": size, "state": power_state(vm),
                    "age": age(now, vm.time_created), "old": bool(vm.time_created) and now - vm.time_created > INSTANCE_MAX_AGE,
                    "risky": is_risky(size), "location": vm.location})
    return vms


def scan(now, next_run):
    vms = scan_vms(now)
    running_rgs = {v["rg"].lower() for v in vms if v["state"] == "running"}
    data = {"vms": vms, "disks": [], "ips": [], "nat": [], "lb": [], "vnet": [], "bastion": []}
    for d in compute.disks.list():
        if d.disk_state != "Unattached":
            continue
        since = (d.tags or {}).get(DISK_SINCE_TAG)
        data["disks"].append({"rg": rg_of(d.id), "name": d.name, "gb": d.disk_size_gb, "sku": val(d.sku.name) if d.sku else "",
                              "age": age(now, d.time_created), "protected": kept(d.tags),
                              "tonight": bool(since) and not kept(d.tags) and next_run - datetime.fromisoformat(since) >= CLEANUP_MIN_AGE})
    for ip in network.public_ip_addresses.list_all():
        if ip.ip_configuration is None and ip.nat_gateway is None:
            since = (ip.tags or {}).get(IP_SINCE_TAG)
            data["ips"].append({"rg": rg_of(ip.id), "name": ip.name, "ip": ip.ip_address or "-",
                                "tonight": bool(since) and next_run - datetime.fromisoformat(since) >= CLEANUP_MIN_AGE})
    for n in network.nat_gateways.list_all():
        data["nat"].append({"rg": rg_of(n.id), "name": n.name, "idle": rg_of(n.id).lower() not in running_rgs})
    for lb in network.load_balancers.list_all():
        data["lb"].append({"rg": rg_of(lb.id), "name": lb.name, "sku": val(lb.sku.name) if lb.sku else "",
                           "idle": rg_of(lb.id).lower() not in running_rgs})
    for b in network.bastion_hosts.list():
        data["bastion"].append({"rg": rg_of(b.id), "name": b.name, "sku": val(b.sku.name) if b.sku else "",
                                "idle": rg_of(b.id).lower() not in running_rgs})
    for v in network.virtual_networks.list_all():
        if rg_of(v.id).lower() not in running_rgs:
            tags = v.tags or {}
            data["vnet"].append({"rg": rg_of(v.id), "name": v.name, "kept": kept(tags), "reason": tags.get("keep-reason", "")})
    return data


def cost_yesterday(now):
    day = (now - timedelta(days=1)).date()
    params = {"type": "ActualCost", "timeframe": "Custom",
              "timePeriod": {"from": f"{day}T00:00:00Z", "to": f"{day}T23:59:59Z"},
              "dataset": {"granularity": "None", "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                          "grouping": [{"type": "Dimension", "name": "ServiceName"},
                                       {"type": "Dimension", "name": "MeterSubCategory"}]}}
    result = CostManagementClient(cred).query.usage(f"/subscriptions/{SUBSCRIPTION}", params)
    cols = [c.name for c in result.columns]
    services, currency = {}, ""
    for row in result.rows:
        r = dict(zip(cols, row))
        currency = r.get("Currency", currency)
        if r["Cost"] >= 0.01:
            services.setdefault(r["ServiceName"], []).append((r["Cost"], r.get("MeterSubCategory") or r["ServiceName"]))
    rows = sorted(((s, sum(c for c, _ in items), sorted(items, reverse=True)) for s, items in services.items()), key=lambda r: -r[1])
    return day, currency, rows


def section(icon, title, rows, note="", count=None):
    # SNS sends plain text only, so sections are marked with an emoji, upper-case title and a rule line
    n = len(rows) if count is None else count
    head = f"{icon}  {title}" + (f" ({n})" if n != "" else "") + (f"  ·  {note}" if note else "")
    return ["", head, RULE] + (rows or ["   ✅ none"])


def item(*fields):
    return "   • " + "  |  ".join(str(f) for f in fields if f != "")


def load_summary():
    conn = os.environ.get("AzureWebJobsStorage", "")
    if not conn.startswith("DefaultEndpointsProtocol"):
        return None
    from azure.storage.blob import BlobServiceClient
    blob = BlobServiceClient.from_connection_string(conn).get_blob_client(SUMMARY_CONTAINER, SUMMARY_BLOB)
    return json.loads(blob.download_blob().readall()) if blob.exists() else None


def save_summary(summary):
    conn = os.environ.get("AzureWebJobsStorage", "")
    if not conn.startswith("DefaultEndpointsProtocol"):
        return
    from azure.storage.blob import BlobServiceClient
    svc = BlobServiceClient.from_connection_string(conn)
    if not svc.get_container_client(SUMMARY_CONTAINER).exists():
        svc.create_container(SUMMARY_CONTAINER)
    svc.get_blob_client(SUMMARY_CONTAINER, SUMMARY_BLOB).upload_blob(json.dumps(summary), overwrite=True)


def cleanup(now, actual):
    summary = {"time": now.isoformat(), "mode": "actual_deletion" if actual else "report_only",
               "deleted": [], "would_delete": [], "waiting": [], "locked": [], "protected": 0, "errors": []}
    for d in compute.disks.list():
        tags = d.tags or {}
        if d.disk_state != "Unattached":
            if DISK_SINCE_TAG in tags:
                _safe(summary, d.id, d.name, "disk", lambda d=d: set_tag(d.id, DISK_SINCE_TAG))  # attached again: reset the clock
            continue
        if kept(tags):
            summary["protected"] += 1
            continue
        _safe(summary, d.id, d.name, "disk", lambda d=d, tags=tags: _cleanup_one(
            summary, now, actual, d.id, d.name, "disk", tags.get(DISK_SINCE_TAG), DISK_SINCE_TAG,
            lambda: compute.disks.begin_delete(rg_of(d.id), d.name).result()))
    for ip in network.public_ip_addresses.list_all():
        tags = ip.tags or {}
        if ip.ip_configuration is not None or ip.nat_gateway is not None:
            if IP_SINCE_TAG in tags:
                _safe(summary, ip.id, ip.name, "public-ip", lambda ip=ip: set_tag(ip.id, IP_SINCE_TAG))
            continue
        _safe(summary, ip.id, ip.name, "public-ip", lambda ip=ip, tags=tags: _cleanup_one(
            summary, now, actual, ip.id, ip.name, "public-ip", tags.get(IP_SINCE_TAG), IP_SINCE_TAG,
            lambda: network.public_ip_addresses.begin_delete(rg_of(ip.id), ip.name).result()))
    print("CLEANUP_SUMMARY " + json.dumps(summary))
    return summary


def _safe(summary, rid, name, kind, action):
    # one failing resource (e.g. its group is being deleted) is recorded and the run goes on with the rest
    try:
        action()
    except Exception as e:
        summary["errors"].append({"kind": kind, "rg": rg_of(rid), "name": name, "error": str(e).splitlines()[0][:300]})


def _cleanup_one(summary, now, actual, rid, name, kind, since_value, tag, delete):
    if since_value:
        since = datetime.fromisoformat(since_value)
    else:
        since = now
        set_tag(rid, tag, now.isoformat())
    entry = {"kind": kind, "rg": rg_of(rid), "name": name, "loose_h": int((now - since).total_seconds() // 3600)}
    if now - since < CLEANUP_MIN_AGE:
        summary["waiting"].append(entry)
    elif not actual:
        summary["would_delete"].append(entry)
    else:
        print(f"Deleting {kind} {rg_of(rid)}/{name}")
        try:
            delete()
            summary["deleted"].append(entry)
        except Exception as e:
            if "ScopeLocked" in str(e):  # CanNotDelete lock on purpose: kept, not an error
                summary["locked"].append(entry)
            else:
                summary["errors"].append({**entry, "error": str(e).splitlines()[0][:300]})


def render_daily(now, data, last_cleanup, cost):
    day, currency, services = cost
    risky = [v for v in data["vms"] if v["risky"] and v["state"] == "running"]
    old = [v for v in data["vms"] if v["old"] and v["state"] == "running" and not v["risky"]]
    idle_nat = [n for n in data["nat"] if n["idle"]]
    tonight = sum(d["tonight"] for d in data["disks"]) + sum(i["tonight"] for i in data["ips"])
    total = sum(t for _, t, _ in services)
    stale = not last_cleanup or now - datetime.fromisoformat(last_cleanup["time"]) > timedelta(hours=30)
    failed = bool(last_cleanup and last_cleanup["errors"])

    def flag(n):
        return "🔴" if n else "✅"

    out = [f"☁️  CLOUDS · AZURE {SUB_NAME} · DAILY RESOURCES REPORT",
           f"📅  {now:%Y-%m-%d %H:%M} UTC  ·  subscription {SUBSCRIPTION}  ·  report only: nothing is stopped or deleted by this report",
           RULE,
           f"   {flag(risky)} Risky VMs running (GPU N-series or >= {RISKY_MIN_VCPU} vCPU): {len(risky)}",
           f"   {flag(old)} VMs running, created more than 8 h ago: {len(old)}",
           f"   {flag(idle_nat)} NAT gateways in a resource group with no running VM: {len(idle_nat)}",
           f"   {'🗑️' if tonight else '✅'} Deleted by the cleanup tonight: {tonight}",
           f"   {'❌' if failed else '✅'} Last nightly cleanup: {'with errors' if failed else 'no errors' if last_cleanup else 'no run recorded'}",
           f"   💶 Cost of {day}: {total:.2f} {currency}"]
    out += section("🔥", f"RISKY VMs RUNNING (GPU N-series or >= {RISKY_MIN_VCPU} vCPU)",
                   [item(v["rg"], v["name"], v["size"], f"created {v['age']} ago") for v in risky])
    out += section("🖥️", "VMs RUNNING, CREATED MORE THAN 8 h AGO",
                   [item(v["rg"], v["name"], v["size"], f"created {v['age']} ago") for v in old])
    out += section("🌐", "NAT GATEWAYS",
                   [item(n["rg"], n["name"], "⚠️ resource group with no running VM" if n["idle"] else "in use") for n in data["nat"]])
    out += section("🏗️", "VNETs IN RESOURCE GROUPS WITH NO RUNNING VM",
                   [item(v["rg"], v["name"], f"🛡️ kept on purpose ({v['reason'] or 'no reason given'})" if v["kept"] else "")
                    for v in sorted(data["vnet"], key=lambda v: v["kept"])],
                   f"{sum(v['kept'] for v in data['vnet'])} kept on purpose (tag NoAutoDelete=true)")
    out += section("🛡️", "AZURE BASTION HOSTS (billed per hour while they exist)",
                   [item(b["rg"], b["name"], b["sku"], "⚠️ resource group with no running VM" if b["idle"] else "") for b in data["bastion"]])
    out += section("⚖️", "LOAD BALANCERS",
                   [item(lb["rg"], lb["name"], lb["sku"], "⚠️ resource group with no running VM" if lb["idle"] else "in use") for lb in data["lb"]])
    out += section("📍", "UNASSOCIATED PUBLIC IPs",
                   [item(i["rg"], i["name"], i["ip"], "🗑️ deleted tonight" if i["tonight"] else "⏳ waiting") for i in data["ips"]])
    out += section("💽", "UNATTACHED DISKS",
                   [item(d["rg"], d["name"], d["sku"], f"{d['gb']} GB", d["age"],
                         "🛡️ protected (NoAutoDelete)" if d["protected"] else "🗑️ deleted tonight" if d["tonight"] else "⏳ waiting")
                    for d in data["disks"]])
    rows = []
    if last_cleanup:
        rows.append(item(f"run {last_cleanup['time'][:16]} UTC", f"mode {last_cleanup['mode']}", f"deleted {len(last_cleanup['deleted'])}",
                         f"errors {len(last_cleanup['errors'])}", f"waiting {len(last_cleanup['waiting'])}",
                         "⚠️ older than 30 h" if stale else ""))
        rows += [f"       ↳ 🗑️ deleted {e['kind']} {e['rg']}/{e['name']}" for e in last_cleanup["deleted"]]
        rows += [f"       ↳ 🔒 locked, kept {e['kind']} {e['rg']}/{e['name']}" for e in last_cleanup.get("locked", [])]
        rows += [f"       ↳ ❌ error {e['kind']} {e['rg']}/{e['name']}: {e['error']}" for e in last_cleanup["errors"]]
    out += section("🧹", "NIGHTLY CLEANUP — last run", rows, count="")
    cost_rows = []
    for s, t, items in services:
        cost_rows.append(f"   • {t:8.2f} {currency}  {s}")
        cost_rows += [f"       ↳ {c:6.2f}  {what}" for c, what in items[:5]]
    out += section("💶", f"COST OF {day} BY SERVICE — total {total:.2f} {currency} (Cost Management)", cost_rows, count="")
    out += ["", "ℹ️  HOW TO ACT", RULE,
            "   • 🗑️ / ⏳ marks: the nightly cleanup (MON-FRI 23:00 UTC) deletes it if it is still loose; ⏳ = first seen, not yet due",
            "   • Keep a disk: tag it NoAutoDelete=true (or attach it again)",
            "   • Keep a VNet on purpose: tags NoAutoDelete=true and keep-reason=<why> (VNets are never deleted)",
            "   • Public IPs cannot be protected: any IP unassociated for more than 1 day is deleted",
            "   • Nothing here stops VMs: deallocate or delete what you no longer need",
            f"   • Docs and code: {DOCS_URL}"]
    return "\n".join(out) + "\n"


def sns_client():
    region, role = TOPIC_ARN.split(":")[3], os.environ.get("AWS_ROLE_ARN")
    if not role:  # local runs: AWS profile / env credentials
        return boto3.client("sns", region_name=region)
    # Federation: the managed identity's Entra token is exchanged for 1-hour AWS credentials, no stored secret
    token = cred.get_token(os.environ["AWS_TOKEN_AUDIENCE"] + "/.default").token
    c = boto3.client("sts", region_name=region).assume_role_with_web_identity(
        RoleArn=role, RoleSessionName="azure-cloud-report", WebIdentityToken=token)["Credentials"]
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
    mode, dry_run = event.get("mode", "daily"), bool(event.get("dry_run"))
    now = datetime.now(timezone.utc)
    if mode == "cleanup":
        summary = cleanup(now, event.get("operating_mode") == "actual_deletion")
        if not dry_run:
            save_summary(summary)
        return {"mode": mode, "deleted": len(summary["deleted"]), "errors": len(summary["errors"])}
    if mode == "urgent":
        risky = [v for v in scan_vms(now) if v["risky"] and v["state"] == "running"]
        print(f"{len(risky)} risky VMs, dry_run={dry_run}")
        if not risky:
            return {"mode": mode, "risky": 0, "published": False}
        body = "\n".join(
            [f"🚨  CLOUDS · AZURE {SUB_NAME} · {len(risky)} RISKY VMs RUNNING",
             f"📅  {now:%Y-%m-%d %H:%M} UTC  ·  GPU N-series or >= {RISKY_MIN_VCPU} vCPU", RULE]
            + [item(v["rg"], v["name"], v["size"], f"created {v['age']} ago") for v in risky]
            + ["", "⚠️  Nothing is stopped automatically. This alert repeats every 4 hours while they run:",
               "   deallocate or delete them if nobody needs them.", f"   • Docs and code: {DOCS_URL}"]) + "\n"
        sent = publish(f"[Clouds][Azure][{SUB_NAME}][URGENT] {len(risky)} risky VMs running", body, dry_run)
        return {"mode": mode, "risky": len(risky), "published": sent}
    next_run = next_cleanup_run(now)
    body = render_daily(now, scan(now, next_run), load_summary(), cost_yesterday(now))
    sent = publish(f"[Clouds][Azure][{SUB_NAME}][Report] daily resources {now:%Y-%m-%d}", body, dry_run)
    return {"mode": mode, "published": sent}
