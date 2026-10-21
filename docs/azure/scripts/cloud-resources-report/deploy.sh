#!/usr/bin/env bash
# Creates or updates the Azure cloud-resources-report Function App of one subscription; mail via AWS SNS with OIDC federation (no secrets).
# Usage: SUB=<subscription id> SUB_NAME=<short name> TOPIC_ARN=<SNS topic ARN> [AWS_PROFILE_PUB=cloud-provisioner] bash deploy.sh
set -euo pipefail
cd "$(dirname "$0")"
SUB=${SUB:?subscription id}; SUB_NAME=${SUB_NAME:?short name, e.g. EOS}
LOC=${LOC:-westeurope}
SUFFIX=$(echo "${SUB_NAME,,}${SUB:0:4}" | tr -cd 'a-z0-9')
RG=cloud-guardrails
SA=cloudguard$SUFFIX
FN=clouds-report-$SUFFIX
TOPIC_ARN=${TOPIC_ARN:?SNS topic ARN the report publishes to}
TEST_TOPIC_ARN=${TOPIC_ARN%:*}:cloud-report-test  # same account; the role may publish to both
AWS_P="--profile ${AWS_PROFILE_PUB:-cloud-provisioner}"
AWS_ACCOUNT=$(cut -d: -f5 <<<"$TOPIC_ARN")
ROLE=azure-cloud-report-publisher
APP_NAME=clouds-report-aws-publisher
TAGS="owner=jnovoa purpose=cloud-resources-report"
A="--subscription $SUB"
TENANT=$(az account show $A --query tenantId -o tsv)

# Azure: resource group, storage, Function App with system identity
az group create $A -n $RG -l $LOC --tags $TAGS >/dev/null
az storage account show $A -g $RG -n $SA >/dev/null 2>&1 || \
  az storage account create $A -g $RG -n $SA -l $LOC --sku Standard_LRS --min-tls-version TLS1_2 --allow-blob-public-access false --tags $TAGS >/dev/null
az functionapp show $A -g $RG -n $FN >/dev/null 2>&1 || \
  az functionapp create $A -g $RG -n $FN --storage-account $SA --consumption-plan-location $LOC --os-type Linux \
    --runtime python --runtime-version 3.12 --functions-version 4 --assign-identity '[system]' --tags $TAGS >/dev/null
MI=$(az functionapp identity show $A -g $RG -n $FN --query principalId -o tsv)
sed "s/SUBSCRIPTION_ID/$SUB/" cleanup-role.json > /tmp/cleanup-role-$SUB.json
az role definition list --custom-role-only true --name "Clouds cleanup (disks and public IPs)" $A --query "[0].id" -o tsv | grep -q . \
  || az role definition create --role-definition @/tmp/cleanup-role-$SUB.json >/dev/null
for role in Reader "Cost Management Reader" "Tag Contributor" "Clouds cleanup (disks and public IPs)"; do
  az role assignment create --assignee-object-id "$MI" --assignee-principal-type ServicePrincipal --role "$role" --scope "/subscriptions/$SUB" >/dev/null 2>&1 || true
done

# Entra app = audience of the identity's token for AWS
APP_ID=$(az ad app list --display-name $APP_NAME --query "[0].appId" -o tsv)
[ -n "$APP_ID" ] || APP_ID=$(az ad app create --display-name $APP_NAME --query appId -o tsv)
az ad app update --id "$APP_ID" --identifier-uris "api://$APP_ID"
az ad sp show --id "$APP_ID" >/dev/null 2>&1 || az ad sp create --id "$APP_ID" >/dev/null
AUD="api://$APP_ID"

# AWS: OIDC provider for the tenant + role that only this identity can assume, limited to sns:Publish
ISSUER_HOST="sts.windows.net/$TENANT/"
PROVIDER_ARN="arn:aws:iam::$AWS_ACCOUNT:oidc-provider/$ISSUER_HOST"
if aws iam get-open-id-connect-provider $AWS_P --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1; then
  aws iam add-client-id-to-open-id-connect-provider $AWS_P --open-id-connect-provider-arn "$PROVIDER_ARN" --client-id "$AUD" 2>/dev/null || true
else
  aws iam create-open-id-connect-provider $AWS_P --url "https://$ISSUER_HOST" --client-id-list "$AUD" \
    --tags Key=owner,Value=jnovoa Key=purpose,Value=cloud-resources-report >/dev/null
fi
TRUST="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Federated\":\"$PROVIDER_ARN\"},\"Action\":\"sts:AssumeRoleWithWebIdentity\",\"Condition\":{\"StringEquals\":{\"$ISSUER_HOST:aud\":\"$AUD\",\"$ISSUER_HOST:sub\":\"$MI\"}}}]}"
if aws iam get-role $AWS_P --role-name $ROLE >/dev/null 2>&1; then
  aws iam update-assume-role-policy $AWS_P --role-name $ROLE --policy-document "$TRUST"
else
  aws iam create-role $AWS_P --role-name $ROLE --assume-role-policy-document "$TRUST" \
    --tags Key=owner,Value=jnovoa Key=purpose,Value=cloud-resources-report >/dev/null
fi
aws iam put-role-policy $AWS_P --role-name $ROLE --policy-name sns-publish-report \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"sns:Publish\",\"Resource\":[\"$TOPIC_ARN\",\"$TEST_TOPIC_ARN\"]}]}"

az functionapp config appsettings set $A -g $RG -n $FN --settings \
  AZURE_SUBSCRIPTION_ID=$SUB AZURE_SUBSCRIPTION_NAME=$SUB_NAME TOPIC_ARN=$TOPIC_ARN \
  AWS_ROLE_ARN=arn:aws:iam::$AWS_ACCOUNT:role/$ROLE AWS_TOKEN_AUDIENCE=$AUD \
  "URGENT_SCHEDULE=0 0 */4 * * *" "DAILY_SCHEDULE=0 0 7 * * 1-5" "CLEANUP_SCHEDULE=0 0 23 * * 1-5" \
  CLEANUP_MODE=actual_deletion SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true >/dev/null

rm -f function.zip && zip -q function.zip function_app.py azure_report.py requirements.txt host.json
az functionapp deployment source config-zip $A -g $RG -n $FN --src function.zip --build-remote true >/dev/null
echo "deployed: $FN (identity $MI, audience $AUD, role arn:aws:iam::$AWS_ACCOUNT:role/$ROLE, topic $TOPIC_ARN)"
