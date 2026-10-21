#!/usr/bin/env python3
# CloudResourcesReport: AWS resources report by SNS email, every region; never stops or deletes anything.
# Event {"mode": "daily"} = full report, always published; {"mode": "urgent"} = risky instances only, published only if any.
# Event {"dry_run": true} logs the report without publishing.

import json
import os
import re
import boto3
from datetime import datetime, timezone, timedelta
from botocore.exceptions import ClientError

ACCOUNT = boto3.client("sts").get_caller_identity()["Account"]
REGION = "eu-west-1"
TOPIC_ARN = os.environ["TOPIC_ARN"]  # set by deploy.sh; SNS is the final channel (INFRA decision 2026-10-21)
INSTANCE_MAX_AGE = timedelta(hours=8)
EKS_MAX_AGE = timedelta(days=1)
RISKY_FAMILIES = {"g", "p", "inf", "trn", "dl", "f"}  # GPU / accelerator families
RISKY_MIN_XLARGE = 8
OWNER_KEYS = ("owner", "Owner", "keos.stratio.com/owner")
CLEANUP_LAMBDAS = ("deleteoldOIDC", "deleteUnusedEBS", "deleteUnusedEIPs", "cleanupEksLogGroups")
EBS_SINCE_TAG = "cleanup.stratio.com/unattached-since"
EIP_SINCE_TAG = "cleanup.stratio.com/unassociated-since"
CLEANUP_MIN_AGE = timedelta(hours=23)  # same rule as the cleanup Lambdas
OIDC_MIN_AGE = timedelta(days=2)


def tags_of(resource):
    return {t["Key"]: t["Value"] for t in resource.get("Tags", []) or []}


def owner_of(tags, vpc_owner=""):
    return next((tags[k] for k in OWNER_KEYS if k in tags), "") or vpc_owner


def is_risky(instance_type):
    family, _, size = instance_type.partition(".")
    letters = re.match(r"[a-z]*", family).group(0)
    xl = re.match(r"(\d+)xlarge", size)
    return letters in RISKY_FAMILIES or size.startswith("metal") or bool(xl and int(xl.group(1)) >= RISKY_MIN_XLARGE)


def next_cleanup_run(now):
    run = now.replace(hour=23, minute=0, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    while run.weekday() >= 5:  # cleanup rules run MON-FRI
        run += timedelta(days=1)
    return run


def age(now, then):
    hours = (now - then).total_seconds() / 3600
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} d"


def scan_instances(ec2, region, now, vpc_owners):
    running = []
    for page in ec2.get_paginator("describe_instances").paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]):
        for res in page["Reservations"]:
            for i in res["Instances"]:
                tags = tags_of(i)
                running.append({"region": region, "id": i["InstanceId"], "type": i["InstanceType"],
                                "name": tags.get("Name", ""), "owner": owner_of(tags, vpc_owners.get(i.get("VpcId"), "")),
                                "vpc": i.get("VpcId", ""), "age": age(now, i["LaunchTime"]),
                                "old": now - i["LaunchTime"] > INSTANCE_MAX_AGE, "risky": is_risky(i["InstanceType"])})
    return running


