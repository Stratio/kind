#!/usr/bin/env bash
# Confirms an SNS email subscription from its confirmation link WITHOUT the link being clicked, with
# AuthenticateOnUnsubscribe=true: only the topic owner can unsubscribe it (nobody can from the mail footer).
# Usage: PROFILE=<aws profile of the topic's account> bash confirm-subscription.sh '<confirmation link>'
set -euo pipefail
url=$1
: "${PROFILE:?set PROFILE to the AWS CLI profile of the topic account}"
topic=$(python3 -c 'import sys, urllib.parse as u; print(u.parse_qs(u.urlparse(sys.argv[1]).query)["TopicArn"][0])' "$url")
token=$(python3 -c 'import sys, urllib.parse as u; print(u.parse_qs(u.urlparse(sys.argv[1]).query)["Token"][0])' "$url")
region=$(cut -d: -f4 <<<"$topic")
[ "$(cut -d: -f5 <<<"$topic")" = "$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)" ] \
  || { echo "PROFILE $PROFILE is not the account of $topic" >&2; exit 1; }
sub=$(aws sns confirm-subscription --profile "$PROFILE" --region "$region" --topic-arn "$topic" --token "$token" \
  --authenticate-on-unsubscribe true --query SubscriptionArn --output text)
echo "confirmed: $sub"
echo "protected: $(aws sns get-subscription-attributes --profile "$PROFILE" --region "$region" --subscription-arn "$sub" \
  --query Attributes.ConfirmationWasAuthenticated --output text)"
