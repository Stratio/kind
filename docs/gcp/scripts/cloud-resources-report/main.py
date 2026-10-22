# Cloud Functions gen2 HTTP entry point; Cloud Scheduler posts {"mode": ..., "operating_mode": ...} with an OIDC token.
import functions_framework

import gcp_report


@functions_framework.http
def handler(request):
    event = request.get_json(silent=True) or {}
    event.pop("no_writes", None)  # local-only flag, never honoured in the cloud
    return gcp_report.main(event)