def scan_region(region, now, next_run, live_clusters):
    ec2 = boto3.client("ec2", region_name=region)
    out = {k: [] for k in ("instances", "eks", "nat", "vpc", "eip", "ebs", "lb", "logs")}
    vpcs = ec2.describe_vpcs()["Vpcs"]
    vpc_owners = {v["VpcId"]: owner_of(tags_of(v)) for v in vpcs}
    out["instances"] = scan_instances(ec2, region, now, vpc_owners)
    running_vpcs = {i["vpc"] for i in out["instances"]}
    for v in vpcs:
        if not v.get("IsDefault") and v["VpcId"] not in running_vpcs:
            tags = tags_of(v)
            out["vpc"].append({"region": region, "id": v["VpcId"], "name": tags.get("Name", ""), "owner": owner_of(tags),
                               "cidr": v["CidrBlock"], "cluster_gone": bool(owner_of(tags)) and owner_of(tags) not in live_clusters,
                               "kept": tags.get("NoAutoDelete", "").lower() == "true", "reason": tags.get("keep-reason", "")})

    eks = boto3.client("eks", region_name=region)
    for page in eks.get_paginator("list_clusters").paginate():
        for name in page["clusters"]:
            c = eks.describe_cluster(name=name)["cluster"]
            if now - c["createdAt"] <= EKS_MAX_AGE:
                continue
            groups = []
            for ng in eks.list_nodegroups(clusterName=name)["nodegroups"]:
                g = eks.describe_nodegroup(clusterName=name, nodegroupName=ng)["nodegroup"]
                s = g.get("scalingConfig", {})
                groups.append(f"{ng} {','.join(g.get('instanceTypes', []))} {g.get('amiType', '')} "
                              f"{g.get('capacityType', '')} {s.get('desiredSize')}/{s.get('minSize')}-{s.get('maxSize')}")
            out["eks"].append({"region": region, "name": name, "version": c.get("version", ""),
                               "owner": owner_of(c.get("tags", {})), "age": age(now, c["createdAt"]), "nodegroups": groups})

    for page in ec2.get_paginator("describe_nat_gateways").paginate(Filter=[{"Name": "state", "Values": ["available"]}]):
        for n in page["NatGateways"]:
            tags = tags_of(n)
            out["nat"].append({"region": region, "id": n["NatGatewayId"], "name": tags.get("Name", ""),
                               "owner": owner_of(tags, vpc_owners.get(n["VpcId"], "")), "age": age(now, n["CreateTime"]),
                               "idle": n["VpcId"] not in running_vpcs})

    for a in ec2.describe_addresses()["Addresses"]:
        if "AssociationId" in a:
            continue
        tags = tags_of(a)
        since = tags.get(EIP_SINCE_TAG)
        out["eip"].append({"region": region, "id": a["AllocationId"], "ip": a.get("PublicIp", ""), "name": tags.get("Name", ""),
                           "tonight": bool(since) and next_run - datetime.fromisoformat(since) >= CLEANUP_MIN_AGE})

    for page in ec2.get_paginator("describe_volumes").paginate():
        for v in page["Volumes"]:
            tags = tags_of(v)
            loose = v["State"] == "available"
            if not loose and v["VolumeType"] not in ("io1", "io2"):
                continue
            since = tags.get(EBS_SINCE_TAG)
            out["ebs"].append({"region": region, "id": v["VolumeId"], "type": v["VolumeType"], "size_gb": v["Size"],
                               "loose": loose, "protected": tags.get("NoAutoDelete", "").lower() == "true",
                               "owner": owner_of(tags), "age": age(now, v["CreateTime"]),
                               "tonight": loose and bool(since) and next_run - datetime.fromisoformat(since) >= CLEANUP_MIN_AGE})

    elbv2 = boto3.client("elbv2", region_name=region)
    lbs = [lb for page in elbv2.get_paginator("describe_load_balancers").paginate() for lb in page["LoadBalancers"]]
    for chunk in (lbs[i:i + 20] for i in range(0, len(lbs), 20)):
        tag_map = {d["ResourceArn"]: tags_of(d) for d in elbv2.describe_tags(ResourceArns=[lb["LoadBalancerArn"] for lb in chunk])["TagDescriptions"]}
        for lb in chunk:
            tags = tag_map.get(lb["LoadBalancerArn"], {})
            cluster = tags.get("elbv2.k8s.aws/cluster") or next((k.split("/")[-1] for k in tags if k.startswith("kubernetes.io/cluster/")), "")
            out["lb"].append({"region": region, "name": lb["LoadBalancerName"], "type": lb["Type"], "cluster": cluster,
                              "orphan": bool(cluster) and cluster not in live_clusters, "age": age(now, lb["CreatedTime"])})
    elb = boto3.client("elb", region_name=region)
    for lb in elb.describe_load_balancers()["LoadBalancerDescriptions"]:
        out["lb"].append({"region": region, "name": lb["LoadBalancerName"], "type": "classic", "cluster": "",
                          "orphan": False, "age": age(now, lb["CreatedTime"])})

    logs = boto3.client("logs", region_name=region)
    for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix="/aws/eks/"):
        for g in page["logGroups"]:
            cluster = g["logGroupName"].split("/")[3] if g["logGroupName"].count("/") >= 3 else ""
            out["logs"].append({"region": region, "name": g["logGroupName"], "mb": g.get("storedBytes", 0) // 2**20,
                                "orphan": cluster not in live_clusters, "retention_d": g.get("retentionInDays", "never")})
    return out


