# -*- coding: utf-8 -*-
{
    "name": "XCall Connector",
    "version": "19.0.1.1.0",
    "summary": "Connect Odoo to the XCall server and receive call webhooks",
    "description": """
XCall Connector
=====================
Connects Odoo to **XCall** - a call-tracking service that records
outgoing/incoming calls from employees' phones and streams the events to Odoo.

This module is the technical foundation (phase 1):

- **Connection** - set the server URL and API key per company, then test it.
- **Webhook subscription** - Odoo registers its own receiver URL and subscribes
  to events on the XCall server (subscribe / unsubscribe from a button).
- **Secure ingest** - incoming webhooks are verified with an HMAC signature,
  duplicates are rejected, and every event is written to a searchable log.

Mapping calls to CRM leads and attaching the call audio is done by the companion
module **XCall CRM** (levelix_calls_crm).

Requirements
------------
A XCall account and server (API key + secret from the XCall panel).
The module does not work standalone - it is a connector to that service.
""",
    "author": "Levelix",
    "maintainer": "Levelix",
    "support": "100levelix@gmail.com",
    "category": "Tools",
    "license": "LGPL-3",
    "depends": ["base"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/levelix_security.xml",
        "security/ir.model.access.csv",
        "views/levelix_call_views.xml",
        "views/levelix_event_views.xml",
        "views/levelix_config_views.xml",
        "views/levelix_menus.xml",
    ],
    "images": ["static/description/banner.png"],
    "application": True,
    "installable": True,
}
