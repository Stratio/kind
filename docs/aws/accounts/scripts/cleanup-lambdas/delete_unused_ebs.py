#!/usr/bin/env python3
# deleteUnusedEBS: delete EBS volumes unattached for more than 1 day, in every region; NoAutoDelete=true is never deleted.
# The first run that sees a volume unattached tags it with the time; it is deleted on a later run once that time is old enough.
# Event {"operating_mode": "actual_deletion"} deletes; anything else only reports. Ends with one CLEANUP_SUMMARY log line.

import json
import boto3
from datetime import datetime, timezone, timedelta

SINCE_TAG = "cleanup.stratio.com/unattached-since"
MIN_AGE = timedelta(hours=23)  # 1 day, minus slack so the next nightly run qualifies
PVC_TAG = "kubernetes.io/created-for/pvc/name"  # set by the EBS CSI driver on every PVC volume


def pvc_cluster(tags):
    # the driver adds the cluster tags only when started with a cluster id (aws-ebs-csi-driver pkg/driver/controller.go)
    return tags.get("KubernetesCluster") or next((k.split("/", 2)[2] for k in tags if k.startswith("kubernetes.io/cluster/")), "")


def pvc_of_live_cluster(tags, region, live):
    cluster = pvc_cluster(tags)
    return cluster in live["all"] if cluster else bool(live["by_region"].get(region, True))  # unknown cluster: any live EKS in the region


def main(event, context):
    actual = (event or {}).get("operating_mode") == "actual_deletion"
    now = datetime.now(timezone.utc)
    summary = {"lambda": "deleteUnusedEBS", "mode": "actual_deletion" if actual else "report_only",
               "deleted": [], "would_delete": [], "waiting": [], "pvc_kept": [], "protected": 0, "errors": []}
    regions = [r["RegionName"] for r in boto3.client("ec2", region_name="eu-west-1").describe_regions()["Regions"]]
    live = {"all": set(), "by_region": {}}
    for region in regions:  # a region whose EKS list fails counts as live, so its PVC volumes are kept
        try:
            names = [n for p in boto3.client("eks", region_name=region).get_paginator("list_clusters").paginate() for n in p["clusters"]]
            live["all"].update(names)
            live["by_region"][region] = bool(names)
        except Exception:
            live["by_region"][region] = True
    for region in regions:
        ec2 = boto3.client("ec2", region_name=region)
        for page in ec2.get_paginator("describe_volumes").paginate():
            for v in page["Volumes"]:
                vid, tags = v["VolumeId"], {t["Key"]: t["Value"] for t in v.get("Tags", [])}
                if v["Attachments"]:
                    if SINCE_TAG in tags:
                        ec2.delete_tags(Resources=[vid], Tags=[{"Key": SINCE_TAG}])  # attached again: reset the clock
                    continue
                if v["State"] != "available":
                    continue
                if tags.get("NoAutoDelete", "").lower() == "true":
                    summary["protected"] += 1
                    continue
                if PVC_TAG in tags and pvc_of_live_cluster(tags, region, live):
                    if SINCE_TAG in tags:
                        ec2.delete_tags(Resources=[vid], Tags=[{"Key": SINCE_TAG}])  # restart the clock once the cluster is gone
                    summary["pvc_kept"].append({"region": region, "id": vid, "pvc": tags[PVC_TAG], "cluster": pvc_cluster(tags) or "?"})
                    continue
                if SINCE_TAG in tags:
                    since = datetime.fromisoformat(tags[SINCE_TAG])
                else:
                    since = now
                    ec2.create_tags(Resources=[vid], Tags=[{"Key": SINCE_TAG, "Value": now.isoformat()}])
                item = {"region": region, "id": vid, "size_gb": v["Size"],
                        "unattached_h": int((now - since).total_seconds() // 3600)}
                if now - since < MIN_AGE:
                    summary["waiting"].append(item)
                elif not actual:
                    summary["would_delete"].append(item)
                else:
                    print(f"Deleting unassociated EBS volume in region {region}: {vid}")
                    try:
                        ec2.delete_volume(VolumeId=vid)
                        summary["deleted"].append(item)
                    except Exception as e:
                        summary["errors"].append({**item, "error": str(e)})
    print("CLEANUP_SUMMARY " + json.dumps(summary))
    return summary
