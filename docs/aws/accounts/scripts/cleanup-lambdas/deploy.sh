#!/usr/bin/env bash
# Creates or updates the four cleanup Lambdas (eu-west-1) of the account behind $PROFILE, their IAM and their nightly
# MON-FRI 23:00 UTC rules with {"operating_mode": "actual_deletion"}, then invokes each in report-only mode.
# PROFILE=cloud-provisioner (963, default) | cloud-provisioner-ct (975). An existing function keeps its current role.
set -euo pipefail
cd "$(dirname "$0")"
PROFILE=${PROFILE:-cloud-provisioner}
P="--profile $PROFILE"
R="--region eu-west-1"
ACCOUNT=$(aws sts get-caller-identity $P --query Account --output text)

for spec in deleteoldOIDC:delete_oidc:oidc deleteUnusedEBS:delete_unused_ebs:ebs \
            deleteUnusedEIPs:delete_unused_eips:eips cleanupEksLogGroups:cleanup_eks_log_groups:logs; do
  IFS=: read -r fn mod pol <<<"$spec"
  sed "s/ACCOUNT_ID/$ACCOUNT/g" "policy-$pol.json" > "/tmp/cleanup-$pol-$ACCOUNT.json"
  rm -f "$mod.zip" && zip -q "$mod.zip" "$mod.py"

  if aws lambda get-function $P $R --function-name "$fn" >/dev/null 2>&1; then
    role=$(aws lambda get-function-configuration $P $R --function-name "$fn" --query Role --output text); role=${role##*/}
    aws iam put-role-policy $P --role-name "$role" --policy-name stratio-cleanup-policy --policy-document "file:///tmp/cleanup-$pol-$ACCOUNT.json"
    aws lambda update-function-code $P $R --function-name "$fn" --zip-file "fileb://$mod.zip" >/dev/null
    aws lambda wait function-updated-v2 $P $R --function-name "$fn"
    aws lambda update-function-configuration $P $R --function-name "$fn" --timeout 900 --runtime python3.12 >/dev/null
  else
    role="stratio-cleanup-$fn"
    if ! aws iam get-role $P --role-name "$role" >/dev/null 2>&1; then
      aws iam create-role $P --role-name "$role" --assume-role-policy-document file://../cloud-resources-report/trust.json \
        --description "Cleanup Lambda $fn" --tags Key=owner,Value=jnovoa Key=purpose,Value=cloud-cleanup >/dev/null
    fi
    aws iam put-role-policy $P --role-name "$role" --policy-name stratio-cleanup-policy --policy-document "file:///tmp/cleanup-$pol-$ACCOUNT.json"
    sleep 10  # a new role is not assumable by Lambda right away
    aws lambda create-function $P $R --function-name "$fn" --runtime python3.12 --handler "$mod.main" \
      --role "arn:aws:iam::$ACCOUNT:role/$role" --zip-file "fileb://$mod.zip" --timeout 900 --memory-size 256 \
      --description "Nightly cleanup ($mod.py)" --tags owner=jnovoa,purpose=cloud-cleanup >/dev/null
  fi
  aws lambda wait function-updated-v2 $P $R --function-name "$fn"
  fn_arn=$(aws lambda get-function $P $R --function-name "$fn" --query Configuration.FunctionArn --output text)

  rule_arn=$(aws events put-rule $P $R --name "$fn" --schedule-expression "cron(0 23 ? * MON-FRI *)" --state ENABLED \
    --description "$fn nightly cleanup" --query RuleArn --output text)
  aws lambda add-permission $P $R --function-name "$fn" --statement-id "events-$fn" --action lambda:InvokeFunction \
    --principal events.amazonaws.com --source-arn "$rule_arn" >/dev/null 2>&1 || true
  tid=$(aws events list-targets-by-rule $P $R --rule "$fn" --query 'Targets[0].Id' --output text)
  [ "$tid" = None ] && tid=1  # keep the existing target id, otherwise put-targets adds a second target
  aws events put-targets $P $R --rule "$fn" --targets "Id=$tid,Arn=$fn_arn,Input='{\"operating_mode\": \"actual_deletion\"}'" >/dev/null

  echo "== $fn in $ACCOUNT (report_only)"
  aws lambda invoke $P $R --function-name "$fn" --payload '{}' --cli-binary-format raw-in-base64-out \
    --cli-read-timeout 950 "/tmp/cleanup-$fn-$ACCOUNT.out" >/dev/null
  head -c 600 "/tmp/cleanup-$fn-$ACCOUNT.out"; echo  # a pipe to head would break the script under pipefail
done
