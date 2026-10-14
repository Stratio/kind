# -*- coding: utf-8 -*-
"""commons.shell — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import sys
import subprocess
import re
import time
from upgrade_lib import state as S

def execute_command(command, dry_run, result = True, max_retries=3, retry_delay=5):
    '''Execute a command and handle the output'''

    output = ""
    retries = 0

    while retries < max_retries:
        if dry_run:
            if result:
                print("DRY-RUN")
            return ""  # No output in dry-run mode
        else:
            status, output = subprocess.getstatusoutput(command)
            if status == 0:
                if result:
                    print("OK")
                return output
            else:
                retries += 1
                if retries < max_retries:
                    time.sleep(retry_delay)
                else:
                    print("FAILED")
                    print("[ERROR] " + output)
                    sys.exit(1)

SENSITIVE_ENV_VARS = [
    "AWS_B64ENCODED_CREDENTIALS",
    "GCP_B64ENCODED_CREDENTIALS",
    "GITHUB_TOKEN",
    "AZURE_CLIENT_SECRET_B64",
]

SENSITIVE_CLI_FLAGS = ["--password", "--client-secret", "-p"]

def redact_command(command):
    '''Redact known sensitive env var assignments and CLI secret flags before logging/raising a command string'''
    safe_command = command
    for var in SENSITIVE_ENV_VARS:
        safe_command = re.sub(rf"{var}=\S+", f"{var}=<redacted>", safe_command)
    for flag in SENSITIVE_CLI_FLAGS:
        safe_command = re.sub(rf"(?<!\S){re.escape(flag)}\s+\S+", f"{flag} <redacted>", safe_command)
    return safe_command

def run_command(command, allow_errors=False, retries=3, retry_delay=2):

    if S.config["dry_run"]:
        mutating_keywords = [
            " apply ",
            " patch ",
            " delete ",
            " scale ",
            " create ",
            " annotate ",
            " label ",
            " upgrade apply ",
        ]

        normalized_command = f" {command.lower()} "

        if any(keyword in normalized_command for keyword in mutating_keywords):
            print("[DRY-RUN] Skipping mutating command")
            return "", ""

    attempts = 0

    while attempts <= retries:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            return result.stdout, result.stderr

        # If the command fails and the error is allowed, return the result without raising an exception
        if allow_errors:
            return result.stdout, result.stderr

        # If the command fails and the error is not allowed, but there are retries left, wait and retry
        attempts += 1
        if attempts > retries:
            raise Exception(f"Error executing '{redact_command(command)}': {result.stderr}")

        time.sleep(retry_delay)