def live_eks(regions):
    clusters, issuers = set(), set()
    for region in regions:
        eks = boto3.client("eks", region_name=region)
        for page in eks.get_paginator("list_clusters").paginate():
            for name in page["clusters"]:
                clusters.add(name)
                issuer = eks.describe_cluster(name=name)["cluster"].get("identity", {}).get("oidc", {}).get("issuer", "")
                issuers.add(issuer.removeprefix("https://"))
    return clusters, issuers


def oidc_tonight(issuers, next_run):
    iam = boto3.client("iam")
    doomed = []
    for p in iam.list_open_id_connect_providers()["OpenIDConnectProviderList"]:
        url = p["Arn"].split(":oidc-provider/", 1)[1]
        if url.startswith("oidc.eks.") and url not in issuers:
            created = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=p["Arn"])["CreateDate"]
            if next_run - created >= OIDC_MIN_AGE:
                doomed.append(url)
    return doomed


def cleanup_last_runs(now):
    logs, cw = boto3.client("logs", region_name=REGION), boto3.client("cloudwatch", region_name=REGION)
    rows = []
    for fn in CLEANUP_LAMBDAS:
        stats = {}
        for metric in ("Invocations", "Errors"):
            points = cw.get_metric_statistics(Namespace="AWS/Lambda", MetricName=metric,
                                              Dimensions=[{"Name": "FunctionName", "Value": fn}],
                                              StartTime=now - timedelta(hours=24), EndTime=now, Period=86400,
                                              Statistics=["Sum"])["Datapoints"]
            stats[metric] = int(sum(p["Sum"] for p in points))
        summary = None
        events = [e for page in logs.get_paginator("filter_log_events").paginate(
            logGroupName=f"/aws/lambda/{fn}", startTime=int((now - timedelta(hours=24)).timestamp() * 1000),
            filterPattern='"CLEANUP_SUMMARY"') for e in page["events"]]
        events.sort(key=lambda e: e["timestamp"])
        if events:
            summary = json.loads(events[-1]["message"].split("CLEANUP_SUMMARY ", 1)[1])
        rows.append({"lambda": fn, "invocations": stats["Invocations"], "errors": stats["Errors"], "summary": summary})
    return rows


USAGE_LABELS = [  # (regex on the usage type, readable label); first match wins
    (r"BoxUsage:(.+)", "instance {0} running"), (r"SpotUsage:(.+)", "spot instance {0} running"),
    (r"NatGateway-Hours", "NAT gateways switched on"), (r"NatGateway-Bytes", "traffic through NAT gateways"),
    (r"EBS:VolumeUsage\.(.+)", "{0} disks (EBS)"), (r"EBS:SnapshotUsage", "disk snapshots"),
    (r"PublicIPv4", "public IPs"), (r"DataTransfer", "data transfer"), (r"AWSSecretsManager-Secrets", "secrets stored"),
    (r"TimedStorage-ByteHrs", "images stored (ECR)"), (r"VendedLog-Bytes", "logs ingested (EKS control plane)"),
    (r"AmazonEKS-Hours:perCluster", "EKS clusters (control plane)"), (r"LoadBalancerUsage", "load balancers switched on"),
]
REGION_PREFIX = {"EU": "eu-west-1", "USE1": "us-east-1", "USW1": "us-west-1", "USW2": "us-west-2", "EUC1": "eu-central-1"}


