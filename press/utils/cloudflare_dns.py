# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt
"""
Cloudflare DNS bridge for Press.

Called instead of Route 53 when dns_provider == "Cloudflare" in Press Settings.
Uses the official Cloudflare Python SDK (cloudflare>=3.0.0).

Requires Press Settings to have:
  - cloudflare_api_token  (Password field)
  - cloudflare_zone_id    (Data field)
  - dns_provider          (Select field: "Cloudflare" | "Route 53" | "Generic")
"""
from __future__ import annotations

import frappe
from cloudflare import Cloudflare
from press.utils import log_error


def _client() -> Cloudflare:
	"""Return an authenticated Cloudflare SDK client."""
	token = frappe.db.get_single_value("Press Settings", "cloudflare_api_token")
	return Cloudflare(api_token=token)


def _zone_id() -> str:
	return frappe.db.get_single_value("Press Settings", "cloudflare_zone_id")


def _is_cloudflare_enabled() -> bool:
	return frappe.db.get_single_value("Press Settings", "dns_provider") == "Cloudflare"


# ---------------------------------------------------------------------------
# Public DNS helpers
# ---------------------------------------------------------------------------


def upsert_cname(name: str, target: str, proxied: bool = False, ttl: int = 1) -> None:
	"""Create or update a CNAME record in the configured Cloudflare zone.

	Args:
		name: Fully-qualified record name (e.g. ``mysite.example.com``).
		target: CNAME target — typically the proxy server hostname.
		proxied: Whether Cloudflare should proxy traffic (orange cloud).
		         Press requires direct IP access, so this defaults to False.
		ttl: TTL in seconds. Use 1 for "Auto" when proxied is False.
	"""
	try:
		cf = _client()
		zone = _zone_id()

		existing = list(cf.dns.records.list(zone_id=zone, type="CNAME", name=name))
		params = {
			"name": name,
			"content": target,
			"type": "CNAME",
			"proxied": proxied,
			"ttl": ttl,
		}

		if existing:
			cf.dns.records.update(dns_record_id=existing[0].id, zone_id=zone, **params)
		else:
			cf.dns.records.create(zone_id=zone, **params)
	except Exception:
		log_error("Cloudflare CNAME Upsert Error", name=name, target=target)


def delete_cname(name: str) -> None:
	"""Delete all CNAME records matching *name* from the configured Cloudflare zone."""
	try:
		cf = _client()
		zone = _zone_id()
		for record in cf.dns.records.list(zone_id=zone, type="CNAME", name=name):
			cf.dns.records.delete(dns_record_id=record.id, zone_id=zone)
	except Exception:
		log_error("Cloudflare CNAME Delete Error", name=name)


# ---------------------------------------------------------------------------
# Frappe doc_event hooks  (registered in hooks.py under "Site Domain")
# ---------------------------------------------------------------------------


def on_site_domain_insert(doc, method=None) -> None:
	"""Create a Cloudflare CNAME when a Site Domain becomes active."""
	if not _is_cloudflare_enabled():
		return
	if doc.status != "Active":
		return
	_sync_domain_to_cloudflare(doc)


def on_site_domain_update(doc, method=None) -> None:
	"""Keep Cloudflare in sync when a Site Domain changes status."""
	if not _is_cloudflare_enabled():
		return
	if doc.status == "Active":
		_sync_domain_to_cloudflare(doc)
	elif doc.status in ("Inactive", "Archived"):
		delete_cname(doc.domain)


def on_site_domain_trash(doc, method=None) -> None:
	"""Remove the Cloudflare CNAME when a Site Domain is deleted."""
	if not _is_cloudflare_enabled():
		return
	delete_cname(doc.domain)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sync_domain_to_cloudflare(domain_doc) -> None:
	"""Resolve the proxy server for *domain_doc* and upsert the CNAME."""
	try:
		site_server = frappe.db.get_value("Site Domain", domain_doc.name, "site")
		if not site_server:
			return
		proxy_server = frappe.db.get_value("Site", site_server, "proxy_server")
		if not proxy_server:
			return
		# Use the proxy server's public hostname as the CNAME target
		proxy_hostname = frappe.db.get_value("Proxy Server", proxy_server, "name")
		if proxy_hostname:
			upsert_cname(domain_doc.domain, proxy_hostname)
	except Exception:
		log_error("Cloudflare Domain Sync Error", domain=domain_doc.name)
