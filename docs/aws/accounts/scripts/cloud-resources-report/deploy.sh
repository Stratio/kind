#!/usr/bin/env bash
# Creates or updates the CloudResourcesReport Lambda (eu-west-1) of the account behind $PROFILE and its two schedules:
# daily full report MON-FRI 07:00 UTC ({"mode": "daily"}) and urgent risky-instance alert every 4 h ({"mode": "urgent"}).
# PROFILE=cloud-provisioner (963, default) | cloud-provisioner-ct (975). TOPIC=<SNS topic name>; CREATE_TOPIC=1 creates it.
set -euo pipefail
cd "$(dirname "$0")"
PROFILE=${PROFILE:-cloud-provisioner}
TOPIC=${TOPIC:-instances-watch}
P="--profile $PROFILE"
R="--region eu-west-1"
ACCOUNT=$(aws sts get-caller-identity $P --query Account --output text)
TOPIC_ARN="arn:aws:sns:eu-west-1:$ACCOUNT:$TOPIC"
ROLE=stratio-cloudresourcesreport-role
FN=CloudResourcesReport
DESC="Report only (SNS email): risky instances every 4 h; daily inventory of instances, EKS, NAT, VPC, LB, EIP, EBS, log groups, cleanup Lambdas and cost, all regions"

if [ "${CREATE_TOPIC:-0}" = 1 ]; then
  aws sns create-topic $P $R --name "$TOPIC" --tags Key=owner,Value=jnovoa Key=purpose,Value=cloud-resources-report >/dev/null
  aws sns set-topic-attributes $P $R --topic-arn "$TOPIC_ARN" --attribute-name DisplayName --attribute-value "Clouds AWS"
fi

if ! aws iam get-role $P --role-name $ROLE >/dev/null 2>&1; then
  aws iam create-role $P --role-name $ROLE --assume-role-policy-document file://trust.json \
    --description "CloudResourcesReport Lambda: read-only inventory in all regions, publish SNS report" \
    --tags Key=owner,Value=jnovoa Key=purpose,Value=cloud-resources-report >/dev/null
  sleep 10  # a new role is not assumable by Lambda right away
fi
sed -e "s/ACCOUNT_ID/$ACCOUNT/g" -e "s#TOPIC_ARN#$TOPIC_ARN#g" policy.json > /tmp/crr-policy-$ACCOUNT.json
aws iam put-role-policy $P --role-name $ROLE --policy-name stratio-cloudresourcesreport-policy --policy-document file:///tmp/crr-policy-$ACCOUNT.json
ROLE_ARN=$(aws iam get-role $P --role-name $ROLE --query Role.Arn --output text)

rm -f function.zip && zip -q function.zip cloud_resources_report.py
if aws lambda get-function $P $R --function-name $FN >/dev/null 2>&1; then
  aws lambda update-function-code $P $R --function-name $FN --zip-file fileb://function.zip >/dev/null
  aws lambda wait function-updated-v2 $P $R --function-name $FN
  aws lambda update-function-configuration $P $R --function-name $FN --description "$DESC" --timeout 900 --memory-size 512 \
    --runtime python3.12 --environment "Variables={TOPIC_ARN=$TOPIC_ARN}" >/dev/null
else
  aws lambda create-function $P $R --function-name $FN --runtime python3.12 --handler cloud_resources_report.main \
    --role "$ROLE_ARN" --zip-file fileb://function.zip --timeout 900 --memory-size 512 --description "$DESC" \
    --environment "Variables={TOPIC_ARN=$TOPIC_ARN}" --tags owner=jnovoa,purpose=cloud-resources-report >/dev/null
fi
aws lambda wait function-updated-v2 $P $R --function-name $FN
FN_ARN=$(aws lambda get-function $P $R --function-name $FN --query Configuration.FunctionArn --output text)

for spec in "daily|cron(0 7 ? * MON-FRI *)" "urgent|cron(0 */4 * * ? *)"; do
  mode=${spec%%|*} cron=${spec##*|}
  rule_arn=$(aws events put-rule $P $R --name "$FN-$mode" --schedule-expression "$cron" --state ENABLED \
    --description "CloudResourcesReport $mode" --query RuleArn --output text)
  aws lambda add-permission $P $R --function-name $FN --statement-id "events-$FN-$mode" --action lambda:InvokeFunction \
    --principal events.amazonaws.com --source-arn "$rule_arn" >/dev/null 2>&1 || true
  aws events put-targets $P $R --rule "$FN-$mode" --targets "Id=1,Arn=$FN_ARN,Input='{\"mode\": \"$mode\"}'" >/dev/null
done
echo "deployed: $FN in $ACCOUNT ($ROLE_ARN), topic $TOPIC_ARN"
