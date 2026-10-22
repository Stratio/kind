#!/usr/bin/env bash
# Creates or updates the GCP cloud-resources-report function of one project; mail via AWS SNS with Google OIDC federation (no secrets).
# Usage: PROJECT=<project id> TOPIC_ARN=<SNS topic ARN> [CLEANUP_MODE=report_only|actual_deletion] [AWS_PROFILE_PUB=cloud-provisioner] bash deploy.sh
# Runs as the project's default Compute SA (roles/editor holds every permission the function needs); RUN_AS_SA=<email> overrides it
set -euo pipefail
cd "$(dirname "$0")"
PROJECT=${PROJECT:?project id}
REGION=${REGION:-europe-west4}
FN=clouds-report
SA=${RUN_AS_SA:-$(gcloud projects describe $PROJECT --format 'value(projectNumber)')-compute@developer.gserviceaccount.com}
TOPIC_ARN=${TOPIC_ARN:?SNS topic ARN the report publishes to}
TEST_TOPIC_ARN=${TOPIC_ARN%:*}:cloud-report-test  # same account; the role may publish to both
CLEANUP_MODE=${CLEANUP_MODE:-report_only}
AWS_P="--profile ${AWS_PROFILE_PUB:-cloud-provisioner}"
AWS_ACCOUNT=$(cut -d: -f5 <<<"$TOPIC_ARN")
AWS_ROLE=gcp-cloud-report-publisher
AUD=sts.amazonaws.com
G="--project $PROJECT"

# GCP: APIs
gcloud services enable $G cloudscheduler.googleapis.com cloudfunctions.googleapis.com run.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com logging.googleapis.com compute.googleapis.com container.googleapis.com
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
AUTH_FLAG=""  # on an update the flag calls run.services.setIamPolicy (403 for editor); the service keeps its private policy
gcloud functions describe $FN $G --gen2 --region $REGION >/dev/null 2>&1 || AUTH_FLAG=--no-allow-unauthenticated
gcloud functions deploy $FN $G --gen2 --region $REGION --runtime python312 --source . --entry-point handler \
  --trigger-http $AUTH_FLAG --service-account $SA --memory 512Mi --timeout 540s \
  --update-labels owner=jnovoa,purpose=cloud-resources-report \
  --set-env-vars GCP_PROJECT_ID=$PROJECT,TOPIC_ARN=$TOPIC_ARN,CLEANUP_MODE=$CLEANUP_MODE,AWS_ROLE_ARN=arn:aws:iam::$AWS_ACCOUNT:role/$AWS_ROLE,AWS_TOKEN_AUDIENCE=$AUD
URL=$(gcloud functions describe $FN $G --gen2 --region $REGION --format 'value(serviceConfig.uri)')
for job in "urgent|0 */4 * * *|{\"mode\":\"urgent\"}" "daily|0 7 * * 1-5|{\"mode\":\"daily\"}" \
           "cleanup|0 23 * * 1-5|{\"mode\":\"cleanup\",\"operating_mode\":\"$CLEANUP_MODE\"}"; do
  IFS='|' read -r name cron body <<<"$job"
  verb=create hdr=--headers; gcloud scheduler jobs describe clouds-report-$name $G --location $REGION >/dev/null 2>&1 && verb=update hdr=--update-headers
  gcloud scheduler jobs $verb http clouds-report-$name $G --location $REGION --schedule "$cron" --time-zone Etc/UTC \
    --uri "$URL" --http-method POST $hdr Content-Type=application/json --message-body "$body" \
    --oidc-service-account-email $SA --oidc-token-audience "$URL" --attempt-deadline 540s >/dev/null
done
echo "deployed: $FN ($URL, sa $SA uid $SA_UID, role arn:aws:iam::$AWS_ACCOUNT:role/$AWS_ROLE, topic $TOPIC_ARN, cleanup $CLEANUP_MODE)"
