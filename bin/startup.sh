#!/bin/bash

dockerd &
if [ "$1" == "eks-smoke" ]; then
  cd /CTS &&
  echo "$PWD" &&
  ls -la &&
  python3 -m pytest --help &&
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=eks-smoke-$(date +%s) --cloud_provider=aws --managed=True
elif [ "$1" == "aws-smoke" ]; then
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=aws-smoke-$(date +%s) --cloud_provider=aws --managed=False
elif [ "$1" == "gcp-smoke" ]; then
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=gcp-smoke-$(date +%s) --cloud_provider=gcp --managed=False
elif [ "$1" == "aks-smoke" ]; then
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=aks-smoke-$(date +%s) --cloud_provider=aks --managed=True
elif [ "$1" == "azure-smoke" ]; then
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=azure-smoke-$(date +%s) --cloud_provider=azure --managed=False
elif [ "$1" == "all-smoke" ]; then
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=eks-smoke-$(date +%s) --cloud_provider=aws --managed=True &
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=aws-smoke-$(date +%s) --cloud_provider=aws --managed=False &
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=gcp-smoke-$(date +%s) --cloud_provider=gcp --managed=False &
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=aks-smoke-$(date +%s) --cloud_provider=aks --managed=True &
  python3 -m pytest -s ./CTS/tests/001_provision_cluster.py ./CTS/tests/002_smoke_tests.py ./CTS/tests/999_delete_cluster.py --cluster_name=azure-smoke-$(date +%s) --cloud_provider=azure --managed=False
fi
