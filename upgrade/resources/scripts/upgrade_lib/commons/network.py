# -*- coding: utf-8 -*-
"""commons.network — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import json
import yaml
from jinja2 import Template, Environment, FileSystemLoader
from upgrade_lib.commons.shell import run_command
from upgrade_lib import state as S

def cp_global_network_policy(action, keos_cluster, backup_dir, dry_run):
    '''Widen/restore the Calico allow-all-traffic-from-control-plane GNP around a CP
    version bump (Azure) — ported from Skind 0.17.0-0.7.5.'''

    check_cmd = f"{S.kubectl} get GlobalNetworkPolicy allow-all-traffic-from-control-plane"
    _, err = run_command(check_cmd, allow_errors=True)
    if err:
        return

    calico_backup_dir = backup_dir + "/calico"
    backup_file = calico_backup_dir + "/allow-all-traffic-from-control-plane_gnp.yaml"

    if action == "patch":
        print("[INFO] Applying temporary allow-control-plane GlobalNetworkPolicy:", end=" ", flush=True)
        if dry_run:
            print("DRY-RUN")
            return
        os.makedirs(calico_backup_dir, exist_ok=True)
        run_command(f"{S.kubectl} get GlobalNetworkPolicy allow-all-traffic-from-control-plane -o yaml > {backup_file}")

        networks = keos_cluster["spec"].get("networks", {})
        vpc_cidr = networks.get("vpc_cidr", "10.0.0.0/16")
        pods_cidr = networks.get("pods_cidr", "192.168.0.0/16")
        patch = {"spec": {"order": 0, "selector": "all()", "ingress": [
            {"action": "Allow", "source": {"nets": [vpc_cidr, pods_cidr]}}
        ]}}
        patch_file = "/tmp/allow_cp_temporal_gnp.yaml"
        with open(patch_file, 'w') as f:
            yaml.dump(patch, f, default_flow_style=False)
        run_command(f"{S.kubectl} patch GlobalNetworkPolicy allow-all-traffic-from-control-plane --type merge --patch-file {patch_file}")
        os.remove(patch_file)
        print("OK")

    elif action == "restore":
        print("[INFO] Restoring allow-control-plane GlobalNetworkPolicy:", end=" ", flush=True)
        if dry_run:
            print("DRY-RUN")
            return
        encapsulation = "vxlan"
        nodes_raw, _ = run_command(f"{S.kubectl} get node -lkubernetes.io/os=linux,node-role.kubernetes.io/control-plane= -o json")
        control_plane_nodes = json.loads(nodes_raw)
        rendered = Template('''
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: allow-all-traffic-from-control-plane
spec:
  order: 0
  selector: all()
  ingress:
  - action: Allow
    source:
      nets:
{% for item in control_plane_nodes['items'] %}
{% set node = item.metadata %}
{% if 'projectcalico.org/IPv4IPIPTunnelAddr' in node.annotations %}
      - {{ node.annotations['projectcalico.org/IPv4IPIPTunnelAddr'] }}/32
{% elif 'projectcalico.org/IPv4VXLANTunnelAddr' in node.annotations %}
      - {{ node.annotations['projectcalico.org/IPv4VXLANTunnelAddr'] }}/32
{% endif %}
{% for address in item.status.addresses %}
{% if address.type == 'InternalIP' %}
      - {{ address.address }}/32
{% endif %}
{% endfor %}
{% endfor %}
''').render(control_plane_nodes=control_plane_nodes, encapsulation=encapsulation)
        restore_file = "/tmp/allow_cp_gnp.yaml"
        with open(restore_file, 'w') as f:
            f.write(rendered)
        run_command(f"{S.kubectl} patch GlobalNetworkPolicy allow-all-traffic-from-control-plane --type merge --patch-file {restore_file}")
        os.remove(restore_file)
        print("OK")
