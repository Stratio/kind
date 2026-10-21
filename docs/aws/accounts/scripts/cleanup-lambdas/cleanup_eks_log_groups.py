#!/usr/bin/env python3
# cleanupEksLogGroups: in every region, gives every /aws/eks/* log group a 30-day retention if it has none, and deletes
# groups of clusters that no longer exist once they are empty (retention has already expired their data) for more than 1 day.
# Event {"operating_mode": "actual_deletion"} acts; anything else only reports. Ends with one CLEANUP_SUMMARY log line.

import json
import boto3
from datetime import datetime, timezone, timedelta

RETENTION_DAYS = 30
SINCE_TAG = "cleanup.stratio.com/empty-since"
MIN_AGE = timedelta(hours=23)  # 1 day, minus slack so the next nightly run qualifies


def main(event, context):
    actual = (event or {}).get("operating_mode") == "actual_deletion"
    now = datetime.now(timezone.utc)
    summary = {"lambda": "cleanupEksLogGroups", "mode": "actual_deletion" if actual else "report_only",
               "deleted": [], "would_delete": [], "waiting": [], "retention_set": [], "errors": []}
    regions = [r["RegionName"] for r in boto3.client("ec2", region_name="eu-west-1").describe_regions()["Regions"]]
    live = set()
    for region in regions:  # an error in any region aborts the run before anything is deleted
        eks = boto3.client("eks", region_name=region)
        for page in eks.get_paginator("list_clusters").paginate():
            live.update(page["clusters"])
    for region in regions:
        logs = boto3.client("logs", region_name=region)
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix="/aws/eks/"):
            for g in page["logGroups"]:
                name, arn = g["logGroupName"], g["arn"].removesuffix(":*")
                cluster = name.split("/")[3] if name.count("/") >= 3 else ""
                item = {"region": region, "id": name}
                if "retentionInDays" not in g:
                    if actual:
                        logs.put_retention_policy(logGroupName=name, retentionInDays=RETENTION_DAYS)
                    summary["retention_set"].append(item)
                if cluster in live or g.get("storedBytes", 0) > 0:
                    continue
                tags = logs.list_tags_for_resource(resourceArn=arn).get("tags", {})
                if SINCE_TAG in tags:
                    since = datetime.fromisoformat(tags[SINCE_TAG])
                else:
                    since = now
                    logs.tag_resource(resourceArn=arn, tags={SINCE_TAG: now.isoformat()})
                if now - since < MIN_AGE:
                    summary["waiting"].append(item)
                elif not actual:
                    summary["would_delete"].append(item)
                else:
                    print(f"Deleting empty log group of a deleted cluster in region {region}: {name}")
                    try:
                        logs.delete_log_group(logGroupName=name)
                        summary["deleted"].append(item)
                    except Exception as e:
                        summary["errors"].append({**item, "error": str(e)})
    print("CLEANUP_SUMMARY " + json.dumps(summary))
    return summary
