#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Supported provisioner versions: 0.7.X                      #
# Supported cloud providers:                                 #
#   - EKS                                                    #
#   - Azure VMs                                              #
#   - GKE                                                    #
##############################################################

import sys

# Force line buffering so log lines stay in execution order (e.g. when piped to a file).
sys.stdout.reconfigure(line_buffering=True)

from upgrade_lib import flow

if __name__ == '__main__':
    flow.run()
