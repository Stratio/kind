# Azure Functions entry points (Python v2 model): three timers that call azure_report.main. Schedules (UTC, NCRONTAB
# with seconds) come from app settings so a test can move them without redeploying.
import os

import azure.functions as func

import azure_report

app = func.FunctionApp()


@app.timer_trigger(schedule="%URGENT_SCHEDULE%", arg_name="timer", use_monitor=False)
def urgent(timer: func.TimerRequest) -> None:
    print(azure_report.main({"mode": "urgent"}))


@app.timer_trigger(schedule="%DAILY_SCHEDULE%", arg_name="timer", use_monitor=False)
def daily(timer: func.TimerRequest) -> None:
    print(azure_report.main({"mode": "daily"}))


@app.timer_trigger(schedule="%CLEANUP_SCHEDULE%", arg_name="timer", use_monitor=False)
def cleanup(timer: func.TimerRequest) -> None:
    print(azure_report.main({"mode": "cleanup", "operating_mode": os.environ.get("CLEANUP_MODE", "report_only")}))
