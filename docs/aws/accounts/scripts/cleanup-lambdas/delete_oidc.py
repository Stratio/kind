#!/usr/bin/env python3
# deleteoldOIDC: delete IAM OIDC providers of EKS clusters that no longer exist in any region and are older than 2 days.
# Event {"operating_mode": "actual_deletion"} deletes; anything else only reports. Ends with one CLEANUP_SUMMARY log line.

import json
import boto3
from datetime import datetime, timezone, timedelta

MIN_AGE = timedelta(days=2)
EKS_ISSUER_PREFIX = "oidc.eks."  # never touch non-EKS providers (GitHub Actions, etc.)


def live_issuers():
    regions = [r["RegionName"] for r in boto3.client("ec2", region_name="eu-west-1").describe_regions()["Regions"]]
    issuers = set()
    for region in regions:
        eks = boto3.client("eks", region_name=region)
        for page in eks.get_paginator("list_clusters").paginate():
            for name in page["clusters"]:
                issuer = eks.describe_cluster(name=name)["cluster"].get("identity", {}).get("oidc", {}).get("issuer", "")
                issuers.add(issuer.removeprefix("https://"))
    return issuers


def main(event, context):
    actual = (event or {}).get("operating_mode") == "actual_deletion"
    now = datetime.now(timezone.utc)
    issuers = live_issuers()  # an error in any region aborts the run before anything is deleted
    iam = boto3.client("iam")
    summary = {"lambda": "deleteoldOIDC", "mode": "actual_deletion" if actual else "report_only",
               "deleted": [], "would_delete": [], "waiting": [], "kept": 0, "errors": []}
    for p in iam.list_open_id_connect_providers()["OpenIDConnectProviderList"]:
        arn = p["Arn"]
        url = arn.split(":oidc-provider/", 1)[1]
        if not url.startswith(EKS_ISSUER_PREFIX) or url in issuers:
            summary["kept"] += 1
            continue
        created = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=arn)["CreateDate"]
        item = {"id": arn, "age_days": (now - created).days}
        if now - created < MIN_AGE:
            summary["waiting"].append(item)
        elif not actual:
            summary["would_delete"].append(item)
        else:
            print(f"Deleting provider {arn}")
            try:
                iam.delete_open_id_connect_provider(OpenIDConnectProviderArn=arn)
                summary["deleted"].append(item)
            except Exception as e:
                summary["errors"].append({**item, "error": str(e)})
    print("CLEANUP_SUMMARY " + json.dumps(summary))
    return summary
