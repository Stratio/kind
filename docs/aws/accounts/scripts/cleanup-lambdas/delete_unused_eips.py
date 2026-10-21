#!/usr/bin/env python3
# deleteUnusedEIPs: release Elastic IPs unassociated for more than 1 day, in every region.
# The first run that sees an address unassociated tags it with the time; it is released on a later run once that time is old enough.
# Event {"operating_mode": "actual_deletion"} releases; anything else only reports. Ends with one CLEANUP_SUMMARY log line.

import json
import boto3
from datetime import datetime, timezone, timedelta

SINCE_TAG = "cleanup.stratio.com/unassociated-since"
MIN_AGE = timedelta(hours=23)  # 1 day, minus slack so the next nightly run qualifies


def main(event, context):
    actual = (event or {}).get("operating_mode") == "actual_deletion"
    now = datetime.now(timezone.utc)
    summary = {"lambda": "deleteUnusedEIPs", "mode": "actual_deletion" if actual else "report_only",
               "deleted": [], "would_delete": [], "waiting": [], "errors": []}
    regions = [r["RegionName"] for r in boto3.client("ec2", region_name="eu-west-1").describe_regions()["Regions"]]
    for region in regions:
        ec2 = boto3.client("ec2", region_name=region)
        for a in ec2.describe_addresses()["Addresses"]:
            alloc, tags = a["AllocationId"], {t["Key"]: t["Value"] for t in a.get("Tags", [])}
            if "AssociationId" in a:
                if SINCE_TAG in tags:
                    ec2.delete_tags(Resources=[alloc], Tags=[{"Key": SINCE_TAG}])  # associated again: reset the clock
                continue
            if SINCE_TAG in tags:
                since = datetime.fromisoformat(tags[SINCE_TAG])
            else:
                since = now
                ec2.create_tags(Resources=[alloc], Tags=[{"Key": SINCE_TAG, "Value": now.isoformat()}])
            item = {"region": region, "id": alloc, "ip": a.get("PublicIp", ""),
                    "unassociated_h": int((now - since).total_seconds() // 3600)}
            if now - since < MIN_AGE:
                summary["waiting"].append(item)
            elif not actual:
                summary["would_delete"].append(item)
            else:
                print(f"Releasing unassociated EIP in region {region}: {alloc}")
                try:
                    ec2.release_address(AllocationId=alloc)
                    summary["deleted"].append(item)
                except Exception as e:
                    summary["errors"].append({**item, "error": str(e)})
    print("CLEANUP_SUMMARY " + json.dumps(summary))
    return summary