def readable_usage(usage_type):
    prefix, _, rest = usage_type.partition("-")
    region = REGION_PREFIX.get(prefix, "")
    if not region:  # no region prefix means us-east-1
        rest, region = usage_type, "us-east-1"
    for pattern, label in USAGE_LABELS:
        m = re.search(pattern, rest)
        if m:
            return f"{label.format(*m.groups())} ({region})"
    return f"{usage_type}"


def cost_yesterday(now):
    ce = boto3.client("ce", region_name="us-east-1")
    day = (now - timedelta(days=1)).date()
    resp = ce.get_cost_and_usage(TimePeriod={"Start": day.isoformat(), "End": now.date().isoformat()}, Granularity="DAILY",
                                 Metrics=["UnblendedCost", "UsageQuantity"],
                                 GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "DIMENSION", "Key": "USAGE_TYPE"}])
    services = {}
    for g in resp["ResultsByTime"][0]["Groups"]:
        cost = float(g["Metrics"]["UnblendedCost"]["Amount"])
        if cost < 0.01:
            continue
        qty, unit = float(g["Metrics"]["UsageQuantity"]["Amount"]), g["Metrics"]["UsageQuantity"]["Unit"]
        services.setdefault(g["Keys"][0], []).append((cost, readable_usage(g["Keys"][1]), f"{qty:.1f} {unit}"))
    return day, sorted(((s, sum(c for c, _, _ in items), sorted(items, reverse=True)) for s, items in services.items()),
                       key=lambda r: -r[1])


RULE = "━" * 64


def section(icon, title, rows, note="", count=None):
    # SNS sends plain text only, so sections are marked with an emoji, upper-case title and a rule line
    n = len(rows) if count is None else count
    head = f"{icon}  {title}" + (f" ({n})" if n != "" else "") + (f"  ·  {note}" if note else "")
    return ["", head, RULE] + (rows or ["   ✅ none"])


def item(*fields):
    return "   • " + "  |  ".join(str(f) for f in fields if f != "")


