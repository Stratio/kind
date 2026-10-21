#!/usr/bin/env python3
# deleteUnusedEBS: delete EBS volumes unattached for more than 1 day, in every region; NoAutoDelete=true is never deleted.
# The first run that sees a volume unattached tags it with the time; it is deleted on a later run once that time is old enough.
# Event {"operating_mode": "actual_deletion"} deletes; anything else only reports. Ends with one CLEANUP_SUMMARY log line.

import json
import boto3
from datetime import datetime, timezone, timedelta

SINCE_TAG = "cleanup.stratio.com/unattached-since"
MIN_AGE = timedelta(hours=23)  # 1 day, minus slack so the next nightly run qualifies


def main(event, context):
    actual = (event or {}).get("operating_mode") == "actual_deletion"
    now = datetime.now(timezone.utc)
    summary = {"lambda": "deleteUnusedEBS", "mode": "actual_deletion" if actual else "report_only",
               "deleted": [], "would_delete": [], "waiting": [], "protected": 0, "errors": []}
    regions = [r["RegionName"] for r in boto3.client("ec2", region_name="eu-west-1").describe_regions()["Regions"]]
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
