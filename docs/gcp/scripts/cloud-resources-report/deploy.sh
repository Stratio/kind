#!/usr/bin/env bash
# Creates or updates the GCP cloud-resources-report function of one project; mail via AWS SNS with Google OIDC federation (no secrets).
# Usage: PROJECT=<project id> TOPIC_ARN=<SNS topic ARN> [CLEANUP_MODE=report_only|actual_deletion] [AWS_PROFILE_PUB=cloud-provisioner] bash deploy.sh
# RUN_AS_SA=<existing SA email> runs as that SA and skips custom role + bindings (deployer without IAM admin; SA must already hold the permissions)
set -euo pipefail
cd "$(dirname "$0")"
PROJECT=${PROJECT:?project id}
REGION=${REGION:-europe-west4}
FN=clouds-report
SA_NAME=cloud-resources-report
SA=${RUN_AS_SA:-$SA_NAME@$PROJECT.iam.gserviceaccount.com}
ROLE_ID=cloudsCleanupReport
TOPIC_ARN=${TOPIC_ARN:?SNS topic ARN the report publishes to}
TEST_TOPIC_ARN=${TOPIC_ARN%:*}:cloud-report-test  # same account; the role may publish to both
CLEANUP_MODE=${CLEANUP_MODE:-report_only}
AWS_P="--profile ${AWS_PROFILE_PUB:-cloud-provisioner}"
AWS_ACCOUNT=$(cut -d: -f5 <<<"$TOPIC_ARN")
AWS_ROLE=gcp-cloud-report-publisher
AUD=sts.amazonaws.com
G="--project $PROJECT"

# GCP: APIs, service account, custom role (read + delete/label of disks and addresses), log reader
gcloud services enable $G cloudscheduler.googleapis.com cloudfunctions.googleapis.com run.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com logging.googleapis.com compute.googleapis.com container.googleapis.com
if [ -z "${RUN_AS_SA:-}" ]; then
  gcloud iam service-accounts describe $SA $G >/dev/null 2>&1 || \
    gcloud iam service-accounts create $SA_NAME $G --display-name "Clouds cloud-resources-report"
  PERMS=compute.instances.list,compute.disks.list,compute.disks.delete,compute.disks.setLabels,compute.addresses.list,compute.globalAddresses.list,compute.routers.list,compute.forwardingRules.list,compute.globalForwardingRules.list,container.clusters.list
  gcloud iam roles describe $ROLE_ID $G >/dev/null 2>&1 \
    && gcloud iam roles update $ROLE_ID $G --permissions $PERMS --quiet >/dev/null \
    || gcloud iam roles create $ROLE_ID $G --title "Clouds cleanup and report" --permissions $PERMS --stage GA --quiet >/dev/null
  for role in projects/$PROJECT/roles/$ROLE_ID roles/logging.viewer; do
    gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$SA --role $role --condition None >/dev/null
  done
fi
SA_UID=$(gcloud iam service-accounts describe $SA $G --format 'value(uniqueId)')

# AWS: role that only this service account can assume (Google is a built-in web identity provider), limited to sns:Publish
TRUST="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Federated\":\"accounts.google.com\"},\"Action\":\"sts:AssumeRoleWithWebIdentity\",\"Condition\":{\"StringEquals\":{\"accounts.google.com:sub\":\"$SA_UID\",\"accounts.google.com:oaud\":\"$AUD\"}}}]}"
if aws iam get-role $AWS_P --role-name $AWS_ROLE >/dev/null 2>&1; then
  aws iam update-assume-role-policy $AWS_P --role-name $AWS_ROLE --policy-document "$TRUST"
else
  aws iam create-role $AWS_P --role-name $AWS_ROLE --assume-role-policy-document "$TRUST" \
    --tags Key=owner,Value=jnovoa Key=purpose,Value=cloud-resources-report >/dev/null
fi
aws iam put-role-policy $AWS_P --role-name $AWS_ROLE --policy-name sns-publish-report \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"sns:Publish\",\"Resource\":[\"$TOPIC_ARN\",\"$TEST_TOPIC_ARN\"]}]}"

# Function (gen2, HTTP, private) and its three schedules
AUTH_FLAG=--no-allow-unauthenticated
[ -z "${RUN_AS_SA:-}" ] || AUTH_FLAG=""  # the flag calls run.services.setIamPolicy; without it the service keeps its policy (private)
gcloud functions deploy $FN $G --gen2 --region $REGION --runtime python312 --source . --entry-point handler \
  --trigger-http $AUTH_FLAG --service-account $SA --memory 512Mi --timeout 540s \
  --update-labels owner=jnovoa,purpose=cloud-resources-report \
  --set-env-vars GCP_PROJECT_ID=$PROJECT,TOPIC_ARN=$TOPIC_ARN,CLEANUP_MODE=$CLEANUP_MODE,AWS_ROLE_ARN=arn:aws:iam::$AWS_ACCOUNT:role/$AWS_ROLE,AWS_TOKEN_AUDIENCE=$AUD
URL=$(gcloud functions describe $FN $G --gen2 --region $REGION --format 'value(serviceConfig.uri)')
[ -n "${RUN_AS_SA:-}" ] || gcloud run services add-iam-policy-binding $FN $G --region $REGION --member serviceAccount:$SA --role roles/run.invoker >/dev/null
for job in "urgent|0 */4 * * *|{\"mode\":\"urgent\"}" "daily|0 7 * * 1-5|{\"mode\":\"daily\"}" \
           "cleanup|0 23 * * 1-5|{\"mode\":\"cleanup\",\"operating_mode\":\"$CLEANUP_MODE\"}"; do
  IFS='|' read -r name cron body <<<"$job"
  verb=create hdr=--headers; gcloud scheduler jobs describe clouds-report-$name $G --location $REGION >/dev/null 2>&1 && verb=update hdr=--update-headers
  gcloud scheduler jobs $verb http clouds-report-$name $G --location $REGION --schedule "$cron" --time-zone Etc/UTC \
    --uri "$URL" --http-method POST $hdr Content-Type=application/json --message-body "$body" \
    --oidc-service-account-email $SA --oidc-token-audience "$URL" --attempt-deadline 540s >/dev/null
done
echo "deployed: $FN ($URL, sa $SA uid $SA_UID, role arn:aws:iam::$AWS_ACCOUNT:role/$AWS_ROLE, topic $TOPIC_ARN, cleanup $CLEANUP_MODE)"