def render_daily(now, data, cleanup, cost, oidc_doomed, scan_errors):
    day, services = cost
    risky = [i for i in data["instances"] if i["risky"]]
    old = [i for i in data["instances"] if i["old"] and not i["risky"]]
    idle_nat = [n for n in data["nat"] if n["idle"]]
    kept = [v for v in data["vpc"] if v["kept"]]
    loose = [v for v in data["ebs"] if v["loose"]]
    piops = [v for v in data["ebs"] if not v["loose"]]
    gone = [g for g in data["logs"] if g["orphan"]]
    tonight = len(oidc_doomed) + sum(e["tonight"] for e in data["eip"]) + sum(v["tonight"] for v in loose)
    failed = [r for r in cleanup if r["invocations"] == 0 or r["errors"]]
    total = sum(t for _, t, _ in services)

    def flag(n):
        return "🔴" if n else "✅"

    out = [f"☁️  CLOUDS · AWS {ACCOUNT} · DAILY RESOURCES REPORT",
           f"📅  {now:%Y-%m-%d %H:%M} UTC  ·  report only: nothing is stopped or deleted by this report", RULE,
           f"   {flag(risky)} Risky instances (GPU/accelerator or >= {RISKY_MIN_XLARGE}xlarge): {len(risky)}",
           f"   {flag(old)} Instances running > 8 h: {len(old)}",
           f"   {flag(data['eks'])} EKS clusters > 1 day: {len(data['eks'])}",
           f"   {flag(idle_nat)} NAT gateways in a VPC with no running instance: {len(idle_nat)}",
           f"   {'🗑️' if tonight else '✅'} Deleted by the cleanup tonight: {tonight}",
           f"   {flag(failed)} Cleanup Lambdas failed or not run: {len(failed)}",
           f"   💶 Cost of {day}: {total:.2f} USD"]

    out += section("🔥", f"RISKY INSTANCES (GPU/accelerator or >= {RISKY_MIN_XLARGE}xlarge)",
                   [item(i["region"], i["id"], i["type"], i["name"], f"owner: {i['owner'] or '-'}", i["age"]) for i in risky])
    out += section("🖥️", "INSTANCES RUNNING > 8 h",
                   [item(i["region"], i["id"], i["type"], i["name"], f"owner: {i['owner'] or '-'}", i["age"]) for i in old])
    eks_rows = []
    for c in data["eks"]:
        eks_rows.append(item(c["region"], c["name"], f"k8s {c['version']}", f"owner: {c['owner'] or '-'}", c["age"]))
        eks_rows += [f"       ↳ nodegroup {g}" for g in c["nodegroups"]]
    out += section("☸️", "EKS CLUSTERS > 1 day", eks_rows)
    out += section("🌐", "NAT GATEWAYS",
                   [item(n["region"], n["id"], n["name"], f"owner: {n['owner'] or '-'}", n["age"],
                         "⚠️ VPC with no running instance" if n["idle"] else "in use") for n in data["nat"]])
    out += section("🏗️", "VPCs WITH NO RUNNING INSTANCE",
                   [item(v["region"], v["id"], v["name"], f"owner: {v['owner'] or '-'}",
                         f"🛡️ kept on purpose ({v['reason'] or 'no reason given'})" if v["kept"]
                         else "💀 cluster gone" if v["cluster_gone"] else "")
                    for v in sorted(data["vpc"], key=lambda v: v["kept"])],
                   f"{len(kept)} kept on purpose (tag NoAutoDelete=true), default VPCs excluded")
    out += section("⚖️", "LOAD BALANCERS",
                   [item(lb["region"], lb["name"], lb["type"], f"cluster: {lb['cluster'] or '-'}", lb["age"],
                         "💀 cluster gone" if lb["orphan"] else "") for lb in data["lb"]])
    out += section("📍", "UNASSOCIATED ELASTIC IPs",
                   [item(e["region"], e["id"], e["ip"], e["name"], "🗑️ released tonight" if e["tonight"] else "⏳ waiting")
                    for e in data["eip"]])
    out += section("💽", "UNATTACHED EBS VOLUMES",
                   [item(v["region"], v["id"], v["type"], f"{v['size_gb']} GB", f"owner: {v['owner'] or '-'}", v["age"],
                         "🛡️ protected (NoAutoDelete)" if v["protected"] else "🗑️ deleted tonight" if v["tonight"] else "⏳ waiting")
                    for v in loose])
    out += section("⚡", "IO1/IO2 VOLUMES IN USE",
                   [item(v["region"], v["id"], v["type"], f"{v['size_gb']} GB", f"owner: {v['owner'] or '-'}", v["age"]) for v in piops])
    out += section("📜", "EKS LOG GROUPS",
                   [item(g["region"], g["name"], f"{g['mb']} MB", f"retention: {g['retention_d']}", "💀 cluster gone" if g["orphan"] else "")
                    for g in sorted(data["logs"], key=lambda g: -g["mb"])[:10]],
                   f"{len(gone)} of clusters that no longer exist ({sum(g['mb'] for g in gone)} MB); largest 10 shown",
                   count=len(data["logs"]))
    out += section("🔑", "OIDC PROVIDERS DELETED TONIGHT", [item(u) for u in oidc_doomed])
    rows = []
    for r in cleanup:
        s = r["summary"]
        state = "❌ did not run" if r["invocations"] == 0 else "❌ failed" if r["errors"] else "✅ ok"
        detail = (f"mode {s['mode']}, deleted {len(s['deleted'])}, errors {len(s['errors'])}, waiting {len(s['waiting'])}"
                  if s else "no CLEANUP_SUMMARY line")
        rows.append(item(r["lambda"], state, f"runs {r['invocations']}, failed runs {r['errors']}", detail))
        if s:
            rows += [f"       ↳ 🗑️ deleted {d['id']}" for d in s["deleted"]] + [f"       ↳ ❌ error {e['id']}: {e['error']}" for e in s["errors"]]
    out += section("🧹", "CLEANUP LAMBDAS — last 24 h", rows, count=len(cleanup))
    cost_rows = []
    for s, t, items in services:
        cost_rows.append(f"   • {t:8.2f} USD  {s}")
        cost_rows += [f"       ↳ {c:6.2f}  {what}  [{qty}]" for c, what, qty in items[:5]]
    out += section("💶", f"COST OF {day} BY SERVICE — total {total:.2f} USD (Cost Explorer)", cost_rows, count="")
    if scan_errors:
        out += section("❗", "REGIONS NOT FULLY SCANNED (the lists above may be incomplete)", [item(e) for e in scan_errors])
    out += FOOTER
    return "\n".join(out) + "\n"


