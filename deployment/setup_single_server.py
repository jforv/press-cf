"""
Press Single-Server Setup Script
---------------------------------
Run ONCE after installing the press app on a unified DigitalOcean Droplet:

    bench --site <site> execute press.deployment.setup_single_server.execute

What this does:
  1. Enables the RAM-aware worker-allocation algorithm on all active Servers.
  2. Enables auto_scale_workers on all active Benches so they participate
     in dynamic worker distribution.
  3. Configures the DigitalOcean Container Registry URL in Press Settings
     (edit DO_REGISTRY_URL below before running).
  4. Optionally creates Prometheus alert rules for automatic scale-up/down
     (pass --kwargs '{"setup_prometheus": true}' to enable this step).

Usage:
    bench --site press.example.com execute press.deployment.setup_single_server.execute
    bench --site press.example.com execute press.deployment.setup_single_server.execute \\
        --kwargs '{"setup_prometheus": true, "scale_up_cpu_threshold": 80, "scale_down_cpu_threshold": 30}'
"""
from __future__ import annotations

import frappe

# ---------------------------------------------------------------------------
# Configuration — edit before running
# ---------------------------------------------------------------------------
DO_REGISTRY_URL = "registry.digitalocean.com"  # your DO registry endpoint


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def execute(
	setup_prometheus: bool = False,
	scale_up_cpu_threshold: float = 80.0,
	scale_down_cpu_threshold: float = 30.0,
) -> None:
	"""Enable single-server auto-scaling for all active servers and benches."""

	frappe.only_for("Administrator")

	_enable_new_worker_allocation()
	_enable_auto_scale_workers_on_benches()
	_set_registry_url()

	if setup_prometheus:
		_setup_prometheus_triggers(scale_up_cpu_threshold, scale_down_cpu_threshold)

	frappe.db.commit()
	print("✅  Single-server setup complete.")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def _enable_new_worker_allocation() -> None:
	"""Use the RAM-aware _auto_scale_workers_new() algorithm on all servers.

	This replaces the old step-table logic that required manual worker counts.
	"""
	servers = frappe.get_all(
		"Server",
		filters={"status": "Active", "is_unified_server": True},
		pluck="name",
	)
	if not servers:
		# Fall back to all active servers if none are marked unified yet
		servers = frappe.get_all("Server", filters={"status": "Active"}, pluck="name")

	for server_name in servers:
		frappe.db.set_value("Server", server_name, "new_worker_allocation", 1, update_modified=False)
		# Mark as unified server (single Droplet: app + db on same machine)
		frappe.db.set_value("Server", server_name, "is_unified_server", 1, update_modified=False)

	print(f"  ✔ Enabled new_worker_allocation on {len(servers)} server(s): {servers}")


def _enable_auto_scale_workers_on_benches() -> None:
	"""Opt all active benches into the auto_scale_workers mechanism."""
	benches = frappe.get_all(
		"Bench",
		filters={"status": "Active", "auto_scale_workers": 0},
		pluck="name",
	)
	for bench_name in benches:
		frappe.db.set_value("Bench", bench_name, "auto_scale_workers", 1, update_modified=False)

	print(f"  ✔ Enabled auto_scale_workers on {len(benches)} bench(es).")


def _set_registry_url() -> None:
	"""Point Press to the DigitalOcean Container Registry."""
	current = frappe.db.get_single_value("Press Settings", "docker_registry_url")
	if not current:
		frappe.db.set_single_value("Press Settings", "docker_registry_url", DO_REGISTRY_URL)
		print(f"  ✔ Set docker_registry_url → {DO_REGISTRY_URL}")
	else:
		print(f"  ℹ docker_registry_url already set to: {current} (skipped)")


def _setup_prometheus_triggers(scale_up_cpu: float, scale_down_cpu: float) -> None:
	"""Create Prometheus alert rules for CPU-based auto scale-up and scale-down."""
	from press.press.doctype.auto_scale_record.auto_scale_record import (
		create_prometheus_rule_for_scaling,
	)

	servers = frappe.get_all(
		"Server",
		filters={"status": "Active", "secondary_server": ("!=", "")},
		pluck="name",
	)

	if not servers:
		print(
			"  ⚠  No servers with a secondary_server configured — skipping Prometheus trigger setup.\n"
			"     Configure a secondary server first, then rerun with setup_prometheus=True."
		)
		return

	for server_name in servers:
		create_prometheus_rule_for_scaling(server_name, metric="CPU", threshold=scale_up_cpu, action="Scale Up")
		create_prometheus_rule_for_scaling(server_name, metric="CPU", threshold=scale_down_cpu, action="Scale Down")
		print(f"  ✔ Created Prometheus alert rules for {server_name}")