DOCS_URL = "https://github.com/Stratio/kind/blob/master/docs/aws/accounts/new_accounts.md#lambda"
FOOTER = ["", "ℹ️  HOW TO ACT", RULE,
          "   • 🗑️ / ⏳ marks: the nightly cleanup (MON-FRI 23:00 UTC) deletes it if it is still loose; ⏳ = first seen, not yet due",
          "   • Keep an EBS volume: tag it NoAutoDelete=true (or attach it again)",
          "   • Keep a VPC on purpose: tags NoAutoDelete=true and keep-reason=<why>",
          "   • Elastic IPs cannot be protected: any IP unassociated for more than 1 day is released",
          "   • Nothing here stops instances: switch off or delete what you no longer need",
          f"   • Docs and code: {DOCS_URL}"]


def publish(subject, body, dry_run):
    print(body)
    if dry_run:
        return False
    resp = boto3.client("sns", region_name=REGION).publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body)
    print("published", resp["MessageId"])
    return True


def main(event, context):
    event = event or {}
    mode, dry_run = event.get("mode", "daily"), bool(event.get("dry_run"))
    now = datetime.now(timezone.utc)
    regions = [r["RegionName"] for r in boto3.client("ec2", region_name=REGION).describe_regions()["Regions"]]

    if mode == "urgent":
        risky = []
        for region in regions:
            ec2 = boto3.client("ec2", region_name=region)
            vpc_owners = {v["VpcId"]: owner_of(tags_of(v)) for v in ec2.describe_vpcs()["Vpcs"]}
            risky += [i for i in scan_instances(ec2, region, now, vpc_owners) if i["risky"]]
        print(f"{len(risky)} risky instances, dry_run={dry_run}")
        if not risky:
            return {"mode": mode, "risky": 0, "published": False}
        body = "\n".join(
            [f"🚨  CLOUDS · AWS {ACCOUNT} · {len(risky)} RISKY INSTANCES RUNNING",
             f"📅  {now:%Y-%m-%d %H:%M} UTC  ·  GPU/accelerator families or >= {RISKY_MIN_XLARGE}xlarge, every region", RULE]
            + [item(i["region"], i["id"], i["type"], i["name"], f"owner: {i['owner'] or '-'}", f"running {i['age']}") for i in risky]
            + ["", "⚠️  Nothing is stopped automatically. This alert repeats every 4 hours while they run:",
               "   switch them off or delete them if nobody needs them."] + FOOTER[-1:]) + "\n"
        sent = publish(f"[Clouds][AWS][{ACCOUNT}][URGENT] {len(risky)} risky instances running", body, dry_run)
        return {"mode": mode, "risky": len(risky), "published": sent}

    clusters, issuers = live_eks(regions)
    next_run = next_cleanup_run(now)
    data = {k: [] for k in ("instances", "eks", "nat", "vpc", "eip", "ebs", "lb", "logs")}
    errors = []
    for region in regions:
        try:
            for k, v in scan_region(region, now, next_run, clusters).items():
                data[k] += v
        except ClientError as e:
            errors.append(f"{region}: {e.response['Error']['Code']}")
    body = render_daily(now, data, cleanup_last_runs(now), cost_yesterday(now), oidc_tonight(issuers, next_run), errors)
    sent = publish(f"[Clouds][AWS][{ACCOUNT}][Report] daily resources {now:%Y-%m-%d}", body, dry_run)
    return {"mode": mode, "published": sent, "scan_errors": len(errors)}
